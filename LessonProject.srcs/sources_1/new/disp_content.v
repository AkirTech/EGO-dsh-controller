`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : disp_content
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz
//
// Description :
//   Builds the eight character codes and eight decimal-point flags that
//   disp_scan8 renders.
//
//   DIGIT ORDER -- read this before editing
//     DK1 is the LEFTMOST digit, and it is chars[5:0] / dps[0].
//     DK8 is the RIGHTMOST digit, and it is chars[47:42] / dps[7].
//     A Verilog concatenation puts its FIRST element in the most significant
//     position, so the rightmost digit is written first and the leftmost last.
//     Writing the fields in reading order silently mirrors the whole display;
//     that bug shipped once already.
//
//   PAGES (cycled by the left/right keys)
//     Page 0 TIME   "HH.MM.SS"  with a decimal point after HH and after MM.
//                   The DP is the cheapest colon the display has: it costs no
//                   extra digit, so all six time digits stay visible.
//     Page 1 STATE  "F  01   "  first digit is the state letter, then the
//                   session number, then blanks.
//     Page 2 ASK    "A5k 1-2 "  shown only while a question is pending; the
//                   counters stay at 1-1 until the serial link drives them.
//
//   Character codes match disp_seg.v:
//     6'h00..6'h09 = '0'..'9'   6'h0A='A' 6'h0B='b' 6'h0C='C' 6'h0D='d'
//     6'h0E='E' 6'h0F='F' 6'h10='H' 6'h11='L' 6'h12='n' 6'h13='o'
//     6'h14='P' 6'h15='r' 6'h16='S' 6'h17='t' 6'h18='U' 6'h19='y'
//     6'h1A='-' 6'h1B=blank
//////////////////////////////////////////////////////////////////////////////////
module disp_content (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        ce_1hz,     // one-cycle pulse at 1 Hz
    input  wire [1:0]  state,      // 0=FREE, 1=RUNNING, 2=WAITING
    input  wire [3:0]  day,        // day of week, 1..7 (M1: constant 1)
    input  wire [3:0]  session,    // current session number (kept for M2+)
    input  wire [1:0]  page,       // 0=TIME, 1=STATE, 2=ASK
    input  wire [3:0]  q_index,    // current question number, 1-based (M2+)
    input  wire [3:0]  q_total,    // questions in this batch (M2+)
    output reg  [47:0] chars,      // DK1 = chars[5:0], DK8 = chars[47:42]
    output reg  [7:0]  dps         // dps[0] = DK1, dps[7] = DK8
);

    //------------------------------------------------------------------
    // Character codes
    //------------------------------------------------------------------
    localparam [5:0] C_0     = 6'h00;
    localparam [5:0] C_1     = 6'h01;
    localparam [5:0] C_2     = 6'h02;
    localparam [5:0] C_A     = 6'h0A;
    localparam [5:0] C_F     = 6'h0F;
    localparam [5:0] C_H     = 6'h10;
    localparam [5:0] C_r     = 6'h15;
    localparam [5:0] C_S     = 6'h16;
    localparam [5:0] C_DASH  = 6'h1A;
    localparam [5:0] C_BLANK = 6'h1B;

    //------------------------------------------------------------------
    // HH:MM:SS counters
    //------------------------------------------------------------------
    reg [5:0] sec, min;
    reg [4:0] hour;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sec  <= 6'd0;
            min  <= 6'd0;
            hour <= 5'd0;
        end else if (ce_1hz) begin
            if (sec == 6'd59) begin
                sec <= 6'd0;
                if (min == 6'd59) begin
                    min <= 6'd0;
                    if (hour == 5'd23) hour <= 5'd0;
                    else                hour <= hour + 5'd1;
                end else begin
                    min <= min + 6'd1;
                end
            end else begin
                sec <= sec + 6'd1;
            end
        end
    end

    //------------------------------------------------------------------
    // Digit extraction.
    // Comparison chains are used instead of division: a divider against a
    // non-constant is a wide arithmetic block, while a chain of compares maps
    // to a few small LUTs.
    //------------------------------------------------------------------

    // Tens digit of a 0..59 value (0..5).
    function [3:0] dec_tens;
        input [5:0] v;
        begin
            if      (v < 6'd10) dec_tens = 4'd0;
            else if (v < 6'd20) dec_tens = 4'd1;
            else if (v < 6'd30) dec_tens = 4'd2;
            else if (v < 6'd40) dec_tens = 4'd3;
            else if (v < 6'd50) dec_tens = 4'd4;
            else                dec_tens = 4'd5;
        end
    endfunction

    // Ones digit of a 0..59 value.
    function [3:0] dec_ones;
        input [5:0] v;
        reg [5:0] rem;
        begin
            if      (v < 6'd10) rem = v;
            else if (v < 6'd20) rem = v - 6'd10;
            else if (v < 6'd30) rem = v - 6'd20;
            else if (v < 6'd40) rem = v - 6'd30;
            else if (v < 6'd50) rem = v - 6'd40;
            else                rem = v - 6'd50;
            dec_ones = rem[3:0];
        end
    endfunction

    // Tens digit of a 0..23 hour (0..2).
    function [3:0] hour_tens;
        input [4:0] v;
        begin
            if      (v < 5'd10) hour_tens = 4'd0;
            else if (v < 5'd20) hour_tens = 4'd1;
            else                hour_tens = 4'd2;
        end
    endfunction

    // Ones digit of a 0..23 hour.
    function [3:0] hour_ones;
        input [4:0] v;
        reg [5:0] rem;
        begin
            if      (v < 5'd10) rem = {1'b0, v};
            else if (v < 5'd20) rem = {1'b0, v} - 6'd10;
            else                rem = {1'b0, v} - 6'd20;
            hour_ones = rem[3:0];
        end
    endfunction

    // 0..15 -> character code
    function [5:0] nib_to_ch;
        input [3:0] v;
        begin
            case (v)
                4'h0: nib_to_ch = C_0;
                4'h1: nib_to_ch = C_1;
                4'h2: nib_to_ch = C_2;
                4'h3: nib_to_ch = 6'h03;
                4'h4: nib_to_ch = 6'h04;
                4'h5: nib_to_ch = 6'h05;
                4'h6: nib_to_ch = 6'h06;
                4'h7: nib_to_ch = 6'h07;
                4'h8: nib_to_ch = 6'h08;
                4'h9: nib_to_ch = 6'h09;
                4'hA: nib_to_ch = C_A;
                4'hB: nib_to_ch = 6'h0B;
                4'hC: nib_to_ch = 6'h0C;
                4'hD: nib_to_ch = 6'h0D;
                4'hE: nib_to_ch = 6'h0E;
                default: nib_to_ch = C_F;
            endcase
        end
    endfunction

    // Status letter.
    function [5:0] state_letter;
        input [1:0] s;
        begin
            case (s)
                2'd1:    state_letter = C_r;  // RUNNING
                2'd2:    state_letter = C_A;  // WAITING -- needs a button press
                default: state_letter = C_F;  // FREE
            endcase
        end
    endfunction

    // Tens digit of a 0..15 session number (0 or 1).
    function [3:0] session_tens;
        input [3:0] v;
        begin
            session_tens = (v < 4'd10) ? 4'd0 : 4'd1;
        end
    endfunction

    // Ones digit of a 0..15 session number.
    function [3:0] session_ones;
        input [3:0] v;
        reg [4:0] rem;
        begin
            rem = {1'b0, v};
            if (v >= 4'd10) rem = rem - 5'd10;
            session_ones = rem[3:0];
        end
    endfunction

    //------------------------------------------------------------------
    // Page composition.
    //
    // DK1 is the LEFT digit and lives in chars[5:0]; DK8 is the RIGHT digit
    // and lives in chars[47:42]. A Verilog concatenation puts its FIRST
    // element in the most significant bits, so the assignment below reads
    // DK8 first and DK1 last. To keep that from being a footgun, each page
    // computes named dk1..dk8 signals and the concatenation is written once,
    // in the only order that is correct.
    //------------------------------------------------------------------
    reg [5:0] dk1, dk2, dk3, dk4, dk5, dk6, dk7, dk8;
    reg       dk1_dp, dk2_dp, dk3_dp, dk4_dp, dk5_dp, dk6_dp, dk7_dp, dk8_dp;

    always @(*) begin
        // Defaults: everything blank and no decimal points. Each page
        // overrides only the digits it uses.
        dk1 = C_BLANK; dk2 = C_BLANK; dk3 = C_BLANK; dk4 = C_BLANK;
        dk5 = C_BLANK; dk6 = C_BLANK; dk7 = C_BLANK; dk8 = C_BLANK;
        dk1_dp = 1'b0; dk2_dp = 1'b0; dk3_dp = 1'b0; dk4_dp = 1'b0;
        dk5_dp = 1'b0; dk6_dp = 1'b0; dk7_dp = 1'b0; dk8_dp = 1'b0;

        case (page)
            //----------------------------------------------------------
            // Page 0: TIME, read left to right as "D HH.MM.SS"
            //   DK1     = day of week, 1..7 (defaults to 1)
            //   DK2     = blank separator
            //   DK3 DK4 = hour      (DP after DK4 acts as the colon)
            //   DK5 DK6 = minute    (DP after DK6 acts as the colon)
            //   DK7 DK8 = second
            //
            // The day-of-week digit replaced the session number: a session id
            // appended at the very end was noise, while a weekday is the one
            // piece of wall-clock context a clock page should carry.
            2'd0: begin
                dk1 = nib_to_ch(day);
                dk2 = C_BLANK;
                dk3 = nib_to_ch(hour_tens(hour));
                dk4 = nib_to_ch(hour_ones(hour));
                dk5 = nib_to_ch(dec_tens(min));
                dk6 = nib_to_ch(dec_ones(min));
                dk7 = nib_to_ch(dec_tens(sec));
                dk8 = nib_to_ch(dec_ones(sec));
                // The DP sits to the right of its digit, so a DP on the
                // units digit of a pair renders as "HH." and "MM." -- the
                // cheapest colon the display has, costing no extra digit.
                dk4_dp = 1'b1;
                dk6_dp = 1'b1;
            end
            //----------------------------------------------------------
            // Page 1: STATE, "0SH F    "
            //   DK1..DK3 = "0SH"  (DSH, with 0 standing in for the D the
            //                      segment table cannot draw clearly and
            //                      H standing in for K)
            //   DK4      = blank
            //   DK5      = state letter (F / r / A)
            //   DK6..DK8 = blank
            2'd1: begin
                dk1 = C_0;
                dk2 = C_S;
                dk3 = C_H;
                dk4 = C_BLANK;
                dk5 = state_letter(state);
                dk6 = C_BLANK;
                dk7 = C_BLANK;
                dk8 = C_BLANK;
            end
            //----------------------------------------------------------
            // Page 2: ASK, "ASH 1-1"
            //   DK1..DK3 = "ASH"  (H stands in for K, the table has no K)
            //   DK4 = current question index
            //   DK5 = dash
            //   DK6 = total questions
            2'd2: begin
                dk1 = C_A;
                dk2 = C_S;
                dk3 = C_H;
                dk4 = nib_to_ch(q_index[3:0]);
                dk5 = C_DASH;
                dk6 = nib_to_ch(q_total[3:0]);
            end
        endcase
    end

    //------------------------------------------------------------------
    // The one and only place the digit order is encoded.
    // DK8 occupies the most significant bits and DK1 the least.
    //------------------------------------------------------------------
    always @(*) begin
        chars = {dk8, dk7, dk6, dk5, dk4, dk3, dk2, dk1};
        dps   = {dk8_dp, dk7_dp, dk6_dp, dk5_dp,
                 dk4_dp, dk3_dp, dk2_dp, dk1_dp};
    end

endmodule
