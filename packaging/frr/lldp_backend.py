#!/usr/bin/env python3
"""Local FRR VTY backend for LLDP and opt-in SNMPv2c.

Persistent intent belongs in integrated frr.conf. /run holds only recovery state
for a backend restart within this container, including unsaved CLI changes.
"""
import copy
import grp
import json
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import subprocess
import threading
import time
import snmp_backend
import vlan_backend
import vrrp_backend

SOCKET = Path('/run/frr/weblab-lldpd.vty')
STATE = Path('/run/frr/weblab-lldp.json')
CONTROL = '/run/weblab-lldpd.socket'
CHILD = Path('/run/frr/weblab-lldp-child.json')
PORT = re.compile(r'eth[0-7]\Z')
SYSTEM_DESCRIPTION = snmp_backend.SYSTEM_DESCRIPTION


def clean(value, limit=256):
    """Neighbor TLVs are untrusted: remove terminal controls and bound display."""
    return ''.join(c for c in str(value) if c.isprintable())[:limit]


def first(value):
    return value[0] if isinstance(value, list) and value else {}


def process_start(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]


def recover_child(child_path=None):
    """Reap only the process group launched by the previous backend instance."""
    child_path = child_path or CHILD
    if not child_path.exists():
        return
    owner = json.loads(child_path.read_text())
    pid, started = owner['pid'], owner['started']
    if type(pid) is not int or pid <= 1 or not isinstance(started, str):
        raise ValueError('Invalid LLDP child identity')
    try:
        if process_start(pid) == started and os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if process_start(pid) != started:
                    break
                time.sleep(.05)
            else:
                if process_start(pid) == started and os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGKILL)
    except (FileNotFoundError, ProcessLookupError):
        pass
    child_path.unlink(missing_ok=True)


def neighbors(document):
    rows = []
    for lldp in document.get('lldp', []):
        for entry in lldp.get('interface', []):
            chassis, port = first(entry.get('chassis', [])), first(entry.get('port', []))
            rows.append({
                'local': clean(entry.get('name', '?')),
                'system': clean(first(chassis.get('name', [])).get('value', '?')),
                'port': clean(first(port.get('id', [])).get('value', '?')),
                'description': clean(first(chassis.get('descr', [])).get('value', '')),
                'chassis': clean(first(chassis.get('id', [])).get('value', '')),
                'port_description': clean(first(port.get('descr', [])).get('value', '')),
                'capabilities': ', '.join(clean(item.get('type', '?'), 64)
                    for item in chassis.get('capability', [])[:16]
                    if item.get('enabled') is True or item.get('enabled') == 'on') or '-',
                'management': ', '.join(clean(item.get('value', '?'), 64)
                    for item in chassis.get('mgmt-ip', [])[:16]) or '-',
                'ttl': clean(first(port.get('ttl', [])).get('value', ''), 16),
                'age': clean(entry.get('age', '-'), 64),
            })
            if len(rows) >= 256:
                return rows
    return rows


def canonical(line, in_interface=False):
    # vtysh validates mode/ambiguity, but its VTY protocol forwards the original
    # spelling, including abbreviations. Normalize only our known command set.
    words = line.strip().split()
    if words[:1] == ['do']:
        words = words[1:]
    patterns = ('vlan interface <name>', 'no vlan interface <name>',
                'show vrrp platform', 'show vlan interfaces', 'show snmp', 'snmp-server enable', 'no snmp-server enable',
                'snmp-server community <value> ro', 'no snmp-server community',
                'snmp-server listen <address>', 'no snmp-server listen',
                'snmp-server source <network>', 'no snmp-server source',
                'snmp-server contact <text...>', 'no snmp-server contact',
                'snmp-server location <text...>', 'no snmp-server location',
                'write terminal', 'show lldp', 'show lldp neighbors',
                'show lldp neighbors detail', 'lldp run', 'no lldp run',
                'lldp transmit', 'no lldp transmit', 'lldp receive', 'no lldp receive',
                'interface <name>', 'interface <name> vrf <vrf>',
                'no interface <name>', 'hostname <name>', 'no hostname',
                'configure terminal', 'configure terminal file-lock',
                'end', 'exit', 'quit', 'exit-vrf')
    matches = []
    for pattern in patterns:
        if 'lldp' in pattern and not pattern.startswith('show'):
            if in_interface != pattern.endswith(('transmit', 'receive')):
                continue
        tokens = pattern.split()
        if tokens[-1] == '<text...>' and len(words) >= len(tokens):
            if all(t.startswith(w) for t, w in zip(tokens[:-1], words)):
                matches.append(' '.join(tokens[:-1] + words[len(tokens)-1:]))
            continue
        if len(tokens) == len(words) and all(t.startswith('<') or t.startswith(w)
                                            for t, w in zip(tokens, words)):
            matches.append(' '.join(w if t.startswith('<') else t for t, w in zip(tokens, words)))
    return matches[0] if len(matches) == 1 else ' '.join(words)


