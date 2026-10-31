"""OpenSSH service supervisor. Separate container; never runs gateway networking."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, '/opt/weblab')
from external_tty import connect


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def terminate_sessions(uid, sig=signal.SIGKILL):
    # OpenSSH changes process titles and newer versions split sshd-auth/session;
    # BusyBox pkill -x sshd does not reliably match them. This PID namespace is
    # dedicated to the SSH service. Also stop the user's shell/background jobs.
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name) == os.getpid():
            continue
        try:
            owner = next(line.split()[1] for line in (path/'status').read_text().splitlines() if line.startswith('Uid:'))
            if (path/'comm').read_text().startswith('sshd') or int(owner) == uid:
                os.kill(int(path.name), sig)
        except (OSError, StopIteration):
            pass


def main():
    mode = sys.argv[1]
    state = Path(os.environ.get('WL_SSH_STATE', '/state'))
    state.mkdir(parents=True, exist_ok=True)
    if mode == 'keys':
        if not Path('/run/ssh/accept-keys').is_file():
            raise ValueError('SSH service is not ready to receive current grants')
        keys = json.loads(sys.argv[2])
        temporary = state/'keys.tmp'
        temporary.write_text(json.dumps(keys)); temporary.replace(state/'keys.json')
        return
    if mode not in ('console', 'gateway'):
        raise ValueError('Unknown SSH service mode')
    if not Path('/.dockerenv').exists():
        raise ValueError('Run the SSH supervisor only in its dedicated Docker container')
    work = Path('/run/ssh'); work.mkdir(parents=True, exist_ok=True)
    Path('/run/sshd').mkdir(exist_ok=True)
    host = state/'ssh_host_ed25519_key'
    if not host.exists():
        run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(host))
    host.chmod(0o600)
    (state/'fingerprint').write_text(run('ssh-keygen', '-lf', str(host)+'.pub').split()[1])
    user = 'console' if mode == 'console' else 'lab'
    port = 2222 if mode == 'console' else 2223
    config = f'''Port {port}
ListenAddress 0.0.0.0
HostKey {host}
PidFile /run/ssh/sshd.pid
AllowUsers {user}
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
PubkeyAuthentication yes
PermitRootLogin no
PermitEmptyPasswords no
PermitUserEnvironment no
PermitUserRC no
PermitTunnel no
AllowAgentForwarding no
X11Forwarding no
AllowStreamLocalForwarding no
GatewayPorts no
MaxAuthTries 3
MaxStartups 10:30:30
MaxSessions 8
LoginGraceTime 30
ClientAliveInterval 30
ClientAliveCountMax 3
PrintMotd no
LogLevel VERBOSE
'''
    if mode == 'console':
        config += '''AuthorizedKeysFile none
AuthorizedKeysCommand /usr/bin/python3 /opt/weblab/external_tty.py keys %t %k
AuthorizedKeysCommandUser console
ExposeAuthInfo yes
DisableForwarding yes
ForceCommand /usr/bin/python3 /opt/weblab/external_tty.py
'''
    else:
        config += '''AuthorizedKeysFile /run/ssh/authorized_keys
AllowTcpForwarding local
'''
    (work/'sshd_config').write_text(config)
    if mode == 'gateway':
        # Never briefly accept stale persisted authorizations after recreation.
        (state/'keys.json').unlink(missing_ok=True)
        (work/'accept-keys').touch()
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    process, generation, next_registration = None, None, 0
    try:
        while not stopping:
            if mode == 'gateway' and not (state/'keys.json').exists():
                time.sleep(.1)
                continue
            keys = (state/'keys.json').read_text() if (state/'keys.json').exists() else '[]'
            if mode == 'console' and time.monotonic() >= next_registration:
                next_registration = time.monotonic()+30
                try:
                    wire = connect()
                    try:
                        wire.send({'op':'host-key', 'key':Path(str(host)+'.pub').read_text().strip()})
                        if (wire.receive(3) or {}).get('ok'):
                            (work/'registered').touch()
                    finally: wire.sock.close()
                except (OSError, EOFError, ValueError):
                    pass
            if process is None or (mode == 'gateway' and keys != generation):
                if process is not None:
                    # Close existing sessions as well as the listener on grant changes.
                    terminate_sessions(10003)
                    process.wait(timeout=5)
                if mode == 'gateway':
                    entries = json.loads(keys)
                    if not isinstance(entries, list) or len(entries) > 64:
                        raise ValueError('Invalid SSH grants')
                    (work/'authorized_keys').write_text(''.join('restrict,pty,port-forwarding '+key+'\n' for key in entries))
                    (work/'authorized_keys').chmod(0o644)
                generation = keys
                (work/'ready').unlink(missing_ok=True)
                process = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(work/'sshd_config')])
            if process.poll() is not None:
                raise RuntimeError('SSH listener exited')
            if not (work/'ready').exists():
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        (work/'ready').touch()
                except OSError:
                    pass
            time.sleep(.5)
    finally:
        if process:
            terminate_sessions(10002 if mode == 'console' else 10003, signal.SIGTERM)
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired: process.kill()


if __name__ == '__main__':
    main()
