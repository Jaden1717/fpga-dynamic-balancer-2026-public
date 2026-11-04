`timescale 1ns/1ps
module tb_adxl355_axi;
reg clk=0, rst_n=0;
always #5 clk=~clk;
reg [15:0] awaddr=0, araddr=0;
reg [31:0] wdata=0;
reg [3:0] wstrb=0;
reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0,drdy=0;
wire awready,wready,bvalid,arready,rvalid;
wire [31:0] rdata;
integer errors=0;
reg [31:0] value, held;
adxl355_acq #(.RAM_DEPTH(12)) dut(
.clk(clk),.rst_n(rst_n),
.S_AXI_AWADDR(awaddr),.S_AXI_AWPROT(3'b0),.S_AXI_AWVALID(awvalid),.S_AXI_AWREADY(awready),
.S_AXI_WDATA(wdata),.S_AXI_WSTRB(wstrb),.S_AXI_WVALID(wvalid),.S_AXI_WREADY(wready),
.S_AXI_BRESP(),.S_AXI_BVALID(bvalid),.S_AXI_BREADY(bready),
.S_AXI_ARADDR(araddr),.S_AXI_ARPROT(3'b0),.S_AXI_ARVALID(arvalid),.S_AXI_ARREADY(arready),
.S_AXI_RDATA(rdata),.S_AXI_RRESP(),.S_AXI_RVALID(rvalid),.S_AXI_RREADY(rready),
.spi_cs_o(),.spi_sclk_o(),.spi_mosi_o(),.spi_miso_i(1'b0),.drdy_i(drdy));
task check(input cond,input [511:0] msg);
begin if(cond !== 1'b1) begin $display("FAIL: %0s",msg); errors=errors+1; end end
endtask
task send_aw(input [15:0] addr,input integer delay_cycles);
begin
repeat(delay_cycles) @(negedge clk);
@(negedge clk); awaddr=addr; awvalid=1;
@(posedge clk); while(!awready) @(posedge clk);
@(negedge clk); awvalid=0; awaddr=16'hffff;
end endtask
task send_w(input [31:0] data,input [3:0] strb,input integer delay_cycles);
begin
repeat(delay_cycles) @(negedge clk);
@(negedge clk); wdata=data; wstrb=strb; wvalid=1;
@(posedge clk); while(!wready) @(posedge clk);
// A master may change data and strobes immediately after acceptance.
@(negedge clk); wvalid=0; wdata=32'hdeadbeef; wstrb=4'hf;
end endtask
task pair(input [15:0] addr,input [31:0] data,input [3:0] strb,input integer ad,input integer wd);
begin fork send_aw(addr,ad); send_w(data,strb,wd); join end endtask
task consume_b;
begin
wait(bvalid); repeat(3) begin @(negedge clk); check(bvalid,"BVALID held until BREADY"); end
bready=1; @(posedge clk); @(negedge clk); bready=0;
end endtask
task write_reg(input [15:0] addr,input [31:0] data,input [3:0] strb,input integer ad,input integer wd);
begin pair(addr,data,strb,ad,wd); consume_b; end endtask
task start_read(input [15:0] addr);
begin
@(negedge clk); araddr=addr; arvalid=1;
@(posedge clk); while(!arready) @(posedge clk);
@(negedge clk); arvalid=0; araddr=16'hffff;
wait(rvalid); @(negedge clk);
end endtask
task finish_read;
begin rready=1; @(posedge clk); @(negedge clk); rready=0; end endtask
task read_reg(input [15:0] addr,output [31:0] result);
begin start_read(addr); result=rdata; finish_read; end endtask
initial begin
#100; @(negedge clk); rst_n=1;
write_reg(16'h4018,32'h11223344,4'hf,0,7);
read_reg(16'h4018,value); check(value===32'h11223344,"AW before W retains accepted payload");
write_reg(16'h4018,32'h55667788,4'hf,7,0);
read_reg(16'h4018,value); check(value===32'h55667788,"W before AW retains accepted payload");
write_reg(16'h4018,32'h11223344,4'hf,0,0);
write_reg(16'h4018,32'h0000aa00,4'b0010,0,0);
read_reg(16'h4018,value); check(value===32'h1122aa44,"byte writes preserve unstroked bytes");
write_reg(16'h4018,32'hffffffff,4'h0,0,0);
read_reg(16'h4018,value); check(value===32'h1122aa44,"zero strobe write has no side effect");
// The live DRDY status changes while its AXI response is stalled.
start_read(16'h4004); held=rdata; drdy=1;
repeat(8) begin @(negedge clk); check(rvalid && rdata===held,"register RDATA stable under backpressure"); end
finish_read; drdy=0; repeat(5) @(negedge clk);
// Emulate the acquisition writer overwriting a ring word after a read response.
dut.ram[0]=32'h12345678; dut.ram[1]=32'h87654321;
read_reg(16'h0004,value); check(value===32'h87654321,"BRAM read uses the requested address");
start_read(16'h0000); held=rdata; check(held===32'h12345678,"BRAM read pipeline returns correct word");
dut.ram[0]=32'habcdabcd;
repeat(8) begin @(negedge clk); check(rvalid && rdata===held,"BRAM RDATA stable under backpressure"); end
finish_read;
// A second request must receive its own response after the stalled first one.
pair(16'h4018,32'h01020304,4'hf,0,0); wait(bvalid);
fork
pair(16'h4018,32'h05060708,4'hf,0,0);
begin consume_b; wait(bvalid); consume_b; end
join
read_reg(16'h4018,value); check(value===32'h05060708,"second write survives stalled first response");
if(errors) $fatal(1,"AXI_ERRORS: %0d",errors);
$display("PASS: all adxl355_axi checks OK"); $finish;
end
initial begin #100000; $fatal(1,"AXI_TIMEOUT"); end
endmodule
