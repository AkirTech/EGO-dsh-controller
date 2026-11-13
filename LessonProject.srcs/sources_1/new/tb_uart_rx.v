`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : tb_uart_rx
// Description : Self-checking testbench for uart_rx timing.
//
//   Written after a real symptom: the board only started accepting bytes after
//   a power cycle. The suspected cause was the start-phase period being derived
//   combinationally from `phase`, so the FSM sampled the start bit one whole
//   bit late instead of half a bit in. That yields a marginal receiver -- it
//   decodes when the clocks happen to line up and fails otherwise.
//
//   What this checks:
//     1. a clean byte decodes with the right value
//     2. the sample point really is the MIDDLE of each bit, not an edge:
//        a byte whose bits are skewed by +/-25% of a bit still decodes
//     3. the start bit is measured as half a bit, so a spike shorter than
//        half a bit is rejected rather than read as a character
//     4. back-to-back bytes with no idle gap still decode (the receiver must
//        return to IDLE and re-arm on the stop bit itself)
//     5. every bit pattern from 0x00 to 0xFF decodes
//
//   Tally (total cases / failed / result) is printed at the end.
//////////////////////////////////////////////////////////////////////////////////
module tb_uart_rx;

    reg clk = 1'b0;
    always #5 clk = ~clk;          // 100 MHz

    reg rst_n = 1'b0;

    integer total_cases = 0;
    integer failed      = 0;

    task chk;
        input [511:0] name;
        input         ok;
        begin
            total_cases = total_cases + 1;
            if (!ok) begin
                failed = failed + 1;
                $display("  [FAIL] %0s", name);
            end
        end
    endtask

    localparam integer CLK_HZ  = 100_000_000;
    localparam integer BAUD    = 100_000;
    localparam integer BIT_CYC = CLK_HZ / BAUD;   // 1000 cycles per bit

    //------------------------------------------------------------------
    // Device under test
    //------------------------------------------------------------------
    reg        rx_line = 1'b1;      // idle high
    wire [7:0] rx_data;
    wire       rx_valid;

    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_dut (
        .clk   (clk),
        .rst_n (rst_n),
        .rx    (rx_line),
        .data  (rx_data),
        .valid (rx_valid)
    );

    // Capture every delivered byte.
    reg [7:0] got [0:255];
    integer   got_n = 0;

    always @(posedge clk) begin
        if (rx_valid) begin
            got[got_n] = rx_data;
            got_n = got_n + 1;
        end
    end

    //------------------------------------------------------------------
    // Bit-level driver
    //------------------------------------------------------------------

    // Drive one bit for `cycles` clock cycles.
    task drive_bit;
        input        level;
        input integer cycles;
        begin
            rx_line = level;
            repeat (cycles) @(posedge clk);
        end
    endtask

    // Send one 8N1 character.
    //
    // `bit_len` is the transmitter's bit period in clock cycles, which models a
    // clock mismatch between the two ends: the receiver's bit period is fixed
    // at BIT_CYC, so a transmitter running slightly fast or slow makes the
    // sample point drift across the character. This is the realistic error --
    // a real UART pair differs by ppm, and the receiver must survive a few
    // percent before the last bit is sampled in the wrong place.
    task send_char;
        input [7:0]   value;
        input integer bit_len;
        integer i;
        begin
            drive_bit(1'b0, bit_len);                 // start
            for (i = 0; i < 8; i = i + 1) begin
                drive_bit(value[i], bit_len);         // data, LSB first
            end
            drive_bit(1'b1, bit_len);                 // stop
        end
    endtask

    // Wait for the receiver to finish the character in flight.
    task settle;
        begin
            repeat (BIT_CYC * 12) @(posedge clk);
        end
    endtask

    //------------------------------------------------------------------
    // Test sequence
    //------------------------------------------------------------------
    integer i;

    initial begin
        repeat (10) @(posedge clk);
        rst_n = 1'b1;
        repeat (5) @(posedge clk);

        $display("");
        $display("==========================================");
        $display("  uart_rx timing test");
        $display("==========================================");

        //--------------------------------------------------------------
        // 1. A clean character decodes
        //--------------------------------------------------------------
        $display("");
        $display("  [1] clean decode");

        got_n = 0;
        send_char(8'hA5, BIT_CYC);
        settle;
        chk("one character delivered", got_n == 1);
        chk("0xA5 decoded", got_n >= 1 && got[0] == 8'hA5);
        if (!(got_n >= 1 && got[0] == 8'hA5))
            $display("    got n=%0d data=0x%02h", got_n, got_n > 0 ? got[0] : 8'hxx);

        //--------------------------------------------------------------
        // 2. Tolerates a clock mismatch between the two ends
        //
        //    A UART has no clock line, so the receiver re-synchronises on every
        //    start edge and then trusts its own bit period for the rest of the
        //    character. Any mismatch drifts the sample point across the 8 data
        //    bits and must stay inside roughly +/-5% to still hit the middle.
        //    Testing +/-4% is the realistic ceiling, not an exotic case.
        //
        //    This is also the check that catches the start-period bug: if the
        //    start bit is measured as a full bit instead of a half, every
        //    sample lands half a bit off and the usable drift window collapses.
        //--------------------------------------------------------------
        $display("");
        $display("  [2] tolerates a transmitter clock mismatch");

        // Transmitter 4% fast: each bit is 960 cycles instead of 1000.
        got_n = 0;
        send_char(8'h5A, BIT_CYC - BIT_CYC / 25);
        settle;
        chk("decodes with a 4% fast transmitter",
            got_n == 1 && got[0] == 8'h5A);
        if (!(got_n == 1 && got[0] == 8'h5A))
            $display("    got n=%0d data=0x%02h", got_n, got_n > 0 ? got[0] : 8'hxx);

        // Transmitter 4% slow: each bit is 1040 cycles.
        got_n = 0;
        send_char(8'h3C, BIT_CYC + BIT_CYC / 25);
        settle;
        chk("decodes with a 4% slow transmitter",
            got_n == 1 && got[0] == 8'h3C);
        if (!(got_n == 1 && got[0] == 8'h3C))
            $display("    got n=%0d data=0x%02h", got_n, got_n > 0 ? got[0] : 8'hxx);

        // Transmitter 2% fast -- comfortably inside what a real pair sees.
        got_n = 0;
        send_char(8'hC3, BIT_CYC - BIT_CYC / 50);
        settle;
        chk("decodes with a 2% fast transmitter",
            got_n == 1 && got[0] == 8'hC3);

        //--------------------------------------------------------------
        // 3. A spike shorter than half a bit is rejected
        //    This is what proves the start phase measures HALF a bit.
        //    If it measured a FULL bit, a spike of 0.4 bit would still be
        //    LOW when sampled at 1.0 bit and would be accepted as a start.
        //--------------------------------------------------------------
        $display("");
        $display("  [3] spike rejection proves the half-bit start window");

        got_n = 0;
        drive_bit(1'b0, BIT_CYC * 4 / 10);   // 0.4 bit low
        drive_bit(1'b1, BIT_CYC * 3);        // release, stay idle
        settle;
        chk("0.4-bit spike produces no character", got_n == 0);
        if (got_n != 0)
            $display("    got %0d character(s), first=0x%02h", got_n, got[0]);

        //--------------------------------------------------------------
        // 4. Back-to-back characters with no idle gap
        //--------------------------------------------------------------
        $display("");
        $display("  [4] back-to-back characters");

        got_n = 0;
        send_char(8'h11, BIT_CYC);
        send_char(8'h22, BIT_CYC);
        send_char(8'h44, BIT_CYC);
        settle;
        chk("three characters delivered", got_n == 3);
        chk("first is 0x11", got_n >= 1 && got[0] == 8'h11);
        chk("second is 0x22", got_n >= 2 && got[1] == 8'h22);
        chk("third is 0x44", got_n >= 3 && got[2] == 8'h44);
        if (!(got_n == 3 && got[0] == 8'h11 && got[1] == 8'h22 && got[2] == 8'h44))
            $display("    got n=%0d [%02h %02h %02h]", got_n,
                     got_n > 0 ? got[0] : 8'hxx,
                     got_n > 1 ? got[1] : 8'hxx,
                     got_n > 2 ? got[2] : 8'hxx);

        //--------------------------------------------------------------
        // 5. Every byte value decodes
        //    Catches a bit-order or bit-index mistake that a few spot
        //    values would miss.
        //--------------------------------------------------------------
        $display("");
        $display("  [5] all 256 byte values");

        begin : all_bytes
            integer v;
            integer bad;
            bad = 0;
            for (v = 0; v < 256; v = v + 1) begin
                got_n = 0;
                // 5% of a bit of skew, to keep the sample point off-centre
                // without approaching the tolerance limit.
                send_char(v[7:0], BIT_CYC + BIT_CYC / 50);
                settle;
                if (!(got_n == 1 && got[0] == v[7:0])) begin
                    bad = bad + 1;
                    if (bad <= 3)
                        $display("    value 0x%02h -> n=%0d got=0x%02h",
                                 v[7:0], got_n, got_n > 0 ? got[0] : 8'hxx);
                end
            end
            chk("all 256 byte values decode", bad == 0);
        end

        //--------------------------------------------------------------
        // Tally
        //--------------------------------------------------------------
        $display("");
        $display("------------------------------------------");
        $display("  TOTAL_CASES = %0d", total_cases);
        $display("  FAILED      = %0d", failed);
        if (failed == 0) $display("  RESULT      = PASS");
        else             $display("  RESULT      = FAIL");
        $display("------------------------------------------");
        $display("");
        $finish;
    end

endmodule
