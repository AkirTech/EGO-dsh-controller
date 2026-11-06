`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : key_debounce
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz
//
// Description :
//   Debounced push-button input with single-click and long-press detection.
//
//   The EGO1 buttons are active HIGH: idle reads 0, pressed reads 1.
//   They are asynchronous inputs, so they must pass through a two-flop
//   synchroniser before any logic uses them, otherwise metastability can
//   turn one press into zero or two events.
//
//   Debounce strategy: a level that differs from the settled value for a
//   continuous DEBOUNCE_MS window is accepted. Mechanical bounce lasts
//   5-10 ms, so a 10 ms window suppresses it reliably.
//
//   Outputs:
//     press_pulse - one clock wide, on the accepted rising edge
//     long_pulse  - one clock wide, once the button has been held for
//                   LONG_MS (fires while still held, not on release)
//     level       - the debounced level itself
//
//   Both pulses are single-cycle pulses in the 100 MHz clock domain.
//////////////////////////////////////////////////////////////////////////////////
module key_debounce #(
    // Debounce window in milliseconds.
    parameter integer DEBOUNCE_MS = 10,
    // Long-press threshold in milliseconds.
    parameter integer LONG_MS     = 800,
    // Clock frequency in MHz, used to convert the above to cycle counts.
    parameter integer CLK_MHZ     = 100
)(
    input  wire clk,
    input  wire rst_n,
    input  wire key_in,      // raw asynchronous button, active high
    output reg  press_pulse, // one cycle, on accepted press
    output reg  long_pulse,  // one cycle, when held past LONG_MS
    output reg  level        // debounced level
);

    //------------------------------------------------------------------
    // Cycle counts derived from the millisecond parameters
    //------------------------------------------------------------------
    localparam integer DEBOUNCE_CYC = DEBOUNCE_MS * CLK_MHZ * 1000;
    localparam integer LONG_CYC     = LONG_MS     * CLK_MHZ * 1000;

    //------------------------------------------------------------------
    // Two-flop synchroniser for the asynchronous input
    //------------------------------------------------------------------
    reg ff1, ff2;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ff1 <= 1'b0;
            ff2 <= 1'b0;
        end else begin
            ff1 <= key_in;
            ff2 <= ff1;
        end
    end

    //------------------------------------------------------------------
    // Debounce: accept a level only after it has been stable for
    // DEBOUNCE_CYC consecutive cycles.
    //------------------------------------------------------------------
    reg [31:0] cnt;      // how long the candidate has been stable
    reg        prev;     // previous synchronised sample

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt   <= 32'd0;
            prev  <= 1'b0;
            level <= 1'b0;
        end else begin
            prev <= ff2;
            if (ff2 == level) begin
                // Current level holds: reset the candidate timer.
                cnt <= 32'd0;
            end else begin
                // Input differs from the settled level: count how long.
                if (cnt >= DEBOUNCE_CYC - 1) begin
                    cnt   <= 32'd0;
                    level <= ff2;          // accepted
                end else begin
                    cnt <= cnt + 32'd1;
                end
            end
        end
    end

    //------------------------------------------------------------------
    // Press pulse on the accepted rising edge (level 0 -> 1)
    //------------------------------------------------------------------
    reg level_d;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            level_d     <= 1'b0;
            press_pulse <= 1'b0;
        end else begin
            level_d     <= level;
            press_pulse <= level & ~level_d;
        end
    end

    //------------------------------------------------------------------
    // Long press: fire once when the held duration reaches LONG_CYC.
    // The hold counter only runs while the button is down, and is
    // cleared on release, so one hold yields exactly one long_pulse.
    //------------------------------------------------------------------
    reg [31:0] hold_cnt;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hold_cnt   <= 32'd0;
            long_pulse <= 1'b0;
        end else if (!level) begin
            hold_cnt   <= 32'd0;
            long_pulse <= 1'b0;
        end else if (hold_cnt >= LONG_CYC - 1) begin
            // Saturate; pulse only on the first cycle at the threshold.
            long_pulse <= (hold_cnt == LONG_CYC - 1);
            hold_cnt   <= hold_cnt + 32'd1;
        end else begin
            hold_cnt   <= hold_cnt + 32'd1;
            long_pulse <= 1'b0;
        end
    end

endmodule
