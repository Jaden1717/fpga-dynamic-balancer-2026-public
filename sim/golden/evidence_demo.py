"""Build an offline, input-bound measurement demonstration from frozen records.

This never imports PYNQ or executes an overlay. Hardware results, when supplied,
are archived evidence reverified here. CPU-only output is explicitly labelled.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

from order1x_fixed import SCALE_G
from repeatability import prepare_record, protocol_starts, summarize, COMPUTE_XSA
from verify_order1x import verify


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')


def _read(payload):
    return json.loads(payload.decode('utf-8-sig'))


def _progress(stage,done,total):
    label = dict(loading='读取并核验冻结采集',prepared='准备角域窗口',verified='复核已归档FPGA窗口').get(stage,stage)
    print('%s %d/%d'%(label,done,total),flush=True)


def build_demo(directory, out, *, hardware_analysis=None, context_path=None,
               avg_log2=7, progress=None):
    directory, out = Path(directory), Path(out)
    if out.exists():
        raise ValueError('output already exists')
    protected = [directory.resolve()]
    if hardware_analysis is not None:
        hardware_analysis = Path(hardware_analysis)
        protected.append(hardware_analysis.resolve())
    if any(p==out.resolve() or p in out.resolve().parents for p in protected):
        raise ValueError('demo output must be outside frozen source directories')
    prepared, raw_hashes = prepare_record(directory,avg_log2=avg_log2,progress=progress)
    snapshots = {}
    def snapshot(path):
        path = Path(path)
        payload = path.read_bytes()
        snapshots[path] = payload
        return payload
    meta_payload = snapshot(directory/'capture.json')
    stop_payload = snapshot(directory/'stop_snapshot.json')
    meta = _read(meta_payload)
    evidence = {'capture.json':meta_payload,'stop_snapshot.json':stop_payload}
    context = None
    if context_path is not None:
        payload = snapshot(Path(context_path))
        context = _read(payload)
        if (not isinstance(context,dict) or context.get('kind')!='hengxin-capture-context-v1'
                or context.get('source_sha256')!=raw_hashes
                or context.get('observation_timing') not in ('retrospective','contemporaneous','unknown')
                or not isinstance(context.get('notes'),list)
                or any(not isinstance(s,str) for s in context['notes'])):
            raise ValueError('context identity/source binding is invalid')
        evidence['context.json'] = payload
    windows, amplitudes = [], []
    for start, fresh in zip(protocol_starts(avg_log2),prepared):
        if hardware_analysis is not None:
            ppath = hardware_analysis/('input_%d.json'%start)
            rpath = hardware_analysis/('result_%d.json'%start)
            ppayload, rpayload = snapshot(ppath), snapshot(rpath)
            archived, result = _read(ppayload), _read(rpayload)
            for key in ('samples_raw','source_sha256','axis','avg_log2','window','preprocessing',
                        'used_turns','points_per_turn','source_capture_xsa_sha256'):
                if archived.get(key)!=fresh.get(key):
                    raise ValueError('archived input/capture binding mismatch: '+key)
            if (result.get('source_provenance')!={k:v for k,v in archived.items() if k!='samples_raw'}
                    or result.get('accelerator_xsa_sha256')!=COMPUTE_XSA):
                raise ValueError('archived hardware provenance mismatch')
            checked = verify(ppath,rpath)
            if checked['prepared_sha256']!=_sha(ppayload) or checked['result_sha256']!=_sha(rpayload):
                raise ValueError('source changed during verification')
            amplitude, phase = checked['amplitude_g'], checked['phase_deg']
            real, imag = checked['real_g'], checked['imag_g']
            evidence['result_%d.json'%start] = rpayload
            evidence['verification_%d.json'%start] = _json_bytes(checked)
            if progress:
                progress('verified',len(windows)+1,len(prepared))
        else:
            ppayload = _json_bytes(fresh)
            ref = fresh['reference']
            amplitude = ref['amplitude_g']
            phase = ref['phase_deg'] if ref['phase_defined'] else None
            real, imag = ref['real_q8']/SCALE_G, ref['imag_q8']/SCALE_G
        evidence['input_%d.json'%start] = ppayload
        amplitudes.append(amplitude)
        windows.append(dict(index=len(windows)+1,start_turn=start,turns=1<<avg_log2,
            start_tick=fresh['window']['start_tick'],end_tick=fresh['window']['end_tick'],
            amplitude_peak_mg=amplitude*1000,phase_deg=phase,real_g=real,imag_g=imag,
            input_sha256=_sha(ppayload),
            result_sha256=_sha(rpayload) if hardware_analysis is not None else None))
    raw_after = {name:_sha((directory/name).read_bytes()) for name in raw_hashes}
    if raw_after!=raw_hashes or any(path.read_bytes()!=payload for path,payload in snapshots.items()):
        raise ValueError('source changed during demo preparation')
    summary = summarize(amplitudes,prepared[0]['tach_quality']['rpm_median'],avg_log2=avg_log2)
    root = Path(__file__).resolve().parents[2]
    core_names = ['sim/golden/angle_antialias.py','sim/golden/prepare_tach_order1x.py',
                  'sim/golden/order1x_fixed.py','sim/golden/phase_free_balance.py',
                  'board/bringup/order1x_accel.py']
    core_hashes = {name:_sha((root/name).read_bytes()) for name in core_names}
    count = 8 if hardware_analysis is not None else 0
    report = dict(schema_version=1,kind='hengxin-offline-measurement-demo-v1',
        evidence_kind='archived_fpga' if count else 'cpu_reference',
        hardware_execution_this_run=False,hardware_windows_verified=count,
        record_id=directory.name,source_sha256=raw_hashes,mainline_sha256=core_hashes,
        capture={name:meta.get(name) for name in ('sample_count','pulse_count','requested_seconds',
            'status','sample_drops','pulse_drops','source_missed','epoch','clock_hz','xsa_sha256')},
        configuration_recorded=meta.get('sensor_configuration') is not None,
        driver_identity_recorded=meta.get('driver_sha256') is not None,
        protocol=dict(axis='z',turns_per_window=1<<avg_log2,points_per_turn=64,
                      resampling='aa128-to64',recorded_ppr=meta['pulses_per_revolution'],
                      interpreted_ppr=2,absolute_phase_calibrated=False),
        measurement=dict(mean_peak_mg=summary['mean_peak_mg'],
            sample_sd_peak_mg=statistics.stdev(amplitudes)*1000,
            cv=summary['cv'],rpm_median=summary['rpm_median'],
            min_peak_mg=min(amplitudes)*1000,max_peak_mg=max(amplitudes)*1000,
            historical_cv_reference=summary['criteria']['cv_limit'],
            historical_cv_reference_met=summary['within_record_repeatability_pass']),
        windows=windows,context=context,correction_allowed=False,physical_validation_complete=False,
        scope='frozen record and same-input arithmetic only; no new hardware run, causal reduction, calibrated accuracy or final correction',
        raw_csv_bundled=False)
    # Validate all serialization and source identities before creating the output.
    payload = _json_bytes(report)
    template = Path(__file__).with_suffix('.html').read_text(encoding='utf-8')
    rows = ''.join('<tr><td>%d</td><td>%d</td><td>%.6f</td><td>%s</td></tr>' % (
        w['index'],w['start_turn'],w['amplitude_peak_mg'],
        '未定义' if w['phase_deg'] is None else '%.3f'%w['phase_deg']) for w in windows)
    safe_json = payload.decode('utf-8').replace('&',r'\u0026').replace('<',r'\u003c').replace('>',r'\u003e').replace('\u2028',r'\u2028').replace('\u2029',r'\u2029')
    page = template.replace('__TABLE_ROWS__',rows).replace('__REPORT_JSON__',safe_json)
    output_files = {'index.html':page.encode('utf-8'),'report.json':payload,
        'source_manifest.json':_json_bytes(dict(raw_capture=raw_hashes,
            archived_files={str(path):_sha(value) for path,value in snapshots.items()},
            mainline=core_hashes,scope='CSV files remain in original archive; copied window evidence retains original bytes')),
        'readme.md':('''# 衡芯离线测量演示
双击index.html即可查看，不需要联网、开发板或本地服务。
report.json为可复核摘要；evidence/保存本次使用的输入及可选历史FPGA结果、重新核对结果和采集元数据。
本次构建没有运行FPGA。CPU来源和已归档FPGA来源分别标记。历史CV10%仅作参考，不自动重采。
原始CSV不在演示包中，其SHA在摘要中；完整输入重建仍需原采集档案。
相对相位不是转子配重角度。窗口SD不是仪器精度或独立启停重复性，最终配重闭环未验证。
在解压目录可运行sha256sum -c sha256sums.txt校验文件。
''').encode('utf-8')}
    output_files.update({'evidence/'+name:value for name,value in evidence.items()})
    out.mkdir(parents=True,exist_ok=False)
    for name,value in output_files.items():
        target = out/name
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:
            stream.write(value)
    with (out/'sha256sums.txt').open('x',encoding='ascii',newline='\n') as stream:
        for name,value in sorted(output_files.items()):
            stream.write(_sha(value)+'  '+name+'\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('--fpga-analysis',help='existing archived input/result folder; never executes hardware')
    parser.add_argument('--context')
    parser.add_argument('--avg-log2',type=int,choices=(6,7),default=7)
    parser.add_argument('--out',required=True,help='new directory outside all frozen inputs')
    args = parser.parse_args(argv)
    try:
        report = build_demo(args.capture,args.out,hardware_analysis=args.fpga_analysis,
            context_path=args.context,avg_log2=args.avg_log2,progress=_progress)
    except (OSError,ValueError,TypeError,KeyError) as error:
        parser.exit(1,str(error)+'\n')
    print('离线演示已生成：%s；归档FPGA复核窗口=%d；本次未执行硬件。' %
          (Path(args.out)/'index.html',report['hardware_windows_verified']))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
