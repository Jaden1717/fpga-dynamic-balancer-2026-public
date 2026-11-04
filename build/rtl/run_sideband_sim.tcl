# Stage adxl355_acq.v and tb_adxl355_sideband.v in C:/AMD/hx_sideband_sim.
# Optional first argument "legacy-red" proves the pre-feature restart leak.
set work C:/AMD/hx_sideband_sim
create_project hx_sideband_sim $work/proj -part xc7z020clg400-1 -force
add_files -fileset sim_1 -norecurse [list $work/adxl355_acq.v $work/tb_adxl355_sideband.v]
if {[llength $argv] == 0 || [lindex $argv 0] ne "legacy-red"} {
    set_property verilog_define SIDEBAND_PORTS [get_filesets sim_1]
}
set_property top tb_adxl355_sideband [get_filesets sim_1]
update_compile_order -fileset sim_1
launch_simulation
run -all
set handle [open $work/proj/hx_sideband_sim.sim/sim_1/behav/xsim/simulate.log r]
set result [read $handle]
close $handle
if {![regexp {PASS: (ADXL355 sideband|restart epoch regression)} $result] ||
    [regexp {Fatal:|FAIL:|TIMEOUT:} $result]} {puts "SIDEBAND_SIM_EXIT: FAIL";exit 1}
puts "SIDEBAND_SIM_EXIT: PASS"
exit
