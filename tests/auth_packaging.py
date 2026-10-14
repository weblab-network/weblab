#!/usr/bin/env python3
"""Opt-in release matrix on disposable Docker volumes; no real guests or accounts."""
import argparse
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]


def docker(*args, check=True):
    result = subprocess.run(['docker', *args], text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


class Package:
    def __init__(self, args, agent):
        self.args, self.agent = args, agent
        self.prefix = 'weblab-auth-package-' + uuid.uuid4().hex[:12]
        self.core, self.companion = self.prefix + '-core', self.prefix + '-agent'
        self.volumes = {key: self.prefix + '-' + key for key in ('data', 'sockets', 'bridge', 'history')}
        self.passwords = {name: uuid.uuid4().hex + 'a1' for name in ('admin', 'user', 'guest')}

    def stop(self):
        for name in (self.core, self.companion):
            # Graceful shutdown gives the application a chance to flush state.
            docker('stop', '--time', '20', name, check=False)
            docker('rm', '-f', name, check=False)

    def close(self):
        self.stop()
        for name in self.volumes.values():
            docker('volume', 'rm', name, check=False)

    def start(self, auth=False, old=False):
        self.stop()
        core = self.args.old_core if old else self.args.core
        agent = self.args.old_agent if old else self.args.agent
        if self.agent:
            docker('run', '-d', '--name', self.companion, '--network', 'none', '--read-only',
                   '--init', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--user', '10001:10001', '--pids-limit', '128', '--memory', '1g', '--cpus', '1',
                   '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
                   '--tmpfs', '/workspace:rw,nosuid,nodev,uid=10001,gid=10001,size=32m',
                   '-v', self.volumes['sockets'] + ':/run/weblab-agent',
                   '-v', self.volumes['history'] + ':/home/agent', agent)
        flags = ['-v', self.volumes['data'] + ':/data']
        if self.agent:
            flags += ['-v', self.volumes['sockets'] + ':/run/weblab-agent',
                      '-v', self.volumes['bridge'] + ':/agent-state',
                      '-e', 'WL_AGENT_SOCKET_DIR=/run/weblab-agent',
                      '-e', 'WL_AGENT_STATE_DIR=/agent-state',
                      '-e', 'WL_AGENT_PROVIDERS=http://127.0.0.1:9/v1']
        # Random test credentials never enter shell commands or test output.
        with tempfile.TemporaryDirectory(prefix='weblab-package-env-') as temp:
            env = Path(temp) / 'test.env'
            env.write_text('WL_AUTH=' + str(int(auth)) + '\n' + ''.join(
                'WL_AUTH_' + name.upper() + '_PASSWORD=' + value + '\n'
                for name, value in self.passwords.items()))
            env.chmod(0o600)
            docker('run', '-d', '--name', self.core, '-p', '127.0.0.1::8080',
                   '--env-file', str(env), *flags, core,
                   '--bind', '0.0.0.0', '--port', '8080', '--image-dir', '/iou', '--data-dir', '/data')
        self.port = int(docker('port', self.core, '8080/tcp').rsplit(':', 1)[1])
        end = time.monotonic() + 30
        while True:
            try:
                assert self.api('/api/auth/status' if not old else '/api/state')[0] == 200
                if self.agent:
                    docker('exec', self.core, 'test', '-S', '/run/weblab-agent/companion.sock')
                break
            except (OSError, AssertionError, RuntimeError):
                if time.monotonic() > end:
                    raise RuntimeError('Packaged services did not become ready') from None
                time.sleep(.25)
        # A disposable PTY echo process, not a vendor image. Recreated after upgrades.
        script = '''import os, socket, sys, tty
from pathlib import Path
tty.setraw(0)
base = Path('/tmp/netio' + str(os.getuid()))
base.mkdir(exist_ok=True)
sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
sock.bind(str(base / sys.argv[-1]))
os.write(1, b'BOOT READY\\r\\n')
while True:
 data = os.read(0,65536)
 if not data: break
 os.write(1,b'RX:' + data)
'''
        docker('exec', self.core, 'python3', '-c',
               'from pathlib import Path; p=Path("/iou/fake.bin"); '
               'p.write_text("#!/usr/bin/env python3\\n"+' + repr(script) + '); p.chmod(0o755)')

    def api(self, path, method='GET', data=None, login=('', ''), token='', expected=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        try:
            conn.request(method, path, None if data is None else json.dumps(data), {
                'Content-Type': 'application/json', 'Cookie': login[0], 'X-Weblab-CSRF': login[1],
                'X-Weblab-Agent-Session': token, 'X-Weblab-Login': '1'})
            response = conn.getresponse()
            body = response.read()
            result = response.status, dict(response.getheaders()), json.loads(body) if body else None
            if expected is not None:
                assert result[0] == expected, (path, result[0], expected)
            return result
        finally:
            conn.close()

    def login(self, name='admin'):
        _, headers, body = self.api('/api/auth/login', 'POST', {
            'name': name, 'password': self.passwords[name], 'remember': True}, expected=200)
        return headers['Set-Cookie'].split(';')[0], body['csrf']

    def seed(self, login=('', '')):
        topology = {'name': 'Packaged upgrade fixture', 'nodes': [
            {'id': 'r1', 'name': 'R1', 'type': 'router', 'image': 'fake.bin', 'x': 100, 'y': 100}], 'links': []}
        self.api('/api/topology', 'PUT', topology, login, expected=200)
        # Opaque saved-device bytes must survive recreation unchanged.
        docker('exec', self.core, 'python3', '-c',
               'from pathlib import Path; p=Path("/data/nodes/r1"); p.mkdir(parents=True,exist_ok=True); '
               '(p/"nvram_00100").write_bytes(b"package-storage-marker\\x00\\xff")')

    def storage(self, login=('', '')):
        state = self.api('/api/state', login=login, expected=200)[2]
        assert state['topology']['name'] == 'Packaged upgrade fixture'
        assert [n['id'] for n in state['topology']['nodes']] == ['r1']
        docker('exec', self.core, 'python3', '-c',
               'from pathlib import Path; assert Path("/data/nodes/r1/nvram_00100").read_bytes()'
               '==b"package-storage-marker\\x00\\xff"')

    def draft(self, login=('', '')):
        token = self.api('/api/agent/session', 'POST', {}, login, expected=200)[2]['token']
        self.api('/api/agent/reset', 'POST', {}, login, token, expected=200)
        state = self.api('/api/agent/state', login=login, token=token, expected=200)[2]
        assert not any(state['settings'][key] for key in ('lab_changes', 'console_input', 'auto_approve'))
        return token, state['conversation_id']

    def draft_survives(self, record, login=('', '')):
        state = self.api('/api/agent/state', login=login, token=record[0], expected=200)[2]
        assert state['conversation_id'] == record[1]

    def authenticated(self, browsers=False):
        self.api('/api/state', expected=401)
        admin = self.login()
        self.storage(admin)
        for name in ('user', 'guest'):
            login = self.login(name)
            self.api('/api/state', login=login, expected=200)
            self.api('/api/topology', 'PUT', {}, login, expected=403)
            self.api('/api/instructions', 'PUT', {}, login, expected=403)
            self.api('/api/activity', login=login, expected=403)
            assert not self.api('/api/agent/info', login=login, expected=200)[2]['enabled']
            self.api('/api/agent/session', 'POST', {}, login, expected=403)
        assert self.api('/api/agent/info', login=admin, expected=200)[2]['enabled'] == self.agent
        self.api('/api/topology', 'PUT', {}, (admin[0], ''), expected=403)
        current = self.api('/api/instructions', login=admin, expected=200)[2]
        published = self.api('/api/instructions', 'PUT', {'revision': current['revision'],
            'file': {'name': 'exercise.md', 'text': '# Packaged exercise\n\nInspect R1.'}}, admin, expected=200)[2]
        assert self.api('/api/instructions', login=login, expected=200)[2] == published
        records = self.api('/api/activity', login=admin, expected=200)[2]['records']
        assert any(r['operation'] == 'instructions.update' for r in records)
        draft = self.draft(admin) if self.agent else None
        self.start(auth=True)
        self.storage(admin)  # Remembered Weblab session also survives recreation.
        assert self.api('/api/instructions', login=admin, expected=200)[2] == published
        rows = self.api('/api/activity', login=admin, expected=200)[2]['records']
        assert {r['id'] for r in records} <= {r['id'] for r in rows}
        if draft:
            self.draft_survives(draft, admin)
            other = self.login()
            self.api('/api/agent/state', login=other, token=draft[0], expected=409)
        self.api('/api/auth/logout', 'POST', {}, admin, expected=200)
        self.api('/api/state', login=admin, expected=401)
        if draft:
            self.api('/api/agent/state', login=other, token=draft[0], expected=409)
        if browsers:
            self.api('/api/nodes/r1/start', 'POST', {}, self.login(), expected=200)
            try:
                subprocess.run(['node', str(ROOT / 'tests/auth_packaging.cjs')],
                    input=json.dumps({'base': 'http://127.0.0.1:' + str(self.port),
                                      'passwords': self.passwords, 'agent': self.agent}),
                    text=True, cwd=ROOT, check=True, timeout=180)
            finally:
                self.api('/api/nodes/r1/stop', 'POST', {}, self.login(), expected=200)


def main(args):
    for agent in (False, True):
        for auth in (False, True):
            fixture = Package(args, agent)
            try:
                fixture.start(auth=auth)
                login = fixture.login() if auth else ('', '')
                assert fixture.api('/api/state', login=login, expected=200)[2]['topology']['nodes'] == []
                fixture.seed(login)
                if auth:
                    fixture.authenticated(browsers=True)
                else:
                    assert fixture.api('/api/agent/info', expected=200)[2]['enabled'] == agent
                    draft = fixture.draft() if agent else None
                    fixture.start()
                    fixture.storage()
                    if draft:
                        fixture.draft_survives(draft)
                print(f'PASS fresh install/recreation: auth={auth}, Agent={agent}', flush=True)
            finally:
                fixture.close()
        fixture = Package(args, agent)
        try:
            fixture.start(old=True)
            fixture.seed()
            draft = fixture.draft() if agent else None
            fixture.start()
            fixture.storage()
            if draft:
                fixture.draft_survives(draft)
            fixture.start(auth=True)
            if draft:
                fixture.api('/api/agent/state', login=fixture.login(), token=draft[0], expected=409)
            fixture.authenticated()
            print(f'PASS v0.6.0 upgrade, then enable auth: Agent={agent}', flush=True)
        finally:
            fixture.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', required=True)
    parser.add_argument('--agent', required=True)
    parser.add_argument('--old-core', default='ghcr.io/weblab-network/weblab:0.6.0')
    parser.add_argument('--old-agent', default='ghcr.io/weblab-network/weblab-agent:0.6.0')
    main(parser.parse_args())
