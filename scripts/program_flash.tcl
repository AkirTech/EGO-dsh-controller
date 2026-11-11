# ==============================================================================
# Program the EGO1's QSPI flash so the design survives power-off.
#
# WHY THIS IS NEEDED
#   A .bit programmed over JTAG goes into the FPGA's internal SRAM. That memory
#   is volatile: every power cycle loses it. The board's N25Q32 QSPI flash is
#   the only non-volatile place for a bitstream, and the FPGA is strapped to
#   boot from it (CONFIG_STATUS MODE_PIN_M[2:0] = 001 = Master SPI).
#
#   So: .bit -> .mcs (write_cfgmem) -> program_hw_cfgmem -> power cycle -> it
#   runs on its own.
#
# USAGE
#   Option 1, GUI:  Tools > Generate Memory Configuration File, then
#                   Flow > Open Hardware Manager > Add Configuration Memory
#                   Device. Equivalent to this script.
#   Option 2, batch:
#       $env:PATH = "D:\Vivado\Vivado\2017.1\bin;$env:PATH"
#       vivado.bat -mode batch -source program_flash.tcl
#
#   Override the defaults from the command line with tclargs:
#       vivado.bat -mode batch -source program_flash.tcl -tclargs <bit> <buswidth>
#
# SAFETY
#   * This ERASES the whole flash. Nothing else lives there on this board, but
#     if the design ever moves to multi-boot (golden + update images), read the
#     notes in docs/SERIAL-PROTOCOL.md before running this.
#   * If the board fails to start afterwards: hold S5 (PROG) while applying
#     power. That clears the configuration and returns the FPGA to a state
#     where JTAG can program it again.
# ==============================================================================

#-------------------------------------------------------------------------------
# Parameters
#
# This file lives in <project>/scripts/, so the project root is one level up.
#-------------------------------------------------------------------------------
set proj_dir  [file normalize [file join [file dirname [info script]] ..]]
set bit_file  [lindex $argv 0]
set buswidth  [lindex $argv 1]

if {$bit_file eq ""} {
    set bit_file [file join $proj_dir prj_out m1_buttons_leds.bit]
}
if {$buswidth eq ""} {
    set buswidth 4
}

set bit_file [file normalize $bit_file]
if {![file exists $bit_file]} {
    error "bitstream not found: $bit_file"
}

# The flash file goes next to the bitstream and takes the bus width into the
# name, so an x1 and an x4 build can never be confused for each other.
set stem     [file rootname $bit_file]
set mcs_file "${stem}_spix${buswidth}.mcs"

puts "== bitstream : $bit_file"
puts "== bus width : SPIx$buswidth"
puts "== flash file: $mcs_file"

#-------------------------------------------------------------------------------
# 1. Build the flash image.
#
# The bitstream header records which SPI width the FPGA will use to read the
# flash, and write_cfgmem refuses to build an image whose width disagrees:
#
#   ERROR: [Writecfgmem 68-20] SPI_BUSWIDTH property is set to "1" on bitfile
#   ... This property has to be set to "4" to generate a configuration memory
#   file for the SPIX4 interface.
#
# That message goes to stderr and is NOT part of the Tcl error value, which is
# only "ERROR: [Common 17-39] 'write_cfgmem' failed due to earlier errors".
# So the reliable signal is simply that the output file was not produced.
#-------------------------------------------------------------------------------
set made_ok [expr {![catch {
    write_cfgmem -force -format MCS -size 4 -interface SPIx${buswidth} \
        -loadbit "up 0x00000000 $bit_file" $mcs_file
}]}]

