"""Export lowercase skill source to an Agent Skills runtime directory."""
from pathlib import Path
import argparse


def export_skill(source, destination):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    body = (source / 'skill.md').read_bytes()
    text = body.decode('utf-8').replace('\r\n', '\n')
    if not text.startswith('---\n') or '\nname:' not in text or '\ndescription:' not in text:
        raise ValueError('skill metadata must include name and description')
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'SKILL.md').write_bytes(body)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1] / 'skill/pynq_evidence_workflow')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(export_skill(args.source, args.out))


if __name__ == '__main__':
    main()
