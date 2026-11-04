"""Is the captured frame actually advancing? Probe for the PL acquisition core.

The ring dump reported 1365 samples with min == max == mean to six decimals, i.e. one
identical X/Y/Z triple in every slot, while SAMPLE_COUNT kept growing at 4028 SPS. So the
transactions run but the data does not change. This probe separates the two possible
causes without touching any ADXL355 configuration register:

  A) LAST_X moves over time while acquiring  -> capture path OK, look elsewhere
  B) LAST_X is constant while SAMPLE_COUNT grows, and a manual read of 0x0F changes
     -> the chip is live but the auto transaction is reusing a stale rx_lat (RTL bug)
  C) LAST_X constant and the manual read of 0x0F also constant
     -> the part itself is not advancing its data registers under our read pattern

Reads /dev/mem directly at 0x41200000, so the acquisition overlay must still be loaded in
the PL (do not reload bringup.xsa first). Nothing here writes an ADXL355 register except
the two reads, so it cannot cause the measurement-mode stall.

Run:  sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_ringprobe.py
"""
import time

from pynq import MMIO

BASE = 0x41200000
OFF_RAM = 0x0000
OFF_CTRL, OFF_STATUS = 0x4000, 0x4004
OFF_SAMPLE_COUNT, OFF_WRITE_SLOT = 0x4008, 0x4010
OFF_LAST_TEMP, OFF_LAST_X = 0x4024, 0x4028
OFF_SPI_TX, OFF_SPI_RX, OFF_SPI_STAT = 0x4034, 0x4038, 0x403C
NSAMP = 1365


def s20(word):
    v = word & 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def spi_read(m, reg):
    # A request issued right after CTRL=0 can still collide with the auto transaction
    # that was already finishing, and the IP answers that with refused -- retry it.
    for _ in range(50):
        m.write(OFF_SPI_TX, (1 << 31) | (((reg << 1) | 1) & 0xFF))
        for _ in range(20000):
            stat = m.read(OFF_SPI_STAT)
            if stat & 0b01:
                return m.read(OFF_SPI_RX) & 0xFF
            if stat & 0b10:
                break
        time.sleep(0.002)
    raise RuntimeError("manual read of 0x{:02X} kept getting refused".format(reg))


def frame(m):
    return (m.read(OFF_LAST_TEMP), m.read(OFF_LAST_X),
            m.read(OFF_LAST_X + 4), m.read(OFF_LAST_X + 8))


def main():
    m = MMIO(BASE, 0x8000)
    if m.read(OFF_STATUS) == 0xFFFFFFFF:
        raise RuntimeError("no overlay at 0x{:08X}".format(BASE))
    m.write(OFF_CTRL, 0)                      # make sure we start from Standby of the IP
    print("idle: STATUS 0x{:X} SAMPLE_COUNT {}".format(m.read(OFF_STATUS),
                                                       m.read(OFF_SAMPLE_COUNT)))
    print("last frame held while idle: TEMP {} X {} Y {} Z {}".format(*frame(m)))

    m.write(OFF_CTRL, 1)                      # enable without restart: keep the counters
    seen = []
    for k in range(10):
        time.sleep(0.1)
        trip = frame(m)[1:]
        seen.append(trip)
        print("t={:.1f}s SAMPLE_COUNT {} LAST X {} Y {} Z {}".format(
            0.1 * (k + 1), m.read(OFF_SAMPLE_COUNT), trip[0], trip[1], trip[2]))
    uniq = len(set(seen))
    print("distinct frames in 1 s: {} of 10".format(uniq))

    # what the ring itself holds right now, at two far-apart slots
    top = m.read(OFF_WRITE_SLOT)
    for back in (1, 700):
        slot = (top - back) % NSAMP
        w = OFF_RAM + 4 * 3 * slot
        print("slot {:4d} ({} back): X {} Y {} Z {}".format(
            slot, back, s20(m.read(w)), s20(m.read(w + 4)), s20(m.read(w + 8))))
    m.write(OFF_CTRL, 0)
    time.sleep(0.002)

    # the original anomaly appeared only in a dump taken AFTER freezing the ring, so
    # repeat exactly that here: six consecutive slots read with the writer stopped
    top = m.read(OFF_WRITE_SLOT)
    held = []
    for back in range(1, 7):
        slot = (top - back) % NSAMP
        w = OFF_RAM + 4 * 3 * slot
        trip = (s20(m.read(w)), s20(m.read(w + 4)), s20(m.read(w + 8)))
        held.append(trip)
        print("frozen slot {:4d} ({} back): X {} Y {} Z {}".format(slot, back, *trip))
    print("distinct frames in the frozen dump: {} of 6".format(len(set(held))))

    # is the chip itself still moving? 0x0F is the middle byte of X
    r1 = spi_read(m, 0x0F)
    time.sleep(0.3)
    r2 = spi_read(m, 0x0F)
    time.sleep(0.3)
    r3 = spi_read(m, 0x0F)
    print("manual read 0x0F three times 0.3 s apart: 0x{:02X} 0x{:02X} 0x{:02X}".format(
        r1, r2, r3))
    if uniq > 1:
        print("VERDICT A: capture path advances; use the frozen-dump line above to see "
              "whether stopping the writer is what flattens the ring")
    elif len({r1, r2, r3}) > 1:
        print("VERDICT B: chip is live, the auto transaction keeps a stale frame -> RTL")
    else:
        print("VERDICT C: the part is not advancing its data registers under this read")


if __name__ == "__main__":
    main()
