import copy
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import lab_server
import link_fabric
import qmp
import test_lab


class FabricTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='link-relay-')
        self.root = Path(self.tmp.name)
        self.netio = self.root / 'netio'
        self.netio.mkdir()
        self.ends = []
        for number in (201, 202):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.bind(str(self.netio / str(number)))
            sock.settimeout(.15)
            self.ends.append(sock)
        self.record = {'id': 'cable', 'a': {'node': 'r1', 'interface': '1/3', 'id': 201, 'port': 0x31},
                       'b': {'node': 'r2', 'interface': '2/1', 'id': 202, 'port': 0x12}}
        self.fabric = link_fabric.LinkFabric(self.root, self.netio, [self.record], {201, 202})
        self.relay = self.fabric.links['cable']['id_number']

    def tearDown(self):
        self.fabric.close()
        for sock in self.ends:
            sock.close()
        self.tmp.cleanup()

    def send(self, side, frame, header=None):
        number, port = ((201, 0x31), (202, 0x12))[side]
        packet = (header or struct.pack('>HHBBH', self.relay, number, side * 16, port, 0x100)) + frame
        self.ends[side].sendto(packet, str(self.netio / str(self.relay)))

    def receive(self, side, frame):
        data = self.ends[side].recv(65543)
        number, port = ((201, 0x31), (202, 0x12))[side]
        self.assertEqual(data[:8], struct.pack('>HHBBH', number, self.relay, port, side * 16, 0x100))
        self.assertEqual(data[8:], frame)

    def absent(self, side):
        with self.assertRaises(socket.timeout):
            self.ends[side].recv(65543)

    def test_frames_directional_loss_restore_and_no_replay(self):
        # Link-local BPDU, OSPF multicast, VLAN-tagged and maximum-size payloads
        # are opaque: no IP-only filter or multicast snooping in the relay.
        frames = [bytes.fromhex('0180c20000000200000000010026424203') + bytes(48),
                  bytes.fromhex('01005e0000050200000000010800') + bytes(50),
                  bytes.fromhex('ffffffffffff020000000001810000640800') + bytes(46),
                  bytes(65535)]
        for frame in frames:
            self.send(0, frame); self.receive(1, frame)
            self.send(1, frame); self.receive(0, frame)
        frame = frames[0]
        self.fabric.set_blocked('cable', True, False)
        self.send(0, frame); self.absent(1)
        self.send(1, frame); self.receive(0, frame)
        self.fabric.set_blocked('cable', False, True)
        self.send(1, frame); self.absent(0)
        self.send(0, frame); self.receive(1, frame)
        self.fabric.set_blocked('cable', True, True)
        for side in (0, 1):
            self.send(side, frame); self.absent(1-side)
        self.fabric.set_blocked('cable', False, False)
        self.absent(0); self.absent(1)
        self.send(0, frame); self.receive(1, frame)

    def test_fault_does_not_affect_parallel_cable(self):
        self.fabric.close()
        other = copy.deepcopy(self.record)
        other['id'] = 'other'
        other['a'].update(interface='1/2', port=0x21)
        other['b'].update(interface='2/2', port=0x22)
        self.fabric = link_fabric.LinkFabric(self.root, self.netio, [self.record, other], {201, 202})
        self.relay = self.fabric.links['cable']['id_number']
        second = self.fabric.links['other']['id_number']
        self.fabric.set_blocked('cable', True, True)
        frame = bytes(60)
        self.ends[0].sendto(struct.pack('>HHBBH', second, 201, 0, 0x21, 0x100) + frame,
                            str(self.netio / str(second)))
        self.assertEqual(self.ends[1].recv(1000), struct.pack('>HHBBH', 202, second, 0x22, 16, 0x100) + frame)
        self.send(0, frame); self.absent(1)

    def test_unplug_or_unknown_blocks_both_until_reconnected(self):
        for state in ('down', 'unknown'):
            self.fabric.set_carrier('cable', 'a', state)
            for side in (0, 1):
                self.send(side, bytes(60)); self.absent(1-side)
        self.fabric.reset_node('r1')
        self.send(0, bytes(60)); self.receive(1, bytes(60))

    def test_reject_wrong_envelopes_and_oversized_frames(self):
        for header in (struct.pack('>HHBBH', self.relay, 999, 0, 0x31, 0x100),
                       struct.pack('>HHBBH', 111, 201, 0, 0x31, 0x100),
                       struct.pack('>HHBBH', self.relay, 201, 1, 0x31, 0x100),
                       struct.pack('>HHBBH', self.relay, 201, 0, 0x31, 0)):
            self.send(0, bytes(60), header)
        self.send(0, bytes(65536)); self.send(0, bytes(13))
        self.absent(1)
        self.send(0, bytes(60)); self.receive(1, bytes(60))

    def test_cleanup_and_recovery_do_not_unlink_replacement_socket(self):
        path = self.netio / str(self.relay)
        path.unlink()
        foreign = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            foreign.bind(str(path))
            link_fabric.recover(self.root, self.netio)
            self.assertTrue(path.exists())
            self.fabric.close()
            self.assertTrue(path.exists())
        finally:
            foreign.close()

    def test_exhaustion_cleans_partial_allocations(self):
        self.fabric.close()
        links = [self.record, {**self.record, 'id': 'second'}]
        with self.assertRaises(link_fabric.FabricError):
            link_fabric.LinkFabric(self.root, self.netio, links, set(range(1, 1023)))
        self.assertFalse((self.netio / '1023').exists())
        self.assertFalse((self.root / 'link-sockets.json').exists())


