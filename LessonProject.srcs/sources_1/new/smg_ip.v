`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : smg_ip   (top level; name kept so const1.xdc and .xpr stay valid)
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz (P17)
//
// Description :
//   Milestone M1: buttons + status machine + indicator lamps.
//   No serial link yet. The debug key cycles the three states so the lamps
//   and the state page can be checked on the bench.
//
//   Buttons (active HIGH, idle 0, pressed 1):
//     key[0] = R15  "center" : M1 debug cycle / M2+ answer the Agent
//     key[1] = V1   "left"   : previous display page
//     key[2] = R11  "right"  : next display page
//     key[3] = R17  (spare)
//     key[4] = U4   (spare)
//
//   Display pages:
//     0 TIME  : HH MM SS  S
//     1 STATE : <F|r|A> S -------
//     2 ASK   : A S H i - t
//
//   Lamps (two lamps, see led_state_drv):
//     led_state[0] -> K1 (D8) : always on while the board is alive
//     led_state[1] -> K6 (D12): FREE off / RUNNING 1 Hz blink / WAITING steady
//
//   Reset: internal power-on reset, no board reset button is used. The EGo1
//   manual does not state the P15 button's active level, and a wrong guess
//   means permanent reset or no reset at all.
//////////////////////////////////////////////////////////////////////////////////
module smg_ip #(
    // 1: DN0 holds the left-most 4 digits. Board-verified as 1.
    parameter        DN0_LEFT = 1,
    // Scan step divider: 100 MHz / 125000 = 800 Hz step rate,
    // 4 steps per frame = 200 Hz frame rate, 25 percent duty per digit.
    parameter [31:0] SCAN_DIV = 32'd125_000,
    // M1 debug behaviour: the center key cycles FREE/RUNNING/WAITING.
    // Set to 0 once the serial link makes the PC authoritative.
    parameter integer DEBUG_MODE = 1,
    // Debounce and long-press windows, in milliseconds.
    parameter integer DEBOUNCE_MS = 10,
    parameter integer LONG_MS     = 800
)(
    input  wire       clk,        // 100 MHz, P17
    input  wire [4:0] key,        // buttons, active high
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
    // 1 Hz clock enable for the time counter
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
    // Button debouncing: five instances, three used in M1.
    //------------------------------------------------------------------
    wire [4:0] key_pp;    // press pulses
    wire [4:0] key_lp;    // long-press pulses

    genvar gi;
    generate
        for (gi = 0; gi < 5; gi = gi + 1) begin : g_keys
            key_debounce #(
                .DEBOUNCE_MS (DEBOUNCE_MS),
                .LONG_MS     (LONG_MS)
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
    // Status machine
    //   M1: the center key cycles the state for bench observation.
    //   M2+: the PC drives it over the serial link and this pulse is unused.
    //------------------------------------------------------------------
    wire [1:0] state;
    wire       is_waiting, is_running;

    status_fsm #(
        .DEBUG_MODE (DEBUG_MODE)
    ) u_fsm (
        .clk       (clk),
        .rst_n     (rst_n),
        .dbg_cycle (key_pp[0]),
        .pc_update (1'b0),        // M2 will wire this to frame_parser
        .pc_state  (2'd0),
        .state     (state),
        .is_waiting(is_waiting),
        .is_running(is_running)
    );

    //------------------------------------------------------------------
    // Display page
    //   Normal operation: left = previous page, right = next page.
    //
    //   While a question is pending the page is FORCED to 2 (the ASK page),
    //   because that is the only page where the up/down keys have a visible
    //   effect. Without this the user could be staring at the clock while
    //   pressing up/down and see nothing happen.
    //------------------------------------------------------------------
    reg [1:0] page_manual;      // page chosen by the left/right keys
    wire [1:0] page;            // page actually shown

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            page_manual <= 2'd0;
        end else if (key_pp[2]) begin
            // right: next page
            page_manual <= (page_manual == 2'd2) ? 2'd0 : page_manual + 2'd1;
        end else if (key_pp[1]) begin
            // left: previous page
            page_manual <= (page_manual == 2'd0) ? 2'd2 : page_manual - 2'd1;
        end
    end

    assign page = is_waiting ? 2'd2 : page_manual;

    //------------------------------------------------------------------
    // Option selection (answers to an Agent question)
    //
    //   Up   = key[4] = U4  : option + 1
    //   Down = key[3] = R17 : option - 1
    //   Center = key[0] = R15 : confirm
    //
    //   Active while a question is pending. In M1 that state is reached with
    //   the center key's debug cycling, so the feature can be exercised on
    //   the bench; from M2 the QUESTION_OPEN frame drives it instead.
    //
    //   Note the center key does double duty: in DEBUG_MODE it both cycles
    //   the state machine AND confirms, which is intentional so one button
    //   can walk the whole flow on a bare board.
    //------------------------------------------------------------------
    wire [3:0] opt_index;
    wire [3:0] opt_confirmed_idx;
    wire       opt_confirmed;

    option_sel #(
        .MAX_INDEX (4'd8)
    ) u_opt (
        .clk           (clk),
        .rst_n         (rst_n),
        .active        (is_waiting),
        .up            (key_pp[4]),      // U4
        .down          (key_pp[3]),      // R17
        .confirm       (key_pp[0]),      // R15
        .option        (opt_index),
        .confirmed     (opt_confirmed),
        .confirmed_idx (opt_confirmed_idx)
    );

    //------------------------------------------------------------------
    // Display content
    //   The day-of-week digit is a constant 1 in M1 and will be loaded from
    //   the serial TIME_SYNC frame in M2. The session number and question
    //   index are likewise kept for M2+.
    //------------------------------------------------------------------
    wire [47:0] chars;
    wire [7:0]  dps;

    disp_content u_content (
        .clk      (clk),
        .rst_n    (rst_n),
        .ce_1hz   (ce_1hz),
        .state    (state),
        .day      (4'd1),         // M1: constant weekday 1, M2 loads it
        .session  (4'h1),         // M2+: current session number
        .page     (page),
        .q_index  (4'h1),         // M2+: driven from received frames
        .option   (opt_index),    // live highlight from option_sel
        .chars    (chars),
        .dps      (dps)
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
        .RUNNING_BLINK_MS (500)
    ) u_led (
        .clk       (clk),
        .rst_n     (rst_n),
        .state     (state),
        .led_state (led_state)
    );

endmodule
