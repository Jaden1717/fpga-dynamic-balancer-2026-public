// Shared free-running capture clock. Integrate ONE instance for acceleration
// and tacho capture: do not instantiate independent clocks for each path.
// COUNTER_BITS must be >= 1. At each rising clk edge, consumers see the value
// BEFORE this counter increments. Reset is the only timebase discontinuity.
module capture_timebase #(
    parameter integer COUNTER_BITS = 64
) (
    input wire clk,
    input wire rst_n,
    input wire sample_strobe,
    output reg [COUNTER_BITS-1:0] time_ticks,
    output reg [COUNTER_BITS-1:0] sample_ticks,
    output reg sample_valid
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            time_ticks <= {COUNTER_BITS{1'b0}};
            sample_ticks <= {COUNTER_BITS{1'b0}};
            sample_valid <= 1'b0;
        end else begin
            time_ticks <= time_ticks + 1'b1;
            sample_valid <= sample_strobe;
            if (sample_strobe)
                sample_ticks <= time_ticks;
        end
    end
endmodule
