"""Reproducible synthetic trial-weight loop, reconstructed from the handoff contract."""
import argparse
import json
from pathlib import Path

import numpy as np

from balance import solve
from rotor_model import make_measurement
from sync_order import extract_1x, to_angle_domain
from tacho import estimate_rot_speed_hz


def run():
    unbalance = 60 * np.exp(1j * np.radians(130))
    trial = 30 + 0j
    initial = make_measurement(unbalance)
    weighted = make_measurement(unbalance + trial, seed=2)
    values = [extract_1x(to_angle_domain(m["t"], m["signal"], m["pulses"]))
              for m in (initial, weighted)]
    result = solve(values[0], values[1], trial, correction_radius_mm=30)
    residual = abs(unbalance + result["correction_gmm"]) / abs(unbalance)
    return dict(measurement_kind="synthetic", fs_hz=4000, points_per_rev=64,
                pulses_per_rev=1, rotation_hz=estimate_rot_speed_hz(initial["pulses"]),
                mass_g=result["mass_g"], angle_deg=result["angle_deg"],
                relative_residual=float(residual), trial_gmm=30,
                correction_radius_mm=30, noise_fraction=0.02,
                seeds=[1, 2], true_correction_mass_g=2, true_correction_angle_deg=310)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="optional JSON result file")
    args = parser.parse_args()
    result = run()
    print(f"rotation {result['rotation_hz']:.3f} Hz; correction "
          f"{result['mass_g']:.4f} g @ {result['angle_deg']:.2f} deg; "
          f"synthetic residual {100 * result['relative_residual']:.4f}%")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["relative_residual"] < 0.05 else 1


if __name__ == "__main__":
    raise SystemExit(main())