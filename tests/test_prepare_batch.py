"""Batch preparation must preserve archived FPGA inputs while parsing once,
reporting real progress and rejecting source changes. No live hardware needed."""
from pathlib import Path
import hashlib
import importlib
import json
import sys
import pytest
from test_prepare_tach_order1x import data
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))


def adapter():
    module=importlib.import_module('prepare_tach_order1x')
    assert hasattr(module,'prepare_many'),'missing single-parse batch preparation'
    return module


def batch(path,**kwargs):
    return adapter().prepare_many(path,source='fan-tach-2ppr',axis='x',avg_log2=1,resampling='aa128-to64',**kwargs)


@pytest.mark.parametrize('capture,analysis,avg',[
    ('data/physical/hx_20261028_003/board_export/trial_a_20251021_185122','data/physical/hx_20261028_003/antialias_20261029',6),
    ('data/physical/hx_20261029_007/board_export/relief_a128_20261029_01','data/physical/hx_20261029_007/board_export/relief_a128_20261029_01_analysis',7)])
def test_batch_preserves_archived_integer_inputs_and_provenance(capture,analysis,avg):
    path=ROOT/capture
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir() if p.is_file()}
    starts=list(range(0,8*(1<<avg),1<<avg))
    values=adapter().prepare_many(path,source='fan-tach-2ppr',axis='z',avg_log2=avg,resampling='aa128-to64',start_turns=starts)
    assert len(values)==8
    for start,value in zip(starts,values):
        name=('trial_a_20251021_185122_z64_%d.json'%start) if avg==6 else ('input_%d.json'%start)
        old=json.loads((ROOT/analysis/name).read_text())
        assert value['samples_raw']==old['samples_raw']
        assert len(value['samples_raw'])==(4096 if avg==6 else 8192)
        for k in ['window','source_sha256','preprocessing','axes_quality','avg_log2','used_turns','points_per_turn']:
            assert value[k]==old[k]
        for k in ['real_q8','imag_q8','magnitude_q8','phase_code','phase_defined']:assert value['reference'][k]==old['reference'][k]
        assert value['reference']['amplitude_g']==pytest.approx(old['reference']['amplitude_g'],abs=1e-15)
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir() if p.is_file()}


def test_batch_parses_csv_once_and_reports_each_completed_window(tmp_path,monkeypatch):
    data(tmp_path)
    module=adapter();tach=importlib.import_module('tach_review');parser=importlib.import_module('sync_csv')
    real_load=parser.load_capture;loads=[];events=[]
    def counted(*args,**kwargs):
        loads.append(1);return real_load(*args,**kwargs)
    monkeypatch.setattr(module,'load_capture',counted)
    monkeypatch.setattr(tach,'load_capture',counted)
    values=batch(tmp_path,start_turns=[0,2],progress=lambda *event:events.append(event))
    assert len(values)==2 and len(loads)==1
    assert events==[('loading',0,2),('prepared',1,2),('prepared',2,2)]


def test_batch_rejects_raw_changes_during_processing(tmp_path):
    data(tmp_path)
    def change(stage,done,total):
        if stage=='prepared' and done==1:
            with (tmp_path/'samples.csv').open('a') as stream:stream.write('\n')
    with pytest.raises(ValueError,match='changed'):
        batch(tmp_path,start_turns=[0,2],progress=change)


@pytest.mark.parametrize('starts',[[],[True],[0,0],[1.0],[-1],[999]])
def test_batch_rejects_invalid_or_unavailable_windows(tmp_path,starts):
    data(tmp_path)
    with pytest.raises(ValueError):batch(tmp_path,start_turns=starts)


def test_eight_window_stage_uses_one_parse_and_keeps_quality_flags(tmp_path,monkeypatch,capsys):
    module=adapter();stage=importlib.import_module('repeatability');tach=importlib.import_module('tach_review');parser=importlib.import_module('sync_csv')
    raw=ROOT/'data/physical/hx_20261029_007/board_export/relief_a128_20261029_01'
    real_load=parser.load_capture;loads=[]
    def counted(*args,**kwargs):loads.append(1);return real_load(*args,**kwargs)
    for target in [module,stage,tach]:monkeypatch.setattr(target,'load_capture',counted)
    out=tmp_path/'analysis'
    assert stage.main(['review',str(raw),'--avg-log2','7','--out',str(out)])==0
    report=json.loads((out/'repeatability.json').read_text())
    assert len(loads)==1
    assert report['hardware_windows_verified']==0
    assert report['within_record_repeatability_pass'] is False
    assert report['correction_allowed'] is False
    assert report['mean_peak_mg']==pytest.approx(.156645,abs=.000002)
    text=capsys.readouterr().out
    assert '1/8' in text and '8/8' in text
    assert 'CPU' in text