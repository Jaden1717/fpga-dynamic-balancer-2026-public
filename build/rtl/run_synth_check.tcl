# Out-of-context synthesis check for the acquisition controller: catches latches,
# unfittable constructs and reports the BRAM it actually infers.
# Run:  vivado.bat -mode batch -nojournal -source run_synth_check.tcl
read_verilog C:/AMD/hx_sim/adxl355_acq.v
synth_design -top adxl355_acq -part xc7z020clg400-1
puts "BRAM_INSTANCES: [llength [get_cells -hier -filter {REF_NAME =~ RAMB*}]]"
report_utilization -hierarchical
puts "SYNTH_EXIT: synthesis finished"
exit
