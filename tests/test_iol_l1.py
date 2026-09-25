import socket
import struct
import tempfile
import time
import threading
import copy
import json
import io
import unittest
from pathlib import Path
from unittest.mock import patch

import iol_l1
import link_fabric
import test_lab
import lab_backup


class L1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='iol-l1-unit-')
        self.root = Path(self.tmp.name)
        self.path = self.root / 'l1'; self.path.mkdir()
        self.endpoint = self.path / 'L1201'
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(str(self.endpoint)); self.sock.settimeout(1.5)
        link = {'id': 'wire', 'a': {'node': 's', 'interface': '1/2', 'id': 201, 'port': 0x21},
                'b': {'node': 'r', 'interface': '0/1', 'id': 202, 'port': 0x10}}
        self.fabric = link_fabric.LinkFabric(self.root, self.root/'netio', [link], {201, 202})
        self.l1 = iol_l1.Controller(self.root, self.path, self.fabric)

    def tearDown(self):
        self.l1.close(); self.fabric.close(); self.sock.close(); self.tmp.cleanup()

    def test_signal_is_independent_of_loss_and_selected_carrier(self):
        self.l1.add_node('s', link_fabric.identity(self.endpoint))
        packet = struct.pack('>HHBBBB', 201, self.fabric.links['wire']['id_number'], 0x21, 0, 3, 0)
        self.assertEqual(self.sock.recv(100), packet)
        self.fabric.set_blocked('wire', True, True)
        self.assertEqual(self.sock.recv(100), packet)
        self.l1.set_carrier('wire', 'a', False)
        self.assertEqual(self.fabric.states()['wire']['carrier_a'], 'down')
        self.assertGreater(self.l1.states()[('wire', 'a')]['pending'], 0)
        with self.assertRaises(socket.timeout): self.sock.recv(100)
        self.l1.set_carrier('wire', 'a', True)
        self.assertEqual(self.sock.recv(100), packet)
        self.assertTrue(self.fabric.states()['wire']['blocked_a_to_b'])
        self.l1.remove_node('s')
        with self.assertRaises(socket.timeout): self.sock.recv(100)

    def test_socket_replacement_blocks_traffic_and_requires_restart(self):
        self.l1.add_node('s', link_fabric.identity(self.endpoint)); self.sock.recv(100)
        self.endpoint.unlink()
        other = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            other.bind(str(self.endpoint))
            deadline = time.monotonic()+2
            while not self.l1.states()[('wire', 'a')]['error'] and time.monotonic()<deadline: time.sleep(.05)
            self.assertEqual(self.fabric.states()['wire']['carrier_a'], 'unknown')
            with self.assertRaises(link_fabric.FabricError): self.l1.set_carrier('wire', 'a', True)
            self.assertTrue(self.endpoint.exists())
        finally: other.close()

    def test_recovery_removes_owned_sender(self):
        self.l1.add_node('s', link_fabric.identity(self.endpoint))
        self.sock.recv(100)
        self.l1.stopping.set()
        self.l1.thread.join()
        for sock, _ in self.l1.senders.values():
            sock.close()
        iol_l1.recover(self.root, self.path)
        self.assertEqual(list(self.path.iterdir()), [self.endpoint])
        self.assertFalse((self.root/'l1-senders.json').exists())

    def test_recovery_and_cleanup_preserve_replaced_sender(self):
        self.l1.add_node('s', link_fabric.identity(self.endpoint)); self.sock.recv(100)
        number = self.fabric.links['wire']['id_number']; path = self.path/f'L1{number}'
        path.unlink(); other=socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            other.bind(str(path))
            iol_l1.recover(self.root, self.path)
            self.l1.close()
            self.assertTrue(path.exists())
        finally: other.close()


