"""Real driver behavior against the AXI MMIO hardware boundary, no PYNQ needed."""
import importlib
import json
from pathlib import Path
import sys
import types

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'board'/'bringup'))


def driver():
    assert (ROOT/'board'/'bringup'/'order1x_accel.py').is_file(), 'missing FPGA 1x accelerator driver'
    return importlib.import_module('order1x_accel')


class Clock:
    def __init__(self,increment=1e-6):self.t=0.;self.increment=increment
    def __call__(self):self.t+=self.increment;return self.t
    def sleep(self,seconds):self.t+=seconds


class AcceleratorMMIO:
    """Register transaction effects; does not implement or replace the driver."""
    def __init__(self):
        self.writes=[];self.reads=[];self.words=[];self.avg=0;self.active=False
        self.pending=False;self.error=0;self.stall_reads=0;self.result_stalls=2
        self.never_input=False;self.never_result=False;self.interrupt_at=None
        self.error_at=None;self.completed_count_delta=0;self.snapshot=set()
        self.magic=0x48583131;self.version=0x10000;self.acknowledged=False
        self.real_word=0xff800000;self.imag_word=0x00400000
        self.mag_word=0x00c00000;self.phase_word=0xc0000000;self.phase_defined=True
    def read(self,off):
        self.reads.append(off)
        if off==0:return self.magic
        if off==4:return self.version
        if off==0xc:
            if self.error_at is not None and len(self.words)>=self.error_at:self.error=4
            if self.active and len(self.words)==64*(1<<self.avg) and not self.never_result:
                if self.result_stalls:self.result_stalls-=1
                else:self.pending=True
            ready=self.active and not self.pending and len(self.words)<64*(1<<self.avg) and not self.never_input
            if ready and self.stall_reads:self.stall_reads-=1;ready=False
            return int(self.active or self.pending)|int(ready)*2|int(self.pending)*4|int(self.pending and self.phase_defined)*8|int(bool(self.error))*16
        if off==0x38:return self.error
        if off in (0x18,0x1c,0x20,0x24,0x28,0x2c,0x34):
            assert self.pending and not self.acknowledged, 'result snapshot must precede ACK'
            self.snapshot.add(off)
            return {0x18:len(self.words)+self.completed_count_delta,0x1c:64*(1<<self.avg),
                    0x20:self.real_word,0x24:self.imag_word,0x28:self.mag_word,
                    0x2c:self.phase_word,0x34:98765}[off]
        raise AssertionError('unexpected read offset %x'%off)
    def write(self,off,value):
        self.writes.append((off,value))
        if off==8 and value==2:
            self.active=False;self.pending=False;self.error=0;self.words=[]
            self.snapshot=set();self.acknowledged=False
        elif off==0x10:
            assert not self.active;self.avg=value
        elif off==8 and value==1:
            assert not self.active and not self.pending;self.active=True
        elif off==0x14:
            assert self.active and not self.pending and len(self.words)<64*(1<<self.avg)
            if self.interrupt_at==len(self.words):raise KeyboardInterrupt()
            self.words.append(value)
        elif off==0x30 and value==1:
            assert self.pending and self.snapshot=={0x18,0x1c,0x20,0x24,0x28,0x2c,0x34}, 'ACK discarded unsnapshotted output'
            self.acknowledged=True;self.pending=False;self.active=False
        else:raise AssertionError('unexpected write %x=%x'%(off,value))


def make_accel(hw=None,clock=None):
    mod=driver();hw=hw or AcceleratorMMIO();clock=clock or Clock()
    return mod.Order1xAccelerator(hw,monotonic=clock,sleep=clock.sleep),hw


def prepared():
    return dict(schema_version=1,core_abi_version=0x10000,points_per_turn=64,
                avg_log2=0,samples_raw=[0]*64,axis='x',used_turns=1,
                source_capture_xsa_sha256='a'*64,source_capture_kind='SYNTHETIC',
                reference={'real_q8':0,'imag_q8':0},custom_note='retain this')


def test_run_decodes_signed_outputs_and_snapshots_before_ack():
    acc,hw=make_accel();words=[-524288,524287,-1]+[0]*61;hw.stall_reads=4
    result=acc.run(words,0)
    assert hw.words==[0xfff80000,0x0007ffff,0xffffffff]+[0]*61
    assert hw.writes[:3]==[(8,2),(0x10,0),(8,1)]
    assert hw.writes[-1]==(0x30,1) and hw.acknowledged
    assert result['real_q8']==-8388608 and result['imag_q8']==4194304
    assert result['magnitude_q8']==12582912 and result['phase_code']==-1073741824
    assert result['real_g']==pytest.approx(-8388608/65536000)
    assert result['imag_g']==pytest.approx(4194304/65536000)
    assert result['amplitude_g']==pytest.approx(12582912/65536000)
    assert result['phase_deg']==-90 and result['phase_defined'] is True
    assert result['hardware_cycles']==98765 and result['accepted_samples']==64
    assert result['expected_samples']==64 and result['wall_seconds']>0
    assert result['physical_phase_calibrated'] is False


