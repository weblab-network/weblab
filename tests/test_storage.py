"""Sparse disk accounting and capacity rejection without vendor guest boots."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from collections import namedtuple

import lab_backup
import lab_server
import storage
import test_lab

Usage = namedtuple('Usage', 'total used free')


class AccountingTests(unittest.TestCase):
    def test_sparse_files_links_logs_and_retained_nodes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            node = root / 'nodes' / 'r1'
            node.mkdir(parents=True)
            disk = node / 'junos.qcow2'
            with disk.open('wb') as stream:
                stream.write(b'x' * 4096)
                stream.truncate(512 * storage.MIB)
            (node / 'console-output.log').write_bytes(b'log' * 100)
            external = root / 'base.qcow2'
            external.write_bytes(b'b' * 65536)
            (node / 'base.qcow2').symlink_to(external)
            (node / 'outside').symlink_to(root, target_is_directory=True)
            os.link(disk, node / 'duplicate.qcow2')
            (root / 'nodes' / 'removed').mkdir()
            (root / 'nodes' / 'removed' / 'old.log').write_text('retained')
            result = storage.report(root, root, [dict(id='r1', name='R1')])
            current = next(n for n in result['nodes'] if n['id'] == 'r1')
            self.assertEqual(current['disk_bytes'], disk.stat().st_blocks * 512)
            self.assertEqual(current['apparent_bytes'], 512 * storage.MIB + 300)
            self.assertEqual(current['files'], 2)
            self.assertGreater(current['log_bytes'], 0)
            self.assertTrue(result['complete'])
            self.assertTrue(next(n for n in result['nodes'] if n['id'] == 'removed')['retained'])
            self.assertEqual(external.stat().st_size, 65536)

    def test_limits_and_unreadable_filesystems_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'nodes' / 'r1').mkdir(parents=True)
            (root / 'nodes' / 'r1' / 'test.log').write_text('hello')
            with patch.object(storage, 'MAX_ENTRIES', 1):
                self.assertFalse(storage.report(root, root, [])['complete'])
            with patch('storage.shutil.disk_usage', side_effect=PermissionError()):
                self.assertTrue(all(f['level'] == 'unknown' for f in storage.filesystems(root, root)))
                with self.assertRaisesRegex(ValueError, 'Cannot check'):
                    storage.require_space(root, operation='test')

    def test_warning_thresholds_and_cached_measurements(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for free, level in [(128 * storage.MIB, 'critical'), (storage.GIB, 'low'), (4 * storage.GIB, 'ok')]:
                with patch('storage.shutil.disk_usage', return_value=Usage(20 * storage.GIB, 0, free)):
                    self.assertTrue(all(f['level'] == level for f in storage.filesystems(root, root)))
            monitor = storage.Monitor(root, root)
            with patch('storage.report', wraps=storage.report) as report:
                first = monitor.report([])
                first['nodes'].append('caller modification')
                self.assertEqual(monitor.report([])['nodes'], [])
                self.assertEqual(report.call_count, 1)
                monitor.report([dict(id='r1', name='renamed')])
                self.assertEqual(report.call_count, 2)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests()
        self.fixture.setUp()
        self.lab = self.fixture.lab
        node = self.lab.topology['nodes'][0]
        self.saved = self.lab.node_dir(node['id']) / f"nvram_{node['iol_id']:05d}"
        self.saved.write_bytes(b'precious saved state')

    def tearDown(self):
        lab_backup.expire(self.lab, all_files=True)
        self.fixture.tearDown()

    def capacity(self, free):
        return patch('storage.shutil.disk_usage', return_value=Usage(10 * storage.GIB, 0, free))

    def test_upload_and_start_rejected_before_writes(self):
        with self.capacity(16 * storage.MIB):
            with self.assertRaisesRegex(lab_server.LabError, 'image upload'):
                self.lab.upload_image('new.bin', io.BytesIO(b'\x7fELF' + b'x' * 100), 104)
            with self.assertRaisesRegex(lab_server.LabError, 'starting a node'):
                self.lab.start('r1')
        self.assertFalse((self.lab.image_dir / 'new.bin').exists())
        self.assertEqual(list(self.lab.image_dir.glob('.upload-*')), [])
        self.assertEqual(self.lab.runtime, {})
        self.assertEqual(self.saved.read_bytes(), b'precious saved state')

    def test_zip_export_and_restore_rejection_preserve_state(self):
        backup = lab_backup.create(self.lab)
        stream, _ = lab_backup.take(self.lab, backup['url'].rsplit('/', 1)[1])
        with stream:
            data = stream.read()
        with self.capacity(storage.RESERVE):
            with self.assertRaisesRegex(ValueError, 'ZIP export'):
                lab_backup.create(self.lab)
            with self.assertRaisesRegex(ValueError, 'staged ZIP restore'):
                lab_backup.restore(self.lab, io.BytesIO(data), lab_server.run)
        self.assertEqual(self.saved.read_bytes(), b'precious saved state')
        self.assertFalse(list(self.lab.directory.glob('.restore-*')))
        self.assertEqual(self.lab.exports, {})

    def test_storage_api_does_not_open_disks_or_run_commands(self):
        with patch('lab_server.run', side_effect=AssertionError('Must not run guest tools')):
            result = self.fixture.request('/api/storage')
        self.assertEqual(len(result['filesystems']), 3)
        self.assertEqual(result['nodes'][0]['id'], 'r1')
        self.assertEqual(self.lab.runtime, {})
        with self.capacity(16 * storage.MIB):
            self.assertEqual(self.fixture.request('/api/state')['storage']['filesystems'][0]['level'], 'critical')


if __name__ == '__main__':
    unittest.main()
