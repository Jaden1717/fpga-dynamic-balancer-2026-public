`timescale 1ns/1ps
// Mathematical-oracle tests catch magnitude/phase sign, quadrant, scaling,
// INT32_MIN negation, handshake overwrite, and stale-result-after-abort bugs.
module tb_cordic_vectoring;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst_n = 0, clear_i = 0, in_valid_i = 0, out_ready_i = 0;
    reg signed [31:0] in_real_i = 0, in_imag_i = 0;
    wire in_ready_o, out_valid_o, phase_defined_o;
    wire [31:0] magnitude_o;
    wire signed [31:0] phase_o;
    integer cycle = 0, last_accept_cycle = 0;
    always @(posedge clk) cycle = cycle + 1;
    integer checks = 0, vectors = 0, seed = 32'h16429acd, n;
    reg signed [31:0] random_x, random_y;
    real max_mag_error = 0.0, max_phase_error_deg = 0.0;
    cordic_vectoring dut(.clk(clk),.rst_n(rst_n),.clear_i(clear_i),.in_valid_i(in_valid_i),.in_real_i(in_real_i),.in_imag_i(in_imag_i),.out_ready_i(out_ready_i),.in_ready_o(in_ready_o),.out_valid_o(out_valid_o),.magnitude_o(magnitude_o),.phase_o(phase_o),.phase_defined_o(phase_defined_o));

    task demand;
        input condition;
        input [511:0] description;
        begin
            checks = checks + 1;
            if (condition !== 1'b1) begin
                $display("FAIL: %0s at %0t", description, $time);
                $fatal(1, "CORDIC contract check failed");
            end
        end
    endtask

    task send_vector;
        input signed [31:0] x, y;
        integer timeout;
        begin
            @(negedge clk);
            timeout = 0;
            while (!in_ready_o && timeout < 80) begin
                @(negedge clk); timeout = timeout + 1;
            end
            demand(in_ready_o, "input eventually ready");
            in_real_i = x; in_imag_i = y; in_valid_i = 1;
            @(posedge clk); #1; last_accept_cycle = cycle;
            demand(!in_ready_o, "accepted input cannot overlap another operation");
            @(negedge clk); in_valid_i = 0;
        end
    endtask

    task await_result;
        integer timeout;
        begin
            timeout = 0;
            while (!out_valid_o && timeout < 80) begin
                @(posedge clk); #1; timeout = timeout + 1;
            end
            demand(out_valid_o, "result available within 80 clocks");
        end
    endtask

    task check_vector;
        input signed [31:0] x, y;
        input integer stall_clocks;
        real xr, yr, expected_mag, actual_mag, expected_phase, phase_error;
        real mag_error, mag_tolerance, phase_error_deg;
        reg [31:0] held_mag;
        reg signed [31:0] held_phase;
        reg held_defined;
        integer s;
        begin
            send_vector(x, y);
            await_result;
            vectors = vectors + 1;
            xr = $itor(x); yr = $itor(y);
            expected_mag = $sqrt(xr*xr + yr*yr);
            actual_mag = $itor(magnitude_o[30:0]);
            if (magnitude_o[31]) actual_mag = actual_mag + 2147483648.0;
            mag_error = actual_mag - expected_mag;
            if (mag_error < 0) mag_error = -mag_error;
            mag_tolerance = 0.501 + expected_mag * 0.000002;
            // Small vectors must round to nearest, not truncate.
            if (mag_error > max_mag_error) max_mag_error = mag_error;
            if (mag_error > mag_tolerance) begin
                $display("VECTOR: x=%0d y=%0d magnitude=%0d expected=%f error=%f tolerance=%f", x,y,magnitude_o,expected_mag,mag_error,mag_tolerance);
            end
            demand(mag_error <= mag_tolerance, "magnitude agrees with Euclidean norm");
            demand(phase_defined_o == ((x != 0) || (y != 0)), "zero vector has undefined phase only");
            if (x == 0 && y == 0) begin
                demand(magnitude_o == 0 && phase_o == 0, "zero returns exact canonical values");
            end else begin
                expected_phase = $atan2(yr, xr) / 6.283185307179586476925286766559 * 4294967296.0;
                phase_error = $itor(phase_o) - expected_phase;
                if (phase_error > 2147483648.0) phase_error = phase_error - 4294967296.0;
                if (phase_error < -2147483648.0) phase_error = phase_error + 4294967296.0;
                if (phase_error < 0) phase_error = -phase_error;
                phase_error_deg = phase_error * 360.0 / 4294967296.0;
                if (phase_error_deg > max_phase_error_deg) max_phase_error_deg = phase_error_deg;
                if (phase_error_deg >= 0.001) begin
                    $display("VECTOR: x=%0d y=%0d phase=%0d expected=%f error_deg=%f", x,y,phase_o,expected_phase,phase_error_deg);
                end
                demand(phase_error_deg < 0.001, "phase agrees with atan2 within 0.001 degree");
                if (y == 0) demand(phase_o == (x < 0 ? 32'sh80000000 : 32'sd0), "real-axis phase exact, negative pi canonical");
                if (x == 0) demand(phase_o == (y < 0 ? -32'sd1073741824 : 32'sd1073741824), "imaginary-axis phase exact");
            end
            // The fixed serial-gain schedule must include every final sum before publish.
            // This catches a missing/extra last accumulation and premature valid.
            if (x != 0 && y != 0) demand(cycle-last_accept_cycle == 56, "general result uses all 24 rotations and 30 exact gain steps");
            else demand(cycle-last_accept_cycle == 0, "axes and zero retain immediate canonical result");
            held_mag = magnitude_o; held_phase = phase_o; held_defined = phase_defined_o;
            for (s=0; s<stall_clocks; s=s+1) begin
                @(negedge clk); in_valid_i=1; in_real_i=123; in_imag_i=-456;
                @(posedge clk); #1;
                demand(out_valid_o && !in_ready_o, "backpressure holds result and rejects overlap");
                demand(magnitude_o == held_mag && phase_o == held_phase && phase_defined_o == held_defined, "stalled output is stable");
            end
            @(negedge clk); in_valid_i=0; out_ready_i=1;
            @(posedge clk); #1; demand(!out_valid_o, "output consumed exactly once");
            @(negedge clk); out_ready_i=0;
        end
    endtask

    task abort_inflight;
        input reset_instead;
        input integer delay_cycles;
        integer s;
        begin
            send_vector(1234567, -7654321);
            repeat (delay_cycles) @(negedge clk);
            if (reset_instead) rst_n=0; else clear_i=1;
            @(posedge clk); #1;
            demand(!out_valid_o && !in_ready_o, "abort suppresses result and input handshake");
            @(negedge clk); rst_n=1; clear_i=0;
            for (s=0; s<40; s=s+1) begin
                @(posedge clk); #1; demand(!out_valid_o, "aborted operation cannot publish a stale result");
            end
        end
    endtask

    initial begin
        repeat (4) @(negedge clk); rst_n=1;
        check_vector(3,4,4); // RED first: zero-output stub must fail norm=5.
        check_vector(3,-4,1); check_vector(-3,4,2); check_vector(-3,-4,3);
        check_vector(0,0,2);
        check_vector(1,0,1); check_vector(-1,0,1); check_vector(0,1,1); check_vector(0,-1,1);
        check_vector(1,1,0); check_vector(1,-1,0); check_vector(-1,1,0); check_vector(-1,-1,0);
        check_vector(2,2,0); check_vector(1,2,0); check_vector(2,1,0); check_vector(-2,1,0);
        check_vector(32'sh80000000,0,1); check_vector(0,32'sh80000000,1);
        check_vector(32'sh7fffffff,32'sh7fffffff,4);
        check_vector(32'sh80000000,32'sh80000000,4);
        check_vector(32'sh80000000,32'sh7fffffff,4);
        check_vector(32'sh7fffffff,32'sh80000000,4);
        check_vector(32'sh7fffffff,1,0); check_vector(32'sh80000000,-1,0);
        for (n=0; n<256; n=n+1) begin
            random_x=$random(seed); random_y=$random(seed);
            check_vector(random_x,random_y,n%5);
        end
        abort_inflight(0,5); check_vector(5,12,0);
        abort_inflight(1,5); check_vector(-5,12,0);
        abort_inflight(0,40); abort_inflight(1,50);
        send_vector(8,15); await_result;
        @(negedge clk); clear_i=1;
        @(posedge clk); #1; demand(!out_valid_o, "clear discards pending result");
        @(negedge clk); clear_i=0;
        check_vector(-8,-15,2);
        send_vector(7,-24); await_result;
        @(negedge clk); rst_n=0; #1; demand(!out_valid_o, "reset discards pending result asynchronously");
        @(negedge clk); rst_n=1;
        check_vector(7,-24,2);
        $display("CORDIC_ACCURACY: vectors=%0d max_magnitude_error=%f max_phase_error_deg=%f", vectors,max_mag_error,max_phase_error_deg);
        $display("PASS: all cordic_vectoring checks OK (%0d assertions)",checks);
        $finish;
    end
    initial begin #1000000; $fatal(1,"TIMEOUT: cordic_vectoring testbench"); end
endmodule



