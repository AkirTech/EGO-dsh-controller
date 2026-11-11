`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : uart_tx
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1
//
// Description :
//   8N1 UART transmitter, one byte at a time, LSB first.
//
//   Handshake is ready/valid, both in the 100 MHz clock domain:
//     - ready is HIGH while the shifter is idle.
//     - valid is sampled only when ready is HIGH; the byte is taken on that
//       clock edge and ready drops on the same edge.
//   There is no divided clock: the bit period is a counter, so the whole
//   design stays in one clock domain with no gated clock to constrain.
//
//   Idle line level is HIGH (UART mark state). The start bit drives it LOW.
//////////////////////////////////////////////////////////////////////////////////
module uart_tx #(
    // Reference clock in Hz, used to derive the bit period.
    parameter integer CLK_HZ = 100_000_000,
    // Line rate in bits per second.
    parameter integer BAUD   = 115200
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] data,     // byte to send
    input  wire       valid,    // take `data` on the next rising edge
    output reg        ready,    // HIGH while idle and able to accept
    output reg        tx        // serial line, idle HIGH
);

    //------------------------------------------------------------------
    // Bit period in clock cycles.
    //   100 MHz / 115200 = 868.05, truncated to 868 -- an error of 0.006 %
    //   per bit, about 0.06 % over a ten-bit character, far inside a UART's
    //   tolerance. Truncation is fine; no fractional accumulator is needed.
    //------------------------------------------------------------------
    localparam integer CLK_PER_BIT = CLK_HZ / BAUD;

    reg [15:0] cnt;      // position inside the current bit
    reg [3:0]  bitidx;   // 0 = start bit, 1..8 = data, 9 = stop
    reg [9:0]  shifter;  // {stop, data[7:0], start}
    reg        busy;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt      <= 16'd0;
            bitidx   <= 4'd0;
            shifter  <= 10'h3FF;
            busy     <= 1'b0;
            ready    <= 1'b1;
            tx       <= 1'b1;
        end else if (busy) begin
            if (cnt == CLK_PER_BIT - 1) begin
                cnt <= 16'd0;
                if (bitidx == 4'd9) begin
                    // Stop bit finished: release the line and the handshake.
                    busy  <= 1'b0;
                    ready <= 1'b1;
                    tx    <= 1'b1;
                end else begin
                    bitidx <= bitidx + 4'd1;
                    tx     <= shifter[bitidx + 4'd1];
                end
            end else begin
                cnt <= cnt + 16'd1;
            end
        end else if (valid) begin
            // Load start + data + stop. bit0 of the shifter is the start bit
            // because UART sends the LSB first.
            shifter <= {1'b1, data, 1'b0};
            busy    <= 1'b1;
            ready   <= 1'b0;
            tx      <= 1'b0;   // start bit
            cnt     <= 16'd0;
            bitidx  <= 4'd0;
        end
    end

endmodule
