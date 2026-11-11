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
//     up      -> option index + 1
//     down    -> option index - 1
//     confirm -> latch the current option and emit one confirm pulse
//
//   Index range is 0..max_index. The upper bound is a RUNTIME input, not a
//   parameter, because QUESTION_OPEN carries how many options this particular
//   question has: a two-option question must not wrap to 8. link_ctrl drives
//   max_index from the frame (n_options - 1), and the protocol caps that at 8
//   so the single display digit on the ASK page can always show it.
//
//   Wrapping at both ends is used instead of clamping: a held finger then
//   cycles quickly through a short list, which matters more than the extra
//   travel on a long one.
//
//   The selection is only editable while `active` is high. Once the question
//   closes the index resets to 0, so the next question starts from the first
//   option rather than wherever the last one left off.
//
//   If max_index shrinks while the index is above it (a new question with
//   fewer options arriving mid-selection) the index is clamped immediately.
//   Without that the board would show an option the question does not have.
//
//   All outputs are synchronous to the 100 MHz clock; the inputs are already
//   single-cycle pulses, so no further edge detection is needed here.
//////////////////////////////////////////////////////////////////////////////////
module option_sel (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       active,       // high while a question is pending
    input  wire [3:0] max_index,    // highest selectable index (0..8)
    input  wire       up,           // single-cycle pulse
    input  wire       down,         // single-cycle pulse
    input  wire       confirm,      // single-cycle pulse
    output reg  [3:0] option,       // currently highlighted option
    output reg        confirmed,    // single-cycle pulse when confirm accepted
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
            end else if (option > max_index) begin
                // The question changed under us and now has fewer options.
                option <= max_index;
            end else if (up) begin
                option <= (option >= max_index) ? 4'd0 : option + 4'd1;
            end else if (down) begin
                option <= (option == 4'd0) ? max_index : option - 4'd1;
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
