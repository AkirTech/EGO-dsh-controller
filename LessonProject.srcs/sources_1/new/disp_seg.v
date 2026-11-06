`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : disp_seg
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz
//
// Description :
//   Segment decoder: 6-bit character code -> 8-bit segment pattern, plus a
//   decimal-point input.
//
//   Segment bit order is {DP, G, F, E, D, C, B, A} with bit0 = A, and every
//   segment is active HIGH. The decimal point is bit7.
//
//   Code range 6'h00..6'h1B covers digits, the letters the display can draw,
//   a dash and a blank. Anything outside that range renders fully blank rather
//   than a plausible-looking glyph, so a bus fault cannot masquerade as a
//   sensible reading.
//
//   This module exists so the DP is a real signal instead of being smuggled
//   into the character code: the same glyph can then be shown with or without
//   its decimal point, which is exactly what the time page needs.
//////////////////////////////////////////////////////////////////////////////////
module disp_seg (
    input  wire [5:0] ch,       // character code
    input  wire       dp,       // decimal point, active high
    output reg  [7:0] seg       // {DP, G, F, E, D, C, B, A}
);

    reg [6:0] body;             // {G,F,E,D,C,B,A}, DP added afterwards

    always @(*) begin
        case (ch)
            6'h00: body = 7'b011_1111; // 0
            6'h01: body = 7'b000_0110; // 1
            6'h02: body = 7'b101_1011; // 2
            6'h03: body = 7'b100_1111; // 3
            6'h04: body = 7'b110_0110; // 4
            6'h05: body = 7'b110_1101; // 5
            6'h06: body = 7'b111_1101; // 6
            6'h07: body = 7'b000_0111; // 7
            6'h08: body = 7'b111_1111; // 8
            6'h09: body = 7'b110_1111; // 9
            6'h0A: body = 7'b111_0111; // A
            6'h0B: body = 7'b111_1100; // b
            6'h0C: body = 7'b011_1001; // C
            6'h0D: body = 7'b101_1110; // d
            6'h0E: body = 7'b111_1001; // E
            6'h0F: body = 7'b111_0001; // F
            6'h10: body = 7'b111_0110; // H
            6'h11: body = 7'b011_1000; // L
            6'h12: body = 7'b101_0100; // n
            6'h13: body = 7'b101_1100; // o
            6'h14: body = 7'b111_0011; // P
            6'h15: body = 7'b101_0000; // r
            6'h16: body = 7'b110_1101; // S (same pattern as 5)
            6'h17: body = 7'b111_1000; // t
            6'h18: body = 7'b011_1110; // U
            6'h19: body = 7'b110_1110; // y
            6'h1A: body = 7'b100_0000; // - (middle segment only)
            6'h1B: body = 7'b000_0000; // blank
            default: body = 7'b000_0000; // undefined -> all off
        endcase
    end

    always @(*) begin
        seg = {dp, body};
    end

endmodule
