// ADXL355 acquisition controller: DRDY-triggered SPI master, 20-bit decode,
// warm-up frame discard, BRAM sample ring, AXI-Lite register + RAM windows.
//
// Board evidence this encodes (硬件-ADXL355振动采集知识文档.md):
//   DRDY = ja_i[3] (W19) is a 4031 Hz / 49.4% duty square wave, and it already ticks while
//     0x08..0x10 are still zero -> the edge is the sample clock, never the start gate.
//   Burst read: cmd 0x09 = (0x04<<1)|1, then 13 bytes = STATUS, FIFO_ENTRIES, TEMP2/1, X2..0,
//     Y2..0, Z2..0. No FIFO popping, no FIFO_OVR clearing (adxl355_stall.py: 24/24 distinct).
//   The datapath is released <=216 ms (<=866 frames) after leaving Standby -> discard counter.
//   T_s = 1/4000 s = 250 us; 14 bytes at 1 MHz SCLK = 112 us -> 45% duty on the SPI bus.
//
// AXI-Lite map, 32 KB address span:
//   0x0000 + 4*(3*slot + {0,1,2})  sample RAM, slot in 0..NSAMP-1, NSAMP = RAM_DEPTH/3
//   0x4000 CTRL        write bit0 enable, bit1 restart (restart is a one-cycle pulse)
//   0x4004 STATUS      bit0 busy bit1 data_valid bit2 drdy_level bit3 dropped(sticky)
//   0x4008 SAMPLE_COUNT      0x400C DISCARD_COUNT   0x4010 WRITE_SLOT
//   0x4014 WRAP_COUNT  0x4018 DISCARD_LIMIT (reset 2000)
//   0x401C LAST_STATUS 0x4020 LAST_ENTRIES 0x4024 LAST_TEMP
//   0x4028 LAST_X 0x402C LAST_Y 0x4030 LAST_Z   (20-bit two's complement, sign extended)
//   0x4034 SPI_TX      write {bit31 start, bit16 burst, bit[15:8] wdata, bit[7:0] cmd}
//   0x4038 SPI_RX      read  {24'd0, last manual-read byte}
//   0x403C SPI_STAT    read  {30'd0, refused(sticky), done(sticky)}
//
// SPI_TX exists because this module is the only SPI master once it owns the JA pins:
// the PS can no longer bit-bang, so every configuration write (soft reset, FILTER,
// POWER_CTL) has to be delegated through it. The command byte is passed through
// literally as (reg<<1)|R/W so the RTL never has to guess the burst layout; a manual
// transaction is only accepted while enable=0, because writing ADXL355 registers while
// the part is measuring is the 5.161 s zero-output failure already measured on the board
// (see 硬件-ADXL355振动采集知识文档.md) - a dropped request raises SPI_STAT[1].
// RAM and the sample/last registers are cleared only by restart, not by disabling.
`default_nettype none

module adxl355_acq #(
    parameter integer CLK_HALF     = 50,     // 100 MHz / (2*CLK_HALF) = SCLK
    parameter integer RAM_DEPTH    = 4096,   // 32-bit words, multiple of 3 in use
    parameter integer DISCARD_DFLT = 2000
) (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [15:0] S_AXI_AWADDR,
    input  wire [2:0]  S_AXI_AWPROT,
    input  wire        S_AXI_AWVALID,
    output wire        S_AXI_AWREADY,
    input  wire [31:0] S_AXI_WDATA,
    input  wire [3:0]  S_AXI_WSTRB,
    input  wire        S_AXI_WVALID,
    output wire        S_AXI_WREADY,
    output wire [1:0]  S_AXI_BRESP,
    output wire        S_AXI_BVALID,
    input  wire        S_AXI_BREADY,
    input  wire [15:0] S_AXI_ARADDR,
    input  wire [2:0]  S_AXI_ARPROT,
    input  wire        S_AXI_ARVALID,
    output wire        S_AXI_ARREADY,
    output wire [31:0] S_AXI_RDATA,
    output wire [1:0]  S_AXI_RRESP,
    output wire        S_AXI_RVALID,
    input  wire        S_AXI_RREADY,

    output reg         spi_cs_o,
    output reg         spi_sclk_o,
    output reg         spi_mosi_o,
    input  wire        spi_miso_i,
    input  wire        drdy_i,

    // Optional synchronous-capture stream. All outputs use clk, no backpressure:
    // the downstream record FIFO must accept valid or report its own overflow.
    input  wire [63:0] capture_ticks_i,
    input  wire        capture_clear_i,
    output reg         sample_valid_o,
    output reg  [31:0] sample_frame_o,
    output reg  [63:0] sample_ticks_o,
    output reg  [31:0] sample_x_o,
    output reg  [31:0] sample_y_o,
    output reg  [31:0] sample_z_o,
    output wire        capture_idle_o,
    output reg  [31:0] capture_missed_o
);

    localparam [7:0]   CMD_BURST = 8'h09;    // read burst starting at STATUS
    localparam integer NSAMP     = RAM_DEPTH / 3;
    localparam integer PW        = $clog2(NSAMP);

    localparam [2:0] A_IDLE = 3'd0, A_SETUP = 3'd1, A_HIGH = 3'd2,
                     A_LOW  = 3'd3, A_HOLD  = 3'd4, A_END  = 3'd5;

    wire [15:0] hc_last = CLK_HALF - 1;

    // ------------------------------------------------------------------- DRDY
    reg [2:0] drdy_r;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) drdy_r <= 3'b111;
        else        drdy_r <= {drdy_r[1:0], drdy_i};
    end
    wire drdy_edge = drdy_r[1] & ~drdy_r[2];

    reg        enable;
    reg        restart;
    reg [31:0] discard_limit;
    // Case equality keeps omitted legacy simulation inputs benign. Hardware
    // integration explicitly ties capture_clear_i low for the legacy overlay.
    wire capture_epoch_clear = restart || (capture_clear_i === 1'b1);

    // --------------------------------------------------- SPI master (mode 0)
    // Manual transactions (delegated configuration writes and single-byte reads) are
    // accepted only while enable=0, so the sample burst and the PS never share CS.
    reg [7:0]   cmd_byte;       // command byte of the running transaction
    reg [6:0]   bits_last;      // index of the last bit of the running transaction
    reg         auto_txn;       // this transaction is a DRDY sample burst
    reg         spi_done;       //   ... completed  (sticky, cleared by the next accept)
    reg         spi_refused;    //   ... dropped    (sticky, cleared by the next accept)
    reg         spi_start;      // one-cycle pulse from the SPI_TX write
    reg [7:0]   manual_rd;      // byte returned by the last manual read
    // request payload, latched with the pulse by the AXI write
    reg [7:0]   spi_cmd;
    reg [7:0]   spi_wdata;
    reg         spi_burst;

    reg [2:0]   st;
    reg [15:0]  hc;
    reg [6:0]   bit_idx;
    reg [15:0]  tx_sh;          // remaining TX bits, MSB first, padded with zeros
    reg [103:0] rx;
    reg [103:0] rx_lat;
    reg         busy;
    reg         auto_done;
    reg         txn_capture_valid;
    reg [63:0]  txn_ticks;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st <= A_IDLE; hc <= 0; bit_idx <= 0; rx <= 0; rx_lat <= 0; tx_sh <= 16'h0000;
            spi_cs_o <= 1'b1; spi_sclk_o <= 1'b0; spi_mosi_o <= 1'b1;
            busy <= 1'b0; auto_done <= 1'b0;
            txn_capture_valid <= 1'b0; txn_ticks <= 64'd0;
            cmd_byte <= 8'd0; bits_last <= 7'd0; auto_txn <= 1'b0;
            spi_done <= 1'b0; spi_refused <= 1'b0; manual_rd <= 8'd0;
        end else begin
            auto_done <= 1'b0;
            // a request that cannot be served right now is answered, not queued: the PS
            // polls SPI_STAT, and silently dropping it would look like a hang
            if (spi_start && (enable || st != A_IDLE)) begin
                spi_done    <= 1'b0;
                spi_refused <= 1'b1;
            end
            hc <= (hc == hc_last) ? 16'd0 : hc + 16'd1;
            case (st)
                A_IDLE: begin
                    hc         <= 0;
                    spi_cs_o   <= 1'b1;
                    spi_sclk_o <= 1'b0;
                    busy       <= 1'b0;
                    if (drdy_edge && enable && !capture_epoch_clear) begin
                        // Accepted synchronized DRDY, before the 112.5 us SPI
                        // transfer. Never timestamp at burst completion.
                        txn_ticks <= capture_ticks_i;
                        txn_capture_valid <= 1'b1;
                        st         <= A_SETUP;
                        busy       <= 1'b1;
                        spi_cs_o   <= 1'b0;
                        cmd_byte   <= CMD_BURST;
                        tx_sh      <= {CMD_BURST, 8'h00};
                        spi_mosi_o <= CMD_BURST[7];
                        bits_last  <= 7'd111;              // 14 bytes
                        auto_txn   <= 1'b1;
                        bit_idx    <= 7'd0;
                        rx         <= 104'd0;
                    end else if (spi_start && !enable && !capture_epoch_clear) begin
                        txn_capture_valid <= 1'b0;
                        st         <= A_SETUP;
                        busy       <= 1'b1;
                        spi_cs_o   <= 1'b0;
                        cmd_byte   <= spi_cmd;
                        tx_sh      <= {spi_cmd, spi_burst ? 8'h00 : spi_wdata};
                        spi_mosi_o <= spi_cmd[7];
                        bits_last  <= spi_burst ? 7'd111 : 7'd15;   // 14 or 2 bytes
                        auto_txn   <= 1'b0;
                        bit_idx    <= 7'd0;
                        rx         <= 104'd0;
                        spi_done   <= 1'b0;
                        spi_refused<= 1'b0;
                    end
                end
                A_SETUP: if (hc == hc_last) begin      // tCSS: CS low, MOSI = cmd[7]
                    hc <= 0;
                    st <= A_HIGH;
                    spi_sclk_o <= 1'b1;
                end
                A_HIGH: if (hc == hc_last) begin       // falling edge of this bit
                    hc         <= 0;
                    spi_sclk_o <= 1'b0;
                    if (bit_idx == bits_last) begin
                        rx_lat <= rx;
                        st     <= A_HOLD;
                    end else begin
                        bit_idx    <= bit_idx + 7'd1;
                        tx_sh      <= {tx_sh[14:0], 1'b0};
                        spi_mosi_o <= tx_sh[14];       // the bit this shift moves into [15]
                        st         <= A_LOW;
                    end
                end
                A_LOW: if (hc == hc_last) begin        // rising edge: capture this bit
                    hc <= 0;
                    if (bit_idx >= 8) rx <= {rx[102:0], spi_miso_i};
                    spi_sclk_o <= 1'b1;
                    st         <= A_HIGH;
                end
                A_HOLD: if (hc == hc_last) begin       // tCSH with SCLK low
                    hc       <= 0;
                    spi_cs_o <= 1'b1;
                    st       <= A_END;
                end
                A_END: begin
                    busy      <= 1'b0;
                    auto_done <= auto_txn && txn_capture_valid;
                    if (!auto_txn) begin
                        spi_done  <= 1'b1;
                        if (cmd_byte[0]) manual_rd <= rx[7:0];  // read: keep the returned byte
                    end
                    st       <= A_IDLE;
                end
                default: st <= A_IDLE;
            endcase
            // Finish a running physical SPI transfer, but its old epoch must
            // never release warmup or publish into a newly cleared capture.
            if (capture_epoch_clear) begin
                txn_capture_valid <= 1'b0;
                auto_done <= 1'b0;
            end
        end
    end

    // ------------------------------------------------------------- decode
    wire [7:0]      d_status  = rx_lat[103:96];
    wire [7:0]      d_entries = rx_lat[95:88];
    wire [11:0]     d_temp    = {rx_lat[83:80], rx_lat[79:72]};
    wire signed [23:0] raw_x  = rx_lat[71:48];
    wire signed [23:0] raw_y  = rx_lat[47:24];
    wire signed [23:0] raw_z  = rx_lat[23:0];
    wire signed [19:0] dec_x  = raw_x >>> 4;
    wire signed [19:0] dec_y  = raw_y >>> 4;
    wire signed [19:0] dec_z  = raw_z >>> 4;
    wire [31:0] word_x = {{12{dec_x[19]}}, dec_x};
    wire [31:0] word_y = {{12{dec_y[19]}}, dec_y};
    wire [31:0] word_z = {{12{dec_z[19]}}, dec_z};

    // ---------------------------------------------- warm-up discard / publish
    reg  [31:0] disc_cnt;
    reg  [31:0] sample_total;
    reg         data_valid;
    reg         dropped;
    reg         pub_run;
    reg  [1:0]  pub;
    reg         pub_capture_valid;
    reg  [63:0] pub_ticks;
    reg  [31:0] pub_x, pub_y, pub_z;
    reg  [31:0] pub_status, pub_entries, pub_temp;

    // Includes the outgoing valid cycle: software may stop pulse capture only
    // once the last sample can already have been accepted by the next FIFO.
    assign capture_idle_o = rst_n && (st == A_IDLE) && !auto_done &&
                            !pub_run && !sample_valid_o;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            disc_cnt <= 0; data_valid <= 1'b0; sample_total <= 0;
            pub_run <= 1'b0; pub <= 2'd0;
            pub_capture_valid <= 1'b0; pub_ticks <= 64'd0;
            pub_x <= 0; pub_y <= 0; pub_z <= 0;
            pub_status <= 0; pub_entries <= 0; pub_temp <= 0;
            sample_valid_o <= 1'b0; sample_frame_o <= 0; sample_ticks_o <= 0;
            sample_x_o <= 0; sample_y_o <= 0; sample_z_o <= 0;
        end else if (capture_epoch_clear) begin
            disc_cnt <= 0; data_valid <= 1'b0; sample_total <= 0;
            pub_run <= 1'b0; pub <= 2'd0;
            pub_capture_valid <= 1'b0; pub_ticks <= 64'd0;
            pub_x <= 0; pub_y <= 0; pub_z <= 0;
            pub_status <= 0; pub_entries <= 0; pub_temp <= 0;
            sample_valid_o <= 1'b0; sample_frame_o <= 0; sample_ticks_o <= 0;
            sample_x_o <= 0; sample_y_o <= 0; sample_z_o <= 0;
        end else begin
            sample_valid_o <= 1'b0;
            if (pub_run) begin
                pub <= (pub == 2'd2) ? 2'd0 : pub + 2'd1;
                if (pub == 2'd2) begin
                    pub_run      <= 1'b0;
                    sample_total <= sample_total + 32'd1;
                    pub_capture_valid <= 1'b0;
                    if (pub_capture_valid) begin
                        sample_valid_o <= 1'b1;
                        sample_frame_o <= sample_total;
                        sample_ticks_o <= pub_ticks;
                        sample_x_o <= pub_x;
                        sample_y_o <= pub_y;
                        sample_z_o <= pub_z;
                    end
                end
            end
            if (auto_done) begin
                if (!data_valid) begin
                    if (disc_cnt >= discard_limit) data_valid <= 1'b1;
                    else                           disc_cnt   <= disc_cnt + 32'd1;
                end else if (!pub_run) begin
                    pub_run <= 1'b1;
                    pub_capture_valid <= 1'b1;
                    // Separate publication snapshot: accepting a subsequent SPI
                    // transaction must not replace the record being committed.
                    pub_ticks <= txn_ticks;
                    pub_x <= word_x; pub_y <= word_y; pub_z <= word_z;
                    pub_status <= {24'd0, d_status};
                    pub_entries <= {24'd0, d_entries};
                    pub_temp <= {20'd0, d_temp};
                end
            end
        end
    end

    // a DRDY edge we could not service (disabled, or mid transaction)
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)  dropped <= 1'b0;
        else if (capture_epoch_clear) dropped <= 1'b0;
        else if (drdy_edge && (!enable || busy)) dropped <= 1'b1;
    end

    // New capture diagnostics count only enabled edges that cannot start SPI.
    // The historical STATUS dropped bit intentionally keeps its legacy meaning.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) capture_missed_o <= 0;
        else if (capture_epoch_clear) capture_missed_o <= 0;
        else if (drdy_edge && enable && st != A_IDLE)
            capture_missed_o <= capture_missed_o + 32'd1;
    end

    // ------------------------------------------------------------- sample RAM
    reg [PW-1:0] slot;
    reg [31:0]   wrap_cnt;
    reg [31:0]   ram [0:RAM_DEPTH-1];
    reg [31:0]   ram_q;
    wire [15:0]  rd_word;   // driven by the assign below (xvlog: concurrent assign to a non-net)

    wire [15:0] wr_word = slot * 16'd3 + pub;
    wire        ram_we  = pub_run && !capture_epoch_clear;

    always @(posedge clk) begin
        if (ram_we) ram[wr_word] <= (pub == 2'd0) ? pub_x
                        : (pub == 2'd1) ? pub_y : pub_z;
        ram_q <= ram[rd_word];
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            slot <= 0; wrap_cnt <= 0;
        end else if (capture_epoch_clear) begin
            slot <= 0; wrap_cnt <= 0;
        end else if (pub_run && pub == 2'd2) begin
            if (slot == NSAMP - 1) begin
                slot     <= 0;
                wrap_cnt <= wrap_cnt + 32'd1;
            end else begin
                slot <= slot + 1'b1;
            end
        end
    end

    reg [31:0] last_status_r, last_entries_r, last_temp_r, last_x_r, last_y_r, last_z_r;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            last_status_r <= 0; last_entries_r <= 0; last_temp_r <= 0;
            last_x_r <= 0; last_y_r <= 0; last_z_r <= 0;
        end else if (capture_epoch_clear) begin
            last_status_r <= 0; last_entries_r <= 0; last_temp_r <= 0;
            last_x_r <= 0; last_y_r <= 0; last_z_r <= 0;
        end else if (pub_run && pub == 2'd2) begin
            // latched, not read live from rx_lat: a manual transaction reuses rx_lat and
            // must not disturb what the last published sample reported
            last_status_r  <= pub_status;
            last_entries_r <= pub_entries;
            last_temp_r    <= pub_temp;
            last_x_r <= pub_x;
            last_y_r <= pub_y;
            last_z_r <= pub_z;
        end
    end

    // ------------------------------------------------------------- AXI Lite
    reg         aw_pend, w_pend, bvalid;
    reg  [15:0] aw_addr;
    reg  [31:0] w_data;
    reg  [3:0]  w_strb;
    reg  [31:0] r_data;
    reg  [1:0]  rd_st;
    reg  [15:0] ar_addr;

    // One outstanding write response; accept address/data independently.
    assign S_AXI_AWREADY = rst_n && !aw_pend && !bvalid;
    assign S_AXI_WREADY  = rst_n && !w_pend && !bvalid;
    assign S_AXI_BRESP   = 2'b00;
    assign S_AXI_BVALID  = bvalid;   // was missing: synthesis pruned bvalid as undriven
    assign S_AXI_RRESP   = 2'b00;

    wire do_write = aw_pend && w_pend && !bvalid;
    wire [31:0] wdata_be = {
        w_strb[3] ? w_data[31:24] : 8'd0,
        w_strb[2] ? w_data[23:16] : 8'd0,
        w_strb[1] ? w_data[15:8]  : 8'd0,
        w_strb[0] ? w_data[7:0]   : 8'd0
    };

    wire [31:0] write_mask = {{8{w_strb[3]}}, {8{w_strb[2]}},
                              {8{w_strb[1]}}, {8{w_strb[0]}}};

    // register space: addr[14] = 1, index = addr[5:2]; RAM space: addr[14] = 0
    wire [3:0] widx = aw_addr[5:2];
    wire [3:0] ridx = ar_addr[5:2];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_pend <= 1'b0; w_pend <= 1'b0; bvalid <= 1'b0;
            aw_addr <= 16'd0; w_data <= 32'd0; w_strb <= 4'd0; enable <= 1'b0; discard_limit <= DISCARD_DFLT;
            spi_cmd <= 8'd0; spi_wdata <= 8'd0; spi_burst <= 1'b0;
        end else begin
            if (S_AXI_AWVALID && S_AXI_AWREADY) begin
                aw_addr <= S_AXI_AWADDR;
                aw_pend <= 1'b1;
            end else if (do_write) begin
                aw_pend <= 1'b0;
            end
            if (S_AXI_WVALID && S_AXI_WREADY) begin
                w_data <= S_AXI_WDATA;
                w_strb <= S_AXI_WSTRB;
                w_pend <= 1'b1;
            end else if (do_write) begin
                w_pend <= 1'b0;
            end
            if (do_write) begin
                bvalid <= 1'b1;
                if (aw_addr[14] && widx == 4'h0 && w_strb[0]) enable        <= wdata_be[0];
                if (aw_addr[14] && widx == 4'h6) discard_limit <= (discard_limit & ~write_mask) | wdata_be;
                if (aw_addr[14] && widx == 4'hD) begin
                    if (w_strb[0]) spi_cmd   <= wdata_be[7:0];
                    if (w_strb[1]) spi_wdata <= wdata_be[15:8];
                    if (w_strb[2]) spi_burst <= wdata_be[16];
                end
            end else if (bvalid && S_AXI_BREADY) begin
                bvalid <= 1'b0;
            end
        end
    end

    // a manual SPI request is a one-cycle pulse taken from the SPI_TX start bit
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) spi_start <= 1'b0;
        else spi_start <= (do_write && aw_addr[14] && widx == 4'hD && wdata_be[31]) ? 1'b1 : 1'b0;
    end

    // restart is a one-cycle pulse taken from the CTRL write
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) restart <= 1'b0;
        else restart <= (do_write && aw_addr[14] && widx == 4'h0 && wdata_be[1]) ? 1'b1 : 1'b0;
    end

    // Combinational register mux. Deliberately an always @* block rather than a function
    // call inside the assign: a function's internal reads do not reliably appear in a
    // continuous assignment's sensitive list, and this exact form returned a stale word on
    // the first AXI read after a control write in xsim/iverilog.
    reg [31:0] reg_rdata;
    always @* begin
        case (ridx)
            4'h0:    reg_rdata = {30'd0, 1'b0, enable};
            4'h1:    reg_rdata = {28'd0, dropped, drdy_r[1], data_valid, busy};
            4'h2:    reg_rdata = sample_total;
            4'h3:    reg_rdata = disc_cnt;
            4'h4:    reg_rdata = {{(16-PW){1'b0}}, slot};
            4'h5:    reg_rdata = wrap_cnt;
            4'h6:    reg_rdata = discard_limit;
            4'h7:    reg_rdata = last_status_r;
            4'h8:    reg_rdata = last_entries_r;
            4'h9:    reg_rdata = last_temp_r;
            4'hA:    reg_rdata = last_x_r;
            4'hB:    reg_rdata = last_y_r;
            4'hC:    reg_rdata = last_z_r;
            4'hD:    reg_rdata = {15'd0, spi_burst, spi_wdata, spi_cmd};
            4'hE:    reg_rdata = {24'd0, manual_rd};
            4'hF:    reg_rdata = {30'd0, spi_refused, spi_done};
            default: reg_rdata = 32'd0;
        endcase
    end

    assign S_AXI_ARREADY = rst_n && (rd_st == 2'd0);
    assign S_AXI_RVALID  = (rd_st == 2'd3);
    assign rd_word       = ar_addr[13:2];
    // Hold the response snapshot until the master accepts it.
    assign S_AXI_RDATA   = r_data;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_st <= 2'd0; ar_addr <= 16'd0; r_data <= 32'd0;
        end else begin
            case (rd_st)
                2'd0: if (S_AXI_ARVALID && S_AXI_ARREADY) begin
                          ar_addr <= S_AXI_ARADDR;
                          rd_st   <= 2'd1;
                      end
                2'd1: rd_st <= 2'd2; // synchronous BRAM reads the latched address
                2'd2: begin
                          r_data <= ar_addr[14] ? reg_rdata : ram_q;
                          rd_st <= 2'd3;
                      end
                2'd3: if (S_AXI_RREADY) rd_st <= 2'd0;
                default: rd_st <= 2'd0;
            endcase
        end
    end

endmodule
`default_nettype wire
