"""Deployment-local SSH grants and the narrow shared-console Unix broker."""
import base64
import copy
import fcntl
import hashlib
import json
import ipaddress
import os
from pathlib import Path
import re
import secrets
import socket
import socketserver
import subprocess
import tempfile
import threading
import time

import console_capture
from external_tty import Wire
import web_auth

ROLES = {'admin':'admin', 'writer':'user', 'reader':'guest'}
CONTROLS = {'lock', 'unlock', 'takeover', 'request-takeover', 'cancel-takeover',
            'approve-takeover', 'deny-takeover'}


def public_key(value):
    if not isinstance(value, str) or len(value) > 16384 or '\n' in value.strip() or '\r' in value:
        raise ValueError('Paste one OpenSSH public key, not a private key or options')
    fields = value.strip().split()
    if len(fields) < 2 or fields[0] not in ('ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256',
                                           'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'):
        raise ValueError('Use an Ed25519, RSA or ECDSA public key without authorized_keys options')
    try:
        raw = base64.b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise ValueError('Invalid public key encoding') from exc
    canonical = fields[0]+' '+fields[1]
    with tempfile.NamedTemporaryFile() as keyfile:
        keyfile.write((canonical+'\n').encode()); keyfile.flush()
        result = subprocess.run(['ssh-keygen', '-lf', keyfile.name], capture_output=True, timeout=3)
    if result.returncode or (fields[0] == 'ssh-rsa' and int(result.stdout.split()[0]) < 2048):
        raise ValueError('Invalid key (RSA requires at least 2048 bits)')
    return canonical, 'SHA256:'+base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')


