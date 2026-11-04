set here [file normalize [file dirname [info script]]]
set work C:/AMD/hx_acq_sync_sim
file mkdir $work
set run [file join $work [clock format [clock seconds] -format %Y%m%d_%H%M%S]_[pid]]
file mkdir $run/src
foreach name {adxl355_acq.v capture_timebase.v tacho_timestamp.v capture_fifo.v sync_capture_axi.v tb_acq_sync.v} {
 file copy -force [file join $here $name] [file join $run src $name]
}
create_project hx_acq_sync_sim [file join $run proj] -part xc7z020clg400-1
set_param general.maxThreads 2
foreach src [glob [file join $run src *.v]] {add_files -fileset sim_1 -norecurse $src}
set_property top tb_acq_sync [get_filesets sim_1]
set_property xsim.simulate.runtime {0ns} [get_filesets sim_1]
update_compile_order -fileset sim_1
set marker [open [file join $work latest_run.txt] w];puts $marker $run;close $marker
if {[catch {launch_simulation;run -all} detail]} {puts "SIM_EXIT: FAIL $detail";exit 1}
set simdir [file join $run proj hx_acq_sync_sim.sim sim_1 behav xsim]
set handle [open [file join $simdir simulate.log] r];set result [read $handle];close $handle
if {![regexp {PASS: complete acq_sync integration} $result] || [regexp {Fatal:|FAIL:|TIMEOUT:} $result]} {puts "SIM_EXIT: FAIL see $simdir";exit 1}
foreach name {samples.csv pulses.csv} {file copy -force [file join $simdir $name] [file join $run $name]}
puts "SIM_EXIT: PASS complete integration $run"
exit 0
