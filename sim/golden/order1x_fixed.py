"""64-point FPGA 1x contract: exact Q16 DFT and ideal polar reference.

This adapter performs time-to-angle interpolation on the PC/PS. It does not
claim that the new core consumes raw timestamp samples directly. The CORDIC
RTL is checked against the independent ideal magnitude/atan2 reference.
"""
import argparse
import cmath
import json
import math
from numbers import Integral
from pathlib import Path
import numpy as np
from sync_csv import load_capture
from timebase import clock_times
from sync_order import to_angle_domain

SCALE_G=256000*256
POINTS=64

def _average(value):
    if isinstance(value,(bool,np.bool_)) or not isinstance(value,Integral) or not 0<=value<=8:
        raise ValueError('avg_log2 must be an integer in 0..8')
    return int(value)

def _words(samples,avg_log2):
    avg=_average(avg_log2)
    words=list(samples)
    if len(words)!=64*(1<<avg):
        raise ValueError('exactly 64 * 2**avg_log2 samples required')
    if any(isinstance(v,(bool,np.bool_)) or not isinstance(v,Integral) or not -(1<<19)<=v<(1<<19) for v in words):
        raise ValueError('input must contain signed20 integer raw counts')
    return [int(v) for v in words],avg

def accumulate(samples,avg_log2=0):
    """Bit-exact coefficient expected from Q16 twiddles, signed ties-away."""
    words,avg=_words(samples,avg_log2)
    # Generate mathematical constants independently of the RTL quarter-wave case.
    cosines=[round(65536*math.cos(2*math.pi*k/64)) for k in range(64)]
    negativesines=[-round(65536*math.sin(2*math.pi*k/64)) for k in range(64)]
    sums=[sum(v*coefs[k%64] for k,v in enumerate(words)) for coefs in (cosines,negativesines)]
    shift=13+avg
    def normalize(value):
        magnitude=(abs(value)+(1<<(shift-1)))>>shift
        return -magnitude if value<0 else magnitude
    return dict(real_q8=normalize(sums[0]),imag_q8=normalize(sums[1]))

def reference_result(samples,avg_log2=0):
    """Exact integer DFT, then independent ideal polar conversion (not bit CORDIC)."""
    result=accumulate(samples,avg_log2)
    coefficient=complex(result['real_q8'],result['imag_q8'])
    magnitude=int(math.floor(abs(coefficient)+.5))
    phase=cmath.phase(coefficient)
    code=int(round(phase*(1<<32)/(2*math.pi)))
    code=((code+(1<<31))%(1<<32))-(1<<31)
    result.update(magnitude_q8=magnitude,phase_code=code,
                  amplitude_g=abs(coefficient)/SCALE_G,
                  phase_deg=code*360.0/(1<<32),phase_defined=bool(coefficient),
                  amplitude_semantics='coherent peak 1x acceleration, not RMS or peak-to-peak')
    return result

def prepare_capture(directory,axis='x',avg_log2=None):
    """Strict capture -> complete revolutions ->64-angle grid ->signed20 words.

    Auto mode uses the first largest power-of-two number of available turns,
    up to256. Remainder turns are explicitly reported, never silently averaged.
    Interpolated raw counts are rounded nearest with ties away from zero.
    """
    if axis not in ('x','y','z'):raise ValueError('axis must be x, y or z')
    capture=load_capture(directory)
    rows,pulses,meta=capture['samples'],capture['pulses'],capture['metadata']
    t,p=clock_times([r[1] for r in rows],[r[1] for r in pulses],meta['clock_hz'])
    selected=p[(p>=t[0])&(p<=t[-1])]
    available=len(selected)-1
    if available<1:raise ValueError('no complete in-record revolution')
    avg=min(8,int(available).bit_length()-1) if avg_log2 is None else _average(avg_log2)
    turns=1<<avg
    if turns>available:raise ValueError('requested averaging window exceeds available complete revolutions')
    axis_index={'x':2,'y':3,'z':4}[axis]
    raw=np.asarray([r[axis_index] for r in rows],dtype=float)
    angular=to_angle_domain(t,raw,selected[:turns+1],pts_per_rev=64)
    rounded=np.where(angular>=0,np.floor(angular+.5),np.ceil(angular-.5))
    words=[int(v) for v in rounded]
    _words(words,avg)
    return dict(schema_version=1,core_abi_version=0x10000,points_per_turn=64,
                avg_log2=avg,used_turns=turns,available_turns=available,
                unused_turns=available-turns,axis=axis,samples_raw=words,
                source_capture_directory=str(Path(directory)),
                source_capture_xsa_sha256=meta['xsa_sha256'],
                source_capture_kind=meta.get('source_kind','BOARD_CAPTURE'),
                resampling_location='Python/PS, linear interpolation on shared-clock optical angle',
                input_quantization='signed20 raw integer; nearest, ties away from zero',
                reference=reference_result(words,avg),physical_phase_calibrated=False)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('--axis',choices=('x','y','z'),default='x')
    parser.add_argument('--avg-log2',type=int)
    parser.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    target=Path(args.out)
    if target.exists():parser.error('--out already exists; choose a new JSON file')
    try:
        prepared=prepare_capture(args.capture,args.axis,args.avg_log2)
        with target.open('x',encoding='utf-8') as stream:
            json.dump(prepared,stream,indent=2,allow_nan=False);stream.write('\n')
    except (ValueError,OSError) as error:parser.exit(1,'ERROR: '+str(error)+'\n')
    print('Prepared {} signed20 words / {} turns for FPGA; interpolation remains Python/PS'.format(len(prepared['samples_raw']),prepared['used_turns']))
    return 0

if __name__=='__main__':raise SystemExit(main())
