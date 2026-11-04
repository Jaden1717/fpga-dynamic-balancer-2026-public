# Bring-up overlay: PS7 + dual-channel AXI GPIO on JA PMOD (TUL PYNQ-Z2)
# Direct-plug layout: ch1 3-bit out on JA1/JA2/JA4 (CS/MOSI/SCLK), ch2 4-bit in on JA3/JA7/JA9/JA10 (MISO/INT1/INT2/DRDY)
# Vivado 2026.1 breaks on non-ASCII paths during IP out-of-context generation; build in C:/AMD
set here [file dirname [info script]]
set work C:/AMD/hengxin_build
if {[llength $argv] < 2} {error "Usage: bringup.tcl OUTPUT_DIRECTORY BOARD_REPOSITORY"}
set work [file normalize [lindex $argv 0]]
set board_repo [file normalize [lindex $argv 1]]
if {[file exists [file join $work bringup]]} {error "Output already contains a project"}

create_project bringup $work/bringup -part xc7z020clg400-1 -force
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

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 -name gpio_ja
set_property -dict [list CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO_WIDTH {3} CONFIG.C_GPIO2_WIDTH {4} \
    CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_ALL_INPUTS_2 {1} CONFIG.C_INTERRUPT_PRESENT {0}] [get_bd_cells gpio_ja]

apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master {/ps7/M_AXI_GP0} Clk_master {/ps7/FCLK_CLK0 (100 MHz)}} [get_bd_intf_pins gpio_ja/S_AXI]
make_bd_pins_external [get_bd_pins gpio_ja/gpio_io_o]
make_bd_pins_external [get_bd_pins gpio_ja/gpio2_io_i]
puts "ports now: [get_bd_ports *]"
set po [get_bd_ports -quiet *gpio_io_o*]
set pi [get_bd_ports -quiet *gpio2_io_i*]
if {[llength $po] == 1} { set_property name ja_o $po }
if {[llength $pi] == 1} { set_property name ja_i $pi }

assign_bd_address
validate_bd_design
save_bd_design

set wrapper [make_wrapper -files [get_files design_1.bd] -top]
add_files -norecurse $wrapper
add_files -fileset constrs_1 $here/bringup.xdc
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

write_hw_platform -fixed -include_bit -force -file $work/bringup.xsa
puts "DONE: [file normalize $work/bringup.xsa]"
exit
