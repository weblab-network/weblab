"""Optional Agent boundary and session lifecycle checks; no inference or real guests."""
import copy
import base64
import collections
import json
from pathlib import Path
import queue
import os
import socket
import tempfile
import threading
import time
import unittest
import struct
import zlib
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import agent_bridge
import agent_companion
import agent_attachments
from agent_transport import request
import lab_server

REAL_RPC = agent_companion.RPC


def screenshot():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))


def attachment(name, raw):
    return {'name': name, 'data': base64.b64encode(raw).decode()}


class AttachmentValidationTests(unittest.TestCase):
    def test_formats_and_limits(self):
        files=agent_attachments.validate([attachment('topology.PNG',screenshot()),attachment('router.cfg',b'router ospf\n')])
        self.assertEqual(files[0]['mime'],'image/png')
        self.assertEqual(files[1]['text'],'router ospf\n')
        for model in ('gpt-oss:20b','gpt-oss-120b','local/gpt-oss:20b'):
            with self.assertRaisesRegex(ValueError,'text-only'):
                agent_attachments.validate([attachment('topology.png',screenshot())],model)
        for items in (None, {}, [attachment('a.txt',b'a')]*5,
                      [attachment('../a.txt',b'a')], [attachment('a\\b.txt',b'a')],
                      [attachment('a.txt\n',b'a')], [attachment('a.svg',b'<svg/>')],
                      [attachment('a.pdf',b'%PDF-1')], [attachment('a.txt',b'\x00binary')],
                      [attachment('a.cfg',b'\xff')], [attachment('a.txt',b'')],
                      [{'name':'a.txt','data':'!invalid!'}],
                      [{'name':'a.txt','data':'YQ==','path':'/etc/passwd'}],
                      [attachment('a.png',b'not an image')],
                      [attachment('a.png',screenshot()[:16]+struct.pack('>II',10000,10000))],
                      [attachment('a.cfg',b'x'*(64*1024)),attachment('b.txt',b'x')],
                      [attachment('a.png',b'x'*(2*1024*1024+1))]):
            with self.subTest(items=str(items)[:80]),self.assertRaises(ValueError):
                agent_attachments.validate(items)

    def test_jpeg_webp_headers(self):
        jpg=b'\xff\xd8\xff\xc0'+struct.pack('>HBHH',7,8,10,20)
        webp=b'RIFF'+b'\x00'*4+b'WEBPVP8X'+b'\x0a\x00\x00\x00'+b'\x00'*4+b'\x13\x00\x00\x09\x00\x00'
        self.assertEqual(agent_attachments.image_dimensions(jpg,'image/jpeg'),(20,10))
        self.assertEqual(agent_attachments.image_dimensions(webp,'image/webp'),(20,10))
        with self.assertRaises(ValueError):
            agent_attachments.image_dimensions(webp[:20]+b'\x02'+webp[21:],'image/webp')


class AgentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-unit-')
        self.root = Path(self.tmp.name)
        self.lab = lab_server.Lab(self.root/'lab', self.root/'images')
        self.bridge = agent_bridge.Bridge(self.lab, self.root/'sock', self.root/'state', 'http://127.0.0.1:11434/v1')
        self.server = lab_server.ThreadingHTTPServer(('127.0.0.1', 0), lab_server.Handler)
        self.server.lab, self.server.agent = self.lab, self.bridge
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.created = self.bridge.api('session')
        self.token = self.created['token']
        self.session = self.bridge.resolve(self.token)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.bridge.close(); self.lab.file_lock.close(); self.tmp.cleanup()

    def test_attachment_http_validation_before_turn_and_isolation(self):
        payload={'prompt':'Inspect this','request_id':'b'*32,'attachments':[attachment('bad.png',b'not an image')]}
        headers={'Content-Type':'application/json','X-Weblab-Agent-Session':self.token}
        with patch.object(self.bridge,'companion') as companion:
            with self.assertRaises(HTTPError):
                urlopen(Request(self.url+'/api/agent/turn',json.dumps(payload).encode(),headers))
            companion.assert_not_called()
        self.assertNotIn('b'*32,self.session.get('seen',[]))
        # A valid >1 MB turn uses only this endpoint's larger body limit.
        self.session['settings']['model']='vision-test'
        payload['attachments']=[attachment('large.png',screenshot()+b'\x00'*800000)]
        with patch.object(self.bridge,'companion',return_value={'status':'idle'}) as companion:
            result=json.load(urlopen(Request(self.url+'/api/agent/turn',json.dumps(payload).encode(),headers)))
            self.assertEqual(result['status'],'idle')
            self.assertEqual(companion.call_args.args[0],'turn')
            self.assertEqual(companion.call_args.args[2]['attachments'],payload['attachments'])
        with self.assertRaises(HTTPError):
            urlopen(Request(self.url+'/api/agent/attachment',b'{"id":"x","conversation_id":"x"}',
                            {**headers,'X-Weblab-Agent-Session':'wrong'}))
        with self.assertRaises(HTTPError):
            urlopen(Request(self.url+'/api/agent/attachment',b'{"id":"x","conversation_id":"x"}',
                            {**headers,'Origin':'http://evil.test'}))
        with self.assertRaises(ValueError):
            request(self.root/'sock/gateway.sock','/tools/api/agent/attachment',{},self.session['gate'])

    def test_history_ownership_permissions_and_routes(self):
        manager = agent_companion.Manager(self.root/'history-sock', self.root/'history-state', self.root/'history-codex')
        def companion(action, session, data=None):
            return manager.handle(action, {'session': session['id'], 'settings': session['settings'],
                                          'gateway_token': session['gate'], **(data or {})})
        try:
            with patch.object(self.bridge, 'companion', side_effect=companion):
                first = self.bridge.api('state', self.token)['conversation_id']
                local = manager.sessions[self.session['id']]
                local.messages = [{'role':'user','text':'Private lab discussion'}]
                local.thread_id = 'original-thread'
                local.persist()
                self.bridge.api('reset', self.token, {})
                new_settings = {**self.created['settings'], 'model':'other-model', 'console_input':True}
                self.bridge.api('settings', self.token, new_settings)
                # Pending capabilities from the previous conversation must not survive opening history.
                pending = {'status':'pending', 'event':threading.Event()}
                self.bridge.approvals[self.session['id']] = [pending]
                self.bridge.observations[self.session['id']] = {'old':'read'}
                self.bridge.turns[self.session['id']] = {'id':'old-turn'}
                self.session['proposals'] = ['old-proposal']
                local.status='running'
                with self.assertRaisesRegex(ValueError,'Stop the active turn'):
                    self.bridge.api('history-open', self.token, {'id':first})
                self.assertEqual(pending['status'],'pending')
                self.assertEqual(self.session['settings']['model'],'other-model')
                local.status='idle'
                resumed = self.bridge.api('history-open', self.token, {'id':first})
                self.assertEqual(resumed['settings']['model'], self.created['settings']['model'])
                self.assertTrue(resumed['settings']['console_input'])
                self.assertFalse(resumed['settings']['auto_approve'])
                self.assertEqual(resumed['proposals'], [])
                self.assertEqual(resumed['approvals'], [])
                self.assertEqual(pending['status'], 'cancelled')
                self.assertTrue(pending['event'].is_set())
                self.assertNotIn(self.session['id'], self.bridge.turns)
                self.assertNotIn(self.session['id'], self.bridge.observations)
                headers={'Content-Type':'application/json','X-Weblab-Agent-Session':self.token}
                response=json.load(urlopen(Request(self.url+'/api/agent/history-read', json.dumps({'id':first}).encode(), headers)))
                self.assertEqual(response['messages'], local.messages)
                self.assertNotIn('thread_id', response)
                self.assertNotIn('seen', response)
                exported=json.load(urlopen(Request(self.url+'/api/agent/history-export', json.dumps({'id':first}).encode(), headers)))
                self.assertEqual(exported['messages'], local.messages)
                self.assertNotIn('settings', exported)
                self.assertNotIn('thread_id', exported)
                self.assertNotIn('id', exported)
                other=self.bridge.api('session')['token']
                for action in ('history-read','history-export','history-open','history-delete','history-rename'):
                    args={'id':first, **({'title':'Stolen'} if action=='history-rename' else {})}
                    with self.assertRaises(ValueError): self.bridge.api(action, other, args)
                with self.assertRaises(HTTPError):
                    urlopen(Request(self.url+'/api/agent/history-read', json.dumps({'id':first}).encode(),
                                    {**headers,'Origin':'http://evil.test'}))
                with self.assertRaises(ValueError):
                    request(self.root/'sock/gateway.sock','/tools/api/agent/history-read',{'id':first},self.session['gate'])
                with self.assertRaises(ValueError):
                    request(self.root/'sock/gateway.sock','/tools/api/agent/history-export',{'id':first},self.session['gate'])
                # Historical endpoints are checked against the current allowlist.
                self.bridge.api('reset', self.token, {})
                local.history[first]['settings']['endpoint']='http://removed-provider/v1'
                with self.assertRaisesRegex(ValueError,'WL_AGENT_PROVIDERS'):
                    self.bridge.api('history-open',self.token,{'id':first})
        finally:
            for session in manager.sessions.values(): session.close()

    def test_gateway_denies_raw_mutators_and_cross_session_proposals(self):
        socket = self.root/'sock/gateway.sock'
        gate = self.session['gate']
        before = copy.deepcopy(self.lab.topology)
        for path in ['/tools/api/topology', '/tools/api/lab/start', '/tools/api/lab/stop',
                     '/tools/api/automation/apply', '/tools/api/automation/console-send',
                     '/tools/api/automation/console-read', '/tools/api/images/foo', '/provider/../api/topology']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                request(socket, path, {}, gate)
        with self.assertRaises(ValueError):
            request(socket, '/tools/api/automation/state', token='wrong')
        self.assertEqual(request(socket, '/tools/api/automation/state', token=gate)['topology'], before)
        self.assertIsNone(self.lab.automation)  # Enabling Agent did not expose external MCP mutations.
        result = request(socket, '/tools/api/automation/preview', {
            'topology': {'version':1,'name':'Example','nodes':[], 'links':[]}, 'instructions':'Practice'}, gate)
        proposal_id = result['proposal_id']
        self.assertEqual(self.bridge.api('proposal', self.token, {'id':proposal_id})['topology']['name'], 'Example')
        other = self.bridge.api('session')['token']
        with self.assertRaises(ValueError):
            self.bridge.api('proposal', other, {'id':proposal_id})
        self.assertEqual(self.lab.topology, before)
        self.assertEqual(self.lab.runtime, {})

    def test_settings_allowlist_and_disabled_feature(self):
        valid = self.created['settings']
        for url in ['http://bad/v1', 'file:///v1', 'http://u:p@127.0.0.1:11434/v1',
                    'http://127.0.0.1:11434/v1?x=1', 'http://127.0.0.1:11434/api']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.bridge.settings({**valid, 'endpoint':url})
        for key,value in [('model','a\nconfig'),('context_window',True),('turn_timeout',3601),('auto_approve','yes')]:
            with self.assertRaises(ValueError):
                self.bridge.settings({**valid,key:value})
        self.assertEqual(self.bridge.settings(valid), valid)
        self.assertFalse(valid['auto_approve'])
        self.assertEqual(valid['turn_timeout'],900)
        self.assertEqual(self.bridge.settings({**valid,'turn_timeout':3600})['turn_timeout'],3600)
        self.server.agent = None
        self.assertEqual(json.load(urlopen(self.url+'/api/agent/info')), {'enabled':False})
        with self.assertRaises(HTTPError):
            urlopen(Request(self.url+'/api/agent/session', b'{}', {'Content-Type':'application/json'}))

    def test_openai_settings_and_browser_only_login(self):
        settings = {**self.created['settings'], 'provider':'openai',
                    'endpoint':agent_bridge.OPENAI_ENDPOINT, 'model':'gpt-5.6-sol'}
        self.assertEqual(self.bridge.settings(settings), settings)
        for change in ({'provider':'other'}, {'endpoint':'https://evil.test'},
                       {'endpoint':agent_bridge.OPENAI_ENDPOINT+'?token=secret'}):
            with self.assertRaises(ValueError): self.bridge.settings({**settings,**change})
        with self.assertRaisesRegex(ValueError,'Select OpenAI'):
            self.bridge.api('login',self.token,{})
        self.session['settings'] = settings
        with patch.object(self.bridge,'companion',return_value={'status':'idle','auth':{'status':'waiting'}}):
            with self.assertRaisesRegex(ValueError,'cancel sign-in'):
                self.bridge.api('settings',self.token,settings)
        with self.assertRaises(ValueError):
            request(self.root/'sock/gateway.sock','/tools/api/agent/login',{},self.session['gate'])
        # Ollama relay cannot be used to turn the OpenAI setting into arbitrary egress.
        with self.assertRaises(ValueError): self.bridge.provider(settings,'GET','/models')

    def test_browser_auth_routes_require_session_and_same_origin(self):
        self.session['settings'].update(provider='openai',endpoint=agent_bridge.OPENAI_ENDPOINT)
        with patch.object(self.bridge,'companion',return_value={'status':'connecting'}) as companion:
            for action in ('login','login-status','login-cancel','logout'):
                headers={'Content-Type':'application/json','X-Weblab-Agent-Session':self.token}
                response=json.load(urlopen(Request(self.url+'/api/agent/'+action,b'{}',headers)))
                self.assertEqual(response['status'],'connecting')
                self.assertEqual(companion.call_args.args[0],action)
                before=companion.call_count
                for changed in ({'X-Weblab-Agent-Session':'wrong'}, {'Origin':'https://evil.test'}):
                    with self.assertRaises(HTTPError):
                        urlopen(Request(self.url+'/api/agent/'+action,b'{}',{**headers,**changed}))
                self.assertEqual(companion.call_count,before)

    def test_openai_connect_allowlist_token_and_revocation(self):
        def connect(host, token):
            sock = socket.socket(socket.AF_UNIX); sock.settimeout(3)
            self.addCleanup(sock.close)
            sock.connect(str(self.root/'sock/gateway.sock'))
            sock.sendall(('CONNECT '+host+' HTTP/1.1\r\nAuthorization: Bearer '+token+'\r\n\r\n').encode())
            header=b''
            while not header.endswith(b'\r\n\r\n'): header+=sock.recv(1)
            return sock, header
        with patch.object(agent_bridge.socket,'create_connection') as upstream:
            _, head = connect('auth.openai.com:443',self.session['gate'])
            self.assertIn(b'403',head)
            self.session['settings']['provider']='openai'
            for host,token in [('evil.test:443',self.session['gate']),
                               ('auth.openai.com:80',self.session['gate']),
                               ('auth.openai.com:443','wrong')]:
                _,head=connect(host,token); self.assertIn(b'403',head)
            upstream.assert_not_called()
        left,right=socket.socketpair(); right.settimeout(3)
        self.addCleanup(right.close); self.addCleanup(left.close)
        with patch.object(agent_bridge.socket,'create_connection',return_value=left) as upstream:
            sock,head=connect('auth.openai.com:443',self.session['gate'])
            self.assertIn(b'200',head)
            upstream.assert_called_once_with(('auth.openai.com',443),timeout=10)
            sock.sendall(b'opaque TLS');self.assertEqual(right.recv(100),b'opaque TLS')
            right.sendall(b'reply');self.assertEqual(sock.recv(100),b'reply')
            self.session['settings']['provider']='ollama'
            self.assertEqual(sock.recv(100),b'')

    def test_permission_deadline_changes_keep_conversation_and_reject_active_turn(self):
        self.session['proposals'] = ['existing-proposal']
        settings = {**self.session['settings'], 'auto_approve':True, 'turn_timeout':1800}
        with patch.object(self.bridge,'companion',return_value={'status':'interrupted'}) as companion:
            self.bridge.api('settings',self.token,settings)
            self.assertEqual([c.args[0] for c in companion.call_args_list],['state'])
            self.assertEqual(self.session['proposals'],['existing-proposal'])
            companion.reset_mock()
            self.bridge.api('settings',self.token,{**settings,'model':'another-model'})
            self.assertEqual([c.args[0] for c in companion.call_args_list],['state','reset'])
            self.assertEqual(self.session['proposals'],[])
        self.bridge.turns[self.session['id']]={'id':'active','expires':time.monotonic()+30}
        with patch.object(self.bridge,'companion',return_value={'status':'running'}):
            with self.assertRaisesRegex(ValueError,'Stop the active turn'):
                self.bridge.api('settings',self.token,settings)
        self.assertEqual(self.bridge.turns[self.session['id']]['id'],'active')
        self.assertEqual(self.session['settings']['model'],'another-model')

    def test_tool_errors_are_session_scoped_and_clear_on_recovery(self):
        with self.assertRaisesRegex(lab_server.LabError,r'links\[0\].*endpoints'):
            self.bridge.tool(self.session,'POST','/api/automation/preview',{
                'topology':{'nodes':[], 'links':[{'endpoints':[]}]},'instructions':'test'})
        self.assertIn('Use a and b',self.bridge.tool_errors[self.session['id']]['message'])
        other = self.bridge.resolve(self.bridge.api('session')['token'])
        self.assertNotIn(other['id'],self.bridge.tool_errors)
        self.bridge.tool(self.session,'POST','/api/automation/preview',{
            'topology':{'nodes':[], 'links':[]},'instructions':'test'})
        self.assertNotIn(self.session['id'],self.bridge.tool_errors)

    def test_browser_origin_token_and_persistence(self):
        with self.assertRaises(HTTPError):
            urlopen(Request(self.url+'/api/agent/session', b'{}', {'Content-Type':'application/json','Origin':'http://evil.test'}))
        with self.assertRaises(HTTPError):
            urlopen(Request(self.url+'/api/agent/state', headers={'X-Weblab-Agent-Session':'bad'}))
        with self.assertRaises(ValueError):
            self.bridge.api('state', self.token)  # Unavailable companion is actionable, not a hung call.
        self.assertEqual(self.bridge.path.stat().st_mode & 0o777, 0o600)
        stored = json.loads(self.bridge.path.read_text())
        self.assertNotIn(self.token, stored)
        self.assertEqual(next(iter(stored.values()))['id'], self.session['id'])
        self.bridge.close()
        self.bridge = agent_bridge.Bridge(self.lab,self.root/'sock',self.root/'state','http://127.0.0.1:11434/v1')
        self.assertEqual(self.bridge.resolve(self.token)['id'],self.session['id'])
        self.bridge.touch_flushed -= 61
        resolved=self.bridge.resolve(self.token)
        stored=json.loads(self.bridge.path.read_text())
        self.assertEqual(next(iter(stored.values()))['touched'],resolved['touched'])
        resolved['touched']=time.time()-agent_bridge.TTL-1
        with self.assertRaisesRegex(ValueError,'expired'):
            self.bridge.api('history-read',self.token,{'id':'a'*32})


