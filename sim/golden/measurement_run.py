"""One capture/review/compare entry; immutable raw evidence, no automatic retry.

Board capture uses the explicitly selected sealed peer and stop observer.
Review works on PC without PYNQ; historical context is never reconstructed
from the reviewing machine. Physical balancing approval remains external.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import uuid

from measurement_timing import StageTimes,TimedAccelerator
import repeatability

KIND='hengxin-measurement-summary-v1'
SETUP_FIELDS=('fixture_id','sensor_mount_id','cable_relief','environment','trial_position',
              'trial_mass_g','trial_radius_mm','power_setting')


def _json(path):
    def reject(value):raise ValueError('non-finite JSON value: '+value)
    value=json.loads(Path(path).read_text(encoding='utf-8-sig'),parse_constant=reject)
    json.dumps(value,allow_nan=False)
    return value


def _save(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,indent=2,allow_nan=False)
        stream.write('\n')


def observe_runtime(proc_root=Path('/proc')):
    result=dict(boot_id=None,uptime_seconds=None,read_errors=[],python_executable=sys.executable,
                python_version=platform.python_version(),platform=platform.platform())
    for name,rel in [('boot_id','sys/kernel/random/boot_id'),('uptime_seconds','uptime')]:
        try:
            text=(Path(proc_root)/rel).read_text(encoding='ascii').strip()
            if name=='boot_id':value=str(uuid.UUID(text))
            else:
                value=float(text.split()[0])
                if not math.isfinite(value) or value<0:raise ValueError('invalid uptime')
            result[name]=value
        except (OSError,ValueError,IndexError,UnicodeError) as error:
            result['read_errors'].append(name+': '+str(error))
    return result


def _setup(value):
    if value is None:value={}
    if not isinstance(value,dict) or set(value)-set(SETUP_FIELDS):
        raise ValueError('unknown setup fields or invalid setup object')
    result={name:value.get(name) for name in SETUP_FIELDS}
    for name,v in result.items():
        if v is None:continue
        if name in ('trial_mass_g','trial_radius_mm'):
            minimum_ok=v>=0 if name=='trial_mass_g' and type(v) in (int,float) else type(v) in (int,float) and v>0
            if type(v) not in (int,float) or not math.isfinite(v) or not minimum_ok:
                raise ValueError('setup '+name+' must be finite with valid mass/radius')
        elif not isinstance(v,str) or not v.strip():raise ValueError('setup '+name+' must be nonempty text')
    if result['trial_mass_g']==0 and result['trial_position']!='none':
        raise ValueError('zero trial mass requires explicit trial_position none')
    if result['trial_position']=='none' and result['trial_mass_g'] not in (None,0):
        raise ValueError('no-trial declaration contradicts nonzero trial mass')
    return result


def _target(out,capture=None):
    target=Path(out).resolve()
    if target.exists():raise ValueError('output already exists; choose a new directory')
    if capture is not None:
        raw=Path(capture).resolve()
        if raw==target or raw in target.parents:
            raise ValueError('output must be outside frozen capture directory')
    target.mkdir(parents=True,exist_ok=False)
    return target



def raw_statistics(directory):
    """One streaming diagnostic pass after strict preparation; not another estimator."""
    count=0;sums=[0,0,0];low=[None]*3;high=[None]*3
    with (Path(directory)/'samples.csv').open(encoding='utf-8-sig',newline='') as stream:
        rows=csv.reader(stream)
        if next(rows,None)!=['frame_id','ticks','x_raw','y_raw','z_raw']:
            raise ValueError('invalid sample columns for statistics')
        for row in rows:
            if len(row)!=5:raise ValueError('invalid sample row for statistics')
            axes=list(map(int,row[2:]))
            if any(not -(1<<19)<=v<(1<<19) for v in axes):raise ValueError('axis out of signed20 range')
            count+=1
            for i,v in enumerate(axes):
                sums[i]+=v
                low[i]=v if low[i] is None else min(low[i],v)
                high[i]=v if high[i] is None else max(high[i],v)
    if not count:raise ValueError('no samples for statistics')
    return dict(sample_count=count,dc_g={name:sums[i]/(count*256000.) for i,name in enumerate('xyz')},
                min_raw=dict(zip('xyz',low)),max_raw=dict(zip('xyz',high)),
                scope='all saved samples, separate from fixed angle windows; DC is not a calibrated tilt measurement')


def _identity():
    here=Path(__file__).resolve().parent
    return {name:repeatability.digest(here/name) for name in
            ('angle_antialias.py','order1x_fixed.py','prepare_tach_order1x.py','phase_free_balance.py')}


def _failure(out,error,timings):
    _save(out/'run.json',dict(status='FAILED',error_type=type(error).__name__,error=str(error),
          automatic_recapture=False,correction_allowed=False,timings=timings.snapshot()))


def _runtime(value,hashes):
    if value is None:return None
    if isinstance(value,(str,Path)):value=_json(value)
    if not isinstance(value,dict) or value.get('kind')!='hengxin-runtime-context-v1' or value.get('source_sha256')!=hashes:
        raise ValueError('runtime context binding mismatch')
    return value


def review(directory,*,out,runtime=None,avg_log2=7,compute_xsa=None,accelerator_factory=None,progress=None):
    repeatability.protocol_starts(avg_log2)
    target=_target(out,directory)
    timings=StageTimes()
    try:
        def factory(xsa):
            if accelerator_factory is not None:acc=accelerator_factory(xsa)
            else:
                board=Path(__file__).resolve().parents[2]/'board/bringup'
                if board.is_dir():sys.path.append(str(board))
                from order1x_accel import open_accelerator
                acc=open_accelerator(xsa)
            return TimedAccelerator(acc,timings.fpga_windows.append)
        result=repeatability.review_capture(directory,avg_log2=avg_log2,
                    out=target/'fpga_evidence' if compute_xsa is not None else None,
                    xsa=compute_xsa,progress=progress,timing=timings.call,
                    accelerator_factory=factory if compute_xsa is not None else None)
        context=_runtime(runtime,result['source_sha256'])
        diagnostics=timings.call('raw_statistics',raw_statistics,directory)
        meta=repeatability.read_json(Path(directory)/'capture.json')
        if repeatability.check_stop(directory,meta['sample_count'],meta['epoch'],meta['xsa_sha256'])!=result['source_sha256']:
            raise ValueError('capture changed during statistics')
        values=result['amplitudes_g']
        report=dict(kind=KIND,data_integrity='PASS',protocol=dict(method='aa128-to64',axis='z',
              avg_log2=avg_log2,start_turns=list(repeatability.protocol_starts(avg_log2))),
              source_sha256=result['source_sha256'],analysis_identity=_identity(),measurement=result,
              absolute_sd_mg=statistics.stdev(values)*1000,
              raw_statistics=diagnostics,runtime_context=context,automatic_recapture=False,correction_allowed=False,
              physical_validation_complete=False,timings=timings.snapshot())
        _save(target/'summary.json',report)
        _save(target/'run.json',dict(status='COMPLETED',automatic_recapture=False,timings=timings.snapshot()))
        return report
    except BaseException as error:
        _failure(target,error,timings)
        raise


def capture(backend,*,out,seconds=40.,setup=None,allow_no_pulses=False,compute_xsa=None,progress=None):
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
        raise ValueError('seconds must be finite and positive')
    declared=_setup(setup)
    if compute_xsa is not None and repeatability.digest(compute_xsa)!=repeatability.COMPUTE_XSA:
        raise ValueError('unexpected compute XSA')
    target=_target(out)
    timings=StageTimes()
    context=dict(kind='hengxin-runtime-context-v1',before=observe_runtime(),after=None,
                 initialization_id=str(uuid.uuid4()),driver_sha256=backend.identities,
                 sensor_configuration=None,source_sha256={},source_declaration='fan-tach-2ppr',
                 declared_setup=declared,setup_verified_by_software=False,
                 observation='contemporaneous software observations plus operator declarations; no physical setup verification')
    try:
        if progress:progress('loading_capture',0,1)
        timings.call('capture_overlay_load',backend.open)
        if progress:progress('capturing',0,1)
        metadata=timings.call('capture_initialize_acquire_drain_save',backend.acquire,seconds,target/'raw',
                            allow_no_pulses=allow_no_pulses,timings=timings)
        snapshot=timings.call('stop_snapshot',backend.stopped_snapshot,target/'raw')
        _save(target/'raw/stop_snapshot.json',snapshot)
        context['source_sha256']={name:repeatability.digest(target/'raw'/name) for name in
                                  ('samples.csv','pulses.csv','capture.json','stop_snapshot.json')}
        context['sensor_configuration']=backend.sensor_configuration
        context['after']=observe_runtime()
        _save(target/'runtime.json',context)
        if metadata!=repeatability.read_json(target/'raw/capture.json'):
            raise ValueError('returned capture metadata differs from saved metadata')
        if metadata.get('completed') is not True or snapshot.get('verdict')!='PASS':
            raise RuntimeError('capture or stopped evidence failed; raw data retained; no retry')
        if metadata.get('status')=='STATIC_ONLY':
            from sync_csv import load_capture
            timings.call('static_validation',load_capture,target/'raw',allow_static=True)
            repeatability.check_stop(target/'raw',metadata['sample_count'],metadata['epoch'],metadata['xsa_sha256'])
            report=dict(kind=KIND,data_integrity='PASS',measurement=None,runtime_context=context,
                        source_sha256=context['source_sha256'],status='STATIC_ONLY',
                        correction_allowed=False,physical_validation_complete=False,automatic_recapture=False)
        else:
            report=timings.call('analysis_total',review,target/'raw',out=target/'analysis',runtime=context,
                                compute_xsa=compute_xsa,progress=progress)
        _save(target/'summary.json',report)
        _save(target/'run.json',dict(status='COMPLETED',timings=timings.snapshot(),automatic_recapture=False))
        return report
    except BaseException as error:
        if not (target/'runtime.json').exists():
            context['after']=observe_runtime()
            context['sensor_configuration']=backend.sensor_configuration
            _save(target/'runtime.json',context)
        _failure(target,error,timings)
        raise


def compare(first,second,*,rpm_relative_tolerance=None):
    a,b=[_json(x) if isinstance(x,(str,Path)) else x for x in (first,second)]
    for r in (a,b):
        if not isinstance(r,dict) or r.get('kind')!=KIND or r.get('data_integrity')!='PASS' or not isinstance(r.get('measurement'),dict):
            raise ValueError('need completed rotational measurement summaries')
    if a['source_sha256']['samples.csv']==b['source_sha256']['samples.csv']:
        raise ValueError('same capture cannot represent independent runs')
    changed=[];unknown=[]
    if a.get('protocol')!=b.get('protocol'):changed.append('analysis_protocol_changed')
    if not a.get('analysis_identity') or not b.get('analysis_identity'):unknown.append('analysis_identity_missing')
    elif a['analysis_identity']!=b['analysis_identity']:changed.append('analysis_implementation_changed')
    summaries=[]
    for r in (a,b):
        m=r['measurement'];p=r['protocol']
        summaries.append(repeatability.summarize(m['amplitudes_g'],m['rpm_median'],axis=p['axis'],avg_log2=p['avg_log2']))
    x,y=summaries
    rpm_delta=abs(x['rpm_median']-y['rpm_median'])/((x['rpm_median']+y['rpm_median'])/2)
    if rpm_relative_tolerance is None:unknown.append('rpm_tolerance_missing')
    elif type(rpm_relative_tolerance) not in (int,float) or not math.isfinite(rpm_relative_tolerance) or rpm_relative_tolerance<0:
        raise ValueError('RPM relative tolerance must be finite and nonnegative')
    elif rpm_delta>rpm_relative_tolerance:changed.append('rpm_outside_declared_tolerance')
    contexts=[_runtime(r.get('runtime_context'),r['source_sha256']) for r in (a,b)]
    if any(c is None for c in contexts):unknown.append('runtime_context_missing')
    else:
        ca,cb=contexts
        boots=[]
        for c in contexts:
            before=c.get('before') or {};after=c.get('after') or {}
            try:
                boot_before=str(uuid.UUID(before.get('boot_id','')))
                boot_after=str(uuid.UUID(after.get('boot_id','')))
            except (ValueError,TypeError,AttributeError):
                boot_before=boot_after=None
                unknown.append('boot_id_missing')
            if boot_before is not None and boot_before!=boot_after:changed.append('boot_id_changed_within_run')
            boots.append(boot_before)
            if not c.get('initialization_id'):unknown.append('initialization_identity_missing')
        if all(boots) and boots[0]!=boots[1]:changed.append('boot_id_changed')
        configs=[c.get('sensor_configuration') for c in contexts]
        def complete_config(v):
            if not isinstance(v,dict) or v.get('verified') is not True:return False
            registers=v.get('registers')
            return isinstance(registers,dict) and all(type(registers.get(k)) is int and 0<=registers[k]<=255
                    for k in ('DEVID','FILTER','RANGE','POWER_CTL'))
        if not all(complete_config(v) for v in configs):
            unknown.append('sensor_configuration_missing')
        elif configs[0]['registers']!=configs[1]['registers']:changed.append('sensor_configuration_changed')
        for name in ('driver_sha256','source_declaration'):
            values=[c.get(name) for c in contexts]
            if not all(values):unknown.append(name+'_missing')
            elif values[0]!=values[1]:changed.append(name+'_changed')
        no_trial=all((c.get('declared_setup') or {}).get('trial_position')=='none' and
                     (c.get('declared_setup') or {}).get('trial_mass_g')==0 for c in contexts)
        for name in SETUP_FIELDS:
            if name=='trial_radius_mm' and no_trial:continue
            values=[(c.get('declared_setup') or {}).get(name) for c in contexts]
            if any(v is None for v in values):unknown.append('setup_'+name+'_missing')
            elif values[0]!=values[1]:changed.append('setup_'+name+'_changed')
    return dict(kind='hengxin-measurement-comparison-v1',
                purpose='same-state, same-boot comparison of recorded conditions and operator declarations',
                comparability='NOT_COMPARABLE' if changed else ('UNKNOWN' if unknown else 'DECLARED_CONDITIONS_MATCH'),
                reasons=list(dict.fromkeys(changed+unknown)),
                mean_difference_mg=y['mean_peak_mg']-x['mean_peak_mg'],
                first_mean_mg=x['mean_peak_mg'],second_mean_mg=y['mean_peak_mg'],
                first_cv=x['cv'],second_cv=y['cv'],rpm_relative_difference=rpm_delta,
                rpm_relative_tolerance=rpm_relative_tolerance,automatic_recapture=False,
                correction_allowed=False,physical_validation_complete=False,
                scope='No causal attribution, accuracy certification or balancing approval. CV is descriptive only; different initialization IDs are recorded and do not alone reject equal effective configuration.')


def _progress(stage,done,total):
    if stage=='loading_capture':print('Loading sealed capture overlay...',flush=True)
    elif stage=='capturing':print('Capture running; raw data and stop evidence will be saved before analysis...',flush=True)
    else:repeatability._print_progress(stage,done,total)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    one=sub.add_parser('review');one.add_argument('capture');one.add_argument('--runtime')
    one.add_argument('--avg-log2',type=int,choices=(6,7),default=7)
    cap=sub.add_parser('capture');cap.add_argument('--peer-directory',required=True)
    cap.add_argument('--evidence-directory',required=True);cap.add_argument('--xsa',required=True)
    cap.add_argument('--seconds',type=float,default=40.);cap.add_argument('--setup')
    cap.add_argument('--allow-no-pulses',action='store_true')
    pair=sub.add_parser('compare');pair.add_argument('first');pair.add_argument('second')
    pair.add_argument('--rpm-relative-tolerance',type=float)
    for p in (one,cap):p.add_argument('--compute-xsa')
    for p in (one,cap,pair):p.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='review':
            result=review(args.capture,out=args.out,runtime=args.runtime,avg_log2=args.avg_log2,
                          compute_xsa=args.compute_xsa,progress=_progress)
        elif args.command=='capture':
            if Path(args.out).exists():raise ValueError('output already exists')
            from measurement_capture import SealedCapture
            backend=SealedCapture(args.peer_directory,args.evidence_directory,args.xsa)
            result=capture(backend,out=args.out,seconds=args.seconds,
                           setup=_json(args.setup) if args.setup else None,
                           allow_no_pulses=args.allow_no_pulses,compute_xsa=args.compute_xsa,progress=_progress)
        else:
            if Path(args.out).exists():raise ValueError('output already exists')
            result=compare(args.first,args.second,rpm_relative_tolerance=args.rpm_relative_tolerance)
            _save(args.out,result)
        if result.get('measurement'):
            m=result['measurement']
            print('1x Z peak %.6f mg; SD %.6f mg; CV %.2f%%; RPM %.2f; %s'%(
                m['mean_peak_mg'],result['absolute_sd_mg'],100*m['cv'],m['rpm_median'],m['amplitude_source']))
            print('Runtime context: '+('bound observations/declarations' if result['runtime_context'] else 'unknown (historical record)'))
        elif args.command=='compare':
            print('Comparability: '+result['comparability']+'; reasons: '+', '.join(result['reasons']))
        else:print('Static capture saved; no rotational result.')
        print('No automatic recapture; physical correction remains unapproved.')
        return 0
    except KeyboardInterrupt:parser.exit(130,'Interrupted; retained evidence/run status; no retry.\n')
    except (OSError,ValueError,RuntimeError,ImportError,KeyError,TypeError) as error:
        parser.exit(1,'ERROR: '+str(error)+'; inspect the run directory if created. Board Python: /usr/local/share/pynq-venv/bin/python3\n')


if __name__=='__main__':raise SystemExit(main())
