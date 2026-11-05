`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : disp_scan8
// Project     : LessonProject
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz
//
// Description :
//   The EGO1 eight-digit 7-segment display is electrically split into two groups:
//     DN0 -- digits 1..4
//            digit select SM_WEI[3:0]  = G2 / C2 / C1 / H1
//            segment sel  SM_DUAN[7:0] = B4 A4 A3 B1 A1 B3 B2 D5
//     DN1 -- digits 5..8
//            digit select SM_WEI1[3:0]  = G1 / F1 / E1 / G6
//            segment sel  SM_DUAN1[7:0] = D4 E3 D3 F4 F3 E2 D2 H2
//
//   The two groups have independent segment buses, so a single 2-bit index
//   drives them in lockstep: each scan step lights one digit in DN0 AND one
//   digit in DN1 at the same time. A full frame therefore needs only 4 steps,
//   giving a 25 percent duty cycle per digit instead of 12.5 percent for a
//   conventional 8-to-1 scan. Lockstep also guarantees identical brightness
//   and zero phase skew between the two groups.
//
//   Polarity: both digit select and segment select are active HIGH.
//   Digit select drives the common cathode through an NPN transistor
//   (2N5551 per schematic), so a HIGH level is what turns a digit on.
//
//   Segment bit order is {DP, G, F, E, D, C, B, A} with bit0 = A.
//
//   Character code packing: 48 bits = 8 characters of 6 bits each.
//   chars[5:0] is the left-most digit, chars[47:42] the right-most.
//////////////////////////////////////////////////////////////////////////////////
module disp_scan8 #(
    // Scan step divider: 100 MHz / SCAN_DIV = step rate.
    // Default 125000 gives 800 Hz step rate; 4 steps per frame = 200 Hz frame rate.
    // The testbench overrides this with a small value to speed up simulation.
    parameter [31:0] SCAN_DIV = 32'd125_000,
    // 1: DN0 holds the left-most 4 digits. Set to 0 if the board is wired the other way.
    parameter        DN0_LEFT = 1
)(
    input  wire        clk,       // 100 MHz system clock
    input  wire        rst_n,     // active-low reset
    input  wire [47:0] chars,     // 8 x 6-bit character codes
    output reg  [3:0]  sm_wei,    // DN0 digit select, active high
    output reg  [7:0]  sm_duan,   // DN0 segment outputs
    output reg  [3:0]  sm_wei1,   // DN1 digit select, active high
    output reg  [7:0]  sm_duan1   // DN1 segment outputs
);

    //------------------------------------------------------------------
    // Scan tick: single-cycle clock enable, no divided clock is generated.
    // Generating a real divided clock would create a gated clock domain and
    // force extra timing constraints, so a clock enable is used instead.
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
    // Split the character payload into the two groups
    //------------------------------------------------------------------
    wire [23:0] ch_dn0 = DN0_LEFT ? chars[23:0]  : chars[47:24];
    wire [23:0] ch_dn1 = DN0_LEFT ? chars[47:24] : chars[23:0];

    reg [5:0] cur0, cur1;
    always @(*) begin
        case (idx)
            2'd0:    begin cur0 = ch_dn0[5:0];   cur1 = ch_dn1[5:0];   end
            2'd1:    begin cur0 = ch_dn0[11:6];  cur1 = ch_dn1[11:6];  end
            2'd2:    begin cur0 = ch_dn0[17:12]; cur1 = ch_dn1[17:12]; end
            default: begin cur0 = ch_dn0[23:18]; cur1 = ch_dn1[23:18]; end
        endcase
    end

    //------------------------------------------------------------------
    // Segment decoder: 6-bit character code -> 8-bit segment pattern.
    // Pure combinational lookup table.
    //------------------------------------------------------------------
    function [7:0] seg_decode;
        input [5:0] ch;
        begin
            case (ch)
                6'h00: seg_decode = 8'h3F; // '0'
                6'h01: seg_decode = 8'h06; // '1'
                6'h02: seg_decode = 8'h5B; // '2'
                6'h03: seg_decode = 8'h4F; // '3'
                6'h04: seg_decode = 8'h66; // '4'
                6'h05: seg_decode = 8'h6D; // '5'
                6'h06: seg_decode = 8'h7D; // '6'
                6'h07: seg_decode = 8'h07; // '7'
                6'h08: seg_decode = 8'h7F; // '8'
                6'h09: seg_decode = 8'h6F; // '9'
                6'h0A: seg_decode = 8'h77; // 'A'
                6'h0B: seg_decode = 8'h7C; // 'b'
                6'h0C: seg_decode = 8'h39; // 'C'
                6'h0D: seg_decode = 8'h5E; // 'd'
                6'h0E: seg_decode = 8'h79; // 'E'
                6'h0F: seg_decode = 8'h71; // 'F'
                6'h10: seg_decode = 8'h76; // 'H'
                6'h11: seg_decode = 8'h38; // 'L'
                6'h12: seg_decode = 8'h54; // 'n'
                6'h13: seg_decode = 8'h5C; // 'o'
                6'h14: seg_decode = 8'h73; // 'P'
                6'h15: seg_decode = 8'h50; // 'r'
                6'h16: seg_decode = 8'h6D; // 'S' (same pattern as '5')
                6'h17: seg_decode = 8'h78; // 't'
                6'h18: seg_decode = 8'h3E; // 'U'
                6'h19: seg_decode = 8'h6E; // 'y'
                6'h1A: seg_decode = 8'h40; // '-'
                6'h1B: seg_decode = 8'h00; // ' ' (all segments off)
                default: seg_decode = 8'h00; // undefined code -> all off
            endcase
        end
    endfunction

    //------------------------------------------------------------------
    // Output registers: both groups update on the same clock edge,
    // so they are inherently in lockstep with no phase skew.
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
            sm_duan  <= seg_decode(cur0);
            sm_duan1 <= seg_decode(cur1);
        end
    end

endmodule
