"""Disposable console broker and optional real OpenSSH-container integration."""
import base64
import json
import http.client
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import time
import unittest
import uuid

import console_capture
import external_access
from external_tty import Escape, Wire
import internet_cloud
import test_lab
import web_auth


class ExternalAccessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.directory = self.fixture.root/'broker'
        self.access = external_access.Access(self.fixture.lab, socket_dir=self.directory, enabled=True)
        self.fixture.server.access = self.access
        self.fixture.lab.access = self.access
        self.addCleanup(self.access.close)
        self.keypath = self.fixture.root/'client-key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(self.keypath)], check=True)
        self.key = self.keypath.with_suffix('.pub').read_text().strip()
        self.grant('writer')
        self.fixture.lab.start('r1')

    def grant(self, role):
        for grant in list(self.access.grants):
            self.access.change({'action':'revoke','id':grant['id']})
        return self.access.change({'action':'add','label':'Test laptop','key':self.key,'role':role})

    def wire(self):
        sock=socket.socket(socket.AF_UNIX);sock.connect(str(self.directory/'broker.sock'))
        self.addCleanup(sock.close)
        return Wire(sock)

    def attach(self):
        wire=self.wire();wire.send({'op':'session','key':' '.join(self.key.split()[:2])})
        self.assertEqual(wire.receive(3)['type'],'nodes')
        wire.send({'op':'attach','node':'r1'})
        return wire

    def until(self, wire, predicate):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            result=wire.receive(.2)
            if result and predicate(result):return result
        self.fail('Missing broker response')

    def test_key_validation_roles_and_deployment_storage(self):
        # Synthetic header only, assembled to avoid credential-scanner false positives.
        private_header = '-----BEGIN ' + 'PRIVATE KEY-----'
        for value in ['command="sh" '+self.key, self.key+'\n'+self.key, private_header, 'ssh-ed25519 bad!']:
            with self.assertRaises(ValueError):external_access.public_key(value)
        self.assertFalse(web_auth.allowed('writer','POST','/api/external-access'))
        self.assertFalse(web_auth.allowed('reader','GET','/api/external-access'))
        self.assertTrue(web_auth.allowed('admin','POST','/api/external-access'))
        self.assertNotIn('key', self.access.info()['keys'][0])
        self.assertNotIn('external-access', json.dumps(self.fixture.lab.topology))
        self.assertEqual(self.access.path.stat().st_mode & 0o777, 0o600)

    def test_reader_cannot_input_and_revocation_closes(self):
        self.grant('reader');wire=self.attach()
        lock=self.until(wire,lambda m:m['type']=='lock')['state'];self.assertTrue(lock['readonly'])
        wire.send({'op':'input','data':base64.b64encode(b'NO-READER-INPUT').decode()})
        wire.send({'op':'control','control':{'action':'lock','revision':lock['revision']}})
        self.access.change({'action':'revoke','id':self.access.grants[0]['id']})
        self.assertIn('revoked',self.until(wire,lambda m:m['type']=='error')['message'])
        console=console_capture.Console(self.fixture.lab.runtime['r1']['port'])
        self.addCleanup(console.close)
        output=console.window(.2,10000)
        self.assertNotIn('NO-READER-INPUT',str(output))

    def test_browser_lock_shared_output_and_disconnect(self):
        browser=console_capture.Console(self.fixture.lab.runtime['r1']['port'],
            human_identity=('admin','a'*64));self.addCleanup(browser.close)
        browser.acquire()
        wire=self.attach();state=self.until(wire,lambda m:m['type']=='lock')['state']
        self.assertEqual(state['takeover_mode'],'request');self.assertFalse(state['mine'])
        wire.send({'op':'input','data':base64.b64encode(b'BLOCKED-SSH').decode()})
        wire.send({'op':'control','control':{'action':'request-takeover','revision':state['revision']}})
        deadline=time.monotonic()+4
        while not (browser.lock.get('pending_takeover') or {}).get('id'):
            browser.receive(deadline)
        request=browser.lock['pending_takeover']
        browser.send(json.dumps({'action':'approve-takeover','revision':browser.lock['revision'],
                                'request_id':request['id']}).encode(),1)
        self.until(wire,lambda m:m['type']=='lock' and m['state']['mine'])
        wire.send({'op':'input','data':base64.b64encode(b'ALLOWED-SSH\x03\t').decode()})
        self.until(wire,lambda m:m['type']=='output' and b'ALLOWED-SSH' in base64.b64decode(m['data']))
        wire.sock.close()
        while browser.lock['locked']:
            browser.receive(time.monotonic()+4)
        self.assertTrue(self.fixture.lab.alive(self.fixture.lab.runtime['r1']['console']))

    def test_expiry_stop_and_forbidden_operations(self):
        self.access.lifetime=.2
        wire=self.attach()
        self.until(wire,lambda m:m['type']=='error' and 'expired' in m['message'])
        self.access.lifetime=60
        wire=self.attach();self.until(wire,lambda m:m['type']=='lock')
        wire.send({'op':'exec','command':'id'})
        self.until(wire,lambda m:m['type']=='error')
        wire=self.attach();self.until(wire,lambda m:m['type']=='lock')
        self.fixture.lab.stop('r1')
        self.until(wire,lambda m:m['type']=='error')

    def test_escape_and_partial_wire(self):
        parser=Escape()
        self.assertEqual(parser.feed(b'hello\x1dlEVIL'),[('input',b'hello'),('menu',None)])
        self.assertEqual(parser.feed(b'\x1d'),[('input',b'\x1d')])
        self.assertEqual(parser.feed(b'\x1d\x1d'),[('input',b'\x1d')])
        a,b=socket.socketpair();self.addCleanup(a.close);self.addCleanup(b.close)
        wire=Wire(a);data=b'{"op":"hello"}'
        b.sendall(struct.pack('!I',len(data))+data[:3]);self.assertIsNone(wire.receive(.01))
        b.sendall(data[3:]);self.assertEqual(wire.receive(.2),{'op':'hello'})
        b.sendall(struct.pack('!I',99999999))
        with self.assertRaises(ValueError):wire.receive(.2)
        a.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,4096);a.settimeout(.02)
        with self.assertRaises(socket.timeout):wire.send({'data':'x'*60000})

    def test_gateway_settings_and_conflicts(self):
        config=internet_cloud.settings({'ssh':{'enabled':True}})
        self.assertEqual(config['ssh']['external_port'],22022)
        with self.assertRaisesRegex(ValueError,'conflict'):
            internet_cloud.check_bindings([config,config])
        with self.assertRaises(ValueError):internet_cloud.ssh_settings({'enabled':'true'})
        with self.assertRaises(ValueError):internet_cloud.ssh_settings({'enabled':True,'source':'invalid'})

    def test_key_api_csrf_roles_and_account_reset(self):
        passwords={'admin':'Admin-example-12345','user':'Writer123','guest':'Reader123'}
        auth=web_auth.Auth(self.fixture.root/'auth',passwords)
        self.addCleanup(auth.db.close)
        self.fixture.server.auth=auth;self.access.auth=auth
        def request(role, method='GET', data=None, csrf=True):
            token,_=auth.login(role,passwords[role],'127.0.0.1')
            headers={'Cookie':web_auth.COOKIE+'='+token,'Content-Type':'application/json'}
            if csrf:headers['X-Weblab-CSRF']=auth.session(token)['csrf']
            client=http.client.HTTPConnection('127.0.0.1',self.fixture.port)
            try:
                client.request(method,'/api/external-access',json.dumps(data) if data is not None else None,headers)
                response=client.getresponse();response.read();return response.status
            finally:client.close()
        self.assertEqual(request('guest'),403);self.assertEqual(request('user'),403)
        self.assertEqual(request('admin','POST',{'action':'revoke','id':self.access.grants[0]['id']},False),403)
        self.assertEqual(request('admin'),200)
        self.grant('writer')
        self.assertTrue(self.access.valid(self.access.grants[0]))
        with auth.db:
            auth.db.execute("UPDATE account_locks SET locked=1 WHERE name='user'")
        self.assertFalse(self.access.valid(self.access.grants[0]))
        self.grant('writer')  # A grant created while locked must not become valid.
        self.assertFalse(self.access.valid(self.access.grants[0]))
        with auth.db:
            auth.db.execute("UPDATE account_locks SET locked=0,reset_marker='new' WHERE name='user'")
        self.assertFalse(self.access.valid(self.access.grants[0]))


