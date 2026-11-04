# Source files are staged to an ASCII path for Vivado on Windows.
# Run from this repository or copy these four files together and run this script.
set here [file dirname [file normalize [info script]]]
set work C:/AMD/hx_sync_axi_sim
file mkdir $work
foreach name {capture_fifo.v sync_capture_axi.v tb_sync_capture_axi.v} {
    set source [file join $here $name]
    set dest [file join $work $name]
    if {[file normalize $source] ne [file normalize $dest]} {file copy -force $source $dest}
}
create_project hx_sync_axi_sim $work/proj -part xc7z020clg400-1 -force
foreach name {capture_fifo.v sync_capture_axi.v tb_sync_capture_axi.v} {
    add_files -fileset sim_1 -norecurse [file join $work $name]
}
set_property top tb_sync_capture_axi [get_filesets sim_1]
update_compile_order -fileset sim_1
launch_simulation
run -all
set sim_log $work/proj/hx_sync_axi_sim.sim/sim_1/behav/xsim/simulate.log
set handle [open $sim_log r]
set result [read $handle]
close $handle
if {![regexp {PASS: all sync_capture_axi checks OK} $result] || [regexp {Fatal:|FAIL:|ERROR:} $result]} {
    puts "SIM_EXIT: FAIL (see $sim_log)"
    exit 1
}
puts "SIM_EXIT: PASS (verified simulator log)"
exit
