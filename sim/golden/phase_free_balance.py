"""Amplitude-only single-plane four-run balance model; no physical approval.

One baseline and three SEPARATE runs with the same mass at the same radius.
Angles share a marked rotor zero and a declared viewing/counting direction.
All four amplitudes must use the same estimator, speed and mounting conditions.
A(theta)^2-A0^2 = Q + X*cos(theta) + Y*sin(theta).
Q=|H*T|^2 and (X+iY)/(2Q)=B/(H*T). Thus the correction
mass vector at the trial radius is -trial_mass*(X+iY)/(2Q).
The sensor transfer phase cancels; TACH absolute phase is never used.
"""
import argparse
import json
import math
from numbers import Real
from pathlib import Path
import numpy as np


def _number(value,name,positive=False):
    if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):
        raise ValueError(name+' must be finite numeric data')
    if value<0 or positive and value==0:
        raise ValueError(name+' must be '+('positive' if positive else 'nonnegative'))
    return float(value)


def solve(baseline_g,trial_amplitudes_g,angles_deg,*,trial_mass_g,trial_radius_mm,uncertainty_g):
    a0=_number(baseline_g,'baseline_g')
    mass=_number(trial_mass_g,'trial_mass_g',True)
    radius=_number(trial_radius_mm,'trial_radius_mm',True)
    uncertainty=_number(uncertainty_g,'uncertainty_g')
    if len(trial_amplitudes_g)!=3 or len(angles_deg)!=3:
        raise ValueError('exactly three trial amplitudes and angles required')
    amps=np.array([_number(a,'trial_amplitude_g') for a in trial_amplitudes_g])
    angles=[]
    for value in angles_deg:
        if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):
            raise ValueError('angles must be finite degrees')
        angles.append(float(value)%360)
    theta=np.radians(angles)
    design=np.column_stack((np.ones(3),np.cos(theta),np.sin(theta)))
    condition=float(np.linalg.cond(design))
    if not math.isfinite(condition) or condition>10:
        raise ValueError('trial angle geometry is singular or ill-conditioned')
    q,x,y=[float(v) for v in np.linalg.solve(design,amps**2-a0**2)]
    if not all(math.isfinite(v) for v in (q,x,y)) or q<=0:
        raise ValueError('trial response is not resolved: nonpositive fitted Q')
    effect=math.sqrt(q)
    if effect<=5*uncertainty or a0>0 and a0<=5*uncertainty:
        raise ValueError('baseline or trial response is not resolved above uncertainty')
    left=x*x+y*y
    right=4*a0*a0*q
    denominator=max(left,right,np.finfo(float).tiny)
    closure=abs(left-right)/denominator
    # If baseline is exactly zero, roundoff in the equal-amplitude solve is benign.
    if a0==0 and math.hypot(x,y)<=1e-12*q:
        closure=0.
    if closure>.15:
        raise ValueError('four-run model closure exceeds 15percent; do not compute physical correction')
    vector=-mass*complex(x,y)/(2*q)
    cmass=abs(vector)
    angle=math.degrees(math.atan2(vector.imag,vector.real))%360
    if cmass<=1e-12*mass:
        cmass=0.;angle=None
    if not math.isfinite(cmass):
        raise ValueError('correction exceeds finite range')
    return dict(schema_version=1,method='amplitude-only baseline plus three separate trial runs',
        correction_mass_g=cmass,correction_radius_mm=radius,correction_angle_deg=angle,
        correction_real_mass_g=vector.real,correction_imag_mass_g=vector.imag,
        trial_mass_g=mass,trial_radius_mm=radius,angles_deg=angles,
        baseline_g=a0,trial_amplitudes_g=amps.tolist(),uncertainty_g=uncertainty,
        fitted_trial_response_g=effect,model_closure_relative_error=closure,
        geometry_condition_number=condition,
        thresholds=dict(max_geometry_condition=10,min_response_uncertainty_ratio=5,max_closure_relative_error=.15),
        thresholds_scope='provisional screening, not validated confidence bounds',
        angle_convention='From marked rotor zero in the SAME positive direction as supplied trial angles, viewed from the SAME face',
        prerequisites='same trial mass/radius, constant linear response, consistent amplitude estimator and settled speed/mounting; physical checks external',
        physical_validation_complete=False,physical_trial_mass_recommended=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('measurements',help='JSON with the six solve argument names')
    parser.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    if Path(args.out).exists():parser.error('--out already exists')
    try:
        doc=json.loads(Path(args.measurements).read_text(encoding='utf-8-sig'))
        if not isinstance(doc,dict):raise ValueError('measurement JSON must be an object')
        result=solve(**doc)
        with Path(args.out).open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,allow_nan=False)
    except (OSError,ValueError,TypeError) as e:parser.exit(1,str(e)+'\n')
    print('Model computed; physical approval and corrected retest still required')
    return 0


if __name__=='__main__':raise SystemExit(main())
