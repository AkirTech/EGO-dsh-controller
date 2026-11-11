`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : frame_builder
// Project     : LessonProject / EGO-dsh-controller
// Target      : xc7a35tcsg324-1 (EGO1) / Vivado 2017.1
//
// Description :
//   Serial frame transmitter: takes one decoded message and streams the
//   complete wire frame to uart_tx, one byte at a time.
//
//   Wire layout (docs/SERIAL-PROTOCOL.md section 2):
//
//     AA 55 | TYPE | LEN | PAYLOAD (LEN bytes) | CRC8
//                    \_________ CRC covers these _______/
//
//   Implementation notes:
//
//   tx_data / tx_valid are COMBINATIONAL, derived from the byte index. That
//   is safe because uart_tx registers the byte itself: it samples valid and
//   data on a clock edge and drops ready on the same edge, so the pair is
//   only consumed once. Registering them here as well would put the FSM and
//   the transmitter one cycle out of step, which silently duplicates the
//   first byte of every frame.
//
//   The CRC is accumulated INCREMENTALLY, one covered byte at a time, as
//   that byte is accepted by the transmitter. Computing it combinationally
//   over the whole payload would chain up to eighty CRC steps into a single
//   combinational path. Incremental accumulation costs one CRC step of
//   logic and is finished long before the last payload byte leaves.
//
//   Payload bytes come out of a shift register, so the datapath contains no
//   variable part-select.
//
//   A one-deep holding register absorbs a request that arrives while a frame
//   is still in flight. A third simultaneous request is dropped: no protocol
//   message can arrive three-deep, and silently losing the oldest is better
//   than blocking the event source.
//////////////////////////////////////////////////////////////////////////////////
module frame_builder (
    input  wire        clk,
    input  wire        rst_n,

    // Message request. Payload byte i is send_payload[i*8 +: 8].
    input  wire        send_req,
    input  wire [7:0]  send_type,
    input  wire [3:0]  send_len,
    input  wire [63:0] send_payload,

    // Byte stream to uart_tx. tx_valid is HIGH while a frame is streaming.
    output wire [7:0]  tx_data,
    output wire        tx_valid,
    input  wire        tx_ready,

    // One-cycle pulse when the request was actually captured. The caller
    // holds send_req until it sees this, so nothing is lost silently when
    // the one-deep holder is already occupied.
    output reg         send_ack,

    // HIGH while there is nothing to send at all.
    output wire        idle
);

    localparam [7:0] HDR0 = 8'hAA;
    localparam [7:0] HDR1 = 8'h55;

    // First payload byte position in the frame.
    localparam [4:0] PAY0 = 5'd4;

    //------------------------------------------------------------------
    // CRC-8/ATM, one byte at a time.
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

    //------------------------------------------------------------------
    // Latched message
    //------------------------------------------------------------------
    reg        active;
    reg [7:0]  type_r;
    reg [3:0]  len_r;
    reg [63:0] shift_r;    // remaining payload, next byte in bits [7:0]
    reg [7:0]  crc_r;      // CRC of every covered byte already accepted
    reg [4:0]  idx;

    reg        hold_valid;
    reg [7:0]  hold_type;
    reg [3:0]  hold_len;
    reg [63:0] hold_payload;

    // Index of the CRC byte, i.e. the last byte of the frame.
    wire [4:0] last_idx = {1'b0, len_r} + 5'd4;

    //------------------------------------------------------------------
    // Byte at a given frame position. Used for both the current byte and
    // the one that becomes current after the next advance.
    //------------------------------------------------------------------
    function [7:0] byte_at;
        input [4:0]  pos;
        input [4:0]  crc_pos;
        input [7:0]  t;
        input [3:0]  l;
        input [7:0]  crc_v;
        input [63:0] pay;
        begin
            if      (pos == 5'd0)     byte_at = HDR0;
            else if (pos == 5'd1)     byte_at = HDR1;
            else if (pos == 5'd2)     byte_at = t;
            else if (pos == 5'd3)     byte_at = {4'b0000, l};
            else if (pos == crc_pos)  byte_at = crc_v;
            else                      byte_at = pay[7:0];
        end
    endfunction

    wire [7:0] cur_byte = byte_at(idx, last_idx, type_r, len_r, crc_r, shift_r);

    // TRUE for the positions the CRC covers: TYPE, LEN and every payload
    // byte. The two header bytes and the CRC byte itself are excluded.
    wire crc_covered = (idx >= 5'd2) && (idx != last_idx);

    assign tx_valid = active;
    assign tx_data  = cur_byte;
    assign idle     = !active && !hold_valid;

    //------------------------------------------------------------------
    // Frame sequencer.
    //
    // All requests funnel through the one-deep holder, which gives exactly
    // one ordering rule to reason about: the holder is drained either when
    // the line goes idle or when the last byte of a frame is accepted.
    // A request that arrives while the holder is already occupied is
    // dropped -- frames are seconds apart in practice, so this only fires
    // on a pathological source, and dropping the newest is the choice that
    // keeps the already-queued (older) message alive.
    //------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            active       <= 1'b0;
            type_r       <= 8'h00;
            len_r        <= 4'd0;
            shift_r      <= 64'h0;
            crc_r        <= 8'h00;
            idx          <= 5'd0;
            hold_valid   <= 1'b0;
            hold_type    <= 8'h00;
            hold_len     <= 4'd0;
            hold_payload <= 64'h0;
            send_ack     <= 1'b0;
        end else begin
            // The accept strobe is one cycle wide by default.
            send_ack <= 1'b0;

            //------------------------------------------------------------
            // Holder: capture whenever it is empty.
            //------------------------------------------------------------
            if (send_req && !hold_valid) begin
                hold_valid   <= 1'b1;
                hold_type    <= send_type;
                hold_len     <= send_len;
                hold_payload <= send_payload;
                send_ack     <= 1'b1;
            end

            if (active) begin
                if (tx_ready) begin
                    if (idx == last_idx) begin
                        // Last byte out. Drain the holder if it is full.
                        if (hold_valid) begin
                            type_r     <= hold_type;
                            len_r      <= hold_len;
                            shift_r    <= hold_payload;
                            crc_r      <= 8'h00;
                            idx        <= 5'd0;
                            hold_valid <= 1'b0;
                        end else begin
                            active <= 1'b0;
                            idx    <= 5'd0;
                            crc_r  <= 8'h00;
                        end
                    end else begin
                        if (crc_covered) crc_r <= crc8_step(crc_r, cur_byte);
                        if (idx >= PAY0) shift_r <= {8'h00, shift_r[63:8]};
                        idx <= idx + 5'd1;
                    end
                end
            end else if (hold_valid) begin
                // Idle and something is queued: start it. These assignments
                // come after the capture block, so a request captured in the
                // same cycle is the one that starts here.
                active     <= 1'b1;
                type_r     <= hold_type;
                len_r      <= hold_len;
                shift_r    <= hold_payload;
                crc_r      <= 8'h00;
                idx        <= 5'd0;
                hold_valid <= 1'b0;
            end
        end
    end

endmodule
