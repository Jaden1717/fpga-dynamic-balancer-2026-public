"""ADXL355 identity, soft reset and measurement mode over the bit-bang SPI path.

Addresses and bit meanings copied from Linux drivers/iio/accel/adxl355_core.c:
    DEVID_AD 0x00 (0xAD), DEVID_MST 0x01 (0x1D), PARTID 0x02 (0xED), STATUS 0x04
    XDATA3 0x08, YDATA3 0x0B, ZDATA3 0x0E -- 3 bytes each, high byte first
    POWER_CTL 0x2D: mode bits[1:0] 0b00 = measurement, 0b01 = standby, 0b10 = startup;
                    bit[2] = DRDY pin enable
    RESET 0x2F: ADXL355_RESET_CODE 0x52, datasheet sequence 0x52 then 0xE3 then 0xAD
STATUS bit meanings are not asserted -- raw hex is printed for before/after comparison.
REVID has no expected constant in the driver, so it is printed only.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_state.py
"""
import time

from spi_bitbang_adxl355 import CS_MASK, OFF_CH2_DATA, SpiGpio, read_reg, write_reg

REG_DEVID, REG_STATUS, REG_POWER_CTL, REG_RESET = 0x00, 0x04, 0x2D, 0x2F
RESET_KEYS = (0x52, 0xE3, 0xAD)
# PMDZ pin 10 (DRDY) -> JA10 -> ja_i[3]
BIT_DRDY = 3

ID_TABLE = (
    (REG_DEVID, "DEVID_AD", 0xAD),
    (0x01, "DEVID_MST", 0x1D),
    (0x02, "PARTID", 0xED),
    (0x03, "REVID", None),
    (REG_STATUS, "STATUS", None),
)


def poll_drdy(spi, ms, step=0.001):
    """Count ja_i[3] edges for `ms` ms.

    Bit-bang sampling is far slower than the 4 kHz ODR, so the pin aliases: what
    matters is whether the level moves at all, not the exact count.
    """
    levels = []
    deadline = time.time() + ms / 1000.0
    while time.time() < deadline:
        levels.append((spi._rd(OFF_CH2_DATA) >> BIT_DRDY) & 1)
        time.sleep(step)
    edges = sum(a != b for a, b in zip(levels, levels[1:]))
    return edges, len(levels), levels[0], levels[-1]


def dump_ids(spi):
    for reg, name, expect in ID_TABLE:
        val = read_reg(spi, reg)
        if expect is None:
            print("{:<10} (0x{:02X}) = 0x{:02X}".format(name, reg, val))
        else:
            print("{:<10} (0x{:02X}) = 0x{:02X} {} expect 0x{:02X}".format(
                name, reg, val, "OK" if val == expect else "FAIL", expect))


def dump_axes(spi):
    for reg, name in ((0x08, "X"), (0x0B, "Y"), (0x0E, "Z")):
        print("{:<2} = 0x{:02X} {:02X} {:02X}".format(
            name, read_reg(spi, reg), read_reg(spi, reg + 1), read_reg(spi, reg + 2)))


def main():
    spi = SpiGpio()
    try:
        print("overlay: gpio_ja @0x{:08X}".format(spi.base))
        spi.write_out(CS_MASK)                      # idle: CS high
        dump_ids(spi)

        for key in RESET_KEYS:                      # soft reset, three writes to 0x2F
            write_reg(spi, REG_RESET, key)
            time.sleep(0.001)
        time.sleep(0.01)
        print("RESET readback = 0x{:02X} (0x00 means the reset took)".format(
            read_reg(spi, REG_RESET)))

        standby = read_reg(spi, REG_POWER_CTL)
        print("POWER_CTL after reset = 0x{:02X}".format(standby))
        edges0, n0, f0, l0 = poll_drdy(spi, 200)
        print("standby : DRDY edges {}/{} samples, first {} last {}".format(
            edges0, n0, f0, l0))

        write_reg(spi, REG_POWER_CTL, 0x04)         # measurement + DRDY pin on
        time.sleep(0.02)
        print("POWER_CTL measure   = 0x{:02X} (expect 0x04)".format(
            read_reg(spi, REG_POWER_CTL)))
        edges1, n1, f1, l1 = poll_drdy(spi, 200)
        print("measure : DRDY edges {}/{} samples, first {} last {}".format(
            edges1, n1, f1, l1))

        dump_axes(spi)
        if edges1 > 0:
            print("DRDY moves -> the datapath is producing samples")
        else:
            print("DRDY stuck at {} while measuring: POWER_CTL bit2 may not route DRDY"
                  " here, or PMDZ pin 10 is not wired to JA10. Judge on the axis "
                  "registers instead.".format(l1))
    finally:
        spi.close()


if __name__ == "__main__":
    main()
