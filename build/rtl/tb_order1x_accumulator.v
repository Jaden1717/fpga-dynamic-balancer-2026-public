`timescale 1ns/1ps
// Contract tests catch Fourier sign/gain errors, angle advance on stalls,
// overflow, incomplete pipeline flush, rounding bias, and result overwrite.
module tb_order1x_accumulator;
  reg clk=0;
  always #5 clk=~clk;
  reg rst_n=0, clear_i=0, start_i=0;
  reg [3:0] avg_log2_i=0;
  reg sample_valid_i=0, result_ready_i=0;
  reg signed [19:0] sample_i=0;
  wire start_ready_o, sample_ready_o, result_valid_o;
  wire signed [31:0] real_o, imag_o;
  wire config_error_o, busy_o;
  order1x_accumulator dut(.*);
  integer checks=0, cases=0, k, turn, idx, value, gaps, n;
  integer seed=32'h13798742;
  real angle, phase, amp, dc;
  task check;
    input cond;
    input [8*120-1:0] why;
    begin
      checks=checks+1;
      if (cond !== 1'b1) begin
        $display("FAIL: %0s at %0t, real=%0d imag=%0d",why,$time,real_o,imag_o);
        $fatal(1,"order1x accumulator contract");
      end
    end
  endtask
  task tick;
    begin @(posedge clk); #1; end
  endtask
  task reset_dut;
    begin
      @(negedge clk); rst_n=0; start_i=0; clear_i=0;
      sample_valid_i=0; result_ready_i=0;
      repeat(3) tick;
      @(negedge clk); rst_n=1;
      tick;
      check(start_ready_o && !busy_o && !result_valid_o,"reset returns idle without result");
    end
  endtask
  task start_block;
    input integer av;
    begin
      @(negedge clk);
      check(start_ready_o,"start ready before transaction");
      avg_log2_i=av; start_i=1;
      tick;
      @(negedge clk); start_i=0;
      check(busy_o && sample_ready_o && !start_ready_o,"valid start accepts samples");
      // The configuration belongs to the accepted start, not later input pins.
      avg_log2_i=15;
    end
  endtask
  task send_value;
    input integer v;
    input integer gap;
    begin
      @(negedge clk); sample_valid_i=0;
      repeat(gap) tick;
      @(negedge clk); sample_i=v; sample_valid_i=1;
      @(posedge clk);
      while (!sample_ready_o) @(posedge clk);
      #1;
      @(negedge clk); sample_valid_i=0;
    end
  endtask
  task expect_result;
    input integer want_r;
    input integer want_i;
    input integer tolerance;
    integer timeout, held_r, held_i, difference_r, difference_i;
    begin
      timeout=0;
      while (!result_valid_o && timeout<30) begin tick; timeout=timeout+1; end
      check(result_valid_o,"all samples produce a bounded-latency result");
      difference_r=$signed(real_o)-want_r;
      difference_i=$signed(imag_o)-want_i;
      check(difference_r<=tolerance && difference_r>=-tolerance,"real coefficient sign/gain/rounding");
      check(difference_i<=tolerance && difference_i>=-tolerance,"imag coefficient sign/gain/rounding");
      held_r=$signed(real_o); held_i=$signed(imag_o);
      repeat(13) begin
        tick;
        check(result_valid_o && $signed(real_o)==held_r && $signed(imag_o)==held_i,
              "backpressure holds result valid/data");
        check(!start_ready_o && !sample_ready_o && busy_o,"pending result blocks another block");
      end
      @(negedge clk); result_ready_i=1;
      tick;
      @(negedge clk); result_ready_i=0;
      check(!result_valid_o && start_ready_o && !busy_o,"result handshake returns idle");
      cases=cases+1;
    end
  endtask
  // Hand-derived quadrant impulses. A 64-point block scales each impulse
  // by 2/64*256=8 at unity or minus-unity twiddles.
  task impulse;
    input integer position;
    input integer amplitude;
    input integer av;
    input integer want_r;
    input integer want_i;
    integer j, t;
    begin
      start_block(av);
      for (t=0;t<(1<<av);t=t+1)
        for (j=0;j<64;j=j+1)
          send_value(j==position ? amplitude : 0,(j%11==0) ? 2 : 0);
      expect_result(want_r,want_i,0);
    end
  endtask
  task constant_block;
    input integer constant_value;
    input integer av;
    integer j;
    begin
      start_block(av);
      for (j=0;j<(64<<av);j=j+1) send_value(constant_value,j%3);
      expect_result(0,0,0);
    end
  endtask
  initial begin
    reset_dut;
    impulse(0,12345,0,98760,0);
    impulse(16,12345,0,0,-98760);
    impulse(32,12345,0,-98760,0);
    impulse(48,12345,0,0,98760);
    impulse(0,-524288,0,-4194304,0);
    impulse(0,524287,1,4194296,0);
    impulse(16,-524288,8,0,4194304);
    constant_block(260000,0);
    constant_block(-524288,1);
    constant_block(524287,8);
    // k=2 has Q16 cosine=64277, sine=12785. With x=4096,
    // exact normalized values are 32138.5 and -6392.5: ties away from zero.
    impulse(2,4096,0,32139,-6393);
    impulse(2,-4096,0,-32139,6393);
    // Independent continuous Fourier expectation: A*cos(theta+phase)
    // gives A*(cos phase+j*sin phase). Integer sample and LUT quantization
    // are allowed 32 Q8 units (<0.13 raw counts) at A=10000.
    for (n=0;n<4;n=n+1) begin
      phase=(n*0.7)-0.9; amp=10000; dc=210000;
      start_block(n%2);
      for(idx=0;idx<(64<<(n%2));idx=idx+1) begin
        angle=6.2831853071795864769*(idx%64)/64.0;
        value=$rtoi(dc+amp*$cos(angle+phase));
        gaps=($random(seed)&3);
        send_value(value,gaps);
      end
      expect_result($rtoi(amp*256*$cos(phase)),$rtoi(amp*256*$sin(phase)),32);
    end
    // Worst sign-aligned full-scale input, 256 turns, exercises 52-bit sum.
    // Literal expected coefficient is independently summed from mathematical
    // rounded cosines, not obtained from DUT signals or its ROM.
    start_block(8);
    for(idx=0;idx<16384;idx=idx+1) begin
      k=idx%64;
      value=(k<=16 || k>=48) ? 524287 : -524288;
      send_value(value,0);
    end
    expect_result(170753885,0,0);
    start_block(8);
    for(idx=0;idx<16384;idx=idx+1) begin
      k=idx%64;
      value=(k<=16 || k>=48) ? -524288 : 524287;
      send_value(value,0);
    end
    expect_result(-170753885,0,0);
    // Invalid configurations emit one pulse and never open a transaction.
    for(n=9;n<16;n=n+1) begin
      @(negedge clk); avg_log2_i=n; start_i=1;
      tick;
      check(config_error_o && !busy_o && !result_valid_o && !sample_ready_o,
            "invalid averaging configuration rejected");
      @(negedge clk); start_i=0;
      tick;
      check(!config_error_o && start_ready_o,"configuration error is one cycle");
    end
    // Clear must abort products already in flight, even on the last sample.
    start_block(0);
    for(idx=0;idx<63;idx=idx+1) send_value(1234,0);
    @(negedge clk); sample_i=524287; sample_valid_i=1;
    tick;
    @(negedge clk); sample_valid_i=0; clear_i=1; start_i=1;
    tick;
    @(negedge clk); clear_i=0; start_i=0;
    repeat(8) begin tick; check(!result_valid_o && !busy_o,"clear flushes final product and wins over start"); end
    impulse(48,29,0,0,232);
    // The final sum is a registered stage before normalization. Clear during
    // that stage must prevent a transient result from escaping into a new
    // transaction; both ready outputs remain closed until it finishes/aborts.
    start_block(0);
    for(idx=0;idx<63;idx=idx+1) send_value(0,0);
    @(negedge clk); sample_i=524287; sample_valid_i=1;
    tick;
    @(negedge clk); sample_valid_i=0;
    tick;
    check(!result_valid_o && busy_o && !sample_ready_o && !start_ready_o,
          "final sum remains busy until registered normalization finishes");
    @(negedge clk); clear_i=1;
    tick;
    @(negedge clk); clear_i=0;
    repeat(4) begin tick; check(!result_valid_o && !busy_o && start_ready_o,
                              "clear in final-sum stage suppresses pending normalization"); end
    impulse(0,37,0,296,0);
    // Reset during a partial block must not leak a result into the next epoch.
    start_block(1);
    for(idx=0;idx<41;idx=idx+1) send_value(-1234,0);
    reset_dut;
    impulse(32,17,0,-136,0);
    // Continuous valid stream proves one-sample-per-clock acceptance.
    start_block(0);
    @(negedge clk); sample_valid_i=1;
    for(idx=0;idx<64;idx=idx+1) begin
      sample_i=(idx==0) ? 100 : 0;
      @(posedge clk); check(sample_ready_o,"uninterrupted stream accepts every input clock"); #1;
      @(negedge clk);
    end
    sample_valid_i=0;
    expect_result(800,0,0);
    $display("PASS: order1x_accumulator %0d cases %0d checks",cases,checks);
    $finish;
  end
  initial begin #100000000; $fatal(1,"TIMEOUT: order1x_accumulator"); end
endmodule
