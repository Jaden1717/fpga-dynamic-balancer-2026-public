"""Separate retrospective context must never rewrite or fill missing raw evidence."""
import hashlib,importlib,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))
RAW=ROOT/'data/physical/hx_20261029_007/board_export/relief_a128_20261029_01'


def api():
    assert (ROOT/'sim/golden/capture_context.py').exists(),'missing immutable capture context'
    return importlib.import_module('capture_context')


def test_retrospective_context_preserves_missing_configuration_and_unknown_setup():
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in RAW.iterdir() if p.is_file()}
    r=api().build_context(RAW,source='fan-tach-2ppr',notes=['User reported cable fixed; retrospective.'])
    assert r['observation_timing']=='retrospective'
    assert r['configuration_evidence']['recorded'] is False
    assert r['configuration_evidence']['value'] is None
    assert r['driver_identity_evidence']['recorded'] is False
    assert r['declared_setup']['trial_position'] is None
    assert r['declared_setup']['fan_power_state'] is None
    assert r['source_sha256']==before
    assert r['source_declaration_verified_by_software'] is False
    assert r['physical_validation_complete'] is False
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in RAW.iterdir() if p.is_file()}


def test_context_cannot_be_written_inside_frozen_capture():
    target=RAW/'new_context_must_not_exist.json'
    assert not target.exists()
    with pytest.raises(SystemExit):
        api().main([str(RAW),'--out',str(target)])
    assert not target.exists()


def test_context_cli_creates_external_bound_sidecar_and_refuses_overwrite(tmp_path):
    target=tmp_path/'context.json'
    assert api().main([str(RAW),'--source','fan-tach-2ppr','--note','Retrospective note','--out',str(target)])==0
    before=target.read_bytes();r=json.loads(before)
    assert r['notes']==['Retrospective note']
    assert r['source_sha256']['samples.csv']=='3f9da7c717397be71b4e6542f4609f519a0e07e39dd52969a5347457ba8eb010'
    with pytest.raises(SystemExit):
        api().main([str(RAW),'--out',str(target)])
    assert target.read_bytes()==before


def test_invalid_observation_timing_is_not_silently_relabelled():
    with pytest.raises(ValueError):
        api().build_context(RAW,observation_timing='verified_by_hardware')
