"""An offline demo must show validated archived data and preserve all evidence."""
import hashlib,importlib,json,shutil,sys
from html.parser import HTMLParser
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))
RAW=ROOT/'data/physical/hx_20261029_007/board_export/relief_a128_20261029_01'
BOARD=RAW.parent/'relief_a128_20261029_01_analysis'
CONTEXT=ROOT/'data/review/hx_improvement_20261029/relief_capture_context.json'


def api():
    assert (ROOT/'sim/golden/evidence_demo.py').is_file(),'missing verified evidence demo'
    return importlib.import_module('evidence_demo')


def digests(folder):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}


def test_archived_demo_reverifies_and_bundles_all_eight_windows_without_touching_sources(tmp_path):
    before=(digests(RAW),digests(BOARD))
    out=tmp_path/'demo'
    r=api().build_demo(RAW,out,hardware_analysis=BOARD,context_path=CONTEXT)
    assert r['hardware_execution_this_run'] is False
    assert r['hardware_windows_verified']==8
    assert r['measurement']['mean_peak_mg']==pytest.approx(.1566448211669922)
    assert r['measurement']['sample_sd_peak_mg']==pytest.approx(.0174193772219499)
    assert r['measurement']['cv']==pytest.approx(.11120302026059237)
    assert r['capture']['sample_count']==159108 and r['capture']['pulse_count']==2291
    assert r['correction_allowed'] is False
    assert r['context']['configuration_evidence']['recorded'] is False
    assert len(r['windows'])==8
    from verify_order1x import verify
    for start in range(0,1024,128):
        assert verify(out/'evidence'/('input_%d.json'%start),out/'evidence'/('result_%d.json'%start))['verified']
    for line in (out/'sha256sums.txt').read_text().splitlines():
        digest,name=line.split('  ',1)
        assert hashlib.sha256((out/name).read_bytes()).hexdigest()==digest
    assert (out/'index.html').is_file() and (out/'report.json').is_file()
    assert before==(digests(RAW),digests(BOARD))


def test_cpu_demo_never_labels_reference_as_hardware(tmp_path):
    out=tmp_path/'cpu'
    r=api().build_demo(RAW,out)
    assert r['evidence_kind']=='cpu_reference'
    assert r['hardware_windows_verified']==0
    assert r['hardware_execution_this_run'] is False
    assert r['measurement']['mean_peak_mg']==pytest.approx(.156645,abs=.000002)
    assert not list((out/'evidence').glob('result_*.json'))


@pytest.mark.parametrize('damage',['display','provenance','input'])
def test_contradictory_evidence_cannot_create_demo(tmp_path,damage):
    board=tmp_path/'board';shutil.copytree(BOARD,board)
    result_path=board/'result_0.json';r=json.loads(result_path.read_text())
    if damage=='display':r['amplitude_g']=123
    if damage=='provenance':r['source_provenance']['source_sha256']['samples.csv']='0'*64
    if damage=='input':
        p=board/'input_0.json';v=json.loads(p.read_text());v['samples_raw'][0]+=1
        p.write_text(json.dumps(v));r['prepared_input_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    result_path.write_text(json.dumps(r))
    out=tmp_path/'invalid'
    with pytest.raises(ValueError):
        api().build_demo(RAW,out,hardware_analysis=board)
    assert not out.exists()


def test_context_from_another_record_is_rejected(tmp_path):
    p=tmp_path/'context.json';v=json.loads(CONTEXT.read_text())
    v['source_sha256']['samples.csv']='0'*64;p.write_text(json.dumps(v))
    with pytest.raises(ValueError,match='context'):
        api().build_demo(RAW,tmp_path/'demo',context_path=p)
    assert not (tmp_path/'demo').exists()


def test_existing_output_is_preserved_before_source_access(tmp_path):
    out=tmp_path/'keep';out.mkdir();(out/'keep.txt').write_text('keep')
    with pytest.raises(ValueError,match='exists'):
        api().build_demo('missing',out)
    assert (out/'keep.txt').read_text()=='keep'


@pytest.mark.parametrize('source',[RAW,BOARD])
def test_output_cannot_be_created_inside_frozen_sources(source):
    out=source/'demo_must_not_be_created'
    assert not out.exists()
    with pytest.raises(ValueError,match='outside'):
        api().build_demo(RAW,out,hardware_analysis=BOARD)
    assert not out.exists()


def test_html_embedded_data_roundtrips_markup_without_creating_injected_elements(tmp_path):
    note='</script><img src=x onerror=window.injected=true><script>window.injected=true</script>'
    c=json.loads(CONTEXT.read_text());c['notes']=[note]
    path=tmp_path/'context.json';path.write_text(json.dumps(c))
    out=tmp_path/'demo';api().build_demo(RAW,out,context_path=path)
    class Reader(HTMLParser):
        def __init__(self):
            super().__init__();self.active=False;self.payload='';self.injected=[]
        def handle_starttag(self,tag,attrs):
            values=dict(attrs)
            if tag=='script' and values.get('id')=='demo-data':self.active=True
            if tag=='img' and values.get('onerror'):self.injected.append(values)
        def handle_endtag(self,tag):
            if tag=='script':self.active=False
        def handle_data(self,data):
            if self.active:self.payload+=data
    reader=Reader();reader.feed((out/'index.html').read_text(encoding='utf-8'))
    assert not reader.injected
    assert json.loads(reader.payload)['context']['notes']==[note]


def test_source_change_during_verification_is_not_published(tmp_path):
    board=tmp_path/'board';shutil.copytree(BOARD,board)
    def change(stage,done,total):
        if stage=='verified' and done==8:
            p=board/'result_0.json';p.write_bytes(p.read_bytes()+b' ')
    out=tmp_path/'demo'
    with pytest.raises(ValueError,match='changed'):
        api().build_demo(RAW,out,hardware_analysis=board,progress=change)
    assert not out.exists()