class Lldp:
    def __init__(self, state_path=STATE):
        self.state_path = state_path
        self.state = {'enabled': False, 'hostname': socket.gethostname(), 'ports': {},
                      'snmp': snmp_backend.defaults(), 'vlans': []}
        self.snmp = snmp_backend.Snmp(process_start)
        self.vlans = vlan_backend.Vlans()
        self.vrrp = vrrp_backend.Vrrp()
        self.process = None
        self.lock = threading.RLock()
        self.error = ''
        self.replaying = set()
        if state_path.exists():
            state = json.loads(state_path.read_text())
            state.setdefault('snmp', snmp_backend.defaults())
            state.setdefault('vlans', [])
            vlan_backend.validate(state['vlans'])
            snmp_backend.validate(state['snmp'])
            if (set(state) != set(self.state) or type(state['enabled']) is not bool
                    or not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', state['hostname'])
                    or not isinstance(state['ports'], dict)
                    or any(not PORT.fullmatch(p) or set(v) != {'transmit', 'receive'}
                           or any(type(b) is not bool for b in v.values())
                           for p, v in state['ports'].items())):
                raise ValueError('Invalid LLDP recovery state')
            self.state = state

    def cli(self, *args):
        result = subprocess.run(['lldpcli', '-u', CONTROL, *args],
                                capture_output=True, text=True, timeout=3)
        if result.returncode:
            raise RuntimeError('lldpcli failed: ' + clean(result.stderr))
        if len(result.stdout) > 1024 * 1024:
            raise RuntimeError('LLDP response exceeds 1 MiB')
        return result.stdout

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait()
            self.process = None
            CHILD.unlink(missing_ok=True)

    def apply(self):
        self.vlans.apply(self.state['vlans'])
        self.snmp.apply(self.state['snmp'], self.state['hostname'])
        if not self.state['enabled']:
            self.stop()
            return
        if self.process is None or self.process.poll() is not None:
            self.stop()
            # Discover no ports until their administrative policy is installed.
            self.process = subprocess.Popen(
                ['lldpd', '-dd', '-k', '-i', '-x', '-X', snmp_backend.AGENTX,
                 '-I', '!*', '-C', 'eth[0-7]',
                 '-u', CONTROL, '-p', '/run/weblab-lldpd.pid', '-O', '/dev/null'],
                start_new_session=True)
            CHILD.write_text(json.dumps({'pid': self.process.pid,
                                         'started': process_start(self.process.pid)}))
            CHILD.chmod(0o600)
            deadline = time.monotonic() + 4
            while True:
                try:
                    self.cli('show', 'configuration')
                    break
                except (RuntimeError, subprocess.TimeoutExpired):
                    if time.monotonic() >= deadline or self.process.poll() is not None:
                        self.stop()
                        raise RuntimeError('lldpd did not become ready')
                    time.sleep(.05)
            self.cli('configure', 'lldp', 'status', 'disabled')
            self.cli('configure', 'lldp', 'portidsubtype', 'ifname')
            # Internal one-port bridges carry virtual MACs; the lab device is
            # still a router, not a switch offering inter-port bridging.
            self.cli('configure', 'system', 'capabilities', 'enabled', 'router')
        self.cli('configure', 'system', 'hostname', self.state['hostname'])
        self.cli('configure', 'system', 'description', SYSTEM_DESCRIPTION)
        ports = sorted(p.name for p in Path('/sys/class/net').iterdir() if PORT.fullmatch(p.name))
        # Only the physical Weblab ports, never a host uplink or VLAN child.
        # Newly discovered ports inherit disabled until explicitly configured below.
        self.cli('configure', 'system', 'interface', 'pattern', ','.join(ports) or '!*')
        for port in ports:
            policy = self.state['ports'].get(port, {'transmit': True, 'receive': True})
            mode = {(True, True): 'rx-and-tx', (True, False): 'tx-only',
                    (False, True): 'rx-only', (False, False): 'disabled'}[
                        policy['transmit'], policy['receive']]
            self.cli('configure', 'ports', port, 'lldp', 'status', mode)

    def persist_runtime(self):
        temporary = self.state_path.with_suffix('.tmp')
        # SNMP communities must never pass through a world-readable temporary file.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(self.state))
        temporary.replace(self.state_path)

    def config(self):
        lines = ['lldp run'] if self.state['enabled'] else []
        lines += snmp_backend.config_lines(self.state['snmp'])
        lines += [f'vlan interface {name}' for name in sorted(self.state['vlans'])]
        for port, policy in sorted(self.state['ports'].items()):
            disabled = [f' no lldp {key}' for key in ('transmit', 'receive') if not policy[key]]
            if disabled:
                lines.extend([f'interface {port}', *disabled, 'exit', '!'])
        return '\n'.join(lines) + '\n'

    def command(self, line, session):
        """Return VTY text/status; vtysh provides command parsing and mode help."""
        line = canonical(line, bool(session.get('interface')))
        if line == 'write terminal':
            return self.config()
        if line == 'show vrrp platform':
            return self.vrrp.show()
        if line == 'show vlan interfaces':
            return self.vlans.show(self.state['vlans'])
        if line == 'show snmp':
            return self.snmp.show(self.state['snmp'])
        if line == 'show lldp':
            status = 'enabled' if self.state['enabled'] else 'disabled'
            health = 'running' if self.process and self.process.poll() is None else 'stopped'
            return f'LLDP {status}; service {health}\n' + (f'Error: {self.error}\n' if self.error else '')
        if line.startswith('show lldp neighbors'):
            if not self.state['enabled']:
                return 'LLDP is disabled\n'
            rows = neighbors(json.loads(self.cli('-f', 'json0', 'show', 'neighbors', 'details')))
            rows = [row for row in rows if PORT.fullmatch(row['local']) and
                    self.state['ports'].get(row['local'], {}).get('receive', True)]
            if not rows:
                return 'No LLDP neighbors\n'
            detailed = line.endswith(' detail')
            text = '' if detailed else 'Local interface  Neighbor                         Capability               Port\n'
            for row in rows:
                if detailed:
                    fields = [('Local interface', row['local']), ('System name', row['system']),
                              ('Chassis ID', row['chassis']), ('Port ID', row['port']),
                              ('Port description', row['port_description']),
                              ('Capabilities', row['capabilities']),
                              ('System description', row['description']),
                              ('Management addresses', row['management']),
                              ('Advertised TTL', row['ttl'] + ' seconds' if row['ttl'] else '-'),
                              ('Age', row['age'])]
                    text += ''.join(f'{label}: {value or "-"}\n' for label, value in fields) + '\n'
                else:
                    text += (f"{row['local']:<16} {row['system'][:32]:<32} "
                             f"{row['capabilities'][:24]:<24} {row['port']}\n")
            return text
        if line == 'XFRR_start_configuration':
            session['replay'] = copy.deepcopy(self.state)
            self.replaying.add(id(session))
            return ''
        if line == 'XFRR_end_configuration':
            snmp_backend.validate(self.state['snmp'])
            # A failed replay must restore both intent and any partially applied
            # links/services, rather than leaving them until another CLI command.
            previous = session.get('replay')
            try:
                self.apply()
                self.persist_runtime()
            except Exception:
                if previous is not None:
                    self.state = previous
                    try:
                        self.apply()
                        self.persist_runtime()
                    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
                        self.error = clean(error)
                raise
            finally:
                session.pop('replay', None)
                self.replaying.discard(id(session))
            self.error = ''
            return ''
        if line in ('end', 'exit', 'quit', 'exit-vrf') or line.startswith('configure '):
            session.pop('interface', None)
            return ''
        if line.startswith('interface '):
            # Other FRR interface types remain usable; reject only LLDP mutations there.
            session['interface'] = line.split()[1] if len(line.split()) == 2 else None
            return ''
        previous = copy.deepcopy(self.state)
        if line.startswith(('vlan interface ', 'no vlan interface ')):
            name = line.split()[-1]
            vlan_backend.parse(name)
            candidate = set(self.state['vlans'])
            if line.startswith('no '):
                candidate.discard(name)
            else:
                candidate.add(name)
            vlan_backend.validate(sorted(candidate))
            self.state['vlans'] = sorted(candidate)
        elif line.startswith(('snmp-server ', 'no snmp-server ')):
            candidate = copy.deepcopy(self.state['snmp'])
            snmp_backend.command(line, candidate)
            validation = dict(candidate)
            if 'replay' in session:
                validation['enabled'] = False
            snmp_backend.validate(validation)
            self.state['snmp'] = candidate
        elif line in ('lldp run', 'no lldp run'):
            self.state['enabled'] = not line.startswith('no ')
        elif line in ('lldp transmit', 'no lldp transmit', 'lldp receive', 'no lldp receive'):
            port = session.get('interface') or ''
            if not PORT.fullmatch(port):
                raise ValueError('LLDP interface controls support eth0 through eth7 in the default VRF')
            policy = self.state['ports'].setdefault(port, {'transmit': True, 'receive': True})
            policy[line.split()[-1]] = not line.startswith('no ')
        elif line == 'no hostname':
            self.state['hostname'] = socket.gethostname()
        elif line.startswith('hostname '):
            hostname = line.split(' ', 1)[1]
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', hostname):
                raise ValueError('Invalid LLDP hostname')
            self.state['hostname'] = hostname
        elif line.startswith('no interface ') and PORT.fullmatch(line.split()[-1]):
            self.state['ports'].pop(line.split()[-1], None)
        else:
            # FRR broadcasts common mode/library commands to every VTY backend.
            # They carry no LLDP settings and do not execute anything here.
            if line == 'lldp' or line.startswith(('lldp ', 'no lldp ', 'show lldp ', 'vlan ', 'no vlan ')):
                raise ValueError('Unsupported LLDP command')
            return ''
        try:
            if not session.get('replay'):
                self.apply()
                self.persist_runtime()
            self.error = ''
        except Exception:
            self.state = previous
            try:
                self.apply()
            except Exception as error:
                self.error = clean(error)
                self.stop()
            raise
        return ''


