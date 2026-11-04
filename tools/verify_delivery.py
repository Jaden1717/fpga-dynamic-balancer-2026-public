"""Audit tracked release paths, byte manifests, maintained Markdown and frozen evidence."""
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from html import unescape
import argparse
import hashlib
import json
import os
import re
import subprocess


def path_errors(files):
    errors = []
    for name in files:
        path = PurePosixPath(name)
        if (not name or not re.fullmatch(r'[a-z0-9._/-]+', name)
                or path.is_absolute() or any(p in ('.', '..') for p in name.split('/'))
                or '//' in name):
            errors.append(f'invalid lowercase relative path: {name}')
    if len(set(files)) != len(files):
        errors.append('duplicate release paths')
    return errors


def manifest_errors(root, files):
    root = Path(root).resolve()
    manifest = root / 'sha256sums.txt'
    if not manifest.is_file():
        return ['missing sha256sums.txt']
    errors = []
    found = set()
    for index, line in enumerate(manifest.read_text(encoding='utf-8').splitlines(), 1):
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            errors.append(f'malformed manifest line {index}')
            continue
        want, name = match.groups()
        if path_errors([name]) or not (root / name).resolve().is_relative_to(root):
            errors.append(f'unsafe manifest path: {name}')
            continue
        if name in found:
            errors.append(f'duplicate manifest entry: {name}')
        found.add(name)
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != want:
            errors.append(f'missing or changed bytes: {name}')
    expected = set(files) - {'sha256sums.txt'}
    for name in sorted(expected - found):
        errors.append(f'unlisted release file: {name}')
    for name in sorted(found - expected):
        errors.append(f'manifest entry outside release: {name}')
    return errors


def link_errors(root, files):
    root = Path(root).resolve()
    errors = []
    for name in files:
        if not name.endswith('.md'):
            continue
        source = root / name
        content = source.read_text(encoding='utf-8')
        targets = re.findall(r'!?\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)', content)
        targets += re.findall(r'''\b(?:src|href)\s*=\s*["']([^"']+)["']''', content, re.I)
        for target in targets:
            target = unescape(target.strip('<>'))
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            dest = (source.parent / unquote(parsed.path)).resolve()
            if not dest.is_relative_to(root) or not dest.exists():
                errors.append(f'broken local link: {name} -> {target}')
                continue
            current = root
            requested = Path(os.path.abspath(source.parent / unquote(parsed.path)))
            for part in requested.relative_to(root).parts:
                if part not in {entry.name for entry in current.iterdir()}:
                    errors.append(f'case mismatch in local link: {name} -> {target}')
                    break
                current = current / part
    return errors


def evidence_errors(root):
    root = Path(root)
    path = root / 'verification/path_migration_20261102.json'
    if not path.is_file():
        return ['missing byte-preservation migration receipt']
    errors = []
    for record in json.loads(path.read_text(encoding='utf-8'))['files']:
        if not record['immutable']:
            continue
        target = root / record['current_path']
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != record['original_sha256']:
            errors.append(f'frozen evidence changed: {record["current_path"]}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    raw = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root)
    files = [name.decode('utf-8') for name in raw.split(b'\0') if name]
    maintained = [n for n in files if n.endswith('.md') and not n.startswith(('data/physical/', 'data/review/', 'sealed/'))]
    errors = path_errors(files) + manifest_errors(root, files) + link_errors(root, maintained) + evidence_errors(root)
    for error in errors:
        print(error)
    print(f'Delivery audit: {len(files)} tracked files; {len(maintained)} maintained Markdown files; {len(errors)} errors.')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
