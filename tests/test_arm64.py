"""Architecture/profile guards and native builder selection; no guest launches."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import frr
import lab_server
import ll2s_device

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_frr_lldp


class Arm64Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, WL_CONTAINER_ONLY='0'), patch('lab_server.platform.machine', return_value='aarch64'):
            self.lab = lab_server.Lab(Path(self.temp.name)/'data', Path(self.temp.name)/'images', automation=True)

    def tearDown(self):
        self.lab.file_lock.close()
        self.temp.cleanup()

    def test_native_detection_catalog_and_automation(self):
        self.assertTrue(self.lab.snapshot()['container_only'])
        self.assertEqual({i['name'] for i in self.lab.catalog()}, {frr.ARM64_IMAGE, ll2s_device.ARM64_IMAGE})
        with patch.object(self.lab.automation, 'run', return_value='alpine:latest\n'):
            profiles = self.lab.automation.catalog()['profiles']
        self.assertEqual({p['family'] for p in profiles}, {'frr', 'll2s', 'alpine', 'diagram'})
        self.assertTrue(self.lab.automation.state()['container_only'])

    def test_reject_unsupported_before_any_start_or_upload(self):
        for image, kind in [('iol.bin', 'router'), ('vios.qcow2', 'router'), (frr.PUBLIC_IMAGE, 'router'), ('weblab-internet:dev', 'cloud')]:
            with self.subTest(image=image):
                self.lab.topology = {'nodes': [
                    {'id': 'r', 'name': 'Native', 'type': 'router', 'image': frr.ARM64_IMAGE},
                    {'id': 'x', 'name': 'Unsupported', 'type': kind, 'image': image}], 'links': []}
                with patch.object(self.lab, 'start') as start:
                    with self.assertRaisesRegex(lab_server.LabError, 'ARM64 container-only'):
                        self.lab.start_all()
                    start.assert_not_called()
                with self.assertRaisesRegex(lab_server.LabError, 'ARM64 container-only'):
                    self.lab.start('x')
                self.assertFalse(self.lab.runtime)
        with self.assertRaisesRegex(lab_server.LabError, 'uploads are unavailable'):
            self.lab.upload_image('iol.bin', io.BytesIO(b'\x7fELF' + b'x'*100), 104)
        self.assertEqual(list(self.lab.image_dir.iterdir()), [])

    def test_native_tags_keep_image_identity_and_opt_in(self):
        info = {'Id': 'sha256:'+'a'*64, 'Architecture': 'arm64', 'RepoDigests': [],
                'Config': {'Labels': {'network.weblab.frr.extension': 'lldp-1',
                                    'network.weblab.frr.snmp': 'v2c-1',
                                    'network.weblab.frr.vlan': '8021q-1',
                                    'network.weblab.frr.vrrp': 'ipv4-1'}}}
        run = lambda *args: json.dumps([info])
        with self.assertRaisesRegex(ValueError, 'WL_ALLOW_UNTESTED_FRR'):
            frr.inspect_image(run, ValueError, name=frr.ARM64_IMAGE)
        self.assertFalse(frr.inspect_image(run, ValueError, True, frr.ARM64_IMAGE)['tested'])
        self.assertEqual(ll2s_device.archive_image(run, ValueError, ll2s_device.ARM64_IMAGE)['digest'], info['Id'])
        info['Architecture'] = 'amd64'
        with self.assertRaisesRegex(ValueError, 'native arm64'):
            frr.inspect_image(run, ValueError, True, frr.ARM64_IMAGE)
        with self.assertRaisesRegex(ValueError, 'native arm64'):
            ll2s_device.inspect_image(run, ValueError, ll2s_device.ARM64_IMAGE)

    def test_builder_uses_daemon_architecture_not_client(self):
        for raw, expected in [('aarch64', 'arm64'), ('arm64', 'arm64'), ('x86_64', 'amd64'), ('amd64', 'amd64')]:
            with self.subTest(raw=raw), patch('subprocess.check_output', return_value=json.dumps({'Architecture': raw, 'OSType': 'linux'})):
                self.assertEqual(build_frr_lldp.native_architecture(), expected)
        for arch, system in [('armv7l', 'linux'), ('arm64', 'windows')]:
            with patch('subprocess.check_output', return_value=json.dumps({'Architecture': arch, 'OSType': system})):
                with self.assertRaisesRegex(ValueError, 'native Linux'):
                    build_frr_lldp.native_architecture()


if __name__ == '__main__':
    unittest.main()
