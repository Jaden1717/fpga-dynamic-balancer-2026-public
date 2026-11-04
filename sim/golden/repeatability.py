"""Eight fixed 64- or 128-turn AA windows; screening, never balance approval.

Raw capture must already be stopped by the sealed HXS1 evidence wrapper.
Optional --xsa replaces capture with HX11, runs all eight windows and verifies
same-input arithmetic. Without --xsa the report explicitly contains CPU data.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from numbers import Real
from prepare_tach_order1x import prepare_many
from sync_csv import load_capture
from verify_order1x import verify

CAPTURE_XSA='eca1b6e2c84d76b3f1ceabf1e30f4cbe0625e7c9551d2d977f8bfd12633dd08a'
COMPUTE_XSA='980107bfd3775bdf060086d69834d3c7e17c0725f332a069fcccae734463b346'
STARTS=tuple(range(0,512,64))


def protocol_starts(avg_log2):
    if type(avg_log2) is not int or avg_log2 not in (6,7):
        raise ValueError('protocol avg_log2 must be 6 (64 turns) or 7 (128 turns)')
    turns=1<<avg_log2
    return tuple(range(0,8*turns,turns))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,indent=2,allow_nan=False)
        stream.write('\n')


def check_stop(directory, sample_count, epoch, xsa_sha256):
    directory=Path(directory)
    stop=read_json(directory/'stop_snapshot.json')
    if (stop.get('format')!='hengxin-hxs1-stop-snapshot-v1' or stop.get('read_only') is not True
            or stop.get('verdict')!='PASS' or stop.get('xsa_sha256')!=xsa_sha256):
        raise ValueError('invalid stop evidence identity/verdict')
    observations=stop.get('observations')
    if not isinstance(observations,list) or len(observations)!=2:
        raise ValueError('stop observations not stable')
    frozen=[]
    for observation in observations:
        if not isinstance(observation,dict):raise ValueError('invalid stop observation')
        status=observation.get('acq_status')
        if type(status) is not int or not 0<=status<2**32 or status&1:
            raise ValueError('acquisition busy or invalid status at stop')
        # ABI STATUS bit2 is the live sensor DRDY pin, not a frozen counter.
        # Ignore only that bit when comparing; retain all other fields/bits.
        frozen.append(dict(observation,acq_status=status&~4))
    if frozen[0]!=frozen[1]:raise ValueError('stop observations not stable')
    obs=observations[0]
    wanted=dict(magic=0x48585331,abi_version=65536,sync_ctrl=0,acq_ctrl=0,source_idle=1,
                sample_pending=0,pulse_pending=0,sample_drops=0,pulse_drops=0,source_missed=0,
                sample_total=sample_count,epoch=epoch)
    if any(type(obs.get(k)) is not int or obs[k]!=v for k,v in wanted.items()):
        raise ValueError('stop state/count/epoch mismatch')
    if type(obs.get('sync_status')) is not int or obs['sync_status']&0x19:
        raise ValueError('stop busy/overflow status')
    hashes=stop.get('file_sha256')
    if not isinstance(hashes,dict):raise ValueError('missing stop file hashes')
    actual={name:digest(directory/name) for name in ('samples.csv','pulses.csv','capture.json')}
    if any(hashes.get(name)!=value for name,value in actual.items()):
        raise ValueError('stop file hash mismatch')
    return dict(actual,**{'stop_snapshot.json':digest(directory/'stop_snapshot.json')})


def summarize(amplitudes_g, rpm_median, *, axis='z', avg_log2=6):
    starts=protocol_starts(avg_log2)
    values=list(amplitudes_g)
    if axis not in ('x','y','z'):raise ValueError('invalid axis')
    if len(values)!=8:raise ValueError('exactly eight windows required')
    if any(isinstance(v,bool) or not isinstance(v,Real) or not math.isfinite(v) or v<=0 for v in values):
        raise ValueError('amplitudes must be finite positive numbers')
    if isinstance(rpm_median,bool) or not isinstance(rpm_median,Real) or not math.isfinite(rpm_median) or rpm_median<=0:
        raise ValueError('RPM must be finite positive')
    mean=statistics.mean(values)
    cv=statistics.stdev(values)/mean
    return dict(axis=axis,method='aa128-to64',start_turns=list(starts),turns_per_window=1<<avg_log2,
                amplitudes_g=values,mean_peak_mg=mean*1000,cv=cv,rpm_median=rpm_median,
                within_record_repeatability_pass=cv<=.10,
                criteria=dict(cv_limit=.10,mean_relative_difference_limit=.10,rpm_relative_difference_limit=.01,
                              note='Stage engineering screens, not confidence bounds or instrument specifications'),
                correction_allowed=False,physical_validation_complete=False)


def compare_summaries(first,second):
    if first['axis']!=second['axis']:raise ValueError('axis mismatch')
    turns=first.get('turns_per_window')
    if type(turns) is not int or turns not in (64,128):raise ValueError('invalid window length')
    avg=turns.bit_length()-1
    for item in (first,second):
        if item.get('method')!='aa128-to64' or item.get('start_turns')!=list(protocol_starts(avg)) or item.get('turns_per_window')!=turns:
            raise ValueError('incompatible windows or method')
    # Recompute rather than trust caller-supplied CV/pass fields.
    a,b=[summarize(r['amplitudes_g'],r['rpm_median'],axis=r['axis'],avg_log2=avg) for r in (first,second)]
    delta=abs(a['mean_peak_mg']-b['mean_peak_mg'])/((a['mean_peak_mg']+b['mean_peak_mg'])/2)
    rpm_delta=abs(a['rpm_median']-b['rpm_median'])/((a['rpm_median']+b['rpm_median'])/2)
    reasons=[]
    if not a['within_record_repeatability_pass'] or not b['within_record_repeatability_pass']:reasons.append('window_cv_exceeds_limit')
    if delta>.10:reasons.append('between_run_mean_difference_exceeds_limit')
    if rpm_delta>.01:reasons.append('between_run_rpm_difference_exceeds_limit')
    return dict(repeatability_pass=not reasons,reasons=reasons,mean_relative_difference=delta,
                rpm_relative_difference=rpm_delta,first=a,second=b,correction_allowed=False,
                physical_validation_complete=False,
                scope='Two operator-declared unchanged A states and independent starts; no proof of causal reduction or calibrated correction')


def prepare_record(directory, *, avg_log2=6, progress=None):
    starts=protocol_starts(avg_log2)
    directory=Path(directory)
    meta=read_json(directory/'capture.json')
    if not isinstance(meta,dict):raise ValueError('invalid capture metadata')
    if meta.get('xsa_sha256')!=CAPTURE_XSA:raise ValueError('unexpected capture XSA')
    hashes=check_stop(directory,meta['sample_count'],meta['epoch'],meta['xsa_sha256'])
    prepared=prepare_many(directory,source='fan-tach-2ppr',axis='z',avg_log2=avg_log2,
                          start_turns=starts,resampling='aa128-to64',progress=progress)
    if any(p['source_sha256']!=hashes for p in prepared):raise ValueError('capture changed during preparation')
    if check_stop(directory,meta['sample_count'],meta['epoch'],meta['xsa_sha256'])!=hashes:
        raise ValueError('capture changed during preparation')
    return prepared,hashes


def review_capture(directory, *, out=None, xsa=None, avg_log2=6, progress=None, timing=None, accelerator_factory=None):
    def call(stage, operation, *args, **kwargs):
        return operation(*args, **kwargs) if timing is None else timing(stage, operation, *args, **kwargs)
    starts=protocol_starts(avg_log2)
    if out is not None and Path(out).exists():raise ValueError('output already exists')
    if xsa is not None and digest(xsa)!=COMPUTE_XSA:raise ValueError('unexpected compute XSA')
    prepared,hashes=call("preparation",prepare_record,directory,avg_log2=avg_log2,progress=progress)
    results=[]
    verified_amplitudes=[]
    if xsa is not None and out is None:raise ValueError('hardware run requires an evidence output directory')
    if out is not None:
        out=Path(out);out.mkdir(parents=True,exist_ok=False)
        for start,p in zip(starts,prepared):call('input_evidence_write',save_json,out/('input_%d.json'%start),p)
    if xsa is not None:
        # Local package includes driver; source-tree invocation resolves board driver.
        board=Path(__file__).resolve().parents[2]/'board/bringup'
        if board.is_dir():sys.path.append(str(board))
        from order1x_accel import open_accelerator
        if progress:progress("loading_compute",0,len(starts))
        accelerator=call("compute_overlay_load",accelerator_factory or open_accelerator,xsa)
        for start,p in zip(starts,prepared):
            input_path=out/('input_%d.json'%start)
            result=call('fpga_window',accelerator.run,p['samples_raw'],p['avg_log2'])
            result.update(accelerator_xsa_sha256=COMPUTE_XSA,accelerator_xsa_path=str(xsa),
                          prepared_input_path=str(input_path),prepared_input_sha256=digest(input_path),
                          source_provenance={k:v for k,v in p.items() if k!='samples_raw'})
            result_path=out/('result_%d.json'%start)
            save_json(result_path,result)
            verification=call('same_input_verification',verify,input_path,result_path)
            save_json(out/('verification_%d.json'%start),verification)
            results.append(result)
            verified_amplitudes.append(verification['amplitude_g'])
            if progress:progress("verified",len(results),len(starts))
    amplitudes=verified_amplitudes if results else [p['reference']['amplitude_g'] for p in prepared]
    report=summarize(amplitudes,prepared[0]['tach_quality']['rpm_median'],avg_log2=avg_log2)
    report.update(source_capture_directory=str(Path(directory).resolve()),source_sha256=hashes,
                  amplitude_source='FPGA verified same-input' if results else 'CPU reference (not new FPGA evidence)',
                  hardware_windows_verified=len(results),
                  windows=[dict(start_turn=s,reference_amplitude_g=p['reference']['amplitude_g'],
                                start_tick=p['window']['start_tick'],end_tick=p['window']['end_tick']) for s,p in zip(starts,prepared)])
    if out is not None:
        save_json(out/'repeatability.json',report)
        paths=sorted(out.iterdir())
        with (out/'sha256sums.txt').open('x',encoding='ascii',newline='\n') as stream:
            for path in paths:stream.write(digest(path)+'  '+path.name+'\n')
    return report


def compare_captures(first,second, *, avg_log2=6):
    if digest(Path(first)/'samples.csv')==digest(Path(second)/'samples.csv'):
        raise ValueError('same capture cannot represent two independent starts')
    a,b=[review_capture(p,avg_log2=avg_log2) for p in (first,second)]
    report=compare_summaries(a,b)
    report.update(first_source=a['source_sha256'],second_source=b['source_sha256'],
                  amplitude_source='CPU references; pair not independently FPGA verified')
    return report


def _print_progress(stage,done,total):
    if stage=='loading':message='Loading and validating raw capture (one CSV parse)...'
    elif stage=='prepared':message='Prepared %d/%d angle windows'%(done,total)
    elif stage=='loading_compute':message='Loading HX11 compute overlay...'
    elif stage=='verified':message='FPGA same-input verification %d/%d'%(done,total)
    else:message='%s %d/%d'%(stage,done,total)
    print(message,flush=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    one=sub.add_parser('review');one.add_argument('capture');one.add_argument('--out',required=True);one.add_argument('--xsa')
    pair=sub.add_parser('compare');pair.add_argument('first');pair.add_argument('second');pair.add_argument('--out',required=True)
    for command in (one,pair):command.add_argument('--avg-log2',type=int,choices=[6,7],default=6,help='6:8x64 legacy protocol; 7:8x128 new protocol; choose before a fresh capture')
    args=parser.parse_args(argv)
    try:
        if Path(args.out).exists():raise ValueError('output already exists')
        if args.command=='review':r=review_capture(args.capture,out=args.out,xsa=args.xsa,avg_log2=args.avg_log2,progress=_print_progress)
        else:
            r=compare_captures(args.first,args.second,avg_log2=args.avg_log2);save_json(args.out,r)
    except (OSError,ValueError,RuntimeError,ImportError,KeyError,TypeError) as error:
        parser.exit(1,'ERROR: '+str(error)+'\n')
    if args.command=='review':
        print('8x%d turns: mean %.6f mg peak, CV %.2f%%, RPM %.2f, provisional CV reference: %s; verified FPGA windows=%d'%(
            r['turns_per_window'],r['mean_peak_mg'],100*r['cv'],r['rpm_median'],'at/below10%' if r['within_record_repeatability_pass'] else 'above10%',r['hardware_windows_verified']))
    else:print('Two-run provisional comparison %s: mean difference %.2f%%, RPM difference %.2f%%'%('inside reference screens' if r['repeatability_pass'] else 'outside reference screens',100*r['mean_relative_difference'],100*r['rpm_relative_difference']))
    if args.command=='review':print('Amplitude source: '+r['amplitude_source'])
    print('Provisional CV screen only; no automatic recapture or fitness verdict from CV alone. correction_allowed=false; see JSON.')
    return 0


if __name__=='__main__':raise SystemExit(main())