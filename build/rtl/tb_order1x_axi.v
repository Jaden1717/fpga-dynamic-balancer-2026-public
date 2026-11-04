`timescale 1ns/1ps
// Directed bus-level contract tests: independent AW/W, byte enables,
// response stalls, invalid commands, result ownership, and pipeline aborts.
module tb_order1x_axi;
  reg clk=0; always #5 clk=~clk;
  reg rst_n=0;
  reg [15:0] S_AXI_AWADDR=0,S_AXI_ARADDR=0;
  reg [2:0] S_AXI_AWPROT=0,S_AXI_ARPROT=0;
  reg S_AXI_AWVALID=0,S_AXI_WVALID=0,S_AXI_BREADY=0;
  reg S_AXI_ARVALID=0,S_AXI_RREADY=0;
  reg [31:0] S_AXI_WDATA=0;
  reg [3:0] S_AXI_WSTRB=0;
  wire S_AXI_AWREADY,S_AXI_WREADY,S_AXI_BVALID,S_AXI_ARREADY,S_AXI_RVALID;
  wire [1:0] S_AXI_BRESP,S_AXI_RRESP;
  wire [31:0] S_AXI_RDATA;
  order1x_axi dut(.*);
  integer checks=0, k, timeout;
  reg [31:0] value,held_r,held_i,held_mag,held_phase,cycles;
  task demand;
    input condition; input [8*120-1:0] why;
    begin checks=checks+1; if(condition!==1'b1) begin
      $display("FAIL: %0s at %0t",why,$time);
      $fatal(1,"order1x AXI contract");
    end end
  endtask
  task tick; begin @(posedge clk); #1; end endtask
  task reset_dut;
    begin
      @(negedge clk); rst_n=0; S_AXI_AWVALID=0;S_AXI_WVALID=0;
      S_AXI_BREADY=0;S_AXI_ARVALID=0;S_AXI_RREADY=0;
      repeat(3) tick;
      @(negedge clk);rst_n=1;tick;
    end
  endtask
  task send_aw;
    input [15:0] address;
    begin
      @(negedge clk);S_AXI_AWADDR=address;S_AXI_AWVALID=1;
      @(posedge clk);while(!S_AXI_AWREADY) @(posedge clk);
      #1;@(negedge clk);S_AXI_AWVALID=0;
    end
  endtask
  task send_w;
    input [31:0] data; input [3:0] strb;
    begin
      @(negedge clk);S_AXI_WDATA=data;S_AXI_WSTRB=strb;S_AXI_WVALID=1;
      @(posedge clk);while(!S_AXI_WREADY) @(posedge clk);
      #1;@(negedge clk);S_AXI_WVALID=0;
    end
  endtask
  task finish_b;
    input integer stalls;
    integer n;
    begin
      timeout=0;while(!S_AXI_BVALID && timeout<20) begin tick;timeout=timeout+1;end
      demand(S_AXI_BVALID && S_AXI_BRESP==0,"write returns OKAY");
      for(n=0;n<stalls;n=n+1) begin tick;
        demand(S_AXI_BVALID && S_AXI_BRESP==0,"BVALID/BRESP held under backpressure");
        demand(!S_AXI_AWREADY && !S_AXI_WREADY,"no new write accepted with pending response");
      end
      @(negedge clk);S_AXI_BREADY=1;tick;
      @(negedge clk);S_AXI_BREADY=0;
    end
  endtask
  task write_reg;
    input [15:0] address;input [31:0] data; input [3:0] strb;
    input integer skew; input integer stalls;
    begin
      if(skew>=0) begin
        send_aw(address);repeat(skew) tick;send_w(data,strb);
      end else begin
        send_w(data,strb);repeat(-skew) tick;send_aw(address);
      end
      finish_b(stalls);
    end
  endtask
  task read_reg;
    input [15:0] address;input integer stalls; output [31:0] data;
    integer n;
    reg [31:0] snapshot;
    begin
      @(negedge clk);S_AXI_ARADDR=address;S_AXI_ARVALID=1;
      @(posedge clk);while(!S_AXI_ARREADY) @(posedge clk);
      #1;@(negedge clk);S_AXI_ARVALID=0;
      timeout=0;while(!S_AXI_RVALID && timeout<20) begin tick;timeout=timeout+1;end
      demand(S_AXI_RVALID && S_AXI_RRESP==0,"read returns OKAY");snapshot=S_AXI_RDATA;
      for(n=0;n<stalls;n=n+1) begin tick;
        demand(S_AXI_RVALID && S_AXI_RDATA===snapshot,"RVALID/RDATA held under backpressure");
        demand(!S_AXI_ARREADY,"AR blocked while response pending");
      end
      data=snapshot;
      @(negedge clk);S_AXI_RREADY=1;tick;
      @(negedge clk);S_AXI_RREADY=0;
    end
  endtask
  task expect_reg;
    input [15:0] address; input [31:0] expected; input [8*110-1:0] why;
    begin read_reg(address,0,value);if(value!==expected)
      $display("REGISTER: address=%h got=%h want=%h",address,value,expected);
      demand(value===expected,why);
    end
  endtask
  task clear_core;
    begin write_reg(16'h08,2,1,0,0);end
  endtask
  task begin_window;
    input [7:0] avg;
    begin
      write_reg(16'h10,avg,1,-2,2);
      write_reg(16'h08,1,1,3,2);
      expect_reg(16'h1c,64<<avg,"expected sample count latched at start");
      expect_reg(16'h18,0,"new window starts with zero accepted words");
    end
  endtask
  task push_word;
    input integer sample;
    begin write_reg(16'h14,sample,15,0,0);end
  endtask
  task await_result;
    integer n;
    begin
      n=0;value=0;
      while(!value[2] && n<40) begin read_reg(16'h0c,0,value);n=n+1;end
      demand(value[2] && value[0] && !value[1],"result held and busy, sample stream closed");
    end
  endtask
  task impulse;
    input integer position;
    begin
      begin_window(0);
      for(k=0;k<64;k=k+1) push_word(k==position ? 320 : 0);
      await_result;
      expect_reg(16'h18,64,"every sample committed exactly once");
      expect_reg(16'h28,2560,"unity-axis impulse magnitude scaling");
    end
  endtask
  initial begin
    reset_dut;
    expect_reg(16'h00,32'h48583131,"readable H X 1 1 identity");
    expect_reg(16'h04,32'h00010000,"version one");
    expect_reg(16'h10,0,"AVG reset value");
    expect_reg(16'h0c,0,"reset status idle with no ready/result/error");
    expect_reg(16'h38,0,"reset error flags empty");
    expect_reg(16'hfc,0,"unimplemented reads return zero");
    write_reg(16'h10,3,0,0,0);
    expect_reg(16'h10,0,"WSTRB zero cannot alter AVG");
    write_reg(16'h08,1,2,0,0);
    expect_reg(16'h0c,0,"START needs byte zero strobe");
    write_reg(16'h30,1,1,-3,3);
    expect_reg(16'h0c,0,"empty ACK ignored");
    fork
      send_aw(16'h10);
      send_w(3,1);
    join
    finish_b(5);
    expect_reg(16'h10,3,"simultaneous AW/W pair writes once");
    push_word(1);
    expect_reg(16'h38,2,"input before START rejected with not-ready error");
    expect_reg(16'h18,0,"rejected input not counted");
    clear_core;
    write_reg(16'h10,16,1,2,0);
    write_reg(16'h08,1,1,-2,0);
    expect_reg(16'h38,8,"invalid AVG including high nibble rejected");
    expect_reg(16'h0c,16,"invalid START leaves idle with sticky error");
    clear_core;
    expect_reg(16'h10,16,"CLEAR preserves programmed AVG");
    begin_window(0);
    // AW may arrive many clocks before W; no sample may be consumed early.
    send_aw(16'h14);
    expect_reg(16'h18,0,"AW alone has no side effect");
    send_w(32'hffffffff,15);
    finish_b(9);
    expect_reg(16'h18,1,"stalled B response commits sign-extended input only once");
    clear_core;
    begin_window(0);
    write_reg(16'h14,320,3,4,2);
    expect_reg(16'h38,16,"partial INPUT rejected");
    expect_reg(16'h18,0,"partial INPUT not counted");
    write_reg(16'h14,32'h000fffff,15,-3,0);
    expect_reg(16'h38,20,"non-sign-extended INPUT rejected");
    expect_reg(16'h18,0,"out-of-range INPUT not counted");
    write_reg(16'h08,1,1,0,0);
    expect_reg(16'h38,21,"busy START rejected without aborting current window");
    write_reg(16'h10,2,1,0,0);
    expect_reg(16'h1c,64,"AVG update cannot change active window length");
    for(k=0;k<64;k=k+1) begin
      if(k%9==0) repeat(4) tick;
      push_word(k==0 ? 320 : 0);
    end
    await_result;
    expect_reg(16'h20,2560,"real coefficient for zero-angle impulse");
    expect_reg(16'h24,0,"imag coefficient for zero-angle impulse");
    expect_reg(16'h28,2560,"magnitude for zero-angle impulse");
    expect_reg(16'h2c,0,"zero-angle phase exact");
    read_reg(16'h0c,3,value);demand(value[3],"nonzero coefficient defines phase");
    read_reg(16'h34,0,cycles);demand(cycles>64,"cycle counter includes AXI input gaps");
    repeat(30) tick;
    expect_reg(16'h34,cycles,"cycle counter freezes at result availability");
    write_reg(16'h08,1,1,-2,0);
    push_word(17);
    expect_reg(16'h38,23,"held result blocks START and INPUT");
    expect_reg(16'h20,2560,"illegal commands preserve held result");
    expect_reg(16'h18,64,"extra input cannot extend window");
    write_reg(16'h30,1,2,0,0);
    read_reg(16'h0c,0,value);demand(value[2],"ACK needs byte zero strobe");
    write_reg(16'h30,1,1,0,7);
    read_reg(16'h0c,0,value);demand(!value[2]&&!value[0],"ACK releases result ownership");
    clear_core;
    impulse(16);
    expect_reg(16'h20,0,"quarter-turn impulse real zero");
    expect_reg(16'h24,-2560,"negative sine convention");
    expect_reg(16'h2c,32'hc0000000,"quarter-turn phase minus 90 degrees");
    // AR handshakes on exactly the CLEAR commit edge. It observes the old
    // value, then retains that snapshot under R backpressure after clear.
    send_aw(16'h08);
    send_w(2,1);
    S_AXI_ARADDR=16'h24;S_AXI_ARVALID=1;
    tick;@(negedge clk);S_AXI_ARVALID=0;
    demand(S_AXI_RVALID && S_AXI_RDATA==32'hfffff600,"atomic pre-clear snapshot at simultaneous AR/CLEAR");
    finish_b(3);
    repeat(9) begin tick;demand(S_AXI_RVALID && S_AXI_RDATA==32'hfffff600,
      "read snapshot survives simultaneous computation clear");end
    @(negedge clk);S_AXI_RREADY=1;tick;@(negedge clk);S_AXI_RREADY=0;
    expect_reg(16'h24,0,"fresh read sees cleared coefficient");
    expect_reg(16'h0c,0,"clear aborts held result and errors");
    expect_reg(16'h1c,0,"clear resets expected count");
    expect_reg(16'h34,0,"clear resets measured cycles");
    begin_window(1);
    for(k=0;k<128;k=k+1) push_word(47);
    await_result;
    expect_reg(16'h20,0,"two-turn DC rejected from real coefficient");
    expect_reg(16'h24,0,"two-turn DC rejected from imag coefficient");
    expect_reg(16'h28,0,"zero vector has zero magnitude");
    read_reg(16'h0c,0,value);demand(!value[3],"zero vector phase marked undefined");
    clear_core;
    begin_window(0);
    repeat(11) push_word(-524288);
    clear_core;repeat(100) tick;
    expect_reg(16'h0c,0,"mid-accumulation clear cannot publish stale result");
    impulse(0);
    write_reg(16'h30,1,1,0,0);
    begin_window(0);
    for(k=0;k<64;k=k+1) push_word(k==0 ? 320 : 0);
    clear_core;repeat(100) tick;
    expect_reg(16'h0c,0,"clear during finalization/CORDIC aborts entire pipeline");
    begin_window(0);
    for(k=0;k<64;k=k+1) push_word(k==16 ? 320 : 0);
    reset_dut;repeat(100) tick;
    expect_reg(16'h0c,0,"reset aborts finalization/CORDIC with no stale result");
    expect_reg(16'h10,0,"reset clears AVG");
    // An orphaned AW must not survive reset and pair with an unrelated W.
    send_aw(16'h14);reset_dut;
    // CLEAR wins if software writes both command bits.
    write_reg(16'h08,3,1,0,0);
    expect_reg(16'h0c,0,"combined CLEAR START leaves idle");
    impulse(0);
    $display("PASS: all order1x_axi checks OK (%0d checks)",checks);
    $finish;
  end
  initial begin #10000000;$fatal(1,"testbench watchdog");end
endmodule


