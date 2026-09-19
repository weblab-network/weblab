#!/usr/bin/env python3
"""Initialize the optional demo edition; never replace an existing workspace."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, '/app')
import lab_backup
import lab_server

BUNDLE = Path('/opt/weblab/exos-demo')


def install_image(bundle, images):
    metadata = json.loads((bundle / 'image.json').read_text())
    source = bundle / metadata['filename']
    images.mkdir(parents=True, exist_ok=True)
    target = images / source.name
    if os.path.lexists(target):
        if not target.is_file() or lab_backup.digest(target) != metadata['sha256']:
            raise ValueError(f'Existing {target} differs from the bundled EXOS release; nothing was replaced')
        return
    if lab_backup.digest(source) != metadata['sha256']:
        raise ValueError('Bundled EXOS image checksum failed')
    # Copy to the mounted filesystem and publish atomically without replacing.
    descriptor, name = tempfile.mkstemp(prefix='.exos-image-', dir=images)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as output, source.open('rb') as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o644)
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def initialize(bundle, images, data):
    install_image(bundle, images)
    data.mkdir(parents=True, exist_ok=True)
    marker = data / '.weblab-demo-initializing'
    if marker.exists():
        raise ValueError('Demo initialization was interrupted. Preserve this directory and select a new empty WL_DATA_DIR to retry.')
    if any(data.iterdir()):
        print('Existing workspace found; demo initialization skipped.', flush=True)
        return False
    # A failed seed must not be mistaken for a successfully initialized workspace.
    with marker.open('x') as stream:
        stream.write('EXOS demo initialization in progress\n')
    lab = lab_server.Lab(data, images)
    try:
        lab.save(json.loads((bundle / 'topology.json').read_text()))
        marker.unlink()
        print('EXOS demo initialized. Open Weblab and click Start lab.', flush=True)
        return True
    finally:
        lab.file_lock.close()


if __name__ == '__main__':
    args = lab_server.parse_args()
    initialize(BUNDLE, Path(args.image_dir).resolve(), Path(args.data_dir).resolve())
    os.execv(sys.executable, [sys.executable, '/app/lab_server.py', *sys.argv[1:]])
