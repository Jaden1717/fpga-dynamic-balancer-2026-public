"""Prepare explicit 2-PPR TACH inputs, parsing each frozen capture once per batch.

Default legacy64 remains unchanged. Batch lifetime is one function call; no
persistent/global capture cache. Hashes are checked before and after the batch.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from order1x_fixed import _average, _words, reference_result
from sync_csv import load_capture
from sync_order import to_angle_domain
from timebase import clock_times
from tach_review import _review_loaded, _validate_screening


def _source_hashes(directory):
    names=['capture.json','samples.csv','pulses.csv']
    if (directory/'stop_snapshot.json').exists():names.append('stop_snapshot.json')
    return {name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in names}


def prepare_many(directory, *, source, start_turns, axis='z', avg_log2=6,
                 parity=0, rpm_min=1000, rpm_max=2000, resampling='legacy64', progress=None):
    if resampling not in ('legacy64','aa128-to64'):raise ValueError('unknown resampling method')
    if axis not in ('x','y','z'):raise ValueError('axis must be x, y or z')
    if type(parity) is not int or parity not in (0,1):raise ValueError('parity must be 0 or 1')
    starts=list(start_turns)
    if not starts or any(type(s) is not int or s<0 for s in starts) or len(set(starts))!=len(starts):
        raise ValueError('start turns must be distinct nonnegative integers')
    avg=_average(avg_log2)
    _validate_screening(source,rpm_min,rpm_max)
    directory=Path(directory)
    if progress:progress('loading',0,len(starts))
    hashes=_source_hashes(directory)
    capture=load_capture(directory)
    quality=_review_loaded(directory,capture,source=source,rpm_min=rpm_min,rpm_max=rpm_max)
    if any(hashes.get(k)!=v for k,v in quality['source_sha256'].items()):raise ValueError('capture changed during loading')
    if quality['status']!='REVIEW_REQUIRED':
        raise ValueError('TACH screening failed: '+quality['status']+' '+str(quality['flags']))
    rows,pulses,meta=capture['samples'],capture['pulses'],capture['metadata']
    axes={}
    for j,name in enumerate(('x','y','z'),2):
        values=np.asarray([r[j] for r in rows],dtype=np.int64)
        axes[name]=dict(min_raw=int(values.min()),max_raw=int(values.max()),
                       near_rail_count=int(np.count_nonzero(np.abs(values)>=.98*524287)))
    if any(a['near_rail_count'] for a in axes.values()):raise ValueError('near/full rail samples prohibit this preparation')
    t,p=clock_times([r[1] for r in rows],[r[1] for r in pulses],meta['clock_hz'])
    indices=np.arange(parity,len(pulses),2)
    indices=indices[(p[indices]>=t[0])&(p[indices]<=t[-1])]
    available=len(indices)-1;turns=1<<avg
    if any(s+turns>available for s in starts):raise ValueError('requested window exceeds complete revolutions')
    raw=np.asarray([r[{'x':2,'y':3,'z':4}[axis]] for r in rows],dtype=float)
    results=[]
    for done,start_turn in enumerate(starts,1):
        selected=indices[start_turn:start_turn+turns+1]
        if resampling=='aa128-to64':
            from angle_antialias import resample_antialiased
            angular=resample_antialiased(t,raw,p[selected])
        else:angular=to_angle_domain(t,raw,p[selected],pts_per_rev=64)
        rounded=np.where(angular>=0,np.floor(angular+.5),np.ceil(angular-.5))
        words=[int(v) for v in rounded];_words(words,avg)
        grid=np.arange(len(words))%64
        floating=2*np.mean(np.asarray(words)*np.exp(-2j*np.pi*grid/64))/256000.
        first,last=int(selected[0]),int(selected[-1])
        result=dict(schema_version=1,core_abi_version=0x10000,points_per_turn=64,
            axis=axis,avg_log2=avg,used_turns=turns,available_turns=available,
            unused_turns=available-turns,samples_raw=words,
            source_capture_directory=str(directory),source_capture_kind='FAN_TACH_2PPR',
            source_capture_xsa_sha256=meta['xsa_sha256'],source_sha256=dict(hashes),
            source_declaration_verified_by_software=False,
            recorded_pulses_per_revolution=meta['pulses_per_revolution'],
            interpreted_pulses_per_revolution=2,parity=parity,
            window=dict(start_turn=start_turn,start_pulse_count=pulses[first][0],
                        end_pulse_count=pulses[last][0],start_tick=pulses[first][1],
                        end_tick=pulses[last][1],start_s=float(p[first]),end_s=float(p[last])),
            reference=reference_result(words,avg),
            floating_same_words=dict(real_g=float(floating.real),imag_g=float(floating.imag),amplitude_g=float(abs(floating))),
            axes_quality=axes,tach_quality=quality,
            resampling_location='Python/PS; linear interpolation within every pair of same-polarity TACH intervals',
            input_quantization='signed20 raw integer; nearest, ties away from zero',
            phase_reference='Relative to recorded selected TACH edge; unknown rotor zero and restart ambiguity',
            absolute_phase_available=False,physical_phase_calibrated=False,physical_validation_complete=False,
            estimator_note='Coherent angle-domain DFT; do not compare against an independently windowed 80-turn least-squares result as a hardware error test')
        if resampling=='aa128-to64':
            result['preprocessing']=dict(method=resampling,location='Python/PS',dense_points_per_turn=128,
                output_points_per_turn=64,passband_max_order=24,stopband_min_order=32,transition='raised cosine',
                filter='whole-window FFT zero-phase low-pass',boundary='selected complete-turn block treated as periodic',
                limitation='prevents 128-to-64 folding only; cannot undo prior acquisition/interpolation aliasing')
            result['resampling_location']='Python/PS; linear 128-point angular interpolation, low-pass, decimate to 64'
        results.append(result)
        if progress:progress('prepared',done,len(starts))
    if _source_hashes(directory)!=hashes:raise ValueError('capture changed during preparation')
    return results


def prepare(directory, *, source, axis='z', avg_log2=6, parity=0,
            start_turn=0, rpm_min=1000, rpm_max=2000, resampling='legacy64'):
    return prepare_many(directory,source=source,axis=axis,avg_log2=avg_log2,parity=parity,
        start_turns=[start_turn],rpm_min=rpm_min,rpm_max=rpm_max,resampling=resampling)[0]

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture')
    p.add_argument('--source',choices=['fan-tach-2ppr'],required=True)
    p.add_argument('--axis',choices=['x','y','z'],default='z')
    p.add_argument('--avg-log2',type=int,default=6)
    p.add_argument('--parity',type=int,choices=[0,1],default=0)
    p.add_argument('--start-turn',type=int,default=0)
    p.add_argument('--rpm-min',type=float,default=1000)
    p.add_argument('--rpm-max',type=float,default=2000)
    p.add_argument('--resampling',choices=['legacy64','aa128-to64'],default='legacy64')
    p.add_argument('--out',required=True)
    args=p.parse_args(argv)
    if Path(args.out).exists():p.error('--out already exists')
    try:
        result=prepare(args.capture,source=args.source,axis=args.axis,
                       avg_log2=args.avg_log2,parity=args.parity,start_turn=args.start_turn,
                       rpm_min=args.rpm_min,rpm_max=args.rpm_max,resampling=args.resampling)
        with Path(args.out).open('x',encoding='utf-8') as f:
            json.dump(result,f,indent=2,allow_nan=False)
    except (ValueError,OSError) as error:p.exit(1,str(error)+'\n')
    print('Prepared {} words / {} turns; physical validation pending'.format(len(result['samples_raw']),result['used_turns']))
    return 0


if __name__=='__main__':raise SystemExit(main())
