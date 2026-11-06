#!/usr/bin/env python3
"""Verify the audited source hashes; optionally compare an upstream checkout."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Optional production source checkout')
    args = parser.parse_args()
    record = json.loads((ROOT / 'docs/source-sync.json').read_text())
    errors = []
    for name, spec in record['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid manifest file path')
        for root, key in [(ROOT, 'public_sha256'), (args.source, 'upstream_sha256')]:
            if root is None:
                continue
            file = root / relative
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != spec[key]:
                errors.append(f'{key}: {name}')
    if errors:
        raise SystemExit('Source sync needs review:\n' + '\n'.join(errors))
    print(json.dumps({'status': 'verified', 'files': len(record['files']),
                      'upstream_revision': record['upstream_revision'],
                      'upstream_checkout_checked': args.source is not None}))


if __name__ == '__main__':
    main()
