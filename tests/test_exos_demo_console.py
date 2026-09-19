from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import build_exos_demo as demo


class BootConsole:
    def __init__(self, *, replay=False, fresh=True):
        self.ready = replay
        self.fresh = fresh
        self.captured = bytearray(b'login: ' if replay else b'')
        self.lock = {'mine': True}
        self.sent = []
        self.output = [] if replay else [
            b'Booting primary image\r\n',
            b'(pending-AAA) login: ',
            b'\r\nAuthentication Service (AAA) on the master node is now available for login.\r\n']

    def acquire(self):
        pass

    def close(self):
        pass

    def receive(self, deadline):
        if not self.output:
            raise AssertionError('Missed the already-replayed login prompt')
        output = self.output.pop(0)
        if b'available for login' in output:
            self.ready = True
        return output

    def send(self, data):
        if not self.ready:
            raise AssertionError('Console input interrupted the bootloader')
        self.sent.append(data)
        if data == b'admin\r':
            self.output.append(b'password: ')
        elif data == b'\r':
            self.output.append(b'Setup [y/N/q]' if self.fresh else b'R1.1 # ')
        elif data == b'q\r':
            self.output.append(b'EXOS-VM.1 # ')


class DemoConsoleTests(unittest.TestCase):
    def login(self, transport, fresh):
        lab = SimpleNamespace(runtime={'node': {'port': 12345}})
        with patch.object(demo, 'ReplayConsole', return_value=transport), patch.object(demo.time, 'sleep'):
            console = demo.EXOSConsole(lab, 'node')
            console.login(fresh=fresh)

    def test_no_input_before_authentication_readiness(self):
        transport = BootConsole()
        self.login(transport, fresh=True)
        self.assertEqual(transport.sent, [b'admin\r', b'\r', b'q\r'])

    def test_restored_login_replayed_during_lock_handshake_is_not_lost(self):
        transport = BootConsole(replay=True, fresh=False)
        self.login(transport, fresh=False)
        self.assertEqual(transport.sent, [b'admin\r', b'\r'])

    def test_developer_menu_continues_after_complete_prompt(self):
        for replay in (False, True):
            with self.subTest(replay=replay):
                transport = MenuConsole(replay=replay)
                self.login(transport, fresh=True)
                self.assertEqual(transport.sent, [b'c\r', b'admin\r', b'\r', b'q\r'])

    def test_repeated_developer_menu_fails_without_repeated_input(self):
        transport = MenuConsole(repeat=True)
        with self.assertRaisesRegex(RuntimeError, 'returned to its developer menu'):
            self.login(transport, fresh=True)
        self.assertEqual(transport.sent, [b'c\r'])

    def test_unknown_prompt_does_not_receive_continue(self):
        transport = BootConsole()
        transport.output = [b'Unknown menu\r\nc) continue with boot process\r\n~>']
        with self.assertRaisesRegex(RuntimeError, 'EXOS console:'):
            self.login(transport, fresh=True)
        self.assertEqual(transport.sent, [])


class MenuConsole(BootConsole):
    menu = b'===== developer menu =====\r\noptions\r\nc) continue with boot process\r\n~>'

    def __init__(self, *, replay=False, repeat=False):
        super().__init__()
        self.boot_output = self.output
        self.repeat = repeat
        self.menu_ready = replay
        self.captured = bytearray(self.menu if replay else b'')
        # Split the prompt across frames to catch premature commands.
        self.output = [] if replay else [self.menu[:-1], self.menu[-1:]]

    def receive(self, deadline):
        output = super().receive(deadline)
        if output == b'>':
            self.menu_ready = True
        return output

    def send(self, data):
        if data == b'c\r':
            if not self.menu_ready:
                raise AssertionError('Continued before the full developer-menu prompt')
            self.sent.append(data)
            self.output = [self.menu] if self.repeat else self.boot_output
            self.menu_ready = False
        else:
            super().send(data)


if __name__ == '__main__':
    unittest.main()
