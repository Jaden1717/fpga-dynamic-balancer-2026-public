set here [file dirname [file normalize [info script]]]
set work C:/AMD/hx_order1x_axi_sim
file mkdir $work
set files {order1x_accumulator.v cordic_vectoring.v order1x_core.v order1x_axi.v tb_order1x_axi.v}
set present {}
foreach name $files {
  set source [file join $here $name]
  if {![file exists $source]} {error "Required source missing: $source"}
  set dest [file join $work $name]
  if {[file normalize $source] ne [file normalize $dest]} {file copy -force $source $dest}
  lappend present $name
}
create_project hx_order1x_axi_sim $work/proj -part xc7z020clg400-1 -force
foreach name $present {add_files -fileset sim_1 -norecurse [file join $work $name]}
set_property file_type SystemVerilog [get_files -of_objects [get_filesets sim_1]]
set_property top tb_order1x_axi [get_filesets sim_1]
update_compile_order -fileset sim_1
launch_simulation
run -all
set sim_log $work/proj/hx_order1x_axi_sim.sim/sim_1/behav/xsim/simulate.log
set handle [open $sim_log r];set result [read $handle];close $handle
if {![regexp {PASS: all order1x_axi checks OK} $result] || [regexp {Fatal:|FAIL:|ERROR:} $result]} {
  puts "SIM_EXIT: FAIL (see $sim_log)";exit 1
}
puts "SIM_EXIT: PASS (verified simulator log)"
exit


