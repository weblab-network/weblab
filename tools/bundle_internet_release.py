#!/usr/bin/env python3
"""Pair a tested Internet release image with its exact sources; never publish."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile


PREFIX = 'weblab-internet-'


def inspect(image):
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', info['Id']) or (info['Os'], info['Architecture']) != ('linux', 'amd64'):
        raise ValueError('Expected a Linux amd64 image with an immutable identity')
    return info


def validate_pair(runtime, sources):
    labels = runtime['Config'].get('Labels') or {}
    other = sources['Config'].get('Labels') or {}
    if labels.get('network.weblab.internet.distribution') != '1':
        raise ValueError('Bundle only the release build target, with collected sources/notices')
    for key in ('org.opencontainers.image.version', 'org.opencontainers.image.revision', 'org.opencontainers.image.source'):
        if not labels.get(key) or labels[key] != other.get(key):
            raise ValueError('Image/source build metadata differ: ' + key)


def copied(image_id, remote, destination):
    container = subprocess.check_output(['docker', 'create', '--network', 'none', image_id]).decode().strip()
    try:
        subprocess.run(['docker', 'cp', container + ':' + remote, str(destination)], check=True)
    finally:
        subprocess.run(['docker', 'rm', container], check=True, stdout=subprocess.DEVNULL)


def file_map(directory):
    result = {}
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise ValueError('Build inputs must not contain symlinks')
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not result:
        raise ValueError('Missing build inputs')
    return result


def bundle(image, sources_image, output):
    runtime, sources = inspect(image), inspect(sources_image)
    validate_pair(runtime, sources)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='weblab-internet-bundle-') as temporary:
        root = Path(temporary)
        copied(sources['Id'], '/sources', root / 'sources')
        copied(runtime['Id'], '/usr/share/weblab-internet', root / 'runtime')
        content, installed = root / 'sources', root / 'runtime'
        if (content/'apk-installed.txt').read_bytes() != (installed/'apk-installed.txt').read_bytes():
            raise ValueError('Source inventory does not match the runtime APK snapshot')
        if json.loads((content/'packages.json').read_text()) != json.loads((installed/'packages.json').read_text()):
            raise ValueError('Source package inventory does not match the release image')
        build_files = file_map(content/'build')
        if build_files != file_map(installed/'build'):
            raise ValueError('Source build inputs differ from the release image')
        identity = {key: runtime[key] for key in ('Id', 'Os', 'Architecture')}
        identity.update(image=image, labels=runtime['Config']['Labels'], build_files=build_files,
                        packages=json.loads((content/'packages.json').read_text()))
        (output / (PREFIX+'image.json')).write_text(json.dumps(identity, indent=2)+'\n')
        shutil.copyfile(installed/'NOTICE', output/(PREFIX+'NOTICE.txt'))
        with tarfile.open(output/(PREFIX+'sources.tar.gz'), 'w:gz') as archive:
            for child in sorted(content.iterdir()):
                archive.add(child, arcname=child.name)
    if inspect(image)['Id'] != runtime['Id'] or inspect(sources_image)['Id'] != sources['Id']:
        raise ValueError('Image tag changed during source bundling')
    with gzip.open(output/(PREFIX+'linux-amd64.tar.gz'), 'wb', compresslevel=6) as stream:
        process = subprocess.Popen(['docker', 'save', image], stdout=subprocess.PIPE)
        try:
            shutil.copyfileobj(process.stdout, stream)
        finally:
            process.stdout.close()
        if process.wait():
            raise RuntimeError('docker save failed')
    if inspect(image)['Id'] != runtime['Id']:
        raise ValueError('Image tag changed during binary export')
    checksums = []
    for path in sorted(output.iterdir()):
        with path.open('rb') as stream:
            checksums.append(hashlib.file_digest(stream, 'sha256').hexdigest() + '  ' + path.name + '\n')
    # Written last. Its absence marks an incomplete bundle.
    (output/(PREFIX+'SHA256SUMS')).write_text(''.join(checksums))
    print('Complete tested binary/source pair:', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--sources-image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    bundle(args.image, args.sources_image, args.output.resolve())
