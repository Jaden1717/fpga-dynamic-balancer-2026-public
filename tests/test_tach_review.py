import csv
import importlib
import json
from pathlib import Path
import pytest
from test_sync_csv import fixture, ROOT


def reviewer():
    assert (ROOT / 'sim/golden/tach_review.py').is_file(), 'missing TACH diagnostic'
    return importlib.import_module('tach_review')


def run(path, **kwargs):
    return reviewer().review_capture(path, source='fan-tach-2ppr', rpm_min=1000, rpm_max=2000, **kwargs)


def test_two_pulses_per_turn_preserves_raw_and_wrap(tmp_path):
    fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = run(tmp_path)
    assert result['rpm_median'] == pytest.approx(1500)
    assert result['recorded_pulses_per_revolution'] == 1
    assert result['interpreted_pulses_per_revolution'] == 2
    assert result['status'] == 'REVIEW_REQUIRED'
    assert result['flags'] == []
    assert result['absolute_phase_available'] is False
    assert all((tmp_path / name).read_bytes() == data for name, data in before.items())
    assert set(result['source_sha256']) == set(before)


def test_source_must_be_explicit(tmp_path):
    fixture(tmp_path)
    with pytest.raises(ValueError, match='source'):
        reviewer().review_capture(tmp_path, source='optical', rpm_min=1000, rpm_max=2000)


def test_unequal_half_turn_spacing_not_reported_as_speed_ripple(tmp_path):
    fixture(tmp_path)
    origin = 2**64 - 2000000
    ticks = [(origin + 1000000 + (i//2)*4000000 + (1000000 if i%2 else 0)) % 2**64 for i in range(10)]
    with (tmp_path/'pulses.csv').open('w', newline='') as f:
        w=csv.writer(f); w.writerow(['pulse_count','ticks']); w.writerows(enumerate(ticks,1))
    result=run(tmp_path)
    assert result['rpm_median'] == pytest.approx(1500)
    assert result['revolution_period_relative_max_deviation'] == pytest.approx(0, abs=1e-12)
    assert result['adjacent_interval_seconds']['max'] / result['adjacent_interval_seconds']['min'] == pytest.approx(3)


def test_burst_not_fixed_by_dropping_pulses(tmp_path):
    fixture(tmp_path)
    p=tmp_path/'pulses.csv'
    rows=list(csv.reader(p.open()))
    rows[2][1]=str((int(rows[1][1])+14) % 2**64)
    with p.open('w', newline='') as f: csv.writer(f).writerows(rows)
    result=run(tmp_path)
    assert result['status']=='REJECT'
    assert 'sub_microsecond_edges' in result['flags']
    assert result['in_record_pulse_count']==10


def test_static_and_loss_not_accepted(tmp_path):
    meta=fixture(tmp_path)
    meta.update(status='STATIC_ONLY',rotation_ready=False,pulse_count=0)
    (tmp_path/'capture.json').write_text(json.dumps(meta))
    (tmp_path/'pulses.csv').write_text('pulse_count,ticks\n')
    assert run(tmp_path)['status']=='INSUFFICIENT_DATA'
    meta['sample_drops']=1
    (tmp_path/'capture.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='data loss'): run(tmp_path)


@pytest.mark.parametrize('low,high', [(0,2000),(2000,1000),(1000,float('nan'))])
def test_invalid_bounds(tmp_path,low,high):
    fixture(tmp_path)
    with pytest.raises(ValueError):
        reviewer().review_capture(tmp_path,source='fan-tach-2ppr',rpm_min=low,rpm_max=high)