if {!$made_ok || ![file exists $mcs_file]} {
    puts ""
    puts "=================================================================="
    puts " Could not build $mcs_file"
    puts ""
    puts " Most likely cause: the bitstream's SPI_BUSWIDTH differs from the"
    puts " requested SPIx$buswidth (see the Writecfgmem 68-20 line above)."
    puts ""
    puts " Fix A -- regenerate the bitstream with a matching width:"
    puts "     set_property BITSTREAM.CONFIG.SPI_BUSWIDTH $buswidth \[current_design\]"
    puts "   in const1.xdc, then Generate Bitstream. The existing .bit cannot"
    puts "   be patched; a new one is required."
    puts ""
    puts " Fix B -- keep the current bitstream and use its width:"
    puts "     vivado.bat -mode batch -source program_flash.tcl -tclargs $bit_file 1"
    puts "=================================================================="
    error "flash image generation failed"
}

puts "== flash image written: $mcs_file"

#-------------------------------------------------------------------------------
# 2. Attach to the device. No bitstream is loaded into the FPGA first: the
#    flash is independent of whatever currently runs, and skipping the load
#    avoids a pointless reconfiguration of a board that may be running fine.
#
#    The hw_* commands are lazily loaded in 2017.1, so load_features labtools
#    has to come first -- otherwise open_hw is not even a known command.
#-------------------------------------------------------------------------------
# DRY_RUN=1 stops after the flash image is built. Used to validate the script
# and the bitstream/width agreement without erasing a board.
if {[info exists ::env(EGO_FLASH_DRY_RUN)]} {
    puts "== EGO_FLASH_DRY_RUN set: stopping before any hardware access"
    puts "== would program: $mcs_file -> n25q32-3.3v-spi-x1_x2_x4"
    return
}

load_features labtools
open_hw
connect_hw_server
open_hw_target

set dev [lindex [get_hw_devices] 0]
if {$dev eq ""} {
    error "no FPGA found; check the USB-JTAG cable and that the board is powered"
}
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev
puts "== device: $dev  part: [get_property PART $dev]"

#-------------------------------------------------------------------------------
# 3. Create the flash object.
#
# The part MUST be the 3.3 V variant. The manual states the N25Q32 on this board
# runs from 3.3 V; the 1.8 V entry also appears in Vivado's list and selecting
# it gives silent erase/program failures rather than an error.
#-------------------------------------------------------------------------------
set part_name "n25q32-3.3v-spi-x1_x2_x4"
# NOTE: Vivado's `filter` does not interpolate ${...} inside its expression --
# braces suppress substitution, and a bare ${part_name} there is a syntax
# error ("There was a syntax error while parsing filter expression"). Build the
# expression with a quoted string so Tcl substitutes first, then hand that to
# filter as its expression argument.
set part_list [filter [get_cfgmem_parts] "NAME == $part_name"]
if {[llength $part_list] == 0} {
    error "cfgmem part $part_name not offered by this Vivado"
}
create_hw_cfgmem -hw_device $dev [lindex $part_list 0]
set cfgmem [get_property PROGRAM.HW_CFGMEM $dev]
puts "== cfgmem: [get_property CFGMEM_PART $cfgmem]"

#-------------------------------------------------------------------------------
# 4. Program.
#
# ERASE is required: the flash is written as a whole image and the old content
# would otherwise corrupt the new bitstream's tail. VERIFY reads it back; it
# roughly doubles the time and is worth keeping, because a flash write that
# silently failed is otherwise indistinguishable from a design bug.
#-------------------------------------------------------------------------------
set_property PROGRAM.BLANK_CHECK  0 $cfgmem
set_property PROGRAM.ERASE        1 $cfgmem
set_property PROGRAM.CFG_PROGRAM  1 $cfgmem
set_property PROGRAM.VERIFY       1 $cfgmem
set_property PROGRAM.CHECKSUM     0 $cfgmem
set_property PROGRAM.FILES [list $mcs_file] $cfgmem

puts "== erasing and programming (this takes a minute)"
program_hw_cfgmem -hw_cfgmem $cfgmem

close_hw_target
disconnect_hw_server
close_hw

puts ""
puts "=================================================================="
puts " Done. Power-cycle the board to confirm it boots on its own."
puts " If it does not start: hold S5 (PROG) while applying power, then"
puts " re-run this script with a matching bus width."
puts "=================================================================="
