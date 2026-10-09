"""Codex-managed, per-session ChatGPT device login; never return credentials."""
import copy
import queue
import re
import threading
import time

DEVICE_URL = 'https://auth.openai.com/codex/device'


class Login:
    def __init__(self, session, rpc_factory):
        self.session, self.rpc_factory = session, rpc_factory
        self.lock = threading.RLock()
        self.worker = None
        self.rpc = None
        self.stop = threading.Event()
        self.value = {'status': 'unknown', 'signed_in': False, 'models': []}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.value)

    def busy(self):
        return bool(self.worker and self.worker.is_alive())

    def start(self, action):
        if action == 'login-cancel':
            self.stop.set()
            return self.snapshot()
        with self.session.lock, self.lock:
            if self.busy():
                raise ValueError('A sign-in operation is already running')
            if self.session.worker and self.session.worker.is_alive():
                raise ValueError('Stop the active turn before changing sign-in')
            self.stop.clear()
            self.value = {'status': 'connecting', 'signed_in': False, 'models': []}
            self.worker = threading.Thread(target=self.run, args=(action,), daemon=True)
            self.worker.start()
            return self.snapshot()

    def account(self, rpc):
        result = rpc.call('account/read', {'refreshToken': False}, cancel=self.stop)
        account = result.get('account') or {}
        signed_in = account.get('type') == 'chatgpt'
        models = []
        if signed_in:
            result = rpc.call('model/list', {'limit': 100}, cancel=self.stop)
            models = [m['model'] for m in result.get('data', [])
                      if isinstance(m, dict) and isinstance(m.get('model'), str)][:100]
        with self.lock:
            self.value = {'status': 'signed-in' if signed_in else 'signed-out',
                          'signed_in': signed_in, 'models': models,
                          'plan': str(account.get('planType', ''))[:80] if signed_in else ''}

    def run(self, action):
        rpc, login_id = None, None
        try:
            rpc = self.rpc = self.rpc_factory(self.session.auth_config(), env=self.session.process_env())
            rpc.call('initialize', {'clientInfo': {'name': 'weblab_agent', 'version': '0.1.0'}}, cancel=self.stop)
            rpc.send({'method': 'initialized'})
            if action == 'logout':
                rpc.call('account/logout', {}, cancel=self.stop)
                self.session.reset()
                with self.lock:
                    self.value = {'status': 'signed-out', 'signed_in': False, 'models': []}
                return
            if action == 'login':
                result = rpc.call('account/login/start', {'type': 'chatgptDeviceCode'}, cancel=self.stop)
                login_id = result.get('loginId')
                code = result.get('userCode', '')
                if result.get('verificationUrl') != DEVICE_URL or not re.fullmatch(r'[A-Za-z0-9-]{4,32}', code):
                    raise ValueError('Unexpected sign-in response')
                with self.lock:
                    self.value = {'status': 'waiting', 'signed_in': False, 'models': [],
                                  'url': DEVICE_URL, 'code': code}
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline and not self.stop.is_set():
                    try:
                        event = rpc.pending.popleft() if rpc.pending else rpc.event(timeout=.25)
                    except queue.Empty:
                        continue
                    params = event.get('params', {})
                    if event.get('method') == 'account/login/completed' and params.get('loginId') == login_id:
                        if not params.get('success'):
                            raise ValueError('Login failed')
                        self.account(rpc)
                        return
                rpc.call('account/login/cancel', {'loginId': login_id}, timeout=5)
                with self.lock:
                    self.value = {'status': 'cancelled' if self.stop.is_set() else 'expired',
                                  'signed_in': False, 'models': []}
                return
            self.account(rpc)
        except Exception:
            # Provider/auth errors may contain secrets or URLs. Do not echo them.
            with self.lock:
                self.value = {'status': 'cancelled' if self.stop.is_set() else 'failed',
                              'signed_in': False, 'models': [],
                              'error': 'Sign-in could not complete. Check connectivity and enable device-code login in your ChatGPT account, then try again.'}
        finally:
            if rpc:
                rpc.close()
            self.rpc = None

    def close(self):
        self.stop.set()
        if self.rpc:
            self.rpc.close()
        if self.worker:
            self.worker.join(timeout=10)
