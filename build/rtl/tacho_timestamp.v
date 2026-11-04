// Single-edge optical/tachometer capture, no RPM division or event buffering.
// COUNTER_BITS >= 1; FILTER_CYCLES >= 1; RISING_EDGE is 1 for rising, 0 for falling.
//
// time_ticks must come from the SAME capture_timebase used for sample timestamps.
// event_ticks is the clock value at FILTER CONFIRMATION, not the physical optical
// edge. For a level first sampled into the first synchronizer at tick T, normal
// confirmation occurs at T + FILTER_CYCLES + 1. The phase-dependent wait from an
// asynchronous edge to T is additional; metastability may add another cycle.
// This fixed/quantized latency must be accounted for during phase calibration.
//
// enable=0 suppresses events, invalidates period history, preserves pulse_count.
// Reset, clear and re-enabling flush/arm from a stable CURRENT input level;
// an existing high (or low in falling mode) is never a fabricated startup pulse.
// Pulses during the 2-clock synchronizer warmup plus FILTER_CYCLES baseline
// acquisition are deliberately not reported. clear does NOT reset the timebase.
// period_ticks is modulo 2**COUNTER_BITS; true intervals must fit that width.
// A missing input pulse therefore produces a longer interval, never an inferred
// event. Outputs must be consumed on event_valid: this core contains no FIFO.
module tacho_timestamp #(
    parameter integer COUNTER_BITS = 64,
    parameter integer FILTER_CYCLES = 4,
    parameter integer RISING_EDGE = 1
) (
    input wire clk,
    input wire rst_n,
    input wire enable,
    input wire clear,
    input wire tacho_async,
    input wire [COUNTER_BITS-1:0] time_ticks,
    output reg event_valid,
    output reg [COUNTER_BITS-1:0] event_ticks,
    output reg [COUNTER_BITS-1:0] pulse_count,
    output reg period_valid,
    output reg [COUNTER_BITS-1:0] period_ticks
);
    localparam integer FILTER_BITS = (FILTER_CYCLES <= 1) ? 1 : $clog2(FILTER_CYCLES + 1);
    (* ASYNC_REG = "TRUE" *) reg tacho_meta;
    (* ASYNC_REG = "TRUE" *) reg tacho_sync;
    reg stable_level;
    reg armed;
    reg [1:0] warmup_count;
    reg [FILTER_BITS-1:0] filter_count;
    reg have_previous;
    reg [COUNTER_BITS-1:0] previous_ticks;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tacho_meta <= 1'b0;
            tacho_sync <= 1'b0;
        end else begin
            tacho_meta <= tacho_async;
            tacho_sync <= tacho_meta;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            event_valid <= 1'b0;
            event_ticks <= {COUNTER_BITS{1'b0}};
            pulse_count <= {COUNTER_BITS{1'b0}};
            period_valid <= 1'b0;
            period_ticks <= {COUNTER_BITS{1'b0}};
            previous_ticks <= {COUNTER_BITS{1'b0}};
            have_previous <= 1'b0;
            stable_level <= 1'b0;
            armed <= 1'b0;
            warmup_count <= 2'd2;
            filter_count <= {FILTER_BITS{1'b0}};
        end else begin
            event_valid <= 1'b0;
            period_valid <= 1'b0;
            if (clear) begin
                event_ticks <= {COUNTER_BITS{1'b0}};
                pulse_count <= {COUNTER_BITS{1'b0}};
                period_ticks <= {COUNTER_BITS{1'b0}};
                previous_ticks <= {COUNTER_BITS{1'b0}};
                have_previous <= 1'b0;
                armed <= 1'b0;
                warmup_count <= 2'd2;
                filter_count <= {FILTER_BITS{1'b0}};
                stable_level <= tacho_sync;
            end else if (!enable) begin
                have_previous <= 1'b0;
                armed <= 1'b0;
                warmup_count <= 2'd2;
                filter_count <= {FILTER_BITS{1'b0}};
                stable_level <= tacho_sync;
            end else if (warmup_count != 0) begin
                warmup_count <= warmup_count - 1'b1;
                stable_level <= tacho_sync;
                filter_count <= {FILTER_BITS{1'b0}};
            end else if (!armed) begin
                // Baseline qualification is separate from event detection.
                if (tacho_sync != stable_level) begin
                    stable_level <= tacho_sync;
                    filter_count <= 1;
                    if (FILTER_CYCLES == 1) begin
                        armed <= 1'b1;
                        filter_count <= {FILTER_BITS{1'b0}};
                    end
                end else if (filter_count == FILTER_CYCLES - 1) begin
                    armed <= 1'b1;
                    filter_count <= {FILTER_BITS{1'b0}};
                end else begin
                    filter_count <= filter_count + 1'b1;
                end
            end else if (tacho_sync == stable_level) begin
                filter_count <= {FILTER_BITS{1'b0}};
            end else if (filter_count == FILTER_CYCLES - 1) begin
                stable_level <= tacho_sync;
                filter_count <= {FILTER_BITS{1'b0}};
                if ((RISING_EDGE != 0 && tacho_sync) ||
                    (RISING_EDGE == 0 && !tacho_sync)) begin
                    event_valid <= 1'b1;
                    event_ticks <= time_ticks;
                    pulse_count <= pulse_count + 1'b1;
                    previous_ticks <= time_ticks;
                    have_previous <= 1'b1;
                    if (have_previous) begin
                        period_valid <= 1'b1;
                        period_ticks <= time_ticks - previous_ticks;
                    end else begin
                        period_ticks <= {COUNTER_BITS{1'b0}};
                    end
                end
            end else begin
                filter_count <= filter_count + 1'b1;
            end
        end
    end
endmodule
