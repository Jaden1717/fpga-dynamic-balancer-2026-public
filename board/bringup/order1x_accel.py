"""Run the ABI-v1 FPGA 64-angle 1x amplitude/phase accelerator.

The prepared JSON comes from sim/golden/order1x_fixed.py. Interpolation from
shared timestamps to angle samples remains on the PC/PS. This driver feeds
those integer samples to a separate compute overlay, which replaces capture.
PYNQ is imported only by open_accelerator(), so the module works in PC tests.

Board example:
  sudo env HOME=/home/xilinx PATH="$PATH" python3 order1x_accel.py \
    --xsa order1x.xsa --input prepared.json --out new_result.json
"""
import argparse
import hashlib
import json
import math
from numbers import Integral, Real
from pathlib import Path
import time

MAGIC=0x48583131
VERSION=0x00010000
POINTS_PER_TURN=64
SCALE_G=65536000  # 256000 raw counts/g, with eight output fraction bits.
CTRL=0x08
STATUS=0x0c
AVG_LOG2=0x10
INPUT=0x14
ACCEPTED=0x18
EXPECTED=0x1c
REAL_Q8=0x20
IMAG_Q8=0x24
MAG_Q8=0x28
PHASE=0x2c
ACK=0x30
CYCLES=0x34
ERROR_CODE=0x38


def _signed32(value):
    value=int(value)&0xffffffff
    return value-(1<<32) if value&(1<<31) else value


def _validate_timeout(timeout):
    if isinstance(timeout,bool) or not isinstance(timeout,Real) or not math.isfinite(timeout) or timeout<=0:
        raise ValueError('timeout must be finite and positive')
    return float(timeout)


def _validate_request(samples_raw,avg_log2,timeout):
    timeout=_validate_timeout(timeout)
    if isinstance(avg_log2,bool) or not isinstance(avg_log2,Integral) or not 0<=avg_log2<=8:
        raise ValueError('avg_log2 must be an integer in 0..8')
    avg=int(avg_log2)
    try:
        words=list(samples_raw)
    except TypeError as error:
        raise ValueError('samples_raw must be an iterable of signed20 integers') from error
    if len(words)!=64*(1<<avg):
        raise ValueError('exactly 64 * 2**avg_log2 samples required')
    if any(isinstance(value,bool) or not isinstance(value,Integral) or not -(1<<19)<=value<(1<<19) for value in words):
        raise ValueError('samples_raw must contain signed20 integer raw counts')
    return [int(value) for value in words],avg,timeout


class Order1xAccelerator:
    """MMIO transaction owner; one block per run, with cleanup on any failure.

    Hardware cycles include input feeding gaps. wall_seconds includes Python
    and MMIO overhead. Neither quantity alone establishes acceleration over a
    software implementation. Phase is referenced to optical angle zero and
    has not been physically calibrated for sensor/group/mechanical delay.
    """
    def __init__(self,mmio,*,monotonic=time.monotonic,sleep=time.sleep):
        self.mmio=mmio
        self._monotonic=monotonic
        self._sleep=sleep
        if self.rd(0)!=MAGIC or self.rd(4)!=VERSION:
            raise ValueError('wrong order1x accelerator magic or ABI version')

    def rd(self,offset):
        return int(self.mmio.read(offset))&0xffffffff

    def clear(self):
        self.mmio.write(CTRL,2)

    def _raise_hardware_error(self,status):
        if status&0x10:
            code=self.rd(ERROR_CODE)
            names=[name for bit,name in enumerate(('start while busy','input not ready',
                   'signed20 range','invalid averaging','partial AXI write')) if code&(1<<bit)]
            raise RuntimeError('accelerator hardware error 0x{:08x}: {}'.format(code,', '.join(names) or 'unspecified'))

    def _wait(self,mask,deadline,stage):
        while True:
            if self._monotonic()>=deadline:
                raise TimeoutError('accelerator timeout waiting for '+stage)
            status=self.rd(STATUS)
            self._raise_hardware_error(status)
            if status&mask:
                return status
            self._sleep(.00001)

    def run(self,samples_raw,avg_log2,timeout=2.0):
        """Validate first, then clear/start/feed/snapshot/ACK exactly one block.

        The entire run shares one timeout, including input feeding and result
        computation. Exceptions and Ctrl+C abort with CLEAR and re-raise; no
        partial result is reported. All result registers are read before ACK.
        """
        words,avg,timeout=_validate_request(samples_raw,avg_log2,timeout)
        started=self._monotonic()
        deadline=started+timeout
        try:
            self.clear()
            self.mmio.write(AVG_LOG2,avg)
            self.mmio.write(CTRL,1)
            for value in words:
                self._wait(2,deadline,'input ready')
                self.mmio.write(INPUT,value&0xffffffff)
            status=self._wait(4,deadline,'result valid')
            # ABI guarantees stable result/count/cycle registers until ACK.
            accepted=self.rd(ACCEPTED)
            expected=self.rd(EXPECTED)
            real=_signed32(self.rd(REAL_Q8))
            imag=_signed32(self.rd(IMAG_Q8))
            magnitude=self.rd(MAG_Q8)
            phase=_signed32(self.rd(PHASE))
            cycles=self.rd(CYCLES)
            self._raise_hardware_error(self.rd(STATUS))
            if accepted!=len(words) or expected!=len(words):
                raise RuntimeError('accelerator sample count mismatch: accepted {}, expected {}, requested {}'.format(accepted,expected,len(words)))
            result=dict(schema_version=1,core_abi_version=VERSION,points_per_turn=64,
                        avg_log2=avg,used_turns=1<<avg,accepted_samples=accepted,
                        expected_samples=expected,real_q8=real,imag_q8=imag,
                        magnitude_q8=magnitude,phase_code=phase,
                        real_g=real/SCALE_G,imag_g=imag/SCALE_G,
                        amplitude_g=magnitude/SCALE_G,
                        phase_deg=phase*360.0/(1<<32),phase_defined=bool(status&8),
                        hardware_cycles=cycles,
                        physical_phase_calibrated=False,
                        amplitude_semantics='coherent peak 1x acceleration, not RMS or peak-to-peak',
                        timing_semantics='hardware cycles include MMIO feeding gaps; wall time includes Python/MMIO overhead',
                        resampling_location='PC/PS before accelerator input')
            self.mmio.write(ACK,1)
            result['wall_seconds']=self._monotonic()-started
            return result
        except BaseException as error:
            try:
                self.clear()
            except BaseException as cleanup_error:
                # PYNQ 3.0.1 may run Python 3.10 without add_note(). Preserve
                # the original failure rather than replacing it with cleanup.
                if hasattr(error,'add_note'):
                    error.add_note('accelerator CLEAR also failed: '+str(cleanup_error))
            raise


def open_accelerator(xsa):
    """Download once; resolve exactly one order1x_axi and map its 64 KiB ABI."""
    from pynq import MMIO, Overlay
    overlay=Overlay(str(xsa))
    candidates=[value for name,value in overlay.ip_dict.items()
                if 'order1x_axi' in str(value.get('type','')) or 'order1x_axi' in name]
    if len(candidates)!=1:
        raise RuntimeError('expected one order1x_axi in overlay ip_dict, found {}'.format(len(candidates)))
    ip=candidates[0]
    base=ip.get('phys_addr',ip.get('address'))
    if base is None:base=ip.get('parameters',{}).get('C_BASEADDR')
    if base is None:raise RuntimeError('order1x_axi has no physical address')
    try:base=base if isinstance(base,int) else int(str(base),0)
    except (TypeError,ValueError) as error:raise RuntimeError('invalid order1x_axi physical address') from error
    if isinstance(base,bool) or base<0 or base&3:
        raise RuntimeError('invalid order1x_axi physical address')
    accelerator=Order1xAccelerator(MMIO(base,0x10000))
    accelerator.overlay=overlay  # Keep the single loaded overlay alive.
    accelerator.base=base
    return accelerator


def _reject_json_constant(value):
    raise ValueError('non-finite JSON number: '+value)


def _prepared_document(path,timeout):
    payload=Path(path).read_bytes()
    document=json.loads(payload.decode('utf-8-sig'),parse_constant=_reject_json_constant)
    if not isinstance(document,dict):raise ValueError('prepared JSON must be an object')
    for name,want in (('schema_version',1),('core_abi_version',VERSION),('points_per_turn',64)):
        value=document.get(name)
        if isinstance(value,bool) or not isinstance(value,int) or value!=want:
            raise ValueError('unsupported prepared JSON '+name)
    words,avg,timeout=_validate_request(document.get('samples_raw'),document.get('avg_log2'),timeout)
    # Also reject overflowed JSON exponents (e.g. 1e999) anywhere in metadata.
    json.dumps(document,allow_nan=False)
    return document,words,avg,timeout,hashlib.sha256(payload).hexdigest()


def _file_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xsa',default='order1x.xsa')
    parser.add_argument('--input',required=True,help='prepared JSON from order1x_fixed.py')
    parser.add_argument('--out',required=True,help='new result JSON; existing paths are rejected')
    parser.add_argument('--timeout',type=float,default=2.0)
    args=parser.parse_args(argv)
    target=Path(args.out)
    try:
        _validate_timeout(args.timeout)
        if target.exists():raise ValueError('--out already exists; choose a new JSON file')
        if not target.parent.is_dir():raise ValueError('--out parent directory must already exist')
        document,words,avg,timeout,prepared_sha256=_prepared_document(args.input,args.timeout)
        xsa=Path(args.xsa)
        accelerator_sha256=_file_sha256(xsa)
    except (ValueError,TypeError,OSError) as error:
        parser.error(str(error))
    try:
        accelerator=open_accelerator(xsa)
        result=accelerator.run(words,avg,timeout=timeout)
        result.update(accelerator_xsa_sha256=accelerator_sha256,
                      accelerator_xsa_path=str(xsa),
                      prepared_input_path=str(Path(args.input)),
                      prepared_input_sha256=prepared_sha256,
                      source_provenance={key:value for key,value in document.items() if key!='samples_raw'})
        with target.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    except KeyboardInterrupt:
        parser.exit(130,'ERROR: interrupted; in-flight accelerator block cleared\n')
    except (ValueError,OSError,RuntimeError,ImportError) as error:
        parser.exit(1,'ERROR: '+str(error)+'\n')
    print('FPGA result saved: {} words, amplitude {:.8g} g, phase {:.6g} deg (physical phase uncalibrated)'.format(
        result['accepted_samples'],result['amplitude_g'],result['phase_deg']))
    return 0


if __name__=='__main__':raise SystemExit(main())
