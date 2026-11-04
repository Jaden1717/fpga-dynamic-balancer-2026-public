# Isolated XSim test; red mode substitutes a zero-result placeholder for TDD.
set here [file normalize [file dirname [info script]]]
set work C:/AMD/hx_cordic
set mode green
if {[llength $argv]} {set mode [lindex $argv 0]}
if {$mode ni {red green}} {error "Expected optional red or green mode"}
file mkdir $work
set stamp [clock format [clock seconds] -format %Y%m%d_%H%M%S]
set run_root [file join $work [format "%s_%s_%s" $mode $stamp [pid]]]
set src_root [file join $run_root src]
file mkdir $src_root
file copy -force [file join $here tb_cordic_vectoring.v] $src_root
if {$mode eq "red"} {
    set h [open [file join $src_root cordic_vectoring.v] w]
    puts $h {module cordic_vectoring #(parameter ITERATIONS=24)(input clk,rst_n,clear_i,in_valid_i,input signed [31:0] in_real_i,in_imag_i,input out_ready_i,output in_ready_o,output reg out_valid_o,output [31:0] magnitude_o,output signed [31:0] phase_o,output phase_defined_o);
assign in_ready_o=rst_n && !clear_i && !out_valid_o;
assign magnitude_o=0; assign phase_o=0; assign phase_defined_o=0;
always @(posedge clk or negedge rst_n) if(!rst_n) out_valid_o<=0; else if(clear_i) out_valid_o<=0; else if(in_valid_i && in_ready_o) out_valid_o<=1; else if(out_ready_i) out_valid_o<=0;
endmodule}
    close $h
    puts "TDD_RED: substituted zero-result placeholder; 3,4 vector must fail norm=5"
} else {
    file copy -force [file join $here cordic_vectoring.v] $src_root
}
create_project hx_cordic_sim [file join $run_root proj] -part xc7z020clg400-1
add_files -fileset sim_1 -norecurse [glob [file join $src_root *.v]]
set_property top tb_cordic_vectoring [get_filesets sim_1]
update_compile_order -fileset sim_1
set sim_log [file join $run_root proj hx_cordic_sim.sim sim_1 behav xsim simulate.log]
set h [open [file join $work [format "latest_%s_run.txt" $mode]] w]
puts $h $run_root
close $h
puts "CORDIC_RUN_ROOT: $run_root"
if {[catch {launch_simulation; run -all} detail]} {
    puts "SIM_EXIT: FAIL ($detail; run=$run_root)"
    exit 1
}
if {![file exists $sim_log]} {puts "SIM_EXIT: FAIL (no simulator log)"; exit 1}
set h [open $sim_log r]
set result [read $h]
close $h
if {![regexp {PASS: all cordic_vectoring checks OK} $result] || [regexp {Fatal:|FAIL:|TIMEOUT:} $result]} {
    puts "SIM_EXIT: FAIL (see $sim_log)"
    exit 1
}
puts "SIM_EXIT: PASS (verified simulator log $sim_log)"
exit 0
