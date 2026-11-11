`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : uart_rx
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1
//
// Description :
//   8N1 UART receiver, LSB first, sampling at the middle of every bit.
//
//   The RXD line is asynchronous, so it goes through a three-flop chain
//   before any logic sees it. Two flops remove metastability; the third
//   makes the value used by the edge detector genuinely stable.
//
//   Start detection is the falling edge of the synchronised line, not its
//   level: the line idles HIGH, so a LOW reading means a start bit only
//   right after a HIGH period.
//
//   Sampling schedule, measured in bit periods from the falling edge:
//     0.5  -> confirm the start bit is still LOW (rejects a spike)
//     1.5  -> data bit 0
//     2.5  -> data bit 1
//     ...
//     8.5  -> data bit 7
//     9.5  -> stop bit, which ends the character
//
//   The stop bit is sampled but not checked: a framing error is dropped
//   silently, and the receiver simply moves on. The protocol layer above
//   has a CRC, so a framing error can never be mistaken for a good frame.
//////////////////////////////////////////////////////////////////////////////////
module uart_rx #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   = 115200
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       rx,       // serial line, idle HIGH
    output reg  [7:0] data,     // received byte, valid when `valid` is HIGH
    output reg        valid     // one-cycle pulse when `data` is complete
);

    localparam integer CLK_PER_BIT = CLK_HZ / BAUD;
    localparam integer HALF_BIT    = CLK_PER_BIT / 2;

    //------------------------------------------------------------------
    // Three-flop synchroniser
    //------------------------------------------------------------------
    reg rx1, rx2, rx3;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx1 <= 1'b1;
            rx2 <= 1'b1;
            rx3 <= 1'b1;
        end else begin
            rx1 <= rx;
            rx2 <= rx1;
            rx3 <= rx2;
        end
    end

    wire rx_s = rx3;

    //------------------------------------------------------------------
    // Receive FSM
    //------------------------------------------------------------------
    localparam [1:0] P_IDLE  = 2'd0;
    localparam [1:0] P_START = 2'd1;
    localparam [1:0] P_DATA  = 2'd2;
    localparam [1:0] P_STOP  = 2'd3;

    reg [1:0]  phase;
    reg [15:0] cnt;
    reg [2:0]  bitidx;
    reg [7:0]  shifter;

    // The start phase only runs for half a bit, the others a full bit.
    wire [15:0] limit = (phase == P_START) ? HALF_BIT[15:0] : CLK_PER_BIT[15:0];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase   <= P_IDLE;
            cnt     <= 16'd0;
            bitidx  <= 3'd0;
            shifter <= 8'h00;
            data    <= 8'h00;
            valid   <= 1'b0;
        end else begin
            // valid is a strobe: default low, raised for exactly one cycle.
            valid <= 1'b0;

            case (phase)
                P_IDLE: begin
                    if (!rx_s) begin
                        // Falling edge: start bit candidate.
                        phase <= P_START;
                        cnt   <= 16'd0;
                    end
                end
                P_START: begin
                    if (cnt == limit - 16'd1) begin
                        cnt <= 16'd0;
                        if (!rx_s) begin
                            // Still LOW at half a bit: a real start bit.
                            phase  <= P_DATA;
                            bitidx <= 3'd0;
                        end else begin
                            // Spike shorter than half a bit: drop it.
                            phase <= P_IDLE;
                        end
                    end else begin
                        cnt <= cnt + 16'd1;
                    end
                end
                P_DATA: begin
                    if (cnt == limit - 16'd1) begin
                        cnt <= 16'd0;
                        // Sample at the middle of the bit period.
                        shifter[bitidx] <= rx_s;
                        if (bitidx == 3'd7) phase <= P_STOP;
                        else                bitidx <= bitidx + 3'd1;
                    end else begin
                        cnt <= cnt + 16'd1;
                    end
                end
                P_STOP: begin
                    if (cnt == limit - 16'd1) begin
                        cnt   <= 16'd0;
                        data  <= shifter;
                        valid <= 1'b1;
                        phase <= P_IDLE;
                    end else begin
                        cnt <= cnt + 16'd1;
                    end
                end
                default: phase <= P_IDLE;
            endcase
        end
    end

endmodule
