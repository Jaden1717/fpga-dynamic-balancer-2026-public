# Standalone English-path XSim project; does not touch the acquisition overlay.
# Run from its repository path: vivado.bat -mode batch -nojournal -source build/rtl/run_tacho_sim.tcl
# Optional -tclargs red deliberately omits both DUT modules for a repeatable negative check.
set here [file normalize [file dirname [info script]]]
set work C:/AMD/hx_tacho_sim
set mode green
if {[llength $argv]} {set mode [lindex $argv 0]}
if {$mode ni {red green}} {error "Expected optional red or green mode"}
file mkdir $work
set stamp [clock format [clock seconds] -format %Y%m%d_%H%M%S]
set run_root [file join $work [format "%s_%s_%s" $mode $stamp [pid]]]
set src_root [file join $run_root src]
file mkdir $src_root
foreach name {capture_timebase.v tacho_timestamp.v tb_tacho_timestamp.v} {
    set src [file join $here $name]
    set dst [file join $src_root $name]
    if {$mode eq "red" && $name ne "tb_tacho_timestamp.v"} {
        puts "TDD_RED: deliberately omitted production module $name"
        continue
    }
    if {![file exists $src]} {error "Missing source $src"}
    file copy -force $src $dst
}
create_project hx_tacho_sim [file join $run_root proj] -part xc7z020clg400-1
foreach name {capture_timebase.v tacho_timestamp.v tb_tacho_timestamp.v} {
    set src [file join $src_root $name]
    if {[file exists $src]} {add_files -fileset sim_1 -norecurse $src}
}
set_property top tb_tacho_timestamp [get_filesets sim_1]
update_compile_order -fileset sim_1
set sim_log [file join $run_root proj hx_tacho_sim.sim sim_1 behav xsim simulate.log]
set handle [open [file join $work [format "latest_%s_run.txt" $mode]] w]
puts $handle $run_root
close $handle
puts "TACHO_RUN_ROOT: $run_root"
if {[catch {launch_simulation; run -all} detail]} {
    puts "SIM_EXIT: FAIL ($detail; run=$run_root)"
    exit 1
}
if {![file exists $sim_log]} {
    puts "SIM_EXIT: FAIL (no simulator log $sim_log)"
    exit 1
}
set handle [open $sim_log r]
set result [read $handle]
close $handle
if {![regexp {PASS: all tacho_timestamp checks OK} $result] ||
    [regexp {Fatal:|FAIL:|TIMEOUT:} $result]} {
    puts "SIM_EXIT: FAIL (see $sim_log)"
    exit 1
}
puts "SIM_EXIT: PASS (verified simulator log $sim_log)"
exit 0
