"""Fetch or import pinned TUL board files as a local build dependency."""
from pathlib import Path, PurePosixPath
from zipfile import ZipFile
from urllib.request import urlopen
import argparse
import hashlib
import io
import json


def prepare_board_files(archive, destination, expected):
    destination = Path(destination).resolve()
    payloads = {}
    with ZipFile(io.BytesIO(archive)) as z:
        for name, digest in expected.items():
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
                raise ValueError('unsafe dependency path')
            if z.namelist().count(name) != 1:
                raise ValueError('missing or duplicate dependency member: ' + name)
            if z.getinfo(name).file_size > 5 * 1024 * 1024:
                raise ValueError('dependency member too large')
            body = z.read(name)
            if hashlib.sha256(body).hexdigest() != digest:
                raise ValueError('dependency hash mismatch: ' + name)
            payloads[name] = body
    destination.mkdir(parents=True, exist_ok=False)
    for name, body in payloads.items():
        path = destination.joinpath(*PurePosixPath(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    receipt = json.loads((root / 'third_party/pynq_z2_board_files.json').read_text(encoding='utf-8'))
    if args.archive:
        body = args.archive.read_bytes()
    else:
        with urlopen(receipt['archive_url'], timeout=30) as response:
            body = response.read(5 * 1024 * 1024 + 1)
    if len(body) > 5 * 1024 * 1024 or hashlib.sha256(body).hexdigest() != receipt['archive_sha256']:
        raise ValueError('board archive does not match pinned source')
    out = prepare_board_files(body, args.out, receipt['files'])
    print('Verified local board repository:', out)
    print('Third-party dependency; no redistribution license is granted by this helper.')


if __name__ == '__main__':
    main()
