"""Hardware-clock conversion preserves precision and a shared phase origin."""
import importlib
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim" / "golden"))


def timebase():
    assert importlib.util.find_spec("timebase") is not None, "missing hardware timebase module"
    return importlib.import_module("timebase")


def test_100mhz_sample_ticks_become_relative_seconds():
    # Catches using nominal sample indices or returning absolute clock seconds.
    result = timebase().ticks_to_seconds([100_000_000, 100_025_000, 100_050_000], 100e6)
    assert result.tolist() == pytest.approx([0, 0.00025, 0.0005])


def test_counter_wrap_preserves_forward_intervals():
    result = timebase().ticks_to_seconds([250, 255, 1, 6], 100, counter_bits=8)
    assert result.tolist() == pytest.approx([0, 0.05, 0.07, 0.12])


def test_64bit_absolute_ticks_do_not_erase_small_differences():
    # Converting the absolute uint64 values to float first loses these ticks.
    ticks = np.array([2**64 - 5, 2**64 - 4, 2**64 - 1, 1], dtype=np.uint64)
    result = timebase().ticks_to_seconds(ticks, 100e6)
    assert result.tolist() == pytest.approx([0, 1e-8, 4e-8, 6e-8], abs=1e-20)


def test_explicit_origin_can_place_first_tick_before_zero():
    result = timebase().ticks_to_seconds([100, 104], 100, counter_bits=8, origin_tick=108)
    assert result.tolist() == pytest.approx([-0.08, -0.04])


def test_two_records_keep_one_origin_across_wrap_and_retain_earlier_pulse():
    sample, pulse = timebase().clock_times(
        [248, 252, 0, 4, 8], [246, 254, 6], 4, counter_bits=8)
    assert sample.tolist() == [0, 1, 2, 3, 4]
    assert pulse.tolist() == [-0.5, 1.5, 3.5]


@pytest.mark.parametrize("width", [2, 8, 32, 64, np.int64(8)])
def test_supported_counter_widths_and_numpy_integers(width):
    result = timebase().ticks_to_seconds([np.uint64(2**int(width) - 1), np.uint64(0)],
                                        np.float64(10), counter_bits=width)
    assert result.tolist() == pytest.approx([0, 0.1])


@pytest.mark.parametrize("ticks", [
    [], 1, [[1, 2]], [[1], [2]], [True, 2], [np.bool_(False), 2],
    [1.0, 2], np.array([1., 2.]), [float("nan")], ["1", "2"],
    [-1, 2], [0, 256], [5, 5], [6, 5], [0, 128], [0, 129],
])
def test_invalid_tick_records_are_rejected(ticks):
    with pytest.raises(ValueError):
        timebase().ticks_to_seconds(ticks, 100, counter_bits=8)


@pytest.mark.parametrize("clock", [0, -1, float("nan"), float("inf"), True, "100"])
def test_invalid_clock_is_rejected(clock):
    with pytest.raises(ValueError):
        timebase().ticks_to_seconds([1, 2], clock)


@pytest.mark.parametrize("width", [1, 65, 8.0, True, "8"])
def test_invalid_counter_width_is_rejected(width):
    with pytest.raises(ValueError):
        timebase().ticks_to_seconds([1, 2], 100, counter_bits=width)


@pytest.mark.parametrize("origin", [-1, 256, True, 1.0, np.bool_(False), "1", 129])
def test_invalid_or_half_range_ambiguous_origin_is_rejected(origin):
    with pytest.raises(ValueError):
        timebase().ticks_to_seconds([1, 2], 100, counter_bits=8, origin_tick=origin)


@pytest.mark.parametrize("sample,pulse", [([1, 2], []), ([], [1, 2]),
                                          ([1, 2], [3, 3]), ([1, 2], [129, 130])])
def test_shared_clock_requires_unambiguous_nonempty_records(sample, pulse):
    with pytest.raises(ValueError):
        timebase().clock_times(sample, pulse, 100, counter_bits=8)


def test_shared_ticks_feed_real_speed_and_synchronous_phase_estimators():
    # Catches independent zeroing of the pulse and sample records (36 deg error).
    sync = importlib.import_module("sync_order")
    tacho = importlib.import_module("tacho")
    modulus = 2**32
    start = modulus - 1_000_000
    sample_ticks = [(start + i * 25_000) % modulus for i in range(801)]
    pulse_ticks = [(start + 1_000_000 + i * 2_000_000) % modulus for i in range(10)]
    sample, pulse = timebase().clock_times(sample_ticks, pulse_ticks, 100e6,
                                           counter_bits=32)
    phase = np.deg2rad(37)
    # Independent known 50 Hz signal, with angle zero at 0.010 s.
    signal = 1.014 + 0.02 * np.cos(2 * np.pi * 50 * (np.arange(801) / 4000 - 0.010) + phase)
    coefficient = sync.extract_1x(sync.to_angle_domain(sample, signal, pulse))
    assert tacho.estimate_rot_speed_hz(pulse) == pytest.approx(50)
    assert abs(coefficient) == pytest.approx(0.02, rel=0.002)
    assert np.angle(coefficient) == pytest.approx(phase, abs=0.001)
