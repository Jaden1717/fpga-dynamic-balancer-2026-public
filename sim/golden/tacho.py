"""Pulse-span rotation speed, using timestamp seconds."""
import numpy as np


def estimate_rot_speed_hz(pulses, pulses_per_rev=1):
    p = np.asarray(pulses, dtype=float)
    if p.ndim != 1 or len(p) < 2 or not np.isfinite(p).all() or np.any(np.diff(p) <= 0):
        raise ValueError("need at least two strictly increasing finite pulse timestamps")
    if not isinstance(pulses_per_rev, int) or pulses_per_rev < 1:
        raise ValueError("pulses_per_rev must be a positive integer")
    return float((len(p) - 1) / (p[-1] - p[0]) / pulses_per_rev)