"""Extra readback must occur while the source is disabled, after peer setup."""
import importlib
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))


def api():
    assert (ROOT/'sim/golden/measurement_capture.py').exists(),'missing sealed peer adapter'
    return importlib.import_module('measurement_capture')


def test_actual_register_readback_is_separate_from_peer_configure_return():
    m=api()
    class Acq:
        def __init__(self):self.configured=False
        def rd(self,offset):assert offset==0x4000;return 0
        def spi_read(self,reg):
            assert self.configured
            return {0:173,0x28:0,0x2c:129,0x2d:0}[reg]
    acq=Acq()
    def original(a):a.configured=True;return None
    r=m.configure_observed(acq,original)
    assert r['verified'] is True
    assert r['registers']=={'DEVID':173,'FILTER':0,'RANGE':129,'POWER_CTL':0}


def test_configure_observer_never_uses_manual_spi_while_source_enabled():
    class Acq:
        def rd(self,offset):return 1
        def spi_read(self,reg):raise AssertionError('must not read SPI')
    with pytest.raises(RuntimeError,match='disabled'):api().configure_observed(Acq(),lambda acq:None)


def test_unexpected_peer_files_rejected_before_import_or_overlay(tmp_path):
    for n in ['sync_capture.py','adxl355_pl_acq.py']:(tmp_path/n).write_text('raise AssertionError("must not import")')
    xsa=tmp_path/'capture.xsa';xsa.write_bytes(b'wrong')
    with pytest.raises(ValueError,match='fingerprint|XSA'):
        api().SealedCapture(tmp_path,tmp_path,xsa)



def test_configuration_mismatch_retains_the_observed_bad_registers():
    class Acq:
        def rd(self,offset):return 0
        def spi_read(self,reg):return {0:173,0x28:3,0x2c:129,0x2d:0}[reg]
    with pytest.raises(RuntimeError) as error:api().configure_observed(Acq(),lambda acq:None)
    assert error.value.configuration['verified'] is False
    assert error.value.configuration['registers']['FILTER']==3
