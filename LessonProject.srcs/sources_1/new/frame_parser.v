`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : frame_parser
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1
//
// Description :
//   Serial frame receiver: turns the byte stream from uart_rx into validated
//   messages.
//
//   Frame layout (see docs/SERIAL-PROTOCOL.md section 2):
//
//     AA 55 | TYPE | LEN | PAYLOAD (LEN bytes) | CRC8
//                    \_________ CRC covers these _______/
//
//   CRC is CRC-8/ATM: polynomial 0x07, initial value 0x00, no reflection and
//   no final XOR. The standard anchor is crc8("123456789") == 0xF4, and the
//   testbench asserts that value, so a wrong polynomial cannot slip through.
//
//   Resynchronisation policy -- deliberately conservative:
//     - A bad CRC or an out-of-range LEN drops the WHOLE frame and returns to
//       searching for the header. It does NOT rewind into the payload looking
//       for another AA 55. Rewinding inside a corrupt frame can lock onto a
//       phantom header and then mis-frame every following byte.
//     - Inside a frame the length field is authoritative, so an AA 55 that
//       happens to appear in the payload is never mistaken for a header.
//
//   Output is a single-cycle strobe with the decoded message held stable on
//   the same edge. Payload byte i occupies msg_payload[i*8 +: 8], so the
//   little-endian convention of the protocol is just a bit offset.
//////////////////////////////////////////////////////////////////////////////////
module frame_parser (
    input  wire        clk,
    input  wire        rst_n,

    // Byte interface from uart_rx
    input  wire [7:0]  rx_data,
    input  wire        rx_valid,

    // Decoded message, valid for one cycle when msg_valid is HIGH
    output reg         msg_valid,
    output reg  [7:0]  msg_type,
    output reg  [3:0]  msg_len,
    output reg  [63:0] msg_payload
);

    //------------------------------------------------------------------
    // Maximum payload the protocol allows. Everything longer is rejected
    // before a single payload byte is stored, so the receive buffer can
    // never overflow.
    //------------------------------------------------------------------
    localparam [7:0] MAX_LEN = 8'd8;

    localparam [2:0] S_IDLE  = 3'd0;
    localparam [2:0] S_H55   = 3'd1;
    localparam [2:0] S_TYPE  = 3'd2;
    localparam [2:0] S_LEN   = 3'd3;
    localparam [2:0] S_BODY  = 3'd4;
    localparam [2:0] S_CRC   = 3'd5;

    reg [2:0]  state;
    reg [7:0]  type_r;
    reg [7:0]  len_r;
    reg [7:0]  crc_r;
    reg [3:0]  bidx;        // payload byte index
    reg [63:0] pay_r;

    //------------------------------------------------------------------
    // One CRC step: fold one byte into the running value.
    //
    // This is the byte-at-a-time form of the reference implementation:
    // XOR the byte in, then shift eight times, feeding back 0x07 whenever
    // the top bit is set.
    //------------------------------------------------------------------
    function [7:0] crc8_step;
        input [7:0] crc_in;
        input [7:0] byte_in;
        integer i;
        reg [7:0] c;
        begin
            c = crc_in ^ byte_in;
            for (i = 0; i < 8; i = i + 1) begin
                if (c[7]) c = (c << 1) ^ 8'h07;
                else      c = (c << 1);
            end
            crc8_step = c;
        end
    endfunction

    // Variable base, constant width part-select: byte i lives at 8*i.
    wire [6:0] byte_off = {bidx, 3'b000};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state       <= S_IDLE;
            type_r      <= 8'h00;
            len_r       <= 8'h00;
            crc_r       <= 8'h00;
            bidx        <= 4'd0;
            pay_r       <= 64'h0;
            msg_valid   <= 1'b0;
            msg_type    <= 8'h00;
            msg_len     <= 4'd0;
            msg_payload <= 64'h0;
        end else begin
            // The strobe is one cycle by construction.
            msg_valid <= 1'b0;

            if (rx_valid) begin
                case (state)
                    //--------------------------------------------------
                    // Hunt for the first header byte.
                    //--------------------------------------------------
                    S_IDLE: begin
                        if (rx_data == 8'hAA) state <= S_H55;
                    end

                    //--------------------------------------------------
                    // Second header byte. A repeated AA keeps us here so
                    // that "AA AA 55 ..." still synchronises.
                    //--------------------------------------------------
                    S_H55: begin
                        if (rx_data == 8'h55)      state <= S_TYPE;
                        else if (rx_data != 8'hAA) state <= S_IDLE;
                    end

                    //--------------------------------------------------
                    // TYPE starts the CRC. Direction is decided by the
                    // layer above, not here.
                    //--------------------------------------------------
                    S_TYPE: begin
                        type_r <= rx_data;
                        crc_r  <= crc8_step(8'h00, rx_data);
                        state  <= S_LEN;
                    end

                    //--------------------------------------------------
                    // LEN. Zero is legal (no current message uses it, but
                    // the parser has no reason to reject it); anything
                    // above MAX_LEN drops the frame immediately.
                    //--------------------------------------------------
                    S_LEN: begin
                        len_r <= rx_data;
                        crc_r <= crc8_step(crc_r, rx_data);
                        bidx  <= 4'd0;
                        pay_r <= 64'h0;
                        if (rx_data > MAX_LEN) begin
                            state <= S_IDLE;      // silently drop
                        end else if (rx_data == 8'h00) begin
                            state <= S_CRC;       // no payload bytes
                        end else begin
                            state <= S_BODY;
                        end
                    end

                    //--------------------------------------------------
                    // Payload bytes, little-endian by bit offset.
                    //--------------------------------------------------
                    S_BODY: begin
                        pay_r[{bidx, 3'b000} +: 8] <= rx_data;
                        crc_r <= crc8_step(crc_r, rx_data);
                        if ({1'b0, bidx} + 5'd1 >= {1'b0, len_r[3:0]}) begin
                            state <= S_CRC;
                        end else begin
                            bidx <= bidx + 4'd1;
                        end
                    end

                    //--------------------------------------------------
                    // CRC byte. On a match hand the message up; on a
                    // mismatch drop the whole frame and hunt again.
                    //--------------------------------------------------
                    S_CRC: begin
                        if (rx_data == crc_r) begin
                            msg_valid   <= 1'b1;
                            msg_type    <= type_r;
                            msg_len     <= len_r[3:0];
                            msg_payload <= pay_r;
                        end
                        state <= S_IDLE;
                    end

                    default: state <= S_IDLE;
                endcase
            end
        end
    end

endmodule
