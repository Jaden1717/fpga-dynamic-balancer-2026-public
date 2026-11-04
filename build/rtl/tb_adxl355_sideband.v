`timescale 1ns/1ps
// Production 1 MHz SPI. Red mode exercises the existing AXI restart contract;
// full mode also validates the new external sideband interface.
module tb_adxl355_sideband;
    reg clk=0, rst_n=0;
    always #5 clk=~clk;
    reg [63:0] ticks=64'h1_ffff_f000;
    always @(posedge clk) if (rst_n) ticks <= ticks+1'b1;
    reg clear=0, drdy=0, miso=0;
    wire cs,sclk,mosi;
    reg [15:0] awaddr=0;
    reg awvalid=0,wvalid=0,bready=0;
    reg [31:0] wdata=0;
    wire awready,wready,bvalid;
`ifdef SIDEBAND_PORTS
    wire valid,idle;
    wire [31:0] frame,x,y,z,missed;
    wire [63:0] sample_ticks;
    reg allow_publication=0;
`endif
    adxl355_acq #(.CLK_HALF(50),.RAM_DEPTH(12),.DISCARD_DFLT(0)) dut (
        .clk(clk),.rst_n(rst_n),
        .S_AXI_AWADDR(awaddr),.S_AXI_AWPROT(3'd0),.S_AXI_AWVALID(awvalid),.S_AXI_AWREADY(awready),
        .S_AXI_WDATA(wdata),.S_AXI_WSTRB(4'hf),.S_AXI_WVALID(wvalid),.S_AXI_WREADY(wready),
        .S_AXI_BRESP(),.S_AXI_BVALID(bvalid),.S_AXI_BREADY(bready),
        .S_AXI_ARADDR(16'd0),.S_AXI_ARPROT(3'd0),.S_AXI_ARVALID(1'b0),.S_AXI_ARREADY(),
        .S_AXI_RDATA(),.S_AXI_RRESP(),.S_AXI_RVALID(),.S_AXI_RREADY(1'b0),
        .spi_cs_o(cs),.spi_sclk_o(sclk),.spi_mosi_o(mosi),.spi_miso_i(miso),.drdy_i(drdy)
`ifdef SIDEBAND_PORTS
        ,.capture_ticks_i(ticks),.capture_clear_i(clear),
        .sample_valid_o(valid),.sample_frame_o(frame),.sample_ticks_o(sample_ticks),
        .sample_x_o(x),.sample_y_o(y),.sample_z_o(z),
        .capture_idle_o(idle),.capture_missed_o(missed)
`endif
    );
    integer checks=0, published=0;
    task check(input cond,input [8*110-1:0] msg);
        begin
            checks=checks+1;
            if (cond !== 1'b1) $fatal(1,"FAIL: %0s at %0t",msg,$time);
        end
    endtask
    task write_reg(input [15:0] addr,input [31:0] data);
        integer timeout;
        begin
            @(negedge clk); awaddr=addr;wdata=data;awvalid=1;wvalid=1;bready=1;
            @(posedge clk); while (!(awready && wready)) @(posedge clk);
            @(negedge clk); awvalid=0;wvalid=0;
            timeout=0;
            while (!bvalid && timeout<100) begin @(negedge clk);timeout=timeout+1;end
            check(bvalid,"AXI write finishes");
            @(posedge clk); @(negedge clk);bready=0;
        end
    endtask
    task pulse_drdy;
        begin
            @(negedge clk);drdy=1;
            repeat(8) @(negedge clk);
            drdy=0;
        end
    endtask
    task epoch_clear;
        begin
            @(negedge clk);clear=1;
            @(negedge clk);clear=0;
        end
    endtask
    task await_spi;
        begin
            wait(!cs);wait(cs);repeat(12) @(negedge clk);
        end
    endtask
    // ADXL355 mode-0 slave; snapshot a complete 13-byte sample on CS fall.
    // Signed axis fixture is independent of the DUT's decode/receive registers.
    reg [103:0] payload;
    integer bitno=0, tag=0;
    integer latest_auto_tag=0;
    reg [63:0] latest_auto_ticks;
    time latest_auto_time;
`ifdef SIDEBAND_PORTS
    // External acceptance snapshots queue records independently of publication.
    // This deliberately permits a newer SPI to start before the previous valid.
    reg [63:0] expected_ticks [0:31];
    integer expected_tags [0:31];
    time expected_times [0:31];
    integer expected_in=0,expected_out=0;
    always @(posedge clk) if(clear || dut.restart) begin
        expected_in=0;expected_out=0;
    end
