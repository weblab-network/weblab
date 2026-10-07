"""MCP console backend against disposable PTYs; never accesses the active lab."""
import threading
import json
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import console_capture
import lab_automation
import lab_server
import test_lab


class ConsoleAutomationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.lab = self.fixture.lab
        self.api = lab_automation.Automation(self.lab, lab_server.LabError, lab_server.ports, lab_server.run)
        self.lab.automation = self.api
        self.lab.start('r1')

    def read(self, **kwargs):
        return self.api.console_read('r1', wait_seconds=0.1, **kwargs)

    def send(self, observed, text='ping -c 3 192.0.2.1\r', **kwargs):
        return self.api.console_send('r1', text, observed['revision'], observed['cursor'],
                                     wait_seconds=0.2, **kwargs)

    def test_read_send_resume_and_release(self):
        initial = self.read()
        self.assertIn('BOOT READY', initial['output'])
        self.assertFalse(initial['locked'])
        result = self.send(initial)
        self.assertEqual(result['input_status'], 'sent')
        self.assertIn('RX:ping -c 3 192.0.2.1', result['output'])
        self.assertNotIn('BOOT READY', result['output'])
        after = self.read(cursor=result['cursor'])
        self.assertEqual(after['output'], '')
        self.assertFalse(after['locked'])
        self.assertEqual(after['cursor'], result['cursor'])

    def test_observe_locked_console_but_never_take_over(self):
        human = console_capture.Console(self.lab.runtime['r1']['port'])
        self.addCleanup(human.close)
        human.acquire()
        observed = self.read()
        self.assertTrue(observed['locked'])
        with self.assertRaisesRegex(lab_server.LabError, 'input lock'):
            self.send(observed, 'SHOULD-NOT-RUN\r')
        human.send(b'HUMAN\r')
        deadline = time.monotonic() + 2
        output = b''
        while b'RX:HUMAN' not in output:
            output += human.receive(deadline)
        self.assertTrue(human.lock['mine'])
        self.assertNotIn('SHOULD-NOT-RUN', self.read()['output'])

    def test_stale_cursor_and_topology_refused(self):
        old = self.read()
        self.send(old, 'FIRST\r')
        with self.assertRaisesRegex(lab_server.LabError, 'Console changed'):
            self.send(old, 'STALE\r')
        fresh = self.read()
        self.lab.topology['name'] = 'Edited'
        with self.assertRaisesRegex(lab_server.LabError, 'Workspace changed'):
            self.send(fresh, 'STALE\r')
        self.assertNotIn('STALE', self.read()['output'])
        self.assertFalse(self.read()['locked'])

    def test_restart_reports_gap_and_refuses_old_cursor(self):
        old = self.read()
        self.lab.stop('r1')
        self.lab.start('r1')
        observed = self.read(cursor=old['cursor'])
        self.assertTrue(observed['gap'])
        with self.assertRaisesRegex(lab_server.LabError, 'Console changed'):
            self.send(old)

    def test_output_limit_can_resume_inside_replayed_frame(self):
        first = self.read(max_bytes=4)
        self.assertEqual(first['output'], 'BOOT')
        self.assertEqual(first['capture_end'], 'limit')
        self.assertEqual(first['bytes_read'], 4)
        rest = self.read(cursor=first['cursor'])
        self.assertIn(' READY', rest['output'])
        self.assertFalse(rest['gap'])
        with self.assertRaisesRegex(lab_server.LabError, 'Console changed'):
            self.send(first)

    def test_validation_stopped_nodes_and_server_opt_in(self):
        for options in ({'wait_seconds': 31}, {'wait_seconds': float('nan')},
                        {'max_bytes': True}, {'max_bytes': 262145}, {'cursor': '../bad'}):
            with self.subTest(options=options), self.assertRaises(lab_server.LabError):
                self.api.console_read('r1', **options)
        observed = self.read()
        for value in ('', 'x' * 4097, None):
            with self.assertRaises(lab_server.LabError):
                self.send(observed, value)
        with self.assertRaises(lab_server.LabError):
            self.api.console_send('r1', 'x', observed['revision'], None)
        with self.assertRaisesRegex(lab_server.LabError, 'Start this node'):
            self.api.console_read('r2')
        for path in ('console-read', 'console-send'):
            with self.assertRaises(HTTPError):
                self.fixture.request('/api/automation/' + path, 'POST',
                                     {'node_id': 'r1'}, origin='https://untrusted.example')
        self.lab.automation = None
        with self.assertRaises(HTTPError):
            self.fixture.request('/api/automation/console-read', 'POST', {'node_id': 'r1'})

    def test_http_roundtrip(self):
        read = self.fixture.request('/api/automation/console-read', 'POST',
                                    {'node_id': 'r1', 'wait_seconds': 0.1})
        result = self.fixture.request('/api/automation/console-send', 'POST', {
            'node_id': 'r1', 'expected_cursor': read['cursor'], 'expected_revision': read['revision'],
            'input': 'HTTP\r', 'wait_seconds': 0.2})
        self.assertIn('RX:HTTP', result['output'])

    def test_read_does_not_block_node_stop(self):
        result = {}
        thread = threading.Thread(target=lambda: result.update(self.api.console_read('r1', wait_seconds=5)))
        thread.start()
        time.sleep(0.1)
        self.lab.stop('r1')
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result['capture_end'], 'disconnected')

    def test_input_send_failure_is_not_safe_to_retry(self):
        observed = self.read()
        original = console_capture.Console.send
        def fail_binary(console, data, opcode=2):
            if opcode == 2:
                raise OSError('connection lost during send')
            return original(console, data, opcode)
        with patch.object(console_capture.Console, 'send', fail_binary):
            result = self.send(observed)
        self.assertEqual(result['input_status'], 'unknown')
        self.assertFalse(self.read()['locked'])

    def test_human_takeover_ends_capture_without_reclaiming_lock(self):
        observed = self.read()
        sent = threading.Event()
        original = console_capture.Console.send
        def notify_send(console, data, opcode=2):
            original(console, data, opcode)
            if opcode == 2:
                sent.set()
        result = {}
        with patch.object(console_capture.Console, 'send', notify_send):
            worker = threading.Thread(target=lambda: result.update(self.api.console_send(
                'r1', 'TEST\r', observed['revision'], observed['cursor'], wait_seconds=5)))
            worker.start()
            self.assertTrue(sent.wait(2))
            human = console_capture.Console(self.lab.runtime['r1']['port'])
            self.addCleanup(human.close)
            deadline = time.monotonic() + 2
            while human.lock is None:
                human.receive(deadline)
            self.assertTrue(human.lock['locked'])
            human.send(json.dumps({'action': 'takeover', 'revision': human.lock['revision']}).encode(), 1)
            while not human.lock['mine']:
                human.receive(deadline)
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(result['capture_end'], 'lock_lost')
        self.assertTrue(human.lock['mine'])


if __name__ == '__main__':
    unittest.main()
