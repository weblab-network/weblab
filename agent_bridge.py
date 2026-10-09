"""Opt-in browser/session bridge and restricted companion gateway. No SDK needed."""
import copy
import hashlib
import http.client
import json
import inspect
import os
from pathlib import Path
import re
import secrets
import threading
import time
import socket
from urllib.parse import urlsplit

from agent_transport import JSONHandler, UnixServer, request, save_json, OPENAI_HOSTS, tunnel
import lab_automation
import agent_attachments

ROOT = Path(__file__).resolve().parent
MAX_SESSIONS = 8
TTL = 24 * 3600
APPROVAL_SECONDS = 60
OPENAI_ENDPOINT = 'https://chatgpt.com/backend-api/codex'


def endpoint(value):
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError('Invalid Ollama endpoint')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/') != '/v1'
            or any(c.isspace() for c in value)):
        raise ValueError('Use an http(s) Ollama /v1 endpoint without credentials or query parameters')
    parsed.port  # Validate port syntax.
    return value.rstrip('/')


class Bridge:
    def __init__(self, lab, sockets, state_dir, providers):
        self.lab = lab
        self.sockets = Path(sockets)
        self.path = Path(state_dir) / 'sessions.json'
        self.providers = [endpoint(p.strip()) for p in providers.split(',') if p.strip()]
        if not self.providers:
            raise ValueError('Configure at least one WL_AGENT_PROVIDERS endpoint')
        self.lock = threading.RLock()
        self.turns, self.approvals, self.observations = {}, {}, {}
        self.tool_errors = {}
        self.touch_flushed = time.monotonic()
        self.sessions = json.loads(self.path.read_text()) if self.path.exists() else {}
        # Independent automation service: enabling Agent does not enable write-capable public MCP routes.
        from lab_server import LabError, ports, run
        self.automation = lab_automation.Automation(lab, LabError, ports, run)
        for session in self.sessions.values():
            session['proposals'] = []  # In-memory proposals do not survive a core restart.
            session['settings'].setdefault('lab_changes', False)
            session['settings'].setdefault('console_input', False)
            session['settings'].setdefault('auto_approve', False)
            session['settings'].setdefault('provider', 'ollama')
        self.gateway = UnixServer(self.sockets / 'gateway.sock', Gateway)
        self.gateway.bridge = self
        self.thread = threading.Thread(target=self.gateway.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                self.revoke(session)
        self.gateway.shutdown()
        self.gateway.server_close()
        self.thread.join()

    def persist(self):
        save_json(self.path, self.sessions)

    def companion(self, action, session, data=None):
        payload = {'session': session['id'], 'gateway_token': session['gate'],
                   'settings': session['settings'], **(data or {})}
        try:
            return request(self.sockets / 'companion.sock', '/' + action, payload, timeout=10)
        except (OSError, http.client.HTTPException) as exc:
            raise ValueError('Agent companion is unavailable. Start the optional companion service and reconnect.') from exc

    def resolve(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            session = self.sessions.get(digest)
            if not session or time.time() - session['touched'] > TTL:
                raise ValueError('Agent session expired. Open a new session.')
            session['touched'] = time.time()
            # Keep remembered sessions usable across core restarts without writing
            # metadata on every browser poll. At most one minute of activity is lost.
            if time.monotonic() - self.touch_flushed >= 60:
                self.persist()
                self.touch_flushed = time.monotonic()
            return session

    def settings(self, data):
        data = {'provider': 'ollama', 'lab_changes': False, 'console_input': False, 'auto_approve': False, **data}
        if set(data) != {'provider', 'endpoint', 'model', 'context_window', 'turn_timeout', 'lab_changes', 'console_input', 'auto_approve'}:
            raise ValueError('Unexpected agent settings')
        if data['provider'] not in ('ollama', 'openai'):
            raise ValueError('Unknown Agent provider')
        if data['provider'] == 'openai':
            url = OPENAI_ENDPOINT
            if data['endpoint'] != url:
                raise ValueError('OpenAI uses the fixed Codex endpoint')
        else:
            url = endpoint(data['endpoint'])
            if url not in self.providers:
                raise ValueError('Endpoint is not in the operator-configured WL_AGENT_PROVIDERS list')
        model = data['model']
        if not isinstance(model, str) or not re.fullmatch(r'[\w.:/-]{1,160}', model):
            raise ValueError('Enter an exact model name')
        context, timeout = data['context_window'], data['turn_timeout']
        if type(context) is not int or not 4096 <= context <= 131072:
            raise ValueError('Context window must be 4096–131072 tokens')
        if type(timeout) is not int or not 30 <= timeout <= 3600:
            raise ValueError('Turn deadline must be 30–3600 seconds')
        if any(type(data[k]) is not bool for k in ('lab_changes', 'console_input', 'auto_approve')):
            raise ValueError('Agent permissions must be booleans')
        return dict(provider=data['provider'], endpoint=url, model=model, context_window=context, turn_timeout=timeout,
                    lab_changes=data['lab_changes'], console_input=data['console_input'],
                    auto_approve=data['auto_approve'])

    def api(self, action, token='', data=None):
        with self.lock:
            return self._api(action, token, data)

    def _api(self, action, token='', data=None):
        data = data or {}
        if action == 'info':
            return {'enabled': True, 'providers': self.providers, 'openai_endpoint': OPENAI_ENDPOINT, 'mode': 'approval'}
        if action == 'session':
            with self.lock:
                for key, stale in list(self.sessions.items()):
                    if time.time() - stale['touched'] >= TTL:
                        self.revoke(stale)
                        self.approvals.pop(stale['id'], None)
                        self.tool_errors.pop(stale['id'], None)
                        del self.sessions[key]
                if len(self.sessions) >= MAX_SESSIONS:
                    raise ValueError('Agent session limit reached. Close an existing session or wait for expiry.')
                token = secrets.token_urlsafe(32)
                session = {'id': secrets.token_hex(16), 'gate': secrets.token_urlsafe(32),
                           'touched': time.time(), 'proposals': [], 'settings': {
                               'provider': 'ollama', 'endpoint': self.providers[0], 'model': 'gpt-oss:20b',
                               'context_window': 32768, 'turn_timeout': 900,
                               'lab_changes': False, 'console_input': False, 'auto_approve': False}}
                self.sessions[hashlib.sha256(token.encode()).hexdigest()] = session
                self.persist()
            return {'token': token, 'settings': session['settings']}
        session = self.resolve(token)
        if action == 'attachment':
            if set(data) != {'conversation_id', 'id'}:
                raise ValueError('Unexpected attachment arguments')
            return self.companion('attachment', session, data)
        if action == 'state':
            state = self.companion('state', session)
            if state['status'] not in ('starting', 'running'):
                self.revoke(session)
            return {**state, 'settings': session['settings'], 'proposals': session['proposals'],
                    'approvals': self.approval_state(session),
                    'tool_error': self.tool_errors.get(session['id'])}
        if action in ('history-read', 'history-export', 'history-open', 'history-rename', 'history-delete'):
            expected = {'id', 'title'} if action == 'history-rename' else {'id'}
            if set(data) != expected:
                raise ValueError('Unexpected history arguments')
            record = self.companion('history-read', session, {'id': data['id']})
            if action == 'history-export':
                from lab_backup import conversation_export
                return conversation_export(record)
            if action == 'history-read':
                return {k: record[k] for k in ('id', 'title', 'created', 'updated', 'settings', 'messages')}
            if action == 'history-open':
                # Validate historical endpoints against today's allowlist. Never restore
                # previous permissions, YOLO, turn IDs, observations or approvals.
                settings = self.settings({**session['settings'], **record['settings']})
                state = self.companion('state', session)
                if state['status'] in ('starting', 'running', 'stopping'):
                    raise ValueError('Stop the active turn before opening another conversation')
                if state.get('auth', {}).get('status') in ('connecting', 'waiting'):
                    raise ValueError('Finish or cancel sign-in before opening another conversation')
                self.revoke(session)
                result = self.companion(action, session, data)
                session['settings'] = settings
                session['proposals'] = []
                self.approvals.pop(session['id'], None)
                self.tool_errors.pop(session['id'], None)
                self.persist()
                return {**result, 'settings': settings, 'proposals': [], 'approvals': []}
            return self.companion(action, session, data)
        if action == 'settings':
            settings = self.settings(data)
            state = self.companion('state', session)
            if state['status'] in ('starting', 'running', 'stopping'):
                raise ValueError('Stop the active turn before changing Agent settings')
            if state.get('auth', {}).get('status') in ('connecting', 'waiting'):
                raise ValueError('Finish or cancel sign-in before changing Agent settings')
            reset = any(settings[k] != session['settings'][k]
                        for k in ('provider', 'endpoint', 'model', 'context_window'))
            self.revoke(session)
            if reset:
                self.companion('reset', session)
            with self.lock:
                session['settings'] = settings
                if reset:
                    self.approvals.pop(session['id'], None)
                    self.tool_errors.pop(session['id'], None)
                    session['proposals'] = []
                self.persist()
            return {'settings': settings}
        if action == 'check':
            settings = self.settings(data)
            if settings['provider'] == 'openai':
                raise ValueError('Use Refresh account / models after signing in with OpenAI')
            status, result = self.provider(settings, 'GET', '/models')
            if status != 200:
                raise ValueError('Ollama model discovery failed (HTTP %s)' % status)
            models = [m.get('id') for m in result.get('data', []) if isinstance(m, dict)]
            return {'available': settings['model'] in models, 'models': models[:200]}
        if action in ('login', 'login-status', 'logout', 'login-cancel'):
            if session['settings']['provider'] != 'openai':
                raise ValueError('Select OpenAI and save settings first')
            if action == 'logout':
                self.revoke(session)
            result = self.companion(action, session)
            if action == 'logout':
                self.approvals.pop(session['id'], None)
                self.tool_errors.pop(session['id'], None)
                session['proposals'] = []
                self.persist()
            return result
        if action == 'turn':
            if (not {'prompt', 'request_id'} <= set(data) or set(data) - {'prompt', 'request_id', 'attachments'}
                    or not isinstance(data['prompt'], str) or not 1 <= len(data['prompt']) <= 16000):
                raise ValueError('Enter a prompt of 1–16000 characters')
            if not isinstance(data['request_id'], str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', data['request_id']):
                raise ValueError('Invalid turn request ID')
            # Persist the request ID before dispatch. An ambiguous transport failure is never replayed.
            if data['request_id'] in session.get('seen', []):
                return self.companion('state', session)
            agent_attachments.validate(data.get('attachments', []), session['settings']['model'])
            state = self.companion('state', session)
            if state['status'] in ('starting', 'running', 'stopping'):
                raise ValueError('A turn is already active in this session')
            self.revoke(session)
            self.tool_errors.pop(session['id'], None)
            session['seen'] = (session.get('seen', []) + [data['request_id']])[-100:]
            self.turns[session['id']] = {'id': data['request_id'],
                                       'expires': time.monotonic() + session['settings']['turn_timeout']}
            self.persist()
            try:
                return self.companion('turn', session, data)
            except Exception:
                self.revoke(session)
                raise
        if action == 'decision':
            return self.decision(session, data)
        if action in ('stop', 'reset'):
            self.revoke(session)
            result = self.companion(action, session)
            if action == 'reset':
                with self.lock:
                    self.approvals.pop(session['id'], None)
                    self.tool_errors.pop(session['id'], None)
                    session['proposals'] = []
                    self.persist()
            return result
        if action == 'close':
            self.revoke(session)
            self.companion('delete', session)
            with self.lock:
                self.approvals.pop(session['id'], None)
                self.tool_errors.pop(session['id'], None)
                del self.sessions[hashlib.sha256(token.encode()).hexdigest()]
                self.persist()
            return {'closed': True}
        if action == 'proposal':
            if data.get('id') not in session['proposals']:
                raise ValueError('Proposal does not belong to this session or has expired')
            return self.automation.proposal(data['id'])
        raise ValueError('Unknown agent action')

    def revoke(self, session):
        """Caller holds lock. Pending approvals are ephemeral and never replayed."""
        self.turns.pop(session['id'], None)
        self.observations.pop(session['id'], None)
        for item in self.approvals.get(session['id'], []):
            if item['status'] in ('pending', 'approved'):
                item['status'] = 'cancelled'
                item['event'].set()

    def active_turn(self, session, turn_id):
        turn = self.turns.get(session['id'])
        if not turn or turn['id'] != turn_id or time.monotonic() >= turn['expires']:
            raise ValueError('Agent turn ended or expired; no action was performed')
        return turn

    def check_companion_turn(self, session, turn_id):
        self.active_turn(session, turn_id)
        state = self.companion('state', session)
        if state.get('request_id') != turn_id or state['status'] not in ('starting', 'running'):
            self.revoke(session)
            raise ValueError('Agent is no longer running this turn; no action was performed')

    def approval_state(self, session):
        result = []
        for item in self.approvals.get(session['id'], []):
            if item['status'] in ('pending', 'approved') and time.monotonic() >= item['deadline']:
                item['status'] = 'expired'
                item['event'].set()
            result.append({k: copy.deepcopy(v) for k, v in item.items()
                           if k not in ('event', 'deadline', 'turn')})
        return result

    def decision(self, session, data):
        if (not {'id', 'approve'} <= set(data) or set(data) - {'id', 'approve', 'auto_approve'}
                or type(data['approve']) is not bool
                or type(data.get('auto_approve', False)) is not bool
                or (data.get('auto_approve') and not data['approve'])):
            raise ValueError('Expected an approval ID and boolean decision')
        self.approval_state(session)
        item = next((a for a in self.approvals.get(session['id'], []) if a['id'] == data['id']), None)
        if not item or item['status'] != 'pending':
            raise ValueError('Approval is unavailable, already decided or expired')
        self.check_companion_turn(session, item['turn'])
        self.active_turn(session, item['turn'])
        self.approval_state(session)
        if item['status'] != 'pending':
            raise ValueError('Approval is unavailable, already decided or expired')
        if data.get('auto_approve'):
            # Only a valid, explicit browser approval can enable session YOLO
            # during a turn. Do not change permissions or accept other pending cards.
            previous = session['settings']['auto_approve']
            session['settings']['auto_approve'] = True
            try:
                self.persist()
            except Exception:
                session['settings']['auto_approve'] = previous
                raise
            item['enabled_auto_approve'] = True
        item['status'] = 'approved' if data['approve'] else 'denied'
        item['event'].set()
        return {'id': item['id'], 'status': item['status']}

    def guarded_tool(self, session, operation, data, turn_id):
        service = self.automation
        permission = 'console_input' if operation == 'console-send' else 'lab_changes'
        handler = {'apply': service.apply, 'start-node': service.start_node,
                   'console-send': service.console_send}[operation]
        if not isinstance(data, dict):
            raise ValueError('Expected tool arguments')
        inspect.signature(handler).bind(**data)  # Reject missing/extra fields before presenting approval.
        arguments = copy.deepcopy(data)
        with self.lock:
            if not session['settings'].get(permission):
                raise ValueError('Enable ' + permission + ' in Agent Settings before requesting this operation')
            self.check_companion_turn(session, turn_id)
            if operation == 'apply':
                if arguments['proposal_id'] not in session['proposals']:
                    raise ValueError('Proposal does not belong to this session')
                context = service.proposal(arguments['proposal_id'])
            else:
                with self.lab.lock:
                    context = {'node': copy.deepcopy(self.lab.node(arguments['node_id']))}
                    service.require_execution_revision(arguments['expected_revision'])
                if operation == 'console-send':
                    observation = self.observations.get(session['id'], {}).get(arguments['node_id'])
                    if (not observation or observation.get('cursor') != arguments['expected_cursor']
                            or observation.get('revision') != arguments['expected_revision']):
                        raise ValueError('Read this console in this conversation before requesting input')
                    if observation.get('gap'):
                        raise ValueError('Console history has a gap; read it again before requesting input')
                    service._console_options(arguments['node_id'], arguments['expected_cursor'],
                                             arguments.get('wait_seconds', 1), arguments.get('max_bytes', 65536))
                    value = arguments['input']
                    if not isinstance(value, str) or not 1 <= len(value.encode('utf-8')) <= 4096:
                        raise ValueError('Console input must be 1–4096 UTF-8 bytes')
                    context['console_output'] = observation.get('output', '')[-4000:]
            history = self.approvals.setdefault(session['id'], [])
            if sum(a['status'] in ('pending', 'approved', 'executing') for a in history) >= 4:
                raise ValueError('Too many pending operations; wait for an approval decision')
            deadline = min(time.monotonic() + APPROVAL_SECONDS, self.active_turn(session, turn_id)['expires'])
            automatic = session['settings'].get('auto_approve', False)
            item = {'id': secrets.token_hex(16), 'turn': turn_id, 'operation': operation,
                    'arguments': arguments, 'context': context,
                    'status': 'approved' if automatic else 'pending', 'auto_approved': automatic,
                    'deadline': deadline, 'expires_at': time.time() + deadline - time.monotonic(),
                    'event': threading.Event()}
            # Retain all pending operations and a bounded recent history.
            history[:] = [a for a in history if a['status'] in ('pending', 'approved', 'executing')] + [
                a for a in history if a['status'] not in ('pending', 'approved', 'executing')][-12:]
            history.append(item)
            if automatic:
                item['event'].set()
        # Never hold the bridge lock while the user reviews or while a device starts.
        item['event'].wait(max(0, deadline - time.monotonic()))
        with self.lock:
            self.approval_state(session)
            if item['status'] != 'approved':
                raise ValueError('Operation ' + item['status'] + '; no action was performed')
            self.active_turn(session, turn_id)
            item['status'] = 'executing'  # Single-use acceptance point. Stop cannot undo accepted work.
        try:
            result = handler(**arguments)  # Rechecks revision/cursor/locks at the point of use.
        except Exception as exc:
            with self.lock:
                item['status'], item['error'] = 'failed', str(exc)[:700]
            raise
        with self.lock:
            item['status'] = 'completed'
            if operation == 'console-send':
                item['input_status'] = result.get('input_status', 'unknown')
                if item['input_status'] == 'unknown':
                    item['status'] = 'uncertain'
                # A new explicit read is required before every subsequent input request.
                self.observations.get(session['id'], {}).pop(arguments['node_id'], None)
            if operation == 'start-node' and not result.get('started'):
                item['status'], item['error'] = 'failed', result.get('error', '')
        return result

    def provider(self, settings, method, suffix, body=None, handler=None):
        # The destination comes exclusively from operator-allowlisted browser settings.
        if settings.get('provider', 'ollama') != 'ollama' or settings['endpoint'] not in self.providers:
            raise ValueError('Ollama relay is not available for this provider')
        url = urlsplit(settings['endpoint'])
        cls = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
        connection = cls(url.hostname, url.port, timeout=settings['turn_timeout'] if handler else 10)
        try:
            connection.request(method, url.path + suffix, body,
                               {'Content-Type': 'application/json', 'Accept': 'text/event-stream, application/json'})
            response = connection.getresponse()
            if handler is None:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError('Provider response is too large')
                return response.status, json.loads(raw)
            if response.status != 200:
                handler.reply({'error': 'Model endpoint returned HTTP %s' % response.status}, 502)
                return
            handler.send_response(200)
            handler.send_header('Content-Type', response.getheader('Content-Type', 'text/event-stream'))
            handler.send_header('Connection', 'close')
            handler.end_headers()
            deadline, count = time.monotonic() + settings['turn_timeout'], 0
            while time.monotonic() < deadline:
                chunk = response.read1(65536)
                if not chunk:
                    break
                count += len(chunk)
                if count > 8 * 1024 * 1024:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        finally:
            connection.close()

    def tool(self, session, method, path, data, turn_id=''):
        try:
            result = self._tool(session, method, path, data, turn_id)
        except Exception as exc:
            with self.lock:
                self.tool_errors[session['id']] = {'operation': path.rsplit('/', 1)[-1],
                                                   'message': str(exc)[:1500]}
            raise
        with self.lock:
            if self.tool_errors.get(session['id'], {}).get('operation') == path.rsplit('/', 1)[-1]:
                self.tool_errors.pop(session['id'], None)
        return result

    def _tool(self, session, method, path, data, turn_id=''):
        service = self.automation
        if method == 'GET':
            logs = re.fullmatch(r'/api/nodes/([a-zA-Z0-9_-]{1,40})/logs', path)
            if logs:
                return {'logs': self.lab.logs(logs[1])}
            if path == '/api/automation/state':
                return service.state()
            if path == '/api/automation/catalog':
                return service.catalog()
            if path == '/api/automation/guide':
                return {'markdown': (ROOT / 'docs/practice-labs.md').read_text()}
            if path == '/api/storage':
                with self.lab.lock:
                    nodes = copy.deepcopy(self.lab.topology['nodes'])
                return self.lab.storage.report(nodes)
            match = re.fullmatch(r'/api/automation/proposals/([a-f0-9]{32})', path)
            if match and match[1] in session['proposals']:
                return service.proposal(match[1])
        if method == 'POST' and path == '/api/automation/console-read':
            result = service.console_read(**data)
            with self.lock:
                self.observations.setdefault(session['id'], {})[data['node_id']] = copy.deepcopy(result)
            return result
        if method == 'POST' and path in ('/api/automation/apply', '/api/automation/start-node', '/api/automation/console-send'):
            return self.guarded_tool(session, path.rsplit('/', 1)[1], data, turn_id)
        if method == 'POST' and path == '/api/automation/preview':
            result = service.preview(**data)
            with self.lock:
                session['proposals'] = (session['proposals'] + [result['proposal_id']])[-16:]
            return result
        raise ValueError('This route is not permitted through the Agent gateway')


class Gateway(JSONHandler):
    def handle_request(self):
        try:
            bridge = self.server.bridge
            token = self.headers.get('Authorization', '').removeprefix('Bearer ')
            with bridge.lock:
                session = next((s for s in bridge.sessions.values()
                                if secrets.compare_digest(s['gate'], token) and time.time()-s['touched'] < TTL), None)
            if not session:
                self.reply({'error': 'Unknown companion capability'}, 403)
                return
            if session['settings'].get('provider', 'ollama') == 'ollama' and session['settings']['endpoint'] not in bridge.providers:
                raise ValueError('Provider is no longer allowed; update settings')
            data = self.body() if self.command == 'POST' else None
            if self.command == 'POST' and self.path == '/provider/responses':
                # Never honor a model-selected alternate endpoint or model.
                data['model'] = session['settings']['model']
                bridge.provider(session['settings'], 'POST', '/responses', json.dumps(data).encode(), self)
            elif self.path.startswith('/tools/'):
                self.reply(bridge.tool(session, self.command, self.path[len('/tools'):], data,
                                       self.headers.get('X-Weblab-Agent-Turn', '')))
            else:
                self.reply({'error': 'Gateway route is not permitted'}, 403)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self.reply({'error': str(exc)}, 400)
            except OSError:
                pass

    do_GET = handle_request
    do_POST = handle_request

    def do_CONNECT(self):
        upstream = None
        try:
            bridge = self.server.bridge
            token = self.headers.get('Authorization', '').removeprefix('Bearer ')
            with bridge.lock:
                session = next((s for s in bridge.sessions.values()
                                if secrets.compare_digest(s['gate'], token) and time.time()-s['touched'] < TTL), None)
                if not session or session['settings'].get('provider') != 'openai' or self.path not in OPENAI_HOSTS:
                    self.reply({'error': 'OpenAI connection is not permitted'}, 403)
                    return
            upstream = socket.create_connection((self.path.rsplit(':', 1)[0], 443), timeout=10)
            self.connection.settimeout(15)
            self.send_response(200); self.end_headers(); self.wfile.flush()
            self.close_connection = True
            def enabled():
                with bridge.lock:
                    return (session in bridge.sessions.values() and session['settings'].get('provider') == 'openai'
                            and time.time()-session['touched'] < TTL)
            tunnel(self.connection, upstream, enabled)
        except OSError:
            self.close_connection = True
        finally:
            if upstream:
                upstream.close()
