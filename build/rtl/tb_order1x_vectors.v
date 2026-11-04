`timescale 1ns/1ps
module tb_order1x_vectors #(parameter integer EXPECTED_CASES=126);
reg clk=0,rst_n=0,clear=0,start=0,svalid=0,ready=0;
always #5 clk=~clk;
reg [3:0] avg;
reg signed [19:0] sample;
wire start_ready,sample_ready,valid,defined,error,busy;
wire signed [31:0] re,im,phase;
wire [31:0] magnitude;
reg signed [31:0] expected_re,expected_im,expected_phase;
reg [31:0] expected_mag,held_re,held_im,held_mag,held_phase;
reg signed [63:0] difference,phase_difference;
integer input_file,output_file,rc,avg_int,word,j,spins,cases=0,checks=0,errors=0;
reg [63:0] cycles=0,start_tick,last_tick;
always @(posedge clk)cycles<=cycles+1;
order1x_core dut(.clk(clk),.rst_n(rst_n),.clear_i(clear),.start_i(start),.avg_log2_i(avg),
.sample_valid_i(svalid),.sample_i(sample),.result_ready_i(ready),.start_ready_o(start_ready),
.sample_ready_o(sample_ready),.result_valid_o(valid),.real_o(re),.imag_o(im),.magnitude_o(magnitude),
.phase_o(phase),.phase_defined_o(defined),.config_error_o(error),.busy_o(busy));
task check(input integer condition,input [8*100-1:0] message);
begin checks=checks+1;if(condition!==1)begin errors=errors+1;$display("FAIL: case=%0d %0s",cases,message);end end endtask
initial begin #200000000;$display("TIMEOUT: order1x vector regression");$finish;end
initial begin
 input_file=$fopen("vectors.txt","r");output_file=$fopen("rtl_results.csv","w");
 if(!input_file||!output_file)$fatal(1,"FAIL: vector files missing");
 $fdisplay(output_file,"case,avg_log2,words,cycles,last_sample_to_result,real_q8,imag_q8,magnitude_q8,phase_code,phase_defined");
 repeat(10)@(negedge clk);rst_n=1;
 rc=$fscanf(input_file,"%d %d %d %d %d\n",avg_int,expected_re,expected_im,expected_mag,expected_phase);
 while(rc==5)begin
  @(negedge clk);spins=0;while(!start_ready&&spins<100)begin @(negedge clk);spins=spins+1;end
  check(start_ready,"start readiness");avg=avg_int;start=1;start_tick=cycles;
  @(negedge clk);start=0;
  for(j=0;j<(64<<avg_int);j=j+1)begin
   rc=$fscanf(input_file,"%d\n",word);check(rc==1,"sample word read");
   if(j%17==0)begin svalid=0;@(negedge clk);end
   spins=0;while(!sample_ready&&spins<100)begin @(negedge clk);spins=spins+1;end
   check(sample_ready,"sample ready within stream");sample=word;svalid=1;
   @(negedge clk);
  end
  svalid=0;last_tick=cycles;spins=0;
  while(!valid&&spins<100)begin @(negedge clk);spins=spins+1;end
  check(valid,"result within100 cycles of last sample");check(!error,"valid configuration");
  check(re===expected_re,"bit-exact normalized real coefficient");
  check(im===expected_im,"bit-exact normalized negative-sine imaginary coefficient");
  difference=$signed({1'b0,magnitude})-$signed({1'b0,expected_mag});if(difference<0)difference=-difference;
  check(difference<=2+expected_mag/500000,"CORDIC magnitude bound");
  phase_difference=$signed(phase)-$signed(expected_phase);
  if(phase_difference>64'sd2147483647)phase_difference=phase_difference-64'sd4294967296;
  if(phase_difference< -64'sd2147483648)phase_difference=phase_difference+64'sd4294967296;
  if(phase_difference<0)phase_difference=-phase_difference;
  check(defined==((expected_re!=0)||(expected_im!=0)),"zero-vector phase flag");
  if(defined)check(phase_difference<=12000,"CORDIC circular phase error <=0.00101deg");
  else check(phase==0&&magnitude==0,"zero canonical output");
  $fdisplay(output_file,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",cases,avg_int,64<<avg_int,cycles-start_tick,cycles-last_tick,re,im,magnitude,phase,defined);
  held_re=re;held_im=im;held_mag=magnitude;held_phase=phase;
  repeat(4)begin @(negedge clk);check(valid&&re==held_re&&im==held_im&&magnitude==held_mag&&phase==held_phase,"held result coherent");end
  ready=1;@(negedge clk);ready=0;cases=cases+1;
  rc=$fscanf(input_file,"%d %d %d %d %d\n",avg_int,expected_re,expected_im,expected_mag,expected_phase);
 end
 $fclose(input_file);$fclose(output_file);
 check(cases==EXPECTED_CASES,"all independently generated cases consumed");
 if(errors==0)$display("PASS: order1x vectors cases=%0d checks=%0d",cases,checks);
 else $display("FAIL: order1x vectors errors=%0d",errors);
 $finish;
end
endmodule
