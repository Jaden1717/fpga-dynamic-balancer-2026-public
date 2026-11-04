"""ADXL355 bring-up on TUL PYNQ-Z2 via bringup.xsa overlay, bit-bang SPI on JA PMOD.

Wiring: PMDZ P1 plugs straight into JA (PMDZ pin n -> JA pin n, 1 pin beside silkscreen P1).
    PMDZ 1=CS->JA1  2=MOSI->JA2  3=MISO->JA3  4=SCLK->JA4  5=GND->JA5  6=VDD->JA6(3.3V)
    PMDZ 7=INT1->JA7  9=INT2->JA9  10=DRDY->JA10 (inputs, unused in this bring-up)

JA pin -> package pin, from the TUL PYNQ-Z2 board file part0_pins.xml:
    JA1=Y18  JA2=Y19  JA3=Y16  JA4=Y17  JA7=U18  JA8=U19  JA9=W18  JA10=W19

Overlay (design_1.hwh unpacked from board/bringup/bringup.xsa):
    gpio_ja = xilinx.com:ip:axi_gpio:2.0, C_IS_DUAL=1, C_GPIO_WIDTH=3 (ja_o[2:0] outputs),
    C_GPIO2_WIDTH=4 (ja_i[3:0] inputs), C_ALL_OUTPUTS=1, C_ALL_INPUTS_2=1,
    C_INTERRUPT_PRESENT=0, C_BASEADDR=0x41200000, C_HIGHADDR=0x4120FFFF.
    Direction is baked into the IP config, so the TRI registers need no writes.

PYNQ 3.0.1 ip_dict entry keys are device / type / parameters / registers / interrupts /
    gpio / phys_addr / addr_range / state / driver. There is NO 'properties' key -- that
    is the raw .hwh spelling, not what PYNQ hands back -- and the base address is an int
    under 'phys_addr'. pynq.MMIO(base, length) is the supported access path (DefaultIP
    itself builds one from exactly these two fields).

SPI command byte = (reg << 1) | R/W, R/W = 1 for read (Linux iio adxl355_spi.c regmap:
    reg_bits=7, pad_bits=1, read_flag_mask=BIT(0); ADI ChibiOS driver: commtxp[0] =
    (reg << 1) | ADXL355_RW). Reading DEVID (0x00) therefore sends 0x01. The first
    version sent the ADXL345-style 0x80 | reg, which the chip decodes as a *write* to
    register 0x40 -- out of range, so it never drives MISO and every byte reads 0xFF.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 spi_bitbang_adxl355.py
               (plain `sudo python3` loses the xilinx user's pynq install -> ModuleNotFoundError)
"""
import os
import time

from pynq import MMIO, Overlay

XSA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bringup.xsa")

IP_NAME = "gpio_ja"
MAP_LEN = 0x1000

# ja_o bit positions (channel 1): CS=JA1, MOSI=JA2, SCLK=JA4
BIT_CS, BIT_MOSI, BIT_SCLK = 0, 1, 2
CS_MASK = 1 << BIT_CS
JA_O_MASK = 0x7
# ja_i bit position (channel 2): ja_i[0]=JA3=MISO
BIT_MISO = 0

# Xilinx AXI GPIO register map (PG144). The overlay has C_IS_DUAL=1, so the
# channel-2 registers sit at 0x08/0x0C -- NOT at 0x04/0x08.
OFF_CH1_DATA, OFF_CH1_TRI = 0x00, 0x04
OFF_CH2_DATA, OFF_CH2_TRI = 0x08, 0x0C

REG_DEVID = 0x00
ADXL355_DEVID = 0xAD

SCLK_DELAY = 0.000002                          # ~2 us half period -> ~250 kHz SCLK


