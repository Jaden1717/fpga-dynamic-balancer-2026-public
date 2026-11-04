"""Time how long 0x08..0x10 stays zero after the part leaves Standby.

Board evidence (2026-10-05 15:0x, adxl355_start.py): after writing FILTER = 0x00 and
POWER_CTL = 0x00, DRDY pulsed at 4031 Hz with 49.4% duty and ja_i[3] was the only moving
bit, so the chip is unquestionably out of standby and sampling at 4 kHz. Yet the axis
registers, read about 100 ms after that write, were exactly +0.000 g. Datasheet Table 1
specs turn on time (standby to measurement, output within 1 mg of final value, 2 g range)
at < 10 ms, so the documented startup does not explain these zeros.

The same signature appeared twice before: adxl355_live.py returned four zero rows and only
then 1 g, and adxl355_stream.py never saw non-zero data at all (it only ever read 0x11). So
this is reproducible, and the PL read state machine needs a number on it: how many frames
to discard, or how long to wait, before the first valid sample.

Method: enter measurement from standby, then repeatedly burst read 0x04..0x10 in one
transaction (STATUS, FIFO_ENTRIES, TEMP, X, Y, Z). A burst costs about 32 ms bit-banged, so
a 6 s window gives roughly 100 polls -- far more time resolution than the effect needs.
Temperature is decoded in the same loop on purpose: it comes from the same ADC, so if TEMP
is sane while the axes are zero, the gating sits in the accelerometer datapath, not in the
converter or the SPI read path. FIFO_ENTRIES is recorded as well, to see whether the FIFO
fills before or at the moment the axes start carrying data.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_warm.py
"""
import time

from adxl355_burst import BLOCK_LEN, REG_BLOCK, burst_read, s24
from adxl355_state import REG_POWER_CTL
from spi_bitbang_adxl355 import CS_MASK, SpiGpio, read_reg, write_reg

REG_FILTER = 0x28
DEADLINE_S = 6.0
POLL_LIMIT = 200
LSB_PER_G = 256000.0
TEMP_INTERCEPT = 1885.0
TEMP_SLOPE = -9.05


def decode(blk):
    """One burst of 0x04..0x10 into status, entries, temperature, and three axis words."""
    axes = [s24(blk[4], blk[5], blk[6]) >> 4,
            s24(blk[7], blk[8], blk[9]) >> 4,
            s24(blk[10], blk[11], blk[12]) >> 4]
    return blk[0], blk[1], ((blk[2] & 0x0F) << 8) | blk[3], axes


def main():
    spi = SpiGpio()
    try:
        spi.write_out(CS_MASK)
        print("before: POWER_CTL 0x{:02X}, FILTER 0x{:02X}".format(
            read_reg(spi, REG_POWER_CTL), read_reg(spi, REG_FILTER)))
        write_reg(spi, REG_FILTER, 0x00)
        write_reg(spi, REG_POWER_CTL, 0x00)

        t0 = time.perf_counter()
        first = None
        polls = 0
        seen_entries = []
        while time.perf_counter() - t0 < DEADLINE_S and polls < POLL_LIMIT:
            status, entries, temp, axes = decode(burst_read(spi, REG_BLOCK, BLOCK_LEN))
            polls += 1
            seen_entries.append(entries & 0x7F)
            if first is None and any(axes):
                first = (time.perf_counter() - t0, polls, status, entries & 0x7F, temp, axes)
            if polls % 10 == 0 or (first is not None and polls == first[1]):
                print("poll {:4d} t {:5.3f} s  STATUS 0x{:02X} ENTRIES {:3d}  TEMP {:5d} "
                      "({:5.1f} C)  X {:+8d} Y {:+8d} Z {:+8d}".format(
                          polls, time.perf_counter() - t0, status, entries & 0x7F, temp,
                          25.0 + (temp - TEMP_INTERCEPT) / TEMP_SLOPE, *axes))

        elapsed = time.perf_counter() - t0
        print("\n{} bursts in {:.2f} s (limit {} polls / {:.0f} s)".format(
            polls, elapsed, POLL_LIMIT, DEADLINE_S))
        print("FIFO_ENTRIES seen: " + " ".join(str(v) for v in seen_entries[:40]))
        if first:
            t, n, status, entries, temp, axes = first
            print("first non-zero axes: poll {} at t = {:.3f} s ({:.0f} ms), STATUS 0x{:02X}, "
                  "ENTRIES {}, then X {:+d} Y {:+d} Z {:+d} LSB".format(
                      n, t, t * 1000.0, status, entries, *axes))
            print("VERDICT: the part needs about {:.0f} ms ({} polled frames, {:.0f} samples at "
                  "4 kHz) after releasing Standby before 0x08..0x10 carries data -> the PL "
                  "controller discards that many frames before publishing the first "
                  "sample".format(t * 1000.0, n, t * 4000.0))
        else:
            print("VERDICT: axes stayed zero for {:.2f} s ({}) while DRDY keeps ticking -> the "
                  "mirror is not the live path here; next test must interleave 0x11 pops with "
                  "axis bursts".format(elapsed, "poll limit hit" if polls >= POLL_LIMIT
                                       else "deadline hit"))
        if seen_entries and max(seen_entries) == 0:
            print("NOTE: FIFO_ENTRIES never left 0 in this window -> the FIFO write side is "
                  "gated exactly like the axes, so this is one effect, not two")
    finally:
        spi.close()


if __name__ == "__main__":
    main()
