"""Single-plane trial-weight calculation from two validated sync capture folders.

Both runs must use the same axis, mounting, optical zero, rotation direction,
sensor configuration and stable speed. Metadata cannot prove those conditions.
This produces an equivalent ADDED-weight suggestion, not physical validation.
"""
import argparse
import cmath
import json
import hashlib
import re
import math
from pathlib import Path

from balance import solve
from sync_csv import analyze_capture, load_capture


def _finite(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + ' must be a finite number')
    try:
        value = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(name + ' must be a finite number') from error
    if not math.isfinite(value) or positive and value <= 0:
        raise ValueError(name + ' must be finite' + (' and positive' if positive else ''))
    return value


def _complex(value):
    return dict(real=float(value.real), imag=float(value.imag))


def _hardware_coefficient(directory, pair, axis):
    from verify_order1x import verify
    from order1x_fixed import prepare_capture
    report = verify(*pair)
    prepared = json.loads(Path(pair[0]).read_text(encoding='utf-8-sig'))
    result = json.loads(Path(pair[1]).read_text(encoding='utf-8-sig'))
    rebuilt = prepare_capture(directory, axis=axis, avg_log2=prepared['avg_log2'])
    for key in ('axis', 'samples_raw', 'source_capture_xsa_sha256'):
        if prepared.get(key) != rebuilt[key]:
            raise ValueError('prepared input does not match capture: ' + key)
    xsa = result.get('accelerator_xsa_sha256')
    if not isinstance(xsa, str) or not re.fullmatch('[0-9a-f]{64}', xsa):
        raise ValueError('missing accelerator XSA provenance')
    report['accelerator_xsa_sha256'] = xsa
    report['source_sha256'] = {name: hashlib.sha256((Path(directory)/name).read_bytes()).hexdigest()
                               for name in ('samples.csv','pulses.csv','capture.json')}
    return complex(report['real_g'], report['imag_g']), prepared['avg_log2'], report


