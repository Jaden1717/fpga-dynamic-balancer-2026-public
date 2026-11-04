"""Measure DRDY on ja_i[3] so the PL controller can be built on a real sample clock.

Why this is the last board measurement needed before writing RTL: the read protocol is now
locked (adxl355_stall.py, 2026-10-05 14:2x: bare 0x08..0x10 reads gave 24/24 distinct
samples with no FIFO servicing, so the PL state machine is just burst + shift). What is
still unmeasured is the trigger. Datasheet Rev. D says the device "outputs DRDY (active
high) to signal that a new sample is available" and that Z_PTR, which is what 0x08..0x10
mirrors, advances once per ODR period = 1/4000 = 250 us. A burst must land well inside
that window or the three axis words can straddle two samples.

Reading the GPIO input register through pynq.MMIO costs roughly 1 to 10 us, which is fast
enough to oversample a 250 us period, unlike a full SPI transaction (~5 ms bit-banged). So
this script polls the DRDY bit as fast as Python allows and estimates the period from the
run lengths, then times one register read and one 13-byte burst to put the exact number on
the PS-side ceiling.

Nothing is written. If DRDY is silent the chip is not in measurement mode, and that must be
fixed by writing POWER_CTL = 0x00 before the PL design has a trigger to use.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_drdy.py
"""
import time

from adxl355_burst import BLOCK_LEN, REG_BLOCK, burst_read
from adxl355_state import BIT_DRDY
from spi_bitbang_adxl355 import CS_MASK, SpiGpio, read_reg

SAMPLES = 20000
WINDOWS = 3
ODR_NOMINAL = 4000.0
AXIS_READS = 9


def poll_drdy(spi, n):
    """Sample the DRDY bit n times back to back. Returns run lengths and edge count."""
    read = spi.read_in
    t0 = time.perf_counter()
    levels = [(read() >> BIT_DRDY) & 1 for _ in range(n)]
    dt = time.perf_counter() - t0
    edges = sum(a != b for a, b in zip(levels, levels[1:]))
    runs, run = [], 1
    for a, b in zip(levels, levels[1:]):
        if a == b:
            run += 1
        else:
            runs.append(run)
            run = 1
    runs.append(run)
    return levels, dt, edges, runs


def report_window(spi, idx):
    levels, dt, edges, runs = poll_drdy(spi, SAMPLES)
    interval_us = dt / SAMPLES * 1e6
    ones = sum(levels)
    print("window {}: {} reads in {:.3f} s -> {:.2f} us/read, {} edges, {:.1f}% high".format(
        idx, SAMPLES, dt, interval_us, edges, 100.0 * ones / SAMPLES))
    if edges == 0:
        print("           DRDY pinned {} for the whole window".format(levels[0]))
        return levels, edges, 0.0, interval_us
    mean_run = sum(runs) / len(runs)
    period_s = 2.0 * mean_run * interval_us * 1e-6
    print("           mean run {:.1f} reads -> period {:.1f} us -> ODR estimate {:.0f} Hz"
          " (sampled {:.1f}x per half period)".format(
              mean_run, period_s * 1e6, 1 / period_s, 125.0 / interval_us))
    return levels, edges, 1 / period_s, interval_us


def time_spi(spi):
    """Per-transaction cost of the two read styles the PL design replaces."""
    t0 = time.perf_counter()
    for _ in range(20):
        read_reg(spi, 0x04)
    single_us = (time.perf_counter() - t0) / 20 * 1e6
    t0 = time.perf_counter()
    for _ in range(20):
        burst_read(spi, REG_BLOCK, BLOCK_LEN)
    burst_us = (time.perf_counter() - t0) / 20 * 1e6
    print("one register read (2-byte transaction)  : {:8.1f} us -> {:5.1f} samples/s if {} per "
          "sample".format(single_us, 1e6 / (single_us * AXIS_READS), AXIS_READS))
    print("one 13-byte burst (STATUS..0x10)        : {:8.1f} us -> {:5.1f} samples/s"
          .format(burst_us, 1e6 / burst_us))
    print("PL budget per sample at 4 kHz           : {:8.1f} us".format(1e6 / ODR_NOMINAL))
    return single_us, burst_us


def main():
    spi = SpiGpio()
    try:
        spi.write_out(CS_MASK)
        power = read_reg(spi, 0x2D)
        print("POWER_CTL = 0x{:02X} (bit0 Standby 0 and bit2 DRDY_OFF 0 required for DRDY to "
              "pulse)".format(power))
        if power & 0x05:
            print("WARNING: standby or DRDY_OFF is set, DRDY cannot pulse in this state")
        print("DRDY = ja_i[{}], {} reads per window, {} windows\n".format(
            BIT_DRDY, SAMPLES, WINDOWS))

        first = poll_drdy(spi, 160)[0]
        print("first 160 reads: " + "".join(str(v) for v in first) + "\n")

        results = [report_window(spi, i) for i in range(WINDOWS)]
        edges_total = sum(r[1] for r in results)
        est = [r[2] for r in results if r[2] > 0.0]
        interval_us = results[-1][3]

        print()
        time_spi(spi)

        if edges_total == 0:
            print("VERDICT: DRDY is silent -> there is no hardware sample trigger to build on; "
                  "next step is a script that writes POWER_CTL = 0x00 and re-runs this test")
        elif interval_us > 30.0:
            print("VERDICT: DRDY moves but Python oversamples a 250 us period only {:.1f}x -> "
                  "the period estimate is rough; the fact that matters stands: the pin "
                  "toggles, so the PL controller can trigger on its edge".format(250.0 / interval_us))
        elif max(est) > ODR_NOMINAL * 1.3 or min(est) < ODR_NOMINAL * 0.7:
            print("VERDICT: DRDY toggles at an estimated {:.0f} Hz, off the 4000 Hz nominal by "
                  "more than 30% -> check whether the ODR bits in FILTER (0x28) were left at a "
                  "non default value".format(sum(est) / len(est)))
        else:
            print("VERDICT: DRDY pulses at {:.0f} Hz, within tolerance of the 4 kHz ODR -> the "
                  "PL SPI master takes DRDY as its sample trigger and must finish the "
                  "0x08..0x10 burst well inside {:.0f} us".format(
                      sum(est) / len(est), 1e6 / (sum(est) / len(est))))
    finally:
        spi.close()


if __name__ == "__main__":
    main()
