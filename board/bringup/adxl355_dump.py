"""Dump continuous ADXL355 frames from the PL sample ring into a CSV file.

Built on top of adxl355_pl_acq.py (same overlay, same register map, same
delegated SPI channel); this file only adds what the sanity run does not need:
absolute sample numbering, chunked capture longer than the ring, and a
metadata header that makes the CSV self describing.

Ring math. SAMPLE_COUNT counts frames since the last restart and WRITE_SLOT is
the next slot the RTL writes, so at that instant slot (WRITE_SLOT - k) holds
absolute frame number (SAMPLE_COUNT - k). The ring is NSAMP = 1365 frames, i.e.
1365/4000 = 0.341 s; dumping all of it costs 1365*3 MMIO reads at the 18.7 us
measured on this board, about 76 ms, so the writer cannot catch the reader and
the ring is readable while acquisition keeps running. One CSV therefore covers
many seconds: take the whole ring, wait, take the next one, and let the frame
number expose any gap between chunks instead of hiding it.

Run on board:
    sudo env HOME=/home/xilinx PATH="$PATH" python3 adxl355_dump.py [seconds] [outdir]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adxl355_pl_acq import (Acq, configure, NSAMP, FS, LSB_PER_G, BIT_ENABLE,
                           BIT_RESTART, BIT_VALID, BIT_DROPPED, OFF_CTRL,
                           OFF_SAMPLE_COUNT, OFF_DISCARD_COUNT, OFF_WRITE_SLOT,
                           OFF_LAST_TEMP, OFF_DISCARD_LIMIT, TEMP_INTERCEPT,
                           TEMP_SLOPE, REG_DEVID, REG_FILTER, REG_RANGE,
                           REG_POWER_CTL)

# expected values, all measured on this board on 2026-10-05
GRAVITY_G = 1.014            # Z mean with the board flat and still
GRAVITY_TOL = 0.05           # g, generous for mount tilt
RATE_TOL = 0.05              # fraction of FS
READ_GAP_S = 0.002           # settle before a delegated SPI request
SPI_TRIES = 50
PACE = 0.98                  # fraction of the ring span to wait between chunks


def temp_c(acq):
    lsb = acq.rd(OFF_LAST_TEMP)
    return lsb, 25.0 + (lsb - TEMP_INTERCEPT) / TEMP_SLOPE


def config_meta(acq):
    """Read the chip config back. Acquisition must be stopped first: the RTL
    refuses a delegated SPI request while enable = 1, because configuring the
    part mid-measurement is what caused the 5.161 s of zero samples."""
    acq.wr(OFF_CTRL, 0)
    time.sleep(READ_GAP_S)
    meta = {}
    for name, reg in (("devid", REG_DEVID), ("filter", REG_FILTER),
                      ("range", REG_RANGE), ("power_ctl", REG_POWER_CTL)):
        for _ in range(SPI_TRIES):
            try:
                meta[name] = acq.spi_read(reg)
                break
            except RuntimeError:
                time.sleep(READ_GAP_S)
        else:
            raise RuntimeError("register 0x{:02X} keeps getting refused".format(reg))
    return meta


def take_ring(acq):
    """Whole ring, oldest first, as (frame_number, x, y, z) raw words."""
    before = acq.rd(OFF_SAMPLE_COUNT)
    slot = acq.rd(OFF_WRITE_SLOT)
    rows = []
    for k in range(NSAMP, 0, -1):
        x, y, z = acq.read_sample((slot - k) % NSAMP)
        rows.append((before - k, x, y, z))
    return rows, before, acq.rd(OFF_SAMPLE_COUNT)


def measure_sps(acq, window=0.5):
    """Rate from the RTL counter over a timed window with no MMIO traffic in
    it, so Python overhead cannot skew the number. This is the only check that
    catches the chip sitting at a lower ODR than requested."""
    n0 = acq.rd(OFF_SAMPLE_COUNT)
    t0 = time.perf_counter()
    time.sleep(window)
    n1 = acq.rd(OFF_SAMPLE_COUNT)
    dt = time.perf_counter() - t0
    return (n1 - n0) / dt, dt


def capture(acq, seconds):
    """Collect ~seconds of frames with the ring left live. Returns (rows, stats)."""
    acq.wr(OFF_CTRL, (1 << BIT_ENABLE) | (1 << BIT_RESTART))
    deadline = time.perf_counter() + 2.0
    while not ((acq.status() >> BIT_VALID) & 1):
        if time.perf_counter() > deadline:
            raise RuntimeError("data_valid never set, DISCARD_COUNT {}".format(
                acq.rd(OFF_DISCARD_COUNT)))
    # the ring only holds real data once every slot has been written at least
    # once, otherwise the first dump is padded with untouched slots reading 0
    deadline = time.perf_counter() + 2.0 * NSAMP / FS
    while acq.rd(OFF_SAMPLE_COUNT) < NSAMP:
        if time.perf_counter() > deadline:
            raise RuntimeError("ring never filled, SAMPLE_COUNT stuck at {}".format(
                acq.rd(OFF_SAMPLE_COUNT)))
        time.sleep(0.01)
    sps, dt = measure_sps(acq)

    rows = []
    gaps = 0
    worst_gap = 0
    drop_seen = 0
    next_frame = None
    t0 = time.perf_counter()
    chunk = 0
    while time.perf_counter() - t0 < seconds:
        ring, before, after = take_ring(acq)
        if ring[0][0] != before - NSAMP:
            raise RuntimeError("ring arithmetic inconsistent: {} vs {}".format(
                ring[0][0], before - NSAMP))
        if next_frame is not None:
            delta = ring[0][0] - next_frame
            if delta > 0:
                gaps += 1
                worst_gap = max(worst_gap, delta)
            elif delta < 0:
                # snapshotted a touch early: drop the repeated tail, keep order
                ring = [r for r in ring if r[0] >= next_frame]
        rows.extend(ring)
        next_frame = ring[-1][0] + 1
        chunk += 1
        stat = acq.status()
        # only a dropped bit seen while still enabled means a DRDY edge was
        # missed; the act of clearing enable can legitimately trip it
        drop_seen |= (stat >> BIT_DROPPED) & 1
        print("chunk {:3d}: frames {:8d}..{:8d} count {:8d} slot {:5d} "
              "status 0x{:X}".format(chunk, ring[0][0], ring[-1][0], after,
                                     acq.rd(OFF_WRITE_SLOT), stat))
        if after <= before:
            raise RuntimeError("SAMPLE_COUNT froze at {}".format(before))
        # the read itself already spent (after - before) frames of the ring, so
        # wait out a hair under the rest of one span: a late snapshot loses
        # frames, an early one only repeats a tail we trim away above
        time.sleep(max(0.0, PACE * (NSAMP - (after - before)) / FS))
    return rows, dict(frames=len(rows), gaps=gaps, worst_gap=worst_gap,
                      dropped=drop_seen, sps=sps, span=time.perf_counter() - t0)


def write_csv(path, acq, meta, rows, stats, seconds):
    lsb, tc = temp_c(acq)
    head = [
        "adxl355 PL acquisition dump, TUL PYNQ-Z2 + EVAL-ADXL355-PMDZ",
        "generator=adxl355_dump.py",
        "ip=adxl355_acq base=0x{:08X} discard_limit={}".format(
            acq.base, acq.rd(OFF_DISCARD_LIMIT)),
        "devid=0x{:02X} filter=0x{:02X} range=0x{:02X} power_ctl=0x{:02X}".format(
            meta["devid"], meta["filter"], meta["range"], meta["power_ctl"]),
        "fs_hz={:.0f} ring_frames={}".format(FS, NSAMP),
        "lsb_per_g={:.0f} gravity_ref_g={:.3f}".format(LSB_PER_G, GRAVITY_G),
        "temp_lsb={} temp_c={:.2f}".format(lsb, tc),
        "requested_s={:.2f} frames={}".format(seconds, stats["frames"]),
        "measured_sps={:.1f} over a counter window".format(stats["sps"]),
        "chunk_gaps={} worst_gap_frames={}".format(stats["gaps"],
                                                   stats["worst_gap"]),
        "n is the absolute frame number since restart, t_s = n / fs_hz",
        "ax_g ay_g az_g are raw 20-bit words over lsb_per_g, unfiltered",
    ]
    with open(path, "w") as fh:
        for line in head:
            fh.write("# " + line + "\n")
        fh.write("n,t_s,ax_g,ay_g,az_g\n")
        for n, x, y, z in rows:
            fh.write("{},{:.6f},{:.6f},{:.6f},{:.6f}\n".format(
                n, n / FS, x / LSB_PER_G, y / LSB_PER_G, z / LSB_PER_G))


def report(path, rows, stats):
    ax = [x / LSB_PER_G for _, x, _, _ in rows]
    ay = [y / LSB_PER_G for _, _, y, _ in rows]
    az = [z / LSB_PER_G for _, _, _, z in rows]
    print("csv         : {} ({:.1f} KB)".format(path, os.path.getsize(path) / 1024.0))
    for name, v in (("X", ax), ("Y", ay), ("Z", az)):
        print("axis {}      : mean {:+.4f} g  min {:+.4f}  max {:+.4f}".format(
            name, sum(v) / len(v), min(v), max(v)))
    print("frames      : {} over {:.3f} s of chunking".format(
        stats["frames"], stats["span"]))
    print("rate        : {:+.1f} SPS from the RTL counter (expect {:.0f})".format(
        stats["sps"], FS))
    print("continuity  : gaps {} worst {} frames, STATUS dropped {}".format(
        stats["gaps"], stats["worst_gap"], stats["dropped"]))

    bad = []
    if abs(sum(az) / len(az) - GRAVITY_G) > GRAVITY_TOL:
        bad.append("Z mean {:+.4f} g is not gravity".format(sum(az) / len(az)))
    if max(abs(v) for v in az) < 1e-4:
        bad.append("every Z word is zero")
    if abs(stats["sps"] - FS) > FS * RATE_TOL:
        bad.append("rate {:+.1f} SPS is off 4 kHz by more than {:.0f} %".format(
            stats["sps"], RATE_TOL * 100))
    if stats["dropped"]:
        bad.append("STATUS dropped latched during capture: a DRDY edge was missed")
    if stats["gaps"]:
        print("VERDICT WARN: {} chunk gap(s), worst {} frames lost".format(
            stats["gaps"], stats["worst_gap"]))
    if bad:
        print("VERDICT FAIL: " + "; ".join(bad))
        return False
    if stats["gaps"]:
        return True
    print("VERDICT OK: gapless frames, gravity present, CSV feeds the "
          "golden model as-is")
    return True


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(
        os.path.abspath(__file__))
    acq = Acq()
    print("overlay: adxl355_acq @0x{:08X}, DISCARD_LIMIT {}".format(
        acq.base, acq.rd(OFF_DISCARD_LIMIT)))
    configure(acq)
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    rows, stats = capture(acq, seconds)
    meta = config_meta(acq)
    path = os.path.join(outdir, "adxl355_{}.csv".format(
        time.strftime("%Y%m%d_%H%M%S")))
    write_csv(path, acq, meta, rows, stats, seconds)
    if not report(path, rows, stats):
        sys.exit(1)


if __name__ == "__main__":
    main()
