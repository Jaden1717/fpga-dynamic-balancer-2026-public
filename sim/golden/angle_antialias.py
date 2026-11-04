"""Offline angular low-pass before 128-to-64 decimation.

The selected complete-turn block is treated as periodic. This is a zero-phase
Python/PS operation, not a causal FPGA filter or a physical phase calibration.
It prevents folding during this decimation only; interpolation cannot undo
aliasing already present in the acquired data or the 128-point grid.
"""
import numpy as np
from sync_order import to_angle_domain


def decimate_angle128(samples):
    x = np.asarray(samples, dtype=float)
    if x.ndim != 1 or x.size == 0 or x.size % 128 or not np.isfinite(x).all():
        raise ValueError('dense grid must contain finite complete 128-point turns')
    orders = np.fft.rfftfreq(x.size, d=1 / 128)
    gain = np.ones_like(orders)
    transition = (orders > 24) & (orders < 32)
    gain[transition] = .5 * (1 + np.cos(np.pi * (orders[transition] - 24) / 8))
    gain[orders >= 32] = 0
    filtered = np.fft.irfft(np.fft.rfft(x) * gain, n=x.size)
    return filtered[::2]


def resample_antialiased(t, raw, edges):
    dense = to_angle_domain(t, raw, edges, pts_per_rev=128)
    return decimate_angle128(dense)
