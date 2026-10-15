#!/usr/bin/env python3
"""Build the FRR LLDP/SNMP/VLAN/VRRP image from a pinned local upstream Git checkout."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

REVISION = 'f8c0b08dcb0c78f9e42b9b86ae70b049e4e617c1'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--tag', default='weblab-frr:10.7.1-lldp-snmp-vlan-vrrp1')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--context-dir', type=Path,
                        help='Retain the exact build inputs in a new directory')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error('--jobs must be between 1 and 16')
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
        subprocess.run(['docker', 'build', '--build-arg', f'JOBS={args.jobs}',
                        '-t', args.tag, str(context)], check=True)


if __name__ == '__main__':
    main()
