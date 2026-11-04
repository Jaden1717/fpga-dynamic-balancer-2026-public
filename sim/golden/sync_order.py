"""One pulse per revolution, angle interpolation and synchronous complex 1x."""
import numpy as np


def point_count(pts_per_rev):
    if not isinstance(pts_per_rev, int) or pts_per_rev < 4:
        raise ValueError("pts_per_rev must be an integer of at least four")
    return pts_per_rev


def to_angle_domain(t, x, pulses, pts_per_rev=64):
    """Return complete turns with angle zero at each rising pulse timestamp.

    Contract: one pulse per revolution. Timestamp and acceleration data must
    share a clock. Linear interpolation does not compensate sensor group delay.
    """
    count = point_count(pts_per_rev)
    t, x, p = (np.asarray(v, dtype=float) for v in (t, x, pulses))
    if (t.ndim != 1 or x.shape != t.shape or len(t) < 2
            or not np.isfinite(t).all() or not np.isfinite(x).all()
            or np.any(np.diff(t) <= 0)):
        raise ValueError("sample times and values must be finite, aligned and increasing")
    if (p.ndim != 1 or len(p) < 2 or not np.isfinite(p).all()
            or np.any(np.diff(p) <= 0) or p[0] < t[0] or p[-1] > t[-1]):
        raise ValueError("pulse timestamps must increase and lie inside the record")
    if np.min(np.diff(p)) / np.max(np.diff(t)) < count:
        raise ValueError("not enough time samples per turn for the requested angle grid")
    fraction = np.arange(count) / count
    targets = p[:-1, None] + np.diff(p)[:, None] * fraction
    return np.interp(targets.ravel(), t, x)


def extract_1x(samples, pts_per_rev=64):
    count = point_count(pts_per_rev)
    samples = np.asarray(samples, dtype=float)
    if (samples.ndim != 1 or len(samples) < count or len(samples) % count
            or not np.isfinite(samples).all()):
        raise ValueError("angle samples must contain finite complete revolutions")
    kernel = np.exp(-2j * np.pi * np.arange(count) / count)
    turns = samples.reshape(-1, count)
    return complex(np.mean(2 / count * (turns @ kernel)))