`endif
    reg [7:0] command;
    integer cmd_bits=0;
    function [23:0] axis_raw(input integer value);
        reg signed [19:0] v;
        begin v=value; axis_raw={v,4'd0};end
    endfunction
    always @(negedge cs) begin
        tag=tag+1;bitno=0;cmd_bits=0;command=0;miso=0;
        payload={8'h07,8'h60,8'h06,8'hea,axis_raw(-12340-tag),axis_raw(2000+tag),axis_raw(250000+tag)};
        if(dut.enable) begin
            // CS changes only at the accepted DRDY clock; counter increments in
            // that same NBA update, so its preceding value is the acceptance tick.
            latest_auto_ticks=ticks-1'b1;
            latest_auto_tag=tag;
            latest_auto_time=$time;
`ifdef SIDEBAND_PORTS
            if(allow_publication) begin
                expected_ticks[expected_in]=ticks-1'b1;
                expected_tags[expected_in]=tag;
                expected_times[expected_in]=$time;
                expected_in=expected_in+1;
            end
`endif
        end
    end
    always @(negedge sclk) if(!cs) begin
        bitno=bitno+1;
        if(bitno>=8 && bitno<112) miso=payload[111-bitno];
    end
    always @(posedge sclk) if(!cs && cmd_bits<8) begin
        command={command[6:0],mosi};cmd_bits=cmd_bits+1;
    end
`ifdef SIDEBAND_PORTS
    integer expected_frame=0;
    reg previous_valid=0;
    time latency;
    always @(negedge clk) if(rst_n) begin
        if(valid) begin
            check(!previous_valid,"sample valid is exactly one clock");
            check(allow_publication,"warmup/manual/canceled transactions never publish");
            check(frame===expected_frame,"frame ID restarts at zero and increments without gaps");
            check(expected_out<expected_in,"sample has an independently observed accepted DRDY");
            check(sample_ticks===expected_ticks[expected_out],"timestamp belongs to accepted DRDY, including high-word carry");
            check($signed(x)==-12340-expected_tags[expected_out],"complete signed X belongs to the accepted SPI frame");
            check($signed(y)==2000+expected_tags[expected_out],"complete signed Y belongs to the accepted SPI frame");
            check($signed(z)==250000+expected_tags[expected_out],"complete signed Z belongs to the accepted SPI frame");
            latency=$time-expected_times[expected_out];
            check(latency>112000 && latency<114000,"publication occurs after production SPI, not at DRDY");
            check(!idle,"idle stays false while outgoing sample valid is visible");
            expected_frame=expected_frame+1;published=published+1;expected_out=expected_out+1;
        end
        previous_valid=valid;
    end
`endif
    integer before_count;
    reg [63:0] ticks_before;
    initial begin
        repeat(10) @(negedge clk);rst_n=1;
        write_reg(16'h4000,1);
        // Behavioral RED: restarting while SPI is active must not let an old
        // completion reopen the new epoch's warmup gate.
        pulse_drdy();wait(!cs);repeat(50) @(negedge clk);
        write_reg(16'h4000,3);
        wait(cs);repeat(12) @(negedge clk);
        check(dut.data_valid===0,"restart during SPI suppresses pre-restart completion/warmup leakage");
        check(dut.sample_total===0,"restart during SPI leaves new epoch frame count zero");
`ifndef SIDEBAND_PORTS
        $display("PASS: restart epoch regression (%0d checks)",checks);$finish;
