"""Bounded, container-local routed 802.1Q interface management.

Only explicitly created interfaces are owned. The runtime ledger records kernel
ifindices (including the parent), so a replacement or foreign link is not deleted.
Interface addresses, shutdown and routing policy remain Zebra's responsibility.
"""
import json
import os
from pathlib import Path
import re
import subprocess

OWNERS = Path('/run/frr/weblab-vlan-links.json')
LIMIT = 64


def parse(name):
    match = re.fullmatch(r'(eth[0-7])\.([1-9][0-9]{0,3})', name)
    if not match or not 1 <= int(match[2]) <= 4094:
        raise ValueError('Use eth0.VID through eth7.VID, with VLAN ID 1-4094')
    return match[1], int(match[2])


def validate(names):
    if not isinstance(names, list) or len(names) > LIMIT or any(not isinstance(n, str) for n in names):
        raise ValueError('At most 64 routed VLAN interfaces are supported')
    if len(set(names)) != len(names):
        raise ValueError('Duplicate VLAN interface')
    for name in names:
        parse(name)


def ip(*args):
    result = subprocess.run(['ip', *args], capture_output=True, text=True, timeout=3)
    if result.returncode:
        raise RuntimeError('VLAN link operation failed: ' + result.stderr.strip()[:256])
    return result.stdout


class Vlans:
    def __init__(self, path=OWNERS):
        self.path = path
        self.owners = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(self.owners, dict):
            raise ValueError('Invalid VLAN ownership ledger')
        validate(list(self.owners))
        for identity in self.owners.values():
            if (not isinstance(identity, list) or len(identity) != 2 or
                    any(type(i) is not int or i < 1 for i in identity)):
                raise ValueError('Invalid VLAN ownership identity')

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(self.owners, stream)
        temporary.replace(self.path)

    def snapshot(self):
        return {link['ifname']: link for link in json.loads(ip('-d', '-j', 'link', 'show'))}

    def check(self, name, links):
        parent, vid = parse(name)
        link = links.get(name)
        if not link:
            return
        info = link.get('linkinfo', {})
        data = info.get('info_data', {})
        parent_link = links.get(parent, {})
        if (self.owners.get(name) != [link['ifindex'], parent_link.get('ifindex')] or
                info.get('info_kind') != 'vlan' or data.get('id') != vid or
                data.get('protocol') != '802.1Q' or link.get('link') != parent or
                link.get('master') or parent_link.get('master')):
            raise ValueError(f'VLAN {name} conflicts with an unowned or changed interface')

    def apply(self, names):
        validate(names)
        if not names and not self.owners:
            return
        links = self.snapshot()
        # Preflight everything before any destructive action.
        for name in set(names) | set(self.owners):
            self.check(name, links)
        for name in names:
            parent, _ = parse(name)
            if parent not in links or links[parent].get('master'):
                raise ValueError(f'VLAN parent {parent} must exist in the default VRF')
        for name in list(self.owners):
            if name not in names:
                if name in links:
                    # Refresh identity immediately before deletion.
                    self.check(name, self.snapshot())
                    ip('link', 'delete', 'dev', name)
                del self.owners[name]
                self.save()
        for name in names:
            if name in links:
                continue  # Never undo shutdown or other FRR interface settings.
            parent, vid = parse(name)
            # Creation is deliberately DOWN. Zebra applies configured shutdown,
            # addresses and routing when it learns the link through Netlink.
            ip('link', 'add', 'link', parent, 'name', name, 'type', 'vlan', 'id', str(vid))
            created = self.snapshot()[name]
            self.owners[name] = [created['ifindex'], links[parent]['ifindex']]
            self.save()

    def show(self, names):
        links = self.snapshot()
        lines = ['Interface        Parent   VLAN   State']
        for name in sorted(names):
            parent, vid = parse(name)
            link = links.get(name)
            status = 'missing' if link is None else ('up' if 'UP' in link['flags'] else 'down')
            try:
                self.check(name, links)
            except ValueError:
                status = 'conflict'
            lines.append(f'{name:<16} {parent:<8} {vid:<6} {status}')
        return '\n'.join(lines) + '\n' if names else 'No routed VLAN interfaces configured\n'
