// Record FIFO with a stable, first-word-visible head and synchronous BRAM reads.
// Memory is never reset. The first/sole incoming record uses a bypass register;
// a successful pop reads the following existing record synchronously. Thus no
// prefetch bubble or invalid head is exposed, including consecutive clock pops.
`default_nettype none
module capture_fifo #(
    parameter integer WIDTH=192,
    parameter integer DEPTH=4096
)(
    input wire clk,
    input wire rst_n,
    input wire clear_i,
    input wire push_i,
    input wire [WIDTH-1:0] data_i,
    input wire pop_i,
    output wire [WIDTH-1:0] head_o,
    output wire [31:0] level_o,
    output reg [31:0] drops_o,
    output reg overflow_o,
    output reg [31:0] highwater_o
);
    localparam integer PW=(DEPTH<2)?1:$clog2(DEPTH);
    localparam integer CW=(DEPTH<2)?1:$clog2(DEPTH+1);
    (* ram_style="block" *) reg [WIDTH-1:0] records [0:DEPTH-1];
    reg [PW-1:0] write_ptr,read_ptr;
    reg [CW-1:0] count;
    reg [WIDTH-1:0] bypass_head;
    reg [WIDTH-1:0] memory_head;
    reg use_bypass;
    wire do_pop=pop_i && (count!=0);
    wire do_push=push_i && ((count<DEPTH) || do_pop);
    wire [PW-1:0] next_read=(read_ptr==DEPTH-1)?{PW{1'b0}}:read_ptr+1'b1;
    wire [PW-1:0] next_write=(write_ptr==DEPTH-1)?{PW{1'b0}}:write_ptr+1'b1;
    wire [CW-1:0] count_next=count+{{(CW-1){1'b0}},do_push}-{{(CW-1){1'b0}},do_pop};
    assign level_o=count;
    assign head_o=(count==0)?{WIDTH{1'b0}}:(use_bypass?bypass_head:memory_head);

    // Keep RAM read/write ports outside the asynchronous reset process so Vivado
    // can infer BRAM instead of thousands of resettable registers and LUT muxes.
    always @(posedge clk) begin
        if(rst_n && !clear_i) begin
            if(do_push) records[write_ptr]<=data_i;
            if(do_pop && count>1) memory_head<=records[next_read];
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            write_ptr<=0;read_ptr<=0;count<=0;
            bypass_head<=0;use_bypass<=1;
            drops_o<=0;overflow_o<=0;highwater_o<=0;
        end else if(clear_i) begin
            write_ptr<=0;read_ptr<=0;count<=0;use_bypass<=1;
            drops_o<=0;overflow_o<=0;highwater_o<=0;
        end else begin
            count<=count_next;
            if(do_push) write_ptr<=next_write;
            if(do_pop) read_ptr<=next_read;
            if(do_push && ((count==0) || (count==1 && do_pop))) begin
                bypass_head<=data_i;use_bypass<=1;
            end else if(do_pop && count>1) use_bypass<=0;
            if(push_i && !do_push) begin drops_o<=drops_o+1'b1;overflow_o<=1;end
            if(count_next>highwater_o) highwater_o<=count_next;
        end
    end
endmodule
`default_nettype wire
