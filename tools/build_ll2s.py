#!/usr/bin/env python3
"""Build native LL2S with the published RSTP correction from pinned public source."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from build_frr_lldp import native_architecture

REVISION = 'e8b9028927a8640a13c97904caccb9a4dce8414d'
BASE_REVISION = 'd498b70473b3cdf2bc2f7a9af3e0dba9574aef61'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Local clone of weblab-network/ll2s')
    parser.add_argument('--tag', help='Default: ll2s:0.2.0-ovs-rstp1-arm64 on ARM; ll2s:dev on amd64')
    parser.add_argument('--context-dir', type=Path, help='Retain build inputs in a new directory')
    args = parser.parse_args()
    arch = native_architecture()
    tag = args.tag or ('ll2s:0.2.0-ovs-rstp1-arm64' if arch == 'arm64' else 'll2s:dev')
    base = 'll2s:weblab-build-base-' + arch
    if tag.startswith('ghcr.io/') or tag == base:
        parser.error('Use a separate local image tag, not a published or base tag')
    with tempfile.TemporaryDirectory(prefix='weblab-ll2s-build-') as directory:
        context = Path(directory)
        archive = context / 'source.tar'
        subprocess.run(['git', '-C', str(args.source), 'archive', '--format=tar',
                        '--output=' + str(archive), REVISION], check=True)
        subprocess.run(['tar', '-xf', str(archive), '-C', str(context)], check=True)
        archive.unlink()
        # The published initial recipe supplies a native Alpine base instead of
        # the subsequent amd64-only release image. The patched recipe, source,
        # checksummed OVS tarball and embedded notices remain upstream's own.
        (context / 'Dockerfile.base').write_bytes(subprocess.check_output(
            ['git', '-C', str(args.source), 'show', BASE_REVISION + ':Dockerfile']))
        if args.context_dir:
            shutil.copytree(context, args.context_dir)
        subprocess.run(['docker', 'build', '--platform', 'linux/' + arch,
                        '-f', str(context / 'Dockerfile.base'), '-t', base, str(context)], check=True)
        subprocess.run(['docker', 'build', '--platform', 'linux/' + arch,
                        '--build-arg', 'LL2S_BASE=' + base, '-t', tag, str(context)], check=True)


if __name__ == '__main__':
    main()
