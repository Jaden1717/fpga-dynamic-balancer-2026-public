"""Burst-read the ADXL355: compare the FIFO path (0x11) against the data registers (0x08).

Evidence from adxl355_diag.py: in measurement mode STATUS = 0x07 (FIFO watermark and
overrun flags set) while the 0x08 registers read 0x00 and FIFO_ENTRIES reads 0x00.
That combination means the ADC is producing samples into the FIFO and nothing drains
it -- so the live path is FIFO_DATA 0x11, which must be read as one burst with CS held
low; byte-wise reads with CS toggling reset the FIFO read pointer.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_burst.py
"""
import time

from spi_bitbang_adxl355 import CS_MASK, SCLK_DELAY, SpiGpio, xfer_byte

REG_BLOCK, BLOCK_LEN = 0x04, 13   # STATUS, FIFO_ENTRIES, 0x06/07, X, Y, Z triplets
REG_FIFO, FIFO_LEN = 0x11, 9      # three 24-bit axis triplets per sample


def burst_read(spi, reg, length):
    """Command byte then `length` data bytes in a single CS-low transaction."""
    spi.write_out(CS_MASK)
    time.sleep(SCLK_DELAY)
    spi.write_out(0)
    xfer_byte(spi, ((reg & 0x7F) << 1) | 0x01, 0)
    data = [xfer_byte(spi, 0x00, 0) for _ in range(length)]
    spi.write_out(CS_MASK)
    time.sleep(SCLK_DELAY)
    return data


def s24(hi, mid, lo):
    v = (hi << 16) | (mid << 8) | lo
    return v - (1 << 24) if v & 0x800000 else v


def main():
    spi = SpiGpio()
    try:
        spi.write_out(CS_MASK)
        for i in range(10):
            blk = burst_read(spi, REG_BLOCK, BLOCK_LEN)
            fifo = burst_read(spi, REG_FIFO, FIFO_LEN)
            print("#{:<2} STATUS=0x{:02X} ENTRIES=0x{:02X} | 0x08 X/Y/Z = {:+9d} "
                  "{:+9d} {:+9d} | 0x11 X/Y/Z = {:+9d} {:+9d} {:+9d}".format(
                      i, blk[0], blk[1],
                      s24(*blk[4:7]), s24(*blk[7:10]), s24(*blk[10:13]),
                      s24(*fifo[0:3]), s24(*fifo[3:6]), s24(*fifo[6:9])))
            time.sleep(0.05)
        print("Expected once drained: one axis near 2.5e5 LSB at rest (gravity),")
        print("numbers moving sample to sample, STATUS overrun bits clearing.")
    finally:
        spi.close()


if __name__ == "__main__":
    main()
