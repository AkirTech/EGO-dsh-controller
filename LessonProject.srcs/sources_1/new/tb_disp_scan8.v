`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module Name : tb_disp_scan8
// Description : Self-checking testbench for the 8-digit scan engine.
//
//   Checks performed:
//     1. Digit select is always one-hot, and DN0 / DN1 stay in strict lockstep
//     2. All four scan phases (idx = 0..3) are exercised
//     3. Each digit's segment pattern matches an independent reference model
//     4. Digit select and segment select are active HIGH
//     5. Outputs are fully blank during reset and never contain X afterwards
//
//   NOTE on the reference model
//     On a 7-segment display 'S' and '5' have identical segment patterns
//     (A, C, D, F, G lit). Decoding "pattern -> character" is therefore
//     ambiguous and would turn an intended 'S' into '5'. This testbench
//     instead builds the expected pattern forwards, by OR-ing named segment
//     bits. That is unambiguous and still an independent implementation,
//     because the DUT uses a plain hex constant table.
//
//   The tally (total cases / failed / result) is printed at the end.
//////////////////////////////////////////////////////////////////////////////////
module tb_disp_scan8;

    //------------------------------------------------------------------
    // Clock: 100 MHz
    //------------------------------------------------------------------
    reg clk = 1'b0;
    always #5 clk = ~clk;

    //------------------------------------------------------------------
    // DUT outputs
    //------------------------------------------------------------------
    wire [3:0] sm_wei;
    wire [7:0] sm_duan;
    wire [3:0] sm_wei1;
    wire [7:0] sm_duan1;

    //------------------------------------------------------------------
    // DUT with a small scan divider so simulation finishes quickly.
    // The divider value does not affect functional behaviour.
    //------------------------------------------------------------------
    localparam [31:0] TEST_SCAN_DIV = 32'd8;

    smg_ip #(
        .DN0_LEFT (1),
        .SCAN_DIV (TEST_SCAN_DIV)
    ) dut (
        .clk      (clk),
        .sm_wei   (sm_wei),
        .sm_duan  (sm_duan),
        .sm_wei1  (sm_wei1),
        .sm_duan1 (sm_duan1)
    );

    // Observe the DUT internal power-on reset through a hierarchical reference
    wire dut_rst_n = dut.rst_n;

    //------------------------------------------------------------------
    // Reference model: named segment bits, then forward-composed patterns.
    // Segment bit order is {DP, G, F, E, D, C, B, A} with bit0 = A.
    //------------------------------------------------------------------
    localparam [7:0] SEG_A  = 8'h01;
    localparam [7:0] SEG_B  = 8'h02;
    localparam [7:0] SEG_C  = 8'h04;
    localparam [7:0] SEG_D  = 8'h08;
    localparam [7:0] SEG_E  = 8'h10;
    localparam [7:0] SEG_F  = 8'h20;
    localparam [7:0] SEG_G  = 8'h40;
    localparam [7:0] SEG_DP = 8'h80;

    // Character identifiers used by the expectation table
    localparam [2:0] C_0 = 3'd0;   // '0', stands for d
    localparam [2:0] C_E = 3'd1;   // 'E'
    localparam [2:0] C_P = 3'd2;   // 'P'
    localparam [2:0] C_S = 3'd3;   // 'S'
    localparam [2:0] C_H = 3'd4;   // 'H', stands for k

    function [7:0] ref_seg;
        input [2:0] code;
        begin
            case (code)
                C_0: ref_seg = SEG_A | SEG_B | SEG_C | SEG_D | SEG_E | SEG_F;
                C_E: ref_seg = SEG_A | SEG_D | SEG_E | SEG_F | SEG_G;
                C_P: ref_seg = SEG_A | SEG_B | SEG_E | SEG_F | SEG_G;
                C_S: ref_seg = SEG_A | SEG_C | SEG_D | SEG_F | SEG_G;
                C_H: ref_seg = SEG_B | SEG_C | SEG_E | SEG_F | SEG_G;
                default: ref_seg = 8'h00;
            endcase
        end
    endfunction

    function [63:0] name_of;
        input [2:0] code;
        begin
            case (code)
                C_0: name_of = "0-for-d";
                C_E: name_of = "E";
                C_P: name_of = "P";
                C_S: name_of = "S";
                C_H: name_of = "H-for-k";
                default: name_of = "?";
            endcase
        end
    endfunction

    //------------------------------------------------------------------
    // Expected content, left to right (DK1 .. DK8)
    //   "deepseek" -> d e e p s e e k -> 0 E E P S E E H
    //------------------------------------------------------------------
    reg [2:0] exp_code [0:7];
    initial begin
        exp_code[0] = C_0;  // DK1
        exp_code[1] = C_E;  // DK2
        exp_code[2] = C_E;  // DK3
        exp_code[3] = C_P;  // DK4
        exp_code[4] = C_S;  // DK5
        exp_code[5] = C_E;  // DK6
        exp_code[6] = C_E;  // DK7
        exp_code[7] = C_H;  // DK8
    end

    //------------------------------------------------------------------
    // Scoreboard
    //------------------------------------------------------------------
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

    //------------------------------------------------------------------
    // Capture: record each scan phase's two segment patterns
    //------------------------------------------------------------------
    reg [7:0] seg0_by_idx [0:3];
    reg [7:0] seg1_by_idx [0:3];
    integer   collect_cnt;

    // Skip one clock after reset release: the outputs are still the
    // registered reset value for that single cycle, which is legal.
    reg armed   = 1'b0;
    reg rst_n_d = 1'b0;

    always @(posedge clk) begin
        rst_n_d <= dut_rst_n;
        if (dut_rst_n === 1'b1 && rst_n_d === 1'b1) armed <= 1'b1;
    end

    always @(posedge clk) begin
        if (armed === 1'b1) begin
            if ((sm_wei == 4'b0001 || sm_wei == 4'b0010 ||
                 sm_wei == 4'b0100 || sm_wei == 4'b1000) &&
                (sm_wei1 == 4'b0001 || sm_wei1 == 4'b0010 ||
                 sm_wei1 == 4'b0100 || sm_wei1 == 4'b1000)) begin
                case (sm_wei)
                    4'b0001: seg0_by_idx[0] = sm_duan;
                    4'b0010: seg0_by_idx[1] = sm_duan;
                    4'b0100: seg0_by_idx[2] = sm_duan;
                    4'b1000: seg0_by_idx[3] = sm_duan;
                    default: ;
                endcase
                case (sm_wei1)
                    4'b0001: seg1_by_idx[0] = sm_duan1;
                    4'b0010: seg1_by_idx[1] = sm_duan1;
                    4'b0100: seg1_by_idx[2] = sm_duan1;
                    4'b1000: seg1_by_idx[3] = sm_duan1;
                    default: ;
                endcase
                collect_cnt = collect_cnt + 1;
            end
        end
    end

    //------------------------------------------------------------------
    // Continuous assertions, active once armed
    //------------------------------------------------------------------
    integer onehot_err   = 0;
    integer lock_err     = 0;
    integer polarity_err = 0;
    integer x_err        = 0;
    integer seg0_trans   = 0;

    reg [7:0] last_seg0 = 8'h00;

    always @(posedge clk) begin
        if (armed === 1'b1) begin
            case (sm_wei)
                4'b0001, 4'b0010, 4'b0100, 4'b1000: ;
                default: onehot_err = onehot_err + 1;
            endcase
            case (sm_wei1)
                4'b0001, 4'b0010, 4'b0100, 4'b1000: ;
                default: onehot_err = onehot_err + 1;
            endcase
            if (sm_wei !== sm_wei1) lock_err = lock_err + 1;
            if (sm_wei === 4'b0000) polarity_err = polarity_err + 1;

            if ((^sm_duan) === 1'bx || (^sm_duan1) === 1'bx ||
                (^sm_wei)  === 1'bx || (^sm_wei1)  === 1'bx)
                x_err = x_err + 1;

            if (sm_duan !== last_seg0) seg0_trans = seg0_trans + 1;
            last_seg0 = sm_duan;
        end
    end

    // During reset all outputs must be blank
    integer reset_blank_err = 0;
    always @(posedge clk) begin
        if (dut_rst_n === 1'b0) begin
            if (sm_wei !== 4'b0000 || sm_duan !== 8'h00 ||
                sm_wei1 !== 4'b0000 || sm_duan1 !== 8'h00)
                reset_blank_err = reset_blank_err + 1;
        end
    end

    //------------------------------------------------------------------
    // Main sequence
    //------------------------------------------------------------------
    integer i;
    integer por_wait;

    initial begin
        collect_cnt = 0;
        for (i = 0; i < 4; i = i + 1) begin
            seg0_by_idx[i] = 8'hxx;
            seg1_by_idx[i] = 8'hxx;
        end

        $display("");
        $display("==========================================");
        $display("  8-digit 7-segment scan engine test");
        $display("  target string: deepseek -> 0EEPSEEH");
        $display("==========================================");

        //--------------------------------------------------------------
        // Wait for the internal POR to release
        //--------------------------------------------------------------
        por_wait = 0;
        while (dut_rst_n !== 1'b1 && por_wait < 2000) begin
            @(posedge clk);
            por_wait = por_wait + 1;
        end
        $display("  POR released after %0d clock cycles", por_wait);
        chk("POR releases within 2000 cycles", por_wait < 2000);

        //--------------------------------------------------------------
        // Run long enough to cover all four phases several times
        //--------------------------------------------------------------
        repeat (TEST_SCAN_DIV * 4 * 8) @(posedge clk);
        repeat (5) @(posedge clk);

        //--------------------------------------------------------------
        // Assertions
        //--------------------------------------------------------------
        chk("reset window outputs fully blank", reset_blank_err == 0);
        if (reset_blank_err != 0) $display("    reset-blank errors = %0d", reset_blank_err);

        chk("digit select always one-hot", onehot_err == 0);
        if (onehot_err != 0) $display("    onehot errors = %0d", onehot_err);

        chk("DN0 and DN1 strictly in lockstep", lock_err == 0);
        if (lock_err != 0) $display("    lockstep errors = %0d", lock_err);

        chk("digit select polarity active-high, never all-zero", polarity_err == 0);
        if (polarity_err != 0) $display("    polarity errors = %0d", polarity_err);

        chk("no X on any output", x_err == 0);
        if (x_err != 0) $display("    X errors = %0d", x_err);

        chk("segment outputs do change, dynamic scan is active", seg0_trans > 4);
        $display("  segment transitions observed = %0d", seg0_trans);

        chk("all 4 scan phases observed", collect_cnt > 0 &&
            seg0_by_idx[0] !== 8'hxx && seg0_by_idx[1] !== 8'hxx &&
            seg0_by_idx[2] !== 8'hxx && seg0_by_idx[3] !== 8'hxx &&
            seg1_by_idx[0] !== 8'hxx && seg1_by_idx[1] !== 8'hxx &&
            seg1_by_idx[2] !== 8'hxx && seg1_by_idx[3] !== 8'hxx);

        //--------------------------------------------------------------
        // Per-digit forward comparison against the reference model
        //--------------------------------------------------------------
        $display("");
        $display("  per-digit check (DUT vs reference model):");
        for (i = 0; i < 4; i = i + 1) begin
            $display("    DK%0d  expect %-8s seg=8'h%02X   got seg=8'h%02X  %0s",
                     i+1, name_of(exp_code[i]), ref_seg(exp_code[i]),
                     seg0_by_idx[i],
                     (seg0_by_idx[i] === ref_seg(exp_code[i])) ? "OK" : "MISMATCH");
            chk("DK1..DK4 digit matches reference",
                seg0_by_idx[i] === ref_seg(exp_code[i]));
        end
        for (i = 0; i < 4; i = i + 1) begin
            $display("    DK%0d  expect %-8s seg=8'h%02X   got seg=8'h%02X  %0s",
                     i+5, name_of(exp_code[i+4]), ref_seg(exp_code[i+4]),
                     seg1_by_idx[i],
                     (seg1_by_idx[i] === ref_seg(exp_code[i+4])) ? "OK" : "MISMATCH");
            chk("DK5..DK8 digit matches reference",
                seg1_by_idx[i] === ref_seg(exp_code[i+4]));
        end

        //--------------------------------------------------------------
        // Hint if the two groups are wired the other way round
        //--------------------------------------------------------------
        if (seg0_by_idx[0] === ref_seg(C_H) && seg1_by_idx[3] === ref_seg(C_0))
            $display("  [INFO] left/right groups appear swapped -> set DN0_LEFT = 0");

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
