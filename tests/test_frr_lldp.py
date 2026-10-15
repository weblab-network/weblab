"""FRR LLDP intent, replay, untrusted neighbors and real local VTY framing."""
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import frr
import lab_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/frr'))
spec = importlib.util.spec_from_file_location('frr_lldp', Path(__file__).resolve().parents[1] /
                                             'packaging/frr/lldp_backend.py')
lldp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lldp)


class LldpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.backend = lldp.Lldp(self.path / 'runtime.json')
        self.apply = patch.object(self.backend, 'apply').start()
        self.session = {}

    def tearDown(self):
        patch.stopall()
        self.tmp.cleanup()

    def command(self, line):
        return self.backend.command(line, self.session)

    def test_default_and_roundtrip_config(self):
        self.assertIn('disabled', self.command('show lldp'))
        self.command('conf t')
        self.command('lldp r')
        self.command('int eth1')
        self.command('no lldp tr')
        self.command('end')
        saved = self.command('do write terminal')
        self.assertIn('lldp run\n', saved)
        self.assertIn('interface eth1\n no lldp transmit\nexit', saved)
        second = lldp.Lldp(self.path / 'second.json')
        with patch.object(second, 'apply') as apply:
            session = {}
            second.command('XFRR_start_configuration', session)
            for line in saved.splitlines():
                second.command(line, session)
            apply.assert_not_called()
            second.command('XFRR_end_configuration', session)
            apply.assert_called_once()
        self.assertEqual(second.config(), saved)
        self.assertEqual(lldp.Lldp(self.path/'runtime.json').config(), saved)

    def test_invalid_port_and_failed_apply_do_not_change_intent(self):
        for name in ('lo', 'eth9', 'eth0.10', 'eth0 vrf blue'):
            self.command('interface ' + name)
            with self.assertRaisesRegex(ValueError, 'eth0 through eth7'):
                self.command('no lldp receive')
        before = self.backend.config()
        self.apply.side_effect = RuntimeError('service failed')
        with self.assertRaisesRegex(RuntimeError, 'service failed'):
            self.command('lldp run')
        self.assertEqual(self.backend.config(), before)
        self.assertIn('service failed', self.command('show lldp'))

    def test_disable_keeps_per_port_policy(self):
        for line in ('lldp run', 'interface eth2', 'no lldp receive', 'end', 'no lldp run'):
            self.command(line)
        config = self.backend.config()
        self.assertNotIn('lldp run', config)
        self.assertIn('no lldp receive', config)
        self.command('interface eth2')
        self.command('lldp receive')
        self.assertNotIn('interface eth2', self.backend.config())

    def test_unrelated_library_commands_can_mention_lldp(self):
        for line in ('frr version 10.7.1_weblab_lldp1', 'description uplink for lldp',
                     'log file /tmp/lldp.log'):
            self.assertEqual(self.command(line), '')
        self.assertFalse(self.backend.state['enabled'])

    def test_neighbor_json_and_control_characters(self):
        document = {'lldp': [{'interface': [{'name': 'eth0', 'chassis': [{
            'name': [{'value': 'R2\x1b\nBAD'}], 'id': [{'value': 'aa:bb'}],
            'descr': [{'value': 'FRR'}]}], 'port': [{'id': [{'value': 'eth1'}],
            'descr': [{'value': 'test'}]}]}]}]}
        self.command('lldp run')
        with patch.object(self.backend, 'cli', return_value=json.dumps(document)):
            result = self.command('sh lldp nei det')
        self.assertIn('R2BAD', result)
        self.assertIn('eth1', result)
        self.assertIn('Chassis ID: aa:bb', result)
        self.assertIn('Management addresses: -', result)
        self.assertIn('Advertised TTL: -', result)
        self.assertIn('Age: -', result)
        self.assertNotIn('\x1b', result)
        self.assertEqual(lldp.clean('x'*10000), 'x'*256)

    def test_neighbor_details_and_enabled_capabilities(self):
        document = {'lldp': [{'interface': [{'name': 'eth0', 'age': '0 day, 00:01:07',
            'chassis': [{'name': [{'value': 'SW1'}], 'capability': [
                {'type': 'Bridge', 'enabled': True}, {'type': 'Router', 'enabled': 'on'},
                {'type': 'Wlan', 'enabled': False}, {'type': 'Station', 'enabled': 'off'}],
                'mgmt-ip': [{'value': '10.0.1.1'}, {'value': 'fe80::1\n\x1b'}]}],
            'port': [{'id': [{'value': 'eth2'}], 'ttl': [{'value': 120}]}]}]}]}
        self.command('lldp run')
        with patch.object(self.backend, 'cli', return_value=json.dumps(document)):
            detail = self.command('show lldp neighbors detail')
            summary = self.command('show lldp neighbors')
        self.assertIn('Capabilities: Bridge, Router', detail)
        self.assertIn('Bridge, Router', summary)
        self.assertNotIn('Wlan', detail)
        self.assertNotIn('Station', detail)
        self.assertIn('Management addresses: 10.0.1.1, fe80::1\n', detail)
        self.assertIn('Advertised TTL: 120 seconds', detail)
        self.assertIn('Age: 0 day, 00:01:07', detail)
        self.assertNotIn('\x1b', detail)

    def test_vty_fragmentation_multiple_commands_and_abort_replay(self):
        path = str(self.path/'test.vty')
        with lldp.Server(path, lldp.Handler) as server:
            server.backend = self.backend
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    client.connect(path)
                    client.sendall(b'show ll')
                    client.sendall(b'dp\0XFRR_start_configuration\0lldp run\0')
                    output = b''
                    while output.count(b'\0\0\0\0') < 3:
                        output += client.recv(4096)
                    self.assertIn(b'LLDP disabled', output)
                    self.apply.assert_not_called()
            finally:
                server.shutdown()
                thread.join(2)
        self.assertFalse(self.backend.state['enabled'])
        self.assertFalse(self.backend.replaying)

    def test_extension_identity_and_exact_archive(self):
        image = {'Id': 'sha256:' + 'b'*64, 'RepoDigests': [frr.DIGEST],
                 'Config': {'Labels': {'network.weblab.frr.extension': 'lldp-1'}}}
        run = lambda *args: json.dumps([image])
        with self.assertRaisesRegex(ValueError, 'WL_ALLOW_UNTESTED_FRR'):
            frr.inspect_image(run, ValueError, False, frr.LLDP_IMAGE)
        self.assertFalse(frr.inspect_image(run, ValueError, True, frr.LLDP_IMAGE)['tested'])
        self.assertEqual(frr.archive_image(run, ValueError, True, frr.LLDP_IMAGE),
                         {'name': frr.LLDP_IMAGE, 'digest': image['Id']})
        image['Config']['Labels'] = {}
        with self.assertRaisesRegex(ValueError, 'extension identity'):
            frr.inspect_image(run, ValueError, True, frr.LLDP_IMAGE)

    def test_recovery_does_not_signal_a_reused_pid(self):
        owner = self.path/'child.json'
        owner.write_text(json.dumps({'pid': 1234, 'started': 'original'}))
        with patch.object(lldp, 'CHILD', owner), \
                patch.object(lldp, 'process_start', return_value='different'), \
                patch.object(lldp.os, 'killpg') as kill:
            lldp.recover_child()
        kill.assert_not_called()
        self.assertFalse(owner.exists())


if __name__ == '__main__':
    unittest.main()
