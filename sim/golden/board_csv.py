"""Strict reader for ADXL355 dumps; fixed-frequency analysis is not order tracking."""
import argparse
import csv
import re
import sys

import numpy as np

COLUMNS = ["n", "t_s", "ax_g", "ay_g", "az_g"]


def positive(value, name):
    number = float(value)
    if not np.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def load(path, rate="measured"):
    """Return axes in g, integer frame IDs and a calibrated relative clock.

    The original board t_s is retained as t_board. By default measured_sps is
    preferred over nominal fs_hz; no pulse timestamps are invented.
    """
    if rate not in ("measured", "nominal"):
        raise ValueError("rate must be measured or nominal")
    meta, lines = {}, []
    with open(path, encoding="utf-8-sig", newline="") as stream:
        for line in stream:
            if line.lstrip().startswith("#"):
                for key, value in re.findall(r"([A-Za-z_][A-Za-z_0-9]*)=([^\s]+)", line):
                    if key in meta and meta[key] != value:
                        raise ValueError(f"conflicting metadata: {key}")
                    meta[key] = value
            elif line.strip():
                lines.append(line)
    if not lines or "fs_hz" not in meta:
        raise ValueError("missing CSV header or fs_hz metadata")
    nominal = positive(meta["fs_hz"], "fs_hz")
    measured = positive(meta.get("measured_sps", nominal), "measured_sps")
    for key in ("chunk_gaps", "worst_gap_frames", "dropped"):
        if key in meta and int(meta[key]) != 0:
            raise ValueError(f"capture is not clean: {key}={meta[key]}")
    reader = csv.reader(lines)
    if next(reader) != COLUMNS:
        raise ValueError("expected CSV columns: " + ",".join(COLUMNS))
    ids, values = [], []
    for row in reader:
        if len(row) != 5:
            raise ValueError("each CSV record needs five columns")
        n = int(row[0])
        if n < 0 or n > np.iinfo(np.int64).max:
            raise ValueError("frame number outside nonnegative int64 range")
        ids.append(n)
        values.append([float(v) for v in row[1:]])
    if not ids:
        raise ValueError("empty capture")
    n = np.asarray(ids, dtype=np.int64)
    data = np.asarray(values, dtype=float)
    if not np.isfinite(data).all():
        raise ValueError("nonfinite samples or timestamps")
    if np.any(np.diff(n) != 1):
        raise ValueError("frame numbers have gaps, duplicates or reversals")
    if not np.allclose(data[:, 0], n / nominal, rtol=0, atol=0.51e-6):
        raise ValueError("board timestamps do not match n/fs_hz")
    if "frames" in meta and int(meta["frames"]) != len(n):
        raise ValueError("metadata frames does not match the CSV row count")
    fs = measured if rate == "measured" else nominal
    return dict(meta=meta, n=n, t=n / fs, t_board=data[:, 0],
                x=data[:, 1], y=data[:, 2], z=data[:, 3], fs=fs,
                nominal_fs=nominal, measured_sps=measured, seconds=len(n) / fs)


def dft_1x(x, fs, f0):
    """Estimate V for x=DC+Re(V*exp(j*2*pi*f0*t)), relative to first sample.

    Fits cosine, sine and DC jointly. Unlike a raw finite-window DFT, this
    handles gravity/DC and partial turns without assuming orthogonal bins.
    The name is kept for compatibility with the handoff analysis entry point.
    """
    fs = positive(fs, "fs")
    f0 = positive(f0, "f0")
    signal = np.asarray(x, dtype=float)
    if signal.ndim != 1 or len(signal) < 3 or not np.isfinite(signal).all():
        raise ValueError("need at least three finite one-dimensional samples")
    if f0 >= fs / 2:
        raise ValueError("frequency must be below Nyquist")
    theta = 2 * np.pi * f0 * np.arange(len(signal)) / fs
    design = np.column_stack((np.cos(theta), np.sin(theta), np.ones(len(signal))))
    coeff, _, rank, _ = np.linalg.lstsq(design, signal, rcond=None)
    if rank != 3:
        raise ValueError("record cannot resolve the supplied frequency")
    return complex(coeff[0], -coeff[1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv")
    parser.add_argument("f_rot_hz", nargs="?", type=float,
                        help="known fixed rotation frequency; no pulse phase reference")
    parser.add_argument("--rate", choices=("measured", "nominal"), default="measured")
    args = parser.parse_args(argv)
    try:
        capture = load(args.csv, args.rate)
        print(f"frames={len(capture['n'])} seconds={capture['seconds']:.6f} "
              f"fs={capture['fs']:.1f} Hz nominal_fs={capture['nominal_fs']:.1f} Hz")
        for name in ("x", "y", "z"):
            axis = capture[name]
            print(f"{name.upper()} mean={axis.mean():+.4f} g "
                  f"min={axis.min():+.4f} max={axis.max():+.4f} "
                  f"std={axis.std():.6f}")
            if args.f_rot_hz is not None:
                v = dft_1x(axis, capture["fs"], args.f_rot_hz)
                print(f"{name.upper()} fixed-frequency 1x amplitude={abs(v):.6f} g "
                      f"phase={np.degrees(np.angle(v)):.2f} deg (first sample reference)")
    except (ValueError, OSError) as exc:
        print(f"invalid capture: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())