# Always stage current checkout sources into an ASCII path.
set here [file normalize [file dirname [info script]]]
set work C:/AMD/hx_order1x_accumulator
file mkdir $work
foreach name {order1x_accumulator.v tb_order1x_accumulator.v} {
    file copy -force [file join $here $name] [file join $work $name]
}
create_project hx_order1x_accumulator $work/proj -part xc7z020clg400-1 -force
add_files -fileset sim_1 -norecurse [list $work/order1x_accumulator.v $work/tb_order1x_accumulator.v]
set_property file_type SystemVerilog [get_files $work/tb_order1x_accumulator.v]
set_property top tb_order1x_accumulator [get_filesets sim_1]
update_compile_order -fileset sim_1
launch_simulation
run -all
set handle [open $work/proj/hx_order1x_accumulator.sim/sim_1/behav/xsim/simulate.log r]
set result [read $handle]
close $handle
if {![regexp {PASS: order1x_accumulator} $result] || [regexp {Fatal:|FAIL:|TIMEOUT:} $result]} {
 puts "ORDER1X_ACCUMULATOR_SIM_EXIT: FAIL"
 exit 1
}
puts "ORDER1X_ACCUMULATOR_SIM_EXIT: PASS"
exit
