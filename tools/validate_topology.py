#!/usr/bin/env python3
"""Validate lab JSON without starting a server or writing lab state."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_server import Lab, LabError


class FileValidator:
    def __init__(self, data, image_dir=None):
        self.topology = {'nodes': []}
        self.image_dir = image_dir
        self.data = data

    def occupied_ids(self):
        # Port/ID occupancy is checked again when the user imports the lab.
        return set()

    def catalog(self):
        if self.image_dir is not None:
            return Lab.catalog(self)
        names = {n.get('image') for n in self.data.get('nodes', [])
                 if isinstance(n, dict) and isinstance(n.get('image'), str)} if isinstance(self.data, dict) else set()
        return [{'name': name} for name in names
                if Path(name).name == name and Path(name).suffix in ('.bin', '.qcow2')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--image-dir', type=Path, help='Also require matching installed device image filenames')
    args = parser.parse_args()
    try:
        if args.file.stat().st_size > 1_000_000:
            raise ValueError('Topology JSON exceeds the 1 MB import limit')
        data = json.loads(args.file.read_text())
        result = Lab.validate(FileValidator(data, args.image_dir), data)
    except (LabError, ValueError, TypeError, OSError) as error:
        parser.exit(1, f'Invalid topology: {error}\n')
    mode = 'installed VM/IOL image names checked; Docker images checked on Start' if args.image_dir else 'image availability not checked'
    print(f"Valid: {len(result['nodes'])} nodes, {len(result['links'])} links; {mode}.")
    print('Configuration syntax and device feature support require a device test.')


if __name__ == '__main__':
    main()
