`timescale 1ns/1ps
// Observable contracts catch CDC/filter errors, false startup pulses,
// invalid modular periods, or sample/tacho timestamp disagreement.
module tb_tacho_timestamp;
  reg clk=0;
  always #5 clk=~clk;
  reg rst_n=0, enable=1, clear=0, tacho_async=0, sample_strobe=0;
  wire [7:0] time_ticks, sample_ticks;
  wire sample_valid, rv, rp, fv, fp, qv, qp;
  wire [7:0] rt, rc, rpt, ft, fc, fpt, qt, qc, qpt;
  capture_timebase #(.COUNTER_BITS(8)) timebase (
    .clk(clk),.rst_n(rst_n),.sample_strobe(sample_strobe),
    .time_ticks(time_ticks),.sample_ticks(sample_ticks),.sample_valid(sample_valid));
  tacho_timestamp #(.COUNTER_BITS(8),.FILTER_CYCLES(4),.RISING_EDGE(1)) rising (
    .clk(clk),.rst_n(rst_n),.enable(enable),.clear(clear),.tacho_async(tacho_async),
    .time_ticks(time_ticks),.event_valid(rv),.event_ticks(rt),.pulse_count(rc),
    .period_valid(rp),.period_ticks(rpt));
  tacho_timestamp #(.COUNTER_BITS(8),.FILTER_CYCLES(4),.RISING_EDGE(0)) falling (
    .clk(clk),.rst_n(rst_n),.enable(enable),.clear(clear),.tacho_async(tacho_async),
    .time_ticks(time_ticks),.event_valid(fv),.event_ticks(ft),.pulse_count(fc),
    .period_valid(fp),.period_ticks(fpt));
  tacho_timestamp #(.COUNTER_BITS(8),.FILTER_CYCLES(1),.RISING_EDGE(1)) filter_one (
    .clk(clk),.rst_n(rst_n),.enable(enable),.clear(clear),.tacho_async(tacho_async),
    .time_ticks(time_ticks),.event_valid(qv),.event_ticks(qt),.pulse_count(qc),
    .period_valid(qp),.period_ticks(qpt));

  // Defaults are 64-bit. This companion observes upper timestamp bits after
  // the 8-bit fixture wraps, guarding against hidden fixed-width truncation.
  wire [63:0] wide_time, wide_sample, wide_event, wide_count, wide_period;
  wire wide_sample_valid, wide_event_valid, wide_period_valid;
  capture_timebase default_timebase (
    .clk(clk),.rst_n(rst_n),.sample_strobe(sample_strobe),.time_ticks(wide_time),
    .sample_ticks(wide_sample),.sample_valid(wide_sample_valid));
  tacho_timestamp default_tacho (
    .clk(clk),.rst_n(rst_n),.enable(enable),.clear(clear),.tacho_async(tacho_async),
    .time_ticks(wide_time),.event_valid(wide_event_valid),.event_ticks(wide_event),
    .pulse_count(wide_count),.period_valid(wide_period_valid),.period_ticks(wide_period));
  reg [63:0] wide_model=0, wide_before, wide_prior=0;
  reg wide_have_prior=0;

  integer checks=0, errors=0, rn=0, fn=0, qn=0, sample_n=0, csv;
  reg [7:0] r_ticks[0:255], f_ticks[0:255], q_ticks[0:255];
  reg [7:0] r_period[0:255], f_period[0:255];
  reg r_period_valid[0:255], f_period_valid[0:255];
  reg [7:0] model_time=0, before_time;
  reg strobe_before;
  integer rising_count=0, falling_count=0;
  reg have_r=0, have_f=0;
  reg [7:0] prior_r=0, prior_f=0;
  task check;
    input condition;
    input [8*100-1:0] reason;
    begin
      checks=checks+1;
      if (condition !== 1'b1) begin
        errors=errors+1;
        $display("FAIL: %0s at %0t",reason,$time);
      end
    end
  endtask

  // Test-owned clock count; expectations never use a DUT internal signal.
  always @(posedge clk) begin
    before_time=model_time; wide_before=wide_model; strobe_before=sample_strobe;
    if (!rst_n) begin model_time=0; wide_model=0; end
    else begin model_time=model_time+8'd1; wide_model=wide_model+64'd1; end
    #1;
    check(time_ticks === model_time,"shared clock increments once and wraps");
    check(wide_time === wide_model,"default 64-bit clock retains upper timestamp bits");
    check(wide_sample_valid === sample_valid,"default sample valid matches parameterized fixture");
    check(wide_event_valid === rv && wide_period_valid === rp,"default tacho validity matches parameterized fixture");
    if (!rst_n || clear || !enable) wide_have_prior=0;
    if (rst_n && wide_sample_valid)
      check(wide_sample === wide_before,"default sample uses full pre-increment 64-bit time");
    if (rst_n && wide_event_valid) begin
      check(wide_event === wide_before,"default event timestamp keeps all 64 bits");
      check(wide_count[7:0] === rc,"default pulse count matches actual event count");
      check(wide_period_valid === wide_have_prior,"default first event has no period");
      if (wide_have_prior)
        check(wide_period === wide_before-wide_prior,"default period is full shared elapsed ticks");
      wide_prior=wide_before; wide_have_prior=1;
    end
    if (!rst_n) begin
      check(!sample_valid && !rv && !fv && !qv,"reset clears valid outputs");
      check(sample_ticks==0 && rc==0 && fc==0,"reset clears capture and count state");
    end else begin
      check(sample_valid === strobe_before,"sample_valid matches registered strobe");
      check(!rp || rv,"rising period_valid is an event pulse");
      check(!fp || fv,"falling period_valid is an event pulse");
      check(!qp || qv,"filter=1 period_valid is an event pulse");
      if (sample_valid) begin
        check(sample_ticks === before_time,"sample uses pre-increment shared ticks");
        sample_n=sample_n+1;
        $fdisplay(csv,"sample,%0d,0,0,0",sample_ticks);
      end
      if (rv) begin
        check(enable && !clear,"disabled or clear cannot emit event");
        check(rt === before_time,"rising uses confirmation clock");
        if (sample_valid) check(rt === sample_ticks,"sample and tacho agree on same cycle");
        r_ticks[rn]=rt; r_period[rn]=rpt; r_period_valid[rn]=rp; rn=rn+1;
        $fdisplay(csv,"pulse_rising,%0d,%0d,%0d,%0d",rt,rp,rpt,rc);
      end
      if (fv) begin
        check(ft === before_time,"falling uses shared confirmation clock");
        if (sample_valid) check(ft === sample_ticks,"falling and sample agree");
        f_ticks[fn]=ft; f_period[fn]=fpt; f_period_valid[fn]=fp; fn=fn+1;
        $fdisplay(csv,"pulse_falling,%0d,%0d,%0d,%0d",ft,fp,fpt,fc);
      end
      if (qv) begin
        check(qt === before_time,"filter=1 uses confirmation clock");
        q_ticks[qn]=qt; qn=qn+1;
      end
    end
  end

  task clocks;
    input integer n;
    integer i;
    begin for(i=0;i<n;i=i+1) begin @(posedge clk); #2; end end
  endtask
  task reset_case;
    input initial_level;
    begin
      @(negedge clk);
      rst_n=0; enable=1; clear=0; tacho_async=initial_level; sample_strobe=0;
      $fdisplay(csv,"# reset: new timebase epoch");
      clocks(3);
      @(negedge clk); rst_n=1;
      rising_count=0; falling_count=0; have_r=0; have_f=0;
      clocks(14);
    end
  endtask
  task verify_transition;
    input level;
    input [7:0] expected_tick;
    input integer old_r,old_f,old_q;
    reg [7:0] wanted_period;
    begin
      if (level) begin
        check(rn==old_r+1 && fn==old_f,"exactly one rising event");
        check(r_ticks[old_r] === expected_tick,"four-sample filter confirms at +5 ticks");
        check(qn==old_q+1 && q_ticks[old_q] === expected_tick-8'd3,"filter=1 confirms at +2 ticks");
        rising_count=rising_count+1;
        check(rc === (rising_count & 255),"rising pulse_count counts actual events");
        check(r_period_valid[old_r] === have_r,"first rising event has no period");
        wanted_period=expected_tick-prior_r;
        if(have_r) check(r_period[old_r] === wanted_period,"rising period is modulo elapsed ticks");
        prior_r=expected_tick; have_r=1;
      end else begin
        check(fn==old_f+1 && rn==old_r && qn==old_q,"exactly one falling event");
        check(f_ticks[old_f] === expected_tick,"falling polarity uses stable filter");
        falling_count=falling_count+1;
        check(fc === (falling_count & 255),"falling pulse_count counts actual events");
        check(f_period_valid[old_f] === have_f,"first falling event has no period");
        wanted_period=expected_tick-prior_f;
        if(have_f) check(f_period[old_f] === wanted_period,"falling period is elapsed ticks");
        prior_f=expected_tick; have_f=1;
      end
    end
  endtask
  task transition;
    input level;
    input integer phase_ns;
    integer old_r,old_f,old_q;
    reg [7:0] expected_tick;
    begin
      @(negedge clk); #(phase_ns);
      old_r=rn; old_f=fn; old_q=qn; tacho_async=level;
      expected_tick=model_time+8'd5;
      clocks(8);
      verify_transition(level,expected_tick,old_r,old_f,old_q);
      check(!rv && !fv && !rp && !fp,"valid outputs last one clock");
    end
  endtask
  task transition_at;
    input level;
    input [7:0] tick;
    integer old_r,old_f,old_q;
    begin
      @(negedge clk);
      while(model_time !== tick) @(negedge clk);
      old_r=rn; old_f=fn; old_q=qn; tacho_async=level;
      clocks(8);
      verify_transition(level,tick+8'd5,old_r,old_f,old_q);
    end
  endtask

  integer old_r,old_f,old_q;
  reg [7:0] rise_expected,fall_expected;
  initial begin
    csv=$fopen("tacho_timestamp_events.csv","w");
    if(csv==0) $fatal(1,"FAIL: cannot open CSV trace");
    $fdisplay(csv,"# counter_bits=8 clock_hz=100000000 filter_cycles=4 confirmation_delay_ticks=5");
    $fdisplay(csv,"kind,ticks,period_valid,period_ticks,pulse_count");
    $display("CASE: initial high is baseline, not pulse");
    old_r=rn; old_f=fn; old_q=qn;
    reset_case(1);
    check(rn==old_r && fn==old_f && qn==old_q,"startup at high fabricates no pulses");
    transition(0,0); transition(1,0);

    $display("CASE: stable pulses and changed speed");
    reset_case(0); sample_strobe=1;
    transition(1,0); transition(0,0); transition(1,0); transition(0,0);
    clocks(11); transition(1,0); clocks(5); transition(0,0);
    clocks(19); transition(1,0); transition(0,0);

    $display("CASE: asynchronous phase around clock edge");
    transition(1,4); transition(0,6); transition(1,6); transition(0,4);

    $display("CASE: reject three-clock glitch, qualify four-clock pulse");
    reset_case(0); old_r=rn; old_f=fn;
    @(negedge clk); tacho_async=1;
    clocks(3); @(negedge clk); tacho_async=0; clocks(10);
    check(rn==old_r && fn==old_f,"three-clock glitch rejected");
    old_r=rn; old_f=fn; old_q=qn;
    @(negedge clk); rise_expected=model_time+8'd5; tacho_async=1;
    clocks(4);
    @(negedge clk); fall_expected=model_time+8'd5; tacho_async=0;
    clocks(10);
    check(rn==old_r+1 && fn==old_f+1,"four-clock pulse qualifies exactly once at each polarity");
    check(r_ticks[old_r] === rise_expected && f_ticks[old_f] === fall_expected,"filter boundary timestamps exact");
    check(!r_period_valid[old_r] && !f_period_valid[old_f],"first qualified narrow pulse has no period");
    rising_count=1; falling_count=1; have_r=1; have_f=1;
    prior_r=rise_expected; prior_f=fall_expected;
    transition(1,0);

    $display("CASE: disable/re-enable at high suppresses startup event");
    old_r=rn; old_f=fn; old_q=qn;
    @(negedge clk); enable=0; tacho_async=0;
    $fdisplay(csv,"# disable: period history invalidated"); clocks(8);
    @(negedge clk); tacho_async=1; clocks(8);
    @(negedge clk); enable=1; have_r=0; have_f=0;
    $fdisplay(csv,"# enable: stable baseline re-arming"); clocks(16);
    check(rn==old_r && fn==old_f && qn==old_q,"disable/re-enable fabricates no event");
    check(rc==rising_count && fc==falling_count,"disable preserves lifetime counts");
    transition(0,0); transition(1,0);

    $display("CASE: clear resets event state, not shared time");
    old_r=rn; old_f=fn; old_q=qn;
    @(negedge clk); clear=1;
    $fdisplay(csv,"# clear: event state reset; shared time continues"); clocks(1);
    check(rc==0 && fc==0 && rt==0 && ft==0 && rpt==0 && fpt==0,"clear resets count/timestamp/period");
    @(negedge clk); clear=0;
    rising_count=0; falling_count=0; have_r=0; have_f=0; clocks(16);
    check(rn==old_r && fn==old_f && qn==old_q,"clear re-arms current high without a pulse");
    transition(0,0); transition(1,0);

    $display("CASE: modulo wrap and missing pulse");
    reset_case(0); sample_strobe=1; old_r=rn;
    transition_at(1,8'd240); transition_at(0,8'd249); transition_at(1,8'd2);
    check(r_ticks[old_r]==245 && r_ticks[old_r+1]==7 && r_period[old_r+1]==18,"literal wrap fixture 245->7 is 18 clocks");
    transition(0,0); old_r=rn; old_f=fn; clocks(40);
    check(rn==old_r && fn==old_f,"missing pulse creates no synthetic event");
    transition(1,0);
    check(r_period[rn-1]>40,"missing pulse preserves actual long interval");

    $display("CASE: sample hold and valid deassertion");
    @(negedge clk); sample_strobe=0; rise_expected=sample_ticks; clocks(4);
    check(!sample_valid && sample_ticks==rise_expected,"no strobe holds previous sample ticks");
    $fclose(csv);
    if(errors!=0) $fatal(1,"FAIL: tacho errors=%0d checks=%0d",errors,checks);
    $display("PASS: all tacho_timestamp checks OK checks=%0d rising=%0d falling=%0d samples=%0d",checks,rn,fn,sample_n);
    $finish;
  end
  initial begin #50000; $fatal(1,"TIMEOUT: tacho testbench"); end
endmodule
