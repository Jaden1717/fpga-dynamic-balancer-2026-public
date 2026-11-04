import sys, json, hashlib
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'sim/golden'))
from order1x_fixed import reference_result

def fixture(tmp_path):
    words=[1000]*64
    p=tmp_path/'prepared.json';p.write_text(json.dumps(dict(samples_raw=words,avg_log2=0)))
    r=dict(reference_result(words,0),prepared_input_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),accepted_samples=64,avg_log2=0)
    q=tmp_path/'result.json';q.write_text(json.dumps(r))
    return p,q,r

def test_verifier_checks_actual_input_instead_of_embedded_reference(tmp_path):
    import verify_order1x
    p,q,r=fixture(tmp_path)
    assert verify_order1x.verify(p,q)['verified'] is True
    r['real_q8']=100;q.write_text(json.dumps(r))
    with pytest.raises(ValueError):verify_order1x.verify(p,q)

def test_verifier_refuses_changed_input(tmp_path):
    import verify_order1x
    p,q,r=fixture(tmp_path);p.write_text(p.read_text()+' ')
    with pytest.raises(ValueError,match='hash'):verify_order1x.verify(p,q)

@pytest.mark.parametrize('field,value',[('accepted_samples',63),('avg_log2',1),('magnitude_q8',3),('phase_defined',True)])
def test_verifier_refuses_wrong_contract(tmp_path,field,value):
    import verify_order1x
    p,q,r=fixture(tmp_path);r[field]=value;q.write_text(json.dumps(r))
    with pytest.raises(ValueError):verify_order1x.verify(p,q)


def test_fpga_bridge_binds_capture_and_uses_verified_iq(tmp_path):
    from test_balance_captures import capture
    from order1x_fixed import prepare_capture
    from balance_captures import solve_captures
    a=capture(tmp_path/'a');b=capture(tmp_path/'b',.01)
    pairs=[]
    for i,c in enumerate((a,b)):
        prepared=prepare_capture(c,axis='x',avg_log2=1)
        p=tmp_path/('p%d.json'%i);p.write_text(json.dumps(prepared))
        r=dict(prepared['reference'],avg_log2=1,accepted_samples=128,accelerator_xsa_sha256='a'*64,
               prepared_input_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        q=tmp_path/('r%d.json'%i);q.write_text(json.dumps(r));pairs.append((p,q))
    args=dict(axis='x',trial_mass_g=.5,trial_radius_mm=20,trial_angle_deg=90,correction_radius_mm=20,
              fpga_baseline=pairs[0],fpga_trial=pairs[1])
    result=solve_captures(a,b,**args)
    assert result['coefficient_source']=='verified_fpga_iq'
    assert result['correction_mass_g']==pytest.approx(1,rel=.003)
    args['fpga_baseline']=pairs[1]
    with pytest.raises(ValueError,match='capture'):solve_captures(a,b,**args)

def test_quality_reports_all_turns_in_nonoverlapping_windows(tmp_path):
    from test_balance_captures import capture
    from sync_csv import analyze_capture
    r=analyze_capture(capture(tmp_path/'a'),axis='x')['quality']
    assert sum(v['turns'] for v in r['windows'])==2
    assert r['windows'][0]['real_g']==pytest.approx(.02,abs=.0001)
