`timescale 1ns/1ps
// End-to-end production-clock fixture: real SPI -> complete sample sideband ->
// BRAM FIFO -> AXI reads -> CSV -> Python synchronous 1x model.
module tb_acq_sync;
reg clk=0, rst_n=0;
always #5 clk=~clk;
reg drdy=0, optical_level=1, miso=0;
wire cs,sclk,mosi;
wire [63:0] ticks;
wire clear_capture,enable_capture;
wire sample_valid, source_idle;
wire [31:0] frame, sx,sy,sz,missed;
wire [63:0] sample_ticks, pulse_ticks,pulse_count;
wire pulse_valid;
integer errors=0, checks=0, device_frame=0;
reg [7:0] dev[0:47];
reg [7:0] cmd;
integer bitn=0,command_bits=0,addrn,bitpos;
reg [63:0] accepted_ticks[0:499];
real angle;
integer axis_x;
reg signed [23:0] rawx,rawy,rawz;
reg [31:0] v, count_samples,count_pulses, f,lo,hi,x,y,z;
reg [63:0] timestamp, previous_pulse;
integer sample_file,pulse_file,i,timeout_cycles;
task check(input integer condition,input [8*120-1:0] message);
begin checks=checks+1; if(condition!==1)begin errors=errors+1;$display("FAIL: %0s t=%0t",message,$time);end end
endtask
reg [15:0] a_awaddr=0,a_araddr=0;
reg [31:0] a_wdata=0;
reg [3:0] a_wstrb=15;
reg a_awvalid=0,a_wvalid=0,a_bready=0,a_arvalid=0,a_rready=0;
wire a_awready,a_wready,a_bvalid,a_arready,a_rvalid;
wire [31:0] a_rdata;
reg [15:0] s_awaddr=0,s_araddr=0;
reg [31:0] s_wdata=0;
reg [3:0] s_wstrb=15;
reg s_awvalid=0,s_wvalid=0,s_bready=0,s_arvalid=0,s_rready=0;
wire s_awready,s_wready,s_bvalid,s_arready,s_rvalid;
wire [31:0] s_rdata;
capture_timebase timebase(.clk(clk),.rst_n(rst_n),.sample_strobe(1'b0),.time_ticks(ticks),.sample_ticks(),.sample_valid());
tacho_timestamp #(.RISING_EDGE(0)) tacho_core(.clk(clk),.rst_n(rst_n),.enable(enable_capture),.clear(clear_capture),.tacho_async(optical_level),.time_ticks(ticks),.event_valid(pulse_valid),.event_ticks(pulse_ticks),.pulse_count(pulse_count),.period_valid(),.period_ticks());
adxl355_acq #(.CLK_HALF(50),.RAM_DEPTH(12),.DISCARD_DFLT(2)) acq(.clk(clk),.rst_n(rst_n),
.S_AXI_AWADDR(a_awaddr),
.S_AXI_AWVALID(a_awvalid),
.S_AXI_AWREADY(a_awready),
.S_AXI_WDATA(a_wdata),
.S_AXI_WSTRB(a_wstrb),
.S_AXI_WVALID(a_wvalid),
.S_AXI_WREADY(a_wready),
.S_AXI_BVALID(a_bvalid),
.S_AXI_BREADY(a_bready),
.S_AXI_ARADDR(a_araddr),
.S_AXI_ARVALID(a_arvalid),
.S_AXI_ARREADY(a_arready),
.S_AXI_RDATA(a_rdata),
.S_AXI_RVALID(a_rvalid),
.S_AXI_RREADY(a_rready),
.S_AXI_AWPROT(3'b0),.S_AXI_ARPROT(3'b0),.S_AXI_BRESP(),.S_AXI_RRESP(),
.spi_cs_o(cs),.spi_sclk_o(sclk),.spi_mosi_o(mosi),.spi_miso_i(miso),.drdy_i(drdy),
.capture_ticks_i(ticks),.capture_clear_i(clear_capture),.sample_valid_o(sample_valid),.sample_frame_o(frame),.sample_ticks_o(sample_ticks),.sample_x_o(sx),.sample_y_o(sy),.sample_z_o(sz),.capture_idle_o(source_idle),.capture_missed_o(missed));
sync_capture_axi #(.SAMPLE_DEPTH(512),.PULSE_DEPTH(8)) records(.clk(clk),.rst_n(rst_n),
.S_AXI_AWADDR(s_awaddr),
.S_AXI_AWVALID(s_awvalid),
.S_AXI_AWREADY(s_awready),
.S_AXI_WDATA(s_wdata),
.S_AXI_WSTRB(s_wstrb),
.S_AXI_WVALID(s_wvalid),
.S_AXI_WREADY(s_wready),
.S_AXI_BVALID(s_bvalid),
.S_AXI_BREADY(s_bready),
.S_AXI_ARADDR(s_araddr),
.S_AXI_ARVALID(s_arvalid),
.S_AXI_ARREADY(s_arready),
.S_AXI_RDATA(s_rdata),
.S_AXI_RVALID(s_rvalid),
.S_AXI_RREADY(s_rready),
.S_AXI_AWPROT(3'b0),.S_AXI_ARPROT(3'b0),.S_AXI_BRESP(),.S_AXI_RRESP(),
.sample_valid_i(sample_valid),.sample_frame_i(frame),.sample_ticks_i(sample_ticks),.sample_x_i(sx),.sample_y_i(sy),.sample_z_i(sz),
.pulse_valid_i(pulse_valid),.pulse_ticks_i(pulse_ticks),.pulse_count_i(pulse_count),.source_idle_i(source_idle),.source_missed_i(missed),
.capture_enable_o(enable_capture),.capture_clear_o(clear_capture));
task a_write(input [15:0] address,input [31:0] data);
integer spins;reg aw_done,w_done;
begin
 @(negedge clk);a_awaddr=address;a_wdata=data;a_awvalid=1;a_wvalid=1;a_bready=1;aw_done=0;w_done=0;spins=0;
 while(!(aw_done&&w_done)&&spins<100)begin
  @(posedge clk);if(a_awvalid&&a_awready)aw_done=1;if(a_wvalid&&a_wready)w_done=1;
  @(negedge clk);if(aw_done)a_awvalid=0;if(w_done)a_wvalid=0;spins=spins+1;
 end
 check(aw_done&&w_done,"write address/data accepted");
 spins=0;while(!a_bvalid&&spins<100)begin @(negedge clk);spins=spins+1;end
 check(a_bvalid,"write response");@(posedge clk);@(negedge clk);a_bready=0;
end endtask
task a_read(input [15:0] address,output [31:0] data);
integer spins;
begin
 @(negedge clk);a_araddr=address;a_arvalid=1;a_rready=1;
 spins=0;@(posedge clk);while(!a_arready&&spins<100)begin @(posedge clk);spins=spins+1;end
 check(a_arready,"read address accepted");@(negedge clk);a_arvalid=0;
 spins=0;while(!a_rvalid&&spins<100)begin @(negedge clk);spins=spins+1;end
 check(a_rvalid,"read response");data=a_rdata;@(posedge clk);@(negedge clk);a_rready=0;
end endtask
task s_write(input [15:0] address,input [31:0] data);
integer spins;reg aw_done,w_done;
begin
 @(negedge clk);s_awaddr=address;s_wdata=data;s_awvalid=1;s_wvalid=1;s_bready=1;aw_done=0;w_done=0;spins=0;
 while(!(aw_done&&w_done)&&spins<100)begin
  @(posedge clk);if(s_awvalid&&s_awready)aw_done=1;if(s_wvalid&&s_wready)w_done=1;
  @(negedge clk);if(aw_done)s_awvalid=0;if(w_done)s_wvalid=0;spins=spins+1;
 end
 check(aw_done&&w_done,"write address/data accepted");
 spins=0;while(!s_bvalid&&spins<100)begin @(negedge clk);spins=spins+1;end
 check(s_bvalid,"write response");@(posedge clk);@(negedge clk);s_bready=0;
end endtask
task s_read(input [15:0] address,output [31:0] data);
integer spins;
begin
 @(negedge clk);s_araddr=address;s_arvalid=1;s_rready=1;
 spins=0;@(posedge clk);while(!s_arready&&spins<100)begin @(posedge clk);spins=spins+1;end
 check(s_arready,"read address accepted");@(negedge clk);s_arvalid=0;
 spins=0;while(!s_rvalid&&spins<100)begin @(negedge clk);spins=spins+1;end
 check(s_rvalid,"read response");data=s_rdata;@(posedge clk);@(negedge clk);s_rready=0;
end endtask
// Sensor data are independently calculated from physical DRDY time.
always @(posedge drdy) begin
 device_frame=device_frame+1;
 angle=6.283185307179586*($realtime-5000000.0)/40000000.0+0.523598775598299;
 axis_x=$rtoi(5120.0*$cos(angle));
 rawx=axis_x*16;rawy=(-20000+device_frame)*16;rawz=256000*16;
 {dev[8],dev[9],dev[10]}=rawx;{dev[11],dev[12],dev[13]}=rawy;{dev[14],dev[15],dev[16]}=rawz;
end
always @(negedge cs) begin
 bitn=0;command_bits=0;cmd=0;miso=0;
 if(rst_n) accepted_ticks[device_frame]=ticks-1; // CS falls at the accepted edge NBA.
end
always @(posedge sclk) if(!cs&&command_bits<8)begin cmd={cmd[6:0],mosi};command_bits=command_bits+1;end
always @(negedge sclk) if(!cs)begin
 bitn=bitn+1;
 if(bitn>=8&&command_bits==8)begin
  addrn=cmd[7:1]+bitn/8-1;bitpos=bitn%8;
  if(addrn<48&&cmd[0])miso=dev[addrn][7-bitpos];else miso=0;
 end
end
initial begin for(i=0;i<48;i=i+1)dev[i]=0;dev[0]=8'hAD;dev[4]=7;end
initial begin #1000;forever begin drdy=1;#125000;drdy=0;#125000;end end
// 25 Hz single optical mark: falling edges 5,45,85 ms.
initial begin #5000000;forever begin optical_level=0;#1000000;optical_level=1;#39000000;end end
initial begin
 #200000000;$fatal(1,"TIMEOUT: complete sync integration");
end
initial begin
 repeat(20)@(negedge clk);rst_n=1;
 // Prove round-trip preserves integer timestamps far above 2**53.
 timebase.time_ticks=64'h2000000000000000;
 s_read(16'h0000,v);check(v==32'h48585331,"sync ABI magic");
 s_write(16'h0008,2);repeat(5)@(negedge clk);
 s_read(16'h001C,v);check(v==1,"epoch increments on clear");
 s_write(16'h0008,1);a_write(16'h4000,3);
 #100000000;
 // Stop accepting samples while leaving event/FIFO path alive for tail records.
 a_write(16'h4000,0);timeout_cycles=0;
 s_read(16'h0078,v);
 while(!v[0]&&timeout_cycles<10000)begin s_read(16'h0078,v);timeout_cycles=timeout_cycles+1;end
 check(v[0],"source drained before capture disable");
 s_write(16'h0008,0);repeat(5)@(negedge clk);
 s_read(16'h0028,v);check(v==0,"no sample FIFO loss");
 s_read(16'h002C,v);check(v==0,"no pulse FIFO loss");
 s_read(16'h007C,v);check(v==0,"no accepted-source misses");
 s_read(16'h0020,count_samples);check(count_samples>390&&count_samples<405,"production 4 kHz sample count");
 s_read(16'h0024,count_pulses);check(count_pulses==3,"three genuine optical marks captured");
 sample_file=$fopen("samples.csv","w");pulse_file=$fopen("pulses.csv","w");
 check(sample_file!=0&&pulse_file!=0,"CSV files opened");
 $fdisplay(sample_file,"frame_id,ticks,x_raw,y_raw,z_raw");
 $fdisplay(pulse_file,"pulse_count,ticks");
 for(i=0;i<count_samples;i=i+1)begin
  s_read(16'h0040,f);s_read(16'h0044,lo);s_read(16'h0048,hi);
  s_read(16'h004C,x);s_read(16'h0050,y);s_read(16'h0054,z);
  timestamp={hi,lo};check(f==i,"sequential frame id through FIFO and AXI");
  check(z==256000,"complete Z word associated with record");
  check($signed(y)>-20000&&$signed(y)<-19500,"independent sensor frame marker");
  check(timestamp==accepted_ticks[$signed(y)+20000],"timestamp is accepted DRDY not SPI completion");
  $fdisplay(sample_file,"%0d,%0d,%0d,%0d,%0d",f,timestamp,$signed(x),$signed(y),$signed(z));
  s_write(16'h0030,1);
 end
 previous_pulse=0;
 for(i=0;i<count_pulses;i=i+1)begin
  s_read(16'h0060,lo);s_read(16'h0064,hi);timestamp={hi,lo};
  s_read(16'h0068,v);check(v==i+1,"pulse count starts at one");
  s_read(16'h006C,v);check(v==0,"pulse count high word");
  if(i>0)check(timestamp-previous_pulse==4000000,"40 ms optical interval on shared clock");
  previous_pulse=timestamp;
  $fdisplay(pulse_file,"%0d,%0d",i+1,timestamp);s_write(16'h0034,1);
 end
 $fclose(sample_file);$fclose(pulse_file);
 s_read(16'h0020,v);check(v==0,"sample tail fully drained");
 s_read(16'h0024,v);check(v==0,"pulse tail fully drained");
 $display("SYNC_DATASET: samples=%0d pulses=%0d clock_hz=100000000 epoch=1",count_samples,count_pulses);
 if(errors==0)$display("PASS: complete acq_sync integration checks=%0d",checks);
 else $fatal(1,"FAIL: complete acq_sync errors=%0d",errors);
 $finish;
end
endmodule
