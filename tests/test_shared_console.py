"""Shared PTY console coverage through the real wrapper and HTTP proxy."""
import json
import socket
import unittest

import test_lab


class SharedConsoleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests()
        self.fixture.setUp()
        self.clients = []
        self.fixture.lab.start('r1')

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.fixture.tearDown()

    def connect(self, cursor=None, versioned=True):
        client = test_lab.WebSocket(self.fixture.port, 'r1', 'netlab.console.v1' if versioned else None, cursor)
        self.clients.append(client)
        if versioned:
            self.assertIn(b'Sec-WebSocket-Protocol: netlab.console.v1', client.headers)
            opcode, data = client.receive()
            self.assertEqual(opcode, 1)
            client.start = json.loads(data)
            client.offset = client.start['offset']
        return client

    def until(self, client, marker):
        result = b''
        # The fake PTY prefixes each read with RX:, including reads that split a marker.
        while marker not in result.replace(b'RX:', b''):
            opcode, data = client.receive()
            if opcode == 9:
                client.send(data, opcode=10)
                continue
            self.assertEqual(opcode, 2)
            client.offset += len(data)
            result += data
        return result

    def test_shared_output_bidirectional_input_and_close_isolation(self):
        first = self.connect()
        self.until(first, b'BOOT READY')
        first.send(b'from-first')
        self.until(first, b'from-first')
        second = self.connect()
        replay = self.until(second, b'from-first')
        self.assertIn(b'BOOT READY', replay)
        self.assertFalse(second.start['gap'])
        second.send(b'from-second')
        self.assertEqual(self.until(first, b'from-second'), self.until(second, b'from-second'))
        # Each client has an independent fragment parser.
        first.send(b'fragment-', fin=False)
        second.send(b'other-message')
        self.until(first, b'other-message')
        self.until(second, b'other-message')
        first.send(b'complete', opcode=0)
        self.until(first, b'fragment-complete')
        self.until(second, b'fragment-complete')
        second.close()
        first.send(b'still-connected')
        self.until(first, b'still-connected')
        legacy = self.connect(versioned=False)
        self.assertIn(b'still-connected', legacy.until(b'still-connected'))
        legacy.send(b'legacy-input')
        self.until(first, b'legacy-input')

    def test_native_iol_ctrl_c_becomes_cli_interrupt_for_all_protocols(self):
        for protocol in (None, 'netlab.console.v1', 'netlab.console.v2'):
            with self.subTest(protocol=protocol):
                client = test_lab.WebSocket(self.fixture.port, 'r1', protocol)
                self.clients.append(client)
                client.until(b'BOOT READY')
                client.send(b'begin\x03', opcode=2, fin=False)
                client.send(b'end', opcode=0)
                result = client.until(b'end')
                self.assertIn(b'begin\x1eend', result.replace(b'RX:', b''))
                self.assertNotIn(b'\x03', result)
                client.send(b'\x1a\x1eOK', opcode=2)
                self.assertIn(b'\x1a\x1eOK', client.until(b'OK').replace(b'RX:', b''))

    def test_resume_only_missing_output_and_invalid_client_isolation(self):
        first = self.connect()
        self.until(first, b'BOOT READY')
        cursor = f"{first.start['epoch']}:{first.offset}"
        first.close()
        second = self.connect()
        self.until(second, b'BOOT READY')
        second.send(b'while-away')
        self.until(second, b'while-away')
        resumed = self.connect(cursor)
        self.assertFalse(resumed.start['gap'])
        missed = self.until(resumed, b'while-away')
        self.assertNotIn(b'BOOT READY', missed)
        second.sock.sendall(b'\x81\x01X')  # Unmasked client data is invalid.
        opcode, _ = second.receive()
        self.assertEqual(opcode, 8)
        resumed.send(b'unaffected')
        self.until(resumed, b'unaffected')

    def test_stale_cursor_gap_and_bounded_history(self):
        first = self.connect()
        self.until(first, b'BOOT READY')
        old = f"{first.start['epoch']}:{first.offset}"
        payload = b'x' * 320_000 + b'END-LARGE-INPUT'
        first.send(payload)
        echoed = self.until(first, b'END-LARGE-INPUT')
        # The PTY input queue must preserve large writes even under backpressure.
        self.assertEqual(echoed.replace(b'RX:', b''), payload)
        late = self.connect(old)
        self.assertTrue(late.start['gap'])
        self.assertGreater(late.start['offset'], 0)
        replay = self.until(late, b'END-LARGE-INPUT')
        self.assertEqual(len(replay), 262144)
        self.assertNotIn(b'BOOT READY', replay)
        restarted = self.connect('old-process:0')
        self.assertTrue(restarted.start['gap'])
        self.assertEqual(self.until(restarted, b'END-LARGE-INPUT'), replay)

    def test_slow_reader_does_not_block_another_station(self):
        # Connect directly to the wrapper so the HTTP proxy's socket buffer does
        # not hide a stalled reader. It still uses the same WebSocket helper.
        first = self.connect()
        self.until(first, b'BOOT READY')
        port = self.fixture.lab.runtime['r1']['port']
        slow = socket.create_connection(('127.0.0.1', port), timeout=3)
        self.addCleanup(slow.close)
        slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        slow.sendall(b'GET /console HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
        # Keep generating output while the other client never reads its socket.
        for _ in range(24):
            first.send(b'y' * 250000 + b'END-BLOCK')
            self.until(first, b'END-BLOCK')
        first.send(b'healthy-after-flood')
        self.until(first, b'healthy-after-flood')
        self.assertIn('dropping slow WebSocket client', self.fixture.lab.logs('r1'))
        self.assertTrue(self.fixture.lab.alive(self.fixture.lab.runtime['r1']['console']))


if __name__ == '__main__':
    unittest.main()
