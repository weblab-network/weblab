"""Opt-in read-only SNMPv2c service for the Weblab FRR extension.

Intent is collected by the shared VTY backend into integrated frr.conf.
Generated Net-SNMP files and process identities are runtime-only.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

DIRECTORY = Path('/run/weblab-snmp')
CHILD = Path('/run/frr/weblab-snmp-child.json')
AGENTX = str(DIRECTORY / 'agentx.sock')
SYSTEM_DESCRIPTION = 'Weblab FRR 10.7.1 - LLDP, SNMP, VLAN and IPv4 VRRP extensions (weblab.network)'


def defaults():
    return dict(enabled=False, community='', listen='', source='', contact='', location='')


def validate(state):
    if not isinstance(state, dict) or set(state) != set(defaults()):
        raise ValueError('Invalid SNMP settings')
    if type(state['enabled']) is not bool or any(not isinstance(state[k], str)
                                               for k in state if k != 'enabled'):
        raise ValueError('Invalid SNMP setting type')
    if state['community'] and not re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}', state['community']):
        raise ValueError('Community must be 1-64 letters, digits or _ . @ -')
    if state['listen']:
        address = ipaddress.IPv4Address(state['listen'])
        if address.is_unspecified or address.is_multicast or int(address) == 0xffffffff:
            raise ValueError('SNMP requires a specific unicast IPv4 listen address')
    if state['source']:
        ipaddress.IPv4Network(state['source'])
    for key in ('contact', 'location'):
        if len(state[key]) > 128 or any(ord(c) < 32 or ord(c) > 126 or c in '\\"#'
                                       for c in state[key]):
            raise ValueError('SNMP text must be at most 128 printable ASCII characters without quote, backslash or #')
    if state['enabled'] and not all(state[k] for k in ('community', 'listen', 'source')):
        raise ValueError('Configure SNMP community, listen address and source network before enabling')


def command(line, state):
    """Mutate a candidate dictionary; caller validates/applies it transactionally."""
    words = line.split()
    if words[:1] == ['no']:
        if len(words) != 3 or words[1] != 'snmp-server' or words[2] not in defaults():
            # The CLI spells the boolean field 'enable'.
            if words != ['no', 'snmp-server', 'enable']:
                raise ValueError('Unsupported SNMP command')
        key = 'enabled' if words[2] == 'enable' else words[2]
        state[key] = defaults()[key]
        if key in ('community', 'listen', 'source'):
            state['enabled'] = False
    elif words == ['snmp-server', 'enable']:
        state['enabled'] = True
    elif len(words) == 4 and words[:2] == ['snmp-server', 'community'] and words[3] == 'ro':
        state['community'] = words[2]
    elif len(words) == 3 and words[0] == 'snmp-server' and words[1] in ('listen', 'source'):
        state[words[1]] = str(ipaddress.IPv4Network(words[2], strict=False)) if words[1] == 'source' else words[2]
    elif len(words) >= 3 and words[0] == 'snmp-server' and words[1] in ('contact', 'location'):
        state[words[1]] = ' '.join(words[2:])
    else:
        raise ValueError('Unsupported SNMP command')


def config_lines(state):
    # Enable sorts before the other settings in vtysh's collected config.
    # The backend defers validation/application until replay finishes.
    lines = []
    for key in ('community', 'listen', 'source', 'contact', 'location'):
        if state[key]:
            lines.append(f'snmp-server {key} {state[key]}' + (' ro' if key == 'community' else ''))
    if state['enabled']:
        lines.append('snmp-server enable')
    return lines


def daemon_config(state, hostname):
    validate(state)
    # Explicit v2c VACM model: no SNMPv1 group, no writable view, no extra MIBs.
    lines = [f"agentaddress udp:{state['listen']}:161", f'sysName {hostname}',
             f'sysDescr {SYSTEM_DESCRIPTION}', 'sysServices 4',
             f"sysContact {state['contact'] or 'unset'}", f"sysLocation {state['location'] or 'unset'}",
             f"com2sec weblab {state['source']} {state['community']}",
             'group weblab_ro v2c weblab']
    for oid in ('.1.3.6.1.2.1.1', '.1.3.6.1.2.1.2', '.1.3.6.1.2.1.31', '.1.0.8802.1.1.2'):
        lines.append(f'view weblab_monitor included {oid}')
    lines += ['access weblab_ro "" v2c noauth exact weblab_monitor none none',
              'master agentx', f'agentXSocket {AGENTX}', 'agentXPerms 0600',
              'maxGetbulkRepeats 32', 'maxGetbulkResponses 256']
    return '\n'.join(lines) + '\n'


def address_present(address):
    result = subprocess.run(['ip', '-j', '-4', 'address', 'show'], capture_output=True,
                            text=True, check=True, timeout=2)
    return any(not entry.get('master') and
               (entry['ifname'] == 'lo' or re.fullmatch(r'eth[0-7](?:\.[1-9][0-9]{0,3})?', entry['ifname'])) and
               any(info.get('local') == address for info in entry.get('addr_info', []))
               for entry in json.loads(result.stdout))


class Snmp:
    def __init__(self, process_start):
        self.process_start = process_start
        self.process = None
        self.desired = None
        self.status = 'disabled'
        self.retry = 0

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=2)
            self.process = None
        CHILD.unlink(missing_ok=True)
        for name in ('agentx.sock', 'snmpd.conf'):
            (DIRECTORY / name).unlink(missing_ok=True)

    def apply(self, state, hostname):
        """Reconcile without failing routing/LLDP when an address isn't ready yet."""
        validate(state)
        desired = (dict(state), hostname)
        if desired != self.desired:
            self.stop()
            self.desired, self.retry = desired, 0
        if not state['enabled']:
            self.stop()
            self.status = 'disabled'
            return
        if time.monotonic() < self.retry:
            return
        try:
            if not address_present(state['listen']):
                self.stop()
                self.status = 'waiting for listen address on a default-VRF lab interface'
                return
            if self.process is not None and self.process.poll() is not None:
                self.stop()
            if self.process is None:
                DIRECTORY.mkdir(mode=0o700, exist_ok=True)
                DIRECTORY.chmod(0o700)
                path = DIRECTORY / 'snmpd.conf'
                path.touch(mode=0o600, exist_ok=True)
                path.chmod(0o600)
                path.write_text(daemon_config(state, hostname))
                persistent = DIRECTORY / 'state'
                persistent.mkdir(mode=0o700, exist_ok=True)
                self.process = subprocess.Popen(
                    ['snmpd', '-f', '-Lo', '-C', '-c', str(path)],
                    env={**os.environ, 'MIBS': '', 'SNMP_PERSISTENT_DIR': str(persistent)},
                    start_new_session=True)
                CHILD.write_text(json.dumps({'pid': self.process.pid,
                                             'started': self.process_start(self.process.pid)}))
                CHILD.chmod(0o600)
            self.status = 'running' if Path(AGENTX).exists() else 'starting'
        except (ValueError, OSError, subprocess.SubprocessError):
            # Do not return command arguments/configuration: they contain the community.
            self.stop()
            self.status = 'unavailable; retrying (routing and LLDP continue)'
            self.retry = time.monotonic() + 5

    def show(self, state):
        return (f'SNMPv2c read-only: {self.status}\n'
                f"Listen: {state['listen'] or '-'}:161/udp\n"
                f"Allowed source: {state['source'] or '-'}\n"
                'MIBs: system, interfaces, ifXTable; LLDP when discovery is enabled\n'
                f'System description: {SYSTEM_DESCRIPTION}\n')