class Access:
    def __init__(self, lab, auth=None, *, socket_dir=None, port=2222, enabled=False, lifetime=28800):
        self.lab, self.auth = lab, auth
        self.enabled, self.port, self.lifetime = enabled, int(port), int(lifetime)
        self.bind = str(ipaddress.IPv4Address(os.environ.get('WL_SSH_BIND', '127.0.0.1')))
        if not 1 <= self.port <= 65535 or not 60 <= self.lifetime <= 86400:
            raise ValueError('SSH port must be 1–65535 and session lifetime 60–86400 seconds')
        self.directory = lab.directory / '.external-access'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.path = self.directory / 'grants.json'
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.grants = json.loads(self.path.read_text()) if self.path.exists() else []
        self.host_key = ''
        self.server = None
        self.socket_lock = None
        if socket_dir:
            directory = Path(socket_dir)
            directory.mkdir(mode=0o750, parents=True, exist_ok=True)
            self.socket_lock = (directory/'service.lock').open('a')
            try:
                fcntl.flock(self.socket_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.socket_lock.close()
                raise ValueError('Another Weblab process owns this SSH socket directory')
            path = directory / 'broker.sock'
            if path.exists():
                path.unlink()  # Exclusive lab owner acquired before creating this service.
            manager = self
            class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
                daemon_threads = True
                block_on_close = False
                slots = threading.BoundedSemaphore(64)

                def process_request(self, request, address):
                    if not self.slots.acquire(blocking=False):
                        request.close(); return
                    try:
                        super().process_request(request, address)
                    except BaseException:
                        self.slots.release(); raise

                def process_request_thread(self, request, address):
                    try:
                        super().process_request_thread(request, address)
                    finally:
                        self.slots.release()

            class Handler(socketserver.BaseRequestHandler):
                def handle(self):
                    wire = Wire(self.request)
                    try:
                        manager.handle(wire)
                    except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
                        try: wire.send({'type':'error', 'message':str(exc)})
                        except OSError: pass
            self.server = Server(str(path), Handler)
            if os.getuid() == 0:
                os.chown(directory, 0, 10002)
                os.chown(path, 0, 10002)
            directory.chmod(0o750); path.chmod(0o660)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.closed.set()
        if self.server:
            self.server.shutdown(); self.server.server_close()
        if self.socket_lock:
            self.socket_lock.close()

    def account_stamp(self, role):
        if not self.auth:
            return 'local-owner'
        with self.auth.lock:
            row = self.auth.db.execute('SELECT a.hash,l.locked,l.reset_marker FROM accounts a '
                'JOIN account_locks l ON l.name=a.name WHERE a.name=?', (ROLES[role],)).fetchone()
        return hashlib.sha256((row[0]+row[2]).encode()).hexdigest() if row and not row[1] else None

    def valid(self, grant):
        stamp = self.account_stamp(grant['role'])
        return stamp is not None and grant.get('account_stamp') == stamp and grant.get('enabled', False)

    def info(self):
        with self.lock:
            return {'enabled':self.enabled, 'available':bool(self.server), 'port':self.port, 'bind':self.bind,
                    'host_key':self.host_key, 'lifetime':self.lifetime,
                    'keys':[{k:v for k,v in g.items() if k not in ('key', 'account_stamp')}
                            | {'valid':self.valid(g)} for g in self.grants]}

    def save(self):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.grants)+'\n'); temp.chmod(0o600); temp.replace(self.path)

    def change(self, data):
        if not isinstance(data, dict):
            raise ValueError('Expected an SSH access request object')
        with self.lock:
            previous = copy.deepcopy(self.grants)
            action = data.get('action')
            if action == 'add':
                if len(self.grants) >= 64:
                    raise ValueError('At most 64 SSH grants; revoke unused keys first')
                label = data.get('label', '')
                if not isinstance(label, str):
                    raise ValueError('Label must be text')
                label = label.strip()
                role, scope = data.get('role', 'reader'), data.get('scope', 'console')
                if not label or len(label) > 80 or not all(c.isprintable() for c in label):
                    raise ValueError('Use a label of 1–80 printable characters')
                if role not in ROLES or scope not in ('console', 'gateway'):
                    raise ValueError('Invalid SSH role or scope')
                node = data.get('node', '') if scope == 'gateway' else ''
                if scope == 'gateway':
                    if not any(n['id'] == node and n['type'] == 'cloud' for n in self.lab.topology['nodes']):
                        raise ValueError('Select an existing Internet node')
                    role = 'admin'  # Lab shell grants never imply read-only access.
                key, fingerprint = public_key(data.get('key'))
                if any(g['fingerprint'] == fingerprint and g['scope'] == scope and g['node'] == node for g in self.grants):
                    raise ValueError('This key already has an access grant here')
                self.grants.append({'id':secrets.token_hex(16), 'label':label, 'key':key,
                    'fingerprint':fingerprint, 'role':role, 'scope':scope, 'node':node,
                    'enabled':True, 'account_stamp':self.account_stamp(role)})
            elif action == 'revoke':
                ident = data.get('id')
                if not any(g['id'] == ident for g in self.grants):
                    raise ValueError('Unknown SSH key')
                self.grants = [g for g in self.grants if g['id'] != ident]
            else:
                raise ValueError('Use add or revoke')
            try:
                self.save()
            except OSError:
                self.grants = previous
                raise
        # Live gateway keys are synchronized separately by the lifecycle monitor.
        return self.info()

    def gateway_keys(self, node):
        with self.lock:
            return [g['key'] for g in self.grants if g['scope'] == 'gateway' and
                    g['node'] == node and self.valid(g)]

    def grant_for(self, key):
        with self.lock:
            return next((copy.deepcopy(g) for g in self.grants if g['scope'] == 'console' and
                         g['key'] == key and self.valid(g)), None) if self.enabled else None

    def session_valid(self, grant, deadline):
        current = self.grant_for(grant['key'])
        return not self.closed.is_set() and time.monotonic() < deadline and current == grant

    def audit(self, grant, session, operation, node, result='ok'):
        if self.auth:
            event = self.auth.activity_begin(ROLES[grant['role']], session, 'ssh', operation, f"{node} · {grant['fingerprint']}")
            self.auth.activity_finish(event, result, None)

    def handle(self, wire):
        request = wire.receive(3)
        if not request:
            return
        if request.get('op') == 'host-key':
            _, fingerprint = public_key(request.get('key'))
            self.host_key = fingerprint
            wire.send({'ok':True}); return
        key = request.get('key', '')
        grant = self.grant_for(key)
        if request.get('op') == 'keys':
            wire.send({'key':key if grant else ''}); return
        if request.get('op') != 'session' or not grant:
            raise ValueError('SSH grant is disabled, revoked or unconfigured')
        deadline = time.monotonic()+self.lifetime
        session = 'ssh:'+secrets.token_hex(32)
        with self.lab.lock:
            nodes = [{'id':n['id'], 'name':n['name'],
                      'state':'running' if 'console' in self.lab.runtime.get(n['id'], {}) else 'stopped'}
                     for n in self.lab.topology['nodes'] if n['type'] not in ('cloud', 'network', 'group')]
        wire.send({'type':'nodes', 'nodes':nodes})
        while self.session_valid(grant, deadline):
            request = wire.receive(.25)
            if request is None:
                continue
            if request.get('op') != 'attach':
                raise ValueError('Expected a console attach')
            node = request.get('node')
            if not isinstance(node, str) or not re.fullmatch(r'[\w-]{1,40}', node, re.ASCII):
                raise ValueError('Invalid node ID')
            with self.lab.lock:
                runtime = self.lab.runtime.get(node, {})
                process = copy.deepcopy(runtime.get('console'))
                if not process or not self.lab.alive(process):
                    raise ValueError('Start this node in Weblab before attaching')
                console = console_capture.Console(runtime['port'], human_identity=(grant['role'], web_auth.console_identity(session)))
                if not self.lab.alive(process):
                    console.close(); raise ValueError('Console process changed')
            self.audit(grant, session, 'console.attach', node)
            try:
                self.relay(wire, console, node, process, grant, session, deadline)
            finally:
                console.close()
                self.audit(grant, session, 'console.detach', node)
            return
        raise ValueError('SSH session expired or access revoked')

    def relay(self, wire, console, node, process, grant, session, deadline):
        last_lock = None
        while self.session_valid(grant, deadline):
            with self.lab.lock:
                if self.lab.runtime.get(node, {}).get('console') != process or not self.lab.alive(process):
                    raise ValueError('Node stopped or console restarted')
            try:
                part = console.receive(time.monotonic()+.05)
                if part:
                    for offset in range(0, len(part), 16384):
                        wire.send({'type':'output', 'data':base64.b64encode(part[offset:offset+16384]).decode()})
            except (console_capture.ConsoleTimeout, socket.timeout):
                pass
            if console.lock != last_lock:
                last_lock = copy.deepcopy(console.lock)
                wire.send({'type':'lock', 'state':last_lock})
            message = wire.receive(0)
            if message is None:
                continue
            if not self.session_valid(grant, deadline):
                break
            console.sock.settimeout(1)
            if message.get('op') == 'input':
                data = base64.b64decode(message.get('data', ''), validate=True)
                if len(data) > 16384:
                    raise ValueError('Input too large')
                if grant['role'] != 'reader':
                    console.send(data)
            elif message.get('op') == 'control':
                value = message.get('control', {})
                if not isinstance(value, dict) or set(value)-{'action', 'revision', 'request_id'} or value.get('action') not in CONTROLS:
                    raise ValueError('Invalid console control')
                if type(value.get('revision')) is not int or not 0 <= value['revision'] < 10**15:
                    raise ValueError('Invalid lock revision')
                if grant['role'] != 'reader':
                    console.send(json.dumps(value).encode(), 1)
                    self.audit(grant, session, 'console.'+value['action'], node, 'requested')
            else:
                raise ValueError('Unknown console request')
        raise ValueError('SSH session expired or access revoked')
