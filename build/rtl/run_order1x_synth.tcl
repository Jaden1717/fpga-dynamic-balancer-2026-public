# Reproducible post-synthesis OOC resource/timing check, xc7z020clg400-1.
# No placement/routing or bitstream generation is performed here.
# Run from an ASCII cwd, e.g. C:/AMD:
# vivado.bat -mode batch -nojournal -source <repo>/build/rtl/run_order1x_synth.tcl
# Optional -tclargs <work-base> <source-dir> <all|order1x_core|order1x_axi>
set script_dir [file normalize [file dirname [info script]]]
set work_base C:/AMD/hx_order1x_synth
set source_dir $script_dir
set selector all
if {[llength $argv] > 0} {set work_base [file normalize [lindex $argv 0]]}
if {[llength $argv] > 1} {set source_dir [file normalize [lindex $argv 1]]}
if {[llength $argv] > 2} {set selector [lindex $argv 2]}
if {$selector eq "all"} {set tops {order1x_core order1x_axi}} elseif {$selector in {order1x_core order1x_axi}} {set tops [list $selector]} else {error "Invalid top selector $selector"}
set_param general.maxThreads 2
set sources {order1x_accumulator.v cordic_vectoring.v order1x_core.v order1x_axi.v}
proc source_sha256 {path} {
    set command [format {$s=[System.IO.File]::OpenRead('%s');try{$h=[System.Security.Cryptography.SHA256]::Create();[System.BitConverter]::ToString($h.ComputeHash($s)).Replace('-','').ToLowerInvariant()}finally{$s.Dispose()}} $path]
    set digest [string trim [exec powershell.exe -NoProfile -NonInteractive -Command $command]]
    if {![regexp {^[a-f0-9]{64}$} $digest]} {error "Bad SHA256 result for $path"}
    return $digest
}
file mkdir $work_base
set run_root [file join $work_base [format "synth_%s_%s" [clock format [clock seconds] -format %Y%m%d_%H%M%S] [pid]]]
set snapshot [file join $run_root src]
file mkdir $snapshot
set hashes [dict create]
set manifest [open [file join $run_root SOURCE_SHA256SUMS] w]
fconfigure $manifest -translation lf
foreach name $sources {
    set original [file join $source_dir $name]
    if {![file exists $original]} {error "Missing production source: $original"}
    set target [file join $snapshot $name]
    file copy -force $original $target
    set digest [source_sha256 $target]
    dict set hashes $name $digest
    puts $manifest "$digest  $name"
}
close $manifest
set latest [open [file join $work_base latest_run.txt] w]
puts $latest $run_root
close $latest
puts "ORDER1X_SYNTH_RUN_ROOT: $run_root"
puts "ORDER1X_SYNTH_SOURCE_DIR: $source_dir"
set clock_xdc [file join $run_root clock_100mhz.xdc]
set xh [open $clock_xdc w]
puts $xh {create_clock -name core_clk -period 10.000 [get_ports clk]}
puts $xh {set_clock_uncertainty 0.100 [get_clocks core_clk]}
close $xh
set all_pass 1
foreach top $tops {
    set out_dir [file join $run_root $top]
    file mkdir $out_dir
    create_project -in_memory synth_$top -part xc7z020clg400-1
    foreach name $sources {read_verilog [file join $snapshot $name]}
    read_xdc $clock_xdc
    synth_design -top $top -part xc7z020clg400-1 -mode out_of_context -flatten_hierarchy rebuilt
    if {[llength [get_ports -quiet clk]] != 1} {error "Expected one clk port"}
    if {[llength [get_clocks core_clk]] != 1} {error "Synthesis clock constraint was not loaded"}
    # Boundary delays are zero-delay OOC assumptions, not board timing.
    # Async reset paths are excluded from setup analysis.
    set data_inputs [get_ports -filter {DIRECTION == IN && NAME != clk && NAME != rst_n}]
    if {[llength $data_inputs]} {set_input_delay 0.000 -clock core_clk $data_inputs}
    if {[llength [all_outputs]]} {set_output_delay 0.000 -clock core_clk [all_outputs]}
    set_false_path -from [get_ports rst_n]
    write_checkpoint -force [file join $out_dir ${top}_post_synth.dcp]
    report_utilization -hierarchical -file [file join $out_dir utilization.rpt]
    report_timing_summary -delay_type max -max_paths 10 -report_unconstrained -file [file join $out_dir timing_summary.rpt]
    report_timing -delay_type max -max_paths 10 -path_type full_clock_expanded -file [file join $out_dir worst_paths.rpt]
    set paths [get_timing_paths -delay_type max -max_paths 1 -nworst 1]
    if {[llength $paths] != 1} {error "No setup timing path in $top"}
    set worst [lindex $paths 0]
    set slack [get_property SLACK $worst]
    set delay [get_property DATAPATH_DELAY $worst]
    set startpoint [get_property STARTPOINT_PIN $worst]
    set endpoint [get_property ENDPOINT_PIN $worst]
    set lut [llength [get_cells -quiet -hierarchical -filter {REF_NAME =~ LUT*}]]
    set ff [llength [get_cells -quiet -hierarchical -filter {REF_NAME =~ FD*}]]
    set dsp [llength [get_cells -quiet -hierarchical -filter {REF_NAME =~ DSP48*}]]
    set bram [llength [get_cells -quiet -hierarchical -filter {REF_NAME =~ RAMB*}]]
    set pass [expr {$slack >= 0.0}]
    if {!$pass} {set all_pass 0}
    set metrics [open [file join $out_dir metrics.json] w]
    fconfigure $metrics -translation lf
    puts $metrics "\{"
    puts $metrics "  \"top\": \"$top\","
    puts $metrics "  \"part\": \"xc7z020clg400-1\","
    puts $metrics "  \"stage\": \"post_synthesis_ooc_estimate_no_place_or_route\","
    puts $metrics "  \"clock_period_ns\": 10.0,"
    puts $metrics "  \"clock_uncertainty_ns\": 0.1,"
    puts $metrics "  \"wns_ns\": $slack,"
    puts $metrics "  \"worst_datapath_ns\": $delay,"
    puts $metrics "  \"lut_primitives\": $lut, \"ff_primitives\": $ff, \"dsp48_primitives\": $dsp, \"bram_primitives\": $bram,"
    puts $metrics "  \"timing_gate_pass\": [expr {$pass ? {true} : {false}}]"
    puts $metrics "\}"
    close $metrics
    puts "ORDER1X_OOC: top=$top LUT=$lut FF=$ff DSP=$dsp BRAM=$bram WNS_NS=$slack DATAPATH_NS=$delay PASS=$pass"
    puts "ORDER1X_WORST_PATH: $startpoint -> $endpoint"
    close_project
}
set stable 1
foreach name $sources {
    set current [source_sha256 [file join $source_dir $name]]
    if {$current ne [dict get $hashes $name]} {set stable 0; puts "SOURCE_CHANGED: $name"}
}
if {!$stable} {puts "SYNTH_EXIT: FAIL (source changed; rerun final snapshot)"; exit 2}
if {!$all_pass} {puts "SYNTH_EXIT: FAIL (100 MHz post-synthesis timing gate; reports preserved at $run_root)"; exit 1}
puts "SYNTH_EXIT: PASS (post-synthesis OOC estimate only; source stable; reports at $run_root)"
exit 0