class LinkApiTests(unittest.TestCase):
    setUp = test_lab.LabTests.setUp
    tearDown = test_lab.LabTests.tearDown
    node = staticmethod(test_lab.LabTests.node)
    request = test_lab.LabTests.request

    def test_start_can_retry_if_relay_setup_failed_before_netmap_write(self):
        with patch.object(self.lab, 'ensure_fabric', side_effect=link_fabric.FabricError('No free IDs')):
            with self.assertRaisesRegex(lab_server.LabError, 'No free IDs'):
                self.lab.start('r1')
        self.assertFalse(self.lab.runtime)
        self.assertTrue((self.lab.node_dir('r1') / 'NETMAP').is_symlink())
        self.assertFalse((self.lab.directory / 'NETMAP').exists())
        self.lab.start('r1')
        self.assertIn('r1', self.lab.runtime)
        self.assertTrue((self.lab.node_dir('r1') / 'NETMAP').exists())

    def test_live_api_lifecycle_and_runtime_only_state(self):
        link_id = self.lab.topology['links'][0]['id']
        url = f'/api/links/{link_id}/traffic'
        with self.assertRaises(HTTPError):
            self.request(url, 'POST', {'blocked_a_to_b': True, 'blocked_b_to_a': False})
        self.lab.start_all()
        pids = [r['console']['pid'] for r in self.lab.runtime.values()]
        original = self.lab.topology_path.read_bytes()
        mapping = (self.lab.directory / 'NETMAP').read_text()
        self.assertEqual(len(mapping.strip().splitlines()), 2)
        result = self.request(url, 'POST', {'blocked_a_to_b': True, 'blocked_b_to_a': False})
        self.assertTrue(result['link_state'][link_id]['blocked_a_to_b'])
        self.assertFalse(result['link_state'][link_id]['blocked_b_to_a'])
        self.assertEqual([r['console']['pid'] for r in self.lab.runtime.values()], pids)
        self.assertEqual(self.lab.topology_path.read_bytes(), original)
        self.assertEqual(self.request('/api/state')['link_state'], result['link_state'])
        for data in ({}, {'blocked_a_to_b': 1, 'blocked_b_to_a': False},
                     {'blocked_a_to_b': False, 'blocked_b_to_a': False, 'extra': True}):
            with self.assertRaises(HTTPError):
                self.request(url, 'POST', data)
        self.lab.stop('r1', dependents=False)
        self.assertTrue(self.lab.link_states()[link_id]['blocked_a_to_b'])
        self.lab.start('r1')
        self.assertTrue(self.lab.link_states()[link_id]['blocked_a_to_b'])
        self.lab.stop_all()
        self.assertIsNone(self.lab.fabric)
        self.assertFalse(self.lab.link_states()[link_id]['blocked_a_to_b'])
        self.assertFalse((self.lab.directory / 'link-sockets.json').exists())

    def test_pc_relay_mapping_keeps_pseudo_endpoint_on_right(self):
        topology = copy.deepcopy(self.lab.topology)
        topology['nodes'][1].update(type='pc', image='alpine:latest')
        topology['links'][0]['b']['port'] = 'eth0'
        self.lab.save(topology)
        self.lab.start('r1')
        pc_id = self.lab.node('r2')['iol_id']
        lines = (self.lab.directory / 'NETMAP').read_text().strip().splitlines()
        self.assertTrue(lines[1].split()[1].startswith(f'{pc_id}:0/0@'))

    def test_carrier_capability_and_uncertain_failure(self):
        self.lab.start('r1')
        cable = self.lab.topology['links'][0]
        with self.assertRaises(lab_server.LabError):
            self.lab.set_link_carrier(cable['id'], {'side': 'a', 'up': False})
        self.lab.runtime['r1']['qemu_sockets'] = str(self.root / 'qmp-test')
        with patch('lab_server.vios.is_vios', return_value=True), patch('qmp.set_link') as control:
            self.lab.set_link_carrier(cable['id'], {'side': 'a', 'up': False})
            control.assert_called_once_with(self.root / 'qmp-test' / 'qmp', 0, False)
            self.assertEqual(self.lab.link_states()[cable['id']]['carrier_a'], 'down')
            control.side_effect = qmp.QMPError('timeout')
            with self.assertRaisesRegex(lab_server.LabError, 'uncertain'):
                self.lab.set_link_carrier(cable['id'], {'side': 'a', 'up': True})
            self.assertEqual(self.lab.link_states()[cable['id']]['carrier_a'], 'unknown')
            control.side_effect = None
            self.lab.set_link_carrier(cable['id'], {'side': 'a', 'up': True})
            self.assertEqual(self.lab.link_states()[cable['id']]['carrier_a'], 'up')
