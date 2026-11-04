set here [file normalize [file dirname [info script]]]
set work C:/AMD/hx_order1x_vectors
set fixture [file normalize [file join $here ../../work/order1x_20261015]]
set expected_cases 126
if {[llength $argv] > 0} {set fixture [file normalize [lindex $argv 0]]}
if {[llength $argv] > 1} {set expected_cases [lindex $argv 1]}
set run [file join $work [clock format [clock seconds] -format %Y%m%d_%H%M%S]_[pid]]
file mkdir $run/src
foreach name {order1x_accumulator.v cordic_vectoring.v order1x_core.v tb_order1x_vectors.v} {file copy -force [file join $here $name] [file join $run src $name]}
create_project hx_order1x_vectors [file join $run proj] -part xc7z020clg400-1
set_param general.maxThreads 2
foreach source [glob [file join $run src *.v]] {add_files -fileset sim_1 -norecurse $source}
set_property top tb_order1x_vectors [get_filesets sim_1]
set_property generic [list EXPECTED_CASES=$expected_cases] [get_filesets sim_1]
set_property xsim.simulate.runtime 0ns [get_filesets sim_1]
update_compile_order -fileset sim_1
set simdir [file join $run proj hx_order1x_vectors.sim sim_1 behav xsim]
file mkdir $simdir
file copy -force [file join $fixture vectors.txt] [file join $simdir vectors.txt]
set marker [open [file join $work latest_run.txt] w];puts $marker $run;close $marker
if {[catch {launch_simulation;run -all} detail]} {puts "SIM_EXIT: FAIL $detail";exit 1}
set handle [open [file join $simdir simulate.log] r];set result [read $handle];close $handle
if {![regexp "PASS: order1x vectors cases=${expected_cases} checks=" $result] || [regexp {FAIL:|Fatal:|TIMEOUT:} $result]} {puts "SIM_EXIT: FAIL see $simdir";exit 1}
file copy -force [file join $simdir rtl_results.csv] [file join $run rtl_results.csv]
puts "SIM_EXIT: PASS $run"
exit 0
