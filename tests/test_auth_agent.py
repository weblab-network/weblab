"""Authenticated Agent ownership and revocation using disposable real transports."""
import copy
import hashlib
import json
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

import agent_bridge
import agent_companion
import lab_server
from agent_transport import UnixServer, UnixHTTP, request
import web_auth
import console_capture
import test_agent
import test_web_auth
import test_console_lock
import test_lab


class AuthAgentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_web_auth.AuthTests()
        self.fixture.setUp()
        self.auth = web_auth.Auth(self.fixture.lab.directory / '.auth', test_web_auth.PASSWORDS)
        self.fixture.server.auth = self.auth
        self.root = self.fixture.root / 'agent'
        self.rpc = patch.object(agent_companion, 'RPC', test_agent.FakeRPC)
        self.rpc.start()
        self.manager = agent_companion.Manager(self.root/'sock', self.root/'companion', self.root/'codex')
        self.server = UnixServer(self.root/'sock'/'companion.sock', agent_companion.Handler)
        self.server.manager = self.manager
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.bridge = agent_bridge.Bridge(self.fixture.lab, self.root/'sock', self.root/'bridge',
                                         'http://127.0.0.1:11434/v1', auth=self.auth)
        self.fixture.server.agent = self.bridge
        self.cookie, self.csrf = self.fixture.login()
        self.owner = web_auth.digest(self.cookie.split('=', 1)[1])
        created = self.http('session', {})
        self.assertEqual(created[0], 200)
        self.token = created[2]['token']
        self.session = self.bridge.resolve(self.token, self.owner)

    def tearDown(self):
        self.bridge.close()
        self.server.shutdown(); self.server.server_close(); self.worker.join()
        for session in list(self.manager.sessions.values()):
            session.close()
        self.rpc.stop()
        self.auth.close()
        self.fixture.tearDown()

    def http(self, action, data=None, cookie=None, csrf=None, token=None):
        return self.fixture.http('/api/agent/' + action, 'GET' if data is None else 'POST', data,
                                 self.cookie if cookie is None else cookie,
                                 self.csrf if csrf is None else csrf,
                                 {'X-Weblab-Agent-Session':getattr(self, 'token', '') if token is None else token})

    def wait(self, predicate):
        deadline = time.monotonic() + 6
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.025)
        self.assertTrue(predicate())

    def gateway(self, path, data=None):
        return request(self.root/'sock'/'gateway.sock', path, data, self.session['gate'])

    def test_roles_csrf_and_exact_login_ownership_for_all_conversation_routes(self):
        self.assertEqual(self.http('session', {}, csrf='')[0], 403)
        self.assertEqual(self.http('info', cookie='')[0], 401)
        self.assertEqual(self.http('info')[2]['session_scope'],
                         hashlib.sha256(self.owner.encode()).hexdigest())
        for role in ('user', 'guest', 'admin'):
            cookie, csrf = self.fixture.login(role)
            for action, data in [('state', None), ('history-read', {'id':'a'*32}),
                                 ('history-export', {'id':'a'*32}), ('attachment', {'id':'a','conversation_id':'a'}),
                                 ('settings', {}), ('turn', {}), ('decision', {}), ('stop', {}),
                                 ('login', {}), ('login-status', {}), ('close', {})]:
                with self.subTest(role=role, action=action):
                    code = self.http(action, data, cookie, csrf)[0]
                    self.assertEqual(code, (409 if data is None else 400) if role=='admin' else 403)
            if role != 'admin':
                self.assertEqual(self.http('info', cookie=cookie, csrf=csrf)[2], {'enabled':False})
                self.assertEqual(self.http('session', {}, cookie, csrf)[0], 403)
            self.assertEqual(self.fixture.http('/api/automation/state',cookie=cookie)[0],403)
        # Another browser logging in as the shared admin does not end this login.
        self.assertEqual(self.http('state')[0], 200)
        state = self.gateway('/tools/api/automation/state')
        self.assertIn('nodes', state['topology'])
        with self.assertRaises(ValueError):
            self.gateway('/tools/api/topology', {})
        with self.assertRaises(ValueError):
            self.gateway('/tools/api/automation/start-node', {})

    def test_logout_cancels_pending_approval_and_deletes_private_companion_state(self):
        self.session['settings']['lab_changes'] = True
        self.assertEqual(self.http('turn', {'prompt':'Inspect the lab', 'request_id':'a'*32})[0], 200)
        local = self.manager.sessions[self.session['id']]
        self.wait(lambda:local.status == 'running')
        # Stand-in private login file: deletion must cover the entire session home.
        home = local.path.parent/'codex'
        home.mkdir(exist_ok=True)
        (home/'auth.json').write_text('{"test":"not-a-credential"}')
        outcome = {}
        arguments = {'node_id':'r1','expected_revision':self.bridge.automation.state()['revision']}
        def start():
            try:
                outcome['result'] = self.bridge.tool(self.session, 'POST', '/api/automation/start-node', arguments, 'a'*32)
            except Exception as exc:
                outcome['error'] = str(exc)
        task = threading.Thread(target=start)
        with patch.object(self.bridge.automation, 'start_node', wraps=self.bridge.automation.start_node) as mutation:
            task.start()
            self.wait(lambda:bool(self.bridge.approvals.get(self.session['id'])))
            pending = self.bridge.approvals[self.session['id']][0]
            self.assertEqual(pending['status'], 'pending')
            code = self.fixture.http('/api/auth/logout','POST',{},self.cookie,self.csrf)[0]
            self.assertEqual(code,200)
            task.join(3)
            self.assertFalse(task.is_alive())
            self.assertIn('cancelled',outcome['error'])
            mutation.assert_not_called()
        self.assertNotIn(self.session['id'],self.bridge.turns)
        with self.assertRaises(ValueError): self.gateway('/tools/api/automation/state')
        self.wait(lambda:not local.path.parent.exists())
        self.assertFalse(local.worker.is_alive())
        self.assertTrue(local.stop.is_set())
        fresh, csrf = self.fixture.login()
        self.assertEqual(self.http('state',cookie=fresh,csrf=csrf)[0],409)

    def test_admin_agent_can_start_and_type_but_cannot_bypass_a_human_lock(self):
        settings = {**self.session['settings'], 'lab_changes':True,
                    'console_input':True, 'auto_approve':True}
        self.assertEqual(self.http('settings',settings)[0],200)
        turn = 'b'*32
        self.assertEqual(self.http('turn',{'prompt':'Inspect the lab','request_id':turn})[0],200)
        local = self.manager.sessions[self.session['id']]
        self.wait(lambda:local.status == 'running')
        def tool(operation, data):
            return self.bridge.tool(self.session,'POST','/api/automation/'+operation,data,turn)
        started = tool('start-node',{'node_id':'r1','expected_revision':self.bridge.automation.state()['revision']})
        self.assertTrue(started['started'])
        read = tool('console-read',{'node_id':'r1','wait_seconds':.1})
        sent = tool('console-send',{'node_id':'r1','input':'agent-test\r',
                    'expected_revision':read['revision'],'expected_cursor':read['cursor'],'wait_seconds':.1})
        self.assertEqual(sent['input_status'],'sent')
        records = self.auth.activity_page({'source':['agent']})['records']
        self.assertEqual(records[0]['result'],'input_sent')
        self.assertEqual(records[0]['account'],'admin')
        self.assertEqual(records[0]['target'],'r1')
        self.assertNotIn('agent-test',json.dumps(records))
        self.assertNotIn(self.session['gate'],json.dumps(records))
        self.assertEqual(records[1]['operation'],'tool.start-node')
        self.assertEqual(records[1]['result'],'tool_ok')
        writer, _ = self.fixture.login('user')
        client = test_lab.WebSocket(self.fixture.port,'r1','netlab.console.v2',headers={'Cookie':writer})
        try:
            client.output, client.lock = b'', None
            wait = lambda predicate: test_console_lock.ConsoleLockTests.read_until(self,client,predicate)
            wait(lambda:client.lock is not None)
            client.send(json.dumps({'action':'lock','revision':client.lock['revision']}).encode())
            wait(lambda:client.lock['mine'])
            read = tool('console-read',{'node_id':'r1','wait_seconds':.1})
            with self.assertRaisesRegex(Exception,'lock|owner'):
                tool('console-send',{'node_id':'r1','input':'must-not-send\r',
                    'expected_revision':read['revision'],'expected_cursor':read['cursor'],'wait_seconds':.1})
            self.assertTrue(client.lock['mine'])
        finally:
            client.close()

    def test_expiry_rejects_gateway_before_cleanup_and_retries_offline_cleanup(self):
        self.assertEqual(self.http('state')[0],200)
        with patch.object(self.bridge, 'companion', side_effect=ValueError('offline')):
            with self.auth.lock, self.auth.db:
                self.auth.db.execute('UPDATE sessions SET expires=0 WHERE token=?',(self.owner,))
            with self.assertRaises(ValueError): self.gateway('/tools/api/automation/state')
            # CONNECT is independently checked, before any external connection.
            self.session['settings'].update(provider='openai',endpoint=agent_bridge.OPENAI_ENDPOINT)
            connection = UnixHTTP(self.root/'sock'/'gateway.sock')
            connection.request('CONNECT','chatgpt.com:443',headers={'Authorization':'Bearer '+self.session['gate']})
            response = connection.getresponse()
            self.assertEqual(response.status,403); response.read(); connection.close()
            self.bridge.clean_retired()
            stored = json.loads(self.bridge.path.read_text())
            self.assertTrue(next(iter(stored.values()))['retired'])
        self.wait(lambda:not self.bridge.sessions)
        self.assertFalse((self.manager.state/self.session['id']).exists())

    def test_agent_borrows_only_its_login_lock_and_preserves_human_ownership(self):
        self.fixture.lab.start('r1')
        self.session['settings'].update(console_input=True, auto_approve=True)
        turn = 'c'*32
        self.assertEqual(self.http('turn', {'prompt':'Inspect console', 'request_id':turn})[0], 200)
        self.wait(lambda:self.manager.sessions[self.session['id']].status == 'running')
        def tool(operation, data):
            return self.bridge.tool(self.session, 'POST', '/api/automation/'+operation, data, turn)
        other_admin, _ = self.fixture.login()
        writer, _ = self.fixture.login('user')
        for cookie in (self.cookie, other_admin, writer):
            with self.subTest(same_login=cookie == self.cookie):
                human = test_lab.WebSocket(self.fixture.port, 'r1', 'netlab.console.v2', headers={'Cookie':cookie})
                try:
                    human.output, human.lock = b'', None
                    wait = lambda predicate:test_console_lock.ConsoleLockTests.read_until(self, human, predicate)
                    wait(lambda:human.lock is not None)
                    human.send(json.dumps({'action':'lock', 'revision':human.lock['revision']}).encode())
                    wait(lambda:human.lock['mine'])
                    revision = human.lock['revision']
                    read = tool('console-read', {'node_id':'r1', 'wait_seconds':.1})
                    self.assertEqual(read['lock_owned_by_session'], cookie == self.cookie)
                    arguments = {'node_id':'r1', 'input':'borrowed-input\r', 'expected_revision':read['revision'],
                                 'expected_cursor':read['cursor'], 'wait_seconds':.1}
                    # Models cannot supply console authority through tool arguments.
                    with self.assertRaises(TypeError):
                        tool('console-send', {**arguments, 'identity':self.owner})
                    if cookie == self.cookie:
                        # Fresh-read/cursor protection still applies to an owned lock.
                        human.send(b'human-intervened\r', 2)
                        wait(lambda:b'human-intervened' in human.output)
                        with self.assertRaisesRegex(Exception, 'Console changed'):
                            tool('console-send', arguments)
                        read = tool('console-read', {'node_id':'r1', 'wait_seconds':.1})
                        arguments.update(expected_revision=read['revision'], expected_cursor=read['cursor'])
                        self.assertEqual(tool('console-send', arguments)['input_status'], 'sent')
                        wait(lambda:b'borrowed-input' in human.output)
                        read = tool('console-read', {'node_id':'r1', 'wait_seconds':.1})
                        arguments.update(input='interrupted-agent-must-not-reach\r',
                                         expected_revision=read['revision'], expected_cursor=read['cursor'])
                        original = console_capture.Console.send
                        def intervene(console, data, opcode=2):
                            if opcode == 2:
                                wait(lambda:human.lock['agent_busy'] and human.lock['revision'] == console.lock['revision'])
                                human.send(json.dumps({'action':'reclaim', 'revision':human.lock['revision']}).encode())
                                wait(lambda:not human.lock['agent_busy'])
                            return original(console, data, opcode)
                        with patch.object(console_capture.Console, 'send', intervene):
                            result = tool('console-send', arguments)
                        self.assertEqual(result['input_status'], 'unknown')
                        self.assertEqual(self.auth.activity_page({'source':['agent']})['records'][0]['result'], 'uncertain')
                        self.assertNotIn(b'interrupted-agent-must-not-reach', human.output)
                    else:
                        with self.assertRaisesRegex(Exception, 'lock|owner'):
                            tool('console-send', arguments)
                    # Drain queued lock updates and prove the browser still owns it
                    # after the short-lived Agent connection has closed.
                    human.send(b'ownership-barrier', 9)
                    while True:
                        opcode, payload = human.receive()
                        if opcode == 10 and payload == b'ownership-barrier': break
                        if opcode == 1:
                            message = json.loads(payload)
                            if message['type'] == 'console-lock': human.lock = message
                    self.assertTrue(human.lock['mine'])
                    self.assertFalse(human.lock['agent_busy'])
                    self.assertGreaterEqual(human.lock['revision'], revision)
                    self.assertNotIn(self.owner, json.dumps(human.lock))
                    self.assertNotIn(web_auth.console_identity(self.owner), json.dumps(human.lock))
                    human.send(json.dumps({'action':'unlock', 'revision':human.lock['revision']}).encode())
                    wait(lambda:not human.lock['locked'])
                finally:
                    human.close()

    def test_companion_cleanup_retries_disk_errors_without_recreating_sessions(self):
        self.assertEqual(self.http('state')[0],200)
        self.manager.sessions[self.session['id']].persist()
        payload = {'session':self.session['id'],'settings':self.session['settings'],
                   'gateway_token':self.session['gate']}
        with patch.object(agent_companion.shutil,'rmtree',side_effect=PermissionError('busy')):
            with self.assertRaises(PermissionError): self.manager.handle('delete',payload)
        self.assertNotIn(self.session['id'],self.manager.sessions)
        self.assertTrue((self.manager.state/self.session['id']).exists())
        self.manager.handle('delete',payload)
        self.assertFalse((self.manager.state/self.session['id']).exists())
        self.manager.handle('delete',payload)  # Idempotent after deletion/restart.
        self.assertNotIn(self.session['id'],self.manager.sessions)

    def test_restart_preserves_valid_owner_but_never_adopts_anonymous_or_auth_off_sessions(self):
        self.assertEqual(self.http('state')[0],200)
        conversation = self.http('state')[2]['conversation_id']
        self.bridge.close()
        self.bridge = agent_bridge.Bridge(self.fixture.lab,self.root/'sock',self.root/'bridge',
                                         'http://127.0.0.1:11434/v1',auth=self.auth)
        self.fixture.server.agent = self.bridge
        self.assertEqual(self.http('state')[2]['conversation_id'],conversation)
        self.session = self.bridge.resolve(self.token,self.owner)
        anonymous = copy.deepcopy(self.session)
        anonymous['owner'] = None
        with self.assertRaises(ValueError): self.bridge.require_owner(anonymous)
        # Disabling auth must not turn login-bound history into a bearer-only session.
        self.bridge.auth = None
        with self.assertRaises(ValueError): self.bridge.resolve(self.token)
        self.bridge.auth = self.auth
        self.assertEqual(self.http('state')[0],200)

    def test_revocation_closes_an_ollama_stream_waiting_for_its_first_byte(self):
        entered, release = threading.Event(), threading.Event()
        class Slow(lab_server.BaseHTTPRequestHandler):
            def do_POST(handler):
                handler.rfile.read(int(handler.headers['Content-Length']))
                entered.set(); release.wait(5)
            def log_message(handler,*args): pass
        provider = lab_server.ThreadingHTTPServer(('127.0.0.1',0),Slow)
        thread = threading.Thread(target=provider.serve_forever,daemon=True); thread.start()
        endpoint = 'http://127.0.0.1:'+str(provider.server_port)+'/v1'
        self.bridge.providers.append(endpoint)
        self.session['settings']['endpoint'] = endpoint
        result = {}
        def relay():
            try:
                self.bridge.provider(self.session['settings'],'POST','/responses',b'{}',MagicMock(),self.session)
            except Exception as exc: result['error'] = str(exc)
        stream = threading.Thread(target=relay); stream.start()
        try:
            self.assertTrue(entered.wait(3))
            self.fixture.http('/api/auth/logout','POST',{},self.cookie,self.csrf)
            stream.join(3)
            self.assertFalse(stream.is_alive())
            self.assertIn('error',result)
        finally:
            release.set(); stream.join(5)
            provider.shutdown(); provider.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
