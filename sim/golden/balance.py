"""Single-plane influence coefficient; masses in g and radii in mm."""
import numpy as np


def solve(v0, v1, trial_gmm, correction_radius_mm):
    v0, v1, trial = complex(v0), complex(v1), complex(trial_gmm)
    radius = float(correction_radius_mm)
    if not all(np.isfinite(v) for v in (v0, v1, trial, radius)) or radius <= 0:
        raise ValueError("finite measurements and positive installation radius required")
    if abs(trial) == 0 or abs(v1 - v0) <= 1e-12 * max(abs(v0), abs(v1), 1e-30):
        raise ValueError("trial weight must produce a resolvable complex response change")
    coefficient = (v1 - v0) / trial
    correction = -v0 / coefficient
    return dict(coefficient=coefficient, correction_gmm=correction,
                mass_g=abs(correction) / radius,
                angle_deg=float(np.degrees(np.angle(correction)) % 360))