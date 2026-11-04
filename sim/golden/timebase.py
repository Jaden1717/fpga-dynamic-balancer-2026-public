"""Convert modulo hardware counters to seconds on one shared clock.

Counter values remain integers until their relative differences are known.
Callers must know that every true adjacent interval is positive and less than
half the counter range. The first value of each record must also be less than
half a range away from its origin; its nearest signed offset may be negative.
These assumptions cannot recover resets, reordered records, or unknown full
wraps during a long gap. Two records must come from the same physical clock.
"""
import math
from numbers import Integral, Real

import numpy as np


def _parameters(clock_hz, counter_bits):
    if (isinstance(counter_bits, (bool, np.bool_))
            or not isinstance(counter_bits, Integral) or not 2 <= counter_bits <= 64):
        raise ValueError("counter_bits must be an integer from 2 to 64")
    if isinstance(clock_hz, (bool, np.bool_)) or not isinstance(clock_hz, Real):
        raise ValueError("clock_hz must be a finite positive number")
    try:
        clock = float(clock_hz)
    except (OverflowError, ValueError) as error:
        raise ValueError("clock_hz must be a finite positive number") from error
    if not math.isfinite(clock) or clock <= 0:
        raise ValueError("clock_hz must be a finite positive number")
    return clock, 1 << int(counter_bits)


def _tick(value, modulus):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
        raise ValueError("ticks and origin_tick must be integers, not booleans or floats")
    value = int(value)
    if not 0 <= value < modulus:
        raise ValueError("ticks and origin_tick must lie within the counter range")
    return value


def _record(ticks, modulus):
    # Object dtype retains mixed input types rather than coercing floats/bools
    # into apparently valid integers, and preserves all uint64 precision.
    values = np.asarray(ticks, dtype=object)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("ticks must be a nonempty one-dimensional integer record")
    return [_tick(value, modulus) for value in values]


def _seconds(ticks, clock, modulus, origin):
    half = modulus // 2
    offset = (ticks[0] - origin) % modulus
    if offset == half:
        raise ValueError("first tick is exactly half a counter range from origin")
    if offset > half:
        offset -= modulus
    offsets = [offset]
    for previous, current in zip(ticks, ticks[1:]):
        delta = (current - previous) % modulus
        if not 0 < delta < half:
            raise ValueError("adjacent ticks must advance by less than half the counter range")
        offset += delta
        offsets.append(offset)
    seconds = np.asarray([offset / clock for offset in offsets], dtype=float)
    if not np.isfinite(seconds).all():
        raise ValueError("relative seconds exceed the finite floating-point range")
    return seconds


def ticks_to_seconds(ticks, clock_hz, *, counter_bits=64, origin_tick=None):
    """Return relative seconds from a nonempty sequence of integer counter values.

    Values must be in ``[0, 2**counter_bits)`` and advance strictly modulo the
    counter by less than half its range. The default origin is the first tick.
    An explicit integer origin uses the nearest signed modular first offset,
    allowing a record to start before zero. An exactly half-range offset is
    ambiguous and rejected. Caller knowledge of the half-range assumptions is
    required; resets and unknown full wraps cannot be detected or repaired.
    """
    clock, modulus = _parameters(clock_hz, counter_bits)
    values = _record(ticks, modulus)
    origin = values[0] if origin_tick is None else _tick(origin_tick, modulus)
    return _seconds(values, clock, modulus, origin)


def clock_times(sample_ticks, pulse_ticks, clock_hz, *, counter_bits=64):
    """Return sample and pulse seconds with the first sample tick as one origin.

    Both records must be nonempty and obey ``ticks_to_seconds`` assumptions.
    The first pulse's true offset from the sample origin must be less than half
    the counter range. Earlier pulses retain negative times; no pulses are
    cropped or created. The caller selects complete turns for ``sync_order``.
    """
    clock, modulus = _parameters(clock_hz, counter_bits)
    samples = _record(sample_ticks, modulus)
    pulses = _record(pulse_ticks, modulus)
    origin = samples[0]
    return (_seconds(samples, clock, modulus, origin),
            _seconds(pulses, clock, modulus, origin))
