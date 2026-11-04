"""Drive the PL acquisition overlay (build/overlay/acq.xsa) on TUL PYNQ-Z2.

Why the PL has to own the bus: measured on this board, one /dev/mem read of the
axi_gpio input register costs 18.7 us, a bit-bang ADXL355 register read costs
4935 us, and a 13-byte burst costs 32244 us -- while the 4 kHz ODR gives every
sample a budget of T_s = 250 us. The PS is about 130 times too slow, so here the
PS only configures the part and reads results out of BRAM.

Register map (32 KB window, the same base the bit-bang overlay used):
    0x00 RAM      4*(3*slot + {X,Y,Z}), slot 0..1364, values 20-bit signed
    0x4000 CTRL   write bit0 enable, bit1 restart (clears ring counters)
    0x4004 STATUS bit0 busy, bit1 data_valid, bit2 drdy_level, bit3 dropped
    0x4008 SAMPLE_COUNT   0x400C DISCARD_COUNT  0x4010 WRITE_SLOT  0x4014 WRAP_COUNT
    0x4018 DISCARD_LIMIT  0x401C LAST_STATUS    0x4020 LAST_ENTRIES
    0x4024 LAST_TEMP      0x4028/2C/30 LAST_X/Y/Z
    0x4034 SPI_TX write {bit31 start, bit16 burst, bit[15:8] data, bit[7:0] command}
    0x4038 SPI_RX read  last byte of a manual read
    0x403C SPI_STAT read bit0 done, bit1 refused (both sticky, cleared by the next request)

The command byte goes through literally as (reg << 1) | R/W, so this script computes it
exactly like spi_bitbang_adxl355.py did. Manual transactions are only accepted while
CTRL enable = 0: writing ADXL355 registers while it measures is what produced the
5.161 s window of zero samples measured on 2026-10-05, and the RTL refuses it.

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_pl_acq.py
"""
import os
import time


XSA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acq.xsa")
IP_NAME = "acq"
MAP_LEN = 0x8000

OFF_RAM = 0x0000
OFF_CTRL, OFF_STATUS = 0x4000, 0x4004
OFF_SAMPLE_COUNT, OFF_DISCARD_COUNT = 0x4008, 0x400C
OFF_WRITE_SLOT, OFF_WRAP_COUNT, OFF_DISCARD_LIMIT = 0x4010, 0x4014, 0x4018
OFF_LAST_STATUS, OFF_LAST_ENTRIES, OFF_LAST_TEMP = 0x401C, 0x4020, 0x4024
OFF_LAST_X, OFF_LAST_Y, OFF_LAST_Z = 0x4028, 0x402C, 0x4030
OFF_SPI_TX, OFF_SPI_RX, OFF_SPI_STAT = 0x4034, 0x4038, 0x403C

BIT_ENABLE, BIT_RESTART = 0, 1
BIT_BUSY, BIT_VALID, BIT_DRDY, BIT_DROPPED = 0, 1, 2, 3

REG_DEVID, REG_STATUS, REG_TEMP2, REG_TEMP1 = 0x00, 0x04, 0x06, 0x07
REG_FILTER, REG_RANGE, REG_POWER_CTL, REG_RESET = 0x28, 0x2C, 0x2D, 0x2F
RESET_KEYS = (0x52, 0xE3, 0xAD)
ADXL355_DEVID = 0xAD
LSB_PER_G = 256000.0
TEMP_INTERCEPT = 1885.0     # 12-bit LSB at 25 C, and 1774 read on the board at 37.3 C
TEMP_SLOPE = -9.05          # LSB per degree C
NSAMP = 1365                      # RAM_DEPTH 4096 / 3, from build/rtl/adxl355_acq.v
FS = 4000.0                       # FILTER = 0x00 -> ODR 4 kHz


def s20(word):
    """The RTL stores the decoded axis sign extended into 32 bits."""
    v = word & 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


class Acq:
    """Memory-map adxl355_acq: register window plus the sample RAM."""

    def __init__(self, xsa=None, overlay=None):
        # Lazy import permits offline parser/tests; reuse avoids a second PL reset.
        from pynq import MMIO, Overlay
        ol = overlay if overlay is not None else Overlay(XSA if xsa is None else xsa)
        self.overlay = ol
        ip = None
        if IP_NAME in ol.ip_dict:
            ip = ol.ip_dict[IP_NAME]
        else:
            # a Verilog module reference is not always named what the block design says
            cands = [v for v in ol.ip_dict.values()
                     if str(v.get("type", "")).find("adxl355_acq") >= 0]
            if len(cands) == 1:
                ip = cands[0]
        if ip is None:
            raise RuntimeError("no adxl355_acq in overlay; ip_dict holds {}".format(
                sorted(ol.ip_dict)))
        base = ip.get("phys_addr")
        if base is None:
            base = ip.get("address") or ip["parameters"]["C_BASEADDR"]
        self.base = base if isinstance(base, int) else int(str(base), 16)
        self.mmio = MMIO(self.base, MAP_LEN)

    def rd(self, off):
        return self.mmio.read(off)

    def wr(self, off, val):
        self.mmio.write(off, val & 0xFFFFFFFF)

    def status(self):
        return self.rd(OFF_STATUS)

    def busy(self):
        return (self.status() >> BIT_BUSY) & 1

    def wait_idle(self, us=2000):
        deadline = time.perf_counter() + us * 1e-6
        while self.busy():
            if time.perf_counter() > deadline:
                raise RuntimeError("acquisition controller stuck busy")
        return True

    def spi_txn(self, cmd, data=0, burst=False):
        """One delegated transaction; returns the read-back byte or None."""
        word = (1 << 31) | (data << 8) | cmd
        if burst:
            word |= 1 << 16
        self.wr(OFF_SPI_TX, word)
        deadline = time.perf_counter() + 0.05
        while True:
            stat = self.rd(OFF_SPI_STAT)
            if stat & 0b10:
                raise RuntimeError("SPI request cmd 0x{:02X} refused: controller enabled"
                                   .format(cmd))
            if stat & 0b01:
                return self.rd(OFF_SPI_RX) & 0xFF if (cmd & 1) else None
            if time.perf_counter() > deadline:
                raise RuntimeError("SPI request cmd 0x{:02X} never completed".format(cmd))

    def spi_read(self, reg):
        return self.spi_txn(((reg & 0x7F) << 1) | 0x01)

    def spi_write(self, reg, val):
        self.spi_txn(((reg & 0x7F) << 1) | 0x00, val)

    def read_sample(self, slot):
        """(X, Y, Z) raw 20-bit words of one ring slot."""
        base = OFF_RAM + 4 * 3 * (slot % NSAMP)
        return (s20(self.rd(base)), s20(self.rd(base + 4)), s20(self.rd(base + 8)))


