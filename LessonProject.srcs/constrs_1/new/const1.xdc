# ==============================================================================
# EGO1 (xc7a35tcsg324-1) pin constraints
# Pin source: official EGo1 User Manual sections 3, 5.1, 5.3, 5.4
# ==============================================================================

# ==============================================================================
# Bitstream: how the FPGA reads the QSPI flash at power-up
#
# The board carries an N25Q32 (32 Mbit / 4 MB, 3.3 V) QSPI flash. This property
# goes into the .bit header and tells the FPGA how many data lines to use when
# it loads that flash on power-up:
#
#   SPI_BUSWIDTH 1 -> ~1.7 s to load a 2.2 MB image
#   SPI_BUSWIDTH 4 -> ~0.4 s (the N25Q32 supports x4 read; verified in
#                     Vivado's data/xicom/spi.cfg: X4_READ = TRUE)
#
# IT MUST MATCH the -interface value passed to write_cfgmem, otherwise Vivado
# refuses to make the flash file:
#
#   ERROR: [Writecfgmem 68-20] SPI_BUSWIDTH property is set to "1" on bitfile
#   ... This property has to be set to "4" to generate a configuration memory
#   file for the SPIX4 interface.
#
# Changing this property changes the bitstream, so a NEW bitstream must be
# generated -- an existing .bit cannot be reused.
#
# Set to 4 for a fast boot. If a board ever fails to start after flashing,
# hold the PROG button (S5) while powering on to clear the configuration, then
# re-flash; falling back to 1 here is the other way out.
# ==============================================================================
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]

# ---- System clock: 100 MHz oscillator on P17 ----
set_property PACKAGE_PIN P17 [get_ports clk]
set_property IOSTANDARD LVCMOS33 [get_ports clk]
create_clock -period 10.000 -name sys_clk [get_ports clk]

# ==============================================================================
# Buttons, active HIGH (manual 5.1): idle 0, pressed 1
#   key[0] = S2 PB2 R15  center
#   key[1] = S3 PB3 V1   left
#   key[2] = S0 PB0 R11  right
#   key[3] = S1 PB1 R17  spare
#   key[4] = S4 PB4 U4   spare
# ==============================================================================
set_property PACKAGE_PIN R15 [get_ports {key[0]}]
set_property PACKAGE_PIN V1  [get_ports {key[1]}]
set_property PACKAGE_PIN R11 [get_ports {key[2]}]
set_property PACKAGE_PIN R17 [get_ports {key[3]}]
set_property PACKAGE_PIN U4  [get_ports {key[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {key[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {key[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {key[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {key[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {key[4]}]

# ==============================================================================
# Serial link to the on-board CP2102 USB-UART bridge (manual section 8)
#
#   uart_tx = FPGA -> PC : ball T4, net named UART_RX from the CP2102's side
#   uart_rx = PC   -> FPGA: ball N5, net named UART_TX from the CP2102's side
#
# The manual's own parenthetical ("FPGA serial transmit / receive") and the
# net names are worded from opposite ends, so the names look swapped. The
# assignment below follows the manual's parenthetical: T4 is the FPGA's
# transmitter. If a loopback test shows nothing at all, swapping these two
# PACKAGE_PIN lines is the first thing to try -- the RTL does not change.
# ==============================================================================
set_property PACKAGE_PIN T4 [get_ports uart_tx]
set_property PACKAGE_PIN N5 [get_ports uart_rx]
set_property IOSTANDARD LVCMOS33 [get_ports uart_tx]
set_property IOSTANDARD LVCMOS33 [get_ports uart_rx]

# ==============================================================================
# Indicator lamps, two of them
#   led_state[0] = K1  on-board D8  : "alive" lamp, always on
#   led_state[1] = K6  on-board D12 : activity lamp
#                                     FREE off / RUNNING 1 Hz blink / WAITING on
#
# Board notes
#   The EGo1 base board has no RGB LED; every one of the 16 on-board LEDs
#   (D0-D15) is green per manual section 5.3. The two lamps above are ordinary
#   green LEDs, so the status is read from the on/off/blink pattern rather
#   than from colour.
#
#   The user refers to these as "D9" and "D13", but manual 5.3 maps D8 to K1
#   and D12 to K6, so the D numbers given offline do not line up with the
#   manual. The ball positions K1 and K6 were stated explicitly and are used
#   here; if the panel silkscreen numbers them differently, only these two
#   PACKAGE_PIN lines change and the RTL stays untouched.
# ==============================================================================
set_property PACKAGE_PIN K1 [get_ports {led_state[0]}]
set_property PACKAGE_PIN K6 [get_ports {led_state[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led_state[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led_state[1]}]

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
