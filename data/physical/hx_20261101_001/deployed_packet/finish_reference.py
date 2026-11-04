"""One-off fixed-reference batch description; no pass threshold or correction."""
import json
from pathlib import Path
import statistics
import sys
root=Path(sys.argv[1]);runs=[]
for i in range(1,4):
    path=root/('run_%02d'%i)/'summary.json'
    if not path.exists():continue
    value=json.loads(path.read_text(encoding='utf-8'))
    measurement=value.get('measurement')
    if not measurement:continue
    context=value.get('runtime_context') or {}
    runs.append(dict(run=i,mean_peak_mg=measurement['mean_peak_mg'],
        within_record_cv=measurement['cv'],rpm_median=measurement['rpm_median'],
        amplitude_source=measurement['amplitude_source'],source_sha256=value['source_sha256'],
        before_boot_id=(context.get('before') or {}).get('boot_id'),
        after_boot_id=(context.get('after') or {}).get('boot_id'),
        sensor_configuration=context.get('sensor_configuration')))
means=[x['mean_peak_mg'] for x in runs]
report=dict(kind='hengxin-fixed-reference-description-v1',planned_runs=3,completed_runs=len(runs),runs=runs,
    mean_of_run_means_mg=statistics.mean(means) if means else None,
    observed_run_range_mg=max(means)-min(means) if means else None,
    observed_run_sd_mg=statistics.stdev(means) if len(means)>1 else None,
    scope='Descriptive pilot only. Range/SD are not confidence bounds or instrument accuracy. All completed runs retained; no automatic extra capture.',
    correction_allowed=False,physical_validation_complete=False)
with (root/'batch_summary.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2,allow_nan=False);stream.write('\n')
print('BATCH: %d/3 completed; means mg=%s; observed range mg=%s'%(len(runs),means,report['observed_run_range_mg']),flush=True)
