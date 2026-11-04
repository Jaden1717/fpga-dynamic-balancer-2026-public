import hashlib
import importlib.util
import json
from pathlib import Path
import uuid
import pytest

MODULE=Path(__file__).parent/'deploy/finish_control.py'
NAMES=['01_reference_before','02_trial_000','03_trial_120','04_trial_240','05_reference_after']
BOOT=str(uuid.uuid5(uuid.NAMESPACE_URL,'control-batch-boot'))

def helper():
    if not MODULE.exists():pytest.fail('Five-state batch descriptor is not implemented')
    spec=importlib.util.spec_from_file_location('finish_control_under_test',MODULE)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m

def make_run(root,index,mean_mg,boot=BOOT):
    d=root/NAMES[index];d.mkdir(parents=True)
    hashes={n:hashlib.sha256((str(index)+n).encode()).hexdigest() for n in ['samples.csv','pulses.csv','capture.json','stop_snapshot.json']}
    amps=[mean_mg*.001]*8
    value=dict(kind='hengxin-measurement-summary-v1',data_integrity='PASS',protocol=dict(method='aa128-to64',axis='z',avg_log2=7,start_turns=list(range(0,1024,128))),source_sha256=hashes,measurement=dict(amplitudes_g=amps,mean_peak_mg=mean_mg,cv=0.,rpm_median=1715.,amplitude_source='CPU reference (not new FPGA evidence)',hardware_windows_verified=0),runtime_context=dict(source_sha256=hashes,before=dict(boot_id=boot),after=dict(boot_id=boot),initialization_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'init'+str(index))),sensor_configuration=dict(verified=True,registers=dict(DEVID=173,FILTER=0,RANGE=129,POWER_CTL=0))))
    (d/'summary.json').write_text(json.dumps(value),encoding='utf8')
    return d/'summary.json'

def test_five_states_use_fixed_order_and_describe_bracket_change(tmp_path):
    for i,a in enumerate([.8,.4,1.2,1.,.82]):make_run(tmp_path,i,a)
    r=helper().describe(tmp_path)
    assert r['status']=='COMPLETED'
    assert r['completed_runs']==5
    assert [v['name'] for v in r['runs']]==NAMES
    assert r['baseline_change_mg']==pytest.approx(.02)
    assert r['same_observed_boot'] is True
    assert r['automatic_extra_capture'] is False
    assert r['correction_allowed'] is False
    assert r['physical_validation_complete'] is False

def test_partial_batch_retains_completed_records_without_fabricating_rest(tmp_path):
    for i,a in enumerate([.8,.4]):make_run(tmp_path,i,a)
    r=helper().describe(tmp_path)
    assert r['status']=='PARTIAL' and r['completed_runs']==2
    assert r['missing_runs']==NAMES[2:]
    assert r['baseline_change_mg'] is None
    assert r['automatic_extra_capture'] is False

def test_reboot_is_reported_without_dropping_measured_states(tmp_path):
    for i,a in enumerate([.8,.4,1.2,1.,.82]):make_run(tmp_path,i,a,BOOT if i<2 else str(uuid.uuid5(uuid.NAMESPACE_URL,'changed')))
    r=helper().describe(tmp_path)
    assert r['status']=='COMPLETED_WITH_DEVIATIONS' and r['completed_runs']==5
    assert r['same_observed_boot'] is False
    assert 'boot_changed_or_unknown' in r['deviations']
    assert r['correction_allowed'] is False

def test_tampered_display_mean_is_not_used_as_measurement(tmp_path):
    for i,a in enumerate([.8,.4,1.2,1.,.82]):make_run(tmp_path,i,a)
    p=tmp_path/NAMES[1]/'summary.json';d=json.loads(p.read_text());d['measurement']['mean_peak_mg']=999.;p.write_text(json.dumps(d))
    r=helper().describe(tmp_path)
    assert r['status']=='INVALID'
    assert r['invalid_runs'][0]['name']==NAMES[1]
    assert 'mean' in r['invalid_runs'][0]['error']
    assert not any(v['mean_peak_mg']==999. for v in r['runs'])

def test_duplicate_capture_is_flagged_without_discarding_original_files(tmp_path):
    paths=[make_run(tmp_path,i,a) for i,a in enumerate([.8,.4,1.2,1.,.82])]
    a=json.loads(paths[0].read_text());b=json.loads(paths[4].read_text());b['source_sha256']=a['source_sha256'];b['runtime_context']['source_sha256']=a['source_sha256'];paths[4].write_text(json.dumps(b))
    r=helper().describe(tmp_path)
    assert r['status']=='INVALID'
    assert 'duplicate_source_capture' in r['deviations']
    assert all(p.exists() for p in paths)


def test_cli_writes_valid_json_and_preserves_existing_output(tmp_path):
    import subprocess,sys
    for i,a in enumerate([.8,.4,1.2,1.,.82]):make_run(tmp_path,i,a)
    p=tmp_path/'batch_summary.json'
    first=subprocess.run([sys.executable,'-X','utf8','-B',str(MODULE),str(tmp_path)],capture_output=True,text=True)
    assert first.returncode==0,first.stderr
    saved=p.read_bytes()
    assert json.loads(saved)['completed_runs']==5
    assert saved.endswith(bytes([10]))
    second=subprocess.run([sys.executable,'-X','utf8','-B',str(MODULE),str(tmp_path)],capture_output=True,text=True)
    assert second.returncode!=0
    assert p.read_bytes()==saved
