"""Diagnostic only: explicitly interpret sealed HXS1 legacy captures as fan TACH.

The original capture.json is NEVER rewritten. Its fixed 1-PPR annotation is
retained as provenance; --source declares the physically connected 2-PPR source.
No pulse is merged/deleted and no absolute rotor phase or balance is inferred.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from sync_csv import load_capture
from timebase import clock_times


def _validate_screening(source, rpm_min, rpm_max):
    if source != 'fan-tach-2ppr':
        raise ValueError('source must explicitly declare fan-tach-2ppr')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (rpm_min, rpm_max)) or not 0 < rpm_min < rpm_max:
        raise ValueError('RPM screening bounds must be finite, positive and ordered')


def _review_loaded(directory, capture, *, source, rpm_min, rpm_max):
    _validate_screening(source, rpm_min, rpm_max)
    directory = Path(directory)
    meta, samples, pulses = capture['metadata'], capture['samples'], capture['pulses']
    result = dict(schema_version=1, status='INSUFFICIENT_DATA', source=source,
        recorded_pulses_per_revolution=meta['pulses_per_revolution'],
        interpreted_pulses_per_revolution=2, source_declaration_verified_by_software=False,
        source_sha256={name: hashlib.sha256((directory/name).read_bytes()).hexdigest()
                       for name in ('capture.json', 'samples.csv', 'pulses.csv')},
        absolute_phase_available=False, balance_ready=False, flags=[],
        thresholds=dict(rpm_min=rpm_min, rpm_max=rpm_max,
                        sub_microsecond_seconds=1e-6, max_period_relative_deviation=.05),
        note='Screening only; a clean report cannot prove correct PPR, no missing edges, or mechanical readiness.',
        raw_pulse_count=len(pulses), in_record_pulse_count=0,
        rpm_median=None, revolution_period_relative_max_deviation=None)
    if not pulses:
        return result
    st, pt = clock_times([r[1] for r in samples], [r[1] for r in pulses], meta['clock_hz'])
    selected = pt[(pt >= st[0]) & (pt <= st[-1])]
    result['in_record_pulse_count'] = len(selected)
    if len(selected) < 3:
        return result
    adjacent = np.diff(selected)
    # Same-polarity TACH edges alternate between two positions per revolution.
    # Pair i with i+2: unequal half-revolution spacing is not speed ripple.
    periods = selected[2:] - selected[:-2]
    rpm = 60 / periods
    deviation = float(np.max(np.abs(periods / np.median(periods) - 1)))
    result.update(status='REVIEW_REQUIRED',
        adjacent_interval_seconds=dict(min=float(adjacent.min()), max=float(adjacent.max())),
        revolution_period_seconds=periods.tolist(),
        overlapping_revolution_interval_count=len(periods),
        rpm_median=float(np.median(rpm)), rpm_min=float(rpm.min()), rpm_max=float(rpm.max()),
        revolution_period_relative_max_deviation=deviation)
    if np.any(adjacent < 1e-6):
        result['flags'].append('sub_microsecond_edges')
    if np.any((rpm < rpm_min) | (rpm > rpm_max)):
        result['flags'].append('rpm_outside_declared_bounds')
    if deviation > .05:
        result['flags'].append('speed_variation_or_edge_error')
    if result['flags']:
        result['status'] = 'REJECT'
    return result


def review_capture(directory, *, source, rpm_min, rpm_max):
    _validate_screening(source, rpm_min, rpm_max)
    capture = load_capture(Path(directory), allow_static=True)
    return _review_loaded(directory, capture, source=source, rpm_min=rpm_min, rpm_max=rpm_max)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--source', required=True, choices=['fan-tach-2ppr'])
    parser.add_argument('--rpm-min', type=float, required=True)
    parser.add_argument('--rpm-max', type=float, required=True)
    args = parser.parse_args(argv)
    try:
        result = review_capture(args.directory, source=args.source,
                                rpm_min=args.rpm_min, rpm_max=args.rpm_max)
    except (ValueError, OSError) as error:
        print(json.dumps(dict(status='INVALID_CAPTURE', error=str(error))))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result['status'] == 'REVIEW_REQUIRED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
