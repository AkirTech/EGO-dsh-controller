`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : led_state_drv
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1 / 100 MHz
//
// Description :
//   Drives the two indicator lamps from the Agent status.
//
//   Physical outputs are led_state[1:0], NOT a three-lamp one-hot vector:
//     led_state[0] -> K1 (on-board D8)
//     led_state[1] -> K6 (on-board D12)
//
//   The requested lamp behaviour is a two-lamp pattern, not one lamp per state:
//
//     FREE    : K1 on,               K6 off
//     RUNNING : K1 on,               K6 blinking at 1 Hz
//     WAITING : K1 on,               K6 on steadily
//
//   Reading it as a traffic light: K1 is the "power/connected" lamp that stays
//   lit whenever the board is alive, and K6 is the activity lamp -- slow blink
//   means work in progress, steady means something is waiting for the human.
//   Steady-on is deliberately the WAITING code: a hand can be judged at a
//   glance across the desk, while a blink of any rate reads as "busy, ignore".
//
//   Note on the old design: earlier revisions used a three-lamp one-hot vector
//   on D13/D14/D15 with three different blink patterns. That is replaced here
//   because the user wants exactly two lamps with this specific pattern.
//
//   State encoding matches status_fsm: 0=FREE, 1=RUNNING, 2=WAITING.
//////////////////////////////////////////////////////////////////////////////////
module led_state_drv #(
    // Blink half-period for the RUNNING lamp, in milliseconds (500 -> 1 Hz).
    parameter integer RUNNING_BLINK_MS = 500,
    // Clock frequency in MHz, used to convert the millisecond value to cycles.
    parameter integer CLK_MHZ = 100
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire [1:0] state,       // 0=FREE, 1=RUNNING, 2=WAITING
    output wire [1:0] led_state    // [1]=K6 activity, [0]=K1 alive
);

    localparam [1:0] ST_FREE    = 2'd0;
    localparam [1:0] ST_RUNNING = 2'd1;
    localparam [1:0] ST_WAITING = 2'd2;

    //------------------------------------------------------------------
    // Free-running blink generator for the activity lamp.
    // A free-running counter is used rather than one restarted on each state
    // change: it never produces a glitch at the transition, and the phase
    // does not matter for a slow blink.
    //------------------------------------------------------------------
    localparam integer RUN_HALF = RUNNING_BLINK_MS * CLK_MHZ * 1000;

    reg [31:0] blink_cnt;
    reg        blink_phase;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            blink_cnt   <= 32'd0;
            blink_phase <= 1'b0;
        end else if (blink_cnt >= RUN_HALF - 1) begin
            blink_cnt   <= 32'd0;
            blink_phase <= ~blink_phase;
        end else begin
            blink_cnt <= blink_cnt + 32'd1;
        end
    end

    //------------------------------------------------------------------
    // Lamp mapping.
    //   K1 (led_state[0]) is on in every state: the board being alive is not
    //   a status the user needs to read, so it never blinks.
    //   K6 (led_state[1]) carries the state.
    //------------------------------------------------------------------
    reg k6;

    always @(*) begin
        case (state)
            ST_RUNNING: k6 = blink_phase;  // 1 Hz blink: work in progress
            ST_WAITING: k6 = 1'b1;         // steady: waiting for the human
            ST_FREE:    k6 = 1'b0;         // off: nothing to do
            default:    k6 = 1'b0;         // fail safe: look idle, not busy
        endcase
    end

    assign led_state = {k6, 1'b1};

endmodule
