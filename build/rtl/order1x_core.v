// One owned 64-point/turn window, retained until the final polar result is ACKed.
// Cartesian outputs and magnitude are raw-count Q8; phase is signed BAM32.
`default_nettype none
module order1x_core(
    input wire clk,input wire rst_n,input wire clear_i,
    input wire start_i,input wire [3:0] avg_log2_i,
    input wire sample_valid_i,input wire signed [19:0] sample_i,
    input wire result_ready_i,
    output wire start_ready_o,output wire sample_ready_o,output wire result_valid_o,
    output reg signed [31:0] real_o,output reg signed [31:0] imag_o,
    output wire [31:0] magnitude_o,output wire signed [31:0] phase_o,
    output wire phase_defined_o,output wire config_error_o,output wire busy_o
);
    reg active;
    wire acc_start_ready,acc_sample_ready,acc_result_valid,acc_busy;
    wire signed [31:0] acc_real,acc_imag;
    wire cordic_ready,cordic_valid;
    wire start_accept=start_i && start_ready_o;
    wire coefficient_transfer=active && acc_result_valid && cordic_ready;
    assign start_ready_o=!active && acc_start_ready && cordic_ready && !clear_i;
    assign sample_ready_o=active && acc_sample_ready;
    assign result_valid_o=active && cordic_valid;
    assign busy_o=active;

    order1x_accumulator accumulator(
        .clk(clk),.rst_n(rst_n),.clear_i(clear_i),.start_i(start_accept),
        .avg_log2_i(avg_log2_i),.sample_valid_i(sample_valid_i && active),.sample_i(sample_i),
        .result_ready_i(active && cordic_ready),.start_ready_o(acc_start_ready),
        .sample_ready_o(acc_sample_ready),.result_valid_o(acc_result_valid),
        .real_o(acc_real),.imag_o(acc_imag),.config_error_o(config_error_o),.busy_o(acc_busy));
    cordic_vectoring #(.ITERATIONS(24)) polar(
        .clk(clk),.rst_n(rst_n),.clear_i(clear_i),
        .in_valid_i(active && acc_result_valid),.in_real_i(acc_real),.in_imag_i(acc_imag),
        .out_ready_i(result_ready_i && active),.in_ready_o(cordic_ready),
        .out_valid_o(cordic_valid),.magnitude_o(magnitude_o),.phase_o(phase_o),
        .phase_defined_o(phase_defined_o));
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin active<=0;real_o<=0;imag_o<=0;end
        else if(clear_i) begin active<=0;real_o<=0;imag_o<=0;end
        else begin
            // Invalid AVG is reported by the accumulator but never owns a window.
            if(start_accept && avg_log2_i<=8) active<=1;
            if(coefficient_transfer) begin real_o<=acc_real;imag_o<=acc_imag;end
            if(result_valid_o && result_ready_i) active<=0;
        end
    end
endmodule
`default_nettype wire
