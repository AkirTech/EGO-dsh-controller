`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : smg_ip
// Project     : LessonProject
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz (P17)
//
// Description :
//   Eight-digit 7-segment display test.
//
//   Shows "0EEPSEEH", which is "deepseek" rewritten under these rules:
//      d  ->  0     seven segments cannot draw a clear lower-case d
//      e  ->  E
//      p  ->  P
//      s  ->  S
//      k  ->  H     seven segments cannot draw K
//
//   Scanning is handled by disp_scan8: the two 4-digit groups are driven in
//   lockstep, so one frame takes only 4 steps.
//
//   Reset: this module generates a power-on reset internally and does NOT use
//   the board P15 reset button. The EGo1 manual does not state that button's
//   active level, and a wrong guess means permanent reset or no reset at all.
//   The internal POR releases after about 2.55 us.
//
//   If left and right groups appear swapped on the board (showing "HEESPEE0"),
//   set the DN0_LEFT parameter to 0. Nothing else needs to change.
//////////////////////////////////////////////////////////////////////////////////
module smg_ip #(
    // 1: DN0 holds the left-most 4 digits (DK1..DK4).
    // Set to 0 if the two groups turn out swapped on the real board.
    parameter        DN0_LEFT = 1,
    // Scan step divider. 100 MHz / 125000 = 800 Hz step rate,
    // 4 steps per frame = 200 Hz frame rate, 25 percent duty cycle per digit,
    // which is far above any visible flicker threshold.
    // The testbench overrides this with a small value.
    parameter [31:0] SCAN_DIV = 32'd125_000
)(
    input  wire       clk,       // 100 MHz, pin P17
    output wire [3:0] sm_wei,    // DN0 digit select : G2 C2 C1 H1
    output wire [7:0] sm_duan,   // DN0 segments     : B4 A4 A3 B1 A1 B3 B2 D5
    output wire [3:0] sm_wei1,   // DN1 digit select : G1 F1 E1 G6
    output wire [7:0] sm_duan1   // DN1 segments     : D4 E3 D3 F4 F3 E2 D2 H2
);

    //------------------------------------------------------------------
    // Power-on reset
    //   The counter must be initialised. On 7-series hardware all flip-flops
    //   power up as 0, but in simulation an uninitialised register is X.
    //   The comparison "X != 8'hFF" evaluates to X, not 1, so the if branch
    //   is not taken, the counter stays X forever and reset never releases.
    //   That exact bug was hit during simulation, hence the explicit init.
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
    // Display payload: "0EEPSEEH"
    //   Character codes from the disp_scan8 seg_decode table:
    //     6'h00 = '0'   6'h0E = 'E'   6'h14 = 'P'
    //     6'h16 = 'S'   6'h10 = 'H'
    //   chars[5:0] is the left-most digit (DK1), chars[47:42] the right-most (DK8).
    //------------------------------------------------------------------
    localparam [47:0] MSG = {
        6'h10,  // DK8 : 'H'  (stands for k)
        6'h0E,  // DK7 : 'E'  (stands for e)
        6'h0E,  // DK6 : 'E'  (stands for e)
        6'h16,  // DK5 : 'S'  (stands for s)
        6'h14,  // DK4 : 'P'  (stands for p)
        6'h0E,  // DK3 : 'E'  (stands for e)
        6'h0E,  // DK2 : 'E'  (stands for e)
        6'h00   // DK1 : '0'  (stands for d)
    };

    //------------------------------------------------------------------
    // Scan engine
    //------------------------------------------------------------------
    disp_scan8 #(
        .SCAN_DIV (SCAN_DIV),
        .DN0_LEFT (DN0_LEFT)
    ) u_scan (
        .clk      (clk),
        .rst_n    (rst_n),
        .chars    (MSG),
        .sm_wei   (sm_wei),
        .sm_duan  (sm_duan),
        .sm_wei1  (sm_wei1),
        .sm_duan1 (sm_duan1)
    );

endmodule
