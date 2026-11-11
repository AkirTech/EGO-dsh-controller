`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : link_ctrl
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1
//
// Description :
//   Protocol layer: the single place where received messages are interpreted
//   and transmitted messages are composed. See docs/SERIAL-PROTOCOL.md v2.
//
//   Responsibilities
//     1. Decode the four PC->FPGA messages (TIME_SYNC, STATE_SET,
//        QUESTION_OPEN, QUESTION_CLOSE) and update the registers that the
//        display, the lamps and the option selector read.
//     2. Reject a well-formed but unsupported message with NACK. A bad CRC
//        or an over-long LEN never reaches here: frame_parser drops those
//        silently, which is what keeps a corrupt stream from provoking a
//        NACK storm.
//     3. Emit KEY_EVT for key presses, EXCEPT that a centre-key click while
//        a question is open emits only ANSWER. The split lives here because
//        this is the layer that knows whether a question is open; making
//        the PC de-duplicate two frames per press pushed the same logic
//        into every consumer.
//     4. Emit ANSWER carrying the option latched by option_sel, plus a
//        saturating elapsed-time measurement.
//     5. Emit STATUS_RPT on every status change and once per second.
//
//   Direction bit: TYPE bit7 is 0 for PC->FPGA and 1 for FPGA->PC. The
//   parser hands the byte over unchanged, so a frame whose direction bit
//   says "from the FPGA" is rejected here rather than acted on.
//
//   EVERYTHING runs in the 100 MHz domain with clock enables (ce_1hz and a
//   1 ms tick). No gated clocks, no cross-domain handshakes.
//////////////////////////////////////////////////////////////////////////////////
module link_ctrl #(
    parameter integer CLK_MHZ = 100
)(
    input  wire        clk,
    input  wire        rst_n,

    //------------------------------------------------------------------
    // Received message from frame_parser
    //------------------------------------------------------------------
    input  wire        msg_valid,
    input  wire [7:0]  msg_type,
    input  wire [3:0]  msg_len,
    input  wire [63:0] msg_payload,

    //------------------------------------------------------------------
    // Transmit request to frame_builder. The request is held until
    // send_ack confirms the builder captured it, so a full builder delays
    // a frame instead of losing it.
    //------------------------------------------------------------------
    output wire        send_req,
    output wire [7:0]  send_type,
    output wire [3:0]  send_len,
    output wire [63:0] send_payload,
    input  wire        send_ack,

    //------------------------------------------------------------------
    // Debounced key pulses from the top level
    //------------------------------------------------------------------
    input  wire [4:0]  key_pp,       // single click
    input  wire [4:0]  key_lp,       // long press

    //------------------------------------------------------------------
    // Authoritative state, driven by STATE_SET.
    //
    // This is the protocol layer's own copy, kept only so a STATUS_RPT can
    // report it and so the change detector has something to compare. The
    // DISPLAY and the LAMPS read status_fsm instead; the two registers are
    // loaded from the same STATE_SET frame in the same cycle, so they never
    // disagree. Keeping both is what lets status_fsm stay the single owner
    // of the visible state.
    //------------------------------------------------------------------
    output reg  [1:0]  state,        // 0=FREE 1=RUNNING 2=WAITING
    output reg  [3:0]  session,      // 0..15, display only
    output reg  [7:0]  pending,      // outstanding requests, display only

    //------------------------------------------------------------------
    // Time, driven by TIME_SYNC
    //------------------------------------------------------------------
    output reg  [4:0]  hour,         // 0..23
    output reg  [5:0]  minute,       // 0..59
    output reg  [5:0]  second,       // 0..59
    output reg  [3:0]  day,          // 1..7, Monday = 1 .. Sunday = 7
    output reg         time_valid,   // a TIME_SYNC has been applied

    //------------------------------------------------------------------
    // Question, driven by QUESTION_OPEN / QUESTION_CLOSE
    //------------------------------------------------------------------
    output reg         q_open,
    output reg  [7:0]  q_req_id,
    output reg  [3:0]  q_index,      // 1..16, shown on the ASK page
    output reg  [3:0]  q_max_index,  // n_options - 1, clamps the selector

    //------------------------------------------------------------------
    // Option selector drive. link_ctrl owns the up/down pulses; the
    // selector owns the index and reports back its latched confirm value,
    // so the option in ANSWER is exactly the one that was on screen.
    //------------------------------------------------------------------
    output reg         opt_up,
    output reg         opt_down,
    input  wire        opt_confirmed,     // one-cycle pulse from option_sel
    input  wire [3:0]  opt_confirmed_idx,

    //------------------------------------------------------------------
    // One-second tick from the top level (single-cycle pulse)
    //------------------------------------------------------------------
    input  wire        ce_1hz
);

    //------------------------------------------------------------------
    // Message numbers. The direction bit is added separately.
    //------------------------------------------------------------------
    localparam [6:0] M_ACK       = 7'h00;
    localparam [6:0] M_TIME_SYNC = 7'h01;
    localparam [6:0] M_STATE_SET = 7'h02;
    localparam [6:0] M_Q_OPEN    = 7'h03;
    localparam [6:0] M_Q_CLOSE   = 7'h04;
    localparam [6:0] M_NACK      = 7'h7F;

    localparam [6:0] M_KEY_EVT   = 7'h01;   // direction set -> on the wire 0x81
    localparam [6:0] M_ANSWER    = 7'h02;   //                    0x82
    localparam [6:0] M_STATUS    = 7'h03;   //                    0x83

    localparam [7:0] DIR_PC      = 8'h00;
    localparam [7:0] DIR_FPGA    = 8'h80;

    //==================================================================
    // 1 ms tick and the saturating answer-latency counter
    //
    // Saturation rather than wrap is deliberate: a wrap at 65.536 s would
    // make a 66 s wait read as 1 s, while saturation only loses precision.
    //==================================================================
    localparam integer MS_CYC = CLK_MHZ * 1000;

    reg [15:0] ms_div;
    reg        ms_tick;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ms_div  <= 16'd0;
            ms_tick <= 1'b0;
        end else if (ms_div == MS_CYC - 1) begin
            ms_div  <= 16'd0;
            ms_tick <= 1'b1;
        end else begin
            ms_div  <= ms_div + 16'd1;
            ms_tick <= 1'b0;
        end
    end

    reg [15:0] elapsed;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            elapsed <= 16'd0;
        end else if (!q_open) begin
            elapsed <= 16'd0;                    // armed for the next question
        end else if (ms_tick && elapsed != 16'hFFFF) begin
            elapsed <= elapsed + 16'd1;
        end
    end

    //==================================================================
    // Uptime in whole seconds, saturating. A DECREASE in this counter is
    // how the PC detects that the board rebooted.
    //==================================================================
    reg [15:0] uptime;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            uptime <= 16'd0;
        end else if (ce_1hz && uptime != 16'hFFFF) begin
            uptime <= uptime + 16'd1;
        end
    end

    //==================================================================
    // Option selector drive. The pulses are registered downstream of the
    // debouncer and only fire while a question is open, so a stray press
    // outside a question cannot move the index.
    //==================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            opt_up   <= 1'b0;
            opt_down <= 1'b0;
        end else begin
            opt_up   <= q_open && key_pp[4];   // U4  : next option
            opt_down <= q_open && key_pp[3];   // R17 : previous option
        end
    end

    //==================================================================
    // Received-message handling
    //
    // Out-of-range fields reject the whole message with NACK rather than
    // being clamped: a silent clamp hides a real sender bug. This is the
    // opposite of the CRC policy, and the difference is deliberate -- a
    // corrupt frame is a transport accident, a well-formed out-of-range
    // frame is a programming error on the other end.
    //==================================================================
    reg msg_bad;     // one-cycle request to send NACK

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state       <= 2'd0;
            session     <= 4'd0;
            pending     <= 8'd0;
            hour        <= 5'd0;
            minute      <= 6'd0;
            second      <= 6'd0;
            day         <= 4'd1;
            time_valid  <= 1'b0;
            q_open      <= 1'b0;
            q_req_id    <= 8'd0;
            q_index     <= 4'd1;
            q_max_index <= 4'd8;
            msg_bad     <= 1'b0;
        end else begin
            msg_bad <= 1'b0;      // NACK request is one cycle wide

            if (msg_valid) begin
                if (msg_type[7] == 1'b1) begin
                    //--------------------------------------------------
                    // A frame claiming to come FROM the FPGA is a sender
                    // bug or an echo. Never act on it.
                    //--------------------------------------------------
                    msg_bad <= 1'b1;
                end else begin
                    case (msg_type[6:0])
                        //------------------------------------------------
                        // TIME_SYNC: hour, min, sec, day
                        //------------------------------------------------
                        M_TIME_SYNC: begin
                            if (msg_len == 4'd4
                                && msg_payload[7:0]   <= 8'd23            // hour
                                && msg_payload[15:8]  <= 8'd59            // minute
                                && msg_payload[23:16] <= 8'd59            // second
                                && msg_payload[31:24] >= 8'd1             // day
                                && msg_payload[31:24] <= 8'd7) begin
                                hour       <= msg_payload[4:0];
                                minute     <= msg_payload[13:8];
                                second     <= msg_payload[21:16];
                                day        <= msg_payload[27:24];
                                time_valid <= 1'b1;
                            end else begin
                                msg_bad <= 1'b1;
                            end
                        end

                        //------------------------------------------------
                        // STATE_SET: state, session, pending
                        //------------------------------------------------
                        M_STATE_SET: begin
                            if (msg_len == 4'd3
                                && msg_payload[7:0]  <= 8'd2             // state
                                && msg_payload[15:8] <= 8'd15) begin     // session
                                state   <= msg_payload[1:0];
                                session <= msg_payload[11:8];
                                pending <= msg_payload[23:16];
                            end else begin
                                msg_bad <= 1'b1;
                            end
                        end

                        //------------------------------------------------
                        // QUESTION_OPEN: req_id, n_options (1..9), q_index
                        //------------------------------------------------
                        M_Q_OPEN: begin
                            if (msg_len == 4'd3
                                && msg_payload[15:8]  >= 8'd1
                                && msg_payload[15:8]  <= 8'd9
                                && msg_payload[23:16] >= 8'd1
                                && msg_payload[23:16] <= 8'd16) begin
                                q_open      <= 1'b1;
                                q_req_id    <= msg_payload[7:0];
                                // n_options - 1 is the highest selectable index.
                                q_max_index <= msg_payload[11:8] - 4'd1;
                                q_index     <= msg_payload[19:16];
                            end else begin
                                msg_bad <= 1'b1;
                            end
                        end

                        //------------------------------------------------
                        // QUESTION_CLOSE: req_id
                        //
                        // Closing a request that is not the open one is a
                        // disagreement about state rather than a syntax
                        // error; clearing anyway is the safe direction,
                        // because a stuck q_open would pin the display to
                        // the ASK page forever.
                        //------------------------------------------------
                        M_Q_CLOSE: begin
                            if (msg_len == 4'd1) begin
                                q_open <= 1'b0;
                            end else begin
                                msg_bad <= 1'b1;
                            end
                        end

                        //------------------------------------------------
                        // ACK from the PC. The FPGA never requires one, so
                        // there is nothing to do, but it is legal traffic
                        // and must not be NACKed.
                        //------------------------------------------------
                        M_ACK: begin
                            // no action
                        end

                        default: msg_bad <= 1'b1;
                    endcase
                end
            end
        end
    end

    //==================================================================
    // Key event capture
    //
    // The centre key has two meanings. While a question is open it confirms
    // the highlighted option and is reported ONLY as ANSWER; otherwise it
    // is an ordinary KEY_EVT. Long presses are reported with event = 1.
    //
    // A click on any key is preferred over a long press, and the lowest key
    // index wins a tie. Both rules are arbitrary but they must be fixed,
    // otherwise the event reported for a simultaneous press would depend on
    // synthesis ordering.
    //==================================================================
    reg        key_req;
    reg [7:0]  key_id_v;
    reg        key_long_v;

    // One-cycle strobes for "some click" / "some long press", centre key
    // excluded while a question is open.
    wire click_0 = key_pp[0] & ~q_open;
    wire click_1 = key_pp[1];
    wire click_2 = key_pp[2];
    wire click_3 = key_pp[3];
    wire click_4 = key_pp[4];

    wire long_0  = key_lp[0] & ~q_open;
    wire long_1  = key_lp[1];
    wire long_2  = key_lp[2];
    wire long_3  = key_lp[3];
    wire long_4  = key_lp[4];

    wire any_click = click_0 | click_1 | click_2 | click_3 | click_4;
    wire any_long  = long_0 | long_1 | long_2 | long_3 | long_4;

    // Encoded key id of the winning click / long press.
    reg [2:0] click_id, long_id;
    always @(*) begin
        if      (click_0) click_id = 3'd0;
        else if (click_1) click_id = 3'd1;
        else if (click_2) click_id = 3'd2;
        else if (click_3) click_id = 3'd3;
        else if (click_4) click_id = 3'd4;
        else              click_id = 3'd0;

        if      (long_0)  long_id = 3'd0;
        else if (long_1)  long_id = 3'd1;
        else if (long_2)  long_id = 3'd2;
        else if (long_3)  long_id = 3'd3;
        else if (long_4)  long_id = 3'd4;
        else              long_id = 3'd0;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            key_req    <= 1'b0;
            key_id_v   <= 8'd0;
            key_long_v <= 1'b0;
        end else if (any_click) begin
            key_req    <= 1'b1;
            key_id_v   <= {5'b0, click_id};
            key_long_v <= 1'b0;
        end else if (any_long) begin
            key_req    <= 1'b1;
            key_id_v   <= {5'b0, long_id};
            key_long_v <= 1'b1;
        end else begin
            key_req    <= 1'b0;
        end
    end

    //==================================================================
    // Status flags
    //   bit0 = a key was pressed this cycle
    //   bit1 = time is valid (a TIME_SYNC has been applied since reset)
    //   bit2 = reserved for a sticky serial error; always 0 in this design,
    //          because every error path is a silent drop and there is no
    //          error state worth reporting
    //==================================================================
    reg [7:0] status_flags;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            status_flags <= 8'h00;
        end else begin
            status_flags <= {5'b00000, 1'b0, time_valid, (any_click | any_long)};
        end
    end

    //==================================================================
    // Transmit arbitration
    //
    // One request slot. A request stays in it until frame_builder
    // acknowledges capture, so a momentarily busy builder delays a frame
    // instead of losing it. While the slot is occupied no new event is
    // considered; key presses are one-cycle pulses, so an event arriving
    // while a frame is queued is dropped rather than queued. That is
    // acceptable because heartbeats are 1 Hz and a human cannot produce
    // clicks faster than a frame takes to send.
    //==================================================================
    reg        send_req_r;
    reg [7:0]  send_type_r;
    reg [3:0]  send_len_r;
    reg [63:0] send_payload_r;

    // Status snapshot for change detection, plus the sticky dirty flag.
    reg [1:0]  state_d;
    reg [3:0]  session_d;
    reg [7:0]  pending_d;
    reg        dirty;

    assign send_req     = send_req_r;
    assign send_type    = send_type_r;
    assign send_len     = send_len_r;
    assign send_payload = send_payload_r;

    // The frame currently in the request slot is a STATUS_RPT.
    wire requesting_status = (send_type_r[6:0] == M_STATUS);

    // A change is pending when any mirrored field differs from its
    // snapshot. Computed from the live signals, not from the registered
    // snapshot, so it is visible in the same cycle as the change.
    wire changed = (state   != state_d)
                || (session != session_d)
                || (pending != pending_d);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            send_req_r     <= 1'b0;
            send_type_r    <= 8'h00;
            send_len_r     <= 4'd0;
            send_payload_r <= 64'h0;
            state_d        <= 2'd0;
            session_d      <= 4'd0;
            pending_d      <= 8'd0;
            dirty          <= 1'b0;
        end else begin
            //------------------------------------------------------------
            // Change detection.
            //
            // `changed` is combinationally true when any field the PC
            // mirrors has moved since the snapshot. The dirty flag is set
            // by a change and cleared only by successfully sending a
            // STATUS_RPT -- and only when no change is pending in the same
            // cycle, otherwise a change coinciding with the acknowledgement
            // would be dropped and never resent.
            //------------------------------------------------------------
            state_d   <= state;
            session_d <= session;
            pending_d <= pending;

            if (send_req_r && send_ack) begin
                // Captured by the builder.
                send_req_r <= 1'b0;
            end else if (!send_req_r) begin
                //--------------------------------------------------------
                // Priority 1: NACK
                //--------------------------------------------------------
                if (msg_bad) begin
                    send_req_r     <= 1'b1;
                    send_type_r    <= DIR_FPGA | {1'b0, M_NACK};
                    send_len_r     <= 4'd1;
                    send_payload_r <= {56'h0, msg_type};
                end
                //--------------------------------------------------------
                // Priority 2: ANSWER
                //   byte 0 : req_id        [7:0]
                //   byte 1 : q_index       [15:8]
                //   byte 2 : option        [23:16]
                //   byte 3 : elapsed_ms LO [31:24]
                //   byte 4 : elapsed_ms HI [39:32]
                //--------------------------------------------------------
                else if (opt_confirmed && q_open) begin
                    send_req_r     <= 1'b1;
                    send_type_r    <= DIR_FPGA | {1'b0, M_ANSWER};
                    send_len_r     <= 4'd5;
                    send_payload_r <= {
                        24'h000000,
                        elapsed[15:8], elapsed[7:0],
                        {4'h0, opt_confirmed_idx},
                        {4'h0, q_index},
                        q_req_id
                    };
                end
                //--------------------------------------------------------
                // Priority 3: KEY_EVT
                //   byte 0 : key_id        [7:0]
                //   byte 1 : event         [15:8]   0=click 1=long
                //   byte 2 : state snapshot[23:16]
                //--------------------------------------------------------
                else if (key_req) begin
                    send_req_r     <= 1'b1;
                    send_type_r    <= DIR_FPGA | {1'b0, M_KEY_EVT};
                    send_len_r     <= 4'd3;
                    send_payload_r <= {
                        40'h0000000000,
                        {6'h0, state},
                        {7'h0, key_long_v},
                        key_id_v
                    };
                end
                //--------------------------------------------------------
                // Priority 4: STATUS_RPT on change or on the heartbeat
                //   byte 0 : state         [7:0]
                //   byte 1 : session       [15:8]
                //   byte 2 : pending       [23:16]
                //   byte 3 : flags         [31:24]
                //   byte 4 : uptime_s LO   [39:32]
                //   byte 5 : uptime_s HI   [47:40]
                //--------------------------------------------------------
                else if (dirty || changed || ce_1hz) begin
                    send_req_r     <= 1'b1;
                    send_type_r    <= DIR_FPGA | {1'b0, M_STATUS};
                    send_len_r     <= 4'd6;
                    send_payload_r <= {
                        16'h0000,
                        uptime[15:8], uptime[7:0],
                        status_flags,
                        pending,
                        {4'h0, session},
                        {6'h0, state}
                    };
                end
            end

            //------------------------------------------------------------
            // Dirty flag update, LAST so it sees the final decision above.
            //------------------------------------------------------------
            if (send_req_r && send_ack && requesting_status && !changed) begin
                dirty <= 1'b0;          // acknowledged and nothing new
            end else if (changed) begin
                dirty <= 1'b1;          // something moved, must be resent
            end
        end
    end

endmodule
