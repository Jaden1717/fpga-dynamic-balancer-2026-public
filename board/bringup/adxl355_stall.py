"""Prove the sample-stall mechanism and lock the read protocol for the PL design.

Board evidence (2026-10-05 14:0x, adxl355_live.py): with POWER_CTL = 0x00 the first four
axis reads were 0x000000, then rows 4 and 5 gave X -267504, Y -202784, Z +4133712. Those are
20-bit values LEFT JUSTIFIED in 24 bits (datasheet: XDATA1 holds bits[3:0] in its upper
nibble), so the real sample is word >> 4 -> Z = 258357 = 1.009 g at 256000 LSB/g on the
+/-2 g range (RANGE 0x81 -> bits[1:0] = 01), X = -0.065 g, Y = -0.049 g. A flat board reads
one g on the vertical axis, so the SPI path, the ADC and the decode are all confirmed.

What is not yet pinned down is why four reads returned zero. The plausible mechanism: the
FIFO fills in 96/4000 = 24 ms and overruns, while FIFO_OVR is set the write pointer stops,
and 0x08..0x10 mirror the newest FIFO set -> the axes FREEZE at stale values. Reading STATUS
clears FIFO_OVR (datasheet) and writing resumes. The live script happened to read STATUS
once per iteration, which is exactly when the values came back.

Three phases decide it. Each reads only the axis registers plus (per phase) the clearing
action, and reports how often consecutive samples differ:

  1. no STATUS read, no FIFO pop  -> frozen (1 distinct value) means the stall is real
  2. STATUS read every iteration  -> values must move
  3. FIFO_DATA popped every iter  -> also must move; that is the PL-side drain model

Read only, nothing is written, so the chip stays in whatever mode it is already in.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_stall.py
"""
import time

from spi_bitbang_adxl355 import CS_MASK, SpiGpio, read_reg

AXIS_REGS = ((0x08, 0x09, 0x0A), (0x0B, 0x0C, 0x0D), (0x0E, 0x0F, 0x10))
REG_STATUS, REG_FIFO = 0x04, 0x11
LSB_PER_G = 256000          # +/-2 g range, datasheet Table 2 typ value
ROUNDS = 24                 # no sleep between reads; one phase is well under a second


def s24(hi, mid, lo):
    v = (hi << 16) | (mid << 8) | lo
    return v - (1 << 24) if v & 0x800000 else v


def read_g(spi):
    """One XYZ sample in g. The 20-bit word is left justified: shift the low 4 bits off."""
    out = []
    for r in AXIS_REGS:
        raw = s24(read_reg(spi, r[0]), read_reg(spi, r[1]), read_reg(spi, r[2]))
        out.append((raw >> 4) / LSB_PER_G)
    return out


def phase(spi, name, action, rounds=ROUNDS):
    seen, zeros, max_delta, prev = set(), 0, 0.0, None
    t0 = time.time()
    for _ in range(rounds):
        sample = read_g(spi)
        action(spi)
        seen.add(tuple(sample))
        if not any(sample):
            zeros += 1
        if prev is not None:
            max_delta = max(max_delta, max(abs(a - b) for a, b in zip(sample, prev)))
        prev = sample
    dt = time.time() - t0
    print("{:22s} {:3d} reads -> {:2d} distinct samples, {:2d} all-zero, "
          "max step {:.3f} g, {:.0f} samples/s".format(
              name, rounds, len(seen), zeros, max_delta, rounds / dt))
    print("                       last sample X {:+.3f} Y {:+.3f} Z {:+.3f} g".format(*sample))
    return len(seen), zeros


def main():
    spi = SpiGpio()
    try:
        spi.write_out(CS_MASK)
        print("POWER_CTL = 0x{:02X}, STATUS = 0x{:02X}, RANGE = 0x{:02X} (01 = +/-2 g)".format(
            read_reg(spi, 0x2D), read_reg(spi, REG_STATUS), read_reg(spi, 0x2C)))
        print("tap or tilt the board ONLY during phase 3\n")

        moved = phase(spi, "1 no clear at all", lambda s: None)
        cleared = phase(spi, "2 STATUS each read", lambda s: read_reg(s, REG_STATUS))
        drained = phase(spi, "3 FIFO pop x9",
                        lambda s: [read_reg(s, REG_FIFO) for _ in range(9)], rounds=150)

        for label, (distinct, zeros) in zip(
                ("stall", "status-clear", "fifo-drain"), (moved, cleared, drained)):
            print("  {:12s}: {:2d} distinct, {:2d} zero".format(label, distinct, zeros))

        if moved[0] == 1 and max(cleared[0], drained[0]) > 1:
            print("VERDICT: samples freeze unless the overrun is cleared -> the PL read "
                  "state machine must pop FIFO_DATA (or read STATUS) once per sample")
        elif moved[0] > 1:
            print("VERDICT: samples move with no clearing at all -> plain 0x08..0x10 "
                  "polling is enough, the FIFO needs no servicing")
        else:
            print("VERDICT: nothing moves in any phase -> the stall is not the overrun; "
                  "next test is standby -> explicit FILTER/ODR write -> measurement")
    finally:
        spi.close()


if __name__ == "__main__":
    main()
