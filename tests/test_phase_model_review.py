"""Sensitivity diagnostics must expose assumptions without replacing the solver."""
import importlib,json,math,sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))


def api():
    assert (ROOT/'sim/golden/phase_model_review.py').exists(),'missing model sensitivity diagnostic'
    return importlib.import_module('phase_model_review')


def known_amplitudes():
    b=.0004+.0003j;t=.0007
    return [abs(b)]+[abs(b+t*np.exp(1j*np.radians(a))) for a in [0,120,240]]


def test_known_normalized_vector_and_two_baseline_roots():
    r=api().diagnose(known_amplitudes(),[0,120,240])
    assert r['normalized_algebra_vector']['real']==pytest.approx(-4/7)
    assert r['normalized_algebra_vector']['imag']==pytest.approx(-3/7)
    assert r['normalized_algebra_vector']['magnitude']==pytest.approx(5/7)
    assert r['closure_relative_error']==pytest.approx(0,abs=1e-12)
    assert r['baseline_only_closure_roots_g']==pytest.approx([.0005,.0007])
    assert r['correction_allowed'] is False
    assert 'correction_mass_g' not in r and 'correction_angle_deg' not in r


def test_uniform_gain_cancels_in_dimensionless_diagnostic():
    a=known_amplitudes()
    first=api().diagnose(a,[0,120,240]);scaled=api().diagnose([x*17 for x in a],[0,120,240])
    assert scaled['normalized_algebra_vector']==pytest.approx(first['normalized_algebra_vector'])
    assert scaled['closure_relative_error']==pytest.approx(first['closure_relative_error'],abs=1e-12)


def test_diagnostic_does_not_bypass_original_inconsistent_model_rejection():
    from phase_free_balance import solve
    with pytest.raises(ValueError,match='closure'):
        solve(.001,[.002]*3,[0,120,240],trial_mass_g=1,trial_radius_mm=35,uncertainty_g=0)
    r=api().diagnose([.001,.002,.002,.002],[0,120,240])
    assert r['closure_relative_error']==pytest.approx(1)
    assert r['correction_allowed'] is False


def test_nonpositive_trial_response_is_reported_without_inventing_ratio():
    r=api().diagnose([.002,.001,.001,.001],[0,120,240])
    assert r['algebra_status']=='nonpositive_q'
    assert r['normalized_algebra_vector'] is None
    assert r['baseline_only_closure_roots_g']==[]


@pytest.mark.parametrize('values,angles',[
    ([.001]*3,[0,120,240]),([.001,-.001,.001,.001],[0,120,240]),
    ([.001,float('nan'),.001,.001],[0,120,240]),([True,.001,.001,.001],[0,120,240]),
    ([.001]*4,[0,0,120])])
def test_bad_data_or_singular_geometry_refused(values,angles):
    with pytest.raises(ValueError):api().diagnose(values,angles)


def test_all_windows_and_one_at_a_time_scenarios_are_retained():
    a=known_amplitudes()
    groups=[[x*.9,x*.95,x,x*1.15] for x in a]
    r=api().review_windows(groups,[0,120,240],angle_perturb_deg=5)
    assert [g['window_count'] for g in r['records']]==[4]*4
    assert len(r['amplitude_scenarios'])==8
    assert len(r['angle_scenarios'])==6
    assert len(r['half_record_scenarios'])==16
    assert r['records'][0]['sample_sd_g']>0
    assert r['correction_allowed'] is False and r['physical_validation_complete'] is False


def test_cli_preserves_existing_output(tmp_path):
    target=tmp_path/'keep.json';target.write_text('keep')
    with pytest.raises(SystemExit):
        api().main(['missing.json','--out',str(target)])
    assert target.read_text()=='keep'


def test_cli_produces_finite_diagnostic_without_mass_or_angle_advice(tmp_path):
    a=known_amplitudes();source=tmp_path/'input.json';out=tmp_path/'report.json'
    source.write_text(json.dumps(dict(amplitude_windows_g=[[x]*4 for x in a],angles_deg=[0,120,240])))
    assert api().main([str(source),'--out',str(out)])==0
    r=json.loads(out.read_text())
    assert r['point']['normalized_algebra_vector']['magnitude']==pytest.approx(5/7)
    assert r['correction_allowed'] is False
    assert 'NaN' not in out.read_text()
