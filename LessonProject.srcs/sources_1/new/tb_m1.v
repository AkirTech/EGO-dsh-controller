`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : tb_m1
// Description : Self-checking testbench for milestone M1 (and the M1 time fix).
//
//   Covers:
//     1. key_debounce  : bounce suppression, one pulse per press, glitch
//                        rejection, long-press threshold
//     2. status_fsm    : debug cycling FREE->RUNNING->WAITING->FREE
//     3. led_state_drv : each state drives the correct one-hot lamp, and the
//                        blinking lamp really toggles
//     4. disp_content  : digit ORDER (DK1 is chars[5:0]), the three pages,
//                        time increment, BCD rollover, decimal-point placement
//     5. disp_seg      : character-to-segment table including DP injection
//
//   DIGIT ORDER IS ASSERTED EXPLICITLY. The first version of this design put
//   the hour in the most significant bits, which mirrored the whole display;
//   the checks below fail if that ever regresses.
//
//   Tally (total cases / failed / result) is printed at the end.
//////////////////////////////////////////////////////////////////////////////////
module tb_m1;

    reg clk = 1'b0;
    always #5 clk = ~clk;          // 100 MHz

    reg rst_n;

    integer total_cases = 0;
    integer failed      = 0;

    task chk;
        input [255:0] name;
        input         ok;
        begin
            total_cases = total_cases + 1;
            if (!ok) begin
                failed = failed + 1;
                $display("  [FAIL] %0s", name);
            end
        end
    endtask

    //==================================================================
    // Part 1 : key_debounce
    //   CLK_MHZ is set to 1 so that the millisecond parameters translate to
    //   only 1000 cycles per millisecond, keeping simulation short while
    //   exercising exactly the same logic.
    //==================================================================
    reg  k_in = 1'b0;
    wire k_pp, k_lp, k_lvl;

    key_debounce #(
        .DEBOUNCE_MS (1),
        .LONG_MS     (5),
        .CLK_MHZ     (1)
    ) u_kd (
        .clk         (clk),
        .rst_n       (rst_n),
        .key_in      (k_in),
        .press_pulse (k_pp),
        .long_pulse  (k_lp),
        .level       (k_lvl)
    );

    integer pp_count = 0;
    integer lp_count = 0;
    always @(posedge clk) begin
        if (k_pp) pp_count = pp_count + 1;
        if (k_lp) lp_count = lp_count + 1;
    end

    //==================================================================
    // Part 2 : status_fsm (debug mode)
    //==================================================================
    reg        fsm_cycle   = 1'b0;
    reg        fsm_pupdate = 1'b0;
    reg  [1:0] fsm_pcstate = 2'd0;
    wire [1:0] fsm_state;
    wire       fsm_waiting, fsm_running;

    status_fsm #(.DEBUG_MODE(1)) u_fsm_dbg (
        .clk       (clk),
        .rst_n     (rst_n),
        .dbg_cycle (fsm_cycle),
        .pc_update (fsm_pupdate),
        .pc_state  (fsm_pcstate),
        .state     (fsm_state),
        .is_waiting(fsm_waiting),
        .is_running(fsm_running)
    );

    //==================================================================
    // Part 3 : led_state_drv
    //   Two lamps: led_state[0] is the always-on "alive" lamp (K1),
    //   led_state[1] is the activity lamp (K6).
    //==================================================================
    reg  [1:0] led_st = 2'd0;
    wire [1:0] led_out;

    led_state_drv #(
        .RUNNING_BLINK_MS (1),      // 1 "ms" half period at CLK_MHZ=1
        .CLK_MHZ          (1)
    ) u_led (
        .clk       (clk),
        .rst_n     (rst_n),
        .state     (led_st),
        .led_state (led_out)
    );

    //==================================================================
    // Part 4 : disp_content
    //   Its own reset so the counters can be re-zeroed without disturbing
    //   the debounce and lamp instances.
    //==================================================================
    reg        dc_rst_n = 1'b0;
    reg  [1:0] dc_state = 2'd0;
    reg  [3:0] dc_day   = 4'd1;
    reg  [3:0] dc_sess  = 4'h1;
    reg  [1:0] dc_page  = 2'd0;
    reg  [3:0] dc_qi    = 4'h1;
    reg  [3:0] dc_qt    = 4'h1;
    reg        ce1      = 1'b0;
    wire [47:0] dc_chars;
    wire [7:0]  dc_dps;

    disp_content u_dc (
        .clk     (clk),
        .rst_n   (dc_rst_n),
        .ce_1hz  (ce1),
        .state   (dc_state),
        .day     (dc_day),
        .session (dc_sess),
        .page    (dc_page),
        .q_index (dc_qi),
        .q_total (dc_qt),
        .chars   (dc_chars),
        .dps     (dc_dps)
    );

    // One counted second: the enable is held across exactly one rising edge.
    task tick1hz;
        begin
            @(negedge clk); ce1 = 1'b1;
            @(posedge clk);
            @(negedge clk); ce1 = 1'b0;
        end
    endtask

    //==================================================================
    // Part 5 : disp_seg
    //==================================================================
    reg  [5:0] sg_ch = 6'h00;
    reg        sg_dp = 1'b0;
    wire [7:0] sg_out;

    disp_seg u_sg (
        .ch  (sg_ch),
        .dp  (sg_dp),
        .seg (sg_out)
    );

    //==================================================================
    // Test sequence
    //==================================================================
    integer i;
    integer pulse_before;

    initial begin
        rst_n    = 1'b0;
        dc_rst_n = 1'b0;
        repeat (10) @(posedge clk);
        rst_n    = 1'b1;
        dc_rst_n = 1'b1;
        repeat (5) @(posedge clk);

        $display("");
        $display("==========================================");
        $display("  EGO-dsh-controller milestone M1 test");
        $display("==========================================");

        //--------------------------------------------------------------
        // 1. Debounce: clean press produces exactly one pulse
        //--------------------------------------------------------------
        $display("");
        $display("  [1] key_debounce");

        pulse_before = pp_count;
        k_in = 1'b1;
        repeat (2000) @(posedge clk);
        k_in = 1'b0;
        repeat (2000) @(posedge clk);
        chk("clean press yields exactly one press_pulse", (pp_count - pulse_before) == 1);
        if ((pp_count - pulse_before) != 1)
            $display("    expected 1, got %0d", pp_count - pulse_before);

        //--------------------------------------------------------------
        // 2. Debounce: bouncy press still yields exactly one pulse
        //--------------------------------------------------------------
        pulse_before = pp_count;
        for (i = 0; i < 12; i = i + 1) begin
            k_in = 1'b1; repeat (20) @(posedge clk);
            k_in = 1'b0; repeat (20) @(posedge clk);
        end
        k_in = 1'b1;
        repeat (2000) @(posedge clk);
        k_in = 1'b0;
        repeat (2000) @(posedge clk);
        chk("bouncy press yields exactly one press_pulse", (pp_count - pulse_before) == 1);
        if ((pp_count - pulse_before) != 1)
            $display("    expected 1, got %0d", pp_count - pulse_before);

        //--------------------------------------------------------------
        // 3. Debounce: a glitch shorter than the window is ignored
        //--------------------------------------------------------------
        pulse_before = pp_count;
        k_in = 1'b1;
        repeat (50) @(posedge clk);
        k_in = 1'b0;
        repeat (2000) @(posedge clk);
        chk("short glitch is suppressed", (pp_count - pulse_before) == 0);

        //--------------------------------------------------------------
        // 4. Long press fires exactly once
        //--------------------------------------------------------------
        lp_count = 0;
        k_in = 1'b1;
        repeat (12000) @(posedge clk);
        chk("long press fires exactly one long_pulse", lp_count == 1);
        if (lp_count != 1) $display("    expected 1, got %0d", lp_count);
        k_in = 1'b0;
        repeat (3000) @(posedge clk);

        //--------------------------------------------------------------
        // 5. Short press must not fire long_pulse
        //--------------------------------------------------------------
        lp_count = 0;
        k_in = 1'b1;
        repeat (2000) @(posedge clk);
        k_in = 1'b0;
        repeat (3000) @(posedge clk);
        chk("short press does not fire long_pulse", lp_count == 0);

        //--------------------------------------------------------------
        // 6. Status machine debug cycling
        //--------------------------------------------------------------
        $display("");
        $display("  [2] status_fsm");

        chk("resets to FREE", fsm_state == 2'd0);

        fsm_cycle = 1'b1; @(posedge clk); fsm_cycle = 1'b0; repeat (2) @(posedge clk);
        chk("FREE -> RUNNING", fsm_state == 2'd1);

        fsm_cycle = 1'b1; @(posedge clk); fsm_cycle = 1'b0; repeat (2) @(posedge clk);
        chk("RUNNING -> WAITING", fsm_state == 2'd2);

        fsm_cycle = 1'b1; @(posedge clk); fsm_cycle = 1'b0; repeat (2) @(posedge clk);
        chk("WAITING -> FREE", fsm_state == 2'd0);

        chk("is_waiting low in FREE", fsm_waiting == 1'b0);
        chk("is_running low in FREE", fsm_running == 1'b0);

        //--------------------------------------------------------------
        // 7. Lamp mapping per state
        //   led_state[0] = K1 alive lamp, must be ON in every state
        //   led_state[1] = K6 activity lamp
        //     FREE on/off -> off, RUNNING -> blinking, WAITING -> steady on
        //--------------------------------------------------------------
        $display("");
        $display("  [3] led_state_drv");

        led_st = 2'd0; #1;
        chk("FREE: alive lamp on", led_out[0] === 1'b1);
        chk("FREE: activity lamp off", led_out[1] === 1'b0);

        led_st = 2'd2; #1;
        chk("WAITING: alive lamp on", led_out[0] === 1'b1);
        chk("WAITING: activity lamp steady on", led_out[1] === 1'b1);

        led_st = 2'd1; #1;
        chk("RUNNING: alive lamp on", led_out[0] === 1'b1);

        // RUNNING activity lamp must actually toggle over time
        led_st = 2'd1;
        @(posedge clk);
        begin : watch_blink
            integer seen_on, seen_off;
            seen_on  = 0;
            seen_off = 0;
            for (i = 0; i < 4000; i = i + 1) begin
                @(posedge clk);
                if (led_out[1] === 1'b1) seen_on  = 1;
                if (led_out[1] === 1'b0) seen_off = 1;
            end
            chk("RUNNING activity lamp both blinks on and off",
                seen_on == 1 && seen_off == 1);
        end

        //--------------------------------------------------------------
        // 8. disp_seg table and DP injection
        //--------------------------------------------------------------
        $display("");
        $display("  [4] disp_seg");

        sg_dp = 1'b0;
        sg_ch = 6'h00; #1; chk("char '0' -> 8'h3F", sg_out === 8'h3F);
        sg_ch = 6'h01; #1; chk("char '1' -> 8'h06", sg_out === 8'h06);
        sg_ch = 6'h08; #1; chk("char '8' -> 8'h7F", sg_out === 8'h7F);
        sg_ch = 6'h0E; #1; chk("char 'E' -> 8'h79", sg_out === 8'h79);
        sg_ch = 6'h1B; #1; chk("blank -> 8'h00", sg_out === 8'h00);
        sg_ch = 6'h2F; #1; chk("undefined code -> 8'h00", sg_out === 8'h00);

        // DP is bit 7 and must combine with any glyph
        sg_dp = 1'b1;
        sg_ch = 6'h00; #1; chk("'0' with DP -> 8'hBF", sg_out === 8'hBF);
        sg_ch = 6'h08; #1; chk("'8' with DP -> 8'hFF", sg_out === 8'hFF);
        sg_ch = 6'h1B; #1; chk("blank with DP -> 8'h80", sg_out === 8'h80);

        //--------------------------------------------------------------
        // 9. DIGIT ORDER: DK1 must be the hour tens digit
        //
        // BIT MAP -- memorise this, it is the opposite of reading order:
        //   DK1 (leftmost)  = chars[5:0]
        //   DK2             = chars[11:6]
        //   DK3             = chars[17:12]
        //   DK4             = chars[23:18]
        //   DK5             = chars[29:24]
        //   DK6             = chars[35:30]
        //   DK7             = chars[41:36]
        //   DK8 (rightmost) = chars[47:42]
        //
        // A Verilog concatenation puts its FIRST element in the most
        // significant bits, so DK8 is written first and DK1 last. Reading
        // the fields in left-to-right order mirrors the whole display, which
        // is exactly the bug that shipped once.
        //--------------------------------------------------------------
        $display("");
        $display("  [5] disp_content digit order and pages");

        dc_rst_n = 1'b0;
        repeat (5) @(posedge clk);
        dc_rst_n = 1'b1;
        repeat (5) @(posedge clk);

        dc_page  = 2'd0;
        dc_state = 2'd0;
        dc_day   = 4'd1;
        dc_sess  = 4'h1;

        // At 00:00:00 with day 1, the payload reads
        //   DK1 '1' DK2 blank DK3 '0' DK4 '0' DK5 '0' DK6 '0' DK7 '0' DK8 '0'
        @(posedge clk); #1;
        chk("DK1 weekday == '1'", dc_chars[5:0] === 6'h01);
        chk("DK2 blank     == ' '", dc_chars[11:6] === 6'h1B);
        chk("DK3 hour tens == '0'", dc_chars[17:12] === 6'h00);
        chk("DK4 hour ones == '0'", dc_chars[23:18] === 6'h00);
        chk("DK7 sec tens  == '0'", dc_chars[41:36] === 6'h00);
        chk("DK8 sec ones  == '0'", dc_chars[47:42] === 6'h00);
        if (dc_chars[5:0] !== 6'h01)
            $display("    got DK1=%0h (expected weekday '1')", dc_chars[5:0]);

        //--------------------------------------------------------------
        // 10. Time increments in the seconds field (DK7/DK8)
        //--------------------------------------------------------------
        tick1hz;
        repeat (2) @(posedge clk);
        chk("after 1 tick DK8 == '1' (seconds ones)", dc_chars[47:42] === 6'h01);
        chk("after 1 tick DK7 == '0' (seconds tens)", dc_chars[41:36] === 6'h00);
        if (!(dc_chars[47:42] === 6'h01 && dc_chars[41:36] === 6'h00))
            $display("    got DK7=%0h DK8=%0h", dc_chars[41:36], dc_chars[47:42]);

        //--------------------------------------------------------------
        // 11. BCD rollover 09 -> 10
        //--------------------------------------------------------------
        dc_rst_n = 1'b0;
        repeat (5) @(posedge clk);
        dc_rst_n = 1'b1;
        repeat (5) @(posedge clk);
        dc_page = 2'd0;

        for (i = 0; i < 9; i = i + 1) begin
            tick1hz;
            repeat (1) @(posedge clk);
        end
        chk("after 9 ticks DK8 == '9'", dc_chars[47:42] === 6'h09);

        tick1hz;
        repeat (2) @(posedge clk);
        chk("BCD rollover: DK7 == '1' and DK8 == '0'",
            dc_chars[41:36] === 6'h01 && dc_chars[47:42] === 6'h00);
        if (!(dc_chars[41:36] === 6'h01 && dc_chars[47:42] === 6'h00))
            $display("    got DK7=%0h DK8=%0h", dc_chars[41:36], dc_chars[47:42]);

        //--------------------------------------------------------------
        // 12. Decimal points sit after the hour and after the minute
        //     dps[0]=DK1 ... dps[7]=DK8
        //--------------------------------------------------------------
        chk("DP on DK4 (hour separator)", dc_dps[3] === 1'b1);
        chk("DP on DK6 (minute separator)", dc_dps[5] === 1'b1);
        chk("DP off on DK1..DK3", dc_dps[2:0] === 3'b000);
        chk("DP off on DK5", dc_dps[4] === 1'b0);
        chk("DP off on DK7/DK8", dc_dps[7:6] === 2'b00);

        //--------------------------------------------------------------
        // 13. Page 1 STATE, "0SH F    "
        //   DK1='0' DK2='S' DK3='H' DK4=blank DK5=state letter
        //--------------------------------------------------------------
        dc_page  = 2'd1;
        dc_state = 2'd1;                      // RUNNING -> 'r'
        @(posedge clk); #1;
        chk("page1 DK1..DK3 spell 0 S H",
            dc_chars[5:0]   === 6'h00 &&
            dc_chars[11:6]  === 6'h16 &&
            dc_chars[17:12] === 6'h10);
        if (!(dc_chars[5:0] === 6'h00 && dc_chars[11:6] === 6'h16 &&
              dc_chars[17:12] === 6'h10))
            $display("    got DK1=%0h DK2=%0h DK3=%0h",
                     dc_chars[5:0], dc_chars[11:6], dc_chars[17:12]);
        chk("page1 DK4 blank", dc_chars[23:18] === 6'h1B);
        chk("page1 DK5 == 'r' for RUNNING", dc_chars[29:24] === 6'h15);

        dc_state = 2'd2;                      // WAITING -> 'A'
        @(posedge clk); #1;
        chk("page1 DK5 == 'A' for WAITING", dc_chars[29:24] === 6'h0A);

        dc_state = 2'd0;                      // FREE -> 'F'
        @(posedge clk); #1;
        chk("page1 DK5 == 'F' for FREE", dc_chars[29:24] === 6'h0F);
        if (dc_chars[29:24] !== 6'h0F)
            $display("    got DK5=%0h", dc_chars[29:24]);
        chk("page1 DK6..DK8 blank",
            dc_chars[35:30] === 6'h1B &&
            dc_chars[41:36] === 6'h1B &&
            dc_chars[47:42] === 6'h1B);
        chk("page1 has no decimal points", dc_dps === 8'h00);

        //--------------------------------------------------------------
        // 14. Page 2 ASK
        //   DK1 = A = chars[5:0]
        //   DK2 = S = chars[11:6]
        //   DK3 = H = chars[17:12]
        //   DK4 = index = chars[23:18]
        //   DK5 = '-'   = chars[29:24]
        //   DK6 = total = chars[35:30]
        //--------------------------------------------------------------
        dc_page = 2'd2;
        dc_qi   = 4'h1;
        dc_qt   = 4'h2;
        @(posedge clk); #1;
        chk("page2 DK1..DK3 spell A S H",
            dc_chars[5:0]   === 6'h0A &&
            dc_chars[11:6]  === 6'h16 &&
            dc_chars[17:12] === 6'h10);
        if (!(dc_chars[5:0] === 6'h0A && dc_chars[11:6] === 6'h16 &&
              dc_chars[17:12] === 6'h10))
            $display("    got DK1=%0h DK2=%0h DK3=%0h",
                     dc_chars[5:0], dc_chars[11:6], dc_chars[17:12]);
        chk("page2 DK4 == '1' index", dc_chars[23:18] === 6'h01);
        chk("page2 DK5 == '-'",       dc_chars[29:24] === 6'h1A);
        chk("page2 DK6 == '2' total", dc_chars[35:30] === 6'h02);

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
