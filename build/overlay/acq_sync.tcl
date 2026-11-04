# Independent overlay: legacy acq ABI + common timestamp + two record FIFOs.
# Default build stays in an English path; source overlay acq.xsa is not overwritten.
set here [file normalize [file dirname [info script]]]
set work C:/AMD/hx_acq_sync_build
if {[llength $argv] > 0} {set work [file normalize [lindex $argv 0]]}
if {[file exists [file join $work project]]} {error "Build output already contains a project; choose a new directory"}
if {[llength $argv] < 2} {error "Usage: acq_sync.tcl OUTPUT_DIRECTORY BOARD_REPOSITORY"}
set board_repo [file normalize [lindex $argv 1]]
if {![file exists [file join $board_repo pynq-z2 A.0 board.xml]]} {error "Pinned PYNQ-Z2 board dependency missing; run tools/prepare_board_files.py"}
set src [file join $work src]
file mkdir $src
set rtl_names {adxl355_acq.v capture_timebase.v tacho_timestamp.v capture_fifo.v sync_capture_axi.v}
foreach name $rtl_names {file copy -force [file join $here ../rtl $name] [file join $src $name]}
create_project acq_sync [file join $work project] -part xc7z020clg400-1 -force
set_param general.maxThreads 2
set_property board_part_repo_paths [list $board_repo] [current_project]
set board_id ""
foreach bp [get_board_parts] {if {[string match *pynq-z2* $bp]} {set board_id $bp}}
if {$board_id eq ""} {error "PYNQ-Z2 board part not found"}
set_property board_part $board_id [current_project]
foreach name $rtl_names {add_files -norecurse [file join $src $name]}
create_bd_design design_1
source [file join $here ps7_cfg.tcl]
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
set_property -dict $ps7_cfg [get_bd_cells ps7]
create_bd_cell -type module -reference adxl355_acq acq
create_bd_cell -type module -reference capture_timebase timebase
create_bd_cell -type module -reference tacho_timestamp optical
set_property -dict [list CONFIG.COUNTER_BITS {64} CONFIG.FILTER_CYCLES {4} CONFIG.RISING_EDGE {0}] [get_bd_cells optical]
create_bd_cell -type module -reference sync_capture_axi sync_capture
set_property -dict [list CONFIG.SAMPLE_DEPTH {4096} CONFIG.PULSE_DEPTH {256} CONFIG.CLOCK_HZ {100000000}] [get_bd_cells sync_capture]
foreach cell {acq timebase optical sync_capture} {
    connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins $cell/clk]
    connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins $cell/rst_n]
}
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] [get_bd_cells zero]
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins timebase/sample_strobe]
connect_bd_net [get_bd_pins timebase/time_ticks] [get_bd_pins acq/capture_ticks_i] [get_bd_pins optical/time_ticks]
connect_bd_net [get_bd_pins sync_capture/capture_enable_o] [get_bd_pins optical/enable]
connect_bd_net [get_bd_pins sync_capture/capture_clear_o] [get_bd_pins optical/clear] [get_bd_pins acq/capture_clear_i]
foreach sig {valid frame ticks x y z} {connect_bd_net [get_bd_pins acq/sample_${sig}_o] [get_bd_pins sync_capture/sample_${sig}_i]}
connect_bd_net [get_bd_pins acq/capture_idle_o] [get_bd_pins sync_capture/source_idle_i]
connect_bd_net [get_bd_pins acq/capture_missed_o] [get_bd_pins sync_capture/source_missed_i]
connect_bd_net [get_bd_pins optical/event_valid] [get_bd_pins sync_capture/pulse_valid_i]
connect_bd_net [get_bd_pins optical/event_ticks] [get_bd_pins sync_capture/pulse_ticks_i]
connect_bd_net [get_bd_pins optical/pulse_count] [get_bd_pins sync_capture/pulse_count_i]
foreach cell {acq sync_capture} {
    apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master {/ps7/M_AXI_GP0} Clk_master {/ps7/FCLK_CLK0 (100 MHz)}} [get_bd_intf_pins $cell/S_AXI]
}
foreach pair {{spi_cs_o spi_cs} {spi_sclk_o spi_sclk} {spi_mosi_o spi_mosi} {spi_miso_i spi_miso} {drdy_i drdy}} {
    lassign $pair pin name
    make_bd_pins_external [get_bd_pins acq/$pin]
    set_property name $name [get_bd_ports *$pin*]
}
make_bd_pins_external [get_bd_pins optical/tacho_async]
set_property name tacho [get_bd_ports *tacho_async*]
assign_bd_address -offset 0x41200000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs acq/S_AXI/reg0] -force
assign_bd_address -offset 0x43C00000 -range 64K -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs sync_capture/S_AXI/reg0] -force
validate_bd_design
save_bd_design
set wrapper [make_wrapper -files [get_files design_1.bd] -top]
add_files -norecurse $wrapper
add_files -fileset constrs_1 [file join $here acq_sync.xdc]
set_property top design_1_wrapper [current_fileset]
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%" || [get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {error "Synthesis failed"}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
report_timing_summary -file [file join $work timing_summary.rpt]
report_utilization -file [file join $work utilization.rpt]
report_drc -file [file join $work drc.rpt]
set setup_paths [get_timing_paths -delay_type max -max_paths 1]
set hold_paths [get_timing_paths -delay_type min -max_paths 1]
if {![llength $setup_paths] || ![llength $hold_paths]} {error "No timing paths found"}
set wns [get_property SLACK $setup_paths]
set whs [get_property SLACK $hold_paths]
puts "SYNC_TIMING: WNS=$wns WHS=$whs"
if {$wns < 0 || $whs < 0} {error "Internal timing failed"}
puts "SYNC_BRAM: [llength [get_cells -hier -filter {REF_NAME =~ RAMB*}]]"
write_hw_platform -fixed -include_bit -force -file [file join $work acq_sync.xsa]
file copy -force [file join $work project acq_sync.runs impl_1 design_1_wrapper.bit] [file join $work acq_sync.bit]
puts "SYNC_BUILD_EXIT: PASS [file join $work acq_sync.xsa]"
exit 0
