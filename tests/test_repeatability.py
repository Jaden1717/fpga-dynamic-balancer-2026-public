"""Breaks covered: omitted windows, false stability passes, unbound raw data,
reused captures, incompatible runs, and overwriting archived evidence."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'sim/golden'))


def api():
    assert (ROOT/'sim/golden/repeatability.py').is_file(), 'repeatability stage tool missing'
    return importlib.import_module('repeatability')


def summary(values=None, rpm=1710):
    return api().summarize([.0002]*8 if values is None else values, rpm, axis='z')


def test_constant_windows_pass_without_claiming_physical_balance():
    r=summary()
    assert r['mean_peak_mg']==pytest.approx(.2)
    assert r['cv']==pytest.approx(0)
    assert r['within_record_repeatability_pass'] is True
    assert r['physical_validation_complete'] is False
    assert r['correction_allowed'] is False


def test_varying_windows_fail_instead_of_discarding_worst_window():
    r=summary([.0001]*4+[.0003]*4)
    assert r['cv']>1/2
    assert not r['within_record_repeatability_pass']
    assert len(r['amplitudes_g'])==8


@pytest.mark.parametrize('values',[[.0002]*7,[.0002]*9,[0.0]*8,[-.1]*8,[float('nan')]*8,[float('inf')]*8,[True]*8])
def test_bad_or_incomplete_windows_rejected(values):
    with pytest.raises(ValueError):summary(values)


def test_pair_uses_symmetric_mean_difference_and_checks_speed():
    r=api().compare_summaries(summary(),summary([.00021]*8))
    assert r['mean_relative_difference']==pytest.approx(.04878048780487805)
    assert r['repeatability_pass'] is True
    assert r['correction_allowed'] is False
    assert not api().compare_summaries(summary(),summary(rpm=1740))['repeatability_pass']


def test_pair_rejects_instability_and_axis_mismatch():
    assert not api().compare_summaries(summary(),summary([.0001]*4+[.0003]*4))['repeatability_pass']
    other=summary();other['axis']='x'
    with pytest.raises(ValueError,match='axis'):api().compare_summaries(summary(),other)


def sealed_capture(tmp_path):
    files={'capture.json':b'{"epoch":1,"xsa_sha256":"' + b'a'*64+b'"}', 'samples.csv':b'samples', 'pulses.csv':b'pulses'}
    for name,value in files.items():(tmp_path/name).write_bytes(value)
    obs=dict(magic=0x48585331,abi_version=65536,sync_ctrl=0,sync_status=6,acq_ctrl=0,acq_status=14,source_idle=1,sample_pending=0,pulse_pending=0,sample_drops=0,pulse_drops=0,source_missed=0,sample_total=10,epoch=1)
    stop=dict(format='hengxin-hxs1-stop-snapshot-v1',read_only=True,verdict='PASS',observations=[obs,copy.deepcopy(obs)],file_sha256={n:hashlib.sha256(v).hexdigest() for n,v in files.items()},xsa_sha256='a'*64)
    (tmp_path/'stop_snapshot.json').write_text(json.dumps(stop))
    return stop


def test_stop_check_binds_hashes_and_freeze_state(tmp_path):
    sealed_capture(tmp_path)
    r=api().check_stop(tmp_path,10,1,'a'*64)
    assert r['samples.csv']==hashlib.sha256(b'samples').hexdigest()
    (tmp_path/'samples.csv').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash'):api().check_stop(tmp_path,10,1,'a'*64)


@pytest.mark.parametrize('mutate',['observations','missing_hash','pending','count','epoch','xsa','magic'])
def test_stop_claim_alone_is_not_enough(tmp_path,mutate):
    stop=sealed_capture(tmp_path)
    if mutate=='observations':stop['observations'][1]['sample_total']=11
    if mutate=='missing_hash':del stop['file_sha256']['samples.csv']
    if mutate=='pending':
        for o in stop['observations']:o['sample_pending']=1
    if mutate=='count':
        for o in stop['observations']:o['sample_total']=11
    if mutate=='epoch':
        for o in stop['observations']:o['epoch']=2
    if mutate=='xsa':stop['xsa_sha256']='b'*64
    if mutate=='magic':
        for o in stop['observations']:o['magic']=0
    (tmp_path/'stop_snapshot.json').write_text(json.dumps(stop))
    with pytest.raises(ValueError):api().check_stop(tmp_path,10,1,'a'*64)


def test_existing_output_is_not_modified(tmp_path):
    out=tmp_path/'existing';out.mkdir();(out/'keep').write_text('keep')
    with pytest.raises((ValueError,SystemExit)):
        api().main(['review','missing','--out',str(out)])
    assert (out/'keep').read_text()=='keep'


def test_same_capture_cannot_count_as_two_independent_starts():
    raw=ROOT/'data/physical/hx_20261028_003/board_export/trial_a_20251021_185122'
    with pytest.raises(ValueError,match='same capture'):
        api().compare_captures(raw,raw)

def test_historical_raw_capture_rebuilds_aa_windows_and_preserves_evidence(tmp_path):
    raw=ROOT/'data/physical/hx_20261028_003/board_export/trial_a_20251021_185122'
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir() if p.is_file()}
    out=tmp_path/'analysis'
    r=api().review_capture(raw,out=out)
    assert r['mean_peak_mg']==pytest.approx(.254868,abs=.000001)
    assert r['cv']==pytest.approx(.0718,abs=.0001)
    assert r['within_record_repeatability_pass'] is True
    assert r['hardware_windows_verified']==0
    assert r['correction_allowed'] is False
    assert r['physical_validation_complete'] is False
    saved=ROOT/'data/physical/hx_20261028_003/antialias_20261029'
    for start in range(0,512,64):
        expected=json.loads((saved/('trial_a_20251021_185122_z64_%d.json'%start)).read_text())
        actual=json.loads((out/('input_%d.json'%start)).read_text())
        assert actual['samples_raw']==expected['samples_raw']
        assert actual['reference']==expected['reference']
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir() if p.is_file()}
    assert b'\r' not in (out/'sha256sums.txt').read_bytes()
    for line in (out/'sha256sums.txt').read_text().splitlines():
        value,name=line.split('  ')
        assert hashlib.sha256((out/name).read_bytes()).hexdigest()==value


def test_return_a_instability_stays_failed():
    raw=ROOT/'data/physical/hx_20261028_003/board_export/trial_a_return_20251021_192501'
    r=api().review_capture(raw)
    assert r['mean_peak_mg']==pytest.approx(.224082,abs=.000001)
    assert r['cv']==pytest.approx(.1940,abs=.0001)
    assert r['within_record_repeatability_pass'] is False
    assert r['correction_allowed'] is False


def test_wrong_compute_overlay_is_rejected_before_any_output_or_hardware_access(tmp_path):
    xsa=tmp_path/'wrong.xsa';xsa.write_bytes(b'wrong overlay')
    out=tmp_path/'out'
    with pytest.raises(ValueError,match='compute XSA'):
        api().review_capture('missing',out=out,xsa=xsa)
    assert not out.exists()

def test_live_drdy_status_may_toggle_after_frozen_capture(tmp_path):
    stop=sealed_capture(tmp_path)
    stop['observations'][0]['acq_status']=10
    stop['observations'][1]['acq_status']=14
    (tmp_path/'stop_snapshot.json').write_text(json.dumps(stop))
    assert api().check_stop(tmp_path,10,1,'a'*64)['samples.csv']==hashlib.sha256(b'samples').hexdigest()


def test_busy_acquisition_status_is_not_a_valid_stopped_capture(tmp_path):
    stop=sealed_capture(tmp_path)
    for obs in stop['observations']:obs['acq_status']=11
    (tmp_path/'stop_snapshot.json').write_text(json.dumps(stop))
    with pytest.raises(ValueError):api().check_stop(tmp_path,10,1,'a'*64)


def test_other_status_bits_still_must_be_stable(tmp_path):
    stop=sealed_capture(tmp_path)
    stop['observations'][0]['acq_status']=14
    stop['observations'][1]['acq_status']=6
    (tmp_path/'stop_snapshot.json').write_text(json.dumps(stop))
    with pytest.raises(ValueError):api().check_stop(tmp_path,10,1,'a'*64)

def test_128_turn_protocol_keeps_eight_fixed_windows_and_same_cv_limit():
    r=api().summarize([.0002]*8,1710,axis='z',avg_log2=7)
    assert r['turns_per_window']==128
    assert r['start_turns']==[0,128,256,384,512,640,768,896]
    assert r['mean_peak_mg']==pytest.approx(.2)
    assert r['criteria']['cv_limit']==.10
    assert r['correction_allowed'] is False


def test_protocol_comparison_cannot_mix_64_and_128_turn_results():
    other=api().summarize([.0002]*8,1710,axis='z',avg_log2=7)
    with pytest.raises(ValueError):api().compare_summaries(summary(),other)


@pytest.mark.parametrize('avg',[5,8,True,6.0])
def test_measurement_protocol_rejects_unplanned_window_lengths(avg):
    with pytest.raises(ValueError):api().summarize([.0002]*8,1710,axis='z',avg_log2=avg)


def test_128_turn_protocol_rejects_short_old_capture_without_output(tmp_path):
    raw=ROOT/'data/physical/hx_20261028_003/board_export/trial_a_20251021_185122'
    out=tmp_path/'analysis'
    with pytest.raises(ValueError,match='(window|revolution|turn)'):
        api().review_capture(raw,out=out,avg_log2=7)
    assert not out.exists()