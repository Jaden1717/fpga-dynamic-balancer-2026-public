"""External observation of the sealed HXS1 peer, without editing its source.

The original configure/stop/drain paths run unchanged. Four extra sensor
register reads occur only after configuration with the PL source disabled.
"""
import hashlib
import importlib.util
from pathlib import Path
import sys

CAPTURE_XSA='eca1b6e2c84d76b3f1ceabf1e30f4cbe0625e7c9551d2d977f8bfd12633dd08a'
PEER_HASHES={'sync_capture.py':'5104db8f339c33e155a0c7ac32227ae14b7933496623248200345a189cb39cb3',
             'adxl355_pl_acq.py':'5986205223a3f0de8af1f09c94bac945045738ffbbbb077013a38b82a63e66de'}
SNAPSHOT_SHA='0ae5c92bb9c618cfaa5329dbe7bd70ce09c36ccbb434c7b086091be9c10207cb'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SensorConfigurationError(RuntimeError):
    def __init__(self,message,configuration):
        super().__init__(message)
        self.configuration=configuration


def configure_observed(acq,configure):
    if int(acq.rd(0x4000))&1:
        raise RuntimeError('manual configuration requires disabled PL source')
    configure(acq)
    if int(acq.rd(0x4000))&1:
        raise RuntimeError('configuration readback requires disabled PL source')
    registers={name:int(acq.spi_read(reg)) for name,reg in
               [('DEVID',0),('FILTER',0x28),('RANGE',0x2c),('POWER_CTL',0x2d)]}
    observation=dict(verified=False,registers=registers,
                     observation='after original peer configure, before source enable; additional readback, no additional sensor register writes')
    for name,expected,mask in [('DEVID',173,255),('FILTER',0,127),('RANGE',129,195),('POWER_CTL',0,7)]:
        if not 0<=registers[name]<=255 or registers[name]&mask!=expected&mask:
            raise SensorConfigurationError('sensor configuration readback mismatch: '+name,observation)
    observation['verified']=True
    return observation


class SealedCapture:
    def __init__(self,peer_directory,evidence_directory,xsa):
        self.peer=Path(peer_directory).resolve()
        self.evidence=Path(evidence_directory).resolve()
        self.xsa=Path(xsa).resolve()
        if digest(self.xsa)!=CAPTURE_XSA:raise ValueError('unexpected capture XSA fingerprint')
        for name,wanted in PEER_HASHES.items():
            if digest(self.peer/name)!=wanted:raise ValueError('peer fingerprint mismatch: '+name)
        if digest(self.evidence/'hxs1_stop_snapshot.py')!=SNAPSHOT_SHA:
            raise ValueError('stop snapshot fingerprint mismatch')
        self.identities=dict(PEER_HASHES,**{'hxs1_stop_snapshot.py':SNAPSHOT_SHA})
        self.sensor_configuration=None
        self.driver=self.snapshot=None

    def open(self):
        # Restore import state so a different ABI's module cannot leak into the
        # sealed peer and the peer cannot poison later imports in a PC process.
        prior=sys.modules.get('adxl355_pl_acq')
        try:
            sys.modules['adxl355_pl_acq']=_load('adxl355_pl_acq',self.peer/'adxl355_pl_acq.py')
            self.driver=_load('measurement_sealed_hxs1',self.peer/'sync_capture.py')
        finally:
            if prior is None:sys.modules.pop('adxl355_pl_acq',None)
            else:sys.modules['adxl355_pl_acq']=prior
        self.snapshot=_load('measurement_hxs1_stop',self.evidence/'hxs1_stop_snapshot.py')
        self.acq,self.sync=self.driver.open_capture(self.xsa)

    def acquire(self,seconds,directory,*,allow_no_pulses,timings):
        def configured(acq):
            try:
                self.sensor_configuration=timings.call('sensor_initialization_and_readback',
                                                      configure_observed,acq,self.driver.configure)
            except SensorConfigurationError as error:
                self.sensor_configuration=error.configuration
                raise
        return self.driver.capture_sync(self.acq,self.sync,seconds,directory,
                allow_no_pulses=allow_no_pulses,xsa_sha256=CAPTURE_XSA,configure_sensor=configured)

    def stopped_snapshot(self,directory):
        return self.snapshot.inspect(directory,self.acq,self.sync)
