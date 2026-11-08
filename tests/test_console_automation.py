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

    def test_latest_read_reaches_prompt_after_long_history(self):
        initial = self.read()
        # Echo enough old text to reproduce a model requesting tiny boot chunks.
        self.send(initial, 'OLD-' + 'x' * 3500 + '\rCURRENT-PROMPT\r')
        history = self.read(max_bytes=128)
        self.assertEqual(history['capture_end'], 'limit')
        with self.assertRaisesRegex(lab_server.LabError, 'no input was sent'):
            self.send(history, 'REJECTED\r')
        latest = self.read(max_bytes=128, latest=True)
        self.assertEqual(latest['capture_end'], 'timeout')
        self.assertEqual(latest['read_mode'], 'latest')
        self.assertGreater(latest['omitted_bytes'], 3000)
        self.assertIn('CURRENT-PROMPT', latest['output'])
        self.assertNotIn('BOOT READY', latest['output'])
        self.assertIn('RX:AFTER-TAIL', self.send(latest, 'AFTER-TAIL\r')['output'])
        with self.assertRaisesRegex(lab_server.LabError, 'Console changed'):
            self.send(latest, 'STALE-TAIL\r')
        self.assertNotIn('REJECTED', self.read()['output'])
        self.assertNotIn('STALE-TAIL', self.read()['output'])

    def test_latest_with_cursor_preserves_every_byte(self):
        first = self.read(max_bytes=4)
        rest = self.read(cursor=first['cursor'], latest=True)
        whole = self.read()
        self.assertEqual(first['output'] + rest['output'], whole['output'])
        self.assertEqual(rest['read_mode'], 'stream')
        self.assertEqual(rest['omitted_bytes'], 0)

    def test_latest_scan_limit_is_bounded_and_resumable(self):
        with patch.object(console_capture, 'LATEST_SCAN_LIMIT', 8):
            first = self.read(max_bytes=4, latest=True)
        self.assertEqual(first['bytes_read'], 8)
        self.assertEqual(first['omitted_bytes'], 4)
        self.assertEqual(first['capture_end'], 'limit')
        self.assertIn('before sending input', first['next_action'])
        with self.assertRaisesRegex(lab_server.LabError, 'Console changed'):
            self.send(first)
        rest = self.read(cursor=first['cursor'], latest=True)
        whole = self.read()
        self.assertEqual(first['output'] + rest['output'], whole['output'][4:])

    def test_latest_read_respects_human_lock(self):
        human = console_capture.Console(self.lab.runtime['r1']['port'])
        self.addCleanup(human.close)
        human.acquire()
        latest = self.read(latest=True)
        self.assertTrue(latest['locked'])
        with self.assertRaisesRegex(lab_server.LabError, 'input lock'):
            self.send(latest)

    def test_validation_stopped_nodes_and_server_opt_in(self):
        for options in ({'wait_seconds': 31}, {'wait_seconds': float('nan')},
                        {'max_bytes': True}, {'max_bytes': 262145}, {'cursor': '../bad'},
                        {'latest': 'true'}, {'latest': 1}):
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
        self.assertEqual(result['input_status'], 'unknown')
        self.assertTrue(human.lock['mine'])




