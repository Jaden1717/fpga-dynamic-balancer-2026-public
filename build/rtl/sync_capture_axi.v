// AXI-Lite synchronous sample/pulse capture queues, register contract v1.
`default_nettype none
module sync_capture_axi #(
    parameter integer SAMPLE_DEPTH=4096,
    parameter integer PULSE_DEPTH=256,
    parameter integer CLOCK_HZ=100000000
)(
    input wire clk,input wire rst_n,
    input wire [15:0] S_AXI_AWADDR,input wire [2:0] S_AXI_AWPROT,
    input wire S_AXI_AWVALID,output wire S_AXI_AWREADY,
    input wire [31:0] S_AXI_WDATA,input wire [3:0] S_AXI_WSTRB,
    input wire S_AXI_WVALID,output wire S_AXI_WREADY,
    output wire [1:0] S_AXI_BRESP,output wire S_AXI_BVALID,input wire S_AXI_BREADY,
    input wire [15:0] S_AXI_ARADDR,input wire [2:0] S_AXI_ARPROT,
    input wire S_AXI_ARVALID,output wire S_AXI_ARREADY,
    output wire [31:0] S_AXI_RDATA,output wire [1:0] S_AXI_RRESP,
    output wire S_AXI_RVALID,input wire S_AXI_RREADY,
    input wire sample_valid_i,input wire [31:0] sample_frame_i,input wire [63:0] sample_ticks_i,
    input wire [31:0] sample_x_i,input wire [31:0] sample_y_i,input wire [31:0] sample_z_i,
    input wire pulse_valid_i,input wire [63:0] pulse_ticks_i,input wire [63:0] pulse_count_i,
    input wire source_idle_i,input wire [31:0] source_missed_i,
    output reg capture_enable_o,output wire capture_clear_o
);
    reg aw_held,w_held,b_valid,r_valid;
    reg [15:0] aw_addr;
    reg [31:0] w_data,r_data;
    reg [3:0] w_strb;
    reg [31:0] epoch;
    wire write_commit=aw_held && w_held && !b_valid;
    wire write_low=write_commit && w_strb[0];
    assign capture_clear_o=write_low && aw_addr==16'h0008 && w_data[1];
    wire sample_pop=write_low && aw_addr==16'h0030 && w_data[0];
    wire pulse_pop=write_low && aw_addr==16'h0034 && w_data[0];
    wire [191:0] sample_head;
    wire [127:0] pulse_head;
    wire [31:0] sample_level,pulse_level,sample_drops,pulse_drops,sample_high,pulse_high;
    wire sample_overflow,pulse_overflow;
    // Each queue preserves its head until explicit successful POP. Samples use
    // 192 bits: {Z,Y,X,ticks_hi,ticks_lo,frame}; pulse records use 128 bits.
    capture_fifo #(.WIDTH(192),.DEPTH(SAMPLE_DEPTH)) samples (
        .clk(clk),.rst_n(rst_n),.clear_i(capture_clear_o),
        .push_i(capture_enable_o && sample_valid_i),
        .data_i({sample_z_i,sample_y_i,sample_x_i,sample_ticks_i,sample_frame_i}),
        .pop_i(sample_pop),.head_o(sample_head),.level_o(sample_level),
        .drops_o(sample_drops),.overflow_o(sample_overflow),.highwater_o(sample_high));
    capture_fifo #(.WIDTH(128),.DEPTH(PULSE_DEPTH)) pulses (
        .clk(clk),.rst_n(rst_n),.clear_i(capture_clear_o),
        .push_i(capture_enable_o && pulse_valid_i),
        .data_i({pulse_count_i,pulse_ticks_i}),.pop_i(pulse_pop),.head_o(pulse_head),
        .level_o(pulse_level),.drops_o(pulse_drops),.overflow_o(pulse_overflow),.highwater_o(pulse_high));

    assign S_AXI_AWREADY=!aw_held && !b_valid;
    assign S_AXI_WREADY=!w_held && !b_valid;
    assign S_AXI_BRESP=2'b00;
    assign S_AXI_BVALID=b_valid;
    assign S_AXI_ARREADY=!r_valid;
    assign S_AXI_RDATA=r_data;
    assign S_AXI_RRESP=2'b00;
    assign S_AXI_RVALID=r_valid;

    // Read data is captured at AR handshake, never recomputed while R is stalled.
    function [31:0] read_register;
        input [15:0] address;
        begin
            case(address)
                16'h0000:read_register=32'h48585331;
                16'h0004:read_register=32'h00010000;
                16'h0008:read_register={31'd0,capture_enable_o};
                16'h000c:read_register={27'd0,pulse_overflow,sample_overflow,
                                       (pulse_level==0),(sample_level==0),capture_enable_o};
                16'h0010:read_register=CLOCK_HZ;
                16'h0014:read_register=SAMPLE_DEPTH;
                16'h0018:read_register=PULSE_DEPTH;
                16'h001c:read_register=epoch;
                16'h0020:read_register=sample_level;
                16'h0024:read_register=pulse_level;
                16'h0028:read_register=sample_drops;
                16'h002c:read_register=pulse_drops;
                16'h0040:read_register=sample_head[31:0];
                16'h0044:read_register=sample_head[63:32];
                16'h0048:read_register=sample_head[95:64];
                16'h004c:read_register=sample_head[127:96];
                16'h0050:read_register=sample_head[159:128];
                16'h0054:read_register=sample_head[191:160];
                16'h0060:read_register=pulse_head[31:0];
                16'h0064:read_register=pulse_head[63:32];
                16'h0068:read_register=pulse_head[95:64];
                16'h006c:read_register=pulse_head[127:96];
                16'h0070:read_register=sample_high;
                16'h0074:read_register=pulse_high;
                16'h0078:read_register={31'd0,source_idle_i};
                16'h007c:read_register=source_missed_i;
                default:read_register=0;
            endcase
        end
    endfunction
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            aw_held<=0;w_held<=0;b_valid<=0;r_valid<=0;
            aw_addr<=0;w_data<=0;w_strb<=0;r_data<=0;
            capture_enable_o<=0;epoch<=0;
        end else begin
            if(S_AXI_AWVALID && S_AXI_AWREADY) begin aw_held<=1;aw_addr<=S_AXI_AWADDR;end
            if(S_AXI_WVALID && S_AXI_WREADY) begin w_held<=1;w_data<=S_AXI_WDATA;w_strb<=S_AXI_WSTRB;end
            if(b_valid && S_AXI_BREADY) b_valid<=0;
            if(write_commit) begin
                aw_held<=0;w_held<=0;b_valid<=1;
                if(w_strb[0] && aw_addr==16'h0008) capture_enable_o<=w_data[0];
                if(capture_clear_o) epoch<=epoch+1'b1;
            end
            if(r_valid && S_AXI_RREADY) r_valid<=0;
            if(S_AXI_ARVALID && S_AXI_ARREADY) begin r_data<=read_register(S_AXI_ARADDR);r_valid<=1;end
        end
    end
endmodule
`default_nettype wire
