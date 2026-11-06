`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : status_fsm
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz
//
// Description :
//   Agent status machine: FREE / RUNNING / WAITING.
//
//   Encoding is 2-bit binary with FREE = 2'b00 on purpose. On 7-series
//   hardware every flip-flop powers up as 0, so an unreset or partially
//   reset machine naturally lands in FREE. That is the safe direction:
//   the board shows "idle" rather than a false "running".
//
//   Transition authority:
//     - In M1 (this milestone) the ONLY driver is the debug key, because
//       there is no serial link yet. Holding the debug key cycles the
//       state so the lamps can be observed on the bench.
//     - From M2 onward the PC becomes authoritative over the serial link
//       and the debug key is disabled by the DEBUG_MODE parameter.
//
//   Local key presses never change the state directly once the PC is
//   authoritative. The board would then run ahead of the real Agent, and
//   the two could diverge permanently if the PC side failed. The board
//   reports the key event and waits for the PC's next STATE_SET.
//////////////////////////////////////////////////////////////////////////////////
module status_fsm #(
    // 1 = debug cycling by key (M1), 0 = PC authoritative (M2 onward)
    parameter integer DEBUG_MODE = 1
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       dbg_cycle,     // one-cycle pulse: advance state
    input  wire       pc_update,     // one-cycle pulse: take pc_state (M2+)
    input  wire [1:0] pc_state,
    output reg  [1:0] state,
    output wire       is_waiting,    // convenience flag for the display
    output wire       is_running
);

    localparam [1:0] ST_FREE    = 2'd0;
    localparam [1:0] ST_RUNNING = 2'd1;
    localparam [1:0] ST_WAITING = 2'd2;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= ST_FREE;
        end else if (DEBUG_MODE == 0 && pc_update) begin
            // PC is authoritative: any legal value is accepted, illegal
            // values are forced back to FREE rather than trusted.
            state <= (pc_state <= ST_WAITING) ? pc_state : ST_FREE;
        end else if (DEBUG_MODE != 0 && dbg_cycle) begin
            // Debug cycling: FREE -> RUNNING -> WAITING -> FREE
            case (state)
                ST_FREE:    state <= ST_RUNNING;
                ST_RUNNING: state <= ST_WAITING;
                default:    state <= ST_FREE;
            endcase
        end
    end

    assign is_waiting = (state == ST_WAITING);
    assign is_running = (state == ST_RUNNING);

endmodule