class Handler(socketserver.BaseRequestHandler):
    def setup(self):
        self.session = {}

    def finish(self):
        with self.server.backend.lock:
            previous = self.session.pop('replay', None)
            self.server.backend.replaying.discard(id(self.session))
            if previous is not None:
                self.server.backend.state = previous
                self.server.backend.persist_runtime()

    def handle(self):
        session = self.session
        pending = b''
        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            pending += chunk
            if len(pending) > 16384:
                return
            while b'\0' in pending:
                raw, pending = pending.split(b'\0', 1)
                status = 0
                try:
                    with self.server.backend.lock:
                        text = self.server.backend.command(raw.decode('utf-8'), session)
                except (ValueError, TypeError, AttributeError, RuntimeError, OSError, subprocess.SubprocessError) as error:
                    text, status = '% ' + clean(error) + '\n', 13  # CMD_WARNING_CONFIG_FAILED
                try:
                    self.request.sendall(text.encode() + b'\0\0\0' + bytes([status]))
                except (BrokenPipeError, ConnectionResetError):
                    return


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    recover_child()
    recover_child(snmp_backend.CHILD)
    backend = Lldp()
    try:
        backend.vlans.apply(backend.state['vlans'])
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        backend.error = clean(error)
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    with Server(str(SOCKET), Handler) as server:
        server.backend = backend
        os.chown(SOCKET, 0, grp.getgrnam('frrvty').gr_gid)
        SOCKET.chmod(0o660)
        def maintain():
            while True:
                time.sleep(1)
                with backend.lock:
                    if not backend.replaying:
                        backend.vrrp.reconcile()
                        backend.snmp.apply(backend.state['snmp'], backend.state['hostname'])
                    if not backend.replaying and backend.state['enabled'] and (backend.process is None or backend.process.poll() is not None):
                        try:
                            backend.apply()
                            backend.error = ''
                        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
                            backend.error = clean(error)
                            backend.stop()
        threading.Thread(target=maintain, daemon=True).start()
        def terminate(signum, frame):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        try:
            server.serve_forever()
        finally:
            with backend.lock:
                backend.stop()
                backend.snmp.stop()
            SOCKET.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
