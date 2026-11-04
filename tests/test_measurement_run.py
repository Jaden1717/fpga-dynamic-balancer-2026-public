"""Catch missing provenance, false comparability, raw mutation and silent retry."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))
RAW=ROOT/'data/physical/hx_20261029_007/board_export/relief_a128_20261029_01'
BOOT='12345678-1234-1234-1234-123456789abc'


def api():
    assert (ROOT/'sim/golden/measurement_run.py').exists(), 'missing unified measurement entry'
    return importlib.import_module('measurement_run')


def hashes(directory):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.is_file()}


def context(source):
    return dict(kind='hengxin-runtime-context-v1',source_sha256=source,
        before=dict(boot_id=BOOT,uptime_seconds=100.),after=dict(boot_id=BOOT,uptime_seconds=140.),
        initialization_id='run-1',
        sensor_configuration=dict(verified=True,registers={'DEVID':173,'FILTER':0,'RANGE':129,'POWER_CTL':0}),
        driver_sha256={'sync_capture.py':'a'*64,'adxl355_pl_acq.py':'b'*64},
        source_declaration='fan-tach-2ppr',
        declared_setup=dict(fixture_id='fixture-1',sensor_mount_id='mount-1',cable_relief='fixed',
            environment='bench-1',trial_position='A',trial_mass_g=.01,trial_radius_mm=40.,power_setting='12V'))


def report(identity='1',values=None,rpm=1710.):
    return dict(kind='hengxin-measurement-summary-v1',data_integrity='PASS',
        protocol=dict(method='aa128-to64',axis='z',avg_log2=7,start_turns=list(range(0,1024,128))),
        analysis_identity={'algorithm.py':'c'*64},
        source_sha256={'samples.csv':identity*64,'pulses.csv':'2'*64,'capture.json':'3'*64,'stop_snapshot.json':'4'*64},
        measurement=dict(amplitudes_g=[.0002]*8 if values is None else values,rpm_median=rpm),
        runtime_context=None,correction_allowed=False,physical_validation_complete=False)


def comparable_pair():
    a,b=report(),report('5')
    a['runtime_context']=context(a['source_sha256'])
    b['runtime_context']=context(b['source_sha256']);b['runtime_context']['initialization_id']='run-2'
    return a,b


def test_proc_observation_does_not_invent_missing_boot_or_uptime(tmp_path):
    r=api().observe_runtime(tmp_path)
    assert r['boot_id'] is None and r['uptime_seconds'] is None
    assert len(r['read_errors'])==2
    (tmp_path/'sys/kernel/random').mkdir(parents=True)
    (tmp_path/'sys/kernel/random/boot_id').write_text(BOOT+'\n')
    (tmp_path/'uptime').write_text('123.25 50.0\n')
    r=api().observe_runtime(tmp_path)
    assert r['boot_id']==BOOT and r['uptime_seconds']==123.25
    (tmp_path/'uptime').write_text('nan 0')
    assert api().observe_runtime(tmp_path)['uptime_seconds'] is None


def test_review_uses_all_windows_and_keeps_unknown_historical_context(tmp_path):
    before=hashes(RAW);out=tmp_path/'review'
    r=api().review(RAW,out=out)
    # Frozen I/Q independently yields this CPU amplitude; FPGA magnitude is quantized.
    assert r['measurement']['mean_peak_mg']==pytest.approx(.1566445439897927,abs=1e-12)
    assert r['measurement']['cv']==pytest.approx(.11120726812455033,abs=1e-12)
    assert len(r['measurement']['amplitudes_g'])==8
    assert r['runtime_context'] is None
    assert r['data_integrity']=='PASS'
    assert r['automatic_recapture'] is False and r['correction_allowed'] is False
    assert r['measurement']['hardware_windows_verified']==0
    assert r['timings']['stages']['preparation']['calls']==1
    assert json.loads((out/'summary.json').read_text())['source_sha256']==before
    assert hashes(RAW)==before
    with pytest.raises(ValueError,match='exists'):api().review(RAW,out=out)
    with pytest.raises(ValueError,match='outside'):api().review(RAW,out=RAW/'forbidden')


def test_foreign_runtime_context_is_rejected_and_failure_is_saved(tmp_path):
    c=tmp_path/'context.json';c.write_text(json.dumps(context({'samples.csv':'0'*64})))
    out=tmp_path/'bad'
    with pytest.raises(ValueError,match='binding'):api().review(RAW,out=out,runtime=c)
    assert not (out/'summary.json').exists()
    failure=json.loads((out/'run.json').read_text())
    assert failure['status']=='FAILED' and failure['automatic_recapture'] is False


def test_compare_recomputes_change_without_using_cv_as_a_gate():
    a,b=comparable_pair();a['measurement']['amplitudes_g']=[.0001]*4+[.0003]*4
    b['measurement']['amplitudes_g']=[.00015]*4+[.00035]*4
    b['measurement']['mean_peak_mg']=999
    r=api().compare(a,b,rpm_relative_tolerance=.01)
    assert r['comparability']=='DECLARED_CONDITIONS_MATCH'
    assert r['mean_difference_mg']==pytest.approx(.05)
    assert r['correction_allowed'] is False and r['automatic_recapture'] is False
    assert a['runtime_context']['initialization_id']!=b['runtime_context']['initialization_id']


@pytest.mark.parametrize('change,expected,reason',[
    ('missing_context','UNKNOWN','runtime_context_missing'),
    ('different_boot','NOT_COMPARABLE','boot_id_changed'),
    ('configuration','NOT_COMPARABLE','sensor_configuration_changed'),
    ('missing_configuration','UNKNOWN','sensor_configuration_missing'),
    ('mount','NOT_COMPARABLE','setup_sensor_mount_id_changed'),
    ('rpm','NOT_COMPARABLE','rpm_outside_declared_tolerance'),
    ('no_budget','UNKNOWN','rpm_tolerance_missing'),
])
def test_compare_separates_unknown_conditions_from_known_changes(change,expected,reason):
    a,b=comparable_pair();tolerance=.01
    if change=='missing_context':b['runtime_context']=None
    if change=='different_boot':
        b['runtime_context']['before']['boot_id']='99999999-1234-1234-1234-123456789abc'
        b['runtime_context']['after']['boot_id']='99999999-1234-1234-1234-123456789abc'
    if change=='configuration':b['runtime_context']['sensor_configuration']['registers']['FILTER']=1
    if change=='missing_configuration':b['runtime_context']['sensor_configuration']=None
    if change=='mount':b['runtime_context']['declared_setup']['sensor_mount_id']='mount-2'
    if change=='rpm':b['measurement']['rpm_median']=1740
    if change=='no_budget':tolerance=None
    r=api().compare(a,b,rpm_relative_tolerance=tolerance)
    assert r['comparability']==expected and reason in r['reasons']
    assert r['physical_validation_complete'] is False


def test_same_capture_and_protocol_mismatch_cannot_be_presented_as_repeats():
    a,b=comparable_pair();b['source_sha256']['samples.csv']=a['source_sha256']['samples.csv']
    with pytest.raises(ValueError,match='same capture'):api().compare(a,b,rpm_relative_tolerance=.01)
    a,b=comparable_pair();b['protocol']['avg_log2']=6
    assert api().compare(a,b,rpm_relative_tolerance=.01)['comparability']=='NOT_COMPARABLE'


def test_capture_error_preserves_context_and_never_retries(tmp_path):
    class Boundary:
        identities={'sync_capture.py':'a'*64}
        sensor_configuration=None
        def open(self):raise RuntimeError('hardware boundary unavailable')
    out=tmp_path/'capture'
    with pytest.raises(RuntimeError,match='hardware boundary'):
        api().capture(Boundary(),out=out,seconds=40)
    assert json.loads((out/'run.json').read_text())['status']=='FAILED'
    assert (out/'runtime.json').exists()
    assert not (out/'summary.json').exists()



def test_capture_success_binds_original_files_and_observed_context(tmp_path):
    import shutil
    class Boundary:
        identities={'sync_capture.py':'a'*64,'adxl355_pl_acq.py':'b'*64}
        sensor_configuration=context({})['sensor_configuration']
        def open(self):pass
        def acquire(self,seconds,directory,*,allow_no_pulses,timings):
            directory.mkdir()
            for name in ('samples.csv','pulses.csv','capture.json'):shutil.copyfile(RAW/name,directory/name)
            return json.loads((directory/'capture.json').read_text())
        def stopped_snapshot(self,directory):return json.loads((RAW/'stop_snapshot.json').read_text())
    out=tmp_path/'capture'
    r=api().capture(Boundary(),out=out,seconds=40,setup=context({})['declared_setup'])
    assert r['data_integrity']=='PASS'
    assert hashes(out/'raw')==hashes(RAW)
    c=json.loads((out/'runtime.json').read_text())
    assert c['source_sha256']==hashes(RAW)
    assert c['sensor_configuration']['registers']['RANGE']==129
    assert c['declared_setup']['sensor_mount_id']=='mount-1'
    assert json.loads((out/'run.json').read_text())['status']=='COMPLETED'


def test_false_static_return_cannot_bypass_raw_validation(tmp_path):
    import shutil
    class Boundary:
        identities={}
        sensor_configuration=None
        def open(self):pass
        def acquire(self,seconds,directory,*,allow_no_pulses,timings):
            directory.mkdir()
            for name in ('samples.csv','pulses.csv','capture.json'):shutil.copyfile(RAW/name,directory/name)
            result=json.loads((directory/'capture.json').read_text());result['status']='STATIC_ONLY'
            return result
        def stopped_snapshot(self,directory):return json.loads((RAW/'stop_snapshot.json').read_text())
    out=tmp_path/'capture'
    with pytest.raises(ValueError,match='metadata'):api().capture(Boundary(),out=out,allow_no_pulses=True)
    assert not (out/'summary.json').exists()
    assert json.loads((out/'run.json').read_text())['status']=='FAILED'


def test_explicit_no_trial_state_can_be_compared_without_a_fictitious_radius():
    a,b=comparable_pair()
    for r in (a,b):
        r['runtime_context']['declared_setup'].update(trial_position='none',trial_mass_g=0.,trial_radius_mm=None)
    r=api().compare(a,b,rpm_relative_tolerance=.01)
    assert r['comparability']=='DECLARED_CONDITIONS_MATCH'


def test_raw_statistics_uses_acceleration_units_and_all_samples(tmp_path):
    (tmp_path/'samples.csv').write_text('frame_id,ticks,x_raw,y_raw,z_raw\n0,0,0,256000,256000\n1,25000,256000,0,256000\n')
    r=api().raw_statistics(tmp_path)
    assert r['sample_count']==2
    assert r['dc_g']=={'x':.5,'y':.5,'z':1.}


def test_wrong_compute_image_fails_before_starting_capture(tmp_path):
    class Boundary:
        identities={}
        sensor_configuration=None
        def open(self):raise AssertionError('must not initialize capture')
    image=tmp_path/'wrong.xsa';image.write_bytes(b'wrong')
    out=tmp_path/'capture'
    with pytest.raises(ValueError,match='compute XSA'):
        api().capture(Boundary(),out=out,compute_xsa=image)
    assert not out.exists()



@pytest.mark.parametrize('missing',['FILTER','RANGE','POWER_CTL','DEVID'])
def test_incomplete_readback_cannot_be_called_matching_configuration(missing):
    a,b=comparable_pair()
    for r in (a,b):del r['runtime_context']['sensor_configuration']['registers'][missing]
    result=api().compare(a,b,rpm_relative_tolerance=.01)
    assert result['comparability']=='UNKNOWN'
    assert 'sensor_configuration_missing' in result['reasons']


def test_invalid_boot_identity_cannot_establish_same_session():
    a,b=comparable_pair()
    for r in (a,b):
        r['runtime_context']['before']['boot_id']='unrecorded'
        r['runtime_context']['after']['boot_id']='unrecorded'
    result=api().compare(a,b,rpm_relative_tolerance=.01)
    assert result['comparability']=='UNKNOWN'
    assert 'boot_id_missing' in result['reasons']
