`timescale 1ns/1ps
// Iterative vectoring CORDIC. Inputs and magnitude share the same fixed-point
// units (e.g. count Q8); phase is signed binary turns: one turn = 2^32.
// [-pi,pi) convention: negative real axis returns 32'h80000000 exactly.
// General-vector latency: ITERATIONS+32 clocks after acceptance; axes/zero
// publish on the acceptance edge. No overlap; output holds until ready.
// clear_i is a synchronous abort; rst_n is an asynchronous active-low abort.
module cordic_vectoring #(
    parameter integer ITERATIONS = 24
)(
    input  wire               clk,
    input  wire               rst_n,
    input  wire               clear_i,
    input  wire               in_valid_i,
    input  wire signed [31:0] in_real_i,
    input  wire signed [31:0] in_imag_i,
    output wire               in_ready_o,
    output reg                out_valid_o,
    input  wire               out_ready_i,
    output reg  [31:0]        magnitude_o,
    output reg signed [31:0] phase_o,
    output reg                phase_defined_o
);
    localparam [2:0] IDLE=0, ROTATE=1, GAIN=2, GAIN_ADD=3, PUBLISH=4;
    localparam integer GUARD_FRACTION = 28;
    reg [2:0] state;
    reg [4:0] iteration;
    reg signed [63:0] x, y;
    reg signed [31:0] z, phase_pending;
    reg signed [95:0] gain_product;
    reg [95:0] gain_addend;
    reg [29:0] gain_bits;
    reg [4:0] gain_step;
    wire signed [63:0] input_x = {{32{in_real_i[31]}},in_real_i};
    wire signed [63:0] input_y = {{32{in_imag_i[31]}},in_imag_i};
    wire [95:0] rounded_product = gain_product + 96'd144115188075855872; // 2^57

    // Constants generated independently with Python's math module:
    // round(atan(2**-i)/(2*pi)*2**32), i=0..23.
    function [31:0] atan_bam;
        input [4:0] index;
        begin
            case(index)
                0: atan_bam=32'd536870912;
                1: atan_bam=32'd316933406;
                2: atan_bam=32'd167458907;
                3: atan_bam=32'd85004756;
                4: atan_bam=32'd42667331;
                5: atan_bam=32'd21354465;
                6: atan_bam=32'd10679838;
                7: atan_bam=32'd5340245;
                8: atan_bam=32'd2670163;
                9: atan_bam=32'd1335087;
               10: atan_bam=32'd667544;
               11: atan_bam=32'd333772;
               12: atan_bam=32'd166886;
               13: atan_bam=32'd83443;
               14: atan_bam=32'd41722;
               15: atan_bam=32'd20861;
               16: atan_bam=32'd10430;
               17: atan_bam=32'd5215;
               18: atan_bam=32'd2608;
               19: atan_bam=32'd1304;
               20: atan_bam=32'd652;
               21: atan_bam=32'd326;
               22: atan_bam=32'd163;
               23: atan_bam=32'd81;
               default: atan_bam=0;
            endcase
        end
    endfunction

    // round(prod(1/sqrt(1+2**(-2*i)), i=0..N-1)*2**30).
    // The finite-iteration gain is honored for parameter values 1..24.
    function [31:0] inverse_gain_q30;
        input integer depth;
        begin
            case(depth)
                1: inverse_gain_q30=32'd759250125;
                2: inverse_gain_q30=32'd679093957;
                3: inverse_gain_q30=32'd658817909;
                4: inverse_gain_q30=32'd653730436;
                5: inverse_gain_q30=32'd652457347;
                6: inverse_gain_q30=32'd652138997;
                7: inverse_gain_q30=32'd652059405;
                8: inverse_gain_q30=32'd652039507;
                9: inverse_gain_q30=32'd652034532;
               10: inverse_gain_q30=32'd652033289;
               11: inverse_gain_q30=32'd652032978;
               12: inverse_gain_q30=32'd652032900;
               13: inverse_gain_q30=32'd652032881;
               14: inverse_gain_q30=32'd652032876;
               default: inverse_gain_q30=32'd652032874;
            endcase
        end
    endfunction
    localparam signed [31:0] INVERSE_GAIN = inverse_gain_q30(ITERATIONS);
    assign in_ready_o = rst_n && !clear_i && state==IDLE && !out_valid_o;

    // 64-bit x/y with 28 guard fraction bits preserve tiny-vector phase.
    // Worst possible grown norm uses <62 bits, leaving signed guard room.
    // Sign-extension precedes negation, including input INT32_MIN.
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            state<=IDLE; iteration<=0; x<=0; y<=0; z<=0;
            gain_product<=0; phase_pending<=0;
            gain_addend<=0; gain_bits<=0; gain_step<=0;
            out_valid_o<=0; magnitude_o<=0; phase_o<=0; phase_defined_o<=0;
        end else if(clear_i) begin
            state<=IDLE; iteration<=0; x<=0; y<=0; z<=0;
            gain_product<=0; phase_pending<=0;
            gain_addend<=0; gain_bits<=0; gain_step<=0;
            out_valid_o<=0; magnitude_o<=0; phase_o<=0; phase_defined_o<=0;
        end else begin
            if(out_valid_o && out_ready_i) out_valid_o<=0;
            case(state)
                IDLE: if(in_valid_i && in_ready_o) begin
                    iteration<=0;
                    if(in_real_i==0 && in_imag_i==0) begin
                        magnitude_o<=0; phase_o<=0; phase_defined_o<=0; out_valid_o<=1;
                    end else if(in_imag_i==0) begin
                        magnitude_o<=in_real_i[31] ? -input_x : input_x;
                        phase_o<=in_real_i[31] ? 32'sh80000000 : 32'sd0;
                        phase_defined_o<=1; out_valid_o<=1;
                    end else if(in_real_i==0) begin
                        magnitude_o<=in_imag_i[31] ? -input_y : input_y;
                        phase_o<=in_imag_i[31] ? -32'sd1073741824 : 32'sd1073741824;
                        phase_defined_o<=1; out_valid_o<=1;
                    end else begin
                        if(in_real_i[31]) begin
                            x<=(-input_x) <<< GUARD_FRACTION;
                            y<=(-input_y) <<< GUARD_FRACTION;
                            z<=32'sh80000000;
                        end else begin
                            x<=input_x <<< GUARD_FRACTION;
                            y<=input_y <<< GUARD_FRACTION;
                            z<=0;
                        end
                        state<=ROTATE;
                    end
                end
                ROTATE: begin
                    // Even when y is zero, rotate to preserve the fixed gain.
                    if(y>=0) begin
                        x<=x+(y >>> iteration);
                        y<=y-(x >>> iteration);
                        z<=z+$signed(atan_bam(iteration));
                    end else begin
                        x<=x-(y >>> iteration);
                        y<=y+(x >>> iteration);
                        z<=z-$signed(atan_bam(iteration));
                    end
                    if(iteration==ITERATIONS-1) state<=GAIN;
                    else iteration<=iteration+1'b1;
                end
                GAIN: begin
                    // All supported inverse gains are positive and <2^30.
                    // Process their 30 bits exactly; a wide one-cycle multiplier
                    // otherwise creates an unregistered DSP cascade >10 ns.
                    gain_product<=0;
                    gain_addend<={32'b0,x};
                    gain_bits<=INVERSE_GAIN[29:0];
                    gain_step<=0;
                    phase_pending<=z;
                    state<=GAIN_ADD;
                end
                GAIN_ADD: begin
                    if(gain_bits[0]) gain_product<=gain_product+gain_addend;
                    gain_addend<=gain_addend << 1;
                    gain_bits<=gain_bits >> 1;
                    if(gain_step==29) state<=PUBLISH;
                    else gain_step<=gain_step+1'b1;
                end
                PUBLISH: begin
                    // x Q28 * gain Q30 => Q58. Positive nearest rounding,
                    // explicit unsigned saturation if a future format widens.
                    magnitude_o<=|rounded_product[95:90] ? 32'hffffffff : rounded_product[89:58];
                    phase_o<=phase_pending; phase_defined_o<=1; out_valid_o<=1;
                    state<=IDLE;
                end
                default: state<=IDLE;
            endcase
        end
    end
`ifndef SYNTHESIS
    initial if(ITERATIONS<1 || ITERATIONS>24) $fatal(1,"ITERATIONS must be in 1..24");
`endif
endmodule