class L1LifecycleTests(unittest.TestCase):
    setUp = test_lab.LabTests.setUp
    tearDown = test_lab.LabTests.tearDown
    node = staticmethod(test_lab.LabTests.node)

    def prepare(self):
        for topology in (self.topology, self.lab.topology):
            for node in topology['nodes']:
                node['iol_l1'] = True
        self.lab.netl1 = self.root / 'l1'
        image = self.root / 'fake.bin'
        setup = ("if '-l' in sys.argv:\n"
                 "    l1=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)\n"
                 f"    Path({str(self.lab.netl1)!r}).mkdir(exist_ok=True)\n"
                 f"    l1.bind({str(self.lab.netl1 / 'L1')!r}+sys.argv[-1])\n")
        code = image.read_text().replace("os.write(1, b'BOOT READY", setup + "os.write(1, b'BOOT READY")
        image.write_text(code)

    def test_default_off_skips_l1_socket_and_keeps_frame_loss(self):
        self.lab.netl1 = self.root / 'l1'
        self.lab.netl1.mkdir()
        # A foreign L1 socket/file is irrelevant when -l was not requested.
        foreign = self.lab.netl1 / f"L1{self.lab.node('r1')['iol_id']}"
        foreign.write_text('not ours')
        with patch('iol_l1.supported', return_value=True), patch.object(self.lab, 'spawn', wraps=self.lab.spawn) as spawn:
            self.lab.start_all()
            for call in spawn.call_args_list:
                if call.args[1] == 'console':
                    self.assertNotIn('-l', call.args[2])
            self.assertNotIn('iol_l1', self.lab.runtime['r1'])
            state = self.lab.link_states()['cable']
            self.assertTrue(state['carrier_capable_a'])
            self.assertFalse(state['carrier_supported_a'])
            self.lab.set_link_traffic('cable', {'blocked_a_to_b': True, 'blocked_b_to_a': False})
            self.assertTrue(self.lab.link_states()['cable']['blocked_a_to_b'])
            with self.assertRaisesRegex(test_lab.lab_server.LabError, 'Enable cable unplug control'):
                self.lab.set_link_carrier('cable', {'side':'a', 'up':False})
            self.lab.stop_all()
            self.assertTrue(foreign.exists())

    def test_opt_in_validation_persistence_and_zip(self):
        image = self.root / 'cisco_iol-17.18.02.bin'
        image.write_bytes((self.root / 'fake.bin').read_bytes()); image.chmod(0o755)
        data = copy.deepcopy(self.topology)
        data['nodes'][0]['image'] = image.name
        self.lab.save(data)
        self.assertFalse(self.lab.node('r1')['iol_l1'], 'Old topology without a setting defaults off')
        for invalid in (1, 'true', None):
            data['nodes'][0]['iol_l1'] = invalid
            with self.assertRaisesRegex(test_lab.lab_server.LabError, 'boolean'):
                self.lab.save(data)
        data['nodes'][0]['iol_l1'] = True
        self.lab.save(data)
        self.assertTrue(json.loads(self.lab.topology_path.read_text())['nodes'][0]['iol_l1'])
        result = lab_backup.create(self.lab)
        stream, _ = lab_backup.take(self.lab, result['url'].rsplit('/', 1)[1])
        with stream:
            archive = stream.read()
        data['nodes'][0]['iol_l1'] = False
        self.lab.save(data)
        lab_backup.restore(self.lab, io.BytesIO(archive), test_lab.lab_server.run)
        self.assertTrue(self.lab.node('r1')['iol_l1'])
        data['nodes'][0].update(image='fake.bin', iol_l1=True)
        with self.assertRaisesRegex(test_lab.lab_server.LabError, 'tested IOL L1'):
            self.lab.save(data)

    def test_running_node_cannot_change_launch_mode(self):
        with patch('iol_l1.supported', return_value=True):
            self.lab.save(self.topology)
            self.lab.start('r1')
            data = copy.deepcopy(self.lab.topology)
            data['nodes'][0]['iol_l1'] = True
            with self.assertRaisesRegex(test_lab.lab_server.LabError, 'Stop all nodes'):
                self.lab.save(data)
            self.assertNotIn('iol_l1', self.lab.runtime['r1'])

    def test_capability_is_opt_in_and_sender_failure_rolls_back(self):
        self.assertFalse(iol_l1.supported(self.lab.node('r1')))
        self.assertTrue(iol_l1.supported({'type': 'switch', 'image': 'cisco_iol-l2-17.18.02.bin'}))
        self.prepare()
        with patch('iol_l1.supported', return_value=True), patch.object(iol_l1.Controller, 'send', side_effect=OSError('Test L1 failure')):
            with self.assertRaisesRegex(test_lab.lab_server.LabError, 'Test L1 failure'):
                self.lab.start('r1')
        self.assertFalse(self.lab.runtime)
        self.assertIsNone(self.lab.l1)
        self.assertIsNone(self.lab.fabric)
        self.assertEqual(list(self.lab.netl1.iterdir()), [])
        self.assertFalse((self.lab.directory/'l1-senders.json').exists())

    def test_failed_endpoint_keeps_capability_but_disables_carrier_action(self):
        self.prepare()
        shutdown = threading.Event()
        worker = threading.Thread(target=self.lab.monitor, args=(shutdown,))
        with patch('iol_l1.supported', return_value=True):
            self.lab.start_all()
            self.lab.terminate(self.lab.runtime['r1']['console'])
            worker.start()
            try:
                deadline = time.monotonic()+4
                while 'r1' in self.lab.runtime and time.monotonic()<deadline:
                    time.sleep(.05)
                snapshot = self.lab.snapshot()
                self.assertEqual(snapshot['status']['r1']['state'], 'error')
                state = snapshot['link_state']['cable']
                self.assertTrue(state['carrier_capable_a'])
                self.assertFalse(state['carrier_supported_a'])
                self.assertTrue(state['carrier_supported_b'])
                with self.assertRaises(test_lab.lab_server.LabError):
                    self.lab.set_link_carrier('cable', {'side':'a', 'up':False})
            finally:
                shutdown.set()
                worker.join()

    def test_start_restart_and_identity_cleanup(self):
        self.prepare()
        with patch('iol_l1.supported', return_value=True):
            self.lab.start('r1')
            self.assertTrue(self.lab.runtime['r1']['iol_l1_identity'])
            link = self.lab.topology['links'][0]['id']
            self.assertTrue(self.lab.link_states()[link]['carrier_iol_a'])
            self.lab.set_link_carrier(link, {'side':'a', 'up':False})
            self.assertEqual(self.lab.link_states()[link]['carrier_a'], 'down')
            self.lab.stop_all()
            self.assertEqual(list(self.lab.netl1.iterdir()), [])
            self.lab.start('r1')
            self.assertEqual(self.lab.link_states()[link]['carrier_a'], 'up')
            target = self.lab.netl1/f"L1{self.lab.node('r1')['iol_id']}"
            target.unlink(); foreign=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
            try:
                foreign.bind(str(target)); self.lab.stop_all(); self.assertTrue(target.exists())
            finally: foreign.close()
