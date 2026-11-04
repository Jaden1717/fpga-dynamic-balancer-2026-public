"""Put the part into measurement mode and watch DRDY in the same run.

Board evidence (2026-10-05 14:3x, adxl355_drdy.py): POWER_CTL read back as 0x01, its reset
default, so the chip had fallen back to Standby between runs (the board was likely
power-cycled or the overlay reloaded). In Standby the accelerometer and temperature
datapaths and the FIFO pointers stop, so 60,000 GPIO reads produced 0 edges and 100% high:
that run measured nothing about DRDY. The axes still read about 1 g because 0x08..0x10
mirror the newest FIFO set, which simply stays frozen at the last sample before standby.

Datasheet rule applied here: configuration registers are written while Standby = 1, then
Standby is released. The chip is already in that state, so one run can write FILTER = 0x00
(bits [2:0] = 0 is ODR 4000 Hz) and POWER_CTL = 0x00 and immediately watch DRDY.

All four ja_i bits are scanned, not only bit 3: the PL controller has to attach DRDY to the
right package pin, and a per-bit activity table is cheap insurance for that constraint.

A GPIO register read measured 18.7 us, so one 250 us ODR period gets about 13 samples and
one half period about 6; the period estimate from run lengths is therefore coarse but
cannot alias, and edges either exist or they do not.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_start.py
"""
import time

from adxl355_state import BIT_DRDY, REG_POWER_CTL
from spi_bitbang_adxl355 import CS_MASK, SpiGpio, read_reg, write_reg

REG_FILTER = 0x28
ODR_NOMINAL = 4000.0
SAMPLES = 12000
WINDOWS = 3
AXIS_REGS = ((0x08, 0x09, 0x0A), (0x0B, 0x0C, 0x0D), (0x0E, 0x0F, 0x10))
LSB_PER_G = 256000.0


def s24(hi, mid, lo):
    v = (hi << 16) | (mid << 8) | lo
    return v - (1 << 24) if v & 0x800000 else v


def read_g(spi):
    return [(s24(read_reg(spi, r[0]), read_reg(spi, r[1]), read_reg(spi, r[2])) >> 4) /
            LSB_PER_G for r in AXIS_REGS]


def poll_bit(spi, n, bit):
    """Sample one ja_i bit n times back to back; return edges, mean run length, dt."""
    read = spi.read_in
    t0 = time.perf_counter()
    levels = [(read() >> bit) & 1 for _ in range(n)]
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


def report_bit(spi, n, bit, label, show_wave=False):
    levels, dt, edges, runs = poll_bit(spi, n, bit)
    interval_us = dt / n * 1e6
    mean_run = sum(runs) / len(runs)
    odr = 1.0 / (2.0 * mean_run * interval_us * 1e-6) if edges else 0.0
    line = "{:14s} bit{}: {:6d} reads, {:.1f} us/read, {:5d} edges, {:5.1f}% high".format(
        label, bit, n, interval_us, edges, 100.0 * sum(levels) / n)
    if edges:
        line += " -> {:.0f} Hz".format(odr)
    print(line)
    if show_wave:
        print("                " + "".join(str(v) for v in levels[:180]))
    return edges, odr, interval_us


def main():
    spi = SpiGpio()
    try:
        spi.write_out(CS_MASK)
        print("before: POWER_CTL 0x{:02X}, FILTER 0x{:02X}".format(
            read_reg(spi, REG_POWER_CTL), read_reg(spi, REG_FILTER)))

        write_reg(spi, REG_FILTER, 0x00)          # ODR 4000 Hz, done while still in Standby
        write_reg(spi, REG_POWER_CTL, 0x00)       # release Standby, DRDY_OFF cleared
        print("after : POWER_CTL 0x{:02X}, FILTER 0x{:02X} (want 00 and 00)\n".format(
            read_reg(spi, REG_POWER_CTL), read_reg(spi, REG_FILTER)))

        time.sleep(0.05)                          # let the decimation filter produce samples
        axes = read_g(spi)
        print("axes right after entering measure : X {:+.3f} Y {:+.3f} Z {:+.3f} g\n".format(*axes))

        print("DRDY window, {} reads each, {} rounds\n".format(SAMPLES, WINDOWS))
        res = [report_bit(spi, SAMPLES, BIT_DRDY, "window", show_wave=(i == 0))
               for i in range(WINDOWS)]
        edges_total = sum(r[0] for r in res)
        est = [r[1] for r in res if r[1] > 0.0]
        interval_us = res[-1][2]

        print("\nper-bit activity scan (3000 reads per bit)")
        scan = [report_bit(spi, 3000, b, "scan") for b in range(4)]
        live_bits = [b for b, r in zip(range(4), scan) if r[0] > 20]

        ratio = 125.0 / interval_us
        if edges_total > 0 and not est:
            print("\nVERDICT: DRDY edges exist but the rate estimate failed")
        elif edges_total > 0 and abs(sum(est) / len(est) - ODR_NOMINAL) < 0.3 * ODR_NOMINAL:
            print("\nVERDICT: DRDY pulses at {:.0f} Hz (half period sampled {:.1f}x) -> the PL "
                  "SPI master triggers on its edge and must finish the 0x08..0x10 burst inside "
                  "the remaining margin".format(sum(est) / len(est), ratio))
        elif edges_total > 0:
            print("\nVERDICT: DRDY pulses at an estimated {:.0f} Hz, far off 4000 Hz -> the "
                  "estimate is limited by {:.1f} us register reads; use the edge presence only "
                  "and confirm the rate in the PL".format(sum(est) / len(est), interval_us))
        elif live_bits:
            print("\nVERDICT: ja_i[{}] is silent but bits {} move -> DRDY is on another pin "
                  "than the PL design assumes; fix the pin mapping before writing RTL".format(
                      BIT_DRDY, live_bits))
        else:
            print("VERDICT: no ja_i bit moves while POWER_CTL = 0x00 -> DRDY is genuinely not "
                  "coming out; next checks are the ja_i bit-to-pin mapping in bringup.xdc and "
                  "whether the PMDZ jumper for DRDY is seated")
    finally:
        spi.close()


if __name__ == "__main__":
    main()
