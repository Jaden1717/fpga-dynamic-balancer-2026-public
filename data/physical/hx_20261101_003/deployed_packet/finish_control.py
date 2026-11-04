"""Describe a bounded five-state physical batch; no solver or capture retry."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import uuid

NAMES=('01_reference_before','02_trial_000','03_trial_120','04_trial_240','05_reference_after')

def describe(root):
    root=Path(root);runs=[];missing=[];invalid=[];deviations=[]
    for name in NAMES:
        path=root/name/'summary.json'
        if not path.exists():missing.append(name);continue
        try:
            b=path.read_bytes();s=json.loads(b.decode('utf-8-sig'))
            if s.get('kind')!='hengxin-measurement-summary-v1' or s.get('data_integrity')!='PASS':raise ValueError('summary identity/integrity')
            want=dict(method='aa128-to64',axis='z',avg_log2=7,start_turns=list(range(0,1024,128)))
            if s.get('protocol')!=want:raise ValueError('fixed protocol mismatch')
            m=s['measurement'];a=m['amplitudes_g']
            if len(a)!=8 or any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in a):raise ValueError('invalid eight window amplitudes')
            mean=statistics.mean(a)*1000
            if not math.isclose(mean,m['mean_peak_mg'],rel_tol=1e-12,abs_tol=1e-12):raise ValueError('display mean differs from windows')
            rpm=m['rpm_median']
            if type(rpm) not in (int,float) or not math.isfinite(rpm) or rpm<=0:raise ValueError('invalid RPM')
            hashes=s['source_sha256'];c=s.get('runtime_context') or {}
            if c.get('source_sha256')!=hashes:raise ValueError('context source binding mismatch')
            if set(hashes)!={'samples.csv','pulses.csv','capture.json','stop_snapshot.json'}:raise ValueError('source hash set mismatch')
            boots=[]
            for side in ('before','after'):
                try:boots.append(str(uuid.UUID((c.get(side) or {}).get('boot_id',''))))
                except (ValueError,TypeError,AttributeError):boots.append(None)
            cfg=c.get('sensor_configuration') or {}
            effective=cfg.get('registers') if cfg.get('verified') is True else None
            runs.append(dict(name=name,mean_peak_mg=mean,within_record_sd_mg=statistics.stdev(a)*1000,within_record_cv=statistics.stdev(a)/statistics.mean(a),rpm_median=rpm,source_sha256=hashes,summary_sha256=hashlib.sha256(b).hexdigest(),before_boot_id=boots[0],after_boot_id=boots[1],initialization_id=c.get('initialization_id'),sensor_registers=effective,amplitude_source=m['amplitude_source'],hardware_windows_verified=m['hardware_windows_verified']))
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:
            invalid.append(dict(name=name,error=str(error)))
    allboots=[b for r in runs for b in (r['before_boot_id'],r['after_boot_id'])]
    sameboot=bool(allboots) and all(b is not None for b in allboots) and len(set(allboots))==1
    if runs and not sameboot:deviations.append('boot_changed_or_unknown')
    configs=[r['sensor_registers'] for r in runs]
    samecfg=bool(configs) and all(c==configs[0] and c is not None for c in configs)
    if runs and not samecfg:deviations.append('sensor_configuration_changed_or_unknown')
    ids=[r['source_sha256']['samples.csv'] for r in runs]
    if len(ids)!=len(set(ids)):deviations.append('duplicate_source_capture')
    byname={r['name']:r for r in runs}
    first=byname.get(NAMES[0]);last=byname.get(NAMES[-1])
    delta=last['mean_peak_mg']-first['mean_peak_mg'] if first and last else None
    status='INVALID' if invalid or 'duplicate_source_capture' in deviations else 'PARTIAL' if len(runs)<5 else 'COMPLETED_WITH_DEVIATIONS' if deviations else 'COMPLETED'
    return dict(kind='hengxin-five-state-description-v1',planned_runs=5,completed_runs=len(runs),status=status,runs=runs,missing_runs=missing,invalid_runs=invalid,deviations=deviations,same_observed_boot=sameboot,identical_observed_sensor_configuration=samecfg,baseline_change_mg=delta,baseline_change_relative_to_before=delta/first['mean_peak_mg'] if delta is not None and first['mean_peak_mg'] else None,primary_baseline=NAMES[0],trial_states=list(NAMES[1:4]),after_baseline_role='drift control, never substitute whichever baseline gives lower closure',automatic_extra_capture=False,correction_allowed=False,physical_validation_complete=False,scope='Description of entry-produced summaries, not independent raw-file verification or calibrated uncertainty. Three trial placements are not independent repeated baselines. No model approval, correction advice, new capture or retry.')

def main():
    root=Path(sys.argv[1]);r=describe(root)
    with (root/'batch_summary.json').open('x',encoding='utf8') as f:json.dump(r,f,indent=2,allow_nan=False);f.write('\n')
    print('CONTROL: %d/5 completed; status=%s; means mg=%s; baseline change mg=%s'%(r['completed_runs'],r['status'],[v['mean_peak_mg'] for v in r['runs']],r['baseline_change_mg']),flush=True)
    print('CPU summaries only; no automatic extra capture or correction approval.',flush=True)
    return 2 if r['status']=='INVALID' else 0

if __name__=='__main__':raise SystemExit(main())
