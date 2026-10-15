#!/usr/bin/env python3
"""Package one already-built Weblab FRR image with its corresponding sources."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile


def run(*args, **kwargs):
    return subprocess.check_output(args, **kwargs)


def bundle(image, output, work_dir=None):
    # A new destination prevents replacing an earlier binary/source pair.
    output.mkdir(parents=True, exist_ok=False)
    info = json.loads(run('docker', 'image', 'inspect', image))[0]
    image_id = info['Id']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_id):
        raise ValueError('Invalid image identity')
    if (info.get('Os'), info.get('Architecture')) != ('linux', 'amd64'):
        raise ValueError('Only the tested Linux amd64 profile can be bundled')
    if (info.get('Config', {}).get('Labels') or {}).get('org.opencontainers.image.version') != '10.7.1-wl1':
        raise ValueError('Expected the Weblab FRR 10.7.1-wl1 build')
    # No inherited environment, credentials or host paths enter image.json.
    identity = {k: info[k] for k in ('Id', 'Architecture', 'Os')}
    identity['image'] = image
    identity['labels'] = info['Config'].get('Labels') or {}
    (output / 'image.json').write_text(json.dumps(identity, indent=2) + '\n')
    with tempfile.TemporaryDirectory(prefix='weblab-frr-sources-', dir=work_dir) as work:
        root = Path(work)
        container = run('docker', 'create', '--network', 'none', image_id).decode().strip()
        try:
            run('docker', 'cp', container + ':/usr/share/weblab-frr', str(root / 'build'))
            run('docker', 'cp', container + ':/lib/apk/db/installed', str(root / 'runtime-installed'))
        finally:
            run('docker', 'rm', container)
        # The collector operates on a snapshot taken before installing its tools.
        # This disposable container has no Docker socket or active lab volumes.
        subprocess.run(['docker', 'run', '--rm', '--tmpfs', '/tmp:rw,size=2g', '--entrypoint', 'sh',
                        '-v', str(root.resolve()) + ':/out', image_id, '-ec',
                        'apk add --no-cache abuild git build-base >/dev/null; '
                        'python3 -u /usr/share/weblab-frr/collect_sources.py '
                        '/out/runtime-installed /out/dependencies'], check=True)
        (root / 'image.json').write_bytes((output / 'image.json').read_bytes())
        with tarfile.open(output / 'weblab-frr-sources.tar.gz', 'w:gz') as archive:
            for child in sorted(root.iterdir()):
                archive.add(child, arcname=child.name)
    # Export the selected tag, checking it still names the inventoried image.
    if json.loads(run('docker', 'image', 'inspect', image))[0]['Id'] != image_id:
        raise ValueError('Image tag changed during source collection')
    with gzip.open(output / 'weblab-frr-linux-amd64.tar.gz', 'wb', compresslevel=6) as stream:
        process = subprocess.Popen(['docker', 'save', image], stdout=subprocess.PIPE)
        try:
            while chunk := process.stdout.read(1024 * 1024):
                stream.write(chunk)
        finally:
            process.stdout.close()
        if process.wait():
            raise RuntimeError('docker save failed')
    if json.loads(run('docker', 'image', 'inspect', image))[0]['Id'] != image_id:
        raise ValueError('Image tag changed during export; discard this bundle')
    checksums = []
    for path in sorted(output.iterdir()):
        if path.is_file():
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            checksums.append(f'{digest}  {path.name}\n')
    # Written last: its presence distinguishes a complete bundle from an interrupted one.
    (output / 'SHA256SUMS').write_text(''.join(checksums))
    print('Complete bundle:', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='ghcr.io/weblab-network/weblab-frr:10.7.1-wl1')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--work-dir', type=Path, help='Scratch filesystem for dependency collection')
    args = parser.parse_args()
    bundle(args.image, args.output.resolve(), args.work_dir)