@pytest.mark.parametrize('avg',[0,1,8])
def test_exact_word_count_for_each_supported_window(avg):
    acc,hw=make_accel();acc.run([0]*(64<<avg),avg)
    assert len(hw.words)==64<<avg


@pytest.mark.parametrize('words,avg,timeout',[
    ([0]*64,True,2),([0]*64,0.0,2),([0]*64,9,2),([0]*64,-1,2),
    ([0]*63,0,2),([0]*65,0,2),([True]+[0]*63,0,2),([1.0]+[0]*63,0,2),
    ([524288]+[0]*63,0,2),([-524289]+[0]*63,0,2),
    ([0]*64,0,0),([0]*64,0,-1),([0]*64,0,float('nan')),
    ([0]*64,0,float('inf')),([0]*64,0,True),([0]*64,0,'2')])
def test_bad_request_does_not_mutate_hardware(words,avg,timeout):
    acc,hw=make_accel()
    with pytest.raises(ValueError):acc.run(words,avg,timeout=timeout)
    assert hw.writes==[]


@pytest.mark.parametrize('which',['magic','version'])
def test_wrong_identity_rejected_without_mutation(which):
    mod=driver();hw=AcceleratorMMIO();setattr(hw,which,0)
    with pytest.raises(ValueError):mod.Order1xAccelerator(hw)
    assert hw.writes==[]


@pytest.mark.parametrize('where',['input','result'])
def test_timeout_aborts_without_ack_or_success(where):
    hw=AcceleratorMMIO();setattr(hw,'never_'+where,True)
    acc,hw=make_accel(hw,Clock(increment=1e-4))
    with pytest.raises(TimeoutError):acc.run([0]*64,0,timeout=.02)
    assert hw.writes[-1]==(8,2) and (0x30,1) not in hw.writes
    assert not hw.active and not hw.pending


def test_hardware_sticky_error_aborts_and_retains_error_code_in_exception():
    hw=AcceleratorMMIO();hw.error_at=3;acc,hw=make_accel(hw)
    with pytest.raises(RuntimeError,match='0x00000004'):acc.run([0]*64,0)
    assert hw.writes[-1]==(8,2) and (0x30,1) not in hw.writes


def test_keyboard_interrupt_clears_inflight_block_and_reraises():
    hw=AcceleratorMMIO();hw.interrupt_at=4;acc,hw=make_accel(hw)
    with pytest.raises(KeyboardInterrupt):acc.run([0]*64,0)
    assert hw.writes[-1]==(8,2) and not hw.active
    assert (0x30,1) not in hw.writes


def test_inconsistent_result_count_is_not_acked_or_reported_as_success():
    hw=AcceleratorMMIO();hw.completed_count_delta=-1;acc,hw=make_accel(hw)
    with pytest.raises(RuntimeError,match='count'):acc.run([0]*64,0)
    assert hw.writes[-1]==(8,2) and (0x30,1) not in hw.writes


def test_zero_vector_reports_undefined_phase():
    hw=AcceleratorMMIO();hw.real_word=hw.imag_word=hw.mag_word=hw.phase_word=0
    hw.phase_defined=False;acc,hw=make_accel(hw);result=acc.run([0]*64,0)
    assert result['amplitude_g']==0 and result['phase_defined'] is False


def install_pynq(monkeypatch,hw,calls,ip_dict=None):
    def overlay(path):
        calls.append(('download',path))
        return types.SimpleNamespace(ip_dict=ip_dict if ip_dict is not None else {
            'order1x_axi_0':{'type':'user.org:user:order1x_axi:1.0','phys_addr':0x43c10000}})
    def mmio(base,length):calls.append(('map',base,length));return hw
    monkeypatch.setitem(sys.modules,'pynq',types.SimpleNamespace(Overlay=overlay,MMIO=mmio))


def test_open_downloads_once_and_maps_exact_64k(monkeypatch):
    mod=driver();hw=AcceleratorMMIO();calls=[];install_pynq(monkeypatch,hw,calls)
    acc=mod.open_accelerator('order1x.xsa')
    assert isinstance(acc,mod.Order1xAccelerator)
    assert calls==[('download','order1x.xsa'),('map',0x43c10000,0x10000)]


