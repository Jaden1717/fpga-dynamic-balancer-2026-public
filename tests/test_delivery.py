"""Catch broken release paths, stale manifests and unusable skill exports."""
from pathlib import Path
import importlib.util
import io
from zipfile import ZipFile
import pytest

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    path = ROOT / 'tools' / (name + '.py')
    assert path.is_file(), f'missing delivery command: {name}'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_paths_accept_lowercase_and_reject_case_space_and_traversal():
    audit = tool('verify_delivery')
    assert audit.path_errors(['readme.md', '.github/workflows/validate.yml', 'data/run_1/raw.csv']) == []
    for bad in ['data/HX_1/raw.csv', 'data/run 1/raw.csv', '../escape.csv', '/absolute.csv', 'data\\run.csv']:
        assert audit.path_errors([bad]), bad


def test_manifest_checks_real_bytes_and_requires_every_release_file(tmp_path):
    audit = tool('verify_delivery')
    (tmp_path / 'data.txt').write_bytes(b'abc')
    sha = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    manifest = tmp_path / 'sha256sums.txt'
    manifest.write_text(f'{sha}  data.txt\n', encoding='ascii')
    files = ['data.txt', 'sha256sums.txt']
    assert audit.manifest_errors(tmp_path, files) == []
    (tmp_path / 'data.txt').write_bytes(b'changed')
    assert audit.manifest_errors(tmp_path, files)
    (tmp_path / 'data.txt').write_bytes(b'abc')
    (tmp_path / 'extra.txt').write_bytes(b'unlisted')
    assert audit.manifest_errors(tmp_path, files + ['extra.txt'])


def test_manifest_rejects_escape_before_reading_outside_release(tmp_path):
    audit = tool('verify_delivery')
    (tmp_path / 'sha256sums.txt').write_text('0' * 64 + '  ../outside.txt\n', encoding='ascii')
    assert audit.manifest_errors(tmp_path, ['sha256sums.txt'])


def test_markdown_audit_catches_broken_local_target(tmp_path):
    audit = tool('verify_delivery')
    (tmp_path / 'readme.md').write_text('[Guide](docs/guide.md)\n[Web](https://example.com)\n', encoding='utf-8')
    assert audit.link_errors(tmp_path, ['readme.md'])
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/guide.md').write_text('# Guide\n', encoding='utf-8')
    assert audit.link_errors(tmp_path, ['readme.md', 'docs/guide.md']) == []


def test_markdown_audit_checks_github_html_images_and_navigation(tmp_path):
    audit = tool('verify_delivery')
    (tmp_path / 'readme.md').write_text('<img src="hero.svg"><a href="guide.md">Guide</a>', encoding='utf-8')
    assert audit.link_errors(tmp_path, ['readme.md'])
    (tmp_path / 'hero.svg').write_text('<svg/>', encoding='utf-8')
    (tmp_path / 'guide.md').write_text('# Guide', encoding='utf-8')
    assert audit.link_errors(tmp_path, ['readme.md', 'guide.md', 'hero.svg']) == []


def test_markdown_audit_rejects_case_mismatch_on_windows_too(tmp_path):
    audit = tool('verify_delivery')
    (tmp_path / 'guide.md').write_text('# Guide', encoding='utf-8')
    (tmp_path / 'readme.md').write_text('[Guide](Guide.md)', encoding='utf-8')
    assert audit.link_errors(tmp_path, ['readme.md', 'guide.md'])


def test_skill_export_creates_runtime_filename_without_modifying_source(tmp_path):
    export = tool('export_skill')
    source = tmp_path / 'source'; source.mkdir()
    payload = b'---\nname: example\ndescription: Example workflow\n---\n\n# Workflow\n'
    (source / 'skill.md').write_bytes(payload)
    out = tmp_path / 'exported'
    export.export_skill(source, out)
    assert (out / 'SKILL.md').read_bytes() == payload
    assert (source / 'skill.md').read_bytes() == payload
    assert {p.name for p in source.iterdir()} == {'skill.md'}


def test_skill_export_preserves_existing_destination(tmp_path):
    export = tool('export_skill')
    source = tmp_path / 'source'; source.mkdir()
    (source / 'skill.md').write_text('---\nname: example\ndescription: Example\n---\n', encoding='utf-8')
    out = tmp_path / 'already'; out.mkdir(); (out / 'keep.txt').write_bytes(b'original')
    with pytest.raises(FileExistsError):
        export.export_skill(source, out)
    assert (out / 'keep.txt').read_bytes() == b'original'


def test_board_dependency_extracts_only_hash_verified_allowlisted_files(tmp_path):
    prepare = tool('prepare_board_files')
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as z:
        z.writestr('pynq-z2/A.0/board.xml', b'abc')
        z.writestr('../escape.txt', b'untrusted extra member')
    expected = {'pynq-z2/A.0/board.xml': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}
    out = tmp_path / 'board'
    prepare.prepare_board_files(stream.getvalue(), out, expected)
    assert (out / 'pynq-z2/A.0/board.xml').read_bytes() == b'abc'
    assert not (tmp_path / 'escape.txt').exists()
    assert len([p for p in out.rglob('*') if p.is_file()]) == 1


def test_board_dependency_rejects_hash_mismatch_before_creating_output(tmp_path):
    prepare = tool('prepare_board_files')
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as z:
        z.writestr('pynq-z2/A.0/board.xml', b'wrong')
    out = tmp_path / 'board'
    with pytest.raises(ValueError):
        prepare.prepare_board_files(stream.getvalue(), out, {'pynq-z2/A.0/board.xml': '0' * 64})
    assert not out.exists()
