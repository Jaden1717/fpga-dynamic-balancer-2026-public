`timescale 1ns/1ps
`default_nettype none
module tb_sync_capture_axi;
  reg clk=0; always #5 clk=~clk;
  reg rst_n=0;
  reg f_clear=0,f_push=0,f_pop=0;
  reg [31:0] f_data=0;
  wire [31:0] f_head,f_level,f_drops,f_high;
  wire f_overflow;
  capture_fifo #(.WIDTH(32),.DEPTH(3)) fifo(
    .clk(clk),.rst_n(rst_n),.clear_i(f_clear),.push_i(f_push),
    .data_i(f_data),.pop_i(f_pop),.head_o(f_head),.level_o(f_level),
    .drops_o(f_drops),.overflow_o(f_overflow),.highwater_o(f_high));
  integer checks=0, errors=0;
  task expect32(input [31:0] got,input [31:0] want,input [255:0] label_text);
    begin
      checks=checks+1;
      if(got!==want) begin
        $display("FAIL: %0s got=%h expected=%h",label_text,got,want); errors=errors+1;
      end
    end
  endtask
  task f_cycle(input push,input pop,input [31:0] data_word);
    begin
      @(negedge clk);f_push=push;f_pop=pop;f_data=data_word;
      @(posedge clk);#1;
    end
  endtask
  task check_fifo;
    integer i;
    begin
      expect32(f_level,0,"reset empty");expect32(f_head,0,"empty head zero");
      f_cycle(1,0,32'h11111111);
      expect32(f_level,1,"first record queued");expect32(f_head,32'h11111111,"first head");
      f_cycle(1,0,32'h22222222);
      expect32(f_level,2,"two records queued");expect32(f_head,32'h11111111,"push keeps head");
      f_cycle(1,0,32'h33333333);
      expect32(f_level,3,"full capacity");expect32(f_high,3,"highwater full");
      f_cycle(1,0,32'h44444444);
      expect32(f_level,3,"full preserves level");expect32(f_drops,1,"full drop new");
      expect32({31'd0,f_overflow},1,"overflow sticky");expect32(f_head,32'h11111111,"overflow preserves old head");
      f_cycle(1,1,32'h55555555);
      expect32(f_level,3,"full pop push holds level");expect32(f_drops,1,"full pop frees capacity");
      expect32(f_head,32'h22222222,"second record after pop");
      f_cycle(0,1,0);expect32(f_head,32'h33333333,"third record after pop");
      f_cycle(0,1,0);expect32(f_head,32'h55555555,"new record after wrap");
      f_cycle(1,1,32'h66666666);expect32(f_head,32'h66666666,"sole pop push bypass");
      expect32(f_level,1,"sole pop push count");
      f_cycle(0,1,0);expect32(f_level,0,"drain empty");expect32(f_head,0,"drained head zero");
      f_cycle(1,1,32'h77777777);expect32(f_level,1,"empty push pop retains new");
      expect32(f_head,32'h77777777,"empty push pop head");
      @(negedge clk); f_clear=1;f_push=1;f_pop=1;f_data=32'h88888888;
      @(posedge clk);#1;expect32(f_level,0,"clear priority");expect32(f_drops,0,"clear drops");
      expect32(f_high,0,"clear highwater");expect32({31'd0,f_overflow},0,"clear sticky");
      @(negedge clk);f_clear=0;f_push=0;f_pop=0;
      for(i=0;i<30;i=i+1) begin
        f_cycle(1,0,32'h1000+i);f_cycle(0,1,0);
        expect32(f_level,0,"repeat pointer wrap empty");
      end
      @(negedge clk);f_push=0;f_pop=0;
    end
  endtask
  // Independent list-model oracle: shifting a three-element list avoids mirroring
  // the DUT's pointer arithmetic, and exercises sustained clock-by-clock traffic.
  task random_fifo;
    reg [31:0] model [0:2];
    reg [31:0] rng,model_drops,model_high;
    integer n,i,model_count;reg push,pop,clear_now;reg [31:0] datum;
    begin
      @(negedge clk);f_clear=1;f_push=0;f_pop=0;
      @(posedge clk);#1;@(negedge clk);f_clear=0;
      rng=32'h8b1243de;model_count=0;model_drops=0;model_high=0;
      for(n=0;n<5000;n=n+1) begin
        rng={rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]};
        push=rng[0];pop=rng[2];clear_now=(n%139==138);datum=32'ha0000000+n;
        @(negedge clk);f_push=push;f_pop=pop;f_clear=clear_now;f_data=datum;
        if(clear_now) begin model_count=0;model_drops=0;model_high=0;end
        else begin
          if(pop && model_count>0) begin
            model[0]=model[1];model[1]=model[2];model_count=model_count-1;
          end
          if(push) begin
            if(model_count<3) begin model[model_count]=datum;model_count=model_count+1;end
            else model_drops=model_drops+1;
          end
          if(model_count>model_high) model_high=model_count;
        end
        @(posedge clk);#1;
        expect32(f_level,model_count,"random FIFO level");
        expect32(f_head,(model_count==0)?0:model[0],"random FIFO data order");
        expect32(f_drops,model_drops,"random FIFO drop count");
        expect32(f_high,model_high,"random FIFO highwater");
        expect32({31'd0,f_overflow},(model_drops!=0)?1:0,"random FIFO sticky overflow");
      end
      @(negedge clk);f_push=0;f_pop=0;f_clear=0;
    end
  endtask
`ifdef FIFO_ONLY
  initial begin
    repeat(3) @(negedge clk);rst_n=1;repeat(2) @(posedge clk);#1;
    check_fifo();random_fifo();if(errors==0) $display("PASS: all capture_fifo checks OK (%0d assertions)",checks);
    else $display("FAIL: capture_fifo %0d errors",errors);$finish;
  end
`endif

`ifndef FIFO_ONLY
  reg [15:0] awaddr=0,araddr=0;
  reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
  reg [31:0] wdata=0;reg [3:0] wstrb=0;
  wire awready,wready,bvalid,arready,rvalid;
  wire [31:0] rdata;wire [1:0] bresp,rresp;
  reg sample_valid=0,pulse_valid=0;
  reg [31:0] sample_frame=0,sample_x=0,sample_y=0,sample_z=0;
  reg [63:0] sample_ticks=0,pulse_ticks=0,pulse_count=0;
  reg source_idle=1;reg [31:0] source_missed=0;
  wire capture_enable,capture_clear;
  integer clear_pulses=0;
  always @(posedge clk) if(rst_n && capture_clear) clear_pulses=clear_pulses+1;
  sync_capture_axi #(.SAMPLE_DEPTH(3),.PULSE_DEPTH(2),.CLOCK_HZ(100000000)) dut(
    .clk(clk),.rst_n(rst_n),
    .S_AXI_AWADDR(awaddr),.S_AXI_AWPROT(3'd0),.S_AXI_AWVALID(awvalid),.S_AXI_AWREADY(awready),
    .S_AXI_WDATA(wdata),.S_AXI_WSTRB(wstrb),.S_AXI_WVALID(wvalid),.S_AXI_WREADY(wready),
    .S_AXI_BRESP(bresp),.S_AXI_BVALID(bvalid),.S_AXI_BREADY(bready),
    .S_AXI_ARADDR(araddr),.S_AXI_ARPROT(3'd0),.S_AXI_ARVALID(arvalid),.S_AXI_ARREADY(arready),
    .S_AXI_RDATA(rdata),.S_AXI_RRESP(rresp),.S_AXI_RVALID(rvalid),.S_AXI_RREADY(rready),
    .sample_valid_i(sample_valid),.sample_frame_i(sample_frame),.sample_ticks_i(sample_ticks),
    .sample_x_i(sample_x),.sample_y_i(sample_y),.sample_z_i(sample_z),
    .pulse_valid_i(pulse_valid),.pulse_ticks_i(pulse_ticks),.pulse_count_i(pulse_count),
    .source_idle_i(source_idle),.source_missed_i(source_missed),
    .capture_enable_o(capture_enable),.capture_clear_o(capture_clear));
  task send_aw(input [15:0] addr,input integer delay_cycles);
    begin
      repeat(delay_cycles) @(negedge clk);
      @(negedge clk);awaddr=addr;awvalid=1;
      @(posedge clk);while(!awready) @(posedge clk);
      @(negedge clk);awvalid=0;awaddr=16'hffff;
    end
  endtask
  task send_w(input [31:0] data_word,input [3:0] strobes,input integer delay_cycles);
    begin
      repeat(delay_cycles) @(negedge clk);
      @(negedge clk);wdata=data_word;wstrb=strobes;wvalid=1;
      @(posedge clk);while(!wready) @(posedge clk);
      @(negedge clk);wvalid=0;wdata=32'hdeadbeef;wstrb=0;
    end
  endtask
  task axi_write(input [15:0] addr,input [31:0] data_word,input [3:0] strobes,
                 input integer aw_delay,input integer w_delay,input integer b_stall);
    integer i;
    begin
      bready=0;
      fork send_aw(addr,aw_delay);send_w(data_word,strobes,w_delay);join
      while(!bvalid) @(negedge clk);
      expect32({30'd0,bresp},0,"write OKAY response");
      for(i=0;i<b_stall;i=i+1) begin
        @(posedge clk);#1;
        expect32({31'd0,bvalid},1,"BVALID stable during stall");
        expect32({30'd0,bresp},0,"BRESP stable during stall");
        expect32({30'd0,awready,wready},0,"no next write before B accepted");
      end
      @(negedge clk);bready=1;@(posedge clk);#1;@(negedge clk);bready=0;
    end
  endtask
  task axi_read(input [15:0] addr,input integer r_stall,output [31:0] value);
    integer i;reg [31:0] held;
    begin
      @(negedge clk);araddr=addr;arvalid=1;rready=0;
      @(posedge clk);while(!arready) @(posedge clk);
      @(negedge clk);arvalid=0;araddr=16'hffff;
      while(!rvalid) @(negedge clk);
      held=rdata;
      for(i=0;i<r_stall;i=i+1) begin
        @(posedge clk);#1;
        expect32({31'd0,rvalid},1,"RVALID stable during stall");
        expect32(rdata,held,"RDATA stable during stall");
        expect32({30'd0,rresp},0,"RRESP stable during stall");
        expect32({31'd0,arready},0,"one pending read response");
      end
      value=rdata;expect32({30'd0,rresp},0,"read OKAY response");
      @(negedge clk);rready=1;@(posedge clk);#1;@(negedge clk);rready=0;
    end
  endtask
  task rd_eq(input [15:0] addr,input [31:0] want,input [255:0] label_text);
    reg [31:0] got;
    begin axi_read(addr,0,got);expect32(got,want,label_text);end
  endtask
  task emit(input [31:0] frame,input [63:0] ticks,input [31:0] x,input [31:0] y,input [31:0] z,
            input pulse,input [63:0] pticks,input [63:0] pcount);
    begin
      @(negedge clk);sample_valid=1;sample_frame=frame;sample_ticks=ticks;
      sample_x=x;sample_y=y;sample_z=z;pulse_valid=pulse;pulse_ticks=pticks;pulse_count=pcount;
      @(posedge clk);#1;@(negedge clk);sample_valid=0;pulse_valid=0;
    end
  endtask
  task check_sample(input [31:0] frame,input [63:0] ticks,input [31:0] x,input [31:0] y,input [31:0] z);
    begin
      rd_eq(16'h40,frame,"sample frame");rd_eq(16'h44,ticks[31:0],"sample ticks low");
      rd_eq(16'h48,ticks[63:32],"sample ticks high");rd_eq(16'h4c,x,"sample X");
      rd_eq(16'h50,y,"sample Y");rd_eq(16'h54,z,"sample Z");
    end
  endtask
  task check_axi;
    integer i;reg [31:0] got;reg [31:0] saved;
    begin
      rd_eq(16'h00,32'h48585331,"capture ID");rd_eq(16'h04,32'h00010000,"capture version");
      rd_eq(16'h0c,6,"reset empty disabled");rd_eq(16'h10,100000000,"clock frequency");
      rd_eq(16'h14,3,"sample capacity");rd_eq(16'h18,2,"pulse capacity");rd_eq(16'h1c,0,"initial epoch");
      emit(100,64'h1ffffffff,32'hffffffef,32'h12345678,32'h80000000,1,64'hfffffffffffffff0,64'h100000000);
      rd_eq(16'h20,0,"disabled sample ignored");rd_eq(16'h24,0,"disabled pulse ignored");
      axi_write(16'h08,1,4'b0010,0,4,2);rd_eq(16'h08,0,"wrong byte cannot enable");
      axi_write(16'h08,1,4'b0001,4,0,5);rd_eq(16'h08,1,"W before AW enables");
      emit(100,64'h1ffffffff,32'hffffffef,32'h12345678,32'h80000000,1,64'hfffffffffffffff0,64'h100000000);
      rd_eq(16'h20,1,"enabled sample queued");rd_eq(16'h24,1,"simultaneous pulse queued");
      check_sample(100,64'h1ffffffff,32'hffffffef,32'h12345678,32'h80000000);
      rd_eq(16'h60,32'hfffffff0,"pulse ticks low");rd_eq(16'h64,32'hffffffff,"pulse ticks high");
      rd_eq(16'h68,0,"pulse count low rollover");rd_eq(16'h6c,1,"pulse count high rollover");
      for(i=0;i<3;i=i+1) begin
        rd_eq(16'h40,100,"reads do not pop");rd_eq(16'h20,1,"read preserves level");
      end
      emit(101,64'h200000000,1,2,3,1,64'h1234567800000001,64'h200000002);
      emit(102,64'h200000001,4,5,6,1,64'h9999999900000002,64'h300000003);
      emit(103,64'h200000002,7,8,9,1,64'h9999999900000003,64'h400000004);
      rd_eq(16'h20,3,"sample full");rd_eq(16'h24,2,"pulse full");
      rd_eq(16'h28,1,"sample drops new");rd_eq(16'h2c,2,"pulse drops new");
      rd_eq(16'h0c,25,"both overflow sticky enabled");rd_eq(16'h70,3,"sample highwater");rd_eq(16'h74,2,"pulse highwater");
      check_sample(100,64'h1ffffffff,32'hffffffef,32'h12345678,32'h80000000);
      axi_write(16'h30,1,4'b0010,0,0,0);rd_eq(16'h20,3,"wrong byte cannot pop");
      axi_write(16'h30,0,4'b0001,0,0,0);rd_eq(16'h20,3,"zero does not pop");
      axi_write(16'h30,1,4'b0001,0,5,15);rd_eq(16'h20,2,"stalled POP exactly once");
      check_sample(101,64'h200000000,1,2,3);
      axi_write(16'h34,1,4'b0001,5,0,12);rd_eq(16'h24,1,"pulse POP exactly once");
      rd_eq(16'h60,1,"second pulse selected");rd_eq(16'h64,32'h12345678,"second pulse high");
      rd_eq(16'h68,2,"second pulse count low");rd_eq(16'h6c,2,"second pulse count high");
      // Changing payload while AW/W are stalled must not replace their latched values.
      axi_write(16'h08,0,4'b0001,0,3,0);rd_eq(16'h08,0,"disable persists");
      emit(104,64'h55,99,99,99,1,64'h66,64'h77);
      rd_eq(16'h20,2,"disable retains queued sample");rd_eq(16'h24,1,"disable retains queued pulse");
      // A held read response is a snapshot even when CLEAR changes its source registers.
      fork
        begin axi_read(16'h40,25,got);expect32(got,101,"held head snapshot across clear");end
        begin repeat(5) @(negedge clk);axi_write(16'h08,3,4'b0001,0,0,8);end
      join
      rd_eq(16'h20,0,"clear flushes samples");rd_eq(16'h24,0,"clear flushes pulses");
      rd_eq(16'h1c,1,"clear increments epoch once");expect32(clear_pulses,1,"one clear pulse despite B stall");
      rd_eq(16'h08,1,"clear bit self clears enable retained");rd_eq(16'h0c,7,"clear resets overflow flags");
      rd_eq(16'h28,0,"clear resets sample drops");rd_eq(16'h2c,0,"clear resets pulse drops");
      rd_eq(16'h70,0,"clear resets sample highwater");rd_eq(16'h74,0,"clear resets pulse highwater");
      check_sample(0,0,0,0,0);rd_eq(16'h60,0,"empty pulse zero");rd_eq(16'h6c,0,"empty count zero");
      axi_write(16'h30,1,4'b0001,0,0,0);axi_write(16'h34,1,4'b0001,0,0,0);
      rd_eq(16'h20,0,"empty POP harmless");rd_eq(16'h24,0,"empty pulse POP harmless");
      source_idle=0;source_missed=32'h80000001;rd_eq(16'h78,0,"source busy");rd_eq(16'h7c,32'h80000001,"source missed count");
      source_idle=1;rd_eq(16'h78,1,"source idle");
      rd_eq(16'h38,0,"unimplemented read zero");rd_eq(16'h41,0,"unaligned read zero");
      axi_write(16'h00,32'hffffffff,4'hf,0,0,0);rd_eq(16'h00,32'h48585331,"read only ID intact");
      axi_write(16'h09,3,4'hf,0,0,0);rd_eq(16'h1c,1,"unaligned CTRL no clear");
      axi_write(16'h08,3,4'b0000,0,0,0);rd_eq(16'h1c,1,"zero strobes no clear");
      // Clear and record arrival on the same edge: clear wins, both FIFOs stay empty.
      fork
        axi_write(16'h08,3,4'b0001,0,0,5);
        begin
          wait(capture_clear===1'b1);@(negedge clk);sample_valid=1;pulse_valid=1;
          @(posedge clk);#1;@(negedge clk);sample_valid=0;pulse_valid=0;
        end
      join
      rd_eq(16'h20,0,"clear beats concurrent sample");rd_eq(16'h24,0,"clear beats concurrent pulse");
      rd_eq(16'h1c,2,"second epoch");expect32(clear_pulses,2,"second single clear pulse");
      // Repeated non-power-of-two wrap, with signed words passed through unchanged.
      for(i=0;i<20;i=i+1) begin
        emit(200+i,64'h123400000000+i,32'hffffffff-i,32'h80000000+i,32'h10000000+i,1,64'h567800000000+i,64'h9abc00000000+i);
        check_sample(200+i,64'h123400000000+i,32'hffffffff-i,32'h80000000+i,32'h10000000+i);
        axi_write(16'h30,1,4'b0001,i%3,(i+1)%3,i%4);
        axi_write(16'h34,1,4'b0001,(i+1)%3,i%3,i%5);
      end
      rd_eq(16'h20,0,"wrap final samples empty");rd_eq(16'h24,0,"wrap final pulses empty");
      rd_eq(16'h28,0,"wrap no sample loss");rd_eq(16'h2c,0,"wrap no pulse loss");
    end
  endtask
  initial begin
    repeat(3) @(negedge clk);rst_n=1;repeat(2) @(posedge clk);#1;
    check_fifo();random_fifo();check_axi();
    if(errors==0) $display("PASS: all sync_capture_axi checks OK (%0d assertions)",checks);
    else $display("FAIL: sync_capture_axi %0d errors",errors);$finish;
  end
`endif

  initial begin #10000000;$display("FAIL: global watchdog");$finish;end
endmodule
`default_nettype wire
