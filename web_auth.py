"""Optional fixed-account authentication. Runtime state is never a lab artifact."""
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

ROLES = {'admin': 'admin', 'user': 'writer', 'guest': 'reader'}
COOKIE = 'weblab_session'
ITERATIONS = 600_000
LOGIN_FAILURE_TTL = 15 * 60
LOGIN_DELAY_THRESHOLD = 3
LOGIN_LOCK_THRESHOLD = LOGIN_DELAY_THRESHOLD + 10
ACTIVITY_LIMIT = 10000
ACTIVITY_DAYS = 30


def console_identity(owner):
    """Private transport identity, distinct from cookies and stored token digests."""
    return hashlib.sha256(('weblab-console:' + owner).encode()).hexdigest()


def activity_operation(method, path):
    """Classify routes without retaining arbitrary paths, filenames or payloads."""
    routes = {
        ('POST', '/api/auth/login'): 'auth.login',
        ('POST', '/api/auth/logout'): 'auth.logout',
        ('PUT', '/api/topology'): 'topology.save',
        ('PUT', '/api/instructions'): 'instructions.update',
        ('POST', '/api/import'): 'lab.import',
        ('POST', '/api/export'): 'lab.export',
        ('POST', '/api/export/initial-configs'): 'config.export_live',
        ('POST', '/api/export/saved-configs'): 'config.export_saved',
        ('POST', '/api/lab/start'): 'lab.start',
        ('POST', '/api/lab/stop'): 'lab.stop',
    }
    if (method, path) in routes:
        return routes[method, path], ''
    if method == 'POST':
        match = re.fullmatch(r'/api/(nodes|links)/([a-zA-Z0-9_-]{1,40})/(start|stop|carrier|traffic)', path)
        if match:
            return ('node.' if match[1] == 'nodes' else 'link.') + match[3], match[2]
        if path.startswith('/api/images/'):
            return 'image.upload', ''
        if path.startswith('/api/agent/') and allowed('admin', method, path):
            action = path.rsplit('/', 1)[1]
            if action not in ('login-status', 'history-read', 'attachment', 'proposal', 'check'):
                return 'agent.' + action, ''
            return None
    return ('request.other', '') if method in ('POST', 'PUT') else None


class Denied(Exception):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS).hex()


def has_letter_and_digit(password):
    return bool(re.search(r'[A-Za-z]', password) and re.search(r'[0-9]', password))


def generated_password():
    while True:
        password = secrets.token_urlsafe(24)
        if has_letter_and_digit(password):
            return password


def allowed(role, method, path):
    # Browser Agent is login-bound; external MCP has no authenticated adapter yet.
    if path.startswith('/api/automation/'):
        return False
    if path.startswith('/api/agent/'):
        if method == 'GET' and path == '/api/agent/info':
            return True
        return role == 'admin' and (
            (method == 'GET' and path == '/api/agent/state') or
            (method == 'POST' and path.removeprefix('/api/agent/') in {
                'session', 'settings', 'check', 'turn', 'stop', 'reset', 'close',
                'proposal', 'decision', 'login', 'login-status', 'login-cancel',
                'logout', 'history-read', 'history-export', 'history-open',
                'history-rename', 'history-delete', 'attachment'}))
    if method == 'GET':
        if path == '/api/activity':
            return role == 'admin'
        if path in ('/api/state', '/api/storage', '/api/instructions') or re.fullmatch(r'/ws/[\w-]+', path):
            return True
        if re.fullmatch(r'/api/nodes/[\w-]+/logs', path):
            return True
        return role == 'admin' and bool(re.fullmatch(r'/api/exports/[a-f0-9]{32}', path))
    if method == 'POST' and (path in ('/api/lab/start', '/api/lab/stop') or
                            re.fullmatch(r'/api/nodes/[\w-]+/(start|stop)', path) or
                            re.fullmatch(r'/api/links/[\w-]+/(carrier|traffic)', path)):
        return role in ('admin', 'writer')
    if role != 'admin':
        return False
    return ((method == 'PUT' and path in ('/api/topology', '/api/instructions')) or
            (method == 'POST' and (path in ('/api/import', '/api/export', '/api/export/initial-configs',
                                           '/api/export/saved-configs') or path.startswith('/api/images/'))))


