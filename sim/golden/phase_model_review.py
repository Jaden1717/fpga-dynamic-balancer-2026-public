"""Dimensionless sensitivity diagnostics beside the unchanged four-run solver.

No mass or placement angle is recommended. Scenarios are descriptive what-if
calculations, not calibrated uncertainty, confidence limits or acceptance tests.
"""
import argparse
import itertools
import json
import math
from numbers import Real
from pathlib import Path
import statistics
import numpy as np


def _numbers(values, name):
    out = []
    for v in values:
        if isinstance(v, (bool, np.bool_)) or not isinstance(v, Real) or not math.isfinite(v):
            raise ValueError(name+' requires finite numeric values')
        out.append(float(v))
    return out


def diagnose(amplitudes_g, angles_deg):
    """Describe the original squared-amplitude equations without a PASS gate."""
    a = _numbers(amplitudes_g, 'amplitudes_g')
    angles = _numbers(angles_deg, 'angles_deg')
    if len(a) != 4 or min(a) < 0 or len(angles) != 3:
        raise ValueError('one nonnegative baseline, three trial amplitudes and angles required')
    theta = np.radians(angles)
    design = np.column_stack((np.ones(3), np.cos(theta), np.sin(theta)))
    condition = float(np.linalg.cond(design))
    if np.linalg.matrix_rank(design) != 3 or not math.isfinite(condition) or condition > 1e6:
        raise ValueError('trial geometry is singular or numerically ill-conditioned')
    scale = max(a)
    base = dict(amplitudes_g=a, angles_deg=angles, geometry_condition_number=condition,
                normalized_algebra_vector=None, closure_relative_error=None,
                baseline_only_closure_roots_g=[], correction_allowed=False,
                physical_validation_complete=False,
                scope='unconstrained algebra diagnostic only; no mass, angle, physical approval or change to original solver')
    if scale == 0:
        return dict(base, algebra_status='nonpositive_q', q_g2=0.)
    normalized = np.asarray(a)/scale
    q0, x, y = (float(v) for v in np.linalg.solve(design, normalized[1:]**2))
    q = q0-normalized[0]**2
    base['q_g2'] = q*scale*scale
    if q <= 0:
        return dict(base, algebra_status='nonpositive_q')
    vector = -complex(x, y)/(2*q)
    left, right = x*x+y*y, 4*normalized[0]**2*q
    closure = abs(left-right)/max(left, right, np.finfo(float).tiny)
    if normalized[0] == 0 and math.hypot(x,y) <= 1e-12*q:
        closure = 0.
    roots = []
    # Changing only the hypothetical baseline requires
    # s^2-q0*s+(x^2+y^2)/4=0, s=A0^2 in scaled units.
    discriminant = q0*q0-left
    tolerance = 64*np.finfo(float).eps*max(q0*q0,left,np.finfo(float).tiny)
    if q0 > 0 and discriminant >= -tolerance:
        d = math.sqrt(max(0.,discriminant))
        for s in ((q0-d)/2, (q0+d)/2):
            if s >= -tolerance and q0-s > tolerance:
                value = math.sqrt(max(0.,s))*scale
                if not roots or not math.isclose(value, roots[-1], rel_tol=1e-12, abs_tol=0):
                    roots.append(value)
    implied_baseline = math.hypot(x,y)/(2*math.sqrt(q))*scale
    base.update(algebra_status='finite_positive_q',
                normalized_algebra_vector=dict(real=vector.real, imag=vector.imag, magnitude=abs(vector)),
                closure_relative_error=float(closure),
                unconstrained_implied_baseline_g=implied_baseline,
                implied_minus_measured_baseline_g=implied_baseline-a[0],
                baseline_only_closure_roots_g=roots,
                baseline_root_scope='hypothetical baseline-only changes; both roots retained; never replace measured data to force closure')
    return base


def _scenario(label, amplitudes, angles):
    if min(amplitudes) < 0:
        return dict(label=label, amplitudes_g=amplitudes, angles_deg=angles,
                    algebra_status='invalid_negative_amplitude_scenario',
                    normalized_algebra_vector=None, correction_allowed=False)
    return dict(label=label, **diagnose(amplitudes,angles))


def review_windows(amplitude_windows_g, angles_deg, angle_perturb_deg=5):
    """Retain all windows; probe each record's SD and both temporal halves.

    Window SD is used as an explicit perturbation size, not an estimate of
    independent-run mean error. Half combinations do not create independent runs.
    """
    groups = [_numbers(g, 'amplitude windows') for g in amplitude_windows_g]
    if len(groups) != 4 or any(len(g)<4 or len(g)%2 or min(g)<0 for g in groups):
        raise ValueError('four nonnegative records with even window counts >=4 required')
    angles = _numbers(angles_deg, 'angles_deg')
    delta = _numbers([angle_perturb_deg], 'angle perturbation')[0]
    if delta < 0:
        raise ValueError('angle perturbation must be nonnegative')
    means = [statistics.mean(g) for g in groups]
    deviations = [statistics.stdev(g) for g in groups]
    point = diagnose(means,angles)
    amplitude_scenarios, angle_scenarios, halves = [], [], []
    for j, sd in enumerate(deviations):
        for sign in (-1,1):
            changed = list(means)
            changed[j] += sign*sd
            amplitude_scenarios.append(_scenario('record%d_%+d_window_sd'%(j,sign),changed,angles))
    for j in range(3):
        for sign in (-1,1):
            changed = list(angles)
            changed[j] += sign*delta
            angle_scenarios.append(_scenario('trial%d_angle_%+gdeg'%(j,sign*delta),means,changed))
    half_means = [(statistics.mean(g[:len(g)//2]), statistics.mean(g[len(g)//2:])) for g in groups]
    for choices in itertools.product((0,1), repeat=4):
        changed = [half_means[j][side] for j,side in enumerate(choices)]
        halves.append(dict(half_choices=list(choices), **_scenario('halves_'+''.join(map(str,choices)),changed,angles)))
    return dict(schema_version=1, point=point,
                records=[dict(window_count=len(g),mean_g=m,sample_sd_g=sd,
                              cv=sd/m if m else None,min_g=min(g),max_g=max(g))
                         for g,m,sd in zip(groups,means,deviations)],
                amplitude_scenarios=amplitude_scenarios, angle_scenarios=angle_scenarios,
                half_record_scenarios=halves, angle_perturbation_degrees=delta,
                uncertainty_scope='window SD/temporal halves and illustrative angle changes only; no independent-run error model, confidence interval or pass rate',
                correction_allowed=False, physical_validation_complete=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('measurements',help='JSON: amplitude_windows_g, angles_deg, optional angle_perturb_deg')
    parser.add_argument('--out',required=True)
    args = parser.parse_args(argv)
    try:
        if Path(args.out).exists():
            raise ValueError('output already exists')
        document = json.loads(Path(args.measurements).read_text(encoding='utf-8-sig'))
        if not isinstance(document,dict):
            raise ValueError('measurement document must be an object')
        report = review_windows(**document)
        with Path(args.out).open('x',encoding='utf-8') as stream:
            json.dump(report,stream,indent=2,allow_nan=False)
            stream.write('\n')
    except (OSError,ValueError,TypeError) as error:
        parser.exit(1,str(error)+'\n')
    print('Sensitivity report saved; original solver unchanged; no correction mass or angle advice.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
