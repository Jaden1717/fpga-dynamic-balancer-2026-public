"""Independent math and strict handoff contract for the FPGA 64-point core."""
import importlib
import json
from pathlib import Path
import sys
import math
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))

def model():
    assert (ROOT/'sim/golden/order1x_fixed.py').is_file(), 'missing fixed 1x reference and preparation adapter'
    return importlib.import_module('order1x_fixed')

@pytest.mark.parametrize('k,re,im',[(0,2560,0),(16,0,-2560),(32,-2560,0),(48,0,2560)])
def test_one_impulse_has_known_fourier_gain_and_sign(k,re,im):
    data=[0]*64;data[k]=320
    result=model().accumulate(data,0)
    assert (result['real_q8'],result['imag_q8'])==(re,im)

@pytest.mark.parametrize('level',[-524288,-1,0,1,256000,524287])
def test_constant_gravity_or_dc_cancels_exactly(level):
    result=model().accumulate([level]*128,1)
    assert result['real_q8']==0 and result['imag_q8']==0


def test_complex_average_occurs_before_magnitude():
    one=[0]*64;one[0]=320
    two=[0]*64;two[0]=-320
    r=model().reference_result(one+two,1)
    assert r['magnitude_q8']==0 and r['phase_defined'] is False


def test_integer_normalization_rounds_negative_ties_away_from_zero():
    data=[0]*64;data[0]=-1
    # Repeating the same turn preserves its averaged coefficient exactly.
    assert model().accumulate(data*32,5)['real_q8']==-8
    positive=[0]*64;positive[0]=1
    # One impulse across16 turns -> +0.5Q8, and mirrored impulse -> -.5Q8.
    assert model().accumulate(positive+[0]*960,4)['real_q8']==1
    assert model().accumulate(data+[0]*960,4)['real_q8']==-1


def test_physical_units_and_phase_follow_existing_complex_convention():
    data=[round(256000*.02*math.cos(2*math.pi*k/64+math.radians(37)))+256000 for k in range(64)]
    r=model().reference_result(data,0)
    assert r['amplitude_g']==pytest.approx(.02,abs=4e-6)
    assert r['phase_deg']==pytest.approx(37,abs=.02)
    assert r['phase_defined'] is True

@pytest.mark.parametrize('data,avg', [([0]*63,0),([0]*65,0),([0]*64,1),([0]*64,9),([0]*64,-1),([0]*64,True),([524288]*64,0),([-524289]*64,0),([True]*64,0),([0.0]*64,0)])
def test_bad_word_count_configuration_and_fixed_input_are_rejected(data,avg):
    with pytest.raises(ValueError):model().accumulate(data,avg)


def test_max_average_window_keeps_full_scale_without_overflow():
    r=model().accumulate([524287]*16384,8)
    assert r['real_q8']==0 and r['imag_q8']==0


def test_prepare_existing_rtl_capture_crops_complete_turns_and_quantizes():
    # This artifact is optional outside original development checkout.
    directory=ROOT/'tests/fixtures/rtl_capture'
    if not directory.is_dir():pytest.skip('original RTL evidence dataset not present')
    p=model().prepare_capture(directory,axis='x')
    assert p['avg_log2']==1 and len(p['samples_raw'])==128
    assert p['points_per_turn']==64 and p['used_turns']==2
    r=model().reference_result(p['samples_raw'],p['avg_log2'])
    assert r['amplitude_g']==pytest.approx(.02,abs=8e-6)
    assert r['phase_deg']==pytest.approx(30,abs=.04)
    with pytest.raises(ValueError):model().prepare_capture(directory,axis='x',avg_log2=2)


def test_preparation_cli_refuses_overwrite_before_reading_capture(tmp_path):
    target=tmp_path/'existing.json';target.write_text('keep')
    with pytest.raises(SystemExit):model().main(['missing','--axis','x','--out',str(target)])
    assert target.read_text()=='keep'
