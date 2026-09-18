"""Shared console transport and export orchestration with platform handlers."""
import base64
import copy
import hashlib
import json
import os
import re
import socket
import struct
import time
from cisco_config import CiscoIOS
import vios

ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')


def clean_output(data):
    text = ANSI.sub('', data.decode('utf-8', errors='replace'))
    # IOSv's PTY emits CR-CR-LF. Render backspaces and overwritten cells so
    # pager erasure does not become indentation on the next configuration line.
    text = re.sub(r'\r+\n', '\n', text)
    lines, line, cursor = [], [], 0
    for char in text:
        if char == '\n':
            lines.append(''.join(line).rstrip())
            line, cursor = [], 0
        elif char == '\r':
            cursor = 0
        elif char == '\x08':
            cursor = max(0, cursor - 1)
        elif char != '\x00':
            if cursor < len(line):
                line[cursor] = char
            else:
                line.append(char)
            cursor += 1
    lines.append(''.join(line).rstrip())
    return '\n'.join(lines)


class Console:
    def __init__(self, port):
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        self.buffer = b''
        self.lock = None
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            request = ('GET /console HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                       'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                       f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Protocol: netlab.console.v2\r\n\r\n')
            self.sock.sendall(request.encode())
            header = b''
            while not header.endswith(b'\r\n\r\n') and len(header) < 16384:
                part = self.sock.recv(1)
                if not part:
                    raise ValueError('Console disconnected during handshake')
                header += part
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
            if not header.startswith(b'HTTP/1.1 101 ') or accept not in header or b'netlab.console.v2' not in header:
                raise ValueError('Console does not support configuration capture; restart the device after updating the server')
        except BaseException:
            self.sock.close()
            raise

    def close(self):
        self.sock.close()  # The wrapper releases this connection's input lock.

    def send(self, data, opcode=2):
        mask = os.urandom(4)
        size = len(data)
        header = bytes([0x80 | opcode])
        header += bytes([0x80 | size]) if size < 126 else b'\xfe' + struct.pack('!H', size)
        self.sock.sendall(header + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(data)))

    def receive(self, deadline):
        while True:
            if len(self.buffer) >= 2:
                opcode, size = self.buffer[0] & 15, self.buffer[1] & 127
                offset = 2
                if size == 126:
                    if len(self.buffer) >= 4:
                        size, offset = struct.unpack('!H', self.buffer[2:4])[0], 4
                    else: size = None
                elif size == 127:
                    if len(self.buffer) >= 10:
                        size, offset = struct.unpack('!Q', self.buffer[2:10])[0], 10
                    else: size = None
                if size is not None:
                    if size > 1_048_576:
                        raise ValueError('Console frame is too large')
                    if len(self.buffer) >= offset + size:
                        payload = self.buffer[offset:offset + size]
                        self.buffer = self.buffer[offset + size:]
                        if opcode == 8:
                            raise ValueError('Console disconnected during configuration capture')
                        if opcode == 9:
                            self.send(payload, 10)
                            continue
                        if opcode == 1:
                            message = json.loads(payload)
                            if message.get('type') == 'console-lock':
                                self.lock = message
                                if message.get('error'):
                                    raise ValueError(message['error'])
                            return b''
                        return payload if opcode == 2 else b''
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('Timed out waiting for console output; check that the device is ready')
            self.sock.settimeout(remaining)
            data = self.sock.recv(65536)
            if not data:
                raise ValueError('Console disconnected during configuration capture')
            self.buffer += data

    def acquire(self):
        deadline = time.monotonic() + 10
        while self.lock is None:
            self.receive(deadline)
        if self.lock['locked']:
            raise ValueError('Release this device’s input lock before exporting initial configs')
        self.send(json.dumps({'action': 'lock', 'revision': self.lock['revision']}).encode(), 1)
        while not self.lock['mine']:
            self.receive(deadline)

    def read_until(self, pattern, timeout=20, pager=None, after=None, interrupted=None,
                   interrupted_prompt=None):
        deadline = time.monotonic() + timeout
        output = b''
        pages = 0
        while True:
            output += self.receive(deadline)
            self.last_output = output
            if not self.lock or not self.lock['mine']:
                raise ValueError('Another station took over input; configuration capture stopped')
            if len(output) > 262144:
                raise ValueError('Console output exceeds the capture limit')
            # A pager token may straddle frames. Count complete tokens once.
            count = output.count(pager) if pager else 0
            if count > pages:
                self.send(b' ' * (count - pages))
                pages = count
            text = clean_output(output)
            match = pattern.search(text)
            if (match is None and interrupted_prompt is not None and
                    interrupted is not None and interrupted.search(output)):
                match = interrupted_prompt.search(text)
            if match and (after is None or after in output or
                          (interrupted is not None and interrupted.search(output))):
                self.last_output = output
                return text[:match.start()], match.group(1)

def handler_for(node):
    if vios.is_veos(node):
        raise ValueError("Arista vEOS configuration capture is not supported yet; use Saved lab ZIP")
    if vios.is_exos(node):
        raise ValueError("EXOS configuration capture is not supported yet; use Saved lab ZIP")
    # Register a platform-specific handler when adding a new vendor/image family.
    if node.get('type') in ('router', 'switch') and node.get('image', '').endswith(('.bin', '.qcow2')):
        return CiscoIOS()
    raise ValueError('No configuration export handler for this device')


def running_config(port, handler=None):
    console = Console(port)
    try:
        console.acquire()
        return (handler or CiscoIOS()).capture(console)
    finally:
        console.close()


def export(lab):
    with lab.lock:
        devices = [node for node in lab.topology['nodes'] if node['type'] != 'pc']
        handlers = {node['id']: handler_for(node) for node in devices}
        for node in devices:
            if node['id'] not in lab.runtime:
                raise ValueError(f"Start {node['name']} before exporting current configurations as initial configs")
        result = copy.deepcopy(lab.topology)
        for node in result['nodes']:
            if node['type'] != 'pc':
                try:
                    node['startup_config'] = running_config(lab.runtime[node['id']]['port'], handlers[node['id']])
                except (ValueError, OSError) as error:
                    raise ValueError(f"{node['name']}: {error}") from error
        if len((json.dumps(result, indent=2) + '\n').encode()) > 1_000_000:
            raise ValueError('Result exceeds the 1 MB topology import limit; use a saved lab ZIP')
        return result