class BulkConsoleTests(unittest.TestCase):
    """Two real shared PTYs: all-target preflight and explicit partial outcomes."""
    def setUp(self):
        self.fixture = test_lab.LabTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.lab = self.fixture.lab
        image = self.fixture.root / 'fake.bin'
        script = image.read_text().replace("b'BOOT READY\\r\\n'", "b'BOOT READY\\r\\nR# '")
        script = script.replace("os.write(1, b'RX:' + data)",
            "os.write(1, data[5:].rstrip(b'\\r') if data.startswith(b'TAIL:') else b'RX:' + data.replace(b'\\r', b'\\r\\n') + b'\\r\\nR# ')")
        image.write_text(script)
        self.api = lab_automation.Automation(self.lab, lab_server.LabError, lab_server.ports, lab_server.run)
        self.lab.automation = self.api
        self.lab.start('r1')
        self.lab.start('r2')

    def arguments(self):
        reads = [self.api.console_read(n, wait_seconds=.1, latest=True) for n in ('r1', 'r2')]
        return {'targets': [{'node_id': r['node_id'], 'expected_cursor': r['cursor']} for r in reads],
                'expected_revision': reads[0]['revision'], 'input': 'show clock\rshow version\r', 'wait_seconds': .1}

    def tail(self, node_id, text):
        r = self.api.console_read(node_id, wait_seconds=.1)
        self.api.console_send(node_id, 'TAIL:\n'+text+'\r', r['revision'], r['cursor'], wait_seconds=.1)

    def assert_unsent(self):
        for node in ('r1', 'r2'):
            self.assertNotIn('show clock', self.api.console_read(node, wait_seconds=.1)['output'])

    def test_real_bulk_http_output_and_lock_release(self):
        result = self.fixture.request('/api/automation/console-send-bulk', 'POST', self.arguments())
        self.assertEqual(result['input_status'], 'sent')
        self.assertEqual(result['command_completion'], 'unverified')
        self.assertEqual([r['node_id'] for r in result['results']], ['r1', 'r2'])
        for capture in result['results']:
            self.assertIn('RX:show clock', capture['output'])
            self.assertIn('show version', capture['output'])
            self.assertFalse(self.api.console_read(capture['node_id'], wait_seconds=.1)['locked'])

    def test_second_stale_or_locked_target_prevents_all_input(self):
        args = self.arguments()
        self.tail('r2', 'R#')
        with self.assertRaisesRegex(lab_server.LabError, 'Console changed'):
            self.api.console_send_bulk(**args)
        human = console_capture.Console(self.lab.runtime['r2']['port'])
        self.addCleanup(human.close)
        human.acquire()
        with self.assertRaisesRegex(lab_server.LabError, 'input lock'):
            self.api.console_send_bulk(**self.arguments())
        self.assert_unsent()
        self.assertFalse(self.api.console_read('r1', wait_seconds=.1)['locked'])

    def test_unknown_pending_pager_config_or_mixed_mode_refused(self):
        for tail in ('Password:', 'R#show clock', '--More--', 'R(config-if)#', 'R>', 'still booting'):
            with self.subTest(tail=tail):
                self.tail('r2', tail)
                with self.assertRaisesRegex(lab_server.LabError, 'prompt|mode'):
                    self.api.console_send_bulk(**self.arguments())
        # Do not look back to the older R# prompt before a later question.
        self.assertNotIn('RX:show clock', self.api.console_read('r1', wait_seconds=.1)['output'])

    def test_mixed_pc_or_vendor_refused_before_any_input(self):
        for kind, image, prompt in (('pc', 'alpine:latest', 'pc:~#'),
                                     ('router', 'quay.io/frrouting/frr:10.7.1', 'R#')):
            with self.subTest(image=image):
                self.lab.node('r2').update(type=kind, image=image)
                self.tail('r2', prompt)
                with self.assertRaisesRegex(lab_server.LabError, 'same device family'):
                    self.api.console_send_bulk(**self.arguments())
        self.assert_unsent()

    def test_bulk_preflight_uses_recent_tail_not_entire_long_history(self):
        for node in ('r1', 'r2'):
            read = self.api.console_read(node, wait_seconds=.1)
            self.api.console_send(node, 'old-'+'x'*3500+'\r', read['revision'], read['cursor'], wait_seconds=.1)
        args = self.arguments()
        with patch.object(console_capture, 'LATEST_SCAN_LIMIT', 16):
            result = self.api.console_send_bulk(**args)
        self.assertEqual(result['input_status'], 'sent')

    def test_pc_only_batch_accepts_stock_shell_prompts(self):
        for node in ('r1', 'r2'):
            self.lab.node(node).update(type='pc', image='alpine:latest')
            self.tail(node, '/ # ')
        args = self.arguments()
        args['input'] = 'ip address\r'
        result = self.api.console_send_bulk(**args)
        self.assertEqual(result['platform'], 'alpine')
        self.assertEqual(result['input_status'], 'sent')

    def test_malformed_arguments_never_send_a_valid_prefix(self):
        args = self.arguments()
        variants = [{'targets': []}, {'targets': args['targets']*5}, {'targets': [args['targets'][0]]*2},
                    {'targets': [{'node_id':'r1', 'expected_cursor':None}]}, {'input':'show clock\r\r'},
                    {'input':'show clock\rnext\nline\r'}, {'input':'show clock\rnext\\r\r'},
                    {'input':'show clock\r\x03\r'}, {'input':'show clock'}, {'input':'x\r'*33},
                    {'input':'x'*4096+'\r'}, {'wait_seconds':6}, {'expected_revision':'stale'}]
        for change in variants:
            with self.subTest(change=change), self.assertRaises(lab_server.LabError):
                self.api.console_send_bulk(**{**args, **change})
        self.assert_unsent()

    def test_stop_between_targets_reports_partial_and_does_not_retry(self):
        checks = []
        def guard():
            checks.append(1)
            if len(checks) == 2:
                raise ValueError('Turn expired')
        self.api._console_bulk_guard = guard
        result = self.api.console_send_bulk(**self.arguments())
        self.assertEqual(result['input_status'], 'partial')
        self.assertEqual([r['input_status'] for r in result['results']], ['sent', 'not_sent'])
        self.assertIn('Turn expired', result['results'][1]['error'])
        self.assertNotIn('show clock', self.api.console_read('r2', wait_seconds=.1)['output'])

    def test_uncertain_send_stops_remaining_nodes_and_keeps_read_cursor(self):
        original = console_capture.Console.send
        def disconnected(console, data, opcode=2):
            if opcode == 2:
                raise OSError('connection lost during send')
            return original(console, data, opcode)
        args = self.arguments()
        with patch.object(console_capture.Console, 'send', disconnected):
            result = self.api.console_send_bulk(**args)
        self.assertEqual([r['input_status'] for r in result['results']], ['unknown', 'not_sent'])
        self.assertEqual(result['results'][0]['cursor'], args['targets'][0]['expected_cursor'])
        self.assertEqual(result['results'][0]['capture_end'], 'limit')
        self.assert_unsent()

    def test_total_capture_budget_preserves_resume_on_each_node(self):
        result = self.api.console_send_bulk(**self.arguments(), max_bytes=8)
        self.assertEqual(result['capture_max_bytes_per_node'], 4)
        for capture in result['results']:
            self.assertEqual(capture['capture_end'], 'limit')
            self.assertEqual(capture['bytes_read'], 4)
            rest = self.api.console_read(capture['node_id'], cursor=capture['cursor'], wait_seconds=.1)
            self.assertIn('RX:show clock\nshow version', capture['output'] + rest['output'])


if __name__ == '__main__':
    unittest.main()
