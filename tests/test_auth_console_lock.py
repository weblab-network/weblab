"""Role-aware handoff through the real authenticated proxy and PTY wrapper."""
import json
import unittest
import time

import console_capture
import web_auth

import test_lab
import test_console_lock
import test_web_auth


class AuthConsoleLockTests(unittest.TestCase):
    tearDown = test_web_auth.AuthTests.tearDown
    node = staticmethod(test_web_auth.AuthTests.node)
    enable = test_web_auth.AuthTests.enable
    http = test_web_auth.AuthTests.http
    login = test_web_auth.AuthTests.login
    read_until = test_console_lock.ConsoleLockTests.read_until

    def setUp(self):
        test_web_auth.AuthTests.setUp(self)
        self.enable()
        if self._testMethodName == 'test_expiry_duplicate_and_old_decision':
            wrapper = self.root / 'wrapper-ws.pl'
            # Perl time uses whole seconds: a one-second TTL can expire at the
            # next clock boundary before the duplicate/barrier round trip.
            wrapper.write_text(wrapper.read_text().replace('my $takeover_timeout = 60;', 'my $takeover_timeout = 3;'))
        self.admin, self.admin_csrf = self.login('admin')
        self.writer, self.writer_csrf = self.login('user')
        self.guest, _ = self.login('guest')
        self.lab.start('r1')

    def connect(self, cookie, headers=None):
        client = test_lab.WebSocket(self.port, 'r1', 'netlab.console.v2',
                                   headers={'Cookie': cookie, **(headers or {})})
        self.addCleanup(client.close)
        client.output, client.lock = b'', None
        self.read_until(client, lambda: client.lock is not None)
        return client

    def control(self, client, action, revision=None, request_id=None):
        client.send(json.dumps({'action': action,
            'revision': client.lock['revision'] if revision is None else revision,
            'request_id': request_id}).encode())

    def locked(self):
        admin, writer = self.connect(self.admin), self.connect(self.writer)
        self.control(admin, 'lock')
        self.read_until(admin, lambda: admin.lock['mine'])
        self.read_until(writer, lambda: writer.lock['locked'])
        return admin, writer

    def request(self, admin, writer):
        self.control(writer, 'request-takeover')
        self.read_until(writer, lambda: bool(writer.lock['pending_takeover']))
        self.read_until(admin, lambda: bool(admin.lock['pending_takeover']))
        return admin.lock['pending_takeover']['id']

    def test_admin_takeover_and_owner_only_approval_with_no_header_bypass(self):
        writer = self.connect(self.writer, {'X-Weblab-Role': 'admin', 'X-Weblab-Read-Only': '0'})
        self.control(writer, 'lock')
        self.read_until(writer, lambda: writer.lock['mine'])
        admin = self.connect(self.admin)
        self.assertEqual(admin.lock['owner_role'], 'writer')
        self.assertEqual(admin.lock['takeover_mode'], 'immediate')
        self.control(admin, 'takeover')
        self.read_until(admin, lambda: admin.lock['mine'])
        self.read_until(writer, lambda: not writer.lock['mine'])
        self.assertEqual(writer.lock['takeover_mode'], 'request')
        # An old/malicious browser sending direct takeover must also await approval.
        self.control(writer, 'takeover')
        self.read_until(writer, lambda: bool(writer.lock['pending_takeover']))
        self.read_until(admin, lambda: bool(admin.lock['pending_takeover']))
        request = admin.lock['pending_takeover']['id']
        self.assertTrue(admin.lock['mine'])
        for other in (writer, self.connect(self.admin)):
            self.control(other, 'approve-takeover', request_id=request)
            self.read_until(other, lambda: bool(other.lock['error']))
            self.assertIn('Only the administrator holding', other.lock['error'])
        reader = self.connect(self.guest, {'X-Weblab-Role': 'admin', 'X-Weblab-Read-Only': '0'})
        self.assertTrue(reader.lock['readonly'])
        self.assertEqual(reader.lock['takeover_mode'], 'none')
        self.control(reader, 'approve-takeover', request_id=request)
        self.control(reader, 'takeover')
        reader.send(b'READER-MUST-NOT-REACH', 2)
        writer.send(b'WRITER-MUST-NOT-REACH', 2)
        writer.send(b'barrier', 9)
        while writer.receive() != (10, b'barrier'):
            pass
        admin.send(b'ADMIN-STILL-WRITES', 2)
        self.read_until(writer, lambda: b'ADMIN-STILL-WRITES' in writer.output)
        self.assertNotIn(b'MUST-NOT-REACH', writer.output)
        self.control(admin, 'approve-takeover', request_id=request)
        self.read_until(writer, lambda: writer.lock['mine'])
        self.read_until(admin, lambda: not admin.lock['mine'])
        self.assertIsNone(writer.lock['pending_takeover'])
        writer.send(b'APPROVED-WRITER', 2)
        self.read_until(admin, lambda: b'APPROVED-WRITER' in admin.output)

    def test_denial_cancel_and_competing_requests(self):
        admin, writer = self.locked()
        second = self.connect(self.writer)
        request = self.request(admin, writer)
        self.control(second, 'request-takeover')
        self.read_until(second, lambda: bool(second.lock['error']))
        self.assertIn('already awaiting', second.lock['error'])
        self.control(second, 'cancel-takeover', request_id=request)
        self.read_until(second, lambda: 'Only the requesting' in second.lock['error'])
        self.control(admin, 'deny-takeover', request_id=request)
        self.read_until(writer, lambda: 'denied' in writer.lock['notice'])
        self.assertFalse(writer.lock['mine'])
        self.read_until(admin, lambda: admin.lock['pending_takeover'] is None)
        again = self.request(admin, second)
        self.assertNotEqual(again, request)
        self.control(admin, 'approve-takeover', request_id=request)
        self.read_until(admin, lambda: bool(admin.lock['error']))
        self.assertIn('expired or changed', admin.lock['error'])
        self.control(second, 'cancel-takeover', request_id=again)
        self.read_until(admin, lambda: admin.lock['pending_takeover'] is None)
        self.assertTrue(admin.lock['mine'])

    def test_expiry_duplicate_and_old_decision(self):
        admin, writer = self.locked()
        request = self.request(admin, writer)
        expires = writer.lock['pending_takeover']['expires_at']
        self.control(writer, 'request-takeover')
        # A ping barrier flushes the duplicate response without extending its life.
        writer.send(b'barrier', 9)
        while True:
            opcode, payload = writer.receive()
            if (opcode, payload) == (10, b'barrier'):
                break
            if opcode == 1:
                state = json.loads(payload)
                if state.get('pending_takeover'):
                    self.assertEqual(state['pending_takeover']['id'], request)
                    self.assertEqual(state['pending_takeover']['expires_at'], expires)
        self.read_until(writer, lambda: 'expired' in writer.lock['notice'])
        self.read_until(admin, lambda: admin.lock['pending_takeover'] is None)
        self.control(admin, 'approve-takeover', request_id=request)
        self.read_until(admin, lambda: bool(admin.lock['error']))
        self.assertTrue(admin.lock['mine'])
        self.assertFalse(writer.lock['mine'])

    def test_owner_change_invalidates_request_and_stale_decision(self):
        admin, writer = self.locked()
        request = self.request(admin, writer)
        revision = admin.lock['revision']
        other = self.connect(self.admin)
        self.control(other, 'takeover')
        self.read_until(other, lambda: other.lock['mine'])
        self.read_until(writer, lambda: writer.lock['pending_takeover'] is None)
        self.assertFalse(writer.lock['mine'])
        self.control(admin, 'approve-takeover', revision=revision, request_id=request)
        self.read_until(admin, lambda: bool(admin.lock['error']))
        self.assertIn('lock changed', admin.lock['error'])
        self.control(other, 'unlock')
        self.read_until(writer, lambda: not writer.lock['locked'])
        self.control(writer, 'lock')
        self.read_until(writer, lambda: writer.lock['mine'])

    def test_requester_disconnect_reconnect_does_not_inherit_request(self):
        admin, writer = self.locked()
        request = self.request(admin, writer)
        writer.close()
        self.read_until(admin, lambda: admin.lock['pending_takeover'] is None)
        new = self.connect(self.writer)
        self.assertIsNone(new.lock['pending_takeover'])
        new_request = self.request(admin, new)
        self.assertNotEqual(request, new_request)
        self.control(admin, 'approve-takeover', request_id=request)
        self.read_until(admin, lambda: bool(admin.lock['error']))
        self.assertTrue(admin.lock['mine'])
        self.assertFalse(new.lock['mine'])

    def test_logout_invalidates_pending_request_and_owner_lock(self):
        admin, writer = self.locked()
        self.request(admin, writer)
        self.assertEqual(self.http('/api/auth/logout', 'POST', cookie=self.writer, csrf=self.writer_csrf)[0], 200)
        self.read_until(admin, lambda: admin.lock['pending_takeover'] is None)
        self.assertTrue(admin.lock['mine'])
        self.writer, _ = self.login('user')
        new = self.connect(self.writer)
        self.request(admin, new)
        self.assertEqual(self.http('/api/auth/logout', 'POST', cookie=self.admin, csrf=self.admin_csrf)[0], 200)
        self.read_until(new, lambda: not new.lock['locked'])
        self.assertIsNone(new.lock['pending_takeover'])

    def test_borrowed_access_blocks_typing_until_reclaim_unlock_handoff_or_disconnect(self):
        admin, writer = self.locked()
        identity = web_auth.console_identity(web_auth.digest(self.admin.split('=', 1)[1]))
        port = self.lab.runtime['r1']['port']
        def borrow():
            agent = console_capture.Console(port, identity=('admin', identity))
            self.addCleanup(agent.close)
            agent.acquire()
            self.assertTrue(agent.lock['mine'])
            return agent
        agent = borrow()
        rival = console_capture.Console(port, identity=('admin', identity))
        try:
            with self.assertRaisesRegex(ValueError, 'Another Agent'):
                rival.acquire()
        finally:
            rival.close()
        # Read-only metadata must never reveal the private login identity.
        self.assertNotIn(identity, json.dumps(agent.lock))
        self.read_until(admin, lambda:admin.lock['agent_busy'])
        revision = admin.lock['revision']
        self.read_until(writer, lambda:writer.lock['revision'] == revision)
        self.control(writer, 'reclaim')
        self.read_until(writer, lambda:bool(writer.lock['error']))
        self.assertIn('Only the lock owner', writer.lock['error'])
        admin.send(b'HUMAN-MUST-NOT-REACH', 2)
        admin.send(b'barrier', 9)
        while admin.receive() != (10, b'barrier'):
            pass
        agent.send(b'AGENT-STILL-WRITES')
        self.read_until(admin, lambda:b'AGENT-STILL-WRITES' in admin.output)
        self.assertNotIn(b'HUMAN-MUST-NOT-REACH', admin.output)
        self.control(admin, 'reclaim')
        self.read_until(admin, lambda:not admin.lock['agent_busy'])
        self.assertTrue(admin.lock['mine'])
        self.assertGreater(admin.lock['revision'], revision)
        while agent.lock['mine']:
            agent.receive(time.monotonic() + 3)
        agent.send(b'REVOKED-MUST-NOT-REACH')
        with self.assertRaisesRegex(ValueError, 'access changed'):
            while True: agent.receive(time.monotonic() + 3)
        agent.close()
        for action in ('unlock', 'takeover', 'disconnect'):
            with self.subTest(action=action):
                if action != 'unlock':
                    self.control(admin, 'lock')
                    self.read_until(admin, lambda:admin.lock['mine'])
                agent = borrow()
                self.read_until(admin, lambda:admin.lock['agent_busy'])
                self.control(admin, 'reclaim', revision=revision)
                self.read_until(admin, lambda:bool(admin.lock['error']))
                self.assertTrue(admin.lock['agent_busy'], 'Stale reclaim cannot interrupt a new borrow')
                if action == 'unlock':
                    self.control(admin, 'unlock')
                    self.read_until(admin, lambda:not admin.lock['locked'])
                elif action == 'takeover':
                    self.read_until(writer, lambda:writer.lock['revision'] == admin.lock['revision'])
                    self.request(admin, writer)
                    self.control(admin, 'approve-takeover', request_id=admin.lock['pending_takeover']['id'])
                    self.read_until(writer, lambda:writer.lock['mine'])
                else:
                    admin.close()
                    self.read_until(writer, lambda:not writer.lock['locked'])
                while agent.lock['mine']:
                    agent.receive(time.monotonic() + 3)
                agent.send(b'REVOKED-MUST-NOT-REACH')
                with self.assertRaisesRegex(ValueError, 'access changed'):
                    while True: agent.receive(time.monotonic() + 3)
                agent.close()
                if action == 'takeover':
                    self.control(writer, 'unlock')
                    self.read_until(writer, lambda:not writer.lock['locked'])
                    self.read_until(admin, lambda:not admin.lock['locked'])
        writer.send(b'FINAL-BARRIER', 2)
        self.read_until(writer, lambda:b'FINAL-BARRIER' in writer.output)
        self.assertNotIn(b'REVOKED-MUST-NOT-REACH', writer.output)

    def test_browser_cannot_forge_agent_or_owner_identity(self):
        admin, _ = self.locked()
        identity = web_auth.console_identity(web_auth.digest(self.admin.split('=', 1)[1]))
        for cookie in (self.writer, self.admin, self.guest):
            client = self.connect(cookie, {'X-Weblab-Console-Identity':identity,
                                          'X-Weblab-Console-Agent':'1', 'X-Weblab-Role':'admin'})
            self.assertFalse(client.lock['borrowable'])
            self.control(client, 'borrow')
            client.send(b'FORGED-MUST-NOT-REACH', 2)
            client.send(b'barrier', 9)
            while client.receive() != (10, b'barrier'):
                pass
        admin.send(b'STILL-ADMIN', 2)
        self.read_until(admin, lambda:b'STILL-ADMIN' in admin.output)
        self.assertNotIn(b'FORGED-MUST-NOT-REACH', admin.output)


if __name__ == '__main__':
    unittest.main()
