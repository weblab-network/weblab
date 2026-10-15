#!/usr/bin/env python3
"""Verify the release's binary/source files against the reviewed repository lock."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import frr


def verify(directory):
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / 'packaging/frr/release.json').read_text())
    if lock['image_id'] != frr.PUBLIC_IMAGE_ID or lock['image'] != frr.PUBLIC_IMAGE:
        raise ValueError('Release lock and profile disagree')
    for name, expected in lock['files'].items():
        if Path(name).name != name:
            raise ValueError('Invalid release filename')
        with (directory / name).open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('Release checksum mismatch: ' + name)
    identity = json.loads((directory / 'image.json').read_text())
    if identity['Id'] != frr.PUBLIC_IMAGE_ID or identity['image'] != frr.PUBLIC_IMAGE:
        raise ValueError('Bundle image identity mismatch')
    # Ensure source provenance is inspectable without extracting untrusted paths.
    with tarfile.open(directory / 'weblab-frr-sources.tar.gz') as archive:
        for name in ('Dockerfile', 'lldp-vtysh.patch', 'lldp_backend.py', 'snmp_backend.py',
                     'vlan_backend.py', 'vrrp_backend.py', 'docker-start', 'NOTICE',
                     'SOURCES.md', 'collect_sources.py'):
            if archive.extractfile('build/' + name).read() != (root / 'packaging/frr' / name).read_bytes():
                raise ValueError('Bundled build source differs from release: ' + name)
        if not archive.getmember('build/frr-source.tar').size:
            raise ValueError('Missing upstream FRR sources')
        if not archive.getmember('dependencies/custom/libyang-2.1.128.tar.gz').size:
            raise ValueError('Missing custom libyang sources')
    print('Release binary, source bundle and repository lock agree')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    verify(parser.parse_args().directory)
