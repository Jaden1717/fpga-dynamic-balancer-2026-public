"""Synthetic rotating unbalance; not a calibrated prediction of real vibration."""
import numpy as np


def make_measurement(unbalance_gmm, fs=4000, f_rot_hz=47.5, seconds=2.0,
                     noise_fraction=0.02, seed=1, harmonic_fraction=0.0):
    parameters = (fs, f_rot_hz, seconds, noise_fraction, harmonic_fraction)
    if (not all(np.isfinite(v) for v in parameters) or min(fs, f_rot_hz, seconds) <= 0
            or f_rot_hz >= fs / 2 or min(noise_fraction, harmonic_fraction) < 0
            or not np.isfinite(unbalance_gmm)):
        raise ValueError("invalid synthetic measurement parameters")
    t = np.arange(int(seconds * fs)) / fs
    if len(t) < 2:
        raise ValueError("record needs at least two samples")
    # Fixed synthetic transfer gain; phase is referenced to the rising pulse at t=0.
    v = 1e-5 * (2 * np.pi * f_rot_hz) ** 2 * complex(unbalance_gmm)
    theta = 2 * np.pi * f_rot_hz * t
    noise = np.random.default_rng(seed).normal(0, abs(v) * noise_fraction, len(t))
    signal = np.real(v * np.exp(1j * theta)) + noise
    signal += abs(v) * harmonic_fraction * np.cos(2 * theta)
    pulses = np.arange(int(np.floor(t[-1] * f_rot_hz)) + 1) / f_rot_hz
    return dict(t=t, signal=signal, pulses=pulses, fs=fs, true_v=v)