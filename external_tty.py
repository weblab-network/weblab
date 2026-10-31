"""Bounded local broker framing and OpenSSH forced console command (stdlib only)."""
import base64
import json
import os
from pathlib import Path
import re
import select
import socket
import struct
import sys
import termios
import time
import tty

MAX_MESSAGE = 131072


class Wire:
    def __init__(self, sock):
        self.sock, self.buffer = sock, bytearray()
        sock.settimeout(1)

    def send(self, value):
        data = json.dumps(value, separators=(',', ':')).encode()
        if len(data) > MAX_MESSAGE:
            raise ValueError('Message too large')
        self.sock.sendall(struct.pack('!I', len(data)) + data)

    def receive(self, timeout=1):
        until = time.monotonic() + timeout
        while True:
            if len(self.buffer) >= 4:
                size = struct.unpack('!I', self.buffer[:4])[0]
                if not 0 < size <= MAX_MESSAGE:
                    raise ValueError('Invalid message length')
                if len(self.buffer) >= size + 4:
                    value = json.loads(self.buffer[4:size+4])
                    del self.buffer[:size+4]
                    if not isinstance(value, dict):
                        raise ValueError('Expected an object')
                    return value
            remaining = max(0, until-time.monotonic())
            if not select.select([self.sock], [], [], remaining)[0]:
                return None
            data = self.sock.recv(16384)
            if not data:
                raise EOFError('Connection ended')
            self.buffer.extend(data)


def connect():
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(3)
    sock.connect(os.environ.get('WL_SSH_SOCKET', '/run/weblab-access/broker.sock'))
    return Wire(sock)


def public_key_from_auth_file(path):
    # OpenSSH supplies this file after successful public-key authentication.
    # AcceptEnv/PermitUserEnvironment are disabled in the service configuration.
    lines = Path(path).read_text()[:32768].splitlines()
    for line in lines:
        fields = line.split()
        if len(fields) >= 3 and fields[0] == 'publickey':
            return ' '.join(fields[1:3])
    raise ValueError('A public-key authenticated session is required')


def safe_text(value):
    return ''.join(c if c.isprintable() else '?' for c in str(value))[:200]


def write_output(data):
    view = memoryview(data)
    deadline = time.monotonic()+3
    while view:
        if not select.select([], [1], [], max(0, deadline-time.monotonic()))[1]:
            raise TimeoutError('SSH client is not reading console output')
        try:
            view = view[os.write(1, view):]
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError('SSH client is not reading console output')


def output(text):
    write_output(text.encode())


class Escape:
    """Ctrl+] opens controls; another Ctrl+] sends one literal byte."""
    def __init__(self):
        self.menu = False

    def feed(self, data):
        events, pending = [], bytearray()
        for index, byte in enumerate(data):
            if self.menu:
                if byte == 29:
                    events.append(('input', b'\x1d'))
                else:
                    events.append(('command', chr(byte)))
                self.menu = False
                # Remaining bytes from a paste/control packet are discarded,
                # never reinterpreted as additional menu actions or guest input.
                return events
            if byte == 29:
                if pending:
                    events.append(('input', bytes(pending))); pending.clear()
                if data[index:] == b'\x1d\x1d':
                    events.append(('input', b'\x1d'))
                    return events
                self.menu = True
                events.append(('menu', None))
                return events
            pending.append(byte)
        if pending:
            events.append(('input', bytes(pending)))
        return events


