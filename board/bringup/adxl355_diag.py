"""Read-only ADXL355 diagnostics: is the datapath actually producing samples?

Addresses and reset values quoted from ADI no-OS adxl355.h (SET_ADXL355_RESET_VAL)
and Linux adxl355_core.c:
    STATUS 0x04 = 0x00, FIFO_ENTRIES 0x05, XDATA3 0x08 / YDATA3 0x0B / ZDATA3 0x0E,
    FIFO_DATA 0x11, FILTER 0x28 = 0x00 (ODR code 0 = 4000 Hz), FIFO_SAMPLES 0x29,
    RANGE 0x2C = 0x81, POWER_CTL 0x2D = 0x01 (standby), SELF_TEST 0x2E = 0x00
The interrupt/threshold block is dumped without names -- not verified against a source.
No register is written here; this only watches.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_diag.py
"""
import time

from spi_bitbang_adxl355 import CS_MASK, OFF_CH2_DATA, SpiGpio, read_reg

CONFIG = ((0x04, "STATUS"), (0x05, "FIFO_ENTRIES"), (0x28, "FILTER_ODR"),
          (0x29, "FIFO_SAMPLES"), (0x2C, "RANGE"), (0x2D, "POWER_CTL"),
          (0x2E, "SELF_TEST"))
AXIS = ((0x08, "X"), (0x0B, "Y"), (0x0E, "Z"))
UNVERIFIED = tuple(range(0x18, 0x28))


def read_axis(spi, reg):
    """24-bit two's complement from three consecutive high/mid/low bytes."""
    v = (read_reg(spi, reg) << 16) | (read_reg(spi, reg + 1) << 8) | read_reg(spi, reg + 2)
    return v - (1 << 24) if v & 0x800000 else v


def read_axes(spi):
    return [read_axis(spi, reg) for reg, _ in AXIS]


def main():
    spi = SpiGpio()
    try:
        spi.write_out(CS_MASK)                       # CS idle high
        for reg, name in CONFIG:
            print("{:<13} (0x{:02X}) = 0x{:02X}".format(name, reg, read_reg(spi, reg)))
        print("unnamed 0x18-0x27: " + " ".join(
            "{:02X}:{:02X}".format(r, read_reg(spi, r)) for r in UNVERIFIED))

        first = read_axes(spi)
        time.sleep(0.5)
        second = read_axes(spi)
        for i, (_, name) in enumerate(AXIS):
            print("{:<2} = {:+9d} -> {:+9d}".format(name, first[i], second[i]))

        entries = []
        for _ in range(20):
            entries.append(read_reg(spi, 0x05))
            time.sleep(0.02)
        print("FIFO_ENTRIES x20/0.4 s: " + " ".join(str(e) for e in entries))
        print("FIFO_DATA 0x11..0x13 = {:02X} {:02X} {:02X}".format(
            read_reg(spi, 0x11), read_reg(spi, 0x12), read_reg(spi, 0x13)))
        print("ja_i = 0x{:08X}  (bit3 = DRDY on JA10)".format(spi._rd(OFF_CH2_DATA)))

        if max(entries) != min(entries):
            print("VERDICT: FIFO counter moves -> ADC/filter is running; read 0x11, "
                  "the 0x08 data registers are not the live path here")
        elif second != first:
            print("VERDICT: data registers update -> datapath alive, only the DRDY pin "
                  "is silent (interrupt enable/routing not configured)")
        else:
            print("VERDICT: nothing moves -> datapath idle; next step is an explicit "
                  "FILTER/ODR write and STARTUP mode, not wiring")
    finally:
        spi.close()


if __name__ == "__main__":
    main()
