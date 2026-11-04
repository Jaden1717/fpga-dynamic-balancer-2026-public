import importlib
import math
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))


def solver():
    assert (ROOT/'phase_free_balance.py').exists() or (ROOT/'sim/golden/phase_free_balance.py').exists(), 'missing phase-free solver'
    return importlib.import_module('phase_free_balance').solve


def amplitudes(b,t,angles):
    return [abs(b+t*complex(math.cos(math.radians(a)),math.sin(math.radians(a)))) for a in angles]


@pytest.mark.parametrize('angles',[[0,120,240],[0,100,230],[350,110,230]])
def test_known_answer_and_sign(angles):
    b=complex(.0004,.0003);t=.0007
    r=solver()(abs(b),amplitudes(b,t,angles),angles,trial_mass_g=.01,trial_radius_mm=35,uncertainty_g=0)
    want=-.01*b/t
    assert r['correction_mass_g']==pytest.approx(abs(want))
    angle=math.degrees(math.atan2(want.imag,want.real))%360
    assert r['correction_angle_deg']==pytest.approx(angle)
    assert r['model_closure_relative_error']==pytest.approx(0,abs=1e-12)
    assert r['physical_validation_complete'] is False


def test_unknown_sensor_phase_cancels():
    angles=[0,120,240];b=.0004+.0003j;t=.0007j
    r=solver()(abs(b),amplitudes(b,t,angles),angles,trial_mass_g=.01,trial_radius_mm=35,uncertainty_g=0)
    want=-.01*b/t
    assert r['correction_mass_g']==pytest.approx(abs(want))
    assert r['correction_angle_deg']==pytest.approx(math.degrees(math.atan2(want.imag,want.real))%360)


@pytest.mark.parametrize('angles',[[0,0,120],[0,1,2]])
def test_bad_geometry_refused(angles):
    with pytest.raises(ValueError,match='geometry'):
        solver()(.001,[.002,.002,.002],angles,trial_mass_g=.01,trial_radius_mm=35,uncertainty_g=0)


def test_weak_effect_refused():
    with pytest.raises(ValueError,match='resolved'):
        solver()(.0005,amplitudes(.0005,.00001,[0,120,240]),[0,120,240],trial_mass_g=.01,trial_radius_mm=35,uncertainty_g=.00001)


def test_inconsistent_four_runs_refused():
    with pytest.raises(ValueError,match='closure'):
        solver()(.001,[.002,.002,.002],[0,120,240],trial_mass_g=.01,trial_radius_mm=35,uncertainty_g=0)


@pytest.mark.parametrize('key,value',[('trial_mass_g',0),('trial_radius_mm',-1),('uncertainty_g',-1),('trial_mass_g',True)])
def test_invalid_physical_parameters(key,value):
    kw=dict(trial_mass_g=.01,trial_radius_mm=35,uncertainty_g=0)
    kw[key]=value
    with pytest.raises(ValueError):solver()(.001,[.002,.002,.002],[0,120,240],**kw)


def test_no_disturbance_returns_zero_correction():
    r=solver()(0,[.001,.001,.001],[0,120,240],trial_mass_g=.01,trial_radius_mm=35,uncertainty_g=0)
    assert r['correction_mass_g']==pytest.approx(0,abs=1e-16)
    assert r['correction_angle_deg'] is None