def configure(acq):
    """The legal bring-up sequence, everything through the delegated channel."""
    devid = acq.spi_read(REG_DEVID)
    print("DEVID via PL = 0x{:02X} {}".format(devid, "OK" if devid == ADXL355_DEVID else "FAIL"))
    if devid != ADXL355_DEVID:
        raise RuntimeError("no ADXL355 behind the PL SPI master")

    for key in RESET_KEYS:                       # soft reset, then it sits in Standby
        acq.spi_write(REG_RESET, key)
    time.sleep(0.01)
    pctl = acq.spi_read(REG_POWER_CTL)
    print("after reset   : POWER_CTL 0x{:02X} (expect 0x01 = Standby)".format(pctl))

    acq.spi_write(REG_FILTER, 0x00)              # ODR 4 kHz, written while in Standby
    acq.spi_write(REG_RANGE, 0x81)               # auto wake, +/- 2 g
    print("config        : FILTER 0x{:02X} RANGE 0x{:02X}".format(
        acq.spi_read(REG_FILTER), acq.spi_read(REG_RANGE)))
    acq.spi_write(REG_POWER_CTL, 0x00)           # measurement plus DRDY output enabled
    print("enter measure : POWER_CTL 0x{:02X}".format(acq.spi_read(REG_POWER_CTL)))


def main():
    acq = Acq()
    print("overlay: adxl355_acq @0x{:08X}, DISCARD_LIMIT {}".format(
        acq.base, acq.rd(OFF_DISCARD_LIMIT)))
    configure(acq)

    acq.wr(OFF_CTRL, (1 << BIT_ENABLE) | (1 << BIT_RESTART))
    deadline = time.perf_counter() + 2.0
    while not ((acq.status() >> BIT_VALID) & 1):
        if time.perf_counter() > deadline:
            raise RuntimeError("data_valid never set; DISCARD_COUNT {}".format(
                acq.rd(OFF_DISCARD_COUNT)))
    print("first sample  : after {} discarded frames".format(acq.rd(OFF_DISCARD_COUNT)))

    # measure the rate the PL actually delivers, not the rate the part claims
    n0 = acq.rd(OFF_SAMPLE_COUNT)
    t0 = time.perf_counter()
    time.sleep(0.5)
    n1 = acq.rd(OFF_SAMPLE_COUNT)
    dt = time.perf_counter() - t0
    sps = (n1 - n0) / dt
    print("sample rate   : {:+.1f} SPS over {:.3f} s (expect {:.0f})".format(sps, dt, FS))
    print("counters      : SAMPLE_COUNT {} WRITE_SLOT {} WRAP_COUNT {} STATUS 0x{:X}".format(
        n1, acq.rd(OFF_WRITE_SLOT), acq.rd(OFF_WRAP_COUNT), acq.status()))

    # freeze the ring so the dump cannot tear against the writer
    acq.wr(OFF_CTRL, 0)
    take = min(n1 - n0, NSAMP)
    if take < 4:
        raise RuntimeError("only {} samples in the window, nothing to look at".format(take))
    slot = (acq.rd(OFF_WRITE_SLOT) - take) % NSAMP
    xs, ys, zs = [], [], []
    for _ in range(take):
        x, y, z = acq.read_sample(slot)
        xs.append(x / LSB_PER_G)
        ys.append(y / LSB_PER_G)
        zs.append(z / LSB_PER_G)
        slot = (slot + 1) % NSAMP

    def summary(name, v):
        return "{:<3} n={:4d} mean {:+.4f} g  min {:+.4f}  max {:+.4f}".format(
            name, len(v), sum(v) / len(v), min(v), max(v))

    print("last STATUS   : 0x{:02X} ENTRIES 0x{:02X} TEMP {} LSB (~{:.1f} C)".format(
        acq.rd(OFF_LAST_STATUS), acq.rd(OFF_LAST_ENTRIES), acq.rd(OFF_LAST_TEMP),
        25.0 + (acq.rd(OFF_LAST_TEMP) - TEMP_INTERCEPT) / TEMP_SLOPE))
    for line in (summary("X", xs), summary("Y", ys), summary("Z", zs)):
        print(line)
    print("first 4 frames: " + " | ".join(
        "X {:+.4f} Y {:+.4f} Z {:+.4f}".format(xs[i], ys[i], zs[i]) for i in range(4)))
    print("If Z sits near 1 g with the board flat, the PL path is delivering real samples.")


if __name__ == "__main__":
    main()