class AgentApprovalTests(unittest.TestCase):
    """Real automation and echo consoles, with only the inference companion mocked."""
    def setUp(self):
        import test_lab
        self.fixture = test_lab.LabTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-approval-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.lab = self.fixture.lab
        self.bridge = agent_bridge.Bridge(self.lab, root/'sockets', root/'state', 'http://localhost:11434/v1')
        self.addCleanup(self.bridge.close)
        self.token = self.bridge.api('session')['token']
        self.session = self.bridge.resolve(self.token)
        self.session['settings'].update(lab_changes=True, console_input=True)
        self.turn = 'a'*32
        self.state = {'status':'running', 'request_id':self.turn}
        self.mock = patch.object(self.bridge, 'companion', side_effect=lambda *a, **k:dict(self.state)).start()
        self.addCleanup(patch.stopall)
        self.bridge.turns[self.session['id']] = {'id': self.turn, 'expires':time.monotonic()+120}
        self.threads = []
        self.addCleanup(self.finish)

    def finish(self):
        with self.bridge.lock: self.bridge.revoke(self.session)
        for thread in self.threads: thread.join(3)

    def launch(self, operation, arguments):
        result = {}
        def run():
            try: result['result'] = self.bridge.tool(self.session, 'POST', '/api/automation/'+operation, arguments, self.turn)
            except Exception as exc: result['error'] = str(exc)
        thread = threading.Thread(target=run); self.threads.append(thread); thread.start()
        end = time.monotonic()+3
        while time.monotonic()<end:
            items = self.bridge.approval_state(self.session)
            if items and items[-1]['status']=='pending': return thread, result, items[-1]
            if not thread.is_alive(): self.fail(result)
            time.sleep(.01)
        self.fail('No approval surfaced')

    def decide(self, item, approve=True):
        return self.bridge.api('decision', self.token, {'id':item['id'], 'approve':approve})

    def console_arguments(self):
        self.lab.start('r1')
        observed = self.bridge.tool(self.session, 'POST', '/api/automation/console-read',
                                    {'node_id':'r1', 'wait_seconds':.2})
        return {'node_id':'r1', 'input':'show test\r', 'expected_cursor':observed['cursor'],
                'expected_revision':observed['revision'], 'wait_seconds':.2}

    def test_console_exact_input_single_use_and_fresh_read(self):
        args = self.console_arguments()
        thread, result, item = self.launch('console-send', args)
        self.assertEqual(item['arguments']['input'], 'show test\r')
        self.assertIn('BOOT READY', item['context']['console_output'])
        self.assertNotIn('RX:', self.bridge.automation.console_read('r1',wait_seconds=.1)['output'])
        # Caller cannot rewrite an operation after it has been offered for review.
        args['input'] = 'different\r'
        self.decide(item); thread.join(3)
        self.assertEqual(result['result']['input_status'], 'sent')
        self.assertIn('RX:show test', result['result']['output'])
        self.assertNotIn('different', result['result']['output'])
        with self.assertRaises(ValueError): self.decide(item)
        with self.assertRaisesRegex(ValueError, 'Read this console'):
            self.bridge.tool(self.session,'POST','/api/automation/console-send',item['arguments'],self.turn)

    def test_incomplete_console_read_requires_continuation_before_approval(self):
        args = self.console_arguments()
        first = self.bridge.tool(self.session, 'POST', '/api/automation/console-read',
                                 {'node_id': 'r1', 'wait_seconds': .1, 'max_bytes': 4})
        args.update(expected_cursor=first['cursor'], expected_revision=first['revision'])
        with self.assertRaisesRegex(ValueError, 'Console read is incomplete; no input was sent'):
            self.bridge.tool(self.session, 'POST', '/api/automation/console-send', args, self.turn)
        self.assertEqual(self.bridge.approval_state(self.session), [])
        rest = self.bridge.tool(self.session, 'POST', '/api/automation/console-read',
                                {'node_id': 'r1', 'cursor': first['cursor'], 'latest': True, 'wait_seconds': .1})
        args.update(expected_cursor=rest['cursor'], expected_revision=rest['revision'])
        thread, result, item = self.launch('console-send', args)
        self.decide(item); thread.join(3)
        self.assertEqual(result['result']['input_status'], 'sent')

    def test_console_cursor_errors_do_not_request_approval_or_send_input(self):
        args = self.console_arguments()
        for provider in ('ollama', 'openai'):
            for automatic in (False, True):
                self.session['settings'].update(provider=provider, auto_approve=automatic)
                for cursor in ('SW4#', args['expected_cursor'] + '0'):
                    with self.subTest(provider=provider, automatic=automatic, cursor=cursor):
                        with self.assertRaises(ValueError) as error:
                            self.bridge.tool(self.session, 'POST', '/api/automation/console-send',
                                             {**args, 'expected_cursor': cursor}, self.turn)
                        message = str(error.exception)
                        self.assertIn('Invalid console cursor', message)
                        self.assertIn('get_console_output', message)
                        self.assertIn('not the CLI prompt', message)
                        self.assertIn('not an approval error', message)
                        self.assertIn('no input was sent', message)
                        self.assertEqual(self.bridge.approval_state(self.session), [])
        # A correct cursor can still refer to an observation from an old revision.
        observation = self.bridge.observations[self.session['id']]['r1']
        observation['revision'] = 'old-revision'
        with self.assertRaisesRegex(ValueError, 'Console read revision does not match'):
            self.bridge.tool(self.session, 'POST', '/api/automation/console-send', args, self.turn)
        self.bridge.observations[self.session['id']].pop('r1')
        with self.assertRaises(ValueError) as error:
            self.bridge.tool(self.session, 'POST', '/api/automation/console-send', args, self.turn)
        self.assertIn('missing fresh read, not an approval error', str(error.exception))
        self.assertEqual(self.bridge.approval_state(self.session), [])
        self.assertNotIn('RX:', self.bridge.automation.console_read('r1', wait_seconds=.1)['output'])
        # Recovery still requires a fresh read and the normal approval path.
        self.session['settings']['auto_approve'] = False
        args = self.console_arguments()
        thread, result, item = self.launch('console-send', args)
        self.decide(item); thread.join(3)
        self.assertEqual(result['result']['input_status'], 'sent')
        self.assertIn('RX:show test', result['result']['output'])

    def test_denial_expiry_stop_and_session_isolation(self):
        args = {'node_id':'r1','expected_revision':self.bridge.automation.state()['revision']}
        thread, result, item = self.launch('start-node', args)
        other = self.bridge.api('session')['token']
        with self.assertRaises(ValueError):
            self.bridge.api('decision',other,{'id':item['id'],'approve':True})
        self.decide(item,False); thread.join(3)
        self.assertIn('denied',result['error']); self.assertFalse(self.lab.runtime)
        with patch.object(agent_bridge,'APPROVAL_SECONDS',.1):
            thread,result,item=self.launch('start-node',args)
            thread.join(3)
        self.assertIn('expired',result['error'])
        with self.assertRaises(ValueError): self.decide(item)
        thread,result,item=self.launch('start-node',args)
        self.bridge.api('stop',self.token,{})
        thread.join(3)
        self.assertIn('cancelled',result['error']); self.assertFalse(self.lab.runtime)
        with self.assertRaises(ValueError): self.decide(item)

    def test_approval_can_enable_session_yolo_without_enabling_permissions(self):
        self.session['settings']['console_input'] = False
        args = {'node_id':'r1','expected_revision':self.bridge.automation.state()['revision']}
        thread, result, item = self.launch('start-node', args)
        decision = {'id':item['id'], 'approve':True, 'auto_approve':True}
        other = self.bridge.api('session')['token']
        with self.assertRaises(ValueError): self.bridge.api('decision',other,decision)
        self.assertFalse(self.bridge.resolve(other)['settings']['auto_approve'])
        self.bridge.api('decision',self.token,decision)
        thread.join(3)
        self.assertTrue(result['result']['started'])
        self.assertTrue(self.session['settings']['auto_approve'])
        self.assertFalse(self.session['settings']['console_input'])
        stored = json.loads(self.bridge.path.read_text())
        self.assertTrue(next(s for s in stored.values() if s['id']==self.session['id'])['settings']['auto_approve'])
        self.assertTrue(self.bridge.approval_state(self.session)[-1]['enabled_auto_approve'])
        with self.assertRaises(ValueError): self.bridge.api('decision',self.token,decision)
        args['expected_revision'] = self.bridge.automation.state()['revision']
        self.bridge.tool(self.session,'POST','/api/automation/start-node',args,self.turn)
        self.assertTrue(self.bridge.approval_state(self.session)[-1]['auto_approved'])
        with self.assertRaisesRegex(ValueError,'Enable console_input'):
            self.bridge.tool(self.session,'POST','/api/automation/console-send',
                {'node_id':'r1','input':'test\r','expected_revision':args['expected_revision'],'expected_cursor':'x'},self.turn)

    def test_invalid_or_expired_approval_cannot_enable_yolo(self):
        args = {'node_id':'r1','expected_revision':self.bridge.automation.state()['revision']}
        thread, result, item = self.launch('start-node', args)
        for extra in ({'approve':False,'auto_approve':True}, {'approve':True,'auto_approve':'yes'},
                      {'approve':True,'auto_approve':True,'lab_changes':True}):
            with self.assertRaises(ValueError):
                self.bridge.api('decision',self.token,{'id':item['id'],**extra})
            self.assertFalse(self.session['settings']['auto_approve'])
        with patch.object(self.bridge,'persist',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.bridge.api('decision',self.token,{'id':item['id'],'approve':True,'auto_approve':True})
        self.assertFalse(self.session['settings']['auto_approve'])
        self.assertEqual(self.bridge.approval_state(self.session)[-1]['status'],'pending')
        self.bridge.approvals[self.session['id']][-1]['deadline'] = time.monotonic()-1
        with self.assertRaises(ValueError):
            self.bridge.api('decision',self.token,{'id':item['id'],'approve':True,'auto_approve':True})
        thread.join(3)
        self.assertFalse(self.session['settings']['auto_approve'])
        self.assertFalse(self.lab.runtime)

    def test_yolo_executes_enabled_actions_and_preserves_guards(self):
        self.session['settings']['auto_approve'] = True
        args = {'node_id':'r1','expected_revision':self.bridge.automation.state()['revision']}
        def call(operation, arguments):
            return self.bridge.tool(self.session,'POST','/api/automation/'+operation,arguments,self.turn)
        self.session['settings']['lab_changes'] = False
        with self.assertRaisesRegex(ValueError,'Enable lab_changes'): call('start-node',args)
        self.session['settings']['lab_changes'] = True
        self.assertTrue(call('start-node',args)['started'])
        item = self.bridge.approval_state(self.session)[-1]
        self.assertTrue(item['auto_approved']);self.assertEqual(item['status'],'completed')
        with self.assertRaises(ValueError): self.decide(item)
        args = self.console_arguments()
        import console_capture
        human = console_capture.Console(self.lab.runtime['r1']['port'])
        try:
            human.acquire()
            with self.assertRaisesRegex(lab_server.LabError,'lock'):call('console-send',args)
        finally: human.close()
        args = self.console_arguments()
        self.assertIn('RX:show test',call('console-send',args)['output'])
        with self.assertRaisesRegex(ValueError,'Read this console'):call('console-send',args)
        self.lab.stop('r1')
        p = call('preview',{'topology':{'nodes':[],'links':[]},'instructions':'test','replace_existing':True})
        call('apply',{'proposal_id':p['proposal_id']})
        self.assertEqual(self.lab.topology['nodes'],[])
        self.bridge.revoke(self.session)
        with self.assertRaisesRegex(ValueError,'ended or expired'):call('apply',{'proposal_id':p['proposal_id']})

    def test_start_and_apply_revalidate_revision(self):
        args = {'node_id':'r1','expected_revision':self.bridge.automation.state()['revision']}
        thread,result,item=self.launch('start-node',args)
        self.lab.topology['name']='Human changed topology'
        self.decide(item);thread.join(3)
        self.assertIn('Workspace changed',result['error']);self.assertFalse(self.lab.runtime)
        args['expected_revision']=self.bridge.automation.state()['revision']
        thread,result,item=self.launch('start-node',args)
        self.decide(item);thread.join(3)
        self.assertTrue(result['result']['started']);self.lab.stop('r1')
        proposal=self.bridge.tool(self.session,'POST','/api/automation/preview',{
            'topology':{'version':1,'name':'Approved empty lab','nodes':[],'links':[]},
            'instructions':'Example','replace_existing':True})
        thread,result,item=self.launch('apply',{'proposal_id':proposal['proposal_id']})
        self.assertNotEqual(self.lab.topology['name'],'Approved empty lab')
        self.assertTrue(item['context']['replaces_existing'])
        self.decide(item);thread.join(3)
        self.assertEqual(self.lab.topology['name'],'Approved empty lab')

    def test_sequential_starts_and_console_survive_layout_edits(self):
        observed = self.bridge.automation.state()['revision']
        for index, node_id in enumerate(('r1', 'r2')):
            # The browser moves nodes both before the call and during approval.
            moved = copy.deepcopy(self.lab.topology)
            moved['nodes'][index]['x'] += 30
            self.lab.save(moved)
            thread, result, item = self.launch('start-node', {
                'node_id': node_id, 'expected_revision': observed})
            moved['nodes'][index]['y'] += 20
            self.lab.save(moved)
            self.decide(item); thread.join(3)
            self.assertTrue(result['result']['started'])
            observed = result['result']['state']['revision']
        self.assertEqual(set(self.lab.runtime), {'r1', 'r2'})
        args = self.console_arguments()
        thread, result, item = self.launch('console-send', args)
        moved = copy.deepcopy(self.lab.topology)
        moved['nodes'][0]['x'] += 10
        self.lab.save(moved)
        self.decide(item); thread.join(3)
        self.assertEqual(result['result']['input_status'], 'sent')
        self.assertIn('RX:show test', result['result']['output'])

    def test_human_lock_and_changed_console_refuse_input(self):
        import console_capture
        args=self.console_arguments()
        thread,result,item=self.launch('console-send',args)
        human=console_capture.Console(self.lab.runtime['r1']['port'])
        try:
            human.acquire()
            self.decide(item);thread.join(3)
            self.assertIn('lock',result['error'].lower())
            # A human command between read and approval invalidates the cursor.
            human.send(b'human\r')
            human.window(.2,65536,require_lock=True)
        finally: human.close()
        thread,result,item=self.launch('console-send',args)
        self.decide(item);thread.join(3)
        self.assertIn('Console changed',result['error'])

    def test_permissions_turn_binding_and_dead_companion(self):
        args={'node_id':'r1','expected_revision':self.bridge.automation.state()['revision']}
        self.session['settings']['lab_changes']=False
        with self.assertRaisesRegex(ValueError,'Enable lab_changes'):
            self.bridge.tool(self.session,'POST','/api/automation/start-node',args,self.turn)
        self.session['settings']['lab_changes']=True
        with self.assertRaisesRegex(ValueError,'turn ended'):
            self.bridge.tool(self.session,'POST','/api/automation/start-node',args,'wrong-turn')
        thread,result,item=self.launch('start-node',args)
        self.state['status']='failed'
        with self.assertRaisesRegex(ValueError,'no longer running'): self.decide(item)
        thread.join(3);self.assertIn('cancelled',result['error'])
        self.assertFalse(self.lab.runtime)
        self.assertIsNone(self.lab.automation)
        # The model gateway cannot decide approvals, even with its valid capability.
        with self.assertRaises(ValueError):
            request(self.bridge.sockets/'gateway.sock','/tools/api/agent/decision',
                    {'id':item['id'],'approve':True},self.session['gate'])

    def test_uncertain_input_is_visible_and_not_retried(self):
        args=self.console_arguments()
        with patch.object(self.bridge.automation,'console_send',return_value={'input_status':'unknown'}) as send:
            thread,result,item=self.launch('console-send',args)
            self.decide(item);thread.join(3)
            self.assertEqual(send.call_count,1)
            self.assertEqual(self.bridge.approval_state(self.session)[-1]['status'],'uncertain')

    def test_duplicate_turn_does_not_restore_revoked_permission(self):
        self.state['status']='idle'
        payload={'prompt':'inspect','request_id':'b'*32}
        self.bridge.api('turn',self.token,payload)
        self.bridge.api('stop',self.token,{})
        self.bridge.api('turn',self.token,payload)
        self.assertNotIn(self.session['id'],self.bridge.turns)
        calls=[c for c in self.mock.call_args_list if c.args[0]=='turn']
        self.assertEqual(len(calls),1)


class FakeRPC:
    instances = []
    def __init__(self, config, env=None):
        self.env = env
        self.config, self.pending = config, collections.deque()
        self.events, self.calls = queue.Queue(), []
        self.closed = False
        self.instances.append(self)
    def call(self, method, params, **kwargs):
        self.calls.append((method,params))
        if method == 'mcpServerStatus/list':
            return {'data':[{'name':'weblab','runtimeStatus':'connected',
                             'tools':{name:{} for name in self.config['mcp_servers.weblab.enabled_tools']}}]}
        if method in ('thread/start','thread/resume'): return {'thread':{'id':'thread'}}
        if method == 'turn/start':
            self.events.put({'method':'item/started','params':{'turnId':'turn','item':{'type':'userMessage'}}})
            return {'turn':{'id':'turn'}}
        if method == 'turn/interrupt':
            self.events.put({'method':'turn/completed','params':{'turn':{'id':'turn','status':'interrupted'}}})
        return {}
    def send(self, data): pass
    def event(self, timeout=1): return self.events.get(timeout=timeout)
    def close(self): self.closed=True


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='companion-unit-')
        self.manager = agent_companion.Manager(Path(self.tmp.name)/'sockets',Path(self.tmp.name)/'state',codex_dir=Path(self.tmp.name)/'codex')
        self.settings = {'model':'test','endpoint':'http://localhost:11434/v1','context_window':32768,'turn_timeout':30}
        self.payload = {'session':'a'*32,'gateway_token':'cap','settings':self.settings}
        self.patch = patch.object(agent_companion,'RPC',FakeRPC); self.patch.start()
    def tearDown(self):
        for session in self.manager.sessions.values(): session.close()
        self.patch.stop(); self.tmp.cleanup()
    def wait(self, predicate):
        end=time.monotonic()+5
        while not predicate() and time.monotonic()<end: time.sleep(.02)
        self.assertTrue(predicate())

    def test_attachment_delivery_history_restart_and_delete(self):
        files=[attachment('network.png',screenshot()),attachment('router.conf',b'router ospf\n network 10.0.0.0/8 area 0\n')]
        payload={**self.payload,'prompt':'Explain these files; do not configure anything','request_id':'a'*32,'attachments':files}
        state=self.manager.handle('turn',payload)
        session=self.manager.sessions['a'*32]
        self.wait(lambda:session.status=='running')
        inputs=next(p['input'] for m,p in FakeRPC.instances[-1].calls if m=='turn/start')
        image=next(i for i in inputs if i['type']=='localImage')
        image_path=Path(image['path'])
        self.assertEqual(image_path.read_bytes(),screenshot())
        self.assertEqual(image_path.stat().st_mode & 0o777,0o600)
        self.assertIn('router ospf',inputs[-1]['text'])
        self.assertIn('untrusted data',inputs[-1]['text'])
        original=state['conversation_id'];item=state['messages'][0]['attachments'][0]
        self.assertNotIn('data',item)
        self.assertNotIn(str(self.manager.state),json.dumps(state))
        self.manager.handle('turn',payload)
        self.assertEqual(len(list(image_path.parent.iterdir())),2)  # Duplicate delivery stores nothing twice.
        read={**self.payload,'conversation_id':original,'id':item['id']}
        self.assertEqual(base64.b64decode(self.manager.handle('attachment',read)['data']),screenshot())
        for data in ({**read,'session':'b'*32},{**read,'conversation_id':'c'*32},
                     {**read,'id':'../../session.json'}):
            with self.assertRaises(ValueError):self.manager.handle('attachment',data)
        self.manager.handle('stop',self.payload);self.wait(lambda:not session.worker.is_alive())
        self.manager.handle('reset',self.payload)
        session.close();del self.manager.sessions['a'*32]
        self.assertEqual(self.manager.handle('attachment',read)['name'],'network.png')
        self.manager.handle('history-open',{**self.payload,'id':original})
        self.assertEqual(self.manager.sessions['a'*32].messages[0]['attachments'][1]['text'],'router ospf\n network 10.0.0.0/8 area 0\n')
        self.manager.handle('reset',self.payload)
        self.manager.handle('history-delete',{**self.payload,'id':original})
        self.assertFalse(image_path.parent.exists())
        with self.assertRaises(ValueError):self.manager.handle('attachment',read)

    def test_attachment_failed_storage_releases_busy_and_rejects_binary(self):
        self.manager.handle('state',self.payload)
        session=self.manager.sessions['a'*32]
        with self.assertRaises(ValueError):
            session.launch('Review','a'*32,[attachment('a.txt',b'\x00')])
        self.assertEqual(session.messages,[])
        self.assertFalse(self.manager.busy.locked())
        with patch.object(self.manager,'check_storage',side_effect=[None,ValueError('disk quota')]):
            with self.assertRaisesRegex(ValueError,'disk quota'):
                session.launch('Review','b'*32,[attachment('a.txt',b'data')])
        self.assertFalse(self.manager.busy.locked())
        self.assertEqual(session.messages,[])
        self.assertEqual(list((session.path.parent/'attachments'/session.conversation['id']).iterdir()),[])
        with patch.object(session,'persist',side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError,'Could not save'):
                session.launch('Review','c'*32,[attachment('a.txt',b'data')])
        self.assertFalse(self.manager.busy.locked())
        self.assertEqual(session.messages,[])
        self.assertEqual(list((session.path.parent/'attachments'/session.conversation['id']).iterdir()),[])

    def test_history_round_trip_restart_rename_delete_and_resume(self):
        original=self.manager.handle('state',self.payload)['conversation_id']
        session=self.manager.sessions['a'*32]
        session.messages=[{'role':'user','text':'Build OSPF\nand STP'},{'role':'assistant','text':'A previous answer'}]
        session.thread_id='saved-thread';session.status='completed';session.persist()
        self.manager.handle('reset',self.payload)
        current=session.conversation['id']
        self.assertNotEqual(current,original)
        self.assertEqual(len(session.snapshot()['history']),2)
        self.assertEqual(session.history[original]['title'],'Build OSPF and STP')
        self.manager.handle('history-rename',{**self.payload,'id':original,'title':'OSPF practice'})
        session.close();del self.manager.sessions['a'*32]
        restored=self.manager.handle('state',self.payload)
        self.assertEqual(restored['conversation_id'],current)
        record=self.manager.handle('history-read',{**self.payload,'id':original})
        self.assertEqual(record['title'],'OSPF practice')
        self.assertEqual(record['messages'][1]['text'],'A previous answer')
        self.manager.handle('history-open',{**self.payload,'id':original})
        session=self.manager.sessions['a'*32]
        self.assertEqual(session.thread_id,'saved-thread')
        self.manager.handle('turn',{**self.payload,'prompt':'Continue after checking current state','request_id':'e'*32})
        self.wait(lambda:session.status=='running')
        rpc=FakeRPC.instances[-1]
        resume=next(p for m,p in rpc.calls if m=='thread/resume')
        self.assertEqual(resume['threadId'],'saved-thread')
        self.assertIn('At the start of each turn read current lab state',resume['developerInstructions'])
        for action in ('history-open','history-delete','history-rename'):
            with self.assertRaisesRegex(ValueError,'active turn'):
                self.manager.handle(action,{**self.payload,'id':original,'title':'Blocked'})
        self.manager.handle('stop',self.payload);self.wait(lambda:not session.worker.is_alive())
        with self.assertRaisesRegex(ValueError,'another conversation'):
            self.manager.handle('history-delete',{**self.payload,'id':original})
        self.manager.handle('reset',self.payload)
        self.manager.handle('history-delete',{**self.payload,'id':original})
        with self.assertRaisesRegex(ValueError,'deleted'):
            self.manager.handle('history-read',{**self.payload,'id':original})
        with self.assertRaisesRegex(ValueError,'Invalid conversation'):
            self.manager.handle('history-read',{**self.payload,'id':'../../other/session'})

    def test_history_migrates_existing_state_and_bounds_conversations(self):
        path=self.manager.state/('a'*32)/'session.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'thread_id':'legacy-thread','messages':[{'role':'user','text':'Existing discussion'}],
                                    'seen':[],'status':'running','error':''}))
        state=self.manager.handle('state',self.payload)
        session=self.manager.sessions['a'*32]
        self.assertEqual(state['status'],'interrupted')
        self.assertEqual(state['history'][0]['title'],'Existing discussion')
        self.assertTrue(session.legacy_history)
        with patch.object(agent_companion,'MAX_CONVERSATIONS',2):
            session.reset()
            session.messages=[{'role':'user','text':'Second conversation'}];session.persist()
            session.reset()  # New/sign-out can always leave an empty draft at the limit.
            with self.assertRaisesRegex(ValueError,'History holds'):
                session.launch('Third conversation','f'*32)
            original=next(iter(session.history))
            session.history_action('history-open',{'id':original})
            self.assertEqual(session.thread_id,'legacy-thread')
            self.assertTrue(session.legacy_history)
            self.assertEqual(len(session.history),1)
        with self.assertRaisesRegex(ValueError,'title'):
            session.history_action('history-rename',{'id':original,'title':' '})
    def test_session_auth_storage_and_environment_isolation(self):
        self.manager.handle('state',self.payload)
        first=self.manager.sessions['a'*32]
        self.manager.handle('state',{**self.payload,'session':'b'*32})
        second=self.manager.sessions['b'*32]
        with patch.dict(os.environ,{'OPENAI_API_KEY':'secret','CODEX_HOME':'/host/auth','HTTPS_PROXY':'http://evil'}):
            one,two=first.process_env(),second.process_env()
            self.assertNotEqual(one['CODEX_HOME'],two['CODEX_HOME'])
            self.assertNotIn('OPENAI_API_KEY',one);self.assertNotIn('HTTPS_PROXY',one)
            self.assertEqual(Path(one['CODEX_HOME']).stat().st_mode & 0o777,0o700)
            first.settings={**self.settings,'provider':'openai'}
            env=first.process_env()
            self.assertEqual(env['HTTPS_PROXY'],'http://127.0.0.1:'+str(first.relay.server_port))
            self.assertEqual(env['NO_PROXY'],'127.0.0.1,localhost')
        first.legacy_history=True;first.settings=self.settings
        self.assertEqual(first.process_env()['CODEX_HOME'],str(self.manager.codex_dir))
        first.reset()
        self.assertNotEqual(first.process_env()['CODEX_HOME'],str(self.manager.codex_dir))

    def test_device_login_lifecycle_and_no_token_exposure(self):
        class AuthRPC(FakeRPC):
            def call(self, method, params, **kwargs):
                result=super().call(method,params,**kwargs)
                if method=='account/login/start':
                    return {'loginId':'login','verificationUrl':'https://auth.openai.com/codex/device','userCode':'ABCD-1234'}
                if method=='account/read':
                    return {'account':{'type':'chatgpt','email':'private@example.test','planType':'plus','access_token':'SECRET'}}
                if method=='model/list': return {'data':[{'model':'test-openai'}]}
                return result
        self.settings.update(provider='openai',endpoint=agent_bridge.OPENAI_ENDPOINT,console_input=True)
        with patch.object(agent_companion,'RPC',AuthRPC):
            self.manager.handle('login',self.payload)
            session=self.manager.sessions['a'*32]
            self.wait(lambda:session.auth.snapshot()['status']=='waiting')
            self.assertEqual(session.auth.snapshot()['code'],'ABCD-1234')
            with self.assertRaisesRegex(ValueError,'sign-in'):
                self.manager.handle('turn',{**self.payload,'prompt':'hi','request_id':'c'*32})
            with self.assertRaisesRegex(ValueError,'sign-in'): self.manager.handle('reset',self.payload)
            rpc=FakeRPC.instances[-1]
            rpc.events.put({'method':'account/login/completed','params':{'loginId':'login','success':True}})
            self.wait(lambda:not session.auth.busy())
            state=session.snapshot()
            self.assertEqual(state['auth']['models'],['test-openai'])
            self.assertTrue(state['auth']['signed_in'])
            for secret in ('SECRET','private@example.test','ABCD-1234'): self.assertNotIn(secret,json.dumps(state))
            self.manager.handle('turn',{**self.payload,'prompt':'inspect','request_id':'d'*32})
            self.wait(lambda:session.status=='running')
            turn_rpc=FakeRPC.instances[-1]
            self.assertEqual(turn_rpc.config['model_provider'],'openai')
            self.assertNotIn('--compact-responses', turn_rpc.config['mcp_servers.weblab.args'])
            self.assertIn('send_console_command', turn_rpc.config['mcp_servers.weblab.enabled_tools'])
            self.assertEqual(turn_rpc.config['mcp_servers.weblab.tools.send_console_command.approval_mode'],'approve')
            self.assertNotIn('model_providers.weblab_ollama.base_url',turn_rpc.config)
            self.assertFalse(turn_rpc.config['features.shell_tool'])
            self.assertTrue(turn_rpc.config['features.code_mode_host'])
            self.assertTrue(turn_rpc.config['features.code_mode.enabled'])
            self.assertIn('functions',turn_rpc.config['features.code_mode.excluded_tool_namespaces'])
            with self.assertRaisesRegex(ValueError,'active turn'): self.manager.handle('logout',self.payload)
            self.manager.handle('stop',self.payload);self.wait(lambda:not session.worker.is_alive())
            self.manager.handle('logout',self.payload);self.wait(lambda:not session.auth.busy())
            self.assertEqual(session.auth.snapshot()['status'],'signed-out')
            self.assertEqual(session.messages,[])
            self.manager.handle('login',self.payload)
            self.wait(lambda:session.auth.snapshot()['status']=='waiting')
            self.manager.handle('login-cancel',self.payload);self.wait(lambda:not session.auth.busy())
            self.assertEqual(session.auth.snapshot()['status'],'cancelled')
            self.assertIn(('account/login/cancel',{'loginId':'login'}),FakeRPC.instances[-1].calls)

    def test_rpc_account_errors_do_not_expose_provider_payloads(self):
        rpc=object.__new__(REAL_RPC)
        rpc.number=0;rpc.pending=collections.deque()
        rpc.send=lambda data:None
        rpc.event=lambda timeout: {'id':1,'error':{'message':'access_token=SECRET'}}
        with self.assertRaisesRegex(ValueError,'Codex account request failed') as error:
            rpc.call('account/read',{})
        self.assertNotIn('SECRET',str(error.exception))

    def test_openai_requires_login_and_redacts_auth_failure(self):
        self.settings.update(provider='openai',endpoint=agent_bridge.OPENAI_ENDPOINT)
        self.manager.handle('turn',{**self.payload,'prompt':'hello','request_id':'e'*32})
        session=self.manager.sessions['a'*32]
        self.wait(lambda:not session.worker.is_alive())
        self.assertIn('Sign in with OpenAI',session.error)
        self.assertNotIn('turn/start',[m for m,p in FakeRPC.instances[-1].calls])
        with patch.object(FakeRPC,'call',side_effect=ValueError('SECRET access_token')):
            self.manager.handle('login',self.payload);self.wait(lambda:not session.auth.busy())
        self.assertEqual(session.auth.snapshot()['status'],'failed')
        self.assertNotIn('SECRET',json.dumps(session.snapshot()))

    def test_stop_duplicate_busy_and_restart(self):
        payload={**self.payload,'prompt':'hello','request_id':'1'*32}
        self.manager.handle('turn',payload)
        session=self.manager.sessions['a'*32]
        self.manager.handle('turn',payload)  # Same request is idempotent.
        with self.assertRaises(ValueError): self.manager.handle('reset',self.payload)
        with self.assertRaises(ValueError):
            self.manager.handle('turn',{**payload,'session':'b'*32,'request_id':'2'*32})
        self.manager.handle('stop',self.payload)
        self.wait(lambda:not session.worker.is_alive())
        self.assertEqual(session.status,'interrupted')
        self.assertEqual(len([m for m in session.messages if m['role']=='user']),1)
        self.assertTrue(FakeRPC.instances[-1].closed)
        session.close(); del self.manager.sessions['a'*32]
        self.assertEqual(self.manager.handle('state',self.payload)['messages'][0]['text'],'hello')
        self.manager.handle('turn',{**payload,'request_id':'3'*32})
        self.wait(lambda:bool(FakeRPC.instances[-1].calls and any(m=='thread/resume' for m,p in FakeRPC.instances[-1].calls)))
        self.manager.handle('stop',self.payload)
        self.wait(lambda:not self.manager.sessions['a'*32].worker.is_alive())
    def test_restart_marks_unfinished_turn_and_storage_limit(self):
        from agent_transport import save_json
        target = self.manager.state/('a'*32)/'session.json'
        save_json(target, {'status':'running','thread_id':'old-thread',
                          'messages':[{'role':'user','text':'Already sent'}],'seen':['7'*32]})
        state = self.manager.handle('state',self.payload)
        self.assertEqual(state['status'],'interrupted')
        self.assertIn('not resent',state['error'])
        self.assertEqual(len(state['messages']),1)
        self.manager.codex_dir.mkdir()
        with (self.manager.codex_dir/'large-log').open('wb') as f:
            f.truncate(256*1024*1024+1)
        with self.assertRaisesRegex(ValueError,'256 MiB'):
            self.manager.handle('turn',{**self.payload,'prompt':'hello','request_id':'8'*32})
        self.assertFalse(self.manager.busy.locked())

    def test_permissions_only_expose_guarded_tools(self):
        self.settings.update(lab_changes=True,console_input=True)
        self.manager.handle('turn',{**self.payload,'prompt':'inspect','request_id':'f'*32})
        session=self.manager.sessions['a'*32]
        self.wait(lambda:session.status=='running')
        config=FakeRPC.instances[-1].config
        self.assertIn('--allow-write',config['mcp_servers.weblab.args'])
        self.assertIn('--compact-responses',config['mcp_servers.weblab.args'])
        self.assertIn('--allow-console-input',config['mcp_servers.weblab.args'])
        self.assertIn('send_console_input',config['mcp_servers.weblab.enabled_tools'])
        self.assertNotIn('send_console_input',agent_companion.TOOLS)
        self.assertIn('send_console_command',config['mcp_servers.weblab.enabled_tools'])
        self.assertNotIn('send_console_command',agent_companion.TOOLS)
        self.assertEqual(config['mcp_servers.weblab.tools.send_console_command.approval_mode'],'approve')
        self.assertEqual(config['mcp_servers.weblab.tools.send_console_input.approval_mode'],'approve')
        self.assertNotIn('mcp_servers.weblab.default_tools_approval_mode',config)
        self.assertEqual(config['approval_policy'],'on-request')
        self.assertEqual(session.snapshot()['request_id'],'f'*32)
        self.assertFalse(config['features.shell_tool'])
        self.assertFalse(config['features.code_mode_host'])
        self.manager.handle('stop',self.payload)
        self.wait(lambda:not session.worker.is_alive())

    def test_missing_mcp_inventory_fails_before_inference(self):
        original=FakeRPC.call
        def call(rpc,method,params,**kwargs):
            if method=='mcpServerStatus/list': return {'data':[]}
            return original(rpc,method,params,**kwargs)
        with patch.object(FakeRPC,'call',call):
            self.manager.handle('turn',{**self.payload,'prompt':'inspect','request_id':'no-tools-test-001'})
            session=self.manager.sessions['a'*32]
            self.wait(lambda:not session.worker.is_alive())
        self.assertEqual(session.status,'failed')
        self.assertIn('Weblab tools are unavailable',session.error)
        self.assertNotIn('turn/start',[name for name,params in FakeRPC.instances[-1].calls])
        self.assertFalse(self.manager.busy.locked())

    def test_stream_text_and_terminal_failure(self):
        self.manager.handle('turn',{**self.payload,'prompt':'hello','request_id':'9'*32})
        session=self.manager.sessions['a'*32]
        self.wait(lambda:session.status=='running')
        rpc=FakeRPC.instances[-1]
        rpc.events.put({'method':'item/reasoning/textDelta','params':{'turnId':'turn','delta':'private raw reasoning'}})
        self.wait(lambda:session.snapshot()['phase']=='Thinking')
        self.assertNotIn('private raw reasoning',json.dumps(session.snapshot()))
        self.assertIn('elapsed_seconds',session.snapshot())
        rpc.events.put({'method':'item/agentMessage/delta','params':{'turnId':'turn','itemId':'a','delta':'<script>test</script>'}})
        self.wait(lambda:len(session.messages)==2)
        self.assertEqual(session.snapshot()['messages'][-1]['text'],'<script>test</script>')
        self.assertEqual(session.snapshot()['phase'],'Writing response')
        rpc.events.put({'method':'error','params':{'willRetry':True}})
        self.wait(lambda:not session.worker.is_alive())
        self.assertEqual(session.status,'interrupted')
        self.assertIn('connection failed',session.error)
        self.assertFalse(self.manager.busy.locked())

    def test_deadline_stops_generation_and_explains_preserved_actions(self):
        # A short internal budget avoids waiting 30s in the transport fixture.
        self.settings['turn_timeout'] = .2
        self.manager.handle('turn',{**self.payload,'prompt':'hello','request_id':'deadline-test-0001'})
        session = self.manager.sessions['a'*32]
        self.wait(lambda:not session.worker.is_alive())
        self.assertEqual(session.status,'interrupted')
        self.assertIn('Completed lab actions remain',session.error)
        self.assertFalse(self.manager.busy.locked())


if __name__ == '__main__': unittest.main()
