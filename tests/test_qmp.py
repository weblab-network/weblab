import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

import qmp


class QMPTests(unittest.TestCase):
    def test_negotiation_events_and_device_error(self):
        for command, reject in ((c, r) for c in ('set_link', 'system_powerdown') for r in (False, True)):
            with self.subTest(command=command, reject=reject), tempfile.TemporaryDirectory(prefix='qmp-test-') as directory:
                path = Path(directory) / 'qmp'
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(path)); server.listen(1)
                requests = []
                def serve():
                    with server, server.accept()[0] as client, client.makefile('rb') as reader:
                        client.sendall(b'{"QMP":{"version":{},"capabilities":[]}}\r\n')
                        for _ in range(2):
                            request = json.loads(reader.readline())
                            requests.append(request)
                            client.sendall(b'{"event":"TEST"}\r\n')
                            response = {'id': request['id'], 'return': {}}
                            if reject and request['id'] == 2:
                                response = {'id': 2, 'error': {'desc': 'Device not found'}}
                            client.sendall(json.dumps(response).encode() + b'\r\n')
                worker = threading.Thread(target=serve)
                worker.start()
                def invoke():
                    if command == 'system_powerdown':
                        qmp.powerdown(path)
                    else:
                        qmp.set_link(path, 3, False)
                try:
                    if reject:
                        with self.assertRaisesRegex(qmp.QMPError, 'Device not found'):
                            invoke()
                    else:
                        invoke()
                    self.assertEqual(requests[1]['execute'], command)
                    if command == 'set_link':
                        self.assertEqual(requests[1]['arguments'], {'name': 'nic3', 'up': False})
                    else:
                        self.assertNotIn('arguments', requests[1])
                finally:
                    worker.join(timeout=5)

    def test_powerdown_lost_or_invalid_reply_does_not_replay(self):
        for reply in (b'', b'null\n', b'{"id":2,"error":null}\n'):
            with self.subTest(reply=reply), tempfile.TemporaryDirectory(prefix='qmp-failure-') as directory:
                path = Path(directory) / 'qmp'
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(path))
                server.listen(1)
                requests = []
                def serve():
                    with server, server.accept()[0] as client, client.makefile('rb') as reader:
                        client.sendall(b'{"QMP":{}}\n')
                        requests.append(json.loads(reader.readline()))
                        client.sendall(b'{"id":1,"return":{}}\n')
                        requests.append(json.loads(reader.readline()))
                        if reply:
                            client.sendall(reply)
                worker = threading.Thread(target=serve)
                worker.start()
                try:
                    with self.assertRaises(qmp.QMPError):
                        qmp.powerdown(path)
                    self.assertEqual([r['execute'] for r in requests], ['qmp_capabilities', 'system_powerdown'])
                finally:
                    worker.join(timeout=5)

    @unittest.skipUnless(shutil.which('qemu-system-x86_64'), 'QEMU optional')
    def test_real_qemu_accepts_named_e1000_carrier_changes(self):
        with tempfile.TemporaryDirectory(prefix='qmp-real-') as directory:
            path = Path(directory) / 'qmp'
            process = subprocess.Popen(['qemu-system-x86_64', '-machine', 'pc,accel=tcg', '-S',
                '-m', '64', '-display', 'none', '-nodefaults', '-netdev', 'user,id=n0',
                '-device', 'e1000,id=nic0,netdev=n0', '-qmp', f'unix:{path},server=on,wait=off'],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not path.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(path.exists(), 'QEMU did not create its QMP socket')
                qmp.set_link(path, 0, False)
                qmp.set_link(path, 0, True)
                with self.assertRaises(qmp.QMPError):
                    qmp.set_link(path, 99, False)
            finally:
                process.terminate(); process.communicate(timeout=5)
