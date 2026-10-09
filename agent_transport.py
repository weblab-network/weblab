"""Small bounded HTTP-over-Unix transport shared by the optional agent services."""
import http.client
import json
import os
from pathlib import Path
import socket
import select
import time
from socketserver import ThreadingMixIn, UnixStreamServer
from http.server import BaseHTTPRequestHandler

MAX_BODY = 4 * 1024 * 1024
OPENAI_HOSTS = frozenset(('auth.openai.com:443', 'chatgpt.com:443'))


def tunnel(left, right, allowed=lambda: True):
    """Bounded opaque TLS transport; endpoints validate the destination first."""
    deadline, count = time.monotonic() + 3600, 0
    while time.monotonic() < deadline and allowed():
        ready, _, _ = select.select([left, right], [], [], 1)
        for source in ready:
            data = source.recv(65536)
            if not data:
                return
            count += len(data)
            if count > 64 * 1024 * 1024:
                return
            (right if source is left else left).sendall(data)


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path, timeout=15):
        super().__init__('localhost', timeout=timeout)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def request(socket_path, path, data=None, token='', timeout=15):
    connection = UnixHTTP(socket_path, timeout)
    try:
        body = None if data is None else json.dumps(data).encode()
        connection.request('GET' if data is None else 'POST', path, body,
                           {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise ValueError('Agent response is too large')
        result = json.loads(raw)
        if response.status >= 400:
            raise ValueError(result.get('error', 'Agent service request failed'))
        return result
    finally:
        connection.close()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream)
    temporary.replace(path)


class UnixServer(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True

    def __init__(self, path, handler):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A live service must never be unlinked/replaced accidentally.
        if path.exists():
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise ValueError('Agent socket is already in use')
            finally:
                probe.close()
        super().__init__(str(path), handler)
        os.chmod(path, 0o666)  # Dedicated volume shared only by core and companion.


class JSONHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log prompts, capabilities or provider payloads.

    def body(self):
        self.connection.settimeout(20)
        length = int(self.headers.get('Content-Length', '0'))
        if self.headers.get('Transfer-Encoding') or not 0 < length <= MAX_BODY:
            raise ValueError('Invalid agent request size')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError('Incomplete agent request')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('Expected an object')
        return data

    def reply(self, value, status=200):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)
