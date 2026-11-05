# ==============================================================================
# EGO1 (xc7a35tcsg324-1) pin constraints for the 8-digit 7-segment display
# Pin source: official EGo1 User Manual, section 5.4 (7-segment display)
# ==============================================================================

# ---- System clock: 100 MHz oscillator on P17 ----
set_property PACKAGE_PIN P17 [get_ports clk]
set_property IOSTANDARD LVCMOS33 [get_ports clk]
create_clock -period 10.000 -name sys_clk [get_ports clk]

# ==============================================================================
# DN0 : digits 1..4
# ==============================================================================

# ---- Digit select: LED_BIT1 .. LED_BIT4 ----
set_property PACKAGE_PIN G2 [get_ports {sm_wei[0]}]
set_property PACKAGE_PIN C2 [get_ports {sm_wei[1]}]
set_property PACKAGE_PIN C1 [get_ports {sm_wei[2]}]
set_property PACKAGE_PIN H1 [get_ports {sm_wei[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_wei[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_wei[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_wei[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_wei[3]}]

# ---- Segment outputs: LED0_CA / CB / CC / CD / CE / CF / CG / DP0 ----
set_property PACKAGE_PIN B4 [get_ports {sm_duan[0]}]
set_property PACKAGE_PIN A4 [get_ports {sm_duan[1]}]
set_property PACKAGE_PIN A3 [get_ports {sm_duan[2]}]
set_property PACKAGE_PIN B1 [get_ports {sm_duan[3]}]
set_property PACKAGE_PIN A1 [get_ports {sm_duan[4]}]
set_property PACKAGE_PIN B3 [get_ports {sm_duan[5]}]
set_property PACKAGE_PIN B2 [get_ports {sm_duan[6]}]
set_property PACKAGE_PIN D5 [get_ports {sm_duan[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan[7]}]

# ==============================================================================
# DN1 : digits 5..8
# ==============================================================================

# ---- Digit select: LED_BIT5 .. LED_BIT8 ----
set_property PACKAGE_PIN G1 [get_ports {sm_wei1[0]}]
set_property PACKAGE_PIN F1 [get_ports {sm_wei1[1]}]
set_property PACKAGE_PIN E1 [get_ports {sm_wei1[2]}]
set_property PACKAGE_PIN G6 [get_ports {sm_wei1[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_wei1[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_wei1[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_wei1[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_wei1[3]}]

# ---- Segment outputs: LED1_CA / CB / CC / CD / CE / CF / CG / DP1 ----
set_property PACKAGE_PIN D4 [get_ports {sm_duan1[0]}]
set_property PACKAGE_PIN E3 [get_ports {sm_duan1[1]}]
set_property PACKAGE_PIN D3 [get_ports {sm_duan1[2]}]
set_property PACKAGE_PIN F4 [get_ports {sm_duan1[3]}]
set_property PACKAGE_PIN F3 [get_ports {sm_duan1[4]}]
set_property PACKAGE_PIN E2 [get_ports {sm_duan1[5]}]
set_property PACKAGE_PIN D2 [get_ports {sm_duan1[6]}]
set_property PACKAGE_PIN H2 [get_ports {sm_duan1[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan1[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan1[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan1[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan1[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan1[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan1[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan1[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sm_duan1[7]}]
