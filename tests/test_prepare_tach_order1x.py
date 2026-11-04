import csv
import importlib
import math
import json
import pytest
from test_sync_csv import fixture, ROOT


def adapter():
    assert (ROOT/'sim/golden/prepare_tach_order1x.py').is_file(), 'missing TACH preparation adapter'
    return importlib.import_module('prepare_tach_order1x')


def data(path):
    meta=fixture(path)
    file=path/'samples.csv'
    with file.open() as f: rows=list(csv.DictReader(f))
    for i,r in enumerate(rows):
        r['x_raw']=round(5120*math.cos(2*math.pi*25*(i/4000-.01)+math.radians(37)))
    with file.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    return meta


def prepare(path,**kwargs):
    return adapter().prepare(path,source='fan-tach-2ppr',axis='x',avg_log2=1,**kwargs)


def test_real_frequency_amplitude_and_raw_preservation(tmp_path):
    data(tmp_path)
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    r=prepare(tmp_path)
    assert len(r['samples_raw'])==128
    assert r['available_turns']==4
    assert r['reference']['amplitude_g']==pytest.approx(.02,abs=3e-5)
    assert r['reference']['phase_deg']==pytest.approx(37,abs=.1)
    assert r['absolute_phase_available'] is False
    assert r['recorded_pulses_per_revolution']==1
    assert r['interpreted_pulses_per_revolution']==2
    assert r['window']['start_pulse_count']==1
    assert r['window']['end_pulse_count']==5
    assert set(r['source_sha256'])==set(before)
    assert all((tmp_path/k).read_bytes()==v for k,v in before.items())


def test_parity_changes_phase_not_amplitude(tmp_path):
    data(tmp_path)
    r=prepare(tmp_path,parity=1,start_turn=1)
    assert r['reference']['amplitude_g']==pytest.approx(.02,abs=3e-5)
    assert r['reference']['phase_deg']==pytest.approx(-143,abs=.1)
    assert r['window']['start_pulse_count']==4
    assert r['window']['end_pulse_count']==8


@pytest.mark.parametrize('kwargs',[dict(parity=2),dict(start_turn=-1),dict(start_turn=True),dict(start_turn=4)])
def test_bad_window_rejected(tmp_path,kwargs):
    data(tmp_path)
    with pytest.raises(ValueError):prepare(tmp_path,**kwargs)


def test_optical_not_silently_reinterpreted(tmp_path):
    data(tmp_path)
    with pytest.raises(ValueError,match='source'):
        adapter().prepare(tmp_path,source='optical')


def test_loss_and_rails_block_preparation(tmp_path):
    meta=data(tmp_path)
    meta['source_missed']=1
    (tmp_path/'capture.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='data loss'):prepare(tmp_path)
    data(tmp_path)
    p=tmp_path/'samples.csv'
    p.write_text(p.read_text().replace('256000','524287',1))
    with pytest.raises(ValueError,match='rail'):prepare(tmp_path)


def test_compatible_with_board_input_contract(tmp_path):
    import sys
    sys.path.insert(0,str(ROOT/'board/bringup'))
    from order1x_accel import _prepared_document
    data(tmp_path)
    r=prepare(tmp_path)
    p=tmp_path/'prepared.json';p.write_text(json.dumps(r))
    document,words,avg,timeout,digest=_prepared_document(p,2.)
    assert words==r['samples_raw'] and avg==1


def test_cli_no_overwrite(tmp_path):
    p=tmp_path/'existing.json';p.write_text('keep')
    with pytest.raises(SystemExit):adapter().main(['missing','--source','fan-tach-2ppr','--out',str(p)])
    assert p.read_text()=='keep'
