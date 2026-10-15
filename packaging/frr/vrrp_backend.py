"""Provision IPv4 VRRP macvlans from FRR's native runtime configuration.

FRR owns elections, protodown and persistent protocol intent. This reconciler
owns only its recorded macvlan identities and IPv4 addresses. Failed reads of
vrrpd never mean configuration removal. No shell or host network access.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

import vlan_backend

OWNERS = Path('/run/frr/weblab-vrrp-links.json')
LIMIT = 64


def identity(parent, vrid):
    if not re.fullmatch(r'eth[0-7]', parent):
        vlan_backend.parse(parent)
    if type(vrid) is not int or not 1 <= vrid <= 255:
        raise ValueError('VRRP ID must be 1-255')
    name = 'wv' + parent[3:].replace('.', 'v') + 'g' + str(vrid)
    return name, f'00:00:5e:00:01:{vrid:02x}'


def read_groups():
    result = subprocess.run(['vtysh', '-d', 'vrrpd', '-c', 'show vrrp json'],
                            capture_output=True, text=True, timeout=3)
    if result.returncode or len(result.stdout) > 1024*1024:
        raise RuntimeError('VRRP daemon unavailable; existing interfaces retained')
    groups = json.loads(result.stdout)
    if not isinstance(groups, list) or len(groups) > LIMIT:
        raise ValueError('Expected at most 64 VRRP groups')
    seen = set()
    for group in groups:
        if (not isinstance(group, dict) or not isinstance(group.get('interface'), str)
                or type(group.get('vrid')) is not int
                or not isinstance(group.get('v4'), dict)
                or not isinstance(group['v4'].get('addresses'), list)
                or not all(isinstance(a, str) for a in group['v4']['addresses'])):
            raise ValueError('Incomplete VRRP state; existing interfaces retained')
        key = (group['interface'], group['vrid'])
        if key in seen:
            raise ValueError('Duplicate VRRP state; existing interfaces retained')
        seen.add(key)
    return groups


def addresses(group, parent):
    """Derive prefix length from a real parent address on the VIP's subnet."""
    result = []
    vips = group['v4']['addresses']
    if not isinstance(vips, list) or len(vips) > 16:
        raise ValueError('At most 16 IPv4 VIPs per group')
    real = [ipaddress.IPv4Interface(f"{a['local']}/{a['prefixlen']}")
            for a in parent.get('addr_info', []) if a.get('family') == 'inet']
    for value in vips:
        vip = ipaddress.IPv4Address(value)
        if vip.is_unspecified or vip.is_multicast or vip.is_loopback or vip.is_link_local:
            raise ValueError('VIP must be a unicast IPv4 address')
        matches = [a for a in real if vip in a.network and a.ip != vip and
                   a.network.prefixlen <= 30 and vip not in (a.network.network_address, a.network.broadcast_address)]
        if any(a.ip == vip for a in real) or not matches:
            raise ValueError(f'{vip}: configure a different real parent IPv4 address on its subnet first')
        prefix = max(a.network.prefixlen for a in matches)
        result.append(f'{vip}/{prefix}')
    return sorted(set(result))



