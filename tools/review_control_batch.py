from pathlib import Path
import hashlib,json,statistics,math,sys,importlib.util
import numpy as np
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path('sim/golden').resolve()))
from repeatability import prepare_record,summarize
from sync_csv import load_capture
from timebase import clock_times
from measurement_review import fit_order
from measurement_run import _identity
from phase_model_review import diagnose,review_windows
from order1x_fixed import SCALE_G
source=Path('data/physical/hx_20261101_003/board_export/return_control_01')
out=Path(sys.argv[1]) if len(sys.argv)>1 else Path('data/review/hx_control_result_20261101');out.mkdir(parents=True,exist_ok=False)
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf8') as f:json.dump(v,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
names=['01_reference_before','02_trial_000','03_trial_120','04_trial_240','05_reference_after']
cos=np.rint(65536*np.cos(2*np.pi*np.arange(64)/64)).astype(np.int64)
nsin=-np.rint(65536*np.sin(2*np.pi*np.arange(64)/64)).astype(np.int64)
reports=[];allwindows=[]
for name in names:
    d=source/name;s=load(d/'summary.json');rt=load(d/'runtime.json');meta=load(d/'raw/capture.json');run=load(d/'run.json')
    assert run['status']=='COMPLETED' and load(d/'analysis/summary.json')==s
    assert s['runtime_context']==rt and s['analysis_identity']==_identity()
    prepared,hashes=prepare_record(d/'raw',avg_log2=7)
    assert hashes==s['source_sha256']==rt['source_sha256']
    assert rt['before']['boot_id']==rt['after']['boot_id'] and not rt['before']['read_errors'] and not rt['after']['read_errors']
    assert rt['sensor_configuration']['verified'] is True and rt['sensor_configuration']['registers']==dict(DEVID=173,FILTER=0,RANGE=129,POWER_CTL=0)
    assert all(meta[k]==0 for k in ['sample_drops','pulse_drops','source_missed'])
    amps=[];iqs=[]
    for j,p in enumerate(prepared):
        words=np.asarray(p['samples_raw'],dtype=np.int64);bins=words.reshape(128,64).sum(axis=0,dtype=np.int64)
        def norm(v):return (1 if v>=0 else -1)*((abs(v)+(1<<19))>>20)
        iq=[norm(int(bins@c)) for c in (cos,nsin)]
        assert iq==[p['reference']['real_q8'],p['reference']['imag_q8']]
        a=math.hypot(*iq)/SCALE_G;assert math.isclose(a,s['measurement']['amplitudes_g'][j],rel_tol=1e-14,abs_tol=1e-18)
        assert p['reference']['amplitude_g']==s['measurement']['amplitudes_g'][j]
        start=p['window']['start_turn']
        record=dict(name=name,start_turn=start,real_q8=iq[0],imag_q8=iq[1],amplitude_g=a,words_sha256=hashlib.sha256(words.astype('<i4').tobytes()).hexdigest(),source_sha256=hashes,start_tick=p['window']['start_tick'],end_tick=p['window']['end_tick'])
        allwindows.append(record);amps.append(p['reference']['amplitude_g']);iqs.append(iq)
    fixed=summarize(amps,prepared[0]['tach_quality']['rpm_median'],avg_log2=7)
    for k in ['mean_peak_mg','cv','rpm_median']:assert math.isclose(fixed[k],s['measurement'][k],rel_tol=1e-13,abs_tol=1e-15)
    cap=load_capture(d/'raw');rows=np.asarray(cap['samples'],dtype=np.int64);pulses=np.asarray(cap['pulses'],dtype=np.int64)
    t,p=clock_times(rows[:,1].tolist(),pulses[:,1].tolist(),meta['clock_hz'])
    axes=rows[:,2:];dc={axis:int(axes[:,i].sum())/len(axes)/256000 for i,axis in enumerate('xyz')}
    assert len(axes)==s['raw_statistics']['sample_count']==meta['sample_count']
    for axis in 'xyz':assert math.isclose(dc[axis],s['raw_statistics']['dc_g'][axis],rel_tol=1e-14,abs_tol=1e-16)
    selected=np.arange(0,len(pulses),2);selected=selected[(p[selected]>=t[0])&(p[selected]<=t[-1])]
    ls=[];window_dc=[]
    for j,start in enumerate(range(0,1024,128)):
        edges=p[selected[start:start+129]]
        result=fit_order(t,axes[:,2]/256000.,edges,include_trend=True)
        ls.append(result)
        keep=(t>=edges[0])&(t<edges[-1])
        window_dc.append(dict(start_turn=start,dc_g={a:float(axes[keep,i].mean()/256000) for i,a in enumerate('xyz')}))
    chunks=[]
    for begin in np.arange(0,40,2):
        keep=(t>=begin)&(t<begin+2)
        if not keep.any():continue
        chunks.append(dict(start_s=float(begin),sample_count=int(keep.sum()),dc_g={a:float(axes[keep,i].mean()/256000) for i,a in enumerate('xyz')},raw_peak_to_peak_g={a:float(np.ptp(axes[keep,i])/256000) for i,a in enumerate('xyz')}))
    r=dict(name=name,source_sha256=hashes,mean_peak_mg=fixed['mean_peak_mg'],window_sd_mg=statistics.stdev(amps)*1000,cv=fixed['cv'],rpm_median=fixed['rpm_median'],amplitudes_g=amps,sample_count=meta['sample_count'],pulse_count=meta['pulse_count'],sample_drops=meta['sample_drops'],pulse_drops=meta['pulse_drops'],source_missed=meta['source_missed'],boot_id=rt['before']['boot_id'],initialization_id=rt['initialization_id'],uptime_before=rt['before']['uptime_seconds'],uptime_after=rt['after']['uptime_seconds'],sensor_configuration=rt['sensor_configuration'],driver_sha256=rt['driver_sha256'],declared_setup=rt['declared_setup'],dc_g=dc,window_dc=window_dc,two_second_chunks=chunks,direct_time_ls_windows=ls,direct_time_ls_mean_mg=statistics.mean(v['amplitude_g'] for v in ls)*1000,hardware_windows_verified=0)
    reports.append(r)
    print('VERIFIED',name,'mean mg',r['mean_peak_mg'],'LS mg',r['direct_time_ls_mean_mg'],'CV',r['cv'],'RPM',r['rpm_median'],flush=True)
