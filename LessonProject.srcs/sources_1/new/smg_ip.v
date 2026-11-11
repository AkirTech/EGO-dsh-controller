`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : smg_ip   (top level; name kept so const1.xdc and .xpr stay valid)
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz (P17)
//
// Description :
//   Milestone M2: the board is a slave of the PC over a 115200 8N1 UART.
//
//   Serial link (docs/SERIAL-PROTOCOL.md v2)
//     uart_rx -> frame_parser -> link_ctrl -> frame_builder -> uart_tx
//   link_ctrl is the protocol layer: it owns the authoritative state, the
//   time, the pending question and the transmit arbitration.
//
//   Buttons (active HIGH, idle 0, pressed 1):
//     key[0] = R15  "center" : confirm the highlighted option
//     key[1] = V1   "left"   : previous display page
//     key[2] = R11  "right"  : next display page
//     key[3] = R17  "down"   : option - 1  (only while a question is open)
//     key[4] = U4   "up"     : option + 1  (only while a question is open)
//
//   Display pages:
//     0 TIME  : D HH.MM.SS  (D = day of week, Monday = 1)
//     1 STATE : 0SH <F|r|A>
//     2 ASK   : ASH <i>-<k>
//
//   Lamps (two lamps, see led_state_drv):
//     led_state[0] -> K1 (D8) : always on while the board is alive
//     led_state[1] -> K6 (D12): FREE off / RUNNING 1 Hz blink / WAITING steady
//
//   Reset: internal power-on reset, no board reset button is used. The EGo1
//   manual does not state the P15 button's active level, and a wrong guess
//   means permanent reset or no reset at all.
//
//   DEBUG_MODE
//     1 = standalone bench mode: the centre key cycles FREE/RUNNING/WAITING
//         and no serial link is required. The display still shows the time
//         and the option selector still works, so the board is usable with
//         nothing plugged in.
//     0 = PC authoritative: STATE_SET owns the state machine.
//   The UART itself is always live in both modes; DEBUG_MODE only decides
//   whether the centre key may also cycle the state.
//////////////////////////////////////////////////////////////////////////////////
module smg_ip #(
    // 1: DN0 holds the left-most 4 digits. Board-verified as 1.
    parameter        DN0_LEFT = 1,
    // Scan step divider: 100 MHz / 125000 = 800 Hz step rate,
    // 4 steps per frame = 200 Hz frame rate, 25 percent duty per digit.
    parameter [31:0] SCAN_DIV = 32'd125_000,
    // 1 = standalone bench behaviour (centre key cycles the state),
    // 0 = STATE_SET is authoritative. See the header comment.
    //
    // Default is 0 as of M2: the PC owns the state machine, and a centre-key
    // press is the answer-confirm key rather than a debug cycle. Set it back
    // to 1 only for a bare-board bring-up with no serial link attached.
    parameter integer DEBUG_MODE = 0,
    // Debounce and long-press windows, in milliseconds.
    parameter integer DEBOUNCE_MS = 10,
    parameter integer LONG_MS     = 800,
    // Reference clock and line rate for the UART.
    parameter integer CLK_HZ      = 100_000_000,
    parameter integer BAUD        = 115200
)(
    input  wire       clk,        // 100 MHz, P17
    input  wire [4:0] key,        // buttons, active high
    input  wire       uart_rx,    // serial in,  N5 (idle high)
    output wire       uart_tx,    // serial out, T4
    output wire [3:0] sm_wei,     // DN0 digit select : G2 C2 C1 H1
    output wire [7:0] sm_duan,    // DN0 segments     : B4 A4 A3 B1 A1 B3 B2 D5
    output wire [3:0] sm_wei1,    // DN1 digit select : G1 F1 E1 G6
    output wire [7:0] sm_duan1,   // DN1 segments     : D4 E3 D3 F4 F3 E2 D2 H2
    output wire [1:0] led_state   // indicator lamps: [0]=K1 alive, [1]=K6 activity
);

    //------------------------------------------------------------------
    // Power-on reset
    //   The counter must be initialised: on 7-series hardware every flip-flop
    //   powers up as 0, but in simulation an uninitialised register is X.
    //   "X != 8'hFF" evaluates to X, not 1, so the if branch is not taken,
    //   the counter stays X forever and reset never releases. That exact bug
    //   was hit during simulation, hence the explicit initialiser.
    //------------------------------------------------------------------
    reg [7:0] por_cnt = 8'd0;
    reg       rst_n   = 1'b0;

    always @(posedge clk) begin
        if (por_cnt != 8'hFF) begin
            por_cnt <= por_cnt + 8'd1;
            rst_n   <= 1'b0;
        end else begin
            rst_n   <= 1'b1;
        end
    end

    //------------------------------------------------------------------
    // 1 Hz clock enable: drives the time counter, the uptime counter and
    // the STATUS_RPT heartbeat.
    //------------------------------------------------------------------
    reg [26:0] sec_div;
    reg        ce_1hz;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sec_div <= 27'd0;
            ce_1hz  <= 1'b0;
        end else if (sec_div == 27'd99_999_999) begin
            sec_div <= 27'd0;
            ce_1hz  <= 1'b1;
        end else begin
            sec_div <= sec_div + 27'd1;
            ce_1hz  <= 1'b0;
        end
    end

    //------------------------------------------------------------------
    // Button debouncing: five instances.
    //------------------------------------------------------------------
    wire [4:0] key_pp;    // press pulses
    wire [4:0] key_lp;    // long-press pulses

    genvar gi;
    generate
        for (gi = 0; gi < 5; gi = gi + 1) begin : g_keys
            key_debounce #(
                .DEBOUNCE_MS (DEBOUNCE_MS),
                .LONG_MS     (LONG_MS),
                .CLK_MHZ     (CLK_HZ / 1_000_000)
            ) u_key (
                .clk         (clk),
                .rst_n       (rst_n),
                .key_in      (key[gi]),
                .press_pulse (key_pp[gi]),
                .long_pulse  (key_lp[gi]),
                .level       ()
            );
        end
    endgenerate

    //------------------------------------------------------------------
    // UART receive side
    //------------------------------------------------------------------
    wire [7:0] rx_byte;
    wire       rx_valid;

    uart_rx #(
        .CLK_HZ (CLK_HZ),
        .BAUD   (BAUD)
    ) u_uart_rx (
        .clk   (clk),
        .rst_n (rst_n),
        .rx    (uart_rx),
        .data  (rx_byte),
        .valid (rx_valid)
    );

    wire        msg_valid;
    wire [7:0]  msg_type;
    wire [3:0]  msg_len;
    wire [63:0] msg_payload;

    frame_parser u_parser (
        .clk         (clk),
        .rst_n       (rst_n),
        .rx_data     (rx_byte),
        .rx_valid    (rx_valid),
        .msg_valid   (msg_valid),
        .msg_type    (msg_type),
        .msg_len     (msg_len),
        .msg_payload (msg_payload)
    );

    //------------------------------------------------------------------
    // Protocol layer
    //------------------------------------------------------------------
    wire        send_req;
    wire [7:0]  send_type;
    wire [3:0]  send_len;
    wire [63:0] send_payload;
    wire        send_ack;

    wire [3:0]  session;
    wire [7:0]  pending;    wire [4:0]  hour;
    wire [5:0]  minute;
    wire [5:0]  second;
    wire [3:0]  day;
    wire        time_valid;

    wire        q_open;
    wire [7:0]  q_req_id;
    wire [3:0]  q_index;
    wire [3:0]  q_max_index;

    wire        opt_up;
    wire        opt_down;
    wire [3:0]  opt_index;
    wire        opt_confirmed;
    wire [3:0]  opt_confirmed_idx;

    wire        dbg_pulse;

    // In DEBUG_MODE the centre key cycles the state. Outside it, the same
    // key is the answer-confirm key and must not touch the state machine.
    assign dbg_pulse = (DEBUG_MODE != 0) ? key_pp[0] : 1'b0;

    //------------------------------------------------------------------
    // Status machine
    //   status_fsm is the single owner of the visible state: the lamps and
    //   the display both read it. Its authority moves with DEBUG_MODE --
    //   the centre key cycles the state on the bench, STATE_SET owns it
    //   once a PC is attached. The board never changes the state on its
    //   own in M2, so it cannot run ahead of the real Agent and diverge.
    //------------------------------------------------------------------
    wire       pc_update_pulse;
    wire [1:0] pc_state;

    // A STATE_SET arrival latches the new state and pulses pc_update. Both
    // status_fsm and link_ctrl are fed from this one decode, so the two
    // copies of the state can never come apart.
    reg  [1:0] set_state_q;
    reg        set_pulse;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            set_state_q <= 2'd0;
            set_pulse   <= 1'b0;
        end else begin
            set_pulse <= 1'b0;
            if (msg_valid
                && msg_type == 8'h02            // STATE_SET from the PC
                && msg_len  == 4'd3
                && msg_payload[7:0]  <= 8'd2
                && msg_payload[15:8] <= 8'd15) begin
                set_state_q <= msg_payload[1:0];
                set_pulse   <= 1'b1;
            end
        end
    end

    assign pc_update_pulse = set_pulse;
    assign pc_state        = set_state_q;

    wire [1:0] fsm_state;
    wire       is_waiting;
    wire       is_running;

    status_fsm #(
        .DEBUG_MODE (DEBUG_MODE)
    ) u_fsm (
        .clk        (clk),
        .rst_n      (rst_n),
        .dbg_cycle  (dbg_pulse),
        .pc_update  (pc_update_pulse),
        .pc_state   (pc_state),
        .state      (fsm_state),
        .is_waiting (is_waiting),
        .is_running (is_running)
    );

    // The protocol layer keeps its own copy for STATUS_RPT; it is loaded
    // from the same STATE_SET decode, so the two never disagree.
    wire [1:0] link_state;

    //------------------------------------------------------------------
    // Option selection
    //   Up   = key[4] = U4  : option + 1
    //   Down = key[3] = R17 : option - 1
    //   Center confirm is generated inside option_sel from its own
    //   `confirm` input, which link_ctrl drives only while a question is
    //   open.
    //------------------------------------------------------------------
    option_sel u_opt (
        .clk           (clk),
        .rst_n         (rst_n),
        .active        (q_open),
        .max_index     (q_max_index),
        .up            (opt_up),
        .down          (opt_down),
        .confirm       (q_open && key_pp[0]),   // R15
        .option        (opt_index),
        .confirmed     (opt_confirmed),
        .confirmed_idx (opt_confirmed_idx)
    );

    //------------------------------------------------------------------
    // Protocol layer
    //------------------------------------------------------------------
    link_ctrl #(
        .CLK_MHZ (CLK_HZ / 1_000_000)
    ) u_link (
        .clk               (clk),
        .rst_n             (rst_n),
        .msg_valid         (msg_valid),
        .msg_type          (msg_type),
        .msg_len           (msg_len),
        .msg_payload       (msg_payload),
        .send_req          (send_req),
        .send_type         (send_type),
        .send_len          (send_len),
        .send_payload      (send_payload),
        .send_ack          (send_ack),
        .key_pp            (key_pp),
        .key_lp            (key_lp),
        .state             (link_state),   // protocol copy, for STATUS_RPT
        .session           (session),
        .pending           (pending),
        .hour              (hour),
        .minute            (minute),
        .second            (second),
        .day               (day),
        .time_valid        (time_valid),
        .q_open            (q_open),
        .q_req_id          (q_req_id),
        .q_index           (q_index),
        .q_max_index       (q_max_index),
        .opt_up            (opt_up),
        .opt_down          (opt_down),
        .opt_confirmed     (opt_confirmed),
        .opt_confirmed_idx (opt_confirmed_idx),
        .ce_1hz            (ce_1hz)
    );

    //------------------------------------------------------------------
    // UART transmit side
    //------------------------------------------------------------------
    wire [7:0] tx_byte;
    wire       tx_valid;
    wire       tx_ready;

    frame_builder u_builder (
        .clk          (clk),
        .rst_n        (rst_n),
        .send_req     (send_req),
        .send_type    (send_type),
        .send_len     (send_len),
        .send_payload (send_payload),
        .tx_data      (tx_byte),
        .tx_valid     (tx_valid),
        .tx_ready     (tx_ready),
        .send_ack     (send_ack),
        .idle         ()
    );

    uart_tx #(
        .CLK_HZ (CLK_HZ),
        .BAUD   (BAUD)
    ) u_uart_tx (
        .clk   (clk),
        .rst_n (rst_n),
        .data  (tx_byte),
        .valid (tx_valid),
        .ready (tx_ready),
        .tx    (uart_tx)
    );

    //------------------------------------------------------------------
    // Display page
    //   Normal operation: left = previous page, right = next page.
    //
    //   While a question is open the page is FORCED to 2 (the ASK page),
    //   because that is the only page where the up/down keys have a visible
    //   effect. Without this the user could be staring at the clock while
    //   pressing up/down and see nothing happen.
    //
    //   The lock follows q_open, NOT the WAITING state. Those two are set by
    //   different frames (QUESTION_OPEN and STATE_SET), and the lock has to
    //   agree with the condition that makes the up/down keys do anything --
    //   which is q_open and nothing else. If it followed the state instead,
    //   a PC that sent QUESTION_OPEN without STATE_SET=WAITING would leave
    //   the user pressing keys on a page that never changes.
    //
    //   A TIME_SYNC load is taken as a hint to show the clock: when the PC
    //   has just set the time, the time page is the one worth looking at.
    //------------------------------------------------------------------
    reg [1:0] page_manual;      // page chosen by the left/right keys
    wire [1:0] page;            // page actually shown

    reg        time_valid_d;
    wire       time_sync_pulse = time_valid & ~time_valid_d;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            page_manual <= 2'd0;
            time_valid_d <= 1'b0;
        end else begin
            time_valid_d <= time_valid;
            if (time_sync_pulse) begin
                page_manual <= 2'd0;
            end else if (key_pp[2]) begin
                // right: next page
                page_manual <= (page_manual == 2'd2) ? 2'd0 : page_manual + 2'd1;
            end else if (key_pp[1]) begin
                // left: previous page
                page_manual <= (page_manual == 2'd0) ? 2'd2 : page_manual - 2'd1;
            end
        end
    end

    assign page = q_open ? 2'd2 : page_manual;

    //------------------------------------------------------------------
    // Display content
    //------------------------------------------------------------------
    wire [47:0] chars;
    wire [7:0]  dps;

    disp_content u_content (
        .clk       (clk),
        .rst_n     (rst_n),
        .ce_1hz    (ce_1hz),
        .load      (time_sync_pulse),
        .hour_in   (hour),
        .minute_in (minute),
        .second_in (second),
        .day_in    (day),
        .state     (fsm_state),
        .page      (page),
        .q_index   (q_index),
        .option    (opt_index),
        .chars     (chars),
        .dps       (dps)
    );

    //------------------------------------------------------------------
    // Scan engine
    //------------------------------------------------------------------
    disp_scan8 #(
        .SCAN_DIV (SCAN_DIV),
        .DN0_LEFT (DN0_LEFT)
    ) u_scan (
        .clk      (clk),
        .rst_n    (rst_n),
        .chars    (chars),
        .dps      (dps),
        .sm_wei   (sm_wei),
        .sm_duan  (sm_duan),
        .sm_wei1  (sm_wei1),
        .sm_duan1 (sm_duan1)
    );

    //------------------------------------------------------------------
    // Indicator lamps
    //------------------------------------------------------------------
    led_state_drv #(
        .RUNNING_BLINK_MS (500),
        .CLK_MHZ          (CLK_HZ / 1_000_000)
    ) u_led (
        .clk       (clk),
        .rst_n     (rst_n),
        .state     (fsm_state),
        .led_state (led_state)
    );

endmodule
