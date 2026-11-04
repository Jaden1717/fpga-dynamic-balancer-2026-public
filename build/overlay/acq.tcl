# Acquisition overlay: PS7 + adxl355_acq (the PL is the only SPI master on JA).
# gpio_ja is deliberately gone: ja_o[0..2] and ja_i[0]/ja_i[3] are now driven by the
# controller, and two drivers on one PACKAGE_PIN is a constraint error, not a warning.
# bringup.xsa stays in the tree as the bit-bang fallback.
# Vivado 2026.1 breaks on non-ASCII paths during IP out-of-context generation, so the RTL
# is copied into C:/AMD before being added; the project is built there too.
set here [file dirname [info script]]
set work C:/AMD/hengxin_build
if {[llength $argv] < 2} {error "Usage: acq.tcl OUTPUT_DIRECTORY BOARD_REPOSITORY"}
set work [file normalize [lindex $argv 0]]
set board_repo [file normalize [lindex $argv 1]]
if {[file exists [file join $work acq]]} {error "Output already contains a project"}
set src  $work/src
file mkdir $src
file copy -force $here/../rtl/adxl355_acq.v $src/adxl355_acq.v

create_project acq $work/acq -part xc7z020clg400-1 -force
set_property board_part_repo_paths [list $board_repo] [current_project]
set board_id ""
foreach bp [get_board_parts] {
    if {[string match *pynq-z2* $bp]} { set board_id $bp }
}
if {$board_id eq ""} { puts "ERROR: pynq-z2 board part not found"; exit 1 }
puts "Using board_part: $board_id"
set_property board_part $board_id [current_project]

create_bd_design "design_1"

source $here/ps7_cfg.tcl
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 -name ps7
set_property -dict $ps7_cfg [get_bd_cells ps7]

add_files -norecurse $src/adxl355_acq.v
create_bd_cell -type module -reference adxl355_acq -name acq
puts "acq pins: [get_bd_pins acq/*]"

# Acquisition-only overlay has no common timebase; disable the optional sideband clear.
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 capture_zero
set_property -dict [list CONFIG.CONST_WIDTH {64} CONFIG.CONST_VAL {0}] [get_bd_cells capture_zero]
connect_bd_net [get_bd_pins capture_zero/dout] [get_bd_pins acq/capture_ticks_i]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 clear_zero
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] [get_bd_cells clear_zero]
connect_bd_net [get_bd_pins clear_zero/dout] [get_bd_pins acq/capture_clear_i]

connect_bd_net [get_bd_pins ps7/FCLK_CLK0]    [get_bd_pins acq/clk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins acq/rst_n]

apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master {/ps7/M_AXI_GP0} Clk_master {/ps7/FCLK_CLK0 (100 MHz)}} [get_bd_intf_pins acq/S_AXI]

# make_bd_pins_external returns nothing useful here (bringup.tcl hit the same thing),
# so create the ports first and rename them by pattern afterwards.
make_bd_pins_external [get_bd_pins acq/spi_cs_o]
make_bd_pins_external [get_bd_pins acq/spi_sclk_o]
make_bd_pins_external [get_bd_pins acq/spi_mosi_o]
make_bd_pins_external [get_bd_pins acq/spi_miso_i]
make_bd_pins_external [get_bd_pins acq/drdy_i]
puts "raw port names: [get_bd_ports *]"
set_property name spi_cs   [get_bd_ports *spi_cs_o*]
set_property name spi_sclk [get_bd_ports *spi_sclk_o*]
set_property name spi_mosi [get_bd_ports *spi_mosi_o*]
set_property name spi_miso [get_bd_ports *spi_miso_i*]
set_property name drdy     [get_bd_ports *drdy_i*]
puts "ports now: [get_bd_ports *]"

# Vivado sized the segment to 64 KB from the 16-bit AWADDR; keeping the old gpio_ja base
# means the board scripts carry one address for both overlays. The RTL decodes only
# addr[14:2], so the upper 32 KB is a mirror of the lower half and is never used.
assign_bd_address -offset 0x41200000 -target_address_space [get_bd_addr_spaces ps7/Data] \
    [get_bd_addr_segs acq/S_AXI/reg0] -force
validate_bd_design
save_bd_design

set wrapper [make_wrapper -files [get_files design_1.bd] -top]
add_files -norecurse $wrapper
add_files -fileset constrs_1 $here/acq.xdc
set_property top design_1_wrapper [current_fileset]

launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    puts "ERROR: synthesis failed"
    exit 1
}

launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    puts "ERROR: implementation failed"
    exit 1
}

write_hw_platform -fixed -include_bit -force -file $work/acq.xsa
set bit [glob -nocomplain $work/acq/acq.runs/impl_1/design_1_wrapper.bit]
if {[llength $bit] == 1} {
    file copy -force $bit $work/acq.bit
    puts "bit copied: $bit"
} else {
    puts "NOTE: no standalone bit found, acq.xsa carries it"
}
puts "DONE: [file normalize $work/acq.xsa]"
exit
