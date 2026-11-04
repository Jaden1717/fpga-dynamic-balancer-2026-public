"""Boot the part the way the PL design will, and watch DRDY while the axes are still zero.

Board evidence (2026-10-05 15:1x, adxl355_warm.py): with the chip already in measurement
(before: POWER_CTL 0x00) the script wrote FILTER = 0x00 and POWER_CTL = 0x00 again, and
0x08..0x10 then stayed exactly zero for 5.161 s -- 156 polled bursts, about 20,600 samples
at 4 kHz. In the same instant that data appeared, FIFO_ENTRIES jumped 0 -> 96, so the FIFO
write side is gated together with the axes and this is a single effect. TEMP never dropped
out (1767..1771 LSB = 37.6..38.0 C) during the whole wait, so the SPI read path and the ADC
were alive the entire time; only the accelerometer datapath was silent.

Datasheet Table 1 specs turn on time (standby to measurement, within 1 mg of final value,
2 g range) at < 10 ms, so 5 s is not the documented startup. Two candidate causes remain:
the intrinsic sensor warm-up, or our own re-write of FILTER while the part was already
measuring (the datasheet requires configuration writes with Standby = 1). This run removes
both ambiguities by using the same init sequence the PL design will use: soft reset, which
puts POWER_CTL back to its 0x01 standby default, then FILTER written once while genuinely
in standby, then POWER_CTL = 0x00, then nothing but reads.

The new measurement is DRDY during the wait. If DRDY already ticks while the axes are zero,
it cannot be trusted as a sample-valid indicator at boot and the PL controller needs its
own discard counter. If DRDY stays flat until the first real sample, the edge itself is the
gate and no counter is required. If data is simply there on the first probe, the 5 s was
our own doing and neither mechanism belongs in the RTL.

One probe costs about 32 ms of burst plus 37 ms of DRDY polling, so the time resolution of
this script is roughly 70 ms -- enough to tell a sub-10 ms datasheet start-up from a
multi-second anomaly, not enough to time the datasheet number itself.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_boot.py
"""
import time

from adxl355_burst import BLOCK_LEN, REG_BLOCK, burst_read, s24
from adxl355_state import BIT_DRDY, REG_DEVID, REG_POWER_CTL, REG_RESET, RESET_KEYS
from spi_bitbang_adxl355 import CS_MASK, SpiGpio, read_reg, write_reg

REG_FILTER = 0x28
REG_RANGE = 0x2C
DEADLINE_S = 15.0
DRDY_READS = 2000
PRINT_EVERY = 8
LSB_PER_G = 256000.0


def drdy_edges(spi, n):
    """Count ja_i[3] transitions over n back-to-back input reads.

    Returns edges, us per read, and the high duty cycle. A 4 kHz square wave needs at least
    ~30 reads per half period to show up here; the board measured 18.7 us per read, which
    gives 6.7 reads per half period, so the edge count is an alias. The level still moves,
    which is the only question this script asks of it.
    """
    read = spi.read_in
    t0 = time.perf_counter()
    levels = [(read() >> BIT_DRDY) & 1 for _ in range(n)]
    dt = time.perf_counter() - t0
    return sum(a != b for a, b in zip(levels, levels[1:])), dt / n * 1e6, sum(levels) / n


def main():
    spi = SpiGpio()
    try:
        spi.write_out(CS_MASK)
        if read_reg(spi, REG_DEVID) != 0xAD:
            print("DEVID wrong, aborting before touching the configuration")
            return
        for key in RESET_KEYS:                       # 0x52, 0xE3, 0xAD
            write_reg(spi, REG_RESET, key)
        time.sleep(0.01)
        print("after soft reset : POWER_CTL 0x{:02X} (expect 0x01 = Standby), RANGE 0x{:02X}"
              .format(read_reg(spi, REG_POWER_CTL), read_reg(spi, REG_RANGE)))

        write_reg(spi, REG_FILTER, 0x00)             # legal here: genuinely in Standby
        t_meas = time.perf_counter()
        write_reg(spi, REG_POWER_CTL, 0x00)
        print("enter measurement: FILTER 0x{:02X} readback, POWER_CTL 0x{:02X}\n".format(
            read_reg(spi, REG_FILTER), read_reg(spi, REG_POWER_CTL)))

        first = None
        probes = 0
        early_edges = 0
        while time.perf_counter() - t_meas < DEADLINE_S:
            probes += 1
            blk = burst_read(spi, REG_BLOCK, BLOCK_LEN)
            t = time.perf_counter() - t_meas
            axes = [s24(blk[4], blk[5], blk[6]) >> 4, s24(blk[7], blk[8], blk[9]) >> 4,
                    s24(blk[10], blk[11], blk[12]) >> 4]
            edges, us, duty = drdy_edges(spi, DRDY_READS)
            if first is None and any(axes):
                first = (t, blk[0], blk[1] & 0x7F, axes, edges, duty)
            elif first is None and probes % PRINT_EVERY == 0:
                print("t {:5.2f} s  STATUS 0x{:02X} ENTRIES {:3d}  DRDY {:4d} edges "
                      "({:.0f}% high)  axes all zero".format(
                          t, blk[0], blk[1] & 0x7F, edges, duty * 100.0))
            if first is None:
                early_edges += edges

        elapsed = time.perf_counter() - t_meas
        print("\n{} probe bursts in {:.2f} s; DRDY edges counted while axes were still zero: {}"
              .format(probes, elapsed, early_edges))
        if first:
            t, status, entries, axes, edges, duty = first
            print("first non-zero axes at t = {:.3f} s, STATUS 0x{:02X}, ENTRIES {}, then "
                  "X {:+d} Y {:+d} Z {:+d} LSB ({:+.3f} g on Z)".format(
                      t, status, entries, *axes, axes[2] / LSB_PER_G))
            if t < 0.2:
                print("VERDICT: data is present within {:.0f} ms of a clean reset -> there is no "
                      "intrinsic multi-second warm-up; the 5.16 s in warm.py came from writing "
                      "FILTER while the part was already measuring. The PL init sequence is "
                      "reset, FILTER, POWER_CTL = 0x00, and it must never re-write a config "
                      "register without putting Standby back first.".format(t * 1000.0))
            elif early_edges > 100:
                print("VERDICT: DRDY already ticks during the {:.0f} ms warm-up -> the edge means "
                      "filter output, not valid sample; the PL controller needs its own discard "
                      "counter of {:.0f} ms (about {:.0f} samples)".format(
                          t * 1000.0, t * 1000.0, t * 4000.0))
            else:
                print("VERDICT: DRDY stays flat for {:.0f} ms and only moves with the first real "
                      "sample -> the rising DRDY edge is itself the start gate, the PL "
                      "controller needs no discard counter".format(t * 1000.0))
        else:
            print("VERDICT: no axis data within {:.0f} s of a clean reset -> the warm-up story is "
                  "wrong; next test is an ODR sweep to see whether the 4 kHz setting itself is "
                  "the problem".format(DEADLINE_S))
    finally:
        spi.close()


if __name__ == "__main__":
    main()
