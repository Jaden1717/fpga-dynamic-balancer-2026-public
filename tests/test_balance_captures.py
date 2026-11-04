"""Two real synchronized capture folders drive the actual influence solver."""
import csv
import importlib
import json
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sim/golden'))


def module():
    assert (ROOT / 'sim/golden/balance_captures.py').is_file(), 'missing two-capture balance bridge'
    return importlib.import_module('balance_captures')


def capture(path, amplitude=.02, period_ticks=4000000):
    path.mkdir()
    origin = 2**61
    with (path/'samples.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['frame_id','ticks','x_raw','y_raw','z_raw'])
        writer.writerows((i,origin+i*25000,round(amplitude*256000*np.cos(2*np.pi*(i*25000-1000000)/period_ticks)),0,256000) for i in range(401))
    with (path/'pulses.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['pulse_count','ticks'])
        writer.writerows((i+1,origin+1000000+i*period_ticks) for i in range(3))
    meta=dict(schema_version=1,clock_hz=100000000,epoch=1,xsa_sha256='a'*64,
              sample_count=401,pulse_count=3,sample_drops=0,pulse_drops=0,source_missed=0,
              completed=True,rotation_ready=True,status='PASS',errors=[],hardware_status=6,
              source_idle=True,pulses_per_revolution=1)
    (path/'capture.json').write_text(json.dumps(meta))
    return path


def solve(tmp_path, **kwargs):
    a=capture(tmp_path/'baseline');b=capture(tmp_path/'trial',.01)
    args=dict(axis='x',trial_mass_g=.5,trial_radius_mm=20,trial_angle_deg=90,correction_radius_mm=20)
    args.update(kwargs)
    return module().solve_captures(a,b,**args)


def test_real_csv_pair_recovers_hand_calculated_one_gram_at_90deg(tmp_path):
    # alpha=.001j g/(g mm), trial=10j g mm => delta=-.01g.
    # correction=-.02/(.001j)=20j g mm => 1g at radius20mm, added at90deg.
    result=solve(tmp_path)
    assert result['correction_mass_g']==pytest.approx(1,rel=.003)
    assert result['correction_angle_deg']==pytest.approx(90,abs=.05)
    assert result['correction_gmm']['imag']==pytest.approx(20,rel=.003)
    assert result['trial_gmm']['imag']==pytest.approx(10)
    assert result['baseline']['rotation_hz']==pytest.approx(25)
    assert result['speed_tolerance']==.01
    assert result['physical_validation_complete'] is False


@pytest.mark.parametrize('field,value', [('trial_mass_g',0),('trial_mass_g',-1),
    ('trial_mass_g',float('nan')),('trial_radius_mm',0),('trial_radius_mm',float('inf')),
    ('correction_radius_mm',-1),('trial_angle_deg',float('nan')),
    ('speed_tolerance',-.01),('speed_tolerance',float('nan'))])
def test_invalid_physical_parameters_refused(tmp_path,field,value):
    with pytest.raises(ValueError):
        solve(tmp_path,**{field:value})


@pytest.mark.parametrize('field,value', [('xsa_sha256','b'*64),('clock_hz',100000001),
    ('pulses_per_revolution',2),('completed',False),('status','PARTIAL')])
def test_incompatible_or_partial_capture_pair_refused(tmp_path,field,value):
    a=capture(tmp_path/'baseline');b=capture(tmp_path/'trial',.01)
    meta=json.loads((b/'capture.json').read_text());meta[field]=value
    (b/'capture.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        module().solve_captures(a,b,axis='x',trial_mass_g=.5,trial_radius_mm=20,trial_angle_deg=90,correction_radius_mm=20)


def test_speed_change_over_default_one_percent_is_rejected(tmp_path):
    a=capture(tmp_path/'baseline');b=capture(tmp_path/'trial',.01,period_ticks=4160000)
    with pytest.raises(ValueError,match='speed'):
        module().solve_captures(a,b,axis='x',trial_mass_g=.5,trial_radius_mm=20,trial_angle_deg=90,correction_radius_mm=20)


def test_equal_baseline_trial_response_is_degenerate(tmp_path):
    a=capture(tmp_path/'baseline');b=capture(tmp_path/'trial',.02)
    with pytest.raises(ValueError,match='resolvable'):
        module().solve_captures(a,b,axis='x',trial_mass_g=.5,trial_radius_mm=20,trial_angle_deg=90,correction_radius_mm=20)


def test_cli_saves_serializable_result_and_refuses_overwrite(tmp_path,capsys):
    a=capture(tmp_path/'baseline');b=capture(tmp_path/'trial',.01)
    output=tmp_path/'result.json'
    args=[str(a),str(b),'--axis','x','--trial-mass-g','.5','--trial-radius-mm','20',
          '--trial-angle-deg','90','--correction-radius-mm','20','--out',str(output)]
    assert module().main(args)==0
    saved=output.read_text();assert json.loads(saved)['correction_mass_g']==pytest.approx(1,rel=.003)
    with pytest.raises(SystemExit):
        module().main(args)
    assert output.read_text()==saved


def test_explicit_speed_tolerance_is_used_and_reported(tmp_path):
    a=capture(tmp_path/'baseline');b=capture(tmp_path/'trial',.01,period_ticks=4160000)
    result=module().solve_captures(a,b,axis='x',trial_mass_g=.5,trial_radius_mm=20,
                                  trial_angle_deg=90,correction_radius_mm=20,speed_tolerance=.05)
    assert result['speed_tolerance']==.05
    assert result['speed_relative_difference']==pytest.approx(1-25/26)
    assert result['physical_validation_complete'] is False
