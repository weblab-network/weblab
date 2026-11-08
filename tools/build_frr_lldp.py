#!/usr/bin/env python3
"""Build the FRR LLDP/SNMP/VLAN/VRRP image from a pinned local upstream Git checkout."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

REVISION = 'f8c0b08dcb0c78f9e42b9b86ae70b049e4e617c1'
BASES = {
    'amd64': 'quay.io/frrouting/frr@sha256:e995beaa50fdc9edb35eadcfefa29b7f062cc06f2b812613789b68fa541554d2',
    'arm64': 'quay.io/frrouting/frr@sha256:46e5bfb0ea9234de44d222906d51d8fe701d8a1cb7c373bc527b73326eea4d76',
}


def native_architecture():
    info = json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .}}'], text=True))
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(info['Architecture'], info['Architecture'])
    if info.get('OSType') != 'linux' or arch not in BASES:
        raise ValueError('Use a native Linux amd64 or arm64 Docker host')
    return arch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--tag', help='Default: the local Weblab FRR tag for the Docker host architecture')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--context-dir', type=Path,
                        help='Retain the exact build inputs in a new directory')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error('--jobs must be between 1 and 16')
    arch = native_architecture()
    args.tag = args.tag or ('weblab-frr:10.7.1-wl1-arm64' if arch == 'arm64' else 'weblab-frr:10.7.1-lldp-snmp-vlan-vrrp1')
    if args.tag.startswith('quay.io/frrouting/'):
        parser.error('Use a separate image tag; never replace upstream FRR')
    packaging = Path(__file__).resolve().parents[1] / 'packaging' / 'frr'
    with tempfile.TemporaryDirectory(prefix='weblab-frr-build-') as directory:
        context = Path(directory)
        for name in ('Dockerfile', 'lldp-vtysh.patch', 'lldp_backend.py', 'snmp_backend.py', 'vlan_backend.py', 'vrrp_backend.py', 'docker-start', 'NOTICE', 'SOURCES.md', 'collect_sources.py'):
            shutil.copy2(packaging / name, context / name)
        shutil.copy2(packaging.parents[1] / 'LICENSE', context / 'Weblab-LICENSE')
        with (context / 'frr-source.tar').open('wb') as stream:
            subprocess.run(['git', '-C', str(args.source), 'archive', REVISION],
                           stdout=stream, check=True)
        if args.context_dir:
            shutil.copytree(context, args.context_dir)
        subprocess.run(['docker', 'build', '--platform', 'linux/' + arch,
                        '--build-arg', 'FRR_BASE=' + BASES[arch], '--build-arg', f'JOBS={args.jobs}',
                        '-t', args.tag, str(context)], check=True)


if __name__ == '__main__':
    main()
