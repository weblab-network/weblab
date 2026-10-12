"""Codex-managed, per-session ChatGPT device login; never return credentials."""
import copy
import math
import queue
import re
import threading
import time

DEVICE_URL = 'https://auth.openai.com/codex/device'


def limit_snapshot(value):
    """Allowlist display-only quota fields; never forward account/credential blobs."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for name in ('primary', 'secondary'):
        if name not in value:
            continue
        window = value[name]
        if window is None:
            result[name] = None
            continue
        if not isinstance(window, dict):
            continue
        percent = window.get('usedPercent')
        if type(percent) not in (int, float) or not math.isfinite(percent) or percent < 0:
            continue
        clean = {'used_percent': min(percent, 100)}
        for source, target in [('windowDurationMins', 'window_minutes'), ('resetsAt', 'resets_at')]:
            number = window.get(source)
            if type(number) is int and 0 <= number <= 10**12:
                clean[target] = number
        result[name] = clean
    return result


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

    def update_allowance(self, data, replace=False):
        if not isinstance(data, dict):
            return
        buckets = {}
        single = data.get('rateLimits')
        if isinstance(single, dict):
            buckets[str(single.get('limitId') or 'codex')[:80]] = single
        multiple = data.get('rateLimitsByLimitId')
        if isinstance(multiple, dict):
            for key, value in list(multiple.items())[:16]:
                if isinstance(key, str) and isinstance(value, dict):
                    buckets[key[:80]] = value
        with self.lock:
            previous = self.value.get('allowance', {})
            limits = {} if replace else copy.deepcopy(previous.get('limits', {}))
            changed = False
            for key, value in buckets.items():
                clean = limit_snapshot(value)
                if clean:
                    changed = True
                    old = limits.get(key, {})
                    limits[key] = {**old, **{name: ({**(old.get(name) or {}), **window}
                                               if window is not None else None)
                                           for name, window in clean.items()}}
            if not changed and not replace:
                return
            self.value['allowance'] = {'limits': dict(list(limits.items())[:16]),
                                       'checked_at': time.time(),
                                       'unavailable': not any(w for bucket in limits.values() for w in bucket.values())}

    def read_allowance(self, rpc, cancel=None):
        try:
            result = rpc.call('account/rateLimits/read', {}, timeout=3, cancel=cancel)
            self.update_allowance(result, replace=True)
        except Exception:
            # Usage is optional telemetry: a quota lookup must not fail a turn/login.
            with self.lock:
                self.value['allowance'] = {**self.value.get('allowance', {}), 'unavailable': True}

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
        if signed_in:
            self.read_allowance(rpc, self.stop)

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
