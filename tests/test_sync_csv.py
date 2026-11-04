"""Strict file integrity plus an end-to-end integer-clock 1x analysis."""
import csv
import importlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sim/golden'))


def parser():
    assert (ROOT / 'sim/golden/sync_csv.py').is_file(), 'missing strict synchronized CSV parser'
    return importlib.import_module('sync_csv')


def fixture(path):
    origin = 2**64 - 2000000
    samples = [(i, (origin + i * 25000) % 2**64,
                round(5120 * np.cos(2 * np.pi * 50 * (i / 4000 - .01) + np.deg2rad(37))),
                0, 256000) for i in range(801)]
    pulses = [(i + 1, (origin + 1000000 + i * 2000000) % 2**64) for i in range(10)]
    for name, header, rows in [('samples.csv', ['frame_id', 'ticks', 'x_raw', 'y_raw', 'z_raw'], samples),
                               ('pulses.csv', ['pulse_count', 'ticks'], pulses)]:
        with (path / name).open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
    meta = dict(schema_version=1, clock_hz=100000000, epoch=1, xsa_sha256='a' * 64,
                sample_count=801, pulse_count=10, sample_drops=0, pulse_drops=0,
                source_missed=0, completed=True, rotation_ready=True, status='PASS', errors=[], hardware_status=6, source_idle=True, pulses_per_revolution=1)
    (path / 'capture.json').write_text(json.dumps(meta))
    return meta


def test_integer_tick_csv_feeds_shared_origin_and_recovers_50hz_37deg(tmp_path):
    fixture(tmp_path)
    result = parser().analyze_capture(tmp_path, axis='x')
    assert result['rotation_hz'] == pytest.approx(50)
    assert result['amplitude_g'] == pytest.approx(.02, rel=.002)
    assert result['phase_deg'] == pytest.approx(37, abs=.02)
    assert result['complete_turns'] == 9


@pytest.mark.parametrize('field,value', [('completed', False), ('status', 'PARTIAL'),
    ('sample_drops', 1), ('pulse_drops', 1), ('source_missed', 1),
    ('sample_count', 800), ('clock_hz', float('nan')), ('epoch', -1),
    ('rotation_ready', False)])
def test_untrusted_metadata_never_silently_enters_balance_analysis(tmp_path, field, value):
    meta = fixture(tmp_path)
    meta[field] = value
    (tmp_path / 'capture.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        parser().analyze_capture(tmp_path, axis='x')


@pytest.mark.parametrize('old,new', [('frame_id,ticks,x_raw,y_raw,z_raw', 'frame,ticks,x_raw,y_raw,z_raw'),
    ('0,18446744073707551616,', '0,18446744073707551616.0,'),
    ('1,18446744073707576616,', '2,18446744073707576616,'),
    ('1,18446744073707576616,', '1,18446744073707551616,')])
def test_bad_columns_decimal_ticks_gaps_and_duplicate_times_rejected(tmp_path, old, new):
    fixture(tmp_path)
    file = tmp_path / 'samples.csv'
    text = file.read_text()
    assert old in text
    file.write_text(text.replace(old, new, 1))
    with pytest.raises(ValueError):
        parser().load_capture(tmp_path)


def test_raw_axis_outside_signed20_rejected(tmp_path):
    fixture(tmp_path)
    file = tmp_path / 'samples.csv'
    file.write_text(file.read_text().replace(',0,256000', ',0,524288', 1))
    with pytest.raises(ValueError):
        parser().load_capture(tmp_path)


def test_static_only_can_be_loaded_explicitly_but_cannot_be_analyzed(tmp_path):
    meta = fixture(tmp_path)
    (tmp_path / 'pulses.csv').write_text('pulse_count,ticks\n')
    meta.update(pulse_count=0, rotation_ready=False, status='STATIC_ONLY')
    (tmp_path / 'capture.json').write_text(json.dumps(meta))
    data = parser().load_capture(tmp_path, allow_static=True)
    assert len(data['samples']) == 801
    with pytest.raises(ValueError):
        parser().analyze_capture(tmp_path)

@pytest.mark.parametrize('filename,old,new', [
    ('pulses.csv', '1,18446744073708551616', '2,18446744073708551616'),
    ('pulses.csv', '2,1000000', '3,1000000'),
    ('samples.csv', '0,18446744073707551616', '0,-1'),
])
def test_bad_pulse_counts_or_sample_tick_range_is_rejected(tmp_path, filename, old, new):
    fixture(tmp_path)
    file = tmp_path / filename
    text = file.read_text()
    assert old in text
    file.write_text(text.replace(old, new, 1))
    with pytest.raises(ValueError):
        parser().load_capture(tmp_path)


def test_parser_refuses_unexpected_pulses_per_revolution(tmp_path):
    meta = fixture(tmp_path)
    meta['pulses_per_revolution'] = 2
    (tmp_path / 'capture.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        parser().analyze_capture(tmp_path)


def test_hardware_overflow_flag_cannot_be_hidden_by_zero_drop_metadata(tmp_path):
    meta = fixture(tmp_path)
    meta['hardware_status'] = 8
    (tmp_path / 'capture.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        parser().load_capture(tmp_path)


def test_complete_epoch_requires_initial_frame_zero(tmp_path):
    fixture(tmp_path)
    file = tmp_path / 'samples.csv'
    rows = file.read_text().splitlines()
    file.write_text(rows[0] + '\n' + '\n'.join(','.join([str(int(row.split(',')[0]) + 1)] + row.split(',')[1:]) for row in rows[1:]) + '\n')
    with pytest.raises(ValueError):
        parser().load_capture(tmp_path)


def test_source_not_idle_is_rejected(tmp_path):
    meta = fixture(tmp_path)
    meta['source_idle'] = False
    (tmp_path / 'capture.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        parser().load_capture(tmp_path)