@unittest.skipUnless(os.environ.get('WL_TEST_SSH_IMAGE'), 'Set WL_TEST_SSH_IMAGE for disposable Docker/OpenSSH checks')
class OpenSSHTests(unittest.TestCase):
    setUp = ExternalAccessTests.setUp
    grant = ExternalAccessTests.grant
    def test_real_ssh_commands_and_denied_forwarding(self):
        name='wl-ssh-test-'+uuid.uuid4().hex[:10]
        image=os.environ['WL_TEST_SSH_IMAGE']
        def docker(*args):
            return subprocess.check_output(['docker',*args],text=True,stderr=subprocess.STDOUT).strip()
        docker('run','-d','--name',name,'--publish','127.0.0.1::2222',
               '--cap-drop','ALL','--cap-add','SETUID','--cap-add','SETGID','--cap-add','SYS_CHROOT',
               '--cap-add','CHOWN','--cap-add','DAC_OVERRIDE','--cap-add','KILL',
               '--security-opt','no-new-privileges','--read-only','--tmpfs','/run/ssh','--tmpfs','/run/sshd',
               '--tmpfs','/state','--tmpfs','/tmp',
               '--mount',f'type=bind,source={self.directory},target=/run/weblab-access',
               '--entrypoint','python3',image,'/opt/weblab/access_service.py','console')
        self.addCleanup(lambda:subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,check=False))
        port=docker('port',name,'2222/tcp').rsplit(':',1)[1]
        command=['ssh','-p',port,'-i',str(self.keypath),'-o','IdentitiesOnly=yes','-o','IdentityAgent=none',
                 '-o','StrictHostKeyChecking=no','-o',f'UserKnownHostsFile={self.fixture.root}/known_hosts',
                 '-o','BatchMode=yes','-o','ConnectTimeout=2','console@127.0.0.1']
        deadline=time.monotonic()+10
        while True:
            result=subprocess.run([*command,'list'],capture_output=True,timeout=5)
            if result.returncode==0:break
            if time.monotonic()>deadline:self.fail(result.stderr.decode()+'\n'+docker('logs',name))
            time.sleep(.2)
        self.assertIn(b'r1',result.stdout)
        self.assertNotIn(b'Could not chdir',result.stderr)
        self.assertNotIn('Unsupported option', docker('logs',name))
        result=subprocess.run([*command,'id'],capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0);self.assertNotIn(b'uid=',result.stdout)
        result=subprocess.run([*command[:-1],'-W','127.0.0.1:8080',command[-1]],capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        session=subprocess.Popen([*command[:-1],'-tt',command[-1],'console r1'],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            captured=b''
            def read(marker):
                nonlocal captured
                deadline=time.monotonic()+5
                while marker not in captured and time.monotonic()<deadline:
                    if select.select([session.stdout],[],[],.1)[0]:
                        chunk=os.read(session.stdout.fileno(),8192)
                        if not chunk:break
                        captured+=chunk
                self.assertIn(marker,captured)
            read(b'BOOT READY')
            session.stdin.write(b'real-ssh-input\t');session.stdin.flush();read(b'RX:real-ssh-input')
            self.access.change({'action':'revoke','id':self.access.grants[0]['id']})
            session.wait(timeout=5)
            self.assertTrue(self.fixture.lab.alive(self.fixture.lab.runtime['r1']['console']))
        finally:
            if session.poll() is None:session.kill();session.wait()
            for stream in (session.stdin,session.stdout,session.stderr):stream.close()
        result=subprocess.run([*command,'list'],capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
