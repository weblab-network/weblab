"""Authentication boundaries against a disposable lab and real console wrapper."""
import http.client
from concurrent.futures import ThreadPoolExecutor
import threading
import os
import socket
import subprocess
import sys
import io
import zipfile
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import lab_server
import web_auth
import test_lab as fixture
from test_lab import WebSocket

PASSWORDS = {n: 'test-unique-password1-' + n for n in web_auth.ROLES}


class AuthTests(unittest.TestCase):
    setUp = fixture.LabTests.setUp
    tearDown = fixture.LabTests.tearDown
    node = staticmethod(fixture.LabTests.node)

    def enable(self):
        self.server.auth = web_auth.Auth(self.lab.directory / '.auth', PASSWORDS)
        self.addCleanup(self.server.auth.close)

    def http(self, path, method='GET', data=None, cookie='', csrf='', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        fields = {'Content-Type':'application/json', 'Cookie':cookie, 'X-Weblab-CSRF':csrf, **(headers or {})}
        conn.request(method, path, None if data is None else json.dumps(data), fields)
        response = conn.getresponse()
        body = response.read()
        result = (response.status, dict(response.getheaders()), json.loads(body) if body else None)
        conn.close()
        return result

    def login(self, name='admin', remember=False):
        code, headers, data = self.http('/api/auth/login', 'POST', {'name':name, 'password':PASSWORDS[name], 'remember':remember}, headers={'X-Weblab-Login':'1'})
        self.assertEqual(code, 200, data)
        return headers['Set-Cookie'].split(';')[0], data['csrf']

    def test_default_unchanged_and_explicit_configuration(self):
        self.assertEqual(self.http('/api/state')[0], 200)
        self.assertEqual(self.http('/api/auth/status')[2], {'enabled':False, 'role':'admin'})
        with patch.dict('os.environ', {'WL_AUTH':'wat'}):
            with self.assertRaises(ValueError): web_auth.Auth.from_env(self.root)
        with self.assertRaises(ValueError): web_auth.Auth(self.root / 'invalid', {n:'same-password-here' for n in PASSWORDS})

    def test_authentication_csrf_and_expiry(self):
        self.enable()
        for path in ['/api/state','/api/storage','/ws/r1','/iol-console.html','/api/agent/state','/api/automation/state']:
            self.assertEqual(self.http(path)[0], 401, path)
        self.assertEqual(self.http('/health')[0], 200)
        self.assertEqual(self.http('/api/auth/status')[2], {'enabled':True})
        self.assertEqual(self.http('/api/auth/login', 'POST', {'name':'admin','password':PASSWORDS['admin']})[0], 403)
        self.assertEqual(self.http('/api/auth/login', 'POST', {'name':'admin','password':PASSWORDS['admin']}, headers={'X-Weblab-Login':'1','Origin':'https://evil.invalid'})[0], 400)
        cookie, csrf = self.login()
        self.assertEqual(self.http('/api/state', cookie=cookie)[0], 200)
        self.assertEqual(self.http('/api/topology','PUT',self.topology,cookie)[0], 403)
        self.assertEqual(self.http('/api/topology','PUT',self.topology,cookie,csrf)[0], 200)
        self.assertEqual(self.http('/api/auth/logout','POST',cookie=cookie,csrf=csrf)[0],200)
        self.assertEqual(self.http('/api/state',cookie=cookie)[0],401)
        cookie, csrf = self.login()
        with self.server.auth.db:
            self.server.auth.db.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.http('/api/state',cookie=cookie)[0],401)

    def test_workspace_reset_requires_admin_csrf_and_preserves_login(self):
        self.enable()
        cookie, csrf = self.login()
        data = {'expected_revision': self.lab.workspace_revision}
        self.assertEqual(self.http('/api/lab/reset', 'POST', data)[0], 401)
        self.assertEqual(self.http('/api/lab/reset', 'POST', data, cookie)[0], 403)
        self.assertTrue(self.lab.topology['nodes'])
        code, _, result = self.http('/api/lab/reset', 'POST', data, cookie, csrf)
        self.assertEqual(code, 200, result)
        self.assertEqual(result['topology']['nodes'], [])
        self.assertEqual(self.http('/api/state', cookie=cookie)[0], 200)
        self.assertEqual(web_auth.activity_operation('POST', '/api/lab/reset'), ('lab.reset', ''))

    def test_role_matrix_and_no_agent_bypass(self):
        self.enable()
        for name in PASSWORDS:
            cookie, csrf = self.login(name)
            self.assertEqual(self.http('/api/state',cookie=cookie)[0],200)
            self.assertEqual(self.http('/api/agent/info',cookie=cookie)[2],{'enabled':False})
            for method,path in [('GET','/api/automation/state'),('POST','/api/automation/console-send'),('POST','/api/unknown')]:
                self.assertEqual(self.http(path,method,{},cookie,csrf)[0],403,(name,path))
            self.assertEqual(self.http('/api/agent/session','POST',{},cookie,csrf)[0],400 if name=='admin' else 403)
            if name != 'admin':
                for method,path in [('PUT','/api/topology'),('POST','/api/import'),('POST','/api/lab/reset'),('POST','/api/export'),('POST','/api/export/saved-configs'),('POST','/api/export/initial-configs'),('POST','/api/images/new.bin'),('GET','/api/exports/'+'a'*32)]:
                    self.assertEqual(self.http(path,method,{},cookie,csrf)[0],403,(name,path))
            for path in ['/api/lab/stop','/api/nodes/r1/stop']:
                self.assertEqual(self.http(path,'POST',{},cookie,csrf)[0],403 if name=='guest' else 200)
            # For authorized users this reaches backend validation, rather than role refusal.
            self.assertEqual(self.http('/api/links/cable/traffic','POST',{},cookie,csrf)[0],403 if name=='guest' else 400)

    def test_internet_start_requires_admin(self):
        self.enable()
        self.lab.save({'name':'Cloud', 'nodes':[{'id':'cloud','type':'cloud','name':'Internet'}], 'links':[]})
        for account in ('user', 'guest', 'admin'):
            cookie,csrf=self.login(account)
            for path,method in (('/api/nodes/cloud/start','start'),('/api/lab/start','start_all')):
                with patch.object(self.lab,method) as start:
                    result=self.http(path,'POST',{},cookie,csrf)
                self.assertEqual(result[0],200 if account=='admin' else 403,result)
                self.assertEqual(start.call_count,1 if account=='admin' else 0)

    def test_password_change_sessions_rate_limit_and_private_storage(self):
        self.enable()
        cookie, csrf = self.login(remember=True)
        auth = self.server.auth
        token = cookie.split('=',1)[1]
        self.assertIsNotNone(auth.session(token))
        with web_auth.sqlite3.connect(self.lab.directory/'.auth'/'auth.sqlite3') as db:
            self.assertNotIn(token, str(db.execute('SELECT * FROM sessions').fetchall()))
            self.assertNotIn(PASSWORDS['admin'], str(db.execute('SELECT * FROM accounts').fetchall()))
        restarted = web_auth.Auth(self.lab.directory/'.auth', PASSWORDS)
        self.assertIsNotNone(restarted.session(token))
        restarted.close()
        changed = web_auth.Auth(self.lab.directory/'.auth', {**PASSWORDS,'admin':'new-unique-password-admin'})
        self.assertIsNone(changed.session(token))
        changed.close()
        # Real timing is covered below; keep this rate-limit regression fast.
        with patch.object(auth, '_wait_before_verification'):
            for _ in range(10):
                self.assertEqual(self.http('/api/auth/login','POST',{'name':'guest','password':'wrong'},headers={'X-Weblab-Login':'1'})[0],401)
            self.assertEqual(self.http('/api/auth/login','POST',{'name':'guest','password':'wrong'},headers={'X-Weblab-Login':'1'})[0],429)
        self.assertEqual((self.lab.directory/'.auth'/'auth.sqlite3').stat().st_mode & 0o777,0o600)

    def test_reader_console_frames_lock_spoof_and_logout(self):
        self.enable()
        writer_cookie, writer_csrf = self.login('user')
        guest, gcsrf = self.login('guest')
        self.assertEqual(self.http('/api/nodes/r1/start','POST',{},writer_cookie,writer_csrf)[0],200)
        writer = WebSocket(self.port,'r1','netlab.console.v2',headers={'Cookie':writer_cookie})
        self.addCleanup(writer.close)
        writer.until(b'BOOT READY')
        reader = WebSocket(self.port,'r1','netlab.console.v2',headers={'Cookie':guest,'X-Weblab-Read-Only':'0'})
        self.addCleanup(reader.close)
        reader.until(b'BOOT READY')
        reader.send(b'READER-MUST-NOT-TYPE\r',2,fin=False)
        reader.send(b'READER-FRAGMENT\r',0)
        reader.send(json.dumps({'action':'lock','revision':0}).encode(),1)
        writer.send(b'WRITER-CHECK\r',2)
        output = reader.until(b'RX:WRITER-CHECK')
        self.assertNotIn(b'READER-',output)
        self.assertFalse(any(b'"locked":true' in item for item in output.splitlines()))
        # Legacy text protocol cannot bypass the wrapper restriction either.
        legacy = WebSocket(self.port,'r1',headers={'Cookie':guest})
        self.addCleanup(legacy.close)
        legacy.until(b'WRITER-CHECK')
        legacy.send(b'LEGACY-MUST-NOT-TYPE\r')
        writer.send(b'SECOND-CHECK\r',2)
        self.assertNotIn(b'LEGACY-MUST',legacy.until(b'RX:SECOND-CHECK'))
        self.assertEqual(self.http('/api/auth/logout','POST',cookie=guest,csrf=gcsrf)[0],200)
        reader.sock.settimeout(3)
        with self.assertRaises(EOFError):
            while True: reader.receive()
        writer.send(b'STILL-WRITER\r',2)
        writer.until(b'RX:STILL-WRITER')

    def test_backup_excludes_credentials_and_restore_preserves_sessions(self):
        self.enable()
        cookie, csrf = self.login()
        self.lab.directory.joinpath('nodes','r1').mkdir(parents=True, exist_ok=True)
        result = lab_server.lab_backup.create(self.lab)
        stream, _ = lab_server.lab_backup.take(self.lab, result['url'].rsplit('/',1)[1])
        with stream:
            data = stream.read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertFalse(any('.auth' in name or 'sqlite' in name for name in archive.namelist()))
        self.assertNotIn(PASSWORDS['admin'].encode(), data)
        self.assertNotIn(cookie.split('=',1)[1].encode(), data)
        lab_server.lab_backup.restore(self.lab, io.BytesIO(data), lab_server.run)
        self.assertEqual(self.http('/api/state',cookie=cookie)[0],200)

    def test_real_startup_enables_optional_admin_agent_but_not_external_mcp(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            port = listener.getsockname()[1]
        env = {**os.environ, 'WL_AUTH':'1', 'WL_AUTH_COOKIE_SECURE':'0', 'WL_AUTOMATION':'1',
               'WL_AGENT_SOCKET_DIR':str(self.root/'unused-socket-directory'),
               **{'WL_AUTH_'+n.upper()+'_PASSWORD':p for n,p in PASSWORDS.items()}}
        proc = subprocess.Popen([sys.executable, str(Path(lab_server.__file__)), '--port',str(port),
                                 '--data-dir',str(self.root/'startup'), '--image-dir',str(self.root)],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        old_port = self.port
        try:
            self.port = port
            for _ in range(100):
                try:
                    if self.http('/health')[0] == 200: break
                except OSError: pass
                if proc.poll() is not None:
                    self.fail('Authenticated server exited: '+proc.stderr.read().decode())
                time.sleep(.05)
            else: self.fail('Authenticated server did not start')
            cookie, csrf = self.login()
            self.assertTrue(self.http('/api/agent/info',cookie=cookie)[2]['enabled'])
            self.assertEqual(self.http('/api/agent/session','POST',{},cookie,csrf)[0],200)
            self.assertEqual(self.http('/api/automation/state',cookie=cookie)[0],403)
            self.assertTrue((self.root/'unused-socket-directory'/'gateway.sock').exists())
            user, token = self.login('user')
            self.assertEqual(self.http('/api/agent/info',cookie=user)[2],{'enabled':False})
            self.assertEqual(self.http('/api/agent/session','POST',{},user,token)[0],403)
        finally:
            self.port = old_port
            proc.terminate()
            proc.communicate(timeout=10)

    def test_backoff_threshold_restart_success_and_inactivity_reset(self):
        self.enable()
        auth = self.server.auth
        def wrong(service, name='admin', ip='first-client'):
            with self.assertRaises(web_auth.Denied) as error:
                service.login(name, 'wrong', ip)
            self.assertEqual(error.exception.status,401)
        with patch.object(auth, '_wait_before_verification') as wait:
            for _ in range(3): wrong(auth)
            wait.assert_not_called()
        restarted = web_auth.Auth(self.lab.directory/'.auth', PASSWORDS)
        try:
            with patch.object(restarted, '_wait_before_verification') as wait:
                # Another IP cannot evade the account counter, even after restart.
                wrong(restarted, ip='another-client')
                self.assertEqual(wait.call_count,1)
                restarted.login('admin', PASSWORDS['admin'], 'another-client')
                self.assertEqual(wait.call_count,2)  # Correct password waits too.
                for _ in range(3): wrong(restarted, ip='another-client')
                self.assertEqual(wait.call_count,2)  # Success reset the streak.
                with restarted.lock, restarted.db:
                    restarted.db.execute('UPDATE login_failures SET updated=?',(time.time()-web_auth.LOGIN_FAILURE_TTL-1,))
                restarted.login('admin', PASSWORDS['admin'], 'another-client')
                self.assertEqual(wait.call_count,2)
        finally:
            restarted.close()

    def test_source_backoff_and_unknown_account_buckets(self):
        self.enable()
        auth = self.server.auth
        with patch.object(auth, '_wait_before_verification') as wait:
            for name in ['unknown1','unknown2','unknown3']:
                with self.assertRaises(web_auth.Denied): auth.login(name,'wrong','same-client')
            wait.assert_not_called()
            # Switching account names cannot evade a source's failure count.
            auth.login('admin',PASSWORDS['admin'],'same-client')
            self.assertEqual(wait.call_count,1)
            with self.assertRaises(web_auth.Denied): auth.login('unknown4','wrong','new-client')
            self.assertEqual(wait.call_count,2)
            self.assertEqual(len(auth.login_locks),4)
            with auth.lock:
                buckets = [r[0] for r in auth.db.execute('SELECT bucket FROM login_failures')]
            self.assertFalse(any('unknown1' in b or 'unknown4' in b for b in buckets))

    def test_http_delay_precedes_verification_without_blocking_existing_sessions(self):
        self.enable()
        cookie, _ = self.login()
        for _ in range(3):
            self.assertEqual(self.http('/api/auth/login','POST',{'name':'guest','password':'wrong'},headers={'X-Weblab-Login':'1'})[0],401)
        auth = self.server.auth
        entered = threading.Event()
        real_wait = auth._wait_before_verification
        real_hash = web_auth.password_hash
        verifications = []
        def wait():
            entered.set()
            real_wait()
        def verify(password, salt):
            verifications.append(time.monotonic())
            return real_hash(password,salt)
        with patch.object(auth, '_wait_before_verification', side_effect=wait), patch('web_auth.secrets.randbelow', return_value=0), patch('web_auth.password_hash', side_effect=verify):
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.http,'/api/auth/login','POST',{'name':'guest','password':PASSWORDS['guest']},headers={'X-Weblab-Login':'1'})
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.http('/api/state',cookie=cookie)[0],200)
                self.assertFalse(pending.done())
                self.assertEqual(self.http('/api/auth/login','POST',{'name':'guest','password':'wrong'},headers={'X-Weblab-Login':'1'})[0],429)
                self.assertEqual(verifications,[])
                self.assertEqual(pending.result(timeout=8)[0],200)
            self.assertEqual(len(verifications),1)
            self.assertGreaterEqual(verifications[0]-started,3)

    def test_role_password_policy_and_generation(self):
        passwords = {**PASSWORDS, 'user':'Abc123', 'guest':'Guest9'}
        auth = web_auth.Auth(self.root/'short-keys', passwords)
        self.addCleanup(auth.close)
        for name in ('user','guest'):
            token, _ = auth.login(name,passwords[name],'client')
            self.assertEqual(auth.session(token)['role'],web_auth.ROLES[name])
        for name, value in [('admin','Admin1'), ('user','Abc12'), ('guest','abcdef'),
                            ('user','123456'), ('guest','abcde!'), ('user','a1'*129)]:
            with self.subTest(name=name, value=value):
                with self.assertRaises(ValueError):
                    web_auth.Auth(self.root/'short-keys', {**passwords, name:value})
        with patch('web_auth.secrets.token_urlsafe', side_effect=['onlyletters','123456','randomLongKey1']):
            self.assertEqual(web_auth.generated_password(),'randomLongKey1')

    def test_unchanged_legacy_passwords_still_work(self):
        directory = self.root/'legacy-keys'
        auth = web_auth.Auth(directory, PASSWORDS)
        legacy = {n:'legacy-password-'+n for n in PASSWORDS}
        # Simulate credentials created before the letter/digit requirement.
        with auth.db:
            for name, password in legacy.items():
                salt = os.urandom(16)
                auth.db.execute('UPDATE accounts SET salt=?,hash=? WHERE name=?',
                                (salt,web_auth.password_hash(password,salt),name))
        auth.close()
        restarted = web_auth.Auth(directory,legacy)
        self.addCleanup(restarted.close)
        token, _ = restarted.login('user',legacy['user'],'client')
        self.assertIsNotNone(restarted.session(token))
        with self.assertRaises(ValueError):
            web_auth.Auth(directory,{**legacy,'user':'changed-letters-only'})

    def test_lockout_persists_and_requires_explicit_account_reset(self):
        self.enable()
        auth = self.server.auth
        token, _ = auth.login('user',PASSWORDS['user'],'signed-in-client')
        start = time.time()
        with patch.object(auth,'_wait_before_verification') as wait:
            for i in range(13):
                # Span rate-limit windows without expiring the failure streak.
                with patch('web_auth.time.time', return_value=start+i*7):
                    with self.assertRaises(web_auth.Denied) as error:
                        auth.login('user','wrong','attacking-client')
                    self.assertEqual(error.exception.status,423 if i==12 else 401)
            self.assertEqual(wait.call_count,10)
        self.assertIsNotNone(auth.session(token))
        with auth.db:
            auth.db.execute('UPDATE login_failures SET updated=0')
        restarted = web_auth.Auth(self.lab.directory/'.auth',PASSWORDS)
        self.addCleanup(restarted.close)
        with patch('web_auth.password_hash') as verify:
            with self.assertRaises(web_auth.Denied) as error:
                restarted.login('user',PASSWORDS['user'],'different-client')
            self.assertEqual(error.exception.status,423)
            verify.assert_not_called()
        restarted.login('guest',PASSWORDS['guest'],'different-client')
        reset = web_auth.Auth(self.lab.directory/'.auth',PASSWORDS,resets={'user':'reset-once'})
        self.addCleanup(reset.close)
        self.assertIsNotNone(reset.session(token))
        reset.login('user',PASSWORDS['user'],'different-client')
        with reset.db:
            reset.db.execute("UPDATE account_locks SET locked=1 WHERE name='user'")
        same_reset = web_auth.Auth(self.lab.directory/'.auth',PASSWORDS,resets={'user':'reset-once'})
        self.addCleanup(same_reset.close)
        with self.assertRaises(web_auth.Denied) as error:
            same_reset.login('user',PASSWORDS['user'],'different-client')
        self.assertEqual(error.exception.status,423)
        changed = web_auth.Auth(self.lab.directory/'.auth',{**PASSWORDS,'user':'New123'},resets={'user':'reset-once'})
        self.addCleanup(changed.close)
        self.assertIsNone(changed.session(token))
        changed.login('user','New123','different-client')