def _as_int(value):
    """Accept an int, or the '0x41200000' / '41200000' strings the .hwh uses."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text, 10)


class SpiGpio:
    """Memory-map the dual-channel AXI GPIO: ch1 data @0x0 (out), ch2 data @0x8 (in)."""

    def __init__(self):
        ol = Overlay(XSA)
        if IP_NAME not in ol.ip_dict:
            raise RuntimeError("no {!r} in overlay; ip_dict holds {}".format(
                IP_NAME, sorted(ol.ip_dict)))
        ip = ol.ip_dict[IP_NAME]
        ip_type = str(ip.get("type", ""))
        if ip_type and "axi_gpio" not in ip_type:
            raise RuntimeError("{} is {!r}, expected an axi_gpio".format(IP_NAME, ip_type))

        base = ip.get("phys_addr")                # PYNQ 3.x: int
        if base is None:                          # older/newer spellings, just in case
            base = ip.get("physical_addr") or ip.get("address")
        if base is None:
            base = ip["parameters"]["C_BASEADDR"]
        self.base = _as_int(base)
        self.mmio = MMIO(self.base, MAP_LEN)

    def _rd(self, off):
        return self.mmio.read(off)

    def write_out(self, val):
        self.mmio.write(OFF_CH1_DATA, val & JA_O_MASK)

    def read_in(self):
        return self._rd(OFF_CH2_DATA)

    def dump_regs(self):
        return ("ch1_data=0x{:08X} ch1_tri=0x{:08X} "
                "ch2_data=0x{:08X} ch2_tri=0x{:08X}").format(
                    self._rd(OFF_CH1_DATA), self._rd(OFF_CH1_TRI),
                    self._rd(OFF_CH2_DATA), self._rd(OFF_CH2_TRI))

    def close(self):
        close = getattr(self.mmio, "close", None)   # MMIO has no close() on 3.0.1
        if close:
            close()


def xfer_byte(spi, out_byte, base):
    """Clocked SPI byte, MSB first. `base` must already hold CS asserted (low).

    SCLK idles low inside a transaction (SPI mode 0): MOSI is driven while SCLK is
    low, the slave samples on the rising edge, MISO is read right after that edge.
    """
    in_byte = 0
    sclk = 1 << BIT_SCLK
    mosi = 1 << BIT_MOSI
    for i in range(7, -1, -1):
        bit = (out_byte >> i) & 1
        level = base | (mosi if bit else 0)
        spi.write_out(level)                      # drive MOSI with SCLK low
        time.sleep(SCLK_DELAY)
        spi.write_out(level | sclk)                # rising edge: slave samples, we read
        in_byte = (in_byte << 1) | ((spi.read_in() >> BIT_MISO) & 1)
        time.sleep(SCLK_DELAY)
    spi.write_out(base)                           # leave SCLK low
    return in_byte


def read_reg(spi, reg):
    """16-bit ADXL355 read: CS must stay LOW for both bytes, one transaction."""
    spi.write_out(CS_MASK)                        # CS high: idle
    time.sleep(SCLK_DELAY)
    spi.write_out(0)                              # CS low: transaction starts
    xfer_byte(spi, ((reg & 0x7F) << 1) | 0x01, 0)  # 7-bit addr <<1 | R/W=1
    val = xfer_byte(spi, 0x00, 0)                 # data byte, CS still low
    spi.write_out(CS_MASK)                        # CS high: transaction ends
    time.sleep(SCLK_DELAY)
    return val


def write_reg(spi, reg, val):
    """16-bit ADXL355 write: same framing as read_reg with R/W = 0."""
    spi.write_out(CS_MASK)
    time.sleep(SCLK_DELAY)
    spi.write_out(0)
    xfer_byte(spi, (reg & 0x7F) << 1, 0)          # 7-bit addr <<1 | R/W=0
    xfer_byte(spi, val & 0xFF, 0)
    spi.write_out(CS_MASK)
    time.sleep(SCLK_DELAY)


def main():
    spi = SpiGpio()
    try:
        print("overlay: gpio_ja axi_gpio @0x{:08X}, MMIO {} B".format(spi.base, MAP_LEN))
        spi.write_out(JA_O_MASK)                  # CS=1 MOSI=1 SCLK=1
        echo = spi._rd(OFF_CH1_DATA) & JA_O_MASK
        print("echo   : wrote 0x7 to ja_o, read back 0x{:X} (expect 0x7)".format(echo))
        spi.write_out(CS_MASK)                    # idle: CS high, SCLK/MOSI low
        print("regs   : " + spi.dump_regs())
        devid = read_reg(spi, REG_DEVID)
        print("DEVID = 0x{:02X} (expect 0x{:02X})".format(devid, ADXL355_DEVID))
        if devid == ADXL355_DEVID:
            print("ADXL355 SPI link OK")
        else:
            print("Check wiring/power: CS=JA1 MOSI=JA2 MISO=JA3 SCLK=JA4 "
                  "VDD=JA6(3.3V) GND=JA5")
    finally:
        spi.close()


if __name__ == "__main__":
    main()