class Auth:
    def __init__(self, directory, passwords, secure=False, resets=None):
        if set(passwords) != set(ROLES) or any(not isinstance(p, str) or not (16 if n == 'admin' else 6) <= len(p) <= 256 for n, p in passwords.items()):
            raise ValueError('WL_AUTH requires ADMIN passwords of 16–256 characters and USER/GUEST passwords of 6–256 characters; run python3 web_auth.py generate')
        if len(set(passwords.values())) != 3:
            raise ValueError('Use a different password for each Weblab account')
        resets = resets or {}
        if any(not isinstance(v, str) or len(v) > 128 for v in resets.values()):
            raise ValueError('WL_AUTH account reset values must be strings of at most 128 characters')
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        path = directory / 'auth.sqlite3'
        # Create with private permissions before SQLite writes any credentials.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        path.chmod(0o600)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.secure = secure
        self.activity_error = False
        # Bounded, per-account admission: no queue of sleeping login threads.
        self.login_locks = {name: threading.Lock() for name in (*ROLES, 'unknown')}
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, salt BLOB, hash TEXT);
            CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, name TEXT, csrf TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS attempts (bucket TEXT PRIMARY KEY, started REAL, count INTEGER);
            CREATE TABLE IF NOT EXISTS login_failures (bucket TEXT PRIMARY KEY, updated REAL, count INTEGER);
            CREATE TABLE IF NOT EXISTS account_locks (name TEXT PRIMARY KEY, locked INTEGER, reset_marker TEXT);
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL,
                finished REAL, account TEXT, role TEXT, session_ref TEXT,
                source TEXT NOT NULL, operation TEXT NOT NULL, target TEXT,
                result TEXT NOT NULL, http_status INTEGER);
        ''')
        with self.db:
            for name, password in passwords.items():
                old = self.db.execute('SELECT salt, hash FROM accounts WHERE name=?', (name,)).fetchone()
                changed_password = not old or not hmac.compare_digest(old[1], password_hash(password, old[0]))
                if changed_password:
                    if name != 'admin' and not has_letter_and_digit(password):
                        raise ValueError('New or changed USER/GUEST passwords require at least one ASCII letter and one digit')
                    salt = secrets.token_bytes(16)
                    self.db.execute('INSERT OR REPLACE INTO accounts VALUES (?,?,?)', (name, salt, password_hash(password, salt)))
                    self.db.execute('DELETE FROM sessions WHERE name=?', (name,))
                marker = digest(resets.get(name, ''))
                lock = self.db.execute('SELECT locked, reset_marker FROM account_locks WHERE name=?', (name,)).fetchone()
                changed_reset = lock is not None and lock[1] != marker
                if changed_password or changed_reset or lock is None:
                    self.db.execute('INSERT OR REPLACE INTO account_locks VALUES (?,0,?)', (name, marker))
                if changed_password or changed_reset:
                    self.db.execute('DELETE FROM login_failures WHERE bucket=?', ('account:' + name,))
                    self.db.execute('DELETE FROM attempts WHERE bucket=?', ('account:' + name,))
        self.dummy_salt = secrets.token_bytes(16)
        with self.lock, self.db:
            self._prune_activity()

    def _prune_activity(self):
        self.db.execute('DELETE FROM activity WHERE started<?', (time.time() - ACTIVITY_DAYS * 86400,))
        self.db.execute('DELETE FROM activity WHERE id<=COALESCE((SELECT id FROM activity ORDER BY id DESC LIMIT 1 OFFSET ?),0)',
                        (ACTIVITY_LIMIT,))

    @staticmethod
    def activity_identity(account, owner):
        account = account if isinstance(account, str) and account in ROLES else None
        # This is a display/correlation reference, not a usable login credential.
        reference = digest('weblab-activity:' + owner) if owner else None
        return account, ROLES.get(account), reference

    def _activity_failed(self):
        if not self.activity_error:
            print('Weblab activity recording failed; records may be incomplete. Check auth storage.', flush=True)
        self.activity_error = True

    def activity_begin(self, account, owner, source, operation, target=''):
        try:
            with self.lock, self.db:
                cursor = self.db.execute('INSERT INTO activity (started,account,role,session_ref,source,operation,target,result) '
                                         'VALUES (?,?,?,?,?,?,?,?)',
                    (time.time(), *self.activity_identity(account, owner), source, operation, target, 'unfinished'))
                self._prune_activity()
                return cursor.lastrowid
        except sqlite3.Error:
            self._activity_failed()
            return None

    def activity_finish(self, ident, result, status=None, identity=None):
        if ident is None:
            return
        try:
            with self.lock, self.db:
                if identity is not None:
                    self.db.execute('UPDATE activity SET account=?,role=?,session_ref=? WHERE id=?',
                                    (*self.activity_identity(*identity), ident))
                self.db.execute('UPDATE activity SET finished=?,result=?,http_status=? WHERE id=?',
                                (time.time(), result, status, ident))
        except sqlite3.Error:
            self._activity_failed()

    def activity_page(self, query):
        try:
            return self._activity_page(query)
        except sqlite3.Error as exc:
            self._activity_failed()
            raise ValueError('Activity storage is unavailable; check server storage') from exc

    def _activity_page(self, query):
        if set(query) - {'before', 'account', 'source'} or any(len(v) != 1 for v in query.values()):
            raise ValueError('Invalid activity filters')
        clauses, values = [], []
        for key, choices in (('account', ROLES), ('source', ('browser', 'agent'))):
            value = query.get(key, [''])[0]
            if value:
                if value not in choices:
                    raise ValueError('Invalid activity filter')
                clauses.append(key + '=?'); values.append(value)
        before = query.get('before', [''])[0]
        if before:
            if not re.fullmatch(r'[0-9]{1,18}', before) or int(before) < 1:
                raise ValueError('Invalid activity cursor')
            clauses.append('id<?'); values.append(int(before))
        with self.lock, self.db:
            self._prune_activity()
            cursor = self.db.execute('SELECT * FROM activity' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') +
                                     ' ORDER BY id DESC LIMIT 101', values)
            columns = [c[0] for c in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return {'records': rows[:100], 'next_before': rows[99]['id'] if len(rows) > 100 else None,
                'recording_error': self.activity_error, 'retention_days': ACTIVITY_DAYS, 'max_records': ACTIVITY_LIMIT}

    @classmethod
    def from_env(cls, data_dir):
        enabled = os.environ.get('WL_AUTH', '0')
        secure = os.environ.get('WL_AUTH_COOKIE_SECURE', '0')
        if enabled not in ('0', '1') or secure not in ('0', '1'):
            raise ValueError('WL_AUTH and WL_AUTH_COOKIE_SECURE must be 0 or 1')
        if enabled == '0':
            return None
        return cls(Path(data_dir) / '.auth', {n: os.environ.get('WL_AUTH_' + n.upper() + '_PASSWORD', '') for n in ROLES}, secure == '1',
                   resets={n: os.environ.get('WL_AUTH_' + n.upper() + '_RESET', '') for n in ROLES})

    def cookie_token(self, headers):
        try:
            cookie = SimpleCookie()
            cookie.load(headers.get('Cookie', ''))
            value = cookie[COOKIE].value if COOKIE in cookie else ''
            return value if re.fullmatch(r'[A-Za-z0-9_-]{43}', value) else ''
        except CookieError:
            return ''

    def session(self, token):
        if not token:
            return None
        return self.session_digest(digest(token))

    def session_digest(self, token_digest):
        """Internal identity lookup; never exposes or stores the bearer cookie."""
        with self.lock:
            row = self.db.execute('SELECT name, csrf, expires FROM sessions WHERE token=?', (token_digest,)).fetchone()
        if not row or row[2] <= time.time():
            return None
        return {'name': row[0], 'role': ROLES[row[0]], 'csrf': row[1], 'expires': row[2]}

    @staticmethod
    def _wait_before_verification():
        time.sleep(3 + secrets.randbelow(2001) / 1000)

    def login(self, name, password, ip, remember=False):
        if not isinstance(name, str) or not isinstance(password, str) or len(password) > 256:
            raise Denied('Invalid username or password', 401)
        account = name if name in ROLES else 'unknown'
        gate = self.login_locks[account]
        if not gate.acquire(blocking=False):
            raise Denied('A login attempt is already being checked. Please wait and try again.', 429)
        try:
            return self._verify_login(name, password, ip, account, remember)
        finally:
            gate.release()

    def _verify_login(self, name, password, ip, account, remember):
        now = time.time()
        buckets = [('ip:' + digest(ip), 30), ('account:' + account, 10)]
        with self.lock, self.db:
            locked = self.db.execute('SELECT locked FROM account_locks WHERE name=?', (name,)).fetchone()
            if locked and locked[0]:
                raise Denied('Account locked after repeated failed logins. Ask the server administrator to reset it.', 423)
            self.db.execute('DELETE FROM attempts WHERE started<?', (now - 60,))
            self.db.execute('DELETE FROM login_failures WHERE updated<?', (now - LOGIN_FAILURE_TTL,))
            self.db.execute('DELETE FROM sessions WHERE expires<=?', (now,))
            delayed = False
            # Bound unknown names to one bucket and hash IPs; never store submitted passwords.
            for bucket, limit in buckets:
                row = self.db.execute('SELECT count FROM attempts WHERE bucket=?', (bucket,)).fetchone()
                if row and row[0] >= limit:
                    raise Denied('Too many login attempts. Try again in a minute.', 429)
                self.db.execute('INSERT INTO attempts VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1', (bucket, now))
                failures = self.db.execute('SELECT count FROM login_failures WHERE bucket=?', (bucket,)).fetchone()
                delayed |= bool(failures and failures[0] >= LOGIN_DELAY_THRESHOLD)
        # Sleep before hashing, outside the shared DB lock. Existing sessions,
        # logout and other accounts remain usable while this account is delayed.
        if delayed:
            self._wait_before_verification()
        with self.lock, self.db:
            now = time.time()
            row = self.db.execute('SELECT salt, hash FROM accounts WHERE name=?', (name,)).fetchone()
            value = password_hash(password, row[0] if row else self.dummy_salt)
            if not row or not hmac.compare_digest(row[1], value):
                for bucket, _ in buckets:
                    self.db.execute('INSERT INTO login_failures VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET updated=excluded.updated, count=count+1', (bucket, now))
                failures = self.db.execute('SELECT count FROM login_failures WHERE bucket=?', ('account:' + account,)).fetchone()[0]
                locked = name in ROLES and failures >= LOGIN_LOCK_THRESHOLD
                if locked:
                    self.db.execute('UPDATE account_locks SET locked=1 WHERE name=?', (name,))
                # Denied is an exception: commit counters before leaving the transaction.
                self.db.commit()
                if locked:
                    raise Denied('Account locked after repeated failed logins. Ask the server administrator to reset it.', 423)
                raise Denied('Invalid username or password', 401)
            for bucket, _ in buckets:
                self.db.execute('DELETE FROM login_failures WHERE bucket=?', (bucket,))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            ttl = 14 * 86400 if remember else 12 * 3600
            self.db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest(token), name, csrf, now + ttl))
            # Keep storage bounded even for a shared fixed account.
            self.db.execute('DELETE FROM sessions WHERE name=? AND token NOT IN (SELECT token FROM sessions WHERE name=? ORDER BY expires DESC LIMIT 100)', (name, name))
        return token, ttl

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute('DELETE FROM sessions WHERE token=?', (digest(token),))

    def cookie(self, token, ttl=None):
        value = f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict'
        if ttl is not None:
            value += f'; Max-Age={ttl}'
        if self.secure:
            value += '; Secure'
        return value

    def close(self):
        self.db.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Generate unique fixed-account credentials for your private .env file')
    parser.add_argument('command', choices=['generate'])
    parser.parse_args()
    print('WL_AUTH=1')
    for name in ROLES:
        print(f'WL_AUTH_{name.upper()}_PASSWORD={generated_password()}')