`else
        check(missed===0,"restart clears missed counter");
        check(idle,"idle returns after invalidated old SPI drains");
        // Warmup first completion is discarded by the legacy release gate.
        pulse_drdy();await_spi();
        check(published==0,"warmup completion emits no sample");
        allow_publication=1;
        pulse_drdy();await_spi();
        check(published==1,"first complete post-warmup frame is emitted");
        pulse_drdy();await_spi();
        check(published==2,"next frame is emitted");
        // Missed edges while busy count; disabled edges never do.
        pulse_drdy();wait(!cs);
        pulse_drdy();pulse_drdy();
        wait(cs);repeat(12) @(negedge clk);
        check(missed===2,"two enabled edges during SPI increment missed counter");
        // Disable in flight must drain the final complete frame before IDLE.
        pulse_drdy();wait(!cs);
        write_reg(16'h4000,0);
        check(!idle,"disable does not claim idle while SPI tail is pending");
        wait(idle);@(negedge clk);
        check(published==4,"idle cannot precede the final in-flight sample after disable");
        check(idle,"idle asserts only after final publication pulse drains");
        allow_publication=0;
        pulse_drdy();pulse_drdy();repeat(10) @(negedge clk);
        check(missed===2,"disabled edges do not increase capture missed counter");
        // Manual burst uses the same SPI engine but cannot emit a sample.
        before_count=published;
        write_reg(16'h4034,32'h80010009);await_spi();
        check(published==before_count,"manual burst does not emit sideband");
        // Start a second SPI at the first frame's X publication clock. A single
        // timestamp register would substitute the second acceptance tick for the
        // first published XYZ; the independent expected queue catches that bug.
        write_reg(16'h4000,1);allow_publication=1;
        before_count=published;
        pulse_drdy();wait(dut.st==3'd4);
        repeat(51) @(negedge clk);drdy=1;
        repeat(8) @(negedge clk);drdy=0;
        check(!cs,"new SPI accepted while previous record publication drains");
        wait(cs);repeat(12) @(negedge clk);
        check(published==before_count+2,"overlapping acceptance preserves both XYZ/timestamp records");
        check(missed===2,"overlapping idle acceptance does not increment missed count");
        // Clear while publishing kills all three-word publication state atomically.
        pulse_drdy();wait(dut.pub_run);
        @(negedge clk); clear=1;allow_publication=0;
        @(negedge clk);clear=0;expected_frame=0;
        repeat(12) @(negedge clk);
        check(dut.sample_total===0 && dut.slot===0,"clear resets sample/ring epoch during publication");
        check(!valid && missed===0,"clear suppresses publication and resets missed count");
        // Clear during SPI does not reset the common timebase, nor leak old data.
        ticks_before=ticks;
        pulse_drdy();wait(!cs);epoch_clear();wait(cs);repeat(12) @(negedge clk);
        check(ticks>ticks_before,"capture clear preserves external common timebase");
        check(dut.data_valid===0 && dut.sample_total===0,"clear invalidates in-flight SPI completion");
        pulse_drdy();await_spi(); // warmup release
        allow_publication=1;
        pulse_drdy();await_spi();
        check(expected_frame==1,"new epoch first frame ID is zero");
        // AXI restart during publication: align the commit at pub=0 by starting
        // the write shortly before SPI finishes (without forcing DUT signals).
        pulse_drdy();wait(dut.st==3'd4);
        // A_HOLD lasts 50 cycles; issue write when CS releases so the
        // registered restart is consumed while the publication is at X.
        repeat(50) @(negedge clk);
        write_reg(16'h4000,3);
        allow_publication=0;expected_frame=0;
        repeat(15) @(negedge clk);
        check(dut.sample_total===0 && !valid,"AXI restart cancels publication tail");
        pulse_drdy();await_spi();allow_publication=1;
        pulse_drdy();await_spi();
        check(expected_frame==1,"frame counter reopens at zero after AXI restart");
        write_reg(16'h4000,0);repeat(10) @(negedge clk);
        check(idle,"capture fully idle at end");
        $display("TIMING: sideband SPI CLK_HALF=50 100MHz; timestamp publication latency %0d ns",latency);
        $display("PASS: ADXL355 sideband %0d checks, %0d emitted frames",checks,published);
        $finish;
`endif
    end
    initial begin #10000000;$fatal(1,"TIMEOUT: sideband test");end
endmodule
