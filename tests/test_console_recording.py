"""Persistent bounded PTY output, independently of browser clients."""
import time
import unittest
from unittest.mock import patch

import test_lab


class RecordingTests(unittest.TestCase):
    setUp = test_lab.LabTests.setUp
    tearDown = test_lab.LabTests.tearDown
    node = staticmethod(test_lab.LabTests.node)

    def wait_log(self, path, marker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if path.exists() and marker in path.read_bytes():
                return
            time.sleep(.05)
        self.fail(f'Recording did not contain {marker!r}')

    def test_records_without_browser_and_preserves_stop_restart_history(self):
        log = self.lab.node_dir('r1') / 'console-output.log'
        self.lab.start('r1')
        self.wait_log(log, b'BOOT READY')  # No browser connection exists yet.
        ws = test_lab.WebSocket(self.port, 'r1')
        try:
            ws.send(b'FIRST_SESSION')
            ws.until(b'RX:FIRST_SESSION')
        finally:
            ws.close()
        self.lab.stop_all()
        self.assertIn(b'RX:FIRST_SESSION', log.read_bytes())
        self.assertIn(b'Console ended', log.read_bytes())
        self.lab.start('r1')
        self.lab.stop_all()
        self.assertEqual(log.read_bytes().count(b'Console started'), 2)
        self.assertIn(b'RX:FIRST_SESSION', log.read_bytes())

    def test_rotation_and_non_echoed_input(self):
        image = self.root / 'fake.bin'
        image.write_text(image.read_text().replace("b'RX:' + data", "(b'x' * 12000 + b'LATEST OUTPUT' if b'ROTATE' in data else b'PUBLIC RESPONSE')"))
        spawn = self.lab.spawn
        def small_log(node_id, key, command, *args):
            if key == 'console':
                command = command[:2] + ['--transcript-bytes', '4096'] + command[2:]
            return spawn(node_id, key, command, *args)
        with patch.object(self.lab, 'spawn', side_effect=small_log):
            self.lab.start('r1')
        ws = test_lab.WebSocket(self.port, 'r1')
        try:
            ws.send(b'SECRET INPUT')
            ws.until(b'PUBLIC RESPONSE')
            log = self.lab.node_dir('r1') / 'console-output.log'
            self.wait_log(log, b'PUBLIC RESPONSE')
            self.assertNotIn(b'SECRET INPUT', log.read_bytes())
            ws.send(b'ROTATE')
            ws.until(b'LATEST OUTPUT')
        finally:
            ws.close()
        self.lab.stop_all()
        log = self.lab.node_dir('r1') / 'console-output.log'
        previous = log.with_name(log.name + '.1')
        self.assertLessEqual(log.stat().st_size, 4096)
        self.assertLessEqual(previous.stat().st_size, 4096)
        output = previous.read_bytes() + log.read_bytes()
        self.assertIn(b'LATEST OUTPUT', output)
        self.assertNotIn(b'SECRET INPUT', output)
        self.assertNotIn(b'BOOT READY', output)

    def test_recording_failure_does_not_break_console_or_follow_symlink(self):
        target = self.root / 'leave-alone'
        target.write_bytes(b'original')
        (self.lab.node_dir('r1') / 'console-output.log').symlink_to(target)
        self.lab.start('r1')
        ws = test_lab.WebSocket(self.port, 'r1')
        try:
            ws.send(b'STILL CONNECTED')
            self.assertIn(b'STILL CONNECTED', ws.until(b'STILL CONNECTED'))
        finally:
            ws.close()
        self.lab.stop_all()
        self.assertEqual(target.read_bytes(), b'original')
        self.assertIn('recording disabled', self.lab.logs('r1'))


if __name__ == '__main__':
    unittest.main()
