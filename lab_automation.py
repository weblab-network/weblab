"""Opt-in topology automation shared by MCP and future in-browser agents.

No model or SDK dependency lives in this module. Console input uses the existing
shared transport, never a separate host shell or Docker execution path.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import time

import console_capture
import frr
import ll2s_device
import vios

MAX_PROPOSALS = 8
PROPOSAL_TTL = 3600
MAX_INSTRUCTIONS = 65536


def revision(topology):
    return hashlib.sha256(json.dumps(topology, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def execution_revision(topology):
    """Coordinates do not change what a start or console operation targets."""
    value = copy.deepcopy(topology)
    for node in value['nodes']:
        node.pop('x', None)
        node.pop('y', None)
    return revision(value)


def portable(topology):
    result = copy.deepcopy(topology)
    for node in result['nodes']:
        node.pop('iol_id', None)
    return result


def resources(nodes):
    qemu = [n for n in nodes if vios.is_qemu(n)]
    return {
        'configured_memory_mb_excluding_pcs': sum(n['memory'] for n in nodes if n['type'] != 'pc'),
        'qemu_vcpus': sum(4 if vios.is_junos(n) else 2 if vios.is_veos(n) else 1 for n in qemu),
        'note': 'Configured guest memory excludes host overhead and Alpine PCs. Container limits and available capacity must be checked separately.',
    }


def switching_warnings(topology):
    """Physical switch graph only; VLANs and guest STP still need verification."""
    groups = {n['id']: n['id'] for n in topology['nodes'] if n['type'] == 'switch'}
    if len(groups) < 2:
        return []

    def root(node):
        while groups[node] != node:
            node = groups[node]
        return node

    cycle = False
    for link in topology['links']:
        a, b = link['a']['node'], link['b']['node']
        if a not in groups or b not in groups:
            continue
        a, b = root(a), root(b)
        if a == b:
            cycle = True
        else:
            groups[a] = b
    warnings = []
    count = len({root(node) for node in groups})
    if count > 1:
        warnings.append(f'Switches form {count} separate groups without direct switch-to-switch connectivity. Routed links do not join their Layer 2 domains.')
    if not cycle:
        warnings.append('No redundant switch-to-switch cycle: STP can run, but this physical graph cannot demonstrate STP blocking/failover between switches. For that exercise add redundant switch links with compatible VLANs.')
    return warnings


class Automation:
    def __init__(self, lab, error, ports, run):
        self.lab, self.error, self.ports, self.run = lab, error, ports, run
        self.proposals = {}
        self.observed_revisions = {}

    def _observe_revision(self, topology):
        # Called with the lab lock. Remember only fingerprints, not lab contents.
        current = revision(topology)
        self.observed_revisions.pop(current, None)
        self.observed_revisions[current] = execution_revision(topology)
        while len(self.observed_revisions) > 128:
            del self.observed_revisions[next(iter(self.observed_revisions))]
        return current

    def require_execution_revision(self, expected):
        # Caller holds the lab lock. Apply deliberately keeps its full revision
        # check: replacing a topology would otherwise overwrite layout edits.
        if isinstance(expected, str) and (expected == revision(self.lab.topology) or
                self.observed_revisions.get(expected) == execution_revision(self.lab.topology)):
            return
        raise self.error('Workspace changed; read current state/console and review before continuing')

    def state(self):
        with self.lab.lock:
            state = self.lab.snapshot()
            state['revision'] = self._observe_revision(state['topology'])
        state['guest_readiness'] = 'unverified'
        state['notice'] = 'Running means the launcher is alive, not that login, interfaces or protocols are ready. Use console evidence to verify readiness and connectivity.'
        return state

    def _console_options(self, node_id, cursor, wait_seconds, max_bytes):
        if not isinstance(node_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', node_id):
            raise self.error('Invalid node ID')
        if cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r'[a-z0-9-]{1,80}:\d{1,16}', cursor)):
            raise self.error('Invalid console cursor')
        if type(wait_seconds) not in (int, float) or not 0.1 <= wait_seconds <= 30:
            raise self.error('wait_seconds must be between 0.1 and 30')
        if type(max_bytes) is not int or not 1 <= max_bytes <= 262144:
            raise self.error('max_bytes must be between 1 and 262144')

    def _console(self, node_id, cursor):
        # Caller holds the lab lock until the wrapper connection is bound to the
        # current runtime. A restart later closes this socket, never redirects it.
        self.lab.node(node_id)
        runtime = self.lab.runtime.get(node_id)
        if not runtime or 'console' not in runtime or not self.lab.alive(runtime['console']):
            raise self.error('Start this node before accessing its console')
        return console_capture.Console(runtime['port'], cursor=cursor)

    def console_read(self, node_id, cursor=None, wait_seconds=1, max_bytes=65536):
        self._console_options(node_id, cursor, wait_seconds, max_bytes)
        try:
            with self.lab.lock:
                console = self._console(node_id, cursor)
                current = self._observe_revision(self.lab.topology)
            try:
                return {'node_id': node_id, 'revision': current,
                        **console.window(wait_seconds, max_bytes)}
            finally:
                console.close()
        except (ValueError, OSError) as exc:
            raise self.error(str(exc)) from exc

    def console_send(self, node_id, input, expected_revision, expected_cursor,
                     wait_seconds=1, max_bytes=65536):
        self._console_options(node_id, expected_cursor, wait_seconds, max_bytes)
        if expected_cursor is None:
            raise self.error('Read the console first and provide expected_cursor')
        if not isinstance(input, str) or not 1 <= len(input.encode('utf-8')) <= 4096:
            raise self.error('Console input must be 1–4096 UTF-8 bytes; include an explicit carriage return to submit a command')
        console = None
        attempted = False
        try:
            with self.lab.lock:
                self.require_execution_revision(expected_revision)
                console = self._console(node_id, expected_cursor)
                console.acquire()  # Never takes over a human lock; drains replay before ACK.
                if not console.stream or console.stream['gap'] or console.cursor != expected_cursor:
                    raise self.error('Console changed or history expired; read it again before sending input')
                # A lock can still be taken over by a human. The wrapper enforces
                # ownership when processing the binary input, including that race.
                attempted = True
                console.send(input.encode('utf-8'))
            result = console.window(wait_seconds, max_bytes, require_lock=True)
            return {'node_id': node_id, 'revision': expected_revision,
                    'input_status': 'sent', **result,
                    'notice': 'Input was sent to the shared console, not acknowledged as executed. The lock is released when this call ends. Read subsequent output before retrying; timeout does not cancel a guest command.'}
        except (ValueError, OSError) as exc:
            if attempted:
                return {'node_id': node_id, 'input_status': 'unknown', 'error': str(exc),
                        'notice': 'Input may have reached the device. Read the console before retrying.'}
            raise self.error(str(exc)) from exc
        finally:
            if console is not None:
                console.close()

    def catalog(self):
        # Docker is optional. Discovery never pulls images or creates containers.
        try:
            installed = set(self.run('docker', 'image', 'ls', '--format', '{{.Repository}}:{{.Tag}}', timeout=5).splitlines())
            docker_error = None
        except self.error as exc:
            installed, docker_error = None, str(exc)
        with self.lab.lock:
            entries = self.lab.catalog()
            alpines = sorted(n for n in (installed or []) if n == 'alpine' or n.startswith('alpine:') and '<' not in n)
            entries += [{'name': n, 'type': 'pc'} for n in alpines or ['alpine:latest']]
            profiles = []
            for entry in entries:
                node = self.lab.validate({'name': 'Profile', 'nodes': [{
                    'id': 'profile_' + secrets.token_hex(8), 'name': 'Device',
                    'image': entry['name'], 'type': entry['type']}], 'links': []})['nodes'][0]
                if node['type'] == 'pc':
                    family, maximum = 'alpine', 1
                elif ll2s_device.is_ll2s(node):
                    family, maximum = 'll2s', 8
                elif frr.is_frr(node):
                    family, maximum = 'frr', 8
                elif vios.is_exos(node):
                    family, maximum = 'exos', 13
                elif vios.is_veos(node):
                    family, maximum = 'veos', 16
                elif vios.is_junos(node):
                    family, maximum = 'vjunos-evolved' if vios.is_junos_evolved(node) else 'vjunos-switch', 16
                elif vios.is_qemu(node):
                    family, maximum = 'iosv', 16
                else:
                    family, maximum = 'iol', 8
                max_node = {**node, 'ethernet': maximum}
                container = family in ('alpine', 'frr', 'll2s')
                profiles.append({
                    'image': node['image'], 'type': node['type'], 'family': family,
                    'image_present': (node['image'] in installed if installed is not None else None) if container else True,
                    'defaults': {k: node[k] for k in ('memory', 'ethernet')},
                    'ports_at_default': self.ports(node), 'ports_at_maximum': self.ports(max_node),
                    'ethernet_maximum': maximum,
                    'ethernet_meaning': 'four-port slots' if family == 'iol' else 'total interfaces (includes management on EXOS, vEOS and Junos)',
                    'startup_config': family in ('iol', 'iosv', 'frr', 'll2s'),
                    'kvm_required': vios.is_qemu(node),
                    'requirements': (f'Install {node["image"]} on the Docker host and load openvswitch. Commit, then write memory before Stop.' if family == 'll2s' else
                                     'Bare-metal Intel KVM. Commit and request system power-off before Stop.' if family == 'vjunos-switch' else
                                     'OVMF UEFI. Commit and request system power-off before Stop.' if family == 'vjunos-evolved' else
                                     f'Companion {vios.ABOOT_IMAGE} required.' if family == 'veos' else ''),
                })
        memory = {}
        try:
            for line in Path('/proc/meminfo').read_text().splitlines():
                key, value = line.split(':', 1)
                if key in ('MemTotal', 'MemAvailable'):
                    memory[key + '_mb'] = int(value.split()[0]) // 1024
        except (OSError, ValueError):
            pass
        return {'profiles': profiles, 'docker_discovery_error': docker_error,
                'host': {'architecture': platform.machine(), 'logical_cpus': os.cpu_count(),
                         'kvm_accessible': os.access('/dev/kvm', os.R_OK | os.W_OK), **memory},
                'notice': 'Catalog presence is not boot or protocol validation. Container digest checks, firmware, licenses and QEMU disk checks still run on Start. Host figures may exceed container limits.'}

    def _expire(self):
        now = time.monotonic()
        self.proposals = {key: value for key, value in self.proposals.items() if value['expires'] > now}

    def _get(self, proposal_id):
        self._expire()
        if not isinstance(proposal_id, str) or proposal_id not in self.proposals:
            raise self.error('Proposal not found or expired; preview again')
        return self.proposals[proposal_id]

    def _fresh(self, topology):
        current_ids = {n['id'] for n in self.lab.topology['nodes']}
        for node in topology['nodes']:
            if node['id'] in current_ids or os.path.lexists(self.lab.directory / 'nodes' / node['id']):
                raise self.error(f"Use a fresh node ID: {node['id']} already belongs to the current topology or stored device data")

    def _shape(self, data):
        if not isinstance(data, dict):
            raise self.error('Expected a topology object')
        def keys(value, allowed, path):
            if not isinstance(value, dict):
                raise self.error(f'{path}: expected an object with fields {", ".join(allowed)}')
            extra = sorted(set(value) - set(allowed))
            if extra:
                names = ', '.join(repr(str(k)[:80]) for k in extra[:8])
                hint = ' Use a and b objects, each with node and port.' if 'endpoints' in extra else ''
                if 'iol_id' in extra:
                    hint += ' Omit internal iol_id values; the server assigns them.'
                raise self.error(f'{path}: unexpected fields {names}. Allowed fields: {", ".join(allowed)}.{hint}')
        keys(data, ('version', 'name', 'nodes', 'links'), 'topology')
        if data.get('version', 1) != 1:
            raise self.error('Only topology version 1 is supported')
        if not isinstance(data.get('nodes'), list) or not isinstance(data.get('links'), list):
            raise self.error('Expected nodes and links arrays')
        for index, n in enumerate(data['nodes']):
            keys(n, ('id', 'name', 'type', 'image', 'x', 'y', 'memory', 'ethernet', 'ipv4', 'gateway', 'startup_config', 'iol_l1'), f'nodes[{index}]')
        for index, link in enumerate(data['links']):
            keys(link, ('id', 'a', 'b'), f'links[{index}]')
            for side in ('a', 'b'):
                keys(link.get(side), ('node', 'port'), f'links[{index}].{side}')

    def _assign_ids(self, topology):
        """Resolve proposal-local aliases; only generated IDs become storage paths."""
        result = copy.deepcopy(topology)
        if len(result['nodes']) > 64 or len(result['links']) > 256:
            raise self.error('Limit: 64 nodes and 256 links per lab')
        occupied = {n['id'] for n in self.lab.topology['nodes']}
        occupied.update(l['id'] for l in self.lab.topology['links'])
        for proposal in self.proposals.values():
            occupied.update(n['id'] for n in proposal['topology']['nodes'])
            occupied.update(l['id'] for l in proposal['topology']['links'])

        def allocate(prefix):
            for _ in range(100):
                identifier = prefix + secrets.token_hex(12)
                if identifier not in occupied and not os.path.lexists(self.lab.directory / 'nodes' / identifier):
                    occupied.add(identifier)
                    return identifier
            raise self.error('Could not allocate a fresh ID; retry the preview')

        def alias(value, path):
            if (not isinstance(value, str) or not value.strip() or len(value) > 128 or
                    any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise self.error(f'{path}: use a nonempty text alias of at most 128 characters, such as r1 or sw1')
            return value

        mapping, indexes = {}, {}
        for index, node in enumerate(result['nodes']):
            local = alias(node.get('id', node.get('name')), f'nodes[{index}].id (or name when id is omitted)')
            if local in mapping:
                raise self.error(f'nodes[{index}]: duplicate alias {local!r}, already used by nodes[{indexes[local]}]; give each node a distinct alias')
            indexes[local] = index
            mapping[local] = node['id'] = allocate('n')
        for index, link in enumerate(result['links']):
            # Link IDs have no cross-references in a proposal; the server owns them.
            if 'id' in link:
                alias(link['id'], f'links[{index}].id')
            link['id'] = allocate('l')
            for side in ('a', 'b'):
                reference = link[side].get('node')
                if not isinstance(reference, str) or reference not in mapping:
                    raise self.error(f'links[{index}].{side}.node: unknown node alias {reference!r}; '
                                     f'use one of {list(mapping)!r} (case-sensitive)')
                link[side]['node'] = mapping[reference]
        return result, mapping

    def preview(self, topology, instructions, replace_existing=False):
        if type(replace_existing) is not bool:
            raise self.error('replace_existing must be a boolean')
        if not isinstance(instructions, str) or not instructions.strip() or len(instructions.encode()) > MAX_INSTRUCTIONS:
            raise self.error('Provide nonempty companion exercise Markdown, at most 64 KiB')
        if any(ord(c) < 32 and c not in '\n\r\t' for c in instructions):
            raise self.error('Instructions must be plain Markdown text')
        self._shape(topology)
        with self.lab.lock:
            self._expire()
            if len(self.proposals) >= MAX_PROPOSALS:
                raise self.error('Too many pending proposals; discard an old proposal or wait for expiry')
            existing = self.lab.topology['nodes']
            if existing and not replace_existing:
                raise self.error('Workspace is not empty. Preserve it or explicitly preview with replace_existing=true after discussing replacement with the user')
            assigned, node_id_map = self._assign_ids(topology)
            normalized = portable(self.lab.validate(assigned))
            self._fresh(normalized)
            warnings = ['Structural validation only; configuration syntax and protocol behavior have not been tested.']
            warnings.extend(switching_warnings(normalized))
            if existing:
                warnings.append('Apply replaces the current topology. Existing saved node directories are retained, but this is not a saved-state ZIP backup. Export your current lab first if needed.')
            if self.lab.runtime:
                warnings.append('Apply is blocked until every node is stopped. This tool never stops devices; shut Junos down gracefully first.')
            linked = {ep['node'] for l in normalized['links'] for ep in (l['a'], l['b'])}
            for n in normalized['nodes']:
                if n['type'] == 'pc' and n['id'] not in linked:
                    warnings.append(f"{n['name']}: PC is uncabled and cannot start until connected.")
                if vios.is_exos(n) or vios.is_veos(n) or vios.is_junos(n):
                    warnings.append(f"{n['name']}: configure manually through its console; startup snippets are unsupported.")
            proposal_id = secrets.token_hex(16)
            self.proposals[proposal_id] = {
                'expires': time.monotonic() + PROPOSAL_TTL,
                'base_revision': revision(self.lab.topology), 'topology': normalized,
                'instructions': instructions, 'warnings': warnings, 'node_id_map': node_id_map,
                'replaces_existing': bool(existing), 'applied_revision': None,
                'previous_topology': portable(self.lab.topology),
            }
            return self.proposal(proposal_id)

    def proposal(self, proposal_id):
        with self.lab.lock:
            p = self._get(proposal_id)
            return {'proposal_id': proposal_id, 'base_revision': p['base_revision'],
                    'applied_revision': p['applied_revision'], 'replaces_existing': p['replaces_existing'],
                    'expires_in_seconds': max(0, int(p['expires'] - time.monotonic())),
                    'topology': copy.deepcopy(p['topology']), 'instructions_markdown': p['instructions'],
                    'node_id_map': dict(p['node_id_map']),
                    'previous_topology': copy.deepcopy(p['previous_topology']),
                    'warnings': list(p['warnings']), 'resources': resources(p['topology']['nodes']),
                    'start_order': [n['id'] for n in sorted(p['topology']['nodes'], key=lambda n: n['type'] == 'pc')],
                    'downloads': {'topology': f'/api/automation/proposals/{proposal_id}/topology',
                                  'instructions': f'/api/automation/proposals/{proposal_id}/instructions'},
                    'notice': 'Node aliases were replaced with fresh IDs; use node_id_map or start_order for subsequent node tools. Show this proposal to the user before calling apply_topology. Downloads are temporary: save JSON and Markdown before expiry or server restart.'}

    def discard(self, proposal_id):
        with self.lab.lock:
            self._get(proposal_id)
            del self.proposals[proposal_id]
            return {'discarded': True}

    def apply(self, proposal_id):
        with self.lab.lock:
            p = self._get(proposal_id)
            current = revision(self.lab.topology)
            if p['applied_revision']:
                if p['applied_revision'] != current:
                    raise self.error('Workspace changed since Apply; inspect its current state')
                return self.state()  # A retry after a lost response never reapplies or resets.
            if current != p['base_revision']:
                raise self.error('Workspace changed since preview; preview again before applying')
            if self.lab.runtime:
                raise self.error('Stop all nodes before applying a topology; no devices were stopped')
            self._fresh(p['topology'])
            self.lab.save(copy.deepcopy(p['topology']))
            p['applied_revision'] = revision(self.lab.topology)
            return self.state()

    def start_node(self, node_id, expected_revision):
        with self.lab.lock:
            self.require_execution_revision(expected_revision)
            self.lab.node(node_id)
            try:
                self.lab.start(node_id)
            except self.error as exc:
                return {'started': False, 'node_id': node_id, 'error': str(exc), 'state': self.state()}
            return {'started': True, 'node_id': node_id, 'state': self.state()}
