"""Catch contradictory display fields despite otherwise valid FPGA integers."""
import hashlib
import json
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))
from order1x_fixed import reference_result
from verify_order1x import verify


def result_files(tmp_path):
    words=[0]*64
    words[16]=320
    prepared=tmp_path/'prepared.json'
    prepared.write_text(json.dumps(dict(samples_raw=words,avg_log2=0)))
    result=dict(reference_result(words,0),accepted_samples=64,expected_samples=64,avg_log2=0,
                real_g=0.0,imag_g=-.0000390625,
                prepared_input_sha256=hashlib.sha256(prepared.read_bytes()).hexdigest())
    path=tmp_path/'result.json'
    path.write_text(json.dumps(result))
    return prepared,path,result


@pytest.mark.parametrize('field,value',[
    ('amplitude_g',123.0),('real_g',1.0),('imag_g',0.1),('phase_deg',90),
    ('amplitude_g',float('nan')),('amplitude_g',float('inf')),
    ('amplitude_g',True),('amplitude_g','0.0000390625'),
    ('expected_samples',63)])
def test_verifier_rejects_contradictory_or_invalid_display_fields(tmp_path,field,value):
    prepared,path,result=result_files(tmp_path)
    result[field]=value
    path.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        verify(prepared,path)


def test_verified_amplitude_is_derived_from_registers_without_display_fields(tmp_path):
    prepared,path,result=result_files(tmp_path)
    for key in ('amplitude_g','phase_deg','real_g','imag_g'):
        result.pop(key,None)
    path.write_text(json.dumps(result))
    report=verify(prepared,path)
    assert report['amplitude_g']==pytest.approx(.0000390625,abs=1e-15)
    assert report['phase_deg']==pytest.approx(-90)
