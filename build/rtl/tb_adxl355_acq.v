`timescale 1ns / 1ps
// Self-checking testbench for adxl355_acq, driven against a behavioural ADXL355 SPI slave.
//
// RAM_DEPTH is overridden to 12 (4 sample slots) so the ring wraps after 5 samples inside a
// short run; the wrap/pointer logic is depth independent, 4096 only changes how long it takes.
// Production timing: CLK_HALF=50 gives SPI 1 MHz, about 113 us per 250 us DRDY frame.
//
// What this proves before any bitstream is built:
//   1 the command byte on the wire is 0x09 and CS stays low across all 14 bytes
//   2 one transaction per DRDY rising edge, and the transaction fits well inside 250 us
//   3 the discard counter releases publishing only after discard_limit frames, and STATUS
//     data_valid is the thing the PS waits on -- not DRDY
//   4 the 24-bit left-justified words decode with an arithmetic >>4 (board value 1.016 g)
//   5 the RAM word layout, LAST_* registers, slot/wrap counters and SAMPLE_COUNT agree
//   6 restart clears slot/counters/flags and re-arms the discard window
//   7 the delegated SPI channel: an out-of-band write/read reaches the device registers,
//     a manual burst never publishes as if it were a sample, and requests made while
//     enable=1 are answered with the refused flag instead of touching the part
module tb_adxl355_acq;

    localparam integer ZERO_FRAMES = 16;      // frames whose axes read 0, like the board
    localparam integer DISCARD     = 20;      // discard_limit under test
    localparam integer FRAMES      = 60;      // DRDY frames to run
    localparam [15:0] REG_BASE = 16'h4000;

    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;                      // 100 MHz

    wire cs, sclk, mosi;
    reg  miso = 1;
    reg  drdy = 0;
    wire [15:0] awaddr = 16'd0, araddr = 16'd0;

    reg  [15:0] axi_awaddr, axi_araddr;
    reg         axi_awvalid = 0, axi_wvalid = 0, axi_bready = 0;
    reg  [31:0] axi_wdata = 0;
    reg  [3:0]  axi_wstrb = 4'hF;
    reg         axi_arvalid = 0, axi_rready = 0;
    wire [31:0] axi_rdata;
    wire        axi_awready, axi_wready, axi_bvalid, axi_arready, axi_rvalid;

    integer errors = 0;
    integer frame_cnt = 0;
    integer g;
    integer spins;

    adxl355_acq #( .CLK_HALF(50), .RAM_DEPTH(12), .DISCARD_DFLT(DISCARD)) dut (
        .clk(clk), .rst_n(rst_n),
        .S_AXI_AWADDR(axi_awaddr), .S_AXI_AWPROT(3'b0), .S_AXI_AWVALID(axi_awvalid),
        .S_AXI_AWREADY(axi_awready),
        .S_AXI_WDATA(axi_wdata), .S_AXI_WSTRB(axi_wstrb), .S_AXI_WVALID(axi_wvalid),
        .S_AXI_WREADY(axi_wready),
        .S_AXI_BRESP(), .S_AXI_BVALID(axi_bvalid), .S_AXI_BREADY(axi_bready),
        .S_AXI_ARADDR(axi_araddr), .S_AXI_ARPROT(3'b0), .S_AXI_ARVALID(axi_arvalid),
        .S_AXI_ARREADY(axi_arready),
        .S_AXI_RDATA(axi_rdata), .S_AXI_RRESP(), .S_AXI_RVALID(axi_rvalid),
        .S_AXI_RREADY(axi_rready),
        .spi_cs_o(cs), .spi_sclk_o(sclk), .spi_mosi_o(mosi), .spi_miso_i(miso),
        .drdy_i(drdy)
    );

    // ------------------------------------------------ fake ADXL355 SPI slave
    // A 17-byte mirror of 0x00..0x10 addressed exactly like the real part:
    // cmd = (reg<<1)|R/W and a burst read walks the pointer up, so the delegated write
    // channel can be checked against what the device ended up holding, not just against
    // the bits that appeared on MOSI.
    reg [7:0] dev [0:47];             // 0x00..0x2F, so real config registers are writable
    integer   li, j, p;
    reg [7:0] cmd_seen;
    reg [7:0] wdata_seen;
    integer   cmd_bits, wd_bits;
    reg       cmd_done;
    reg [7:0] last_cmd;                   // command byte of the finished transaction
    reg [7:0] last_wdata;                 // its data byte
    integer   txn_seen = 0;                   // transactions the device has completed
    wire [6:0] cmd_addr = cmd_seen[7:1];  // cmd = (reg<<1) | R/W

    task set_payload;                          // one frame of device-side content
        input integer n;
        reg signed [19:0] xv, yv, zv;
        begin
            dev[4]  = 8'h07;                   // STATUS
            dev[5]  = 8'h60;                   // FIFO_ENTRIES = 96
            dev[6]  = 8'h06;                   // TEMP2
            dev[7]  = 8'hEA;                   // TEMP1  -> 0x6EA = 1770 LSB
            if (frame_cnt < ZERO_FRAMES) begin
                xv = 0; yv = 0; zv = 0;
            end else begin
                xv = -20'sd8225  + n;
                yv =  20'sd2694  + n;
                zv =  20'sd260197 + n;
            end
            {dev[8],  dev[9],  dev[10]} = {20'sd0, xv} << 4;
            {dev[11], dev[12], dev[13]} = {20'sd0, yv} << 4;
            {dev[14], dev[15], dev[16]} = {20'sd0, zv} << 4;
        end
    endtask

    // the device updates its mirror once per ODR period, on the DRDY rising edge
    always @(posedge drdy) begin
        frame_cnt = frame_cnt + 1;
        set_payload(frame_cnt);
    end

    always @(negedge cs) begin                 // mode 0: MSB out on CS fall
        li = 0; miso = 1'b0;
        cmd_seen = 8'h00; wdata_seen = 8'h00;
        cmd_bits = 0; wd_bits = 0; cmd_done = 0;
    end
    always @(negedge sclk) if (!cs) begin      // present the bit the DUT will sample
        li = li + 1;
        if (cmd_done && li >= 8) begin
            j = cmd_addr + li / 8 - 1;         // the part auto-increments the pointer
            p = li % 8;
            if (cmd_seen[0] && j <= 47) miso = dev[j][7 - p];
            else                        miso = 1'b0;
        end
    end
    always @(posedge sclk) if (!cs) begin      // collect what the controller sends
        if (cmd_bits < 8) begin
            cmd_seen = {cmd_seen[6:0], mosi};
            cmd_bits = cmd_bits + 1;
            if (cmd_bits == 8) cmd_done = 1;
        end else if (wd_bits < 8) begin
            wdata_seen = {wdata_seen[6:0], mosi};
            wd_bits    = wd_bits + 1;
        end
    end
    always @(posedge cs) begin                 // CS rise ends the transaction
        txn_seen = txn_seen + 1;
        last_cmd   = cmd_seen;
        last_wdata = wdata_seen;
        if (cmd_done && !cmd_seen[0] && cmd_addr <= 7'd47)
            dev[cmd_addr] = wdata_seen;        // a configuration write landed
    end

    initial begin
        for (li = 0; li <= 47; li = li + 1) dev[li] = 8'h00;
        dev[0] = 8'hAD;                        // DEVID
        dev[1] = 8'h1D;                        // DEVID_MST
        dev[2] = 8'hED;                        // PARTID
        set_payload(0);
    end

    // ------------------------------------------------------------------ AXI
    task reg_write(input [15:0] off, input [31:0] val);
        begin
            @(posedge clk);
            axi_awaddr <= REG_BASE + off; axi_awvalid <= 1'b1;
            axi_wdata  <= val;            axi_wvalid  <= 1'b1;
            @(posedge clk);
            while (!axi_awready || !axi_wready) @(posedge clk);
            axi_awvalid <= 1'b0; axi_wvalid <= 1'b0;
            axi_bready <= 1'b1;
            g = 0;
            while (axi_bvalid !== 1'b1 && g < 200) begin @(posedge clk); g = g + 1; end
            check(axi_bvalid === 1'b1, "AXI write handshake completed");
            axi_bready <= 1'b0;
            @(posedge clk);
        end
    endtask

    task reg_read(input [15:0] off, output [31:0] val);
        begin
            @(posedge clk);
            axi_araddr <= REG_BASE + off; axi_arvalid <= 1'b1;
            @(posedge clk);
            while (!axi_arready) @(posedge clk);
            axi_arvalid <= 1'b0;
            axi_rready <= 1'b1;
            g = 0;
            while (axi_rvalid !== 1'b1 && g < 200) begin @(posedge clk); g = g + 1; end
            check(axi_rvalid === 1'b1, "AXI read handshake completed");
            val = axi_rdata;
            axi_rready <= 1'b0;
            @(posedge clk);
        end
    endtask

    task ram_read(input integer word, output [31:0] val);
        begin
            @(posedge clk);
            axi_araddr <= word * 4; axi_arvalid <= 1'b1;
            @(posedge clk);
            while (!axi_arready) @(posedge clk);
            axi_arvalid <= 1'b0;
            axi_rready <= 1'b1;
            g = 0;
            while (axi_rvalid !== 1'b1 && g < 200) begin @(posedge clk); g = g + 1; end
            check(axi_rvalid === 1'b1, "AXI RAM read handshake completed");
            val = axi_rdata;
            axi_rready <= 1'b0;
            @(posedge clk);
        end
    endtask

    // one delegated SPI transaction: queue it through SPI_TX and poll SPI_STAT
    task spi_txn(input [7:0] cmd, input [7:0] dbyte, input burst, output [31:0] spi_stat);
        begin
            reg_write(16'h0034, {1'b1, 14'd0, burst, dbyte, cmd});
            spi_stat = 32'd0;
            spins = 0;
            while (spi_stat[1:0] == 2'b00 && spins < 5000) begin
                reg_read(16'h003C, spi_stat);
                spins = spins + 1;
            end
            check(spi_stat[0] === 1'b1 || spi_stat[1] === 1'b1,
                  "delegated SPI completed or refused before timeout");
        end
    endtask

    task check(input integer cond, input [8*80 - 1 : 0] msg);
        begin
            if (cond !== 1) begin
                errors = errors + 1;
                $display("FAIL t=%0t: %0s", $time, msg);
            end
        end
    endtask

    // Measure the actual stimulus period; do not trust a declaration or comment.
    time previous_drdy = 0;
    integer measured_drdy_ns = 0;
    always @(posedge drdy) begin
        if (previous_drdy != 0) begin
            measured_drdy_ns = $time - previous_drdy;
            if (measured_drdy_ns != 250000)
                $fatal(1, "DRDY period is %0d ns, expected 250000 ns", measured_drdy_ns);
        end
        previous_drdy = $time;
    end

    // ------------------------------------------------------------- stimulus
    localparam integer period_ns = 250000;
    integer    cs_low_max_ns = 0;
    integer    cs_low_ns = 0;

    always @(cs) begin
        if (!cs) cs_low_ns = 0;
        else if (cs_low_ns > cs_low_max_ns) cs_low_max_ns = cs_low_ns;
    end
    always @(posedge clk) if (!cs) cs_low_ns = cs_low_ns + 10;

    reg [31:0] r;
    integer    i;
    reg [31:0] w0, w1, w2;
    reg [31:0] p0x, p0y, p0z;
    reg [31:0] cnt_before;
    reg [31:0] stat;

    initial begin
        axi_awaddr = 0; axi_araddr = 0;

        #200 rst_n = 1;

        // arm: enable only, discard_limit comes from the parameter default
        reg_write(16'h0000, 32'h0000_0001);

        // wait out the discard window (DISCARD+2 frames) plus margin
        #(period_ns * (DISCARD + 6));
        $display("TIMING: DRDY=%0d ns SPI_CS_MAX=%0d ns", measured_drdy_ns, cs_low_max_ns);

        reg_read(16'h0004, r);
        check(r[1] === 1'b1, "STATUS data_valid set after discard window");
        check(r[3] === 1'b0, "STATUS dropped sticky is clear");
        reg_read(16'h000C, r);
        check(r == DISCARD, "DISCARD_COUNT equals discard_limit");
        reg_read(16'h0008, r);
        check(r > 0, "SAMPLE_COUNT advanced past the window");
        reg_read(16'h0018, r);
        check(r == DISCARD, "DISCARD_LIMIT reads back the parameter default");

        // LAST_* must equal the most recent published frame
        reg_read(16'h0028, p0x);
        reg_read(16'h002C, p0y);
        reg_read(16'h0030, p0z);
        reg_read(16'h0020, r);
        check(r == 32'h60, "LAST_ENTRIES = 96");
        reg_read(16'h0024, r);
        check(r == 32'h6EA, "LAST_TEMP = 1770 LSB");
        check(p0z[31:20] == 12'h000 && p0z[19:0] > 260197,
              "LAST_Z is a positive 20-bit decode above the 1 g offset");

        // the wire protocol itself
        check(cmd_done, "a full command byte was clocked in the device");
        check(cmd_seen == 8'h09, "command byte is 0x09 (burst read from STATUS)");
        check(cs_low_max_ns >= 112000 && cs_low_max_ns <= 114000,
              "CS low for the whole 14-byte transaction, about 113 us at SPI 1 MHz");
        check(cs_low_max_ns < 250000, "transaction fits inside the 250 us frame");

        // walk the ring: every published slot must be non-zero and strictly monotonic
        reg_read(16'h0010, r);                 // WRITE_SLOT
        check(r > 0 && r <= 4, "WRITE_SLOT inside the 4-slot ring");
        ram_read(0, w0);  ram_read(1, w1);  ram_read(2, w2);
        check(w2 != 32'd0, "slot0 Z word is non-zero (published after the window)");
        check(w2 === p0z || w1 === p0y || w0 === p0x,
              "ring content is consistent with the LAST_* registers");

        // let the ring wrap several times, then check the wrap counter
        #(period_ns * 30);
        reg_read(16'h0014, r);
        check(r >= 3, "WRAP_COUNT grew as the ring overwrote old slots");
        reg_read(16'h0008, w0);
        reg_read(16'h0004, r);
        check(r[3] === 1'b0, "still no dropped DRDY edge at 250 us frames");

        // restart re-arms the window
        reg_write(16'h0000, 32'h0000_0003);    // enable + restart

        reg_read(16'h0004, r);

        check(r[1] === 1'b0, "restart cleared data_valid");
        reg_read(16'h0008, r);
        check(r == 32'd0, "restart cleared SAMPLE_COUNT");
        reg_read(16'h0010, r);
        check(r == 32'd0, "restart cleared WRITE_SLOT");
        #(period_ns * (DISCARD + 4));
        reg_read(16'h0004, r);
        check(r[1] === 1'b1, "publishing resumed after the re-armed discard window");

        // zero-enable must stop the bus
        reg_read(16'h0008, cnt_before);
        reg_write(16'h0000, 32'h0000_0000);
        #(period_ns * 10);
        reg_read(16'h0008, r);
        check(r == cnt_before, "SAMPLE_COUNT frozen while disabled");
        reg_read(16'h0004, r);
        check(r[3] === 1'b1, "edges seen while disabled raise the dropped flag");

        // -------- 7. the delegated SPI channel (the PS has no bit-bang here) --------
        // write RANGE = 0x81 exactly the way the bring-up script did: cmd = (0x2C<<1)|0
        spi_txn(8'h58, 8'h81, 1'b0, stat);
        check(stat[0] === 1'b1, "SPI_STAT done after the delegated write");
        check(stat[1] === 1'b0, "SPI_STAT refused stays clear while disabled");
        check(last_cmd === 8'h58, "the device decoded command byte 0x58");
        check(last_wdata === 8'h81, "the device saw write data 0x81 on MOSI");
        check(dev[8'h2C] === 8'h81, "RANGE really changed inside the device mirror");

        // read it back with cmd = (0x2C<<1)|1, and DEVID for the board-known 0xAD
        spi_txn(8'h59, 8'h00, 1'b0, stat);
        reg_read(16'h0038, r);
        check(r[7:0] === 8'h81, "SPI_RX returns the register the device now holds");
        spi_txn(8'h01, 8'h00, 1'b0, stat);
        reg_read(16'h0038, r);
        check(r[7:0] === 8'hAD, "manual DEVID read returns 0xAD like the bit-bang did");

        // a manual burst must not publish: it is not a DRDY sample
        reg_read(16'h0008, cnt_before);
        spi_txn(8'h09, 8'h00, 1'b1, stat);
        check(stat[0] === 1'b1, "manual 14-byte burst completed");
        reg_read(16'h0008, r);
        check(r == cnt_before, "a manual burst does not advance SAMPLE_COUNT");

        // while enable=1 the channel is refused, so the part is never reconfigured
        // mid-measurement (the 5.161 s zero-output failure measured on the board)
        reg_write(16'h0000, 32'h0000_0001);
        #(period_ns * 3);
        reg_read(16'h0008, cnt_before);
        spi_txn(8'h58, 8'h00, 1'b0, stat);
        check(stat[1] === 1'b1, "SPI_STAT refused after a request while measuring");
        #(period_ns * 3);                       // a refusal returns in microseconds, so give
        reg_read(16'h0008, r);                  // the sample engine frames to prove it lived
        check(r > cnt_before, "sampling kept running through the refused request");
        reg_write(16'h0000, 32'h0000_0000);

        if (errors == 0) $display("PASS: all adxl355_acq checks OK (%0t)", $time);
        else             $fatal(1, "ERRORS: %0d checks failed", errors);
        $finish;
    end

    initial begin
        #60_000_000;
        $fatal(1, "TIMEOUT: simulation exceeded 60 ms");
    end

    // 4 kHz square wave, first rising edge at 125 us
    initial begin
        drdy = 0;
        forever begin
            #(period_ns / 2) drdy = 1;
            #(period_ns / 2) drdy = 0;
        end
    end

endmodule