class Vrrp:
    def __init__(self, path=OWNERS):
        self.path = path
        self.owners = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(self.owners, dict) or len(self.owners) > LIMIT:
            raise ValueError('Invalid VRRP ownership ledger')
        for name, record in self.owners.items():
            if (set(record) != {'parent', 'vrid', 'index', 'parent_index', 'initialized'} or
                    identity(record['parent'], record['vrid'])[0] != name or
                    type(record['initialized']) is not bool or
                    any(type(record[k]) is not int or record[k] < 1 for k in ('index', 'parent_index'))):
                raise ValueError('Invalid VRRP ownership identity')
        self.status = 'waiting for VRRP daemon'
        self.rows = []

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(self.owners, stream)
        temporary.replace(self.path)

    def snapshot(self):
        return {p['ifname']: p for p in json.loads(vlan_backend.ip('-d', '-j', 'address', 'show'))}

    def check(self, name, links, record):
        link = links.get(name)
        if not link:
            return
        parent = links.get(record['parent'], {})
        _, mac = identity(record['parent'], record['vrid'])
        info = link.get('linkinfo', {})
        if (link['ifindex'] != record['index'] or parent.get('ifindex') != record['parent_index'] or
                link.get('link') != record['parent'] or link.get('address') != mac or
                info.get('info_kind') != 'macvlan' or info.get('info_data', {}).get('mode') != 'bridge' or
                link.get('master') or parent.get('master')):
            raise ValueError(f'{name}: foreign or changed VRRP interface; left untouched')

    def apply(self):
        groups = read_groups()  # Do not delete anything if the daemon cannot be read.
        links = self.snapshot() if groups or self.owners else {}
        desired, rows = {}, []
        for group in groups:
            parent, vrid = group['interface'], group['vrid']
            try:
                if group.get('autoconfigured'):
                    raise ValueError('autoconfiguration remains manually managed')
                name, mac = identity(parent, vrid)
                if group.get('v6', {}).get('addresses'):
                    rows.append(f'{parent} VRID {vrid}: IPv6 provisioning is not supported')
                if not group['v4']['addresses']:
                    continue
                # Withdraw owned interfaces when prerequisites become unavailable.
                desired[name] = None
                link = links.get(parent)
                if not link or link.get('master'):
                    raise ValueError('waiting for parent in default VRF')
                if '.' in parent:
                    physical, vid = vlan_backend.parse(parent)
                    info = link.get('linkinfo', {})
                    if (info.get('info_kind') != 'vlan' or info.get('info_data', {}).get('id') != vid or
                            link.get('link') != physical):
                        raise ValueError('parent is not the expected VLAN interface')
                vip_addresses = addresses(group, link)
                attached = group['v4'].get('interface', 'None')
                if attached not in ('None', '', name):
                    raise ValueError(f'already uses manually managed interface {attached}')
                if name in links and name not in self.owners:
                    raise ValueError(f'{name} exists outside this integration')
                # Do not introduce a second virtual MAC if an existing manual
                # macvlan has not yet been noticed by vrrpd.
                if any(n != name and p.get('link') == parent and p.get('address') == mac
                       for n, p in links.items()):
                    raise ValueError('virtual MAC already belongs to another interface')
                desired[name] = (parent, vrid, mac, vip_addresses)
            except (ValueError, KeyError, TypeError) as error:
                rows.append(f'{parent} VRID {vrid}: {error}')
        for name, record in list(self.owners.items()):
            try:
                self.check(name, links, record)
                if name not in desired or desired[name] is None:
                    if name in links:
                        self.check(name, self.snapshot(), record)
                        vlan_backend.ip('link', 'delete', 'dev', name)
                    del self.owners[name]
                    self.save()
            except ValueError as error:
                rows.append(str(error))
        for name, spec in desired.items():
            if spec is None:
                continue
            parent, vrid, mac, wanted = spec
            try:
                if name not in links:
                    # Add without a VIP. Set protodown before assigning addresses;
                    # FRR alone subsequently controls Master/Backup availability.
                    vlan_backend.ip('link', 'add', 'link', parent, 'name', name,
                                    'address', mac, 'type', 'macvlan', 'mode', 'bridge')
                    new = self.snapshot()[name]
                    self.owners[name] = {'parent': parent, 'vrid': vrid,
                                         'index': new['ifindex'], 'parent_index': links[parent]['ifindex'], 'initialized': False}
                    self.save()
                if not self.owners[name]['initialized']:
                    self.check(name, self.snapshot(), self.owners[name])
                    vlan_backend.ip('link', 'set', 'dev', name, 'protodown', 'on')
                    vlan_backend.ip('link', 'set', 'dev', name, 'up')
                    self.owners[name]['initialized'] = True
                    self.save()
                current = self.snapshot()
                self.check(name, current, self.owners[name])
                existing = {f"{a['local']}/{a['prefixlen']}" for a in current[name].get('addr_info', [])
                            if a.get('family') == 'inet'}
                for address in existing - set(wanted):
                    vlan_backend.ip('-4', 'address', 'delete', address, 'dev', name)
                for address in set(wanted) - existing:
                    vlan_backend.ip('-4', 'address', 'add', address, 'dev', name)
                rows.append(f'{parent} VRID {vrid}: {name} ready ({", ".join(wanted)})')
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
                rows.append(f'{name}: {error}')
        self.rows = rows
        self.status = 'IPv4 provisioning active' if groups else 'no VRRP groups configured'

    def reconcile(self):
        try:
            self.apply()
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            self.status = str(error)

    def show(self):
        return self.status + '\n' + ''.join(row + '\n' for row in self.rows) + 'Election state: show vrrp\n'
