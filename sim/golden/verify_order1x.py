"""Verify hardware arithmetic against the exact prepared file; not physical certification."""
import argparse
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
from order1x_fixed import reference_result, SCALE_G

def verify(prepared_path, result_path):
    payload=Path(prepared_path).read_bytes()
    prepared=json.loads(payload.decode('utf-8-sig'))
    result=json.loads(Path(result_path).read_text(encoding='utf-8-sig'))
    digest=hashlib.sha256(payload).hexdigest()
    if result.get('prepared_input_sha256')!=digest:
        raise ValueError('prepared input hash mismatch')
    ref=reference_result(prepared['samples_raw'],prepared['avg_log2'])
    if 'expected_samples' in result and (type(result['expected_samples']) is not int or
            result['expected_samples']!=len(prepared['samples_raw'])):
        raise ValueError('hardware mismatch: expected_samples')
    for key,want in [('accepted_samples',len(prepared['samples_raw'])),('avg_log2',prepared['avg_log2']),
                     ('real_q8',ref['real_q8']),('imag_q8',ref['imag_q8'])]:
        if type(result.get(key)) is not int or result[key]!=want:
            raise ValueError('hardware mismatch: '+key)
    for key,low,high in [('magnitude_q8',0,2**32),('phase_code',-2**31,2**31)]:
        if type(result.get(key)) is not int or not low<=result[key]<high:
            raise ValueError('invalid hardware '+key)
    if type(result.get('phase_defined')) is not bool or result['phase_defined']!=ref['phase_defined']:
        raise ValueError('phase_defined mismatch')
    mag_error=abs(result['magnitude_q8']-ref['magnitude_q8'])
    phase_error=abs(((result['phase_code']-ref['phase_code']+2**31)%2**32)-2**31)*360/2**32
    if mag_error>2 or ref['phase_defined'] and phase_error>.001:
        raise ValueError('hardware polar result exceeds tolerance')
    # Optional legacy display fields must agree with verified registers.
    # Ideal-reference legacy amplitude may differ by half a magnitude LSB.
    canonical=dict(real_g=result['real_q8']/SCALE_G,imag_g=result['imag_q8']/SCALE_G,
                   amplitude_g=result['magnitude_q8']/SCALE_G,
                   phase_deg=result['phase_code']*360.0/(1<<32))
    for name,want in canonical.items():
        if name not in result:continue
        value=result[name]
        if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):
            raise ValueError('invalid derived display field: '+name)
        tolerance=(.5/SCALE_G+1e-15) if name=='amplitude_g' else 1e-9 if name=='phase_deg' else 1e-15
        error=abs(((value-want+180)%360)-180) if name=='phase_deg' else abs(value-want)
        if error>tolerance:
            raise ValueError('display field disagrees with register: '+name)
    return dict(verified=True,prepared_sha256=digest,result_sha256=hashlib.sha256(Path(result_path).read_bytes()).hexdigest(),
                real_g=result['real_q8']/SCALE_G,imag_g=result['imag_q8']/SCALE_G,
                amplitude_g=canonical['amplitude_g'],phase_deg=canonical['phase_deg'] if ref['phase_defined'] else None,
                magnitude_error_q8=mag_error,phase_error_deg=phase_error if ref['phase_defined'] else None,
                verification_scope='same prepared input arithmetic only; hardware provenance and physical quality require separate acceptance',
                physical_validation_complete=False)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('prepared');p.add_argument('result');p.add_argument('--out',required=True)
    args=p.parse_args(argv)
    try:
        report=verify(args.prepared,args.result)
        with Path(args.out).open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2,allow_nan=False)
    except (OSError,ValueError,KeyError,TypeError) as error:p.exit(1,str(error)+'\n')
    return 0

if __name__=='__main__':raise SystemExit(main())