@pytest.mark.parametrize('ip_dict',[{}, {'a':{'type':'order1x_axi','phys_addr':0},'b':{'type':'order1x_axi','phys_addr':4}}, {'a':{'type':'order1x_axi'}}])
def test_ambiguous_or_unmapped_ip_is_rejected(monkeypatch,ip_dict):
    mod=driver();calls=[];install_pynq(monkeypatch,AcceleratorMMIO(),calls,ip_dict)
    with pytest.raises(RuntimeError):mod.open_accelerator('order1x.xsa')
    assert not any(call[0]=='map' for call in calls)


def write_request(tmp_path,document=None):
    source=tmp_path/'prepared.json';source.write_text(json.dumps(prepared() if document is None else document),encoding='utf-8')
    xsa=tmp_path/'order1x.xsa';xsa.write_bytes(b'accelerator image, distinct from capture image')
    return source,xsa,tmp_path/'result.json'


def test_cli_keeps_source_provenance_separate_from_accelerator_image(tmp_path,monkeypatch):
    mod=driver();source,xsa,out=write_request(tmp_path);calls=[]
    install_pynq(monkeypatch,AcceleratorMMIO(),calls)
    assert mod.main(['--xsa',str(xsa),'--input',str(source),'--out',str(out)])==0
    result=json.loads(out.read_text(encoding='utf-8'))
    assert result['source_provenance']['source_capture_xsa_sha256']=='a'*64
    assert result['source_provenance']['source_capture_kind']=='SYNTHETIC'
    assert result['source_provenance']['reference']=={'real_q8':0,'imag_q8':0}
    assert result['source_provenance']['custom_note']=='retain this'
    assert 'samples_raw' not in result['source_provenance']
    assert len(result['accelerator_xsa_sha256'])==64 and result['accelerator_xsa_sha256']!='a'*64
    assert len([c for c in calls if c[0]=='download'])==1


@pytest.mark.parametrize('bad_field,bad_value',[
    ('schema_version',2),('schema_version',True),('core_abi_version',0),
    ('points_per_turn',32),('avg_log2',9),('samples_raw',[0]*63),
    ('samples_raw',[False]+[0]*63),('samples_raw',[0.1]+[0]*63)])
def test_cli_invalid_json_preflight_never_downloads(tmp_path,monkeypatch,bad_field,bad_value):
    mod=driver();document=prepared();document[bad_field]=bad_value
    source,xsa,out=write_request(tmp_path,document);calls=[]
    install_pynq(monkeypatch,AcceleratorMMIO(),calls)
    with pytest.raises(SystemExit):mod.main(['--xsa',str(xsa),'--input',str(source),'--out',str(out)])
    assert calls==[] and not out.exists()


def test_cli_does_not_overwrite_or_download_for_existing_output(tmp_path,monkeypatch):
    mod=driver();source,xsa,out=write_request(tmp_path);out.write_text('precious')
    calls=[];install_pynq(monkeypatch,AcceleratorMMIO(),calls)
    with pytest.raises(SystemExit):mod.main(['--xsa',str(xsa),'--input',str(source),'--out',str(out)])
    assert calls==[] and out.read_text()=='precious'


def test_cli_rejects_nan_metadata_before_download(tmp_path,monkeypatch):
    mod=driver();document=prepared();document['reference']['amplitude_g']=float('nan')
    source,xsa,out=write_request(tmp_path,document);calls=[]
    install_pynq(monkeypatch,AcceleratorMMIO(),calls)
    with pytest.raises(SystemExit):mod.main(['--xsa',str(xsa),'--input',str(source),'--out',str(out)])
    assert calls==[] and not out.exists()


def test_cli_hash_binds_the_preflight_input_bytes_even_if_file_changes_later(tmp_path,monkeypatch):
    import hashlib
    mod=driver();source,xsa,out=write_request(tmp_path)
    original_digest=hashlib.sha256(source.read_bytes()).hexdigest()
    calls=[];install_pynq(monkeypatch,AcceleratorMMIO(),calls)
    original_open=mod.open_accelerator
    def replace_input_then_open(image):
        source.write_text('{"changed_after_validation": true}',encoding='utf-8')
        return original_open(image)
    monkeypatch.setattr(mod,'open_accelerator',replace_input_then_open)
    mod.main(['--xsa',str(xsa),'--input',str(source),'--out',str(out)])
    result=json.loads(out.read_text(encoding='utf-8'))
    assert result['prepared_input_sha256']==original_digest
    assert result['source_provenance']['source_capture_xsa_sha256']=='a'*64
