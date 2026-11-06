`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : disp_scan8
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz
//
// Description :
//   Eight-digit 7-segment scan engine with per-digit decimal points.
//
//   The EGO1 eight-digit display is electrically two groups of four:
//     DN0 -- digits 1..4
//            digit select SM_WEI[3:0]  = G2 / C2 / C1 / H1
//            segment sel  SM_DUAN[7:0] = B4 A4 A3 B1 A1 B3 B2 D5
//     DN1 -- digits 5..8
//            digit select SM_WEI1[3:0]  = G1 / F1 / E1 / G6
//            segment sel  SM_DUAN1[7:0] = D4 E3 D3 F4 F3 E2 D2 H2
//
//   The two groups have independent segment buses, so one 2-bit index drives
//   both in lockstep: each step lights one digit in each group at the same
//   time. A frame therefore needs only 4 steps, giving 25 percent duty per
//   digit instead of 12.5 percent for a conventional 8-to-1 scan. Lockstep
//   also guarantees identical brightness and zero phase skew.
//
//   Character order
//     chars[5:0]   is DK1, the LEFTMOST digit.
//     chars[47:42] is DK8, the RIGHTMOST digit.
//     dps[0]       belongs to DK1, dps[7] to DK8.
//   This is the order the source modules emit; the scan engine does not
//   reverse anything.
//
//   Polarity: digit select and segment select are both active HIGH. Digit
//   select drives the common cathode through an NPN transistor (2N5551 per
//   schematic), so a HIGH level turns a digit on.
//////////////////////////////////////////////////////////////////////////////////
module disp_scan8 #(
    // Scan step divider: 100 MHz / SCAN_DIV = step rate.
    // Default 125000 gives an 800 Hz step rate; 4 steps per frame = 200 Hz.
    // The testbench overrides this with a small value to shorten simulation.
    parameter [31:0] SCAN_DIV = 32'd125_000,
    // 1: DN0 holds the leftmost 4 digits. Board-verified as 1.
    parameter        DN0_LEFT = 1
)(
    input  wire        clk,       // 100 MHz system clock
    input  wire        rst_n,     // active-low reset
    input  wire [47:0] chars,     // 8 x 6-bit character codes
    input  wire [7:0]  dps,       // per-digit decimal point, dps[0] = DK1
    output reg  [3:0]  sm_wei,    // DN0 digit select, active high
    output reg  [7:0]  sm_duan,   // DN0 segment outputs
    output reg  [3:0]  sm_wei1,   // DN1 digit select, active high
    output reg  [7:0]  sm_duan1   // DN1 segment outputs
);

    //------------------------------------------------------------------
    // Scan tick: single-cycle clock enable. No divided clock is generated,
    // because a real divided clock would create a gated clock domain and
    // force extra timing constraints.
    //------------------------------------------------------------------
    reg [31:0] div_cnt;
    reg [1:0]  idx;
    wire       tick = (div_cnt == (SCAN_DIV - 32'd1));

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div_cnt <= 32'd0;
            idx     <= 2'd0;
        end else if (tick) begin
            div_cnt <= 32'd0;
            idx     <= idx + 2'd1;
        end else begin
            div_cnt <= div_cnt + 32'd1;
        end
    end

    //------------------------------------------------------------------
    // Select the character and decimal point for the current step.
    // Digit numbering is left to right, so DK1..DK4 come from the low
    // 24 bits of chars and DK5..DK8 from the high 24 bits.
    //------------------------------------------------------------------
    reg [5:0] cur_left;    // the digit from the left group (DK1..DK4)
    reg [5:0] cur_right;   // the digit from the right group (DK5..DK8)
    reg       dp_left;
    reg       dp_right;

    // Left group carries DK1..DK4 when DN0_LEFT = 1.
    wire [23:0] ch_left  = DN0_LEFT ? chars[23:0]  : chars[47:24];
    wire [23:0] ch_right = DN0_LEFT ? chars[47:24] : chars[23:0];
    wire [3:0]  dp_left_v  = DN0_LEFT ? dps[3:0] : dps[7:4];
    wire [3:0]  dp_right_v = DN0_LEFT ? dps[7:4] : dps[3:0];

    always @(*) begin
        case (idx)
            2'd0: begin
                cur_left  = ch_left[5:0];
                cur_right = ch_right[5:0];
                dp_left   = dp_left_v[0];
                dp_right  = dp_right_v[0];
            end
            2'd1: begin
                cur_left  = ch_left[11:6];
                cur_right = ch_right[11:6];
                dp_left   = dp_left_v[1];
                dp_right  = dp_right_v[1];
            end
            2'd2: begin
                cur_left  = ch_left[17:12];
                cur_right = ch_right[17:12];
                dp_left   = dp_left_v[2];
                dp_right  = dp_right_v[2];
            end
            default: begin
                cur_left  = ch_left[23:18];
                cur_right = ch_right[23:18];
                dp_left   = dp_left_v[3];
                dp_right  = dp_right_v[3];
            end
        endcase
    end

    //------------------------------------------------------------------
    // Segment decoding, one instance per group.
    //------------------------------------------------------------------
    wire [7:0] seg_left, seg_right;

    disp_seg u_seg_left (
        .ch  (cur_left),
        .dp  (dp_left),
        .seg (seg_left)
    );

    disp_seg u_seg_right (
        .ch  (cur_right),
        .dp  (dp_right),
        .seg (seg_right)
    );

    //------------------------------------------------------------------
    // Output registers: both groups update on the same clock edge, so they
    // are inherently in lockstep with no phase skew.
    //------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sm_wei   <= 4'b0000;
            sm_duan  <= 8'h00;
            sm_wei1  <= 4'b0000;
            sm_duan1 <= 8'h00;
        end else begin
            sm_wei   <= 4'b0001 << idx;
            sm_wei1  <= 4'b0001 << idx;
            sm_duan  <= seg_left;
            sm_duan1 <= seg_right;
        end
    end

endmodule