def attached(wire):
    escape, lock, confirm, menu_lock = Escape(), {}, None, {}
    while True:
        message = wire.receive(.05)
        if message:
            kind = message.get('type')
            if kind == 'output':
                write_output(base64.b64decode(message['data'], validate=True))
            elif kind == 'lock':
                lock = message['state']
                text = lock.get('error') or lock.get('notice')
                status = 'read-only' if lock.get('readonly') or (lock.get('locked') and not lock.get('mine')) else 'input available'
                output('\r\n[Weblab: '+status+('; '+safe_text(text) if text else '')+']\r\n')
                if (lock.get('pending_takeover') or {}).get('direction') == 'incoming':
                    output('[Control requested: Ctrl+] then a to allow, d to deny]\r\n')
            elif kind in ('error', 'ended'):
                output('\r\n[Weblab: '+safe_text(message.get('message', 'Console ended'))+']\r\n')
                return
        if not select.select([0], [], [], 0)[0]:
            continue
        data = os.read(0, 16384)
        if not data:
            return
        for kind, value in escape.feed(data):
            if kind == 'input':
                confirm = None
                wire.send({'op': 'input', 'data': base64.b64encode(value).decode()})
            elif kind == 'menu':
                menu_lock = json.loads(json.dumps(lock))
                output('\r\n[Weblab controls: l lock, u unlock, t take/request, a allow, d deny, c cancel request, q disconnect; Ctrl+] literal; any other key resumes]\r\n')
            else:
                if value == 'q':
                    return
                action = {'l':'lock', 'u':'unlock', 't':'takeover', 'a':'approve-takeover',
                          'd':'deny-takeover', 'c':'cancel-takeover'}.get(value)
                if value == 'y' and confirm is not None:
                    wire.send(confirm); confirm = None; continue
                confirm = None
                if not action or 'revision' not in menu_lock:
                    continue
                if action == 'takeover' and menu_lock.get('takeover_mode') == 'request':
                    action = 'request-takeover'
                control = {'action': action, 'revision': menu_lock['revision']}
                if action in ('approve-takeover', 'deny-takeover', 'cancel-takeover'):
                    control['request_id'] = (menu_lock.get('pending_takeover') or {}).get('id', '')
                request = {'op':'control', 'control':control}
                if action == 'takeover' and menu_lock.get('takeover_mode') == 'confirm':
                    confirm = request
                    escape.menu = True
                    output('\r\n[Take over this console? y confirms; any other key cancels]\r\n')
                else:
                    wire.send(request)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == 'keys':
        wire = connect()
        try:
            wire.send({'op':'keys', 'key':sys.argv[2]+' '+sys.argv[3]})
            result = wire.receive(3) or {}
            if result.get('key'):
                print('restrict,pty '+result['key'])
        finally:
            wire.sock.close()
        return
    command = os.environ.get('SSH_ORIGINAL_COMMAND', '').strip()
    os.set_blocking(1, False)
    if command not in ('', 'list') and not re.fullmatch(r'console [A-Za-z0-9_-]{1,40}', command):
        raise ValueError('Use list or console <node-id>; other commands and subsystems are disabled')
    key = public_key_from_auth_file(os.environ['SSH_USER_AUTH'])
    wire = connect()
    old = None
    try:
        wire.send({'op':'session', 'key':key})
        response = wire.receive(3) or {}
        if response.get('type') != 'nodes':
            raise ValueError(response.get('message', 'SSH access unavailable'))
        if not command.startswith('console '):
            for node in response['nodes']:
                output(f"{safe_text(node['id']):<16} {safe_text(node['name'])} ({node['state']})\r\n")
            if command == 'list':
                return
            if not os.isatty(0):
                raise ValueError('Allocate a terminal with ssh -t')
            output('Node ID (empty to disconnect): ')
            # Keep revocation/expiry effective even at the selection prompt.
            while True:
                message = wire.receive(.05)
                if message and message.get('type') == 'error':
                    raise ValueError(message['message'])
                if select.select([0], [], [], 0)[0]:
                    node_id = os.read(0, 80).decode().strip()
                    break
            if not node_id:
                return
        else:
            node_id = command.split()[1]
        if not os.isatty(0):
            raise ValueError('Use ssh -t to attach a console')
        old = termios.tcgetattr(0)
        tty.setraw(0)
        wire.send({'op':'attach', 'node':node_id})
        output('\r\n[Weblab console: Ctrl+] opens controls; disconnect leaves the node running]\r\n')
        attached(wire)
    finally:
        if old is not None:
            termios.tcsetattr(0, termios.TCSADRAIN, old)
        wire.sock.close()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, EOFError, KeyError) as exc:
        print('Weblab SSH: '+str(exc), file=sys.stderr)
        sys.exit(1)
