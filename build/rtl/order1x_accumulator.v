// 64 angle samples/revolution, average 1..256 complete revolutions.
// Input: signed ADXL355 raw counts. Output: complex 1x coefficient in raw Q8.
// Convention: X = 2/N * SUM(x[k] * exp(-j*2*pi*k/64)).
// Caller must supply uniformly spaced angle samples, not time-domain samples.
module order1x_accumulator(
    input wire clk,
    input wire rst_n,
    input wire clear_i,
    input wire start_i,
    input wire [3:0] avg_log2_i,
    input wire sample_valid_i,
    input wire signed [19:0] sample_i,
    input wire result_ready_i,
    output wire start_ready_o,
    output wire sample_ready_o,
    output reg result_valid_o,
    output reg signed [31:0] real_o,
    output reg signed [31:0] imag_o,
    output reg config_error_o,
    output wire busy_o
);
    reg active;
    reg input_done;
    reg [5:0] angle_index;
    reg [14:0] samples_remaining;
    reg [4:0] normalization_shift;
    reg signed [51:0] real_sum, imag_sum;

    // One registered multiplier stage, with matching valid/last tags. Products
    // use signed 20x18 multiplies so each maps to one 7-series DSP48E1 slice.
    (* use_dsp = "yes" *) reg signed [37:0] real_product, imag_product;
    reg product_valid, product_last;
    reg normalize_pending;
    wire accept_sample = sample_valid_i && sample_ready_o;
    assign start_ready_o = !active && !result_valid_o && !clear_i;
    assign sample_ready_o = active && !input_done && !clear_i;
    assign busy_o = active || result_valid_o;

    // round(65536*sin(pi*n/32)), n=0..16, generated offline with nearest
    // integer rounding. Reflection/sign extension builds all 64 phases from
    // one quarter wave; opposite phases are exact negatives, so DC cancels.
    function signed [17:0] quarter_sin;
        input [4:0] n;
        begin
            case (n)
                 0: quarter_sin=18'sd0;
                 1: quarter_sin=18'sd6424;
                 2: quarter_sin=18'sd12785;
                 3: quarter_sin=18'sd19024;
                 4: quarter_sin=18'sd25080;
                 5: quarter_sin=18'sd30893;
                 6: quarter_sin=18'sd36410;
                 7: quarter_sin=18'sd41576;
                 8: quarter_sin=18'sd46341;
                 9: quarter_sin=18'sd50660;
                10: quarter_sin=18'sd54491;
                11: quarter_sin=18'sd57798;
                12: quarter_sin=18'sd60547;
                13: quarter_sin=18'sd62714;
                14: quarter_sin=18'sd64277;
                15: quarter_sin=18'sd65220;
                16: quarter_sin=18'sd65536;
                default: quarter_sin=18'sd0;
            endcase
        end
    endfunction
    function signed [17:0] sine_q16;
        input [5:0] phase;
        begin
            case (phase[5:4])
                2'b00: sine_q16= quarter_sin({1'b0,phase[3:0]});
                2'b01: sine_q16= quarter_sin(5'd16-{1'b0,phase[3:0]});
                2'b10: sine_q16=-quarter_sin({1'b0,phase[3:0]});
                2'b11: sine_q16=-quarter_sin(5'd16-{1'b0,phase[3:0]});
            endcase
        end
    endfunction
    wire [5:0] cosine_phase=angle_index+6'd16;
    wire signed [17:0] cosine=sine_q16(cosine_phase);
    wire signed [17:0] minus_sine=-sine_q16(angle_index);
    wire signed [51:0] extended_real={{14{real_product[37]}},real_product};
    wire signed [51:0] extended_imag={{14{imag_product[37]}},imag_product};
    wire signed [51:0] next_real_sum=real_sum+extended_real;
    wire signed [51:0] next_imag_sum=imag_sum+extended_imag;

    // 2/(64*turns) * Q16 products -> Q8 output: shift 13+log2(turns).
    // Round magnitude then restore sign, including negative exact half ties.
    function signed [31:0] normalized;
        input signed [51:0] sum;
        input [4:0] shift;
        reg [52:0] magnitude;
        reg [52:0] rounded;
        begin
            if (sum[51]) magnitude=-$signed({sum[51],sum});
            else magnitude={1'b0,sum};
            rounded=(magnitude+(53'd1<<(shift-1'b1)))>>shift;
            if(sum[51]) normalized=-$signed(rounded[31:0]);
            else normalized=$signed(rounded[31:0]);
        end
    endfunction

    always @(posedge clk) begin
        if (!rst_n || clear_i) begin
            active<=1'b0;
            input_done<=1'b0;
            angle_index<=6'd0;
            samples_remaining<=15'd0;
            normalization_shift<=5'd13;
            real_sum<=52'sd0;
            imag_sum<=52'sd0;
            real_product<=38'sd0;
            imag_product<=38'sd0;
            product_valid<=1'b0;
            product_last<=1'b0;
            normalize_pending<=1'b0;
            result_valid_o<=1'b0;
            real_o<=32'sd0;
            imag_o<=32'sd0;
            config_error_o<=1'b0;
        end else begin
            config_error_o<=1'b0;
            product_valid<=accept_sample;
            product_last<=1'b0;
            normalize_pending<=1'b0;
            if (result_valid_o && result_ready_i) result_valid_o<=1'b0;

            if (start_i && start_ready_o) begin
                if (avg_log2_i>4'd8) begin
                    config_error_o<=1'b1;
                end else begin
                    active<=1'b1;
                    input_done<=1'b0;
                    angle_index<=6'd0;
                    samples_remaining<=15'd64<<avg_log2_i;
                    normalization_shift<=5'd13+{1'b0,avg_log2_i};
                    real_sum<=52'sd0;
                    imag_sum<=52'sd0;
                end
            end
            if (accept_sample) begin
                real_product<=sample_i*cosine;
                imag_product<=sample_i*minus_sine;
                product_last<=(samples_remaining==15'd1);
                samples_remaining<=samples_remaining-15'd1;
                angle_index<=angle_index+6'd1;
                if (samples_remaining==15'd1) input_done<=1'b1;
            end
            if (product_valid) begin
                real_sum<=next_real_sum;
                imag_sum<=next_imag_sum;
                if (product_last) normalize_pending<=1'b1;
            end
            // Final products first enter the existing 52-bit sum registers.
            // Normalize only in the following clock, breaking the carry chain
            // from final multiply/add through magnitude/rounding/sign restore.
            // active stays set and input_done stays set during this stage.
            if (normalize_pending) begin
                active<=1'b0;
                result_valid_o<=1'b1;
                real_o<=normalized(real_sum,normalization_shift);
                imag_o<=normalized(imag_sum,normalization_shift);
            end
        end
    end
endmodule
