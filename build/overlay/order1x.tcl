# Independent PS-fed 64-angle 1x engine; acq_sync is kept intact.
set here [file normalize [file dirname [info script]]]
set work C:/AMD/hx_order1x_build
if {[llength $argv]} {set work [file normalize [lindex $argv 0]]}
if {[file exists [file join $work project]]} {error "Build output already contains a project; choose a new directory"}
if {[llength $argv] < 2} {error "Usage: order1x.tcl OUTPUT_DIRECTORY BOARD_REPOSITORY"}
set board_repo [file normalize [lindex $argv 1]]
if {![file exists [file join $board_repo pynq-z2 A.0 board.xml]]} {error "Pinned PYNQ-Z2 board dependency missing; run tools/prepare_board_files.py"}
file mkdir $work/src
foreach name {order1x_accumulator.v cordic_vectoring.v order1x_core.v order1x_axi.v} {file copy -force [file join $here ../rtl $name] [file join $work src $name]}
create_project order1x [file join $work project] -part xc7z020clg400-1 -force
set_param general.maxThreads 2
set_property board_part_repo_paths [list $board_repo] [current_project]
set board_id ""
foreach bp [get_board_parts] {if {[string match *pynq-z2* $bp]} {set board_id $bp}}
if {$board_id eq ""} {error "PYNQ-Z2 board part not found"}
set_property board_part $board_id [current_project]
foreach source [glob [file join $work src *.v]] {add_files -norecurse $source}
create_bd_design design_1
source [file join $here ps7_cfg.tcl]
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
set_property -dict $ps7_cfg [get_bd_cells ps7]
create_bd_cell -type module -reference order1x_axi order1x
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins order1x/clk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins order1x/rst_n]
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master {/ps7/M_AXI_GP0} Clk_master {/ps7/FCLK_CLK0 (100 MHz)}} [get_bd_intf_pins order1x/S_AXI]
assign_bd_address -offset 0x43C10000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs order1x/S_AXI/reg0] -force
validate_bd_design
save_bd_design
set wrapper [make_wrapper -files [get_files design_1.bd] -top]
add_files -norecurse $wrapper
set_property top design_1_wrapper [current_fileset]
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
report_timing_summary -file [file join $work timing_summary.rpt]
report_utilization -file [file join $work utilization.rpt]
report_drc -file [file join $work drc.rpt]
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
puts "ORDER1X_TIMING: WNS=$wns WHS=$whs"
if {$wns < 0 || $whs < 0} {error "Routed100MHz timing did not close"}
puts "ORDER1X_DSP: [llength [get_cells -hier -filter {REF_NAME =~ DSP*}]]"
write_hw_platform -fixed -include_bit -force -file [file join $work order1x.xsa]
file copy -force [file join $work project order1x.runs impl_1 design_1_wrapper.bit] [file join $work order1x.bit]
puts "ORDER1X_BUILD_EXIT: PASS [file join $work order1x.xsa]"
exit 0
