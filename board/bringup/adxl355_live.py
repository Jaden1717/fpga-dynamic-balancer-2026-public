"""Decisive read-only check: is the ADXL355 ADC actually converting?

Field decodes are taken from the ADXL354/ADXL355 datasheet Rev. D (Table 15 register map,
Table 20 STATUS, Table 21 FIFO_ENTRIES, temperature section) and are quoted in the
knowledge doc. Two things follow from that table which earlier scripts got wrong:

  * Registers 0x12 to 0x1D DO NOT EXIST (map jumps 0x11 -> 0x1E). Draining the FIFO with a
    9-byte auto-increment burst therefore read FIFO_DATA once and eight phantom addresses.
  * STATUS is bit0 DATA_RDY, bit1 FIFO_FULL, bit2 FIFO_OVR, bit3 Activity, bit4 NVM_BUSY.
    The 0x07 seen on board = DATA_RDY + FIFO_FULL + FIFO_OVR, and FIFO_ENTRIES counts
    STORED samples (0 to 96), so 0x00 means empty, not full.

The temperature register (0x06/0x07, 12 bit) is the cleanest liveness probe: the ADC drives
it regardless of the accelerometer element, and a powered, converting part reads roughly
1885 LSB at 25 C with a slope of -9.05 LSB/C. Zero there means the read path, not the sensor.

Note: reading STATUS clears FIFO_OVR, so only the first STATUS of a run can be trusted for
that bit. Nothing in this script writes a register.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_live.py
"""
import time

from adxl355_burst import BLOCK_LEN, REG_BLOCK, burst_read, s24
from spi_bitbang_adxl355 import CS_MASK, SpiGpio, read_reg

REG_TEMP2, REG_TEMP1 = 0x06, 0x07
REG_XDATA, REG_YDATA, REG_ZDATA = 0x08, 0x0B, 0x0E
REG_POWER_CTL = 0x2D
STATUS_NAMES = ((4, "NVM_BUSY"), (3, "ACTIVITY"), (2, "FIFO_OVR"),
                (1, "FIFO_FULL"), (0, "DATA_RDY"))
TEMP_INTERCEPT = 1885.0      # LSB at 25 C
TEMP_SLOPE = -9.05           # LSB per degree C


def status_bits(val):
    return " ".join(name for bit, name in STATUS_NAMES if val & (1 << bit)) or "(none)"


def temp_c(raw12):
    return 25.0 + (raw12 - TEMP_INTERCEPT) / TEMP_SLOPE


def read_axes(spi):
    return [s24(read_reg(spi, r), read_reg(spi, r + 1), read_reg(spi, r + 2))
            for r in (REG_XDATA, REG_YDATA, REG_ZDATA)]


def main():
    spi = SpiGpio()
    try:
        spi.write_out(CS_MASK)
        power = read_reg(spi, REG_POWER_CTL)
        print("POWER_CTL readback = 0x{:02X} (bit0 Standby must be 0 = measuring)".format(power))

        blk = burst_read(spi, REG_BLOCK, BLOCK_LEN)   # 0x04..0x10, one CS-low transaction
        status, entries = blk[0], blk[1]
        raw_temp = ((blk[2] & 0x0F) << 8) | blk[3]
        print("STATUS 0x{:02X} -> {}".format(status, status_bits(status)))
        print("FIFO_ENTRIES {} stored samples".format(entries))
        print("raw 0x04..0x10 : " + " ".join("{:02X}".format(b) for b in blk))
        print("TEMP 12 bit = {} -> {:.1f} C".format(raw_temp, temp_c(raw_temp)))
        print("burst axes   X {:+9d}  Y {:+9d}  Z {:+9d}".format(
            s24(blk[4], blk[5], blk[6]), s24(blk[7], blk[8], blk[9]),
            s24(blk[10], blk[11], blk[12])))

        print("\n6 x single reads, 0.3 s apart")
        temps, axes = [], []
        for i in range(6):
            t = ((read_reg(spi, REG_TEMP2) & 0x0F) << 8) | read_reg(spi, REG_TEMP1)
            a = read_axes(spi)
            st = read_reg(spi, 0x04)
            temps.append(t)
            axes.append(a)
            print("  [{}] TEMP {:5d} ({:5.1f} C)  X {:+8d} Y {:+8d} Z {:+8d}  STATUS 0x{:02X}"
                  " {}".format(i, t, temp_c(t), a[0], a[1], a[2], st, status_bits(st)))
            time.sleep(0.3)

        print("\nFIFO pop: nine reads of 0x11 (FIFO advances every third byte; "
              "low byte bit1 = empty-read, bit0 = x-axis marker)")
        for k in range(3):
            w = [read_reg(spi, 0x11) for _ in range(3)]
            print("  word {} : {:02X} {:02X} {:02X}  as s24 {:+9d}  "
                  "empty={} xmark={}".format(
                      k, w[0], w[1], w[2], s24(w[0], w[1], w[2]), w[2] & 2, w[2] & 1))

        print("\nDATA_RDY bit, 12 reads 5 ms apart (4 kHz ODR -> expect toggling)")
        st = [read_reg(spi, 0x04) & 1 for _ in range(12)]
        print("  " + " ".join(str(v) for v in st))

        if power & 0x01:
            print("VERDICT: chip is sitting in Standby (POWER_CTL bit0 = 1) -> this script "
                  "is read only, so nothing can move. Have the next script write "
                  "POWER_CTL = 0x00 and read in the same run")
        elif any(abs(v) > 100000 for a in axes for v in a):
            print("VERDICT: axes carry real acceleration -> use 0x08..0x10 directly; the "
                  "FIFO drain just has to pop 0x11 once per 24-bit word")
        elif 500 < max(temps) < 3500:
            print("VERDICT: ADC converts (temperature looks sane) but axes stay ~0 -> the "
                  "accel datapath itself is dead, not the SPI read path")
        else:
            print("VERDICT: temperature is not sane (read {}) -> the read path for "
                  "0x06..0x10 is not returning chip data; IDs and config regs read fine, "
                  "so look at byte timing for the second/third data byte".format(temps[-1]))
    finally:
        spi.close()


if __name__ == "__main__":
    main()
