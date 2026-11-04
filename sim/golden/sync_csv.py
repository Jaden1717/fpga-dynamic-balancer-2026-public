"""Strict synchronized capture reader and 1x bridge (one optical pulse/turn).

python sim/golden/sync_csv.py capture_sync --axis x
The output is a measured complex acceleration coefficient, not an imbalance
mass or a correction angle. No sensor group-delay calibration is assumed.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

from timebase import clock_times, ticks_to_seconds
from sync_order import to_angle_domain, extract_1x
from tacho import estimate_rot_speed_hz

DECIMAL = re.compile(r'-?[0-9]+\Z')
SAMPLE_COLUMNS = ['frame_id', 'ticks', 'x_raw', 'y_raw', 'z_raw']
PULSE_COLUMNS = ['pulse_count', 'ticks']


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value < high:
        raise ValueError('{} must be an integer in [{}, {})'.format(name, low, high))
    return value


def _csv(path, columns):
    result = []
    with path.open('r', encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream)
        if next(reader, None) != columns:
            raise ValueError('{} columns must be {}'.format(path.name, ','.join(columns)))
        for line, row in enumerate(reader, 2):
            if len(row) != len(columns) or any(not DECIMAL.fullmatch(value) for value in row):
                raise ValueError('{} line {} needs exactly {} decimal integers'.format(path.name, line, len(columns)))
            result.append(tuple(int(value, 10) for value in row))
    return result


def _check_records(samples, pulses, clock):
    if len(samples) < 2:
        raise ValueError('at least two samples are required')
    if samples[0][0] != 0:
        raise ValueError('complete epoch requires initial frame 0')
    if pulses and pulses[0][0] != 1:
        raise ValueError('complete epoch requires initial pulse 1')
    for name, rows, bits in [('frame', samples, 32), ('pulse', pulses, 64)]:
        for row in rows:
            _integer(row[0], name + ' count', 0, 1 << bits)
            _integer(row[1], name + ' ticks', 0, 1 << 64)
        for previous, current in zip(rows, rows[1:]):
            if (current[0] - previous[0]) % (1 << bits) != 1:
                raise ValueError(name + ' count gap, reset or reorder')
        if rows:
            ticks_to_seconds([row[1] for row in rows], clock)
    for row in samples:
        for value in row[2:]:
            _integer(value, 'axis raw', -(1 << 19), 1 << 19)


def load_capture(directory, allow_static=False):
    """Validate complete metadata and integer records; never repair lost data."""
    directory = Path(directory)
    with (directory / 'capture.json').open(encoding='utf-8-sig') as stream:
        meta = json.load(stream)
    if not isinstance(meta, dict) or meta.get('schema_version') != 1:
        raise ValueError('unsupported capture schema')
    _integer(meta.get('schema_version'), 'schema_version', 1, 2)
    _integer(meta.get('epoch'), 'epoch', 0, 1 << 32)
    clock = meta.get('clock_hz')
    if isinstance(clock, bool) or not isinstance(clock, (int, float)) or not math.isfinite(clock) or clock <= 0:
        raise ValueError('clock_hz must be finite and positive')
    if not isinstance(meta.get('xsa_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', meta['xsa_sha256']):
        raise ValueError('missing or invalid xsa_sha256 provenance')
    if meta.get('completed') is not True or meta.get('errors') != []:
        raise ValueError('partial or failed capture is not valid analysis input')
    _integer(meta.get('pulses_per_revolution'), 'pulses_per_revolution', 1, 2)
    _integer(meta.get('hardware_status'), 'hardware_status', 0, 1 << 32)
    if meta['hardware_status'] & 0x19 or meta.get('source_idle') is not True:
        raise ValueError('capture not frozen/idle or FIFO overflow status set')
    static = meta.get('status') == 'STATIC_ONLY'
    if static:
        if not allow_static or meta.get('rotation_ready') is not False:
            raise ValueError('STATIC_ONLY requires explicit loading and cannot be rotational analysis')
    elif meta.get('status') != 'PASS' or meta.get('rotation_ready') is not True:
        raise ValueError('capture is not rotation ready')
    for name in ('sample_drops', 'pulse_drops', 'source_missed'):
        _integer(meta.get(name), name, 0, 1 << 32)
        if meta[name] != 0:
            raise ValueError(name + ' indicates data loss')
    hashes = meta.get('raw_sha256')
    if hashes is not None:
        if not isinstance(hashes, dict) or any(hashes.get(name) != hashlib.sha256((directory/name).read_bytes()).hexdigest()
                for name in ('samples.csv', 'pulses.csv')):
            raise ValueError('raw file hash mismatch')
    config = meta.get('sensor_configuration')
    if config is not None:
        if not isinstance(config, dict) or config.get('verified') is not True:
            raise ValueError('sensor configuration is not verified')
        registers = config.get('registers', {})
        for name, expected, mask in [('FILTER', 0, 0x7f), ('RANGE', 0x81, 0xc3), ('POWER_CTL', 0, 7)]:
            actual = registers.get(name) if isinstance(registers, dict) else None
            if type(actual) is not int or not 0 <= actual <= 255 or actual & mask != expected & mask:
                raise ValueError('sensor configuration mismatch: ' + name)
    samples = _csv(directory / 'samples.csv', SAMPLE_COLUMNS)
    pulses = _csv(directory / 'pulses.csv', PULSE_COLUMNS)
    for name, actual in [('sample_count', len(samples)), ('pulse_count', len(pulses))]:
        _integer(meta.get(name), name, 0, 1 << 64)
        if meta[name] != actual:
            raise ValueError(name + ' does not match CSV row count')
    if not static and len(pulses) < 2:
        raise ValueError('rotation capture requires at least two pulses')
    _check_records(samples, pulses, clock)
    eligible = 0
    if pulses:
        sample_times, pulse_times = clock_times([row[1] for row in samples], [row[1] for row in pulses], clock)
        eligible = int(np.count_nonzero((pulse_times >= sample_times[0]) & (pulse_times <= sample_times[-1])))
    if static and eligible >= 2:
        raise ValueError('STATIC_ONLY metadata contradicts in-record pulse count')
    if not static and eligible < 2:
        raise ValueError('rotation capture requires two optical pulses inside sample time span')
    if 'in_record_pulse_count' in meta and meta['in_record_pulse_count'] != eligible:
        raise ValueError('in_record_pulse_count does not match actual tick span')
    return dict(metadata=meta, samples=samples, pulses=pulses)


def analyze_capture(directory, axis='z', pts_per_rev=64):
    """Use only complete revolutions bracketed by in-record optical pulses."""
    if axis not in ('x', 'y', 'z'):
        raise ValueError('axis must be x, y or z')
    capture = load_capture(directory)
    samples, pulses, meta = capture['samples'], capture['pulses'], capture['metadata']
    sample_times, pulse_times = clock_times([row[1] for row in samples],
                                          [row[1] for row in pulses], meta['clock_hz'])
    # Pulse events can precede the first post-discard ADXL frame or occur while
    # the final in-flight sample is published. Keep them in CSV; crop only here.
    selected = pulse_times[(pulse_times >= sample_times[0]) & (pulse_times <= sample_times[-1])]
    if len(selected) < 2:
        raise ValueError('need two optical pulses inside the acceleration time span')
    values = np.asarray([row[{'x': 2, 'y': 3, 'z': 4}[axis]] for row in samples], dtype=float) / 256000.0
    angle_samples = to_angle_domain(sample_times, values, selected, pts_per_rev=pts_per_rev)
    coefficient = extract_1x(angle_samples, pts_per_rev=pts_per_rev)
    quality = _quality(capture, sample_times, selected, angle_samples, pts_per_rev)
    return dict(quality=quality, axis=axis, rotation_hz=estimate_rot_speed_hz(selected),
                amplitude_g=abs(coefficient), phase_deg=float(np.degrees(np.angle(coefficient))),
                coefficient_real_g=coefficient.real, coefficient_imag_g=coefficient.imag,
                complete_turns=len(selected) - 1, points_per_revolution=pts_per_rev,
                phase_reference='selected optical edge; uncalibrated sensor/path delay',
                epoch=meta['epoch'], xsa_sha256=meta['xsa_sha256'])


def _quality(capture, sample_times, selected, angle_samples=None, pts_per_rev=64):
    axes = {}
    for index, axis in enumerate(('x', 'y', 'z'), 2):
        raw = np.asarray([row[index] for row in capture['samples']], dtype=np.int64)
        axes[axis] = dict(min_raw=int(raw.min()), max_raw=int(raw.max()),
                          rail_count=int(np.count_nonzero((raw == -524288) | (raw == 524287))),
                          near_rail_count=int(np.count_nonzero(np.abs(raw) >= .98 * 524287)),
                          max_step_raw=int(np.max(np.abs(np.diff(raw)))))
    periods = np.diff(selected)
    turns = [] if angle_samples is None else [extract_1x(row, pts_per_rev) for row in
              angle_samples.reshape(-1, pts_per_rev)]
    mean = np.mean(turns) if len(turns) else 0j
    scatter = float(np.sqrt(np.sum(np.abs(np.asarray(turns)-mean)**2)/(len(turns)-1))) if len(turns)>1 else None
    windows = []
    for start in range(0, len(turns), 8):
        group = turns[start:start+8]
        value = complex(np.mean(group))
        windows.append(dict(start_turn=start, turns=len(group), real_g=value.real, imag_g=value.imag,
                            amplitude_g=abs(value), phase_deg=float(np.degrees(np.angle(value)))))
    dt = np.diff(sample_times)
    return dict(windows=windows, axes=axes, near_rail_fraction=.98,
                near_rail_note='screening only; not a calibrated sensor limit',
                sample_interval_seconds=dict(min=float(min(dt)), max=float(max(dt)), median=float(np.median(dt))),
                period_seconds=periods.tolist(),
                period_relative_max_deviation=float(np.max(np.abs(periods/np.median(periods)-1))) if len(periods) else None,
                turn_coefficients_g=[dict(real=v.real, imag=v.imag, amplitude=abs(v), phase_deg=float(np.degrees(np.angle(v)))) for v in turns],
                turn_complex_scatter_g=scatter,
                optical_one_pulse_per_turn_verified=False,
                configuration_verified=capture['metadata'].get('sensor_configuration', {}).get('verified', False)
                    if isinstance(capture['metadata'].get('sensor_configuration'), dict) else False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('--axis', choices=('x', 'y', 'z'), default='z')
    parser.add_argument('--pts-per-rev', type=int, default=64)
    parser.add_argument('--quality-only', action='store_true', help='allow a static quality report')
    parser.add_argument('--out', help='new JSON report; never overwrite')
    args = parser.parse_args(argv)
    try:
        if args.quality_only:
            capture = load_capture(args.directory, allow_static=True)
            if capture['metadata']['status'] == 'STATIC_ONLY':
                if capture['pulses']:
                    times, pulses = clock_times([r[1] for r in capture['samples']], [r[1] for r in capture['pulses']], capture['metadata']['clock_hz'])
                else:
                    times = ticks_to_seconds([r[1] for r in capture['samples']], capture['metadata']['clock_hz'])
                    pulses = np.asarray([], dtype=float)
                selected = pulses[(pulses >= times[0]) & (pulses <= times[-1])]
                result = dict(quality=_quality(capture, times, selected))
            else:
                result = analyze_capture(args.directory, args.axis, args.pts_per_rev)
        else:
            result = analyze_capture(args.directory, args.axis, args.pts_per_rev)
        result['source_sha256'] = {name: hashlib.sha256((Path(args.directory)/name).read_bytes()).hexdigest()
                                   for name in ('samples.csv', 'pulses.csv', 'capture.json')}
        if args.out:
            with Path(args.out).open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(result, indent=2, allow_nan=False)+'\n')
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, 'ERROR: {}\n'.format(error))
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