def solve_captures(baseline, trial, *, axis, trial_mass_g, trial_radius_mm,
                   trial_angle_deg, correction_radius_mm, speed_tolerance=.01,
                   fpga_baseline=None, fpga_trial=None):
    """Return a JSON-safe influence result using explicit complex trial g mm.

    Speed mismatch is abs(f_trial-f_baseline)/f_baseline, at most the configured
    tolerance (default 1%). No speed-squared scaling or phase calibration is
    silently applied. Epochs may differ because each run explicitly clears.
    """
    mass = _finite(trial_mass_g, 'trial_mass_g', True)
    radius = _finite(trial_radius_mm, 'trial_radius_mm', True)
    angle = _finite(trial_angle_deg, 'trial_angle_deg') % 360
    correction_radius = _finite(correction_radius_mm, 'correction_radius_mm', True)
    tolerance = _finite(speed_tolerance, 'speed_tolerance')
    if not 0 <= tolerance < 1:
        raise ValueError('speed_tolerance must be in [0, 1)')
    if axis not in ('x', 'y', 'z'):
        raise ValueError('axis must be x, y or z')
    metadata0 = load_capture(baseline)['metadata']
    metadata1 = load_capture(trial)['metadata']
    for field in ('clock_hz', 'xsa_sha256', 'pulses_per_revolution'):
        if metadata0[field] != metadata1[field]:
            raise ValueError('capture pair differs in ' + field)
    baseline_analysis = analyze_capture(baseline, axis=axis)
    trial_analysis = analyze_capture(trial, axis=axis)
    speed_difference = abs(trial_analysis['rotation_hz'] - baseline_analysis['rotation_hz']) / baseline_analysis['rotation_hz']
    if speed_difference > tolerance:
        raise ValueError('relative speed difference {:.6g} exceeds tolerance {:.6g}'.format(speed_difference, tolerance))
    v0 = complex(baseline_analysis['coefficient_real_g'], baseline_analysis['coefficient_imag_g'])
    v1 = complex(trial_analysis['coefficient_real_g'], trial_analysis['coefficient_imag_g'])
    hardware = None
    if (fpga_baseline is None) != (fpga_trial is None):
        raise ValueError('both FPGA prepared/result pairs are required')
    if fpga_baseline is not None:
        v0, avg0, report0 = _hardware_coefficient(baseline, fpga_baseline, axis)
        v1, avg1, report1 = _hardware_coefficient(trial, fpga_trial, axis)
        if avg0 != avg1 or report0['accelerator_xsa_sha256'] != report1['accelerator_xsa_sha256']:
            raise ValueError('FPGA averaging window or accelerator XSA differs')
        hardware = dict(baseline=report0, trial=report1, avg_log2=avg0)
    # Conservative screening policy, not an instrument specification or mechanical approval.
    for analysis in (baseline_analysis, trial_analysis):
        q = analysis['quality']
        if any(v['rail_count'] for v in q['axes'].values()):
            raise ValueError('clipping: full-scale rail samples prohibit quantitative balance')
        if q['period_relative_max_deviation'] > .01:
            raise ValueError('period variation exceeds preliminary 1% screening limit')
        if q['turn_complex_scatter_g'] is None:
            raise ValueError('resolvable response requires at least two complete turns per run')
    scatter = math.hypot(baseline_analysis['quality']['turn_complex_scatter_g'],
                         trial_analysis['quality']['turn_complex_scatter_g'])
    response_floor = max(3 * scatter, 2 / 256000.0)
    if abs(v1-v0) <= response_floor:
        raise ValueError('trial response is not resolvable above turn scatter / raw-count floor')
    trial_gmm = mass * radius * cmath.exp(1j * math.radians(angle))
    if not all(math.isfinite(value) for value in (trial_gmm.real, trial_gmm.imag)):
        raise ValueError('trial mass-radius product exceeds finite range')
    result = solve(v0, v1, trial_gmm, correction_radius)
    output = dict(response_screening=dict(delta_g=abs(v1-v0), combined_turn_scatter_g=scatter, minimum_delta_g=response_floor, scatter_multiplier=3, period_relative_limit=.01, mechanical_approval_required=True), schema_version=1, axis=axis, baseline=baseline_analysis, trial=trial_analysis,
                  baseline_directory=str(Path(baseline)), trial_directory=str(Path(trial)),
                  trial_mass_g=mass, trial_radius_mm=radius, trial_angle_deg=angle,
                  trial_gmm=_complex(trial_gmm), correction_radius_mm=correction_radius,
                  baseline_coefficient_g=_complex(v0), trial_coefficient_g=_complex(v1),
                  influence_coefficient_g_per_gmm=_complex(result['coefficient']),
                  correction_gmm=_complex(result['correction_gmm']),
                  correction_magnitude_gmm=abs(result['correction_gmm']),
                  correction_mass_g=result['mass_g'], correction_angle_deg=result['angle_deg'],
                  angle_semantics='equivalent ADDED weight at the stated correction radius; same optical zero and positive angle direction as trial',
                  speed_relative_difference=speed_difference, speed_tolerance=tolerance,
                  clock_hz=metadata0['clock_hz'], xsa_sha256=metadata0['xsa_sha256'],
                  physical_validation_complete=False, phase_calibrated=False,
                  assumptions=['same sensor axis, rigid mounting and optical zero in both runs',
                               'same rotation direction, angle convention and sensor configuration',
                               'baseline excludes trial weight; trial run adds only the stated known weight',
                               'stable speed and linear single-plane influence response',
                               'sensor/path phase delay has not been independently calibrated'])
    output['coefficient_source'] = 'verified_fpga_iq' if hardware else 'python_full_record'
    output['hardware_verification'] = hardware
    try:
        json.dumps(output, allow_nan=False)
    except ValueError as error:
        raise ValueError('derived balance result exceeds finite range') from error
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline')
    parser.add_argument('trial')
    parser.add_argument('--axis', choices=('x', 'y', 'z'), required=True)
    parser.add_argument('--trial-mass-g', type=float, required=True)
    parser.add_argument('--trial-radius-mm', type=float, required=True)
    parser.add_argument('--trial-angle-deg', type=float, required=True)
    parser.add_argument('--correction-radius-mm', type=float, required=True)
    parser.add_argument('--speed-tolerance', type=float, default=.01,
                        help='relative speed mismatch allowed (default .01 = 1%%)')
    parser.add_argument('--fpga-baseline', nargs=2, metavar=('PREPARED','RESULT'))
    parser.add_argument('--fpga-trial', nargs=2, metavar=('PREPARED','RESULT'))
    parser.add_argument('--out', help='optional new JSON file; existing files are never overwritten')
    args = parser.parse_args(argv)
    if args.out and Path(args.out).exists():
        parser.error('--out already exists; choose a new result file')
    try:
        result = solve_captures(args.baseline, args.trial, axis=args.axis,
                                trial_mass_g=args.trial_mass_g, trial_radius_mm=args.trial_radius_mm,
                                trial_angle_deg=args.trial_angle_deg,
                                correction_radius_mm=args.correction_radius_mm,
                                speed_tolerance=args.speed_tolerance,
                                fpga_baseline=args.fpga_baseline, fpga_trial=args.fpga_trial)
        text = json.dumps(result, indent=2, allow_nan=False) + '\n'
        if args.out:
            with Path(args.out).open('x', encoding='utf-8') as stream:
                stream.write(text)
    except (ValueError, OSError) as error:
        parser.exit(1, 'ERROR: {}\n'.format(error))
    print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
