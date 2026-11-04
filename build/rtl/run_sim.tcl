# xsim batch run for the ADXL355 acquisition controller.
# Vivado breaks on non-ASCII paths, so the sources are copied to C:/AMD/hx_sim first:
#   cp build/rtl/adxl355_acq.v build/rtl/tb_adxl355_acq.v C:/AMD/hx_sim/
# Run:  vivado.bat -mode batch -nojournal -source run_sim.tcl
set work C:/AMD/hx_sim
file mkdir $work/proj

create_project hx_acq_sim $work/proj -part xc7z020clg400-1 -force
add_files -fileset sim_1 -norecurse $work/adxl355_acq.v
add_files -fileset sim_1 -norecurse $work/tb_adxl355_acq.v
update_compile_order -fileset sim_1

launch_simulation
run -all
# XSim can report $fatal while the enclosing Vivado process still returns zero.
# Require this run's PASS marker and reject failures in the freshly written log.
set sim_log $work/proj/hx_acq_sim.sim/sim_1/behav/xsim/simulate.log
set handle [open $sim_log r]
set result [read $handle]
close $handle
if {![regexp {PASS: all adxl355_acq checks OK} $result] ||
    [regexp {Fatal:|FAIL t=|ERRORS:|TIMEOUT:} $result]} {
    puts "SIM_EXIT: FAIL (see $sim_log)"
    exit 1
}
puts "SIM_EXIT: PASS (verified simulator log)"
exit