assert len({r['boot_id'] for r in reports})==1
assert len({r['initialization_id'] for r in reports})==5
assert all(r['driver_sha256']==reports[0]['driver_sha256'] for r in reports)
a=[r['mean_peak_mg']*.001 for r in reports];angles=load(source/'geometry.json')['angles_deg'];assert angles==[0,120,240]
primary=diagnose(a[:4],angles)
after=diagnose([a[4]]+a[1:4],angles)
lsmeans=[r['direct_time_ls_mean_mg']*.001 for r in reports]
lsprimary=diagnose(lsmeans[:4],angles)
sensitivity=review_windows([r['amplitudes_g'] for r in reports[:4]],angles)
v0=np.asarray(list(reports[0]['dc_g'].values()));v1=np.asarray(list(reports[-1]['dc_g'].values()))
norms=[float(np.linalg.norm(list(v['dc_g'].values()))) for v in reports]
cosangle=float((v0@v1)/(np.linalg.norm(v0)*np.linalg.norm(v1)));angle=math.degrees(math.acos(max(-1,min(1,cosangle))))
rpms=[v['rpm_median'] for v in reports]
r=dict(kind='hengxin-five-state-independent-review-v1',date='2026-11-01',archive_sha256='3937e7d84579e44074dd23d18ea9b51e8d51d9c61a275649f8996a7c1a53ca41',raw_files_hash_verified=20,stopped_captures_verified=5,integer_iq_windows_independently_verified=40,runs=reports,geometry=load(source/'geometry.json'),primary_model=primary,after_baseline_diagnostic_only=after,direct_time_ls_primary_model=lsprimary,baseline_change_mg=reports[-1]['mean_peak_mg']-reports[0]['mean_peak_mg'],baseline_change_relative_to_before=reports[-1]['mean_peak_mg']/reports[0]['mean_peak_mg']-1,rpm_span=max(rpms)-min(rpms),rpm_span_relative_to_mean=(max(rpms)-min(rpms))/statistics.mean(rpms),baseline_rpm_relative_change=rpms[-1]/rpms[0]-1,dc_vector_norms_g=norms,normalized_dc_vector_angle_change_deg=angle,dc_angle_scope='Angle between raw DC vectors only. Without independent bias/temperature calibration it is not a measured physical tilt or a causal diagnosis.',primary_unchanged_solver_closure_limit=.15,primary_closure_exceeds_unchanged_solver_limit=primary['closure_relative_error']>.15,original_mass_solver_called=False,absolute_trial_mass_g=None,old_data_substituted=False,new_fpga_executions=0,additional_capture_scheduled=False,automatic_extra_capture=False,correction_allowed=False,physical_validation_complete=False)
save(out/'independent_review.json',r);save(out/'integer_window_verification.json',dict(windows=allwindows));save(out/'sensitivity.json',sensitivity)
print('CONCLUSION',json.dumps({k:v for k,v in r.items() if k not in ['runs','geometry','primary_model','after_baseline_diagnostic_only','direct_time_ls_primary_model']},ensure_ascii=False,indent=2))
print('MODELS',json.dumps(dict(primary=primary,after_diagnostic=after,ls_primary=lsprimary),ensure_ascii=False))
