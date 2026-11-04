"""Independent analytic truth; diagnostic stability cannot certify correction."""
import importlib
import hashlib
from pathlib import Path
import sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))


def api():
    assert (ROOT/'sim/golden/measurement_review.py').is_file(),'missing independent measurement diagnostics'
    return importlib.import_module('measurement_review')


def synthetic():
    t=np.arange(4001)/2000
    edges=np.arange(21)/10
    theta=2*np.pi*10*t
    x=1+.003*(t-1)/2+.0002*np.cos(theta+.6)+.02*np.cos(9*theta+.2)+.001*np.cos(2*np.pi*31.5*t+.4)
    return t,x,edges


def test_joint_fit_recovers_known_weak_order_amid_drift_and_strong_interference():
    t,x,edges=synthetic()
    r=api().fit_order(t,x,edges,include_trend=True,nuisance_orders=(9,),nuisance_hz=(31.5,))
    assert r['amplitude_g']==pytest.approx(.0002,abs=1e-12)
    assert r['phase_deg']==pytest.approx(34.37746770784939,abs=1e-7)
    assert r['trend_change_g']==pytest.approx(.003,abs=1e-12)
    assert r['residual_rms_g']<1e-12
    assert r['physical_validation_complete'] is False


def test_drift_fit_beats_constant_only_projection_on_known_truth():
    t=np.arange(4001)/2000
    edges=np.arange(21)/10
    x=.0002*np.cos(2*np.pi*10*t+.6)+.003*(t-1)/2
    plain=api().fit_order(t,x,edges,include_trend=False)
    trend=api().fit_order(t,x,edges,include_trend=True)
    assert abs(plain['amplitude_g']-.0002)>1e-6
    assert trend['amplitude_g']==pytest.approx(.0002,abs=1e-12)


def test_joint_fit_refuses_unidentifiable_nuisance_at_target_frequency():
    t,x,edges=synthetic()
    with pytest.raises(ValueError,match='identif|condition|rank'):
        api().fit_order(t,x,edges,nuisance_hz=(10,))


@pytest.mark.parametrize('damage',['duplicate_time','nan_signal','edges_outside'])
def test_fit_refuses_invalid_observations(damage):
    t,x,edges=synthetic()
    if damage=='duplicate_time':t[10]=t[9]
    if damage=='nan_signal':x[10]=np.nan
    if damage=='edges_outside':edges[-1]=3
    with pytest.raises(ValueError):api().fit_order(t,x,edges)


def test_phase_cancellation_is_visible_when_amplitude_cv_is_zero():
    r=api().complex_stability([1+0j,-1+0j,1+0j,-1+0j])
    assert r['amplitude_cv']==0
    assert r['phasor_coherence']==0
    assert r['coherent_amplitude_g']==0
    assert r['complex_sample_sd_g']==pytest.approx(1.1547005383792515)
    assert r['correction_allowed'] is False


def test_constant_phasor_has_no_scatter():
    r=api().complex_stability([.003+.004j]*8)
    assert r['mean_amplitude_g']==pytest.approx(.005)
    assert r['phasor_coherence']==pytest.approx(1)
    assert r['adjacent_complex_difference_scale_g']==pytest.approx(0)


def test_frozen_record_diagnostics_bind_real_hardware_and_keep_all_windows():
    raw=ROOT/'data/physical/hx_20261029_007/board_export/relief_a128_20261029_01'
    board=raw.parent/'relief_a128_20261029_01_analysis'
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir() if p.is_file()}
    r=api().review_capture(raw,avg_log2=7,hardware_analysis=board,nuisance_hz=(31.5,))
    assert len(r['windows'])==8
    assert r['hardware_windows_verified']==8
    assert r['fpga_summary']['mean_amplitude_g']*1000==pytest.approx(.1566448211669922)
    assert r['sensor_configuration_bound'] is False
    assert r['source_sha256']==before
    assert r['correction_allowed'] is False
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir() if p.is_file()}
