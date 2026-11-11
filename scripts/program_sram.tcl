# ==============================================================================
# Program the M2 bitstream into the FPGA's SRAM (volatile, fast).
#
# WHY THIS EXISTS
#   `program_flash.tcl` writes the QSPI flash so the design survives power-off,
#   but that needs the flash to be readable over JTAG, which is not reliable on
#   every board. Loading SRAM always works and takes about two seconds, so this
#   is the fast path for iterating on the design.
#
#   SRAM is lost on power-off, so after a power cycle either re-run this, or
#   hold PROG (S5) to reload whatever is in the flash.
#
# USAGE
#   $env:PATH = "D:\Vivado\Vivado\2017.1\bin;$env:PATH"
#   vivado.bat -mode batch -source scripts\program_sram.tcl
#
#   Override the bitstream:
#   vivado.bat -mode batch -source scripts\program_sram.tcl -tclargs <path-to.bit>
# ==============================================================================

set proj_dir [file normalize [file join [file dirname [info script]] ..]]

set bit_file [lindex $argv 0]
if {$bit_file eq ""} {
    set bit_file [file join $proj_dir prj_out m2_spix4.bit]
}
set bit_file [file normalize $bit_file]

if {![file exists $bit_file]} {
    error "bitstream not found: $bit_file"
}

puts "== bitstream : $bit_file"
puts "== size      : [file size $bit_file] bytes"

# The hw_* commands are lazily loaded in 2017.1.
load_features labtools
open_hw
connect_hw_server

# open_hw_target fails with "There is no current hw_target" when the cable is
# absent, which is the most common situation. Turn it into an instruction.
if {[catch {open_hw_target} err]} {
    catch { disconnect_hw_server }
    catch { close_hw }
    puts ""
    puts "=================================================================="
    puts " No JTAG cable found."
    puts ""
    puts " Check:"
    puts "   1. A USB cable is plugged into J22 (the USB-JTAG port)."
    puts "   2. The 3-pin jumper is on 'middle <-> QSPI'."
    puts "      With it on the JTAG side and no external programmer attached,"
    puts "      the board enumerates as Unknown USB Device (error 43)."
    puts "   3. The board is powered."
    puts "=================================================================="
    error $err
}

set dev [lindex [get_hw_devices] 0]
if {$dev eq ""} {
    error "no FPGA found; check the J22 USB-JTAG cable and that the board is powered"
}
current_hw_device $dev
puts "== device    : $dev  part: [get_property PART $dev]"

set_property PROGRAM.FILE $bit_file $dev
puts "== programming SRAM..."
program_hw_devices $dev

refresh_hw_device -update_hw_probes false $dev
set done [get_property REGISTER.CONFIG_STATUS.BIT14_DONE_PIN $dev]
puts "== DONE      : $done"

close_hw_target
disconnect_hw_server
close_hw

puts ""
if {$done != 1} {
    error "DONE did not assert; the design is not running"
}
puts "=================================================================="
puts " Loaded into SRAM. It survives until the board loses power."
puts " For a power-cycle-proof image, program the flash instead:"
puts "     vivado.bat -mode batch -source scripts\\program_flash.tcl"
puts "=================================================================="
