"""Server-enforced console ownership, legacy clients and takeover races."""
import json
import unittest
import test_lab


class ConsoleLockTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests()
        self.fixture.setUp()
        self.fixture.lab.start_all()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.fixture.tearDown()

    def connect(self, node='r1'):
        client = test_lab.WebSocket(self.fixture.port, node, 'netlab.console.v2')
        self.clients.append(client)
        self.assertIn(b'netlab.console.v2', client.headers)
        client.output = b''
        client.lock = None
        self.read_until(client, lambda: client.lock is not None)
        return client

    def read_until(self, client, predicate):
        while not predicate():
            opcode, payload = client.receive()
            if opcode == 1:
                data = json.loads(payload)
                if data['type'] == 'console-lock':
                    client.lock = data
            elif opcode == 2:
                client.output += payload
            elif opcode == 9:
                client.send(payload, opcode=10)
            else:
                self.fail(f'Unexpected opcode: {opcode}')
        return client.lock

    def control(self, client, action, revision=None):
        client.send(json.dumps({'action': action, 'revision': client.lock['revision'] if revision is None else revision}).encode())

    def test_lock_blocks_other_input_including_legacy_and_keeps_output(self):
        owner, observer = self.connect(), self.connect()
        legacy = test_lab.WebSocket(self.fixture.port, 'r1')
        self.clients.append(legacy)
        self.control(owner, 'lock')
        self.read_until(owner, lambda: owner.lock['mine'])
        self.read_until(observer, lambda: observer.lock['locked'])
        observer.send(b'OBSERVER-MUST-NOT-REACH-PTY', opcode=2)
        legacy.send(b'LEGACY-MUST-NOT-REACH-PTY')
        self.control(observer, 'unlock')
        self.read_until(observer, lambda: bool(observer.lock['error']))
        self.assertIn('Only the station', observer.lock['error'])
        # A ping barrier ensures legacy input was parsed before checking output.
        legacy.send(b'barrier', opcode=9)
        while legacy.receive() != (10, b'barrier'):
            pass
        owner.send(b'OWNER-STILL-WRITES', opcode=2)
        for client in (owner, observer):
            self.read_until(client, lambda: b'OWNER-STILL-WRITES' in client.output)
            self.assertNotIn(b'MUST-NOT-REACH', client.output)
        self.control(owner, 'unlock')
        self.read_until(observer, lambda: not observer.lock['locked'])
        observer.send(b'OBSERVER-NOW-WRITES', opcode=2)
        self.read_until(owner, lambda: b'OBSERVER-NOW-WRITES' in owner.output)

    def test_takeover_is_atomic_and_stale_confirmation_cannot_steal(self):
        first, second, third = self.connect(), self.connect(), self.connect()
        self.control(first, 'lock')
        self.read_until(first, lambda: first.lock['mine'])
        self.read_until(second, lambda: second.lock['locked'])
        self.read_until(third, lambda: third.lock['locked'])
        original_revision = second.lock['revision']
        self.control(second, 'lock')
        self.read_until(second, lambda: bool(second.lock['error']))
        self.assertIn('Confirm Take over', second.lock['error'])
        self.control(second, 'takeover', original_revision)
        self.read_until(second, lambda: second.lock['mine'])
        self.read_until(first, lambda: not first.lock['mine'])
        self.assertTrue(first.lock['locked'])
        self.control(third, 'takeover', original_revision)
        self.read_until(third, lambda: bool(third.lock['error']))
        self.assertIn('changed', third.lock['error'])
        self.assertFalse(third.lock['mine'])
        first.send(b'OLD-OWNER-BLOCKED', opcode=2)
        first.send(b'barrier', opcode=9)
        while first.receive() != (10, b'barrier'):
            pass
        second.send(b'NEW-OWNER-WRITES', opcode=2)
        self.read_until(third, lambda: b'NEW-OWNER-WRITES' in third.output)
        self.assertNotIn(b'OLD-OWNER-BLOCKED', third.output)

    def test_disconnect_releases_only_owners_node_and_new_connections_observe(self):
        owner = self.connect()
        other_node = self.connect('r2')
        self.control(owner, 'lock')
        self.read_until(owner, lambda: owner.lock['mine'])
        observer = self.connect()
        self.assertTrue(observer.lock['locked'])
        self.assertFalse(observer.lock['mine'])
        self.assertFalse(other_node.lock['locked'])
        self.control(other_node, 'lock')
        self.read_until(other_node, lambda: other_node.lock['mine'])
        owner.close()
        self.read_until(observer, lambda: not observer.lock['locked'])
        observer.send(b'RELEASED-AFTER-DISCONNECT', opcode=2)
        self.read_until(observer, lambda: b'RELEASED-AFTER-DISCONNECT' in observer.output)
        self.assertTrue(other_node.lock['mine'])
        # Reconnecting starts unlocked and never silently takes the lock back.
        reconnected = self.connect()
        self.assertFalse(reconnected.lock['locked'])

    def test_control_frames_never_become_console_input(self):
        client = self.connect()
        client.send(b'not JSON')
        self.read_until(client, lambda: bool(client.lock['error']))
        self.assertIn('Invalid', client.lock['error'])
        # Literal JSON pasted as binary input remains literal device input.
        pasted = b'{"action":"lock","revision":0}'
        client.send(pasted + b'PASTE-END', opcode=2)
        self.read_until(client, lambda: b'PASTE-END' in client.output)
        self.assertIn(pasted, client.output)
        self.assertNotIn(b'not JSON', client.output)
        self.assertFalse(client.lock['locked'])


if __name__ == '__main__':
    unittest.main()
