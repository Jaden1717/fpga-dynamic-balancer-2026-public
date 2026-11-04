"""Timing must preserve the real driver's MMIO sequence and failure cleanup."""
import importlib
from pathlib import Path
import sys
import pytest
from test_order1x_accel import AcceleratorMMIO,driver
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sim/golden'))


def api():
    assert (ROOT/'sim/golden/measurement_timing.py').exists(),'missing phase timing adapter'
    return importlib.import_module('measurement_timing')


class Clock:
    def __init__(self):self.value=0.
    def __call__(self):return self.value
    def sleep(self,seconds):self.value+=seconds


class TimedMMIO(AcceleratorMMIO):
    def __init__(self,clock):super().__init__();self.clock=clock
    def read(self,offset):self.clock.value+=.002;return super().read(offset)
    def write(self,offset,value):self.clock.value+=.003;return super().write(offset,value)


def test_profile_preserves_register_sequence_and_measures_real_boundaries():
    m=api();clock=Clock();hw=TimedMMIO(clock)
    inner=driver().Order1xAccelerator(hw,monotonic=clock,sleep=clock.sleep)
    values=[];acc=m.TimedAccelerator(inner,values.append,clock=clock)
    result=acc.run([0]*64,0)
    clock2=Clock();hw2=TimedMMIO(clock2)
    plain=driver().Order1xAccelerator(hw2,monotonic=clock2,sleep=clock2.sleep).run([0]*64,0)
    assert result==plain and hw.writes==hw2.writes and hw.reads==hw2.reads
    assert inner.mmio is hw
    r=values[0]
    assert r['completed'] is True
    assert r['input_feed_wall_seconds']==pytest.approx(.320)
    assert r['result_wait_wall_seconds']==pytest.approx(.00602)
    assert r['snapshot_ack_wall_seconds']==pytest.approx(.019)


def test_timeout_is_not_reported_as_a_completed_profile_and_restores_mmio():
    m=api();clock=Clock();hw=TimedMMIO(clock);hw.never_result=True
    inner=driver().Order1xAccelerator(hw,monotonic=clock,sleep=clock.sleep)
    values=[];acc=m.TimedAccelerator(inner,values.append,clock=clock)
    with pytest.raises(TimeoutError):acc.run([0]*64,0,timeout=.4)
    assert inner.mmio is hw and hw.active is False
    assert values[0]['completed'] is False
    assert all(offset!=0x30 for offset,value in hw.writes)
