"""Real QEMU UART/PTY regression without vendor images, KVM or guest boot."""
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

import vios
from test_lab import WebSocket

ROOT = Path(__file__).resolve().parents[1]


class QemuConsoleTests(unittest.TestCase):
    def console_args(self, image):
        node = {'id': 'console-test', 'type': 'switch', 'image': image,
                'memory': 1024, 'ethernet': 2}
        args = vios.command(node, Path('/unused/disk'), Path('/unused/sockets'), boot=Path('/unused/boot'))
        self.assertEqual(args.count('-serial'), 1)
        self.assertEqual(args.count('-chardev'), 1)
        self.assertEqual(args[args.index('-monitor')+1], 'none')
        return [part for option in ('-serial', '-chardev')
                for part in (option, args[args.index(option)+1])]

    def test_every_profile_delivers_control_bytes_to_guest(self):
        for image in ('cisco_vios-159-3.M12.qcow2', 'vios_l2-test.qcow2',
                      'EXOS-VM_33.1.1.31.qcow2', 'vEOS64-lab-4.36.1F.qcow2',
                      'vJunos-switch-26.2R1.7.qcow2', 'vJunosEvolved-26.2R1.7-EVO.qcow2'):
            with self.subTest(image=image):
                self.assertEqual(self.console_args(image),
                                 ['-serial', 'chardev:console', '-chardev', 'stdio,id=console,signal=off'])

    @unittest.skipUnless(shutil.which('qemu-system-x86_64'), 'Requires QEMU, no vendor image or KVM')
    def test_websocket_ctrl_c_reaches_real_qemu_uart_and_host_stop_still_works(self):
        # qtest reads the emulated UART directly while CPUs are paused. The
        # normal console wrapper owns the real controlling PTY, just as in a lab.
        with tempfile.TemporaryDirectory(prefix='wl-qemu-console-') as temp:
            path = Path(temp) / 'qtest'
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            command = ['perl', str(ROOT/'wrapper-ws.pl'), '--bind', '127.0.0.1',
                       '--path', '/ws/test', '-p', str(port), '-m', shutil.which('qemu-system-x86_64'), '--',
                       '-machine', 'pc,accel=tcg', '-m', '32', '-S', '-display', 'none', '-monitor', 'none',
                       '-qtest', f'unix:{path},server=on,wait=off',
                       *self.console_args('EXOS-VM_33.1.1.31.qcow2')]
            with (Path(temp)/'log').open('w+') as log:
                process = subprocess.Popen(command, stdout=log, stderr=log)
                test_socket, ws = socket.socket(socket.AF_UNIX), None
                test_socket.settimeout(3)
                try:
                    deadline = time.monotonic()+5
                    while not path.exists():
                        self.assertIsNone(process.poll())
                        self.assertLess(time.monotonic(), deadline, 'QEMU startup timeout')
                        time.sleep(.02)
                    test_socket.connect(str(path))
                    reader = test_socket.makefile('rb')
                    self.addCleanup(reader.close)
                    def uart_read(register):
                        test_socket.sendall(f'inb {register:#x}\n'.encode())
                        reply = reader.readline()
                        self.assertTrue(reply.startswith(b'OK '), reply)
                        return int(reply.split()[1], 16)
                    # Wait for QEMU's stdio initialization before sending input.
                    uart_read(0x3fd)
                    for protocol in (None, 'netlab.console.v1', 'netlab.console.v2'):
                        ws = WebSocket(port, 'test', protocol=protocol)
                        for value in (3, 26, 28, ord('x')):  # Ctrl+C/Z/\ and ordinary text
                            ws.send(bytes([value]), opcode=2)
                            deadline = time.monotonic()+3
                            while not uart_read(0x3fd) & 1:
                                self.assertIsNone(process.poll(), 'Console input killed QEMU')
                                self.assertLess(time.monotonic(), deadline, 'Control byte did not reach UART')
                                time.sleep(.01)
                            self.assertEqual(uart_read(0x3f8), value)
                        ws.close()
                        ws = None
                    self.assertIsNone(process.poll())
                    process.terminate()
                    process.wait(timeout=5)
                    log.seek(0)
                    output = log.read()
                    self.assertNotIn('terminating on signal 2', output)
                    self.assertIn('child exited with status 0', output)
                finally:
                    if ws:
                        ws.close()
                    test_socket.close()
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
