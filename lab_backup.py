"""Portable stopped-lab backups; device images and runtime resources stay local."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
import uuid
import zipfile

import frr
import vios
import disk_delta

MAX_BYTES = 8 * 1024**3
MAX_FILES = 4096
METADATA_BYTES = 1_000_000
CHUNK = 1024 * 1024
LOG_BYTES = 8 * 1024 * 1024
LOG_NAMES = ('console-output.log.1', 'console-output.log', 'console.log', 'bridge.log')
LOG_README = '''Lab logs (optional; not device state)

Console-output.log.1 precedes console-output.log. These are raw terminal output,
including commands echoed by the device, and may contain ANSI escape sequences.
Recording runs on the server without an open browser console. It begins when a
device is next started with a version supporting recording; older output cannot
be recovered. Each device retains two 4 MiB segments across starts. UTC start/end
markers separate runs; device-generated timestamps use the device's own clock.
Hidden input (such as non-echoed passwords) is not recorded separately.

Console.log and bridge.log are launcher diagnostics. This archive includes at
most the last 8 MiB of each file. Manifest log entries give original sizes and
byte offsets, so omitted prefixes are explicit. Missing files mean no retained
log was available. These are the logs for the node IDs in topology.json; history
can include earlier uses of those IDs. Device names are also in topology.json.

Logs may contain configuration details or secrets printed by devices. Review
before sharing. Import validates these files but does not install them as device
state or merge them into local logs. Extract logs/ from the ZIP to read them.
'''


def log_path(name, nodes):
    if name == 'logs/README.txt':
        return True
    parts = name.split('/')
    return (len(parts) == 4 and parts[:2] == ['logs', 'nodes'] and
            parts[2] in nodes and parts[3] in LOG_NAMES)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b''):
            result.update(chunk)
    return result.hexdigest()


def safe_path(name):
    path = PurePosixPath(name)
    return (isinstance(name, str) and bool(name) and len(name) <= 512 and
            not path.is_absolute() and '\\' not in name and '\x00' not in name and
            all(part not in ('', '.', '..') for part in name.split('/')))


def saved_path(node, relative):
    """Allow device storage only; never import launcher inputs, logs or symlinks."""
    if not safe_path(relative) or node['type'] == 'pc':
        return False
    if frr.is_frr(node):
        return relative == frr.config_path(node, Path('.')).as_posix()
    if vios.is_qemu(node):
        return relative == vios.disk_paths(node, Path('.'))[1].name
    # IOL's NVRAM, VLAN database and emulated flash storage. Diagnostic output
    # (pnp-tech, core dumps) and generated NETMAP/license links are not state.
    parts = relative.split('/')
    return (len(parts) == 1 and relative in (f"nvram_{node['iol_id']:05d}",
                                           f"vlan.dat-{node['iol_id']:05d}",
                                           'vlan.dat', 'startup-config', 'private-config') or
            len(parts) > 1 and parts[0] in ('CRDU', 'flash', 'flash0', 'nvram', 'pnp-info'))


def stopped(lab):
    if lab.runtime:
        raise ValueError('Stop all nodes before exporting or importing saved device state')


def expire(lab, all_files=False):
    for token, (stream, created, _) in list(lab.exports.items()):
        if all_files or time.monotonic() - created > 900:
            stream.close()
            del lab.exports[token]


@contextmanager
def export_source(lab, node, path, size, compact_veos):
    """Prepare an optional lossless encoding without touching the saved disk."""
    image = lab.image_dir / node['image']
    with path.open('rb') as source:
        if (compact_veos and vios.is_veos(node) and size >= CHUNK and
                0 < image.stat().st_size <= disk_delta.MAX_BASE):
            with tempfile.TemporaryFile(dir=lab.directory) as delta, image.open('rb') as base:
                restored_hash = disk_delta.encode(source, base, delta, size, image.stat().st_size)
                encoded_size = delta.tell()
                # Skip the new format if the reference recipe provides little benefit.
                if encoded_size < size * 0.9:
                    delta.seek(0)
                    yield delta, encoded_size, {'encoding': disk_delta.ENCODING,
                                               'restored_size': size, 'restored_sha256': restored_hash}
                    return
            source.seek(0)
        yield source, size, {}


def create(lab, include_logs=False, compact_veos=True):
    if type(include_logs) is not bool:
        raise ValueError('include_logs must be a boolean')
    if type(compact_veos) is not bool:
        raise ValueError('compact_veos must be a boolean')
    with lab.lock:
        stopped(lab)
        expire(lab)
        if len(lab.exports) >= 2:
            raise ValueError('Download the pending backup first, or wait 15 minutes for it to expire')
        stream = tempfile.TemporaryFile(dir=lab.directory)
        try:
            manifest = {'format': 'web-netlab-backup', 'version': 1, 'images': [], 'files': []}
            if any(frr.is_frr(n) for n in lab.topology['nodes']):
                # Pin container content as well as the topology's human-readable tag.
                from lab_server import run, LabError
                identity = frr.archive_image(run, LabError, lab.allow_untested_frr)
                manifest.update(version=2, containers=[identity])
            for node in lab.topology['nodes']:
                vios.boot_image(node, lab.image_dir, ValueError)
            for name in sorted(vios.required_images(lab.topology['nodes'])):
                image = lab.image_dir / name
                manifest['images'].append({'name': name, 'size': image.stat().st_size, 'sha256': digest(image)})
            total = 0
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                archive.writestr('topology.json', json.dumps(lab.topology, indent=2) + '\n')
                for node in lab.topology['nodes']:
                    directory = lab.directory / 'nodes' / node['id']
                    if directory.is_symlink():
                        raise ValueError('Device storage cannot be a symlink')
                    for parent, dirs, files in os.walk(directory, followlinks=False):
                        dirs[:] = [d for d in dirs if not (Path(parent) / d).is_symlink()]
                        for name in sorted(files):
                            path = Path(parent) / name
                            relative = path.relative_to(directory).as_posix()
                            if not saved_path(node, relative):
                                continue
                            if not stat.S_ISREG(path.lstat().st_mode):
                                raise ValueError('Device storage must contain regular files')
                            size = path.stat().st_size
                            total += size
                            if total > MAX_BYTES or len(manifest['files']) >= MAX_FILES - 2:
                                raise ValueError('Backup exceeds the 8 GiB / 4096 file limit')
                            entry = f"nodes/{node['id']}/{relative}"
                            checksum = hashlib.sha256()
                            with export_source(lab, node, path, size, compact_veos) as (source, stored_size, extra):
                                if extra:
                                    entry += disk_delta.SUFFIX
                                    manifest['version'] = 2
                                with archive.open(entry, 'w', force_zip64=True) as target:
                                    for chunk in iter(lambda: source.read(CHUNK), b''):
                                        checksum.update(chunk)
                                        target.write(chunk)
                                manifest['files'].append({'path': entry, 'size': stored_size,
                                                          'sha256': checksum.hexdigest(), **extra})
                if include_logs:
                    manifest['logs'] = []
                    manifest['logs_exported_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                    def add_log(entry, content, original_size):
                        nonlocal total
                        total += len(content)
                        if total > MAX_BYTES or len(manifest['files']) + len(manifest['logs']) >= MAX_FILES - 2:
                            raise ValueError('Backup exceeds the 8 GiB / 4096 file limit')
                        archive.writestr(entry, content)
                        manifest['logs'].append({'path': entry, 'size': len(content),
                                                 'original_size': original_size,
                                                 'offset': original_size - len(content),
                                                 'sha256': hashlib.sha256(content).hexdigest()})
                    readme = LOG_README.encode()
                    add_log('logs/README.txt', readme, len(readme))
                    for node in lab.topology['nodes']:
                        directory = lab.directory / 'nodes' / node['id']
                        for name in LOG_NAMES:
                            path = directory / name
                            if not path.exists() and not path.is_symlink():
                                continue
                            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                            with os.fdopen(fd, 'rb') as source:
                                info = os.fstat(source.fileno())
                                if not stat.S_ISREG(info.st_mode):
                                    raise ValueError('Logs must be regular files')
                                source.seek(max(0, info.st_size - LOG_BYTES))
                                content = source.read(LOG_BYTES)
                            add_log(f"logs/nodes/{node['id']}/{name}", content, info.st_size)
                metadata = json.dumps(manifest)
                if len(metadata.encode()) > METADATA_BYTES:
                    raise ValueError('Backup manifest is too large')
                archive.writestr('manifest.json', metadata)
            if stream.tell() > MAX_BYTES:
                raise ValueError('Compressed backup exceeds 8 GiB')
            stream.seek(0)
            token = uuid.uuid4().hex
            filename = (re.sub(r'[^a-zA-Z0-9_-]', '-', lab.topology['name']) or 'lab') + '.zip'
            lab.exports[token] = (stream, time.monotonic(), filename)
            return {'url': '/api/exports/' + token, 'filename': filename}
        except BaseException:
            stream.close()
            raise


def take(lab, token):
    with lab.lock:
        expire(lab)
        item = lab.exports.pop(token, None)
        if item is None:
            raise ValueError('Backup download expired; export again')
        return item[0], item[2]


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    sync_dir(path.parent)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def recover(directory):
    """Roll back an interrupted installation, or finish committed cleanup."""
    marker = directory / '.restore.json'
    if not marker.exists():
        return
    record = json.loads(marker.read_text())
    if not re.fullmatch(r'\.restore-[a-f0-9]{32}', record['stage']):
        raise ValueError('Invalid restore recovery directory')
    stage = directory / record['stage']
    nodes = directory / 'nodes'
    old = stage / 'old-nodes'
    if record['phase'] != 'committed':
        if old.exists():
            if nodes.exists():
                shutil.rmtree(nodes)
            old.replace(nodes)
        elif not record['had_nodes'] and not (stage / 'nodes').exists() and nodes.exists():
            shutil.rmtree(nodes)
        write_json(directory / 'topology.json', record['topology'])
    # Remove the marker before staging cleanup: repeated crash recovery is safe.
    marker.unlink()
    sync_dir(directory)
    shutil.rmtree(stage, ignore_errors=True)


def install(lab, stage, topology):
    marker = lab.directory / '.restore.json'
    nodes = lab.directory / 'nodes'
    record = {'phase': 'prepared', 'stage': stage.name, 'had_nodes': nodes.exists(), 'topology': lab.topology}
    write_json(marker, record)
    try:
        if nodes.exists():
            nodes.replace(stage / 'old-nodes')
        (stage / 'nodes').replace(nodes)
        sync_dir(stage)
        sync_dir(lab.directory)
        write_json(lab.topology_path, topology)
        record['phase'] = 'committed'
        write_json(marker, record)
    except BaseException:
        recover(lab.directory)
        raise
    lab.topology = topology
    lab.errors.clear()
    recover(lab.directory)


def restore(lab, stream, run):
    with lab.lock:
        stopped(lab)
        stage = lab.directory / ('.restore-' + uuid.uuid4().hex)
        stage.mkdir()
        (stage / 'nodes').mkdir()
        try:
            with zipfile.ZipFile(stream) as archive:
                members = archive.infolist()
                if (len(members) > MAX_FILES or
                        sum(m.file_size for m in members if m.filename not in ('manifest.json', 'topology.json')) > MAX_BYTES):
                    raise ValueError('Backup exceeds the 8 GiB / 4096 file limit')
                names = set()
                for member in members:
                    mode = member.external_attr >> 16
                    if (not safe_path(member.filename) or member.orig_filename != member.filename or
                            member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or
                            member.filename in names or member.is_dir() or
                            stat.S_IFMT(mode) not in (0, stat.S_IFREG) or member.flag_bits & 1):
                        raise ValueError('Unsafe or duplicate backup entry')
                    names.add(member.filename)
                def metadata(name):
                    if name not in names or archive.getinfo(name).file_size > METADATA_BYTES:
                        raise ValueError('Missing or oversized backup metadata')
                    return json.loads(archive.read(name))
                manifest = metadata('manifest.json')
                if (not isinstance(manifest, dict) or manifest.get('format') != 'web-netlab-backup' or
                        type(manifest.get('version')) is not int or manifest['version'] not in (1, 2)):
                    raise ValueError('Unsupported lab backup format')
                original = metadata('topology.json')
                topology = lab.validate(original)
                sources = {n['id']: n for n in original['nodes']}
                targets = {n['id']: n for n in topology['nodes']}
                ids = [n.get('iol_id') for n in original['nodes']]
                if any(type(i) is not int or not 100 <= i <= 1023 for i in ids) or len(set(ids)) != len(ids):
                    raise ValueError('Invalid saved device IDs')
                image_records = manifest.get('images')
                file_records = manifest.get('files')
                log_records = manifest.get('logs', [])
                if not isinstance(image_records, list) or not isinstance(file_records, list) or not isinstance(log_records, list):
                    raise ValueError('Invalid backup manifest')
                has_frr = any(frr.is_frr(n) for n in topology['nodes'])
                containers = manifest.get('containers', [])
                valid = (isinstance(containers, list) and len(containers) == 1 and
                         isinstance(containers[0], dict) and set(containers[0]) == {'name', 'digest'} and
                         containers[0]['name'] == frr.IMAGE and
                         (containers[0]['digest'] == frr.DIGEST or
                          re.fullmatch(r'sha256:[a-f0-9]{64}', str(containers[0]['digest'])))) if has_frr else containers == []
                if not valid or (has_frr and manifest['version'] != 2):
                    raise ValueError('Missing or mismatched FRR container image metadata')
                if has_frr:
                    local = frr.archive_image(run, ValueError, lab.allow_untested_frr)
                    if containers != [local]:
                        raise ValueError('FRR archive requires a different container image; install the exact saved image. '
                                         'Allowing untested images does not bypass archive identity checks.')
                required = vios.required_images(topology['nodes'])
                for node in topology['nodes']:
                    vios.boot_image(node, lab.image_dir, ValueError)
                seen = set()
                for record in image_records:
                    name = record['name']
                    if name not in required or name in seen:
                        raise ValueError('Unexpected or duplicate image in backup')
                    seen.add(name)
                    image = lab.image_dir / name
                    if image.stat().st_size != record['size'] or digest(image) != record['sha256']:
                        raise ValueError(f'Installed image does not match backup: {name}')
                if seen != required:
                    raise ValueError('Backup is missing required image checksums')
                expected = {'topology.json', 'manifest.json'}
                restored_total = 0
                for record in file_records:
                    name = record['path']
                    if name not in names or name in expected:
                        raise ValueError('Missing or duplicate saved device file')
                    expected.add(name)
                    parts = name.split('/', 2)
                    if len(parts) != 3 or parts[0] != 'nodes' or parts[1] not in sources:
                        raise ValueError('Saved file references an unknown device')
                    node_id, relative = parts[1:]
                    encoding = record.get('encoding')
                    if encoding is not None:
                        if (manifest['version'] != 2 or encoding != disk_delta.ENCODING or
                                not vios.is_veos(sources[node_id]) or not relative.endswith(disk_delta.SUFFIX)):
                            raise ValueError('Unsupported saved disk encoding')
                        relative = relative[:-len(disk_delta.SUFFIX)]
                    restored_size = record.get('restored_size') if encoding else record['size']
                    if type(restored_size) is not int or not 0 <= restored_size <= MAX_BYTES:
                        raise ValueError('Invalid restored device file size')
                    restored_total += restored_size
                    if restored_total > MAX_BYTES:
                        raise ValueError('Restored backup exceeds the 8 GiB limit')
                    if not saved_path(sources[node_id], relative):
                        raise ValueError('Unsupported saved device file')
                    member = archive.getinfo(name)
                    if type(record['size']) is not int or record['size'] != member.file_size:
                        raise ValueError('Saved file size mismatch')
                    for prefix in ('nvram_', 'vlan.dat-'):
                        if relative == f"{prefix}{sources[node_id]['iol_id']:05d}":
                            relative = f"{prefix}{targets[node_id]['iol_id']:05d}"
                            break
                    target = stage / 'nodes' / node_id / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        raise ValueError('Duplicate restored device file')
                    checksum = hashlib.sha256()
                    with archive.open(member) as source, target.open('xb') as output:
                        if encoding:
                            image = lab.image_dir / sources[node_id]['image']
                            with image.open('rb') as base:
                                payload_hash, restored_hash = disk_delta.decode(
                                    source, base, output, restored_size, image.stat().st_size)
                            if restored_hash != record['restored_sha256']:
                                raise ValueError('Restored disk checksum mismatch')
                        else:
                            for chunk in iter(lambda: source.read(CHUNK), b''):
                                checksum.update(chunk)
                                output.write(chunk)
                            payload_hash = checksum.hexdigest()
                        output.flush()
                        os.fsync(output.fileno())
                    if payload_hash != record['sha256']:
                        raise ValueError('Saved file checksum mismatch')
                # Logs are checked as untrusted archive data, then discarded.
                # They must never become launcher inputs or writable device files.
                for record in log_records:
                    name = record['path']
                    if name not in names or name in expected or not log_path(name, sources):
                        raise ValueError('Invalid or duplicate log entry')
                    expected.add(name)
                    member = archive.getinfo(name)
                    if type(record['size']) is not int or record['size'] != member.file_size or member.file_size > LOG_BYTES:
                        raise ValueError('Log file size mismatch or limit exceeded')
                    restored_total += member.file_size
                    if restored_total > MAX_BYTES:
                        raise ValueError('Restored backup exceeds the 8 GiB limit')
                    checksum = hashlib.sha256()
                    with archive.open(member) as source:
                        for chunk in iter(lambda: source.read(CHUNK), b''):
                            checksum.update(chunk)
                    if checksum.hexdigest() != record['sha256']:
                        raise ValueError('Log file checksum mismatch')
                if names != expected:
                    raise ValueError('Backup contains unlisted files')
                for node in topology['nodes']:
                    if frr.is_frr(node):
                        config = frr.config_path(node, stage / 'nodes' / node['id'])
                        if config.exists():
                            frr.read_config(config)
                    if vios.is_qemu(node):
                        base, disk = vios.disk_paths(node, stage / 'nodes' / node['id'])
                        if disk.exists():
                            vios.validate_image(lab.image_dir / node['image'], run, ValueError)
                            base.symlink_to(lab.image_dir / node['image'])
                            vios.validate_image(disk, run, ValueError, backing_name=base.name)
                for parent, _, _ in os.walk(stage, topdown=False):
                    sync_dir(Path(parent))
            install(lab, stage, topology)
            return lab.snapshot()
        except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, RuntimeError, EOFError) as exc:
            raise ValueError('Invalid lab backup: ' + str(exc)) from exc
        finally:
            if not (lab.directory / '.restore.json').exists():
                shutil.rmtree(stage, ignore_errors=True)
