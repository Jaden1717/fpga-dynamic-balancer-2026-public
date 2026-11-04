"""Reset the ADXL355, enter measurement, then drain the FIFO back-to-back.

Why this order: once the FIFO overruns (STATUS 0x07) the ADXL355 stops storing new
samples until it is reset, so a chip left measuring with no reader keeps handing back
stale bytes -- that is what produced 00 00 02 x3 on 0x11 and zeros on 0x08. A soft
reset clears the FIFO state, and draining immediately shows whether the bit-bang loop
can outrun 4 kHz.

Budget: ODR 4 kHz, 96-entry FIFO, each 9-byte burst yields 3 samples, so one burst must
complete within 3 samples = 0.75 ms. If effective SPS lands below 4000 the overrun returns
and the read path has to move into the PL.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_stream.py
"""
import time

from adxl355_burst import FIFO_LEN, REG_FIFO, burst_read, s24
from adxl355_state import REG_POWER_CTL, REG_RESET, REG_STATUS, RESET_KEYS
from spi_bitbang_adxl355 import CS_MASK, SpiGpio, read_reg, write_reg

MEASURE_MODE = 0x00      # POWER_CTL mode bits 0b00 = measurement, internal DRDY bit off
ROUNDS = 200             # 3 samples each -> 600 samples, 0.15 s of data at 4 kHz
GRAVITY_LSB = 100000     # |1 g| shows up as ~2.5e5 LSB on whichever axis points down


def main():
    spi = SpiGpio()
    try:
        spi.write_out(CS_MASK)
        for key in RESET_KEYS:
            write_reg(spi, REG_RESET, key)
            time.sleep(0.001)
        write_reg(spi, REG_POWER_CTL, MEASURE_MODE)

        mins = [0, 0, 0]
        maxs = [0, 0, 0]
        status_seen = 0
        samples = 0
        t0 = time.time()
        for r in range(ROUNDS):
            raw = burst_read(spi, REG_FIFO, FIFO_LEN)
            trip = [s24(*raw[i:i + 3]) for i in (0, 3, 6)]
            if r < 3:
                print("burst {}: X {:+9d}  Y {:+9d}  Z {:+9d}".format(r, *trip))
            for a, v in enumerate(trip):
                mins[a] = min(mins[a], v)
                maxs[a] = max(maxs[a], v)
            samples += 3
            if r % 20 == 0:                      # STATUS read costs a whole extra
                status_seen |= read_reg(spi, REG_STATUS)   # transaction; sample it
        dt = time.time() - t0

        print("drained {} samples in {:.3f} s -> {:.0f} SPS (need >= 4000 to keep up)".format(
            samples, dt, samples / dt))
        for i, name in enumerate("XYZ"):
            print("{}: min {:+9d}  max {:+9d}".format(name, mins[i], maxs[i]))
        print("STATUS OR over the run = 0x{:02X}, FIFO_ENTRIES now 0x{:02X}".format(
            status_seen, read_reg(spi, 0x05)))

        if max(maxs) >= GRAVITY_LSB or min(mins) <= -GRAVITY_LSB:
            print("VERDICT: real acceleration coming out of the FIFO -> build the "
                  "acquisition loop on burst reads of 0x11")
        elif samples / dt < 4000:
            print("VERDICT: loop too slow, FIFO overran again -> the read path belongs "
                  "in the PL (SPI controller), Python polling cannot hold 4 kHz")
        else:
            print("VERDICT: kept up but still near zero -> FIFO is not the live path; "
                  "next step is to disable the FIFO and poll 0x08")
    finally:
        spi.close()


if __name__ == "__main__":
    main()
