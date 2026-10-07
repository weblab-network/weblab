"""Filesystem capacity and bounded metadata-only node storage reporting.

Never opens guest disks, follows base-image symlinks, or invokes qemu-img.
"""
import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import time

MIB = 1024**2
GIB = 1024**3
RESERVE = 64 * MIB
START_RESERVE = 256 * MIB
MAX_ENTRIES = 50000


def require_space(path, needed=0, *, operation, reserve=RESERVE, error=ValueError):
    """Conservative preflight, not a reservation against concurrent writers."""
    try:
        free = shutil.disk_usage(path).free
    except OSError as exc:
        raise error(f'Cannot check free space before {operation}: {exc.strerror}') from exc
    if free < needed + reserve:
        raise error(f'Not enough free disk space for {operation}: '
                    f'{free / MIB:.1f} MiB available; at least '
                    f'{(needed + reserve) / MIB:.1f} MiB needed including headroom. '
                    'Free space or use a larger volume; saved node data has not been deleted.')


def filesystems(directory, image_dir):
    result = []
    for label, path in [('Lab data', directory), ('Images', image_dir),
                        ('Temporary files', Path(tempfile.gettempdir()))]:
        try:
            usage = shutil.disk_usage(path)
            level = ('critical' if usage.free < START_RESERVE else
                     'low' if usage.free < 2 * GIB or usage.free < usage.total * .1 else 'ok')
            result.append(dict(label=label, total_bytes=usage.total, free_bytes=usage.free,
                               level=level, filesystem=os.stat(path).st_dev))
        except OSError:
            result.append(dict(label=label, total_bytes=None, free_bytes=None,
                               level='unknown', filesystem=None))
    return result


def usage_tree(path, budget):
    result = dict(allocated_bytes=0, apparent_bytes=0, disk_bytes=0, log_bytes=0,
                  other_bytes=0, files=0, complete=True)
    seen = set()

    def failed(_):
        result['complete'] = False

    try:
        # Anchor traversal to a real directory, including when its name races
        # with replacement. fwalk does not follow symlinked descendants.
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            device = os.fstat(fd).st_dev
            for _, dirs, names, parent in os.fwalk('.', dir_fd=fd, follow_symlinks=False, onerror=failed):
                if budget[0] <= 0:
                    result['complete'] = False
                    break
                info = os.fstat(parent)
                result['allocated_bytes'] += info.st_blocks * 512
                result['other_bytes'] += info.st_blocks * 512
                for name in dirs[:]:
                    budget[0] -= 1
                    if budget[0] <= 0:
                        dirs.clear()
                        result['complete'] = False
                        break
                    try:
                        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode) or info.st_dev != device:
                            dirs.remove(name)
                            if info.st_dev != device:
                                result['complete'] = False
                    except OSError:
                        dirs.remove(name)
                        result['complete'] = False
                for name in names:
                    budget[0] -= 1
                    if budget[0] < 0:
                        result['complete'] = False
                        break
                    try:
                        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except OSError:
                        result['complete'] = False
                        continue
                    identity = (info.st_dev, info.st_ino)
                    if not stat.S_ISREG(info.st_mode) or identity in seen:
                        continue
                    seen.add(identity)
                    size = info.st_blocks * 512
                    category = ('disk_bytes' if name.endswith('.qcow2') else
                                'log_bytes' if name.endswith('.log') or '.log.' in name else 'other_bytes')
                    result[category] += size
                    result['allocated_bytes'] += size
                    result['apparent_bytes'] += info.st_size
                    result['files'] += 1
                budget[0] -= 1
        finally:
            os.close(fd)
    except FileNotFoundError:
        pass
    except OSError:
        result['complete'] = False
    return result


def report(directory, image_dir, nodes):
    names = {node['id']: node['name'] for node in nodes}
    result = dict(measured_at=datetime.now(timezone.utc).isoformat(),
                  filesystems=filesystems(directory, image_dir), nodes=[], complete=True)
    budget = [MAX_ENTRIES]
    root = directory / 'nodes'
    try:
        if root.is_symlink():
            raise ValueError('Node storage root is a symlink')
        with os.scandir(root) as entries:
            for entry in entries:
                if budget[0] <= 0:
                    result['complete'] = False
                    break
                budget[0] -= 1
                if not entry.is_dir(follow_symlinks=False):
                    continue
                usage = usage_tree(entry.path, budget)
                result['nodes'].append(dict(id=entry.name, name=names.get(entry.name, entry.name),
                                            retained=entry.name not in names, **usage))
                result['complete'] &= usage['complete']
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        result['complete'] = False
    result['nodes'].sort(key=lambda n: (-n['allocated_bytes'], n['id']))
    result['node_allocated_bytes'] = sum(n['allocated_bytes'] for n in result['nodes'])
    return result


class Monitor:
    def __init__(self, directory, image_dir):
        self.directory, self.image_dir = directory, image_dir
        self.lock = threading.Lock()
        self.cached = None
        self.expires = 0
        self.key = None

    def report(self, nodes):
        key = tuple((n['id'], n['name']) for n in nodes)
        with self.lock:
            if self.cached is None or key != self.key or time.monotonic() >= self.expires:
                self.cached = report(self.directory, self.image_dir, nodes)
                self.key = key
                self.expires = time.monotonic() + 10
            return copy.deepcopy(self.cached)


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description='Print one metadata-only storage sample as JSON.')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--image-dir', type=Path, required=True)
    args = parser.parse_args()
    # No server lock, guest disk access, or changes to the running workspace.
    nodes = []
    try:
        with (args.data_dir / 'topology.json').open() as stream:
            topology = json.loads(stream.read(1_000_001))
        nodes = topology.get('nodes', [])
    except (OSError, ValueError):
        pass
    print(json.dumps(report(args.data_dir, args.image_dir, nodes)))
