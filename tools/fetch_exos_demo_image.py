#!/usr/bin/env python3
"""Fetch the explicitly pinned, redistributable Virtual EXOS demo binary."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    metadata = json.loads((root / 'packaging/exos/image.json').read_text())
    args.directory.mkdir(parents=True, exist_ok=True)
    target = args.directory / metadata['filename']
    if target.exists():
        raise SystemExit(f'{target} already exists; nothing was replaced')
    descriptor, name = tempfile.mkstemp(prefix='.download-', dir=args.directory)
    temporary = Path(name)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, 'wb') as output, urllib.request.urlopen(metadata['url'], timeout=90) as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise ValueError('EXOS download exceeds the expected size limit')
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != metadata['sha256']:
            raise ValueError('EXOS download checksum mismatch; no image installed')
        temporary.chmod(0o644)
        os.link(temporary, target)
        print(f'Verified {target}: {digest.hexdigest()}')
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
