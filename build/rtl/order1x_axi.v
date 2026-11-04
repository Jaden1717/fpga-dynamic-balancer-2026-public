// AXI-Lite 1x amplitude/phase accelerator, register ABI v1.
// INPUT accepts only full-word, correctly sign-extended signed 20-bit samples.
// Numerical convention: 2/N * SUM(x[k] * exp(-j*2*pi*k/64)), in raw Q8.
`default_nettype none
module order1x_axi(
    input wire clk,input wire rst_n,
    input wire [15:0] S_AXI_AWADDR,input wire [2:0] S_AXI_AWPROT,
    input wire S_AXI_AWVALID,output wire S_AXI_AWREADY,
    input wire [31:0] S_AXI_WDATA,input wire [3:0] S_AXI_WSTRB,
    input wire S_AXI_WVALID,output wire S_AXI_WREADY,
    output wire [1:0] S_AXI_BRESP,output wire S_AXI_BVALID,input wire S_AXI_BREADY,
    input wire [15:0] S_AXI_ARADDR,input wire [2:0] S_AXI_ARPROT,
    input wire S_AXI_ARVALID,output wire S_AXI_ARREADY,
    output wire [31:0] S_AXI_RDATA,output wire [1:0] S_AXI_RRESP,
    output wire S_AXI_RVALID,input wire S_AXI_RREADY
);
    reg aw_held,w_held,b_valid,r_valid;
    reg [15:0] aw_addr;
    reg [31:0] w_data,r_data;
    reg [3:0] w_strb;
    reg [7:0] avg_log2;
    reg [31:0] accepted_count,expected_count,cycles,error_code;
    wire write_commit=aw_held && w_held && !b_valid;
    wire low_write=write_commit && w_strb[0];
    wire clear=low_write && aw_addr==16'h0008 && w_data[1];
    wire start_command=low_write && aw_addr==16'h0008 && w_data[0] && !clear;
    wire input_command=write_commit && aw_addr==16'h0014;
    wire ack=low_write && aw_addr==16'h0030 && w_data[0];
    wire start_ready,sample_ready,result_valid,phase_defined,core_error,busy;
    wire signed [31:0] result_real,result_imag,result_phase;
    wire [31:0] result_magnitude;
    wire valid_input_range=w_data[31:20]=={12{w_data[19]}};
    wire start_accept=start_command && start_ready && avg_log2<=8;
    wire input_accept=input_command && w_strb==4'hf && valid_input_range && sample_ready;

    order1x_core core(
        .clk(clk),.rst_n(rst_n),.clear_i(clear),.start_i(start_accept),
        .avg_log2_i(avg_log2[3:0]),.sample_valid_i(input_accept),.sample_i(w_data[19:0]),
        .result_ready_i(ack),.start_ready_o(start_ready),.sample_ready_o(sample_ready),
        .result_valid_o(result_valid),.real_o(result_real),.imag_o(result_imag),
        .magnitude_o(result_magnitude),.phase_o(result_phase),.phase_defined_o(phase_defined),
        .config_error_o(core_error),.busy_o(busy));

    // One outstanding write transaction; independent channels may arrive in
    // either order. Commit is combinational from the two captured channels so
    // ready/valid tests and side effects use the same edge, with no stale pulse.
    assign S_AXI_AWREADY=!aw_held && !b_valid;
    assign S_AXI_WREADY=!w_held && !b_valid;
    assign S_AXI_BRESP=2'b00;
    assign S_AXI_BVALID=b_valid;
    assign S_AXI_ARREADY=!r_valid;
    assign S_AXI_RDATA=r_data;
    assign S_AXI_RRESP=2'b00;
    assign S_AXI_RVALID=r_valid;
    function [31:0] read_register;
        input [15:0] address;
        begin
            case(address)
                16'h0000:read_register=32'h48583131;
                16'h0004:read_register=32'h00010000;
                16'h0008:read_register=0; // write-one command bits are not state
                16'h000c:read_register={27'd0,(|error_code),phase_defined,result_valid,sample_ready,busy};
                16'h0010:read_register={24'd0,avg_log2};
                16'h0014:read_register=0;
                16'h0018:read_register=accepted_count;
                16'h001c:read_register=expected_count;
                16'h0020:read_register=result_real;
                16'h0024:read_register=result_imag;
                16'h0028:read_register=result_magnitude;
                16'h002c:read_register=result_phase;
                16'h0030:read_register=0;
                16'h0034:read_register=cycles;
                16'h0038:read_register=error_code;
                default:read_register=0;
            endcase
        end
    endfunction
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            aw_held<=0;w_held<=0;b_valid<=0;r_valid<=0;
            aw_addr<=0;w_data<=0;w_strb<=0;r_data<=0;
            avg_log2<=0;accepted_count<=0;expected_count<=0;cycles<=0;error_code<=0;
        end else begin
            if(S_AXI_AWVALID && S_AXI_AWREADY) begin aw_held<=1;aw_addr<=S_AXI_AWADDR;end
            if(S_AXI_WVALID && S_AXI_WREADY) begin w_held<=1;w_data<=S_AXI_WDATA;w_strb<=S_AXI_WSTRB;end
            if(b_valid && S_AXI_BREADY) b_valid<=0;
            if(write_commit) begin
                aw_held<=0;w_held<=0;b_valid<=1;
                if(w_strb[0] && aw_addr==16'h0010) avg_log2<=w_data[7:0];
            end
            // RDATA is an atomic snapshot at AR handshake, even if CLEAR
            // commits on the same edge or while the master stalls RREADY.
            if(r_valid && S_AXI_RREADY) r_valid<=0;
            if(S_AXI_ARVALID && S_AXI_ARREADY) begin r_data<=read_register(S_AXI_ARADDR);r_valid<=1;end

            if(clear) begin accepted_count<=0;expected_count<=0;cycles<=0;error_code<=0;end
            else begin
                if(busy && !result_valid && cycles!=32'hffffffff) cycles<=cycles+1'b1;
                if(start_accept) begin
                    accepted_count<=0;expected_count<=32'd64<<avg_log2;cycles<=0;
                end
                if(input_accept) accepted_count<=accepted_count+1'b1;
                if(start_command) begin
                    if(!start_ready) error_code[0]<=1;
                    else if(avg_log2>8) error_code[3]<=1;
                end
                if(input_command) begin
                    if(w_strb!=4'hf) error_code[4]<=1;
                    else if(!valid_input_range) error_code[2]<=1;
                    else if(!sample_ready) error_code[1]<=1;
                end
                if(core_error) error_code[3]<=1;
            end
        end
    end
endmodule
`default_nettype wire
