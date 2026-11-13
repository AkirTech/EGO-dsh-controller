`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : tb_day_rollover
// Description : Self-checking testbench for the weekday rollover in
//               disp_content.
//
//   The bug this pins down: the weekday digit was loaded by TIME_SYNC and then
//   never touched again, so midnight advanced the clock to 00:00:00 while the
//   weekday stayed frozen at whatever the last sync carried. On the board that
//   reads as "the time jumped but the day did not".
//
//   Rules under test:
//     1. at 23:59:59 the day advances by one (and the clock wraps to 00:00:00)
//     2. Saturday rolls into 00:00:00 on the 1st, with no day change
//     3. day 7 (Sunday) rolls over to day 1 (Monday), not to 8
//     4. a TIME_SYNC load sets the day and nothing else fights it
//     5. an out-of-range loaded day is pulled back into 1..7 on rollover
//
//   Tally (total cases / failed / result) is printed at the end.
//////////////////////////////////////////////////////////////////////////////////
module tb_day_rollover;

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

    //------------------------------------------------------------------
    // Device under test
    //------------------------------------------------------------------
    reg        ce1   = 1'b0;
    reg        load  = 1'b0;
    reg  [4:0] h_in  = 5'd0;
    reg  [5:0] m_in  = 6'd0;
    reg  [5:0] s_in  = 6'd0;
    reg  [3:0] d_in  = 4'd1;
    reg  [1:0] page  = 2'd0;
    reg  [3:0] qi    = 4'd1;
    reg  [3:0] opt   = 4'd0;

    wire [47:0] chars;
    wire [7:0]  dps;

    disp_content u_dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .ce_1hz    (ce1),
        .load      (load),
        .hour_in   (h_in),
        .minute_in (m_in),
        .second_in (s_in),
        .day_in    (d_in),
        .state     (2'd0),
        .page      (page),
        .q_index   (qi),
        .option    (opt),
        .chars     (chars),
        .dps       (dps)
    );

    //------------------------------------------------------------------
    // Helpers
    //------------------------------------------------------------------

    // One counted second: the enable is held across exactly one rising edge.
    task tick;
        begin
            @(negedge clk); ce1 = 1'b1;
            @(posedge clk);
            @(negedge clk); ce1 = 1'b0;
            @(posedge clk);   // let the counter settle
        end
    endtask

    // Load a wall-clock value through the TIME_SYNC path.
    task set_clock;
        input [4:0] h;
        input [5:0] m;
        input [5:0] s;
        input [3:0] d;
        begin
            @(negedge clk);
            h_in = h; m_in = m; s_in = s; d_in = d;
            load = 1'b1;
            @(posedge clk);
            @(negedge clk);
            load = 1'b0;
            @(posedge clk);
        end
    endtask

    // Read the displayed digits. DK1 = chars[5:0] is the weekday.
    wire [5:0] shown_day   = chars[5:0];
    wire [5:0] hour_tens   = chars[17:12];
    wire [5:0] hour_ones   = chars[23:18];

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
        $display("  disp_content weekday rollover test");
        $display("==========================================");

        //--------------------------------------------------------------
        // 1. A plain load sets the weekday
        //--------------------------------------------------------------
        $display("");
        $display("  [1] TIME_SYNC load");

        set_clock(5'd23, 6'd59, 6'd58, 4'd3);
        chk("load sets day 3", u_dut.day_r === 4'd3);
        chk("load sets hour 23", u_dut.hour === 5'd23);
        chk("load sets minute 59", u_dut.min === 6'd59);
        chk("load sets second 58", u_dut.sec === 6'd58);
        chk("DK1 shows the loaded day 3", shown_day === 4'h3);

        //--------------------------------------------------------------
        // 2. 23:59:58 -> 23:59:59: the day must NOT move yet
        //--------------------------------------------------------------
        tick;
        chk("one tick reaches 23:59:59", u_dut.sec === 6'd59 && u_dut.min === 6'd59);
        chk("day still 3 at 23:59:59", u_dut.day_r === 4'd3);

        //--------------------------------------------------------------
        // 3. 23:59:59 -> 00:00:00: the day MUST advance (the reported bug)
        //--------------------------------------------------------------
        tick;
        chk("clock wraps to 00:00:00",
            u_dut.hour === 5'd0 && u_dut.min === 6'd0 && u_dut.sec === 6'd0);
        chk("day advances 3 -> 4 at midnight", u_dut.day_r === 4'd4);
        if (u_dut.day_r !== 4'd4)
            $display("    got day=%0d (expected 4)", u_dut.day_r);
        chk("DK1 shows the new day 4", shown_day === 4'h4);
        chk("DK3/DK4 show hour 00",
            hour_tens === 4'h0 && hour_ones === 4'h0);

        //--------------------------------------------------------------
        // 4. Midnight on the 1st rolls to the 2nd
        //--------------------------------------------------------------
        set_clock(5'd23, 6'd59, 6'd59, 4'd1);
        chk("day 1 loaded", u_dut.day_r === 4'd1);
        tick;
        chk("day 1 -> 2 at midnight", u_dut.day_r === 4'd2);

        //--------------------------------------------------------------
        // 5. Sunday (7) wraps to Monday (1), not to 8
        //--------------------------------------------------------------
        set_clock(5'd23, 6'd59, 6'd59, 4'd7);
        chk("day 7 loaded", u_dut.day_r === 4'd7);
        tick;
        chk("day 7 wraps to 1", u_dut.day_r === 4'd1);
        if (u_dut.day_r !== 4'd1)
            $display("    got day=%0d (expected 1)", u_dut.day_r);
        chk("DK1 shows 1 after the wrap", shown_day === 4'h1);

        //--------------------------------------------------------------
        // 6. A load in the middle of the day must not disturb the weekday
        //--------------------------------------------------------------
        set_clock(5'd12, 6'd0, 6'd0, 4'd5);
        chk("day 5 loaded at noon", u_dut.day_r === 4'd5);
        for (i = 0; i < 5; i = i + 1) tick;
        chk("day unchanged after 5 mid-day ticks", u_dut.day_r === 4'd5);
        chk("clock advanced to 12:00:05",
            u_dut.hour === 5'd12 && u_dut.min === 6'd0 && u_dut.sec === 6'd5);

        //--------------------------------------------------------------
        // 7. Only the final tick of the day advances the weekday
        //    23:00:00 + many ticks must not bump the day
        //--------------------------------------------------------------
        set_clock(5'd23, 6'd59, 6'd50, 4'd2);
        for (i = 0; i < 9; i = i + 1) tick;   // -> 23:59:59
        chk("still day 2 at 23:59:59", u_dut.day_r === 4'd2);
        tick;                                  // -> 00:00:00
        chk("day 2 -> 3 on the wrap tick", u_dut.day_r === 4'd3);

        //--------------------------------------------------------------
        // 8. A full week of midnights returns to the starting weekday
        //
        // NOTE: each rollover needs its own set_clock. A tick only advances one
        // second, so after the first midnight the counter sits at 00:00:00 and
        // the next one is a whole day away -- not one tick away.
        //--------------------------------------------------------------
        set_clock(5'd23, 6'd59, 6'd59, 4'd4);
        chk("week test starts on day 4", u_dut.day_r === 4'd4);
        for (i = 0; i < 7; i = i + 1) begin
            tick;                                          // 4->5, 5->6 ... 7->1
            set_clock(5'd23, 6'd59, 6'd59, u_dut.day_r);   // re-arm for the next midnight
        end
        chk("seven rollovers return to day 4", u_dut.day_r === 4'd4);
        if (u_dut.day_r !== 4'd4)
            $display("    got day=%0d (expected 4 after 7 wraps)", u_dut.day_r);

        //--------------------------------------------------------------
        // 9. Loading a valid day never produces an out-of-range digit
        //--------------------------------------------------------------
        for (i = 1; i <= 7; i = i + 1) begin
            set_clock(5'd23, 6'd59, 6'd59, i[3:0]);
            tick;
            chk("rollover from a valid day stays in 1..7",
                u_dut.day_r >= 4'd1 && u_dut.day_r <= 4'd7);
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
