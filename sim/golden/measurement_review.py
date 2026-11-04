"""Offline measurement diagnostics on frozen captures, never balance approval.

All fixed windows are retained. Candidate fits do not replace FPGA results.
TACH parity comparisons use half-turn-shifted intervals, not identical windows.
Only NumPy is required; no imported third-party algorithm implementation.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import numpy as np
from angle_antialias import resample_antialiased
from repeatability import prepare_record, protocol_starts, COMPUTE_XSA, save_json
from sync_csv import load_capture
from sync_order import extract_1x
from timebase import clock_times
from verify_order1x import verify


def fit_order(t, values_g, edges, *, include_trend=True, nuisance_orders=(),
              nuisance_hz=(), max_condition=1e6):
    """Fit DC/cos(theta)/sin(theta), optional linear drift and explicit tones.

    Edges delimit complete turns and share the sample clock. No outlier removal
    or automatic frequency search. Residual RMS includes unmodelled harmonics;
    it is not a calibrated noise floor or uncertainty of the 1x coefficient.
    """
    t, x, edges = [np.asarray(v, dtype=float) for v in (t, values_g, edges)]
    if (t.ndim != 1 or x.shape != t.shape or len(t) < 4
            or not np.isfinite(t).all() or not np.isfinite(x).all()
            or np.any(np.diff(t) <= 0)):
        raise ValueError('finite, aligned and strictly increasing samples required')
    if (edges.ndim != 1 or len(edges) < 2 or not np.isfinite(edges).all()
            or np.any(np.diff(edges) <= 0) or edges[0] < t[0] or edges[-1] > t[-1]):
        raise ValueError('turn edges must increase inside sample span')
    if not math.isfinite(max_condition) or max_condition <= 1:
        raise ValueError('invalid numerical condition bound')
    orders, frequencies = list(nuisance_orders), list(nuisance_hz)
    for value in orders + frequencies:
        if isinstance(value, (bool, np.bool_)) or not np.isfinite(value) or value <= 0:
            raise ValueError('nuisance orders/frequencies must be positive finite numbers')
    keep = (t >= edges[0]) & (t < edges[-1])
    selected_t, selected_x = t[keep], x[keep]
    phase = np.interp(selected_t, edges, np.arange(len(edges)) * 2*np.pi)
    duration = edges[-1] - edges[0]
    tau = (selected_t - (edges[0]+edges[-1])/2) / duration
    columns = [np.ones(len(selected_t)), np.cos(phase), np.sin(phase)]
    if include_trend:
        columns.append(tau)
    for order in orders:
        columns.extend([np.cos(order*phase), np.sin(order*phase)])
    for frequency in frequencies:
        angle = 2*np.pi*frequency*(selected_t-(edges[0]+edges[-1])/2)
        columns.extend([np.cos(angle), np.sin(angle)])
    design = np.column_stack(columns)
    if len(selected_x) <= design.shape[1]:
        raise ValueError('not enough samples to identify model')
    beta, _, rank, singular = np.linalg.lstsq(design, selected_x, rcond=None)
    condition = float(singular[0]/singular[-1]) if singular[-1] else math.inf
    if rank != design.shape[1] or not math.isfinite(condition) or condition > max_condition:
        raise ValueError('model is not identifiable: rank/condition failure')
    coefficient = complex(beta[1], -beta[2])
    return dict(real_g=coefficient.real, imag_g=coefficient.imag,
                amplitude_g=abs(coefficient),
                phase_deg=math.degrees(math.atan2(coefficient.imag, coefficient.real)) if coefficient else None,
                trend_change_g=float(beta[3]) if include_trend else 0.,
                dc_at_window_midpoint_g=float(beta[0]),
                residual_rms_g=float(np.sqrt(np.mean((selected_x-design@beta)**2))),
                numerical_condition=condition, samples=len(selected_x),
                nuisance_orders=orders, nuisance_hz=frequencies,
                include_trend=bool(include_trend), physical_validation_complete=False)


def complex_stability(coefficients):
    """Describe same-record phasors, without independence/confidence assumptions."""
    values = np.asarray(list(coefficients), dtype=complex)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError('at least two finite complex coefficients required')
    magnitudes = np.abs(values)
    mean = complex(np.mean(values))
    mean_amplitude = float(np.mean(magnitudes))
    sd = float(np.std(magnitudes, ddof=1))
    return dict(windows=len(values), mean_amplitude_g=mean_amplitude,
                amplitude_sample_sd_g=sd,
                amplitude_cv=sd/mean_amplitude if mean_amplitude else None,
                mean_real_g=mean.real, mean_imag_g=mean.imag,
                coherent_amplitude_g=abs(mean),
                phasor_coherence=min(1., abs(mean)/mean_amplitude) if mean_amplitude else None,
                complex_sample_sd_g=float(np.sqrt(np.sum(np.abs(values-mean)**2)/(len(values)-1))),
                adjacent_complex_difference_scale_g=float(np.sqrt(np.mean(np.abs(np.diff(values))**2)/2)),
                scope='same-record descriptors; adjacent windows may be correlated; no confidence interval or restart reproducibility claim',
                correction_allowed=False)


def _coefficient(value):
    return complex(value['real_g'], value['imag_g'])


def review_capture(directory, *, avg_log2=7, hardware_analysis=None, nuisance_hz=()):
    """Cross-check all 8 fixed windows; never choose a variant by its CV."""
    directory = Path(directory)
    prepared, hashes = prepare_record(directory, avg_log2=avg_log2)
    capture = load_capture(directory)
    meta, rows, pulses = capture['metadata'], capture['samples'], capture['pulses']
    t, p = clock_times([r[1] for r in rows], [r[1] for r in pulses], meta['clock_hz'])
    raw = np.asarray([r[4] for r in rows], dtype=float)/256000.
    indices = []
    for parity in (0, 1):
        select = np.arange(parity, len(pulses), 2)
        indices.append(select[(p[select] >= t[0]) & (p[select] <= t[-1])])
    turns = 1 << avg_log2
    windows, hardware = [], []
    for start, item in zip(protocol_starts(avg_log2), prepared):
        edges = p[indices[0][start:start+turns+1]]
        fits = dict(time_ls=fit_order(t, raw, edges, include_trend=False),
                    time_ls_drift=fit_order(t, raw, edges),
                    time_ls_drift_9x=fit_order(t, raw, edges, nuisance_orders=(9,)))
        if nuisance_hz:
            fits['time_ls_drift_9x_tones'] = fit_order(
                t, raw, edges, nuisance_orders=(9,), nuisance_hz=nuisance_hz)
        base = item['floating_same_words']
        alternate = None
        if start+turns < len(indices[1]):
            alternate = extract_1x(resample_antialiased(
                t, raw, p[indices[1][start:start+turns+1]]))
        entry = dict(start_turn=start, start_tick=item['window']['start_tick'],
                     end_tick=item['window']['end_tick'], fits=fits,
                     aa_parity0=dict(real_g=base['real_g'], imag_g=base['imag_g']),
                     aa_parity1=None if alternate is None else dict(real_g=alternate.real, imag_g=alternate.imag),
                     parity_relative_amplitude_difference=None if alternate is None else
                     abs(abs(alternate)-base['amplitude_g'])/((abs(alternate)+base['amplitude_g'])/2)
                     if abs(alternate)+base['amplitude_g'] else 0.)
        if hardware_analysis is not None:
            board = Path(hardware_analysis)
            input_path, result_path = board/('input_%d.json'%start), board/('result_%d.json'%start)
            archived = json.loads(input_path.read_text(encoding='utf-8-sig'))
            result = json.loads(result_path.read_text(encoding='utf-8-sig'))
            for name in ('samples_raw', 'source_sha256', 'axis', 'avg_log2', 'window', 'preprocessing'):
                if archived.get(name) != item.get(name):
                    raise ValueError('hardware input/capture binding mismatch: '+name)
            if (result.get('source_provenance') != {k:v for k,v in archived.items() if k != 'samples_raw'}
                    or result.get('accelerator_xsa_sha256') != COMPUTE_XSA):
                raise ValueError('hardware provenance mismatch')
            checked = verify(input_path, result_path)
            entry['fpga_verified'] = checked
            hardware.append(complex(checked['real_g'], checked['imag_g']))
        windows.append(entry)
    after = {name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in hashes}
    if hashes != after:
        raise ValueError('source changed during diagnostic review')
    methods = {name:complex_stability([_coefficient(w['fits'][name]) for w in windows])
               for name in windows[0]['fits']}
    methods['aa_parity0'] = complex_stability([_coefficient(w['aa_parity0']) for w in windows])
    alternate = [w['aa_parity1'] for w in windows if w['aa_parity1'] is not None]
    if len(alternate) >= 2:
        methods['aa_parity1'] = complex_stability([_coefficient(v) for v in alternate])
    fpga_summary = None
    if hardware:
        fpga_summary = complex_stability(hardware)
        # Amplitude statistics use the verified magnitude register, not hypot(I,Q)
        # or an unvalidated display field; retain I/Q for phasor descriptors.
        amplitudes = [w['fpga_verified']['amplitude_g'] for w in windows]
        mean = statistics.mean(amplitudes)
        sd = statistics.stdev(amplitudes)
        fpga_summary.update(mean_amplitude_g=mean, amplitude_sample_sd_g=sd,
                            amplitude_cv=sd/mean if mean else None)
    return dict(schema_version=1, axis='z', avg_log2=avg_log2, turns_per_window=turns,
                source_sha256=hashes, source_capture_directory=str(directory),
                sensor_configuration_bound=meta.get('sensor_configuration') is not None,
                driver_identity_bound=meta.get('driver_sha256') is not None,
                hardware_windows_verified=len(hardware),
                amplitude_source='archived FPGA registers plus CPU diagnostics' if hardware else 'CPU diagnostics only; no new FPGA evidence',
                windows=windows, methods=methods, fpga_summary=fpga_summary,
                parity_scope='opposite selected TACH edges; half-turn-shifted windows and unknown angular separation; amplitude sensitivity only, not absolute phase calibration',
                fit_scope='candidate diagnostics on the same fixed windows; no outlier removal, automatic tone search, estimator replacement or acceptance by minimum CV',
                correction_allowed=False, physical_validation_complete=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('--avg-log2', type=int, choices=(6,7), default=7)
    parser.add_argument('--fpga-analysis')
    parser.add_argument('--nuisance-hz', action='append', type=float, default=[])
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if Path(args.out).exists():
            raise ValueError('output already exists')
        report = review_capture(args.capture, avg_log2=args.avg_log2,
                                hardware_analysis=args.fpga_analysis,
                                nuisance_hz=args.nuisance_hz)
        save_json(args.out, report)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, str(error)+'\n')
    print('Saved all 8 windows; verified archived FPGA windows=%d; diagnostics do not approve correction.' %
          report['hardware_windows_verified'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
