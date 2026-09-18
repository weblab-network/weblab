"""Saved-state round trips, untrusted archives and interrupted restore recovery."""
import copy
import io
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
import warnings
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

import lab_backup
import lab_server
import vios
import test_lab


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests()
        self.fixture.setUp()
        self.lab = self.fixture.lab
        self.lab.save(self.fixture.topology)
        self.node = self.lab.topology['nodes'][0]
        self.nvram = self.lab.node_dir(self.node['id']) / f"nvram_{self.node['iol_id']:05d}"
        self.contents = b'\x00\xffsaved startup config\n' * 300
        self.nvram.write_bytes(self.contents)

    def tearDown(self):
        lab_backup.expire(self.lab, all_files=True)
        self.fixture.tearDown()

    def backup(self, include_logs=False):
        result = lab_backup.create(self.lab, include_logs)
        stream, _ = lab_backup.take(self.lab, result['url'].rsplit('/', 1)[1])
        with stream:
            return stream.read()

    def restore(self, data):
        return lab_backup.restore(self.lab, io.BytesIO(data), lab_server.run)

    def rewrite(self, data, change):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [(m, archive.read(m)) for m in archive.infolist()]
        entries = change(entries)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for member, content in entries:
                archive.writestr(member, content)
        return output.getvalue()

    def assert_unchanged(self, bad):
        topology = copy.deepcopy(self.lab.topology)
        self.nvram.write_bytes(b'keep this state')
        with self.assertRaises((ValueError, lab_server.LabError)):
            self.restore(bad)
        self.assertEqual(self.lab.topology, topology)
        self.assertEqual(self.nvram.read_bytes(), b'keep this state')
        self.assertFalse(list(self.lab.directory.glob('.restore-*')))

    def test_round_trip_remaps_nvram_and_excludes_runtime(self):
        directory = self.nvram.parent
        (directory / 'console.log').write_text('diagnostic')
        (directory / 'iourc').write_text('license')
        (directory / 'NETMAP').symlink_to(self.nvram)
        (directory / 'flash').mkdir()
        (directory / 'flash' / 'vlan.dat').write_bytes(b'VLAN state')
        vlan_name = f"vlan.dat-{self.node['iol_id']:05d}"
        vlan_contents = b'\x00\xffVLAN database: USERS SERVERS VOICE IOT UNUSED_NATIVE'
        (directory / vlan_name).write_bytes(vlan_contents)
        (directory / 'vlan.dat').write_bytes(b'legacy VLAN database')
        backup = self.backup()
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            self.assertNotIn(f"nodes/{self.node['id']}/console.log", archive.namelist())
            self.assertNotIn(f"nodes/{self.node['id']}/iourc", archive.namelist())
            self.assertEqual(archive.read(f"nodes/{self.node['id']}/{vlan_name}"), vlan_contents)
        self.lab.topology['nodes'][0]['iol_id'] = 1000
        (directory / 'flash' / 'stale').write_text('old')
        (directory / 'vlan.dat-01001').write_bytes(b'stale VLAN database')
        orphan = self.lab.node_dir('old-node') / 'nvram_00999'
        orphan.write_bytes(b'other lab state')
        restored = self.restore(backup)
        self.assertEqual(restored['status'][self.node['id']]['state'], 'stopped')
        self.assertEqual((directory / 'nvram_01000').read_bytes(), self.contents)
        self.assertFalse(self.nvram.exists())
        self.assertFalse((directory / 'flash' / 'stale').exists())
        self.assertEqual((directory / 'flash' / 'vlan.dat').read_bytes(), b'VLAN state')
        self.assertEqual((directory / 'vlan.dat-01000').read_bytes(), vlan_contents)
        self.assertEqual((directory / 'vlan.dat').read_bytes(), b'legacy VLAN database')
        self.assertFalse((directory / vlan_name).exists())
        self.assertFalse((directory / 'vlan.dat-01001').exists())
        self.assertFalse(orphan.parent.exists())

    def test_fresh_restore_remaps_vlan_when_application_id_is_occupied(self):
        old_name = f"vlan.dat-{self.node['iol_id']:05d}"
        (self.nvram.parent / old_name).write_bytes(b'original VLAN database')
        backup = self.backup()
        self.lab.save({'name': 'Empty destination', 'nodes': [], 'links': []})
        shutil.rmtree(self.lab.directory / 'nodes')
        with patch.object(self.lab, 'occupied_ids', return_value=set(range(100, 1000))):
            self.restore(backup)
        directory = self.lab.node_dir(self.node['id'])
        self.assertEqual(self.lab.node(self.node['id'])['iol_id'], 1000)
        self.assertEqual((directory / 'vlan.dat-01000').read_bytes(), b'original VLAN database')
        self.assertEqual((directory / 'nvram_01000').read_bytes(), self.contents)
        self.assertFalse((directory / old_name).exists())

    def test_vlan_database_allowlist_and_checksum(self):
        name = f"vlan.dat-{self.node['iol_id']:05d}"
        for wrong in ('vlan.dat-00001', 'vlan.dat-100', name + '.tmp', name + '/file'):
            self.assertFalse(lab_backup.saved_path(self.node, wrong), wrong)
        path = self.nvram.parent / name
        path.write_bytes(b'VLAN state')
        backup = self.backup()
        corrupt = self.rewrite(backup, lambda items: [
            (m, b'x' * len(data) if m.filename.endswith('/' + name) else data)
            for m, data in items])
        self.assert_unchanged(corrupt)
        self.assertEqual(path.read_bytes(), b'VLAN state')
        path.unlink()
        path.symlink_to(self.nvram)
        with self.assertRaisesRegex(ValueError, 'regular files'):
            self.backup()

    def test_running_lab_rejected_in_both_directions(self):
        backup = self.backup()
        self.lab.runtime['dummy'] = {}
        try:
            with self.assertRaisesRegex(ValueError, 'Stop all'):
                self.backup()
            with self.assertRaisesRegex(ValueError, 'Stop all'):
                self.restore(backup)
        finally:
            self.lab.runtime.clear()

    def test_optional_logs_are_bounded_verified_and_not_installed(self):
        directory = self.nvram.parent
        (directory / 'console-output.log').write_bytes(b'console history\n')
        (directory / 'console-output.log.1').write_bytes(b'previous history\n')
        (directory / 'console.log').write_bytes(b'x' * 9000 + b'last diagnostic\n')
        (directory / 'unrelated.log').write_text('not a lab log')
        with patch.object(lab_backup, 'LOG_BYTES', 4096):
            backup = self.backup(True)
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            logs = json.loads(archive.read('manifest.json'))['logs']
            name = f"logs/nodes/{self.node['id']}/console.log"
            record = next(r for r in logs if r['path'] == name)
            self.assertEqual(record['size'], 4096)
            self.assertEqual(record['offset'], 9016 - 4096)
            self.assertTrue(archive.read(name).endswith(b'last diagnostic\n'))
            self.assertNotIn(f"logs/nodes/{self.node['id']}/unrelated.log", archive.namelist())
        self.restore(backup)
        self.assertEqual(self.nvram.read_bytes(), self.contents)
        self.assertFalse((directory / 'console-output.log').exists())
        self.assertFalse((self.lab.directory / 'logs').exists())
        corrupt = self.rewrite(backup, lambda items: [(m, b'x' * len(data) if m.filename == name else data) for m, data in items])
        self.assert_unchanged(corrupt)
        # Even a listed/checksummed log cannot become a launcher input.
        def unsafe(items):
            result = []
            for member, data in items:
                if member.filename == 'manifest.json':
                    value = json.loads(data)
                    next(r for r in value['logs'] if r['path'] == name)['path'] = 'nodes/r1/console.log'
                    data = json.dumps(value).encode()
                result.append(('nodes/r1/console.log' if member.filename == name else member, data))
            return result
        self.assert_unchanged(self.rewrite(backup, unsafe))

    def test_log_opt_in_validation_and_symlink_rejection(self):
        for value in ('true', 1, None):
            with self.assertRaisesRegex(ValueError, 'boolean'):
                self.backup(value)
        (self.nvram.parent / 'console-output.log').symlink_to(self.nvram)
        with self.assertRaises(OSError):
            self.backup(True)
        self.assertFalse(self.lab.exports)
        # Default exports remain unchanged, without any log archive entries.
        with zipfile.ZipFile(io.BytesIO(self.backup())) as archive:
            self.assertNotIn('logs', json.loads(archive.read('manifest.json')))

    def test_wrong_or_missing_image_rejected(self):
        backup = self.backup()
        image = self.lab.image_dir / self.node['image']
        image.write_bytes(image.read_bytes() + b'\n')
        self.assert_unchanged(backup)
        image.unlink()
        self.assert_unchanged(backup)

    def test_unsafe_archives_and_checksums_leave_lab_untouched(self):
        backup = self.backup()
        for path in ('../escape', '/tmp/escape', 'nodes/r1/../../escape', 'nodes/r1/NETMAP'):
            with self.subTest(path=path):
                self.assert_unchanged(self.rewrite(backup, lambda items: items + [(path, b'bad')]))
        link = zipfile.ZipInfo('nodes/r1/flash/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assert_unchanged(self.rewrite(backup, lambda items: items + [(link, b'/etc/passwd')]))
        self.assert_unchanged(self.rewrite(backup, lambda items: [(m, b'bad' if m.filename.startswith('nodes/') else data) for m, data in items]))
        self.assert_unchanged(self.rewrite(backup, lambda items: [(m, bytes([data[0] ^ 1]) + data[1:] if m.filename.startswith('nodes/') else data) for m, data in items]))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            self.assert_unchanged(self.rewrite(backup, lambda items: items + [items[0]]))
        self.assert_unchanged(b'not a zip')
        with patch.object(lab_backup, 'MAX_BYTES', 1):
            # File count limit independently guards ZIP directory expansion.
            with patch.object(lab_backup, 'MAX_FILES', 1):
                self.assert_unchanged(backup)

    def test_install_failure_rolls_back(self):
        backup = self.backup()
        self.nvram.write_bytes(b'old state')
        original = lab_backup.write_json
        def fail(path, value):
            if path == self.lab.topology_path and value['name'] != 'old lab':
                raise OSError('simulated disk error')
            return original(path, value)
        self.lab.topology['name'] = 'old lab'
        self.lab.persist()
        with patch.object(lab_backup, 'write_json', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'simulated'):
                self.restore(backup)
        self.assertEqual(self.nvram.read_bytes(), b'old state')
        self.assertEqual(json.loads(self.lab.topology_path.read_text())['name'], 'old lab')
        self.assertFalse((self.lab.directory / '.restore.json').exists())

    def test_interrupted_restore_recovery(self):
        stage = self.lab.directory / ('.restore-' + 'a' * 32)
        stage.mkdir()
        record = {'phase': 'prepared', 'stage': stage.name, 'had_nodes': True, 'topology': self.lab.topology}
        lab_backup.write_json(self.lab.directory / '.restore.json', record)
        (self.lab.directory / 'nodes').replace(stage / 'old-nodes')
        self.lab.node_dir('replacement').joinpath('new').write_text('new state')
        lab_backup.write_json(self.lab.topology_path, {'name': 'partial'})
        lab_backup.recover(self.lab.directory)
        lab_backup.recover(self.lab.directory)
        self.assertEqual(self.nvram.read_bytes(), self.contents)
        self.assertFalse((self.lab.directory / 'nodes' / 'replacement').exists())
        self.assertEqual(json.loads(self.lab.topology_path.read_text()), self.lab.topology)

    @unittest.skipUnless(shutil.which('qemu-img') and shutil.which('qemu-io'), 'Requires qemu-utils')
    def test_iosv_overlay_round_trip_and_wrong_backing(self):
        image = self.lab.image_dir / 'test_vios.qcow2'
        lab_server.run('qemu-img', 'create', '-f', 'qcow2', str(image), '16M')
        self.node['image'] = image.name
        self.lab.topology['links'] = []
        self.lab.save(self.lab.topology)
        directory = self.lab.node_dir(self.node['id'])
        disk = vios.disk_for(self.node, directory, self.lab.image_dir, lab_server.run, ValueError)
        lab_server.run('qemu-io', '-f', 'qcow2', '-c', 'write -P 0xab 0 4096', str(disk))
        checksum = lab_backup.digest(image)
        backup = self.backup()
        disk.unlink()
        self.restore(backup)
        lab_server.run('qemu-io', '-f', 'qcow2', '-c', 'read -P 0xab 0 4096', str(disk))
        self.assertEqual(checksum, lab_backup.digest(image))
        self.assertEqual(vios.disk_paths(self.node, directory)[0].resolve(), image.resolve())
        lab_server.run('qemu-img', 'rebase', '-u', '-f', 'qcow2', '-F', 'qcow2', '-b', '/tmp/unexpected.qcow2', str(disk))
        bad = self.backup()
        with self.assertRaisesRegex(ValueError, 'unexpected backing'):
            self.restore(bad)

    def test_http_download_import_and_origin(self):
        base = f'http://127.0.0.1:{self.fixture.port}'
        with urlopen(Request(base + '/api/export', data=b'{}', headers={'Content-Type':'application/json'})) as response:
            exported = json.load(response)
        with urlopen(base + exported['url']) as response:
            self.assertEqual(response.headers['Content-Type'], 'application/zip')
            data = response.read()
        self.nvram.write_bytes(b'changed')
        with urlopen(Request(base + '/api/import', data=data, headers={'Content-Type':'application/zip'})) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.nvram.read_bytes(), self.contents)
        with self.assertRaises(HTTPError) as result:
            urlopen(Request(base + '/api/import', data=data, headers={'Content-Type':'application/zip', 'Origin':'http://evil.invalid'}))
        self.assertEqual(result.exception.code, 400)
        with self.assertRaises(HTTPError):
            urlopen(base + exported['url'])


if __name__ == '__main__':
    unittest.main()
