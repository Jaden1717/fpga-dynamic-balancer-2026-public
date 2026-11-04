import sys
import json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'board/bringup'),str(ROOT/'sim/golden')]
import adxl355_pl_acq as adxl
from test_balance_captures import capture
from test_sync_capture import FIFOMMIO, AcqBoundary, Clock
import sync_capture
import sync_csv
import balance_captures

class Sensor:
    def __init__(self, bad=None, reserved=False):
        self.reg={0:0xad,0x2d:1}; self.bad=bad; self.reserved=reserved
    def spi_write(self,r,v):
        if r==0x2f: self.reg[0x2d]=1
        else: self.reg[r]=v
    def spi_read(self,r):
        if r==self.bad: return 0xff
        return self.reg.get(r,0) | ({0x28:0x80,0x2c:0x3c,0x2d:0xf8}.get(r,0) if self.reserved else 0)

@pytest.mark.parametrize('register',[0x28,0x2c,0x2d])
def test_bad_readback_blocks_measurement(register):
    with pytest.raises(RuntimeError,match='readback'):
        adxl.configure(Sensor(register))

def test_reserved_bits_ignored_and_actual_readback_retained():
    result=adxl.configure(Sensor(reserved=True))
    assert result['verified'] is True
    assert result['registers']['RANGE']==0xbd

def test_configuration_failure_is_archived_before_acquisition(tmp_path):
    hw=FIFOMMIO(); acq=AcqBoundary(hw); clock=Clock(); sensor=Sensor(0x28)
    acq.spi_read=sensor.spi_read; acq.spi_write=sensor.spi_write
    result=sync_capture.capture_sync(acq,sync_capture.SyncCapture(hw),.01,tmp_path,
                                     monotonic=clock,sleep=clock.sleep)
    assert result['status']=='FAILED'
    assert not acq.started
    assert result['sensor_configuration']['registers']['FILTER']==255
    assert result['raw_sha256']['samples.csv']

def test_clipping_report_prevents_balance(tmp_path):
    a=capture(tmp_path/'a'); b=capture(tmp_path/'b',.01)
    path=a/'samples.csv'; rows=path.read_text().splitlines(); r=rows[10].split(',');r[2]='524287';rows[10]=','.join(r);path.write_text('\n'.join(rows)+'\n')
    result=sync_csv.analyze_capture(a,axis='x')
    assert result['quality']['axes']['x']['rail_count']==1
    with pytest.raises(ValueError,match='clipping'):
        balance_captures.solve_captures(a,b,axis='x',trial_mass_g=.5,trial_radius_mm=20,trial_angle_deg=90,correction_radius_mm=20)

def test_quality_exposes_periods_and_per_turn_complex_values(tmp_path):
    a=capture(tmp_path/'a')
    report=sync_csv.analyze_capture(a,axis='x')['quality']
    assert report['period_seconds']==pytest.approx([.04,.04])
    assert len(report['turn_coefficients_g'])==2
    assert report['turn_coefficients_g'][0]['real']==pytest.approx(.02,abs=.0001)
    assert report['period_relative_max_deviation']<1e-10

def test_equal_mean_speed_does_not_hide_period_variation(tmp_path):
    a=capture(tmp_path/'a');b=capture(tmp_path/'b',.01)
    p=a/'pulses.csv'; rows=p.read_text().splitlines(); r=rows[2].split(',');r[1]=str(int(r[1])-1000000);rows[2]=','.join(r);p.write_text('\n'.join(rows)+'\n')
    with pytest.raises(ValueError,match='period'):
        balance_captures.solve_captures(a,b,axis='x',trial_mass_g=.5,trial_radius_mm=20,trial_angle_deg=90,correction_radius_mm=20)

def test_weak_trial_relative_to_turn_scatter_is_refused(tmp_path):
    a=capture(tmp_path/'a');b=capture(tmp_path/'b',.02001)
    p=b/'samples.csv'; rows=p.read_text().splitlines()
    for i in range(1,len(rows)):
        r=rows[i].split(','); r[2]=str(round(int(r[2])*(.8 if i<201 else 1.2)));rows[i]=','.join(r)
    p.write_text('\n'.join(rows)+'\n')
    with pytest.raises(ValueError,match='resolvable'):
        balance_captures.solve_captures(a,b,axis='x',trial_mass_g=.5,trial_radius_mm=20,trial_angle_deg=90,correction_radius_mm=20)


def test_claimed_hash_mismatch_rejected(tmp_path):
    a=capture(tmp_path/'a');p=a/'capture.json';m=json.loads(p.read_text());m['raw_sha256']={'samples.csv':'0'*64,'pulses.csv':'0'*64};p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='hash'):
        sync_csv.load_capture(a)

def test_quality_cli_saves_static_report_without_overwrite(tmp_path):
    a=capture(tmp_path/'a');p=a/'capture.json';m=json.loads(p.read_text());m.update(status='STATIC_ONLY',rotation_ready=False,pulse_count=0);p.write_text(json.dumps(m));(a/'pulses.csv').write_text('pulse_count,ticks\n')
    out=tmp_path/'quality.json'
    assert sync_csv.main([str(a),'--quality-only','--out',str(out)])==0
    result=json.loads(out.read_text())
    assert result['quality']['axes']['z']['max_raw']==256000
    assert result['source_sha256']['samples.csv']
    assert result['quality']['period_seconds']==[]
    with pytest.raises(SystemExit):sync_csv.main([str(a),'--quality-only','--out',str(out)])

def test_failed_measurement_power_readback_retains_failure(tmp_path):
    class MeasurementFail(Sensor):
        def spi_read(self,r):
            if r==0x2d and self.reg[r]==0: return 1
            return super().spi_read(r)
    with pytest.raises(RuntimeError,match='POWER_CTL readback'):adxl.configure(MeasurementFail())

def test_claimed_configuration_with_wrong_range_is_refused(tmp_path):
    a=capture(tmp_path/'a');p=a/'capture.json';m=json.loads(p.read_text());m['sensor_configuration']={'verified':True,'registers':{'FILTER':0,'RANGE':0x82,'POWER_CTL':0}};p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='configuration'):sync_csv.load_capture(a)
