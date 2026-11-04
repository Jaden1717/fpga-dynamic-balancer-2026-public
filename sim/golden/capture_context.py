"""Bind a separate capture-context sidecar without rewriting frozen evidence.

Missing configuration/driver identity stays missing. Operator notes and their
timing are declarations, not automatically verified physical observations.
"""
import argparse
import json
from pathlib import Path
from repeatability import check_stop, read_json
from sync_csv import load_capture


def build_context(directory, *, source='unknown', observation_timing='retrospective', notes=()):
    if source not in ('unknown','fan-tach-2ppr','external-1ppr','static'):
        raise ValueError('unsupported source declaration')
    if observation_timing not in ('retrospective','contemporaneous','unknown'):
        raise ValueError('observation timing must be explicitly declared')
    notes = list(notes)
    if any(not isinstance(note,str) or not note.strip() for note in notes):
        raise ValueError('notes must contain nonempty strings')
    directory = Path(directory)
    meta = read_json(directory/'capture.json')
    hashes = check_stop(directory,meta['sample_count'],meta['epoch'],meta['xsa_sha256'])
    capture = load_capture(directory,allow_static=True)
    if capture['metadata'] != meta:
        raise ValueError('capture metadata changed during context preparation')
    if source=='static' and meta.get('status')!='STATIC_ONLY':
        raise ValueError('static declaration contradicts rotating capture')
    if check_stop(directory,meta['sample_count'],meta['epoch'],meta['xsa_sha256'])!=hashes:
        raise ValueError('capture changed during context preparation')
    return dict(schema_version=1,kind='hengxin-capture-context-v1',
                capture_directory=str(directory.resolve()),source_sha256=hashes,
                source_declaration=source,source_declaration_verified_by_software=False,
                observation_timing=observation_timing,notes=notes,
                documentation_date=None,observed_at=None,
                configuration_evidence=dict(recorded=meta.get('sensor_configuration') is not None,
                                            value=meta.get('sensor_configuration')),
                driver_identity_evidence=dict(recorded=meta.get('driver_sha256') is not None,
                                              value=meta.get('driver_sha256')),
                declared_setup=dict(fixture_id=None,sensor_mount_id=None,sensor_axis=None,
                                    cable_relief=None,environment=None,trial_position=None,
                                    trial_mass_g=None,trial_radius_mm=None,fan_power_state=None),
                integrity_scope='strict CSV/metadata loading and stopped-file binding only; no physical source, setup or missing configuration inferred',
                physical_validation_complete=False,correction_allowed=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('--source',choices=('unknown','fan-tach-2ppr','external-1ppr','static'),default='unknown')
    parser.add_argument('--observation-timing',choices=('retrospective','contemporaneous','unknown'),default='retrospective')
    parser.add_argument('--note',action='append',default=[])
    parser.add_argument('--out',required=True)
    args = parser.parse_args(argv)
    try:
        target = Path(args.out)
        capture = Path(args.capture).resolve()
        if target.exists():
            raise ValueError('output already exists')
        if capture==target.resolve() or capture in target.resolve().parents:
            raise ValueError('context sidecar must be outside frozen capture directory')
        context = build_context(args.capture,source=args.source,
                                observation_timing=args.observation_timing,notes=args.note)
        with target.open('x',encoding='utf-8') as stream:
            json.dump(context,stream,indent=2,allow_nan=False)
            stream.write('\n')
    except (ValueError,OSError,KeyError,TypeError) as error:
        parser.exit(1,str(error)+'\n')
    print('External context saved; unknown evidence remains unknown; physical validation incomplete.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
