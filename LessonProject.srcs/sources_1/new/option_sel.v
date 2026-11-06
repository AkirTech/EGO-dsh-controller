`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : option_sel
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz
//
// Description :
//   Holds the currently highlighted answer option while an Agent question is
//   pending, and drives the "confirm" event.
//
//   Controls (debounced single-click pulses from the top level):
//     up    -> option index + 1
//     down  -> option index - 1
//     confirm -> latch the current option and emit one confirm pulse
//
//   Index range is 0..MAX_INDEX (default 8, i.e. nine options). The limit is
//   deliberate: page 3 shows the option number as a single digit, and the
//   Python layer refuses to send more than nine options so the board never
//   has to render "10". Wrapping at the ends is used instead of clamping so a
//   held finger can cycle quickly through a short list.
//
//   The selection is only editable while `active` is high. Once the question
//   closes the index is reset to 0 by the caller, so the next question starts
//   from the first option rather than wherever the last one left off.
//
//   Outputs are synchronous to the 100 MHz clock; all inputs are already
//   single-cycle pulses, so no further edge detection is needed here.
//////////////////////////////////////////////////////////////////////////////////
module option_sel #(
    // Highest selectable index. 8 => nine options (0..8), matching the
    // single-digit display field on page 3.
    parameter [3:0] MAX_INDEX = 4'd8
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       active,      // high while a question is pending
    input  wire       up,          // single-cycle pulse
    input  wire       down,        // single-cycle pulse
    input  wire       confirm,     // single-cycle pulse
    output reg  [3:0] option,      // currently highlighted option, 0..MAX_INDEX
    output reg        confirmed,   // single-cycle pulse when confirm accepted
    output reg  [3:0] confirmed_idx // option value latched at confirm time
);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            option        <= 4'd0;
            confirmed     <= 1'b0;
            confirmed_idx <= 4'd0;
        end else begin
            // Default: confirm pulse is a one-cycle strobe.
            confirmed <= 1'b0;

            if (!active) begin
                // Outside a question nothing is selectable, and the index
                // returns to the first option so the next question is
                // unambiguous.
                option <= 4'd0;
            end else if (up) begin
                option <= (option >= MAX_INDEX) ? 4'd0 : option + 4'd1;
            end else if (down) begin
                option <= (option == 4'd0) ? MAX_INDEX : option - 4'd1;
            end else if (confirm) begin
                // Latch the value that is on screen at the moment of the
                // press; the index itself is left alone so the display does
                // not jump while the top level waits for the PC.
                confirmed     <= 1'b1;
                confirmed_idx <= option;
            end
        end
    end

endmodule
