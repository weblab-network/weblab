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
import console_evidence
import frr
import internet_cloud
import link_fabric
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
        self.internet_proposals = {}
        self.observed_revisions = {}
        self.console_factory = console_capture.Console

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

    def link_state(self, link_id):
        """A bounded read with an opaque guard for this cable's runtime and faults."""
        if not isinstance(link_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', link_id):
            raise self.error('Invalid link ID')
        with self.lab.lock:
            link = next((l for l in self.lab.topology['links'] if l['id'] == link_id), None)
            if link is None:
                raise self.error('Link does not exist')
            fabric = self.lab.fabric
            relayed = fabric is not None and link_id in fabric.links
            # IOL's background carrier monitor can report an error without the lab lock.
            for _ in range(3):
                serial = fabric.revision(link_id) if relayed else None
                state = self.lab.link_states()[link_id]
                if not relayed or serial == fabric.revision(link_id):
                    break
            else:
                raise self.error('Link state is changing; read it again before continuing')
            endpoints, runtime = {}, {}
            for side in ('a', 'b'):
                node = self.lab.node(link[side]['node'])
                record = self.lab.runtime.get(node['id'], {})
                endpoints[side] = {**link[side], 'name': node['name'], 'type': node['type'],
                                   'running': bool(record)}
                control_socket = (self.lab.netl1 / ('L1' + str(node['iol_id'])) if record.get('iol_l1_identity') else
                                  Path(record['qemu_sockets']) / 'qmp' if record.get('qemu_sockets') else None)
                runtime[side] = {'record': record,
                    'alive': {k: self.lab.alive(v) for k, v in record.items()
                              if isinstance(v, dict) and 'pid' in v and 'stamp' in v},
                    'control_socket': link_fabric.identity(control_socket) if control_socket else None,
                    'socket': link_fabric.identity(self.lab.netio / str(node.get('iol_id', '')))}
            relay = ({'serial': serial, 'error': fabric.error,
                      'alive': bool(fabric.thread and fabric.thread.is_alive()),
                      'socket': link_fabric.identity(self.lab.netio / str(fabric.links[link_id]['id_number']))}
                     if relayed else None)
            state['available'] = bool(state.get('available') and relay and relay['alive'] and not fabric.closed)
            # A detection countdown is not a new fault. It must not expire a human approval.
            guarded_state = {k: v for k, v in state.items() if not k.startswith('carrier_pending_')}
            token = revision({'link': link, 'runtime': runtime, 'relay': relay, 'state': guarded_state})
            return {'link_id': link_id, 'revision': self._observe_revision(self.lab.topology),
                    'link_revision': token, 'a': endpoints['a'], 'b': endpoints['b'], 'state': state,
                    'notice': 'Faults are runtime-only. Frame loss and carrier are independent. Carrier is a requested state; guest detection and protocol convergence require console or packet evidence.'}

    def check_link_action(self, operation, link_id, expected_revision, expected_link_revision, **action):
        """Preflight for approval and again under the lab lock at the point of use."""
        with self.lab.lock:
            self.require_execution_revision(expected_revision)
            current = self.link_state(link_id)
            if not isinstance(expected_link_revision, str) or current['link_revision'] != expected_link_revision:
                raise self.error('Link runtime or faults changed; read get_link_state and review again')
            if not current['state'].get('available'):
                raise self.error('Link fault controls are unavailable; start a connected device and read get_link_state again')
            if operation == 'link-frame-loss':
                if set(action) != {'blocked_a_to_b', 'blocked_b_to_a'} or any(type(v) is not bool for v in action.values()):
                    raise self.error('Frame loss requires boolean blocked_a_to_b and blocked_b_to_a')
            elif operation == 'link-carrier':
                if set(action) != {'side', 'up'} or action['side'] not in ('a', 'b') or type(action['up']) is not bool:
                    raise self.error('Carrier requires side a/b and boolean up')
                if not current['state'].get('carrier_supported_' + action['side']):
                    raise self.error('Cable control is not enabled/supported for this running endpoint. For tested IOL profiles enable cable unplug control in Inspector while stopped, then start it. Frame blocking is separate.')
            else:
                raise self.error('Unknown link operation')
            return current

    def link_frame_loss(self, link_id, blocked_a_to_b, blocked_b_to_a, expected_revision, expected_link_revision):
        action = {'blocked_a_to_b': blocked_a_to_b, 'blocked_b_to_a': blocked_b_to_a}
        with self.lab.lock:
            self.check_link_action('link-frame-loss', link_id, expected_revision, expected_link_revision, **action)
            self.lab.set_link_traffic(link_id, action)
            return {**self.link_state(link_id), 'applied': True, 'requested': action}

    def link_carrier(self, link_id, side, up, expected_revision, expected_link_revision):
        action = {'side': side, 'up': up}
        with self.lab.lock:
            self.check_link_action('link-carrier', link_id, expected_revision, expected_link_revision, **action)
            try:
                self.lab.set_link_carrier(link_id, action)
            except self.error as exc:
                # A failed hardware command can have taken effect. Never retry it implicitly.
                return {**self.link_state(link_id), 'applied': False, 'requested': action,
                        'outcome': 'uncertain', 'error': str(exc)[:1000]}
            return {**self.link_state(link_id), 'applied': True, 'requested': action}

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
        if self.lab.node(node_id)['type'] in ('cloud', 'network', 'group'):
            raise self.error('Clouds and diagram groups have no console')
        runtime = self.lab.runtime.get(node_id)
        if not runtime or 'console' not in runtime or not self.lab.alive(runtime['console']):
            raise self.error('Start this node before accessing its console')
        return self.console_factory(runtime['port'], cursor=cursor)

    def console_read(self, node_id, cursor=None, wait_seconds=1, max_bytes=65536, latest=False):
        self._console_options(node_id, cursor, wait_seconds, max_bytes)
        if type(latest) is not bool:
            raise self.error('latest must be a boolean')
        try:
            with self.lab.lock:
                console = self._console(node_id, cursor)
                current = self._observe_revision(self.lab.topology)
                node = copy.deepcopy(self.lab.node(node_id))
            try:
                result = console.window(wait_seconds, max_bytes, latest=latest and cursor is None)
                evidence = console_evidence.summarize(node, result)
                return {'node_id': node_id, 'revision': current, **result,
                        'evidence': evidence,
                        'next_action': console_evidence.next_action(result, evidence)}
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
                node = copy.deepcopy(self.lab.node(node_id))
                console.acquire()  # Acquires or borrows its login's lock; drains replay before ACK.
                if not console.stream or console.stream['gap'] or console.cursor != expected_cursor:
                    raise self.error('Console changed or history expired; no input was sent. '
                                     'Call get_console_output for this node with the last read cursor; '
                                     'if capture_end is limit, keep reading with each returned cursor. '
                                     'Inspect the latest prompt, then use the new revision/cursor. '
                                     'To inspect the current tail instead, omit cursor and set latest=true. '
                                     'Do not retry input using the rejected cursor.')
                # A lock can still be taken over by a human. The wrapper enforces
                # ownership when processing the binary input, including that race.
                attempted = True
                console.send(input.encode('utf-8'))
            result = console.window(wait_seconds, max_bytes, require_lock=True)
            uncertain = result['capture_end'] in ('lock_lost', 'disconnected')
            evidence = console_evidence.summarize(node, result)
            return {'node_id': node_id, 'revision': expected_revision,
                    'input_status': 'unknown' if uncertain else 'sent', **result,
                    'next_action': ('Call get_console_output for this node before the next input, '
                                    'even if this send captured a prompt. Use its new revision/cursor. '
                                    + console_evidence.next_action(result, evidence)),
                    'command_completion': 'unverified',
                    'evidence': evidence,
                    'notice': ('Console access ended during input; delivery is uncertain. Read the console before retrying.' if uncertain else
                               'Input was sent to the shared console, not acknowledged as executed. Temporary Agent access ends with this call; a borrowed human lock stays held. Read subsequent output before retrying; timeout does not cancel a guest command.')}
        except (ValueError, OSError) as exc:
            if attempted:
                return {'node_id': node_id, 'input_status': 'unknown', 'error': str(exc),
                        'command_completion': 'unverified',
                        'notice': 'Input may have reached the device. Read the console before retrying.'}
            raise self.error(str(exc)) from exc
        finally:
            if console is not None:
                console.close()

    def console_bulk_options(self, targets, input, wait_seconds=1, max_bytes=65536):
        if not isinstance(targets, list) or not 1 <= len(targets) <= 8:
            raise self.error('Select 1–8 explicit console targets')
        seen = set()
        for target in targets:
            if not isinstance(target, dict) or set(target) != {'node_id', 'expected_cursor'}:
                raise self.error('Each target needs only node_id and expected_cursor from its own fresh read')
            self._console_options(target['node_id'], target['expected_cursor'], wait_seconds, max_bytes)
            if target['expected_cursor'] is None:
                raise self.error('Read every console first and provide its expected_cursor')
            if target['node_id'] in seen:
                raise self.error('Duplicate console target')
            seen.add(target['node_id'])
        if wait_seconds > 5:
            raise self.error('Bulk wait_seconds must be between 0.1 and 5 per node')
        if not isinstance(input, str) or not input.endswith('\r'):
            raise self.error('Bulk input must contain plain commands, each terminated by Enter')
        commands = input[:-1].split('\r')
        if (not 1 <= len(commands) <= 32 or len(input.encode('utf-8')) > 4096 or
                any(not command.strip() or any(ord(c) < 32 or 127 <= ord(c) < 160 or
                    c in '\u2028\u2029' for c in command) or
                    any(escape in command for escape in ('\\r', '\\n')) for command in commands)):
            raise self.error('Bulk input requires 1–32 nonempty plain commands, at most 4096 UTF-8 bytes; no control keys or Enter escapes')

    def console_bulk_profile(self, nodes, captures):
        profiles = [console_evidence.bulk_prompt(node, capture) for node, capture in zip(nodes, captures)]
        for node, profile in zip(nodes, profiles):
            if profile is None:
                raise self.error(node['id'] + ': bulk commands require a recognized empty top-level prompt; '
                                 'inspect/prepare this console individually, then read it again')
        if len(set(profiles)) != 1:
            raise self.error('Bulk targets must use the same device family and prompt mode; '
                             'separate PCs, vendors and privilege levels')
        return profiles[0]

    def console_send_bulk(self, targets, input, expected_revision, wait_seconds=1, max_bytes=65536):
        """Preflight every console, then paste once per node with explicit outcomes.

        max_bytes is the TOTAL raw capture budget, divided across targets.
        All initial cursor/prompt/lock checks finish before the first write.
        Changes after that may produce partial delivery; never retry or roll back.
        """
        self.console_bulk_options(targets, input, wait_seconds, max_bytes)
        consoles, captures, nodes, results = [], [], [], []
        budget = max(1, max_bytes // len(targets))
        try:
            for target in targets:
                # Reinspect only the tail preceding the supplied observation.
                # Starting at history offset zero can hit the scan limit forever
                # on long-lived consoles, even after the agent drained all output.
                epoch, offset = target['expected_cursor'].rsplit(':', 1)
                lookback = f'{epoch}:{max(0, int(offset) - 4096)}'
                with self.lab.lock:
                    self.require_execution_revision(expected_revision)
                    node = copy.deepcopy(self.lab.node(target['node_id']))
                    console = self._console(target['node_id'], lookback)
                consoles.append(console)
                nodes.append(node)
                capture = console.window(.1, 8192)
                if capture.get('cursor') != target['expected_cursor']:
                    raise self.error(node['id'] + ': Console changed; read every target again; no input was sent')
                captures.append(capture)
                console.acquire()
                if not console.stream or console.stream['gap'] or console.cursor != target['expected_cursor']:
                    raise self.error(node['id'] + ': Console changed while acquiring input; no input was sent')
            family, mode = self.console_bulk_profile(nodes, captures)
            for index, (target, node, console) in enumerate(zip(targets, nodes, consoles)):
                attempted = False
                try:
                    # Drain lock notifications/new output while earlier targets ran.
                    check = console.window(.1, 1, require_lock=True)
                    if (check['capture_end'] != 'timeout' or check.get('gap') or check.get('error') or
                            console.cursor != target['expected_cursor'] or not console.lock or not console.lock['mine']):
                        raise self.error('Console changed or input lock was lost; inspect it before further input')
                    # The Agent bridge supplies a per-call, server-owned cancellation
                    # check. This is deliberately absent from the public argument schema.
                    guard = getattr(self, '_console_bulk_guard', None)
                    if guard:
                        guard()
                    with self.lab.lock:
                        self.require_execution_revision(expected_revision)
                        attempted = True
                        console.send(input.encode('utf-8'))
                    capture = console.window(wait_seconds, budget, require_lock=True)
                    uncertain = capture['capture_end'] in ('lock_lost', 'disconnected')
                    results.append({'node_id': node['id'], 'input_status': 'unknown' if uncertain else 'sent', **capture})
                    if not uncertain:
                        continue
                except (ValueError, OSError, self.error) as exc:
                    results.append({'node_id': node['id'], 'input_status': 'unknown' if attempted else 'not_sent',
                                    'error': str(exc)[:500], 'cursor': target['expected_cursor'],
                                    'capture_end': 'limit' if attempted else 'not_started'})
                results.extend({'node_id': remaining['node_id'], 'input_status': 'not_sent',
                                'reason': 'Stopped after an earlier target changed, failed or had uncertain delivery'}
                               for remaining in targets[index + 1:])
                break
            # Captures retain all output/cursor accounting; strip only repetitive hints
            # so eight small results fit the integrated agent's tool-output budget.
            for result in results:
                result.pop('notice', None)
                result.pop('next_action', None)
            status = 'sent' if all(r['input_status'] == 'sent' for r in results) else 'partial'
            if all(r['input_status'] == 'not_sent' for r in results):
                status = 'not_sent'
            return {'input_status': status, 'revision': expected_revision, 'platform': family, 'prompt_mode': mode,
                    'results': results, 'capture_max_bytes_per_node': budget, 'command_completion': 'unverified',
                    'next_action': 'Read every target again before further input. Follow each limited capture cursor (or unread_cursor) '
                                   'until drained. Inspect all output; never replay the whole batch after partial or uncertain delivery.',
                    'notice': 'One paste per node; no intermediate command checks, automatic retries or rollback. '
                              'Delivery is not command success. Prompts are heuristic; use only known non-interactive sequences.'}
        except (ValueError, OSError) as exc:
            raise self.error(str(exc)) from exc
        finally:
            for console in consoles:
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
            if not self.lab.container_only:
                entries += [{'name': internet_cloud.IMAGE, 'type': 'cloud'}]
            entries += [{'name': '', 'type': 'network'}, {'name': '', 'type': 'group'}]
            entries += [{'name': n, 'type': 'pc'} for n in alpines or ['alpine:latest']]
            profiles = []
            for entry in entries:
                node = self.lab.validate({'name': 'Profile', 'nodes': [{
                    'id': 'profile_' + secrets.token_hex(8), 'name': 'Device',
                    'image': entry['name'], 'type': entry['type']}], 'links': []})['nodes'][0]
                if node['type'] in ('network', 'group'):
                    family, maximum = 'diagram', 0
                elif node['type'] == 'cloud':
                    family, maximum = 'internet', 1
                elif node['type'] == 'pc':
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
                container = family in ('alpine', 'frr', 'll2s', 'internet')
                profiles.append({
                    'image': node['image'], 'type': node['type'], 'family': family,
                    'image_present': ((os.environ.get('WL_INTERNET_IMAGE', internet_cloud.IMAGE) if family == 'internet' else node['image']) in installed if installed is not None else None) if container else True,
                    'defaults': {k: node[k] for k in ('memory', 'ethernet')},
                    'ports_at_default': self.ports(node), 'ports_at_maximum': self.ports(max_node),
                    'ethernet_maximum': maximum,
                    'ethernet_meaning': 'no interfaces or forwarding; network clouds have a shared drawing anchor' if family == 'diagram' else 'four-port slots' if family == 'iol' else 'total interfaces (includes management on EXOS, vEOS and Junos)',
                    'startup_config': family in ('iol', 'iosv', 'frr', 'll2s'),
                    'kvm_required': vios.is_qemu(node),
                    'requirements': ('Diagram-only object. No image, runtime, console or forwarding. Groups have no connections.' if family == 'diagram' else 'Install the configured WL_INTERNET_IMAGE on the Docker host: pull the matching published helper or build packaging/internet/Dockerfile. IPv4 NAT/firewall/port forwards; settings instead of a console. See docs/internet.md.' if family == 'internet' else f'Install {node["image"]} on the Docker host and load openvswitch. Commit, then write memory before Stop.' if family == 'll2s' else
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
                'host': {'architecture': platform.machine(), 'container_only': self.lab.container_only, 'logical_cpus': os.cpu_count(),
                         'kvm_accessible': os.access('/dev/kvm', os.R_OK | os.W_OK), **memory},
                'notice': 'Catalog presence is not boot or protocol validation. Container digest checks, firmware, licenses and QEMU disk checks still run on Start. Host figures may exceed container limits.'}

    def _expire(self):
        now = time.monotonic()
        self.proposals = {key: value for key, value in self.proposals.items() if value['expires'] > now}
        self.internet_proposals = {key: value for key, value in self.internet_proposals.items() if value['expires'] > now}

    def _internet_node(self, node_id):
        node = self.lab.node(node_id)
        if not internet_cloud.is_cloud(node):
            raise self.error('Only Internet nodes support gateway operations')
        return node

    def internet_runtime_revision(self, node_id):
        runtime = self.lab.runtime.get(node_id)
        if runtime is None:
            return 'stopped'
        # A fresh runtime object invalidates approvals even if settings did not change.
        return hashlib.sha256(f'{id(runtime)}:{runtime.get("cloud_container_id", "")}'.encode()).hexdigest()

    def internet_proposal(self, proposal_id):
        self._expire()
        if not isinstance(proposal_id, str) or proposal_id not in self.internet_proposals:
            raise self.error('Internet proposal not found or expired; preview again')
        return copy.deepcopy(self.internet_proposals[proposal_id])

    def internet_settings(self, node_id, route_offset=0, rule_offset=0, forward_offset=0,
                          page_size=4, expected_revision=None, proposal_id=None):
        with self.lab.lock:
            node = self._internet_node(node_id)
            for offset in (route_offset, rule_offset, forward_offset):
                if type(offset) is not int or offset < 0:
                    raise self.error('Settings offsets must be nonnegative integers')
            if type(page_size) is not int or not 1 <= page_size <= 8:
                raise self.error('page_size must be between 1 and 8')
            current = self._observe_revision(self.lab.topology)
            if expected_revision is not None and expected_revision != current:
                raise self.error('Workspace changed between settings pages; read from the first page again')
            config = internet_cloud.settings(node['internet'])
            if proposal_id is not None:
                proposal = self.internet_proposal(proposal_id)
                if proposal['node_id'] != node_id:
                    raise self.error('Internet proposal targets a different node')
                config = proposal['after']
            counts, offsets, continuation = {}, {}, {}
            arrays = [('routes', config['routes'], route_offset),
                      ('rules', config['firewall']['rules'], rule_offset),
                      ('port_forwards', config['port_forwards'], forward_offset)]
            for key, values, offset in arrays:
                if offset > len(values):
                    raise self.error('Settings offset is past the end; read from the first page again')
                counts[key], offsets[key] = len(values), offset
                end = min(offset + page_size, len(values))
                continuation[key] = end if end < len(values) else None
                values[:] = values[offset:end]
            return {'node_id': node_id, 'name': node['name'], 'revision': current,
                    'runtime_revision': self.internet_runtime_revision(node_id),
                    'running': node_id in self.lab.runtime, 'proposal_id': proposal_id,
                    'internet': config, 'totals': counts, 'offsets': offsets, 'next_offsets': continuation,
                    'complete': all(v is None for v in continuation.values()) and not any(offsets.values()),
                    'notice': 'Follow next_offsets, keeping exhausted offsets at their totals and the same revision. '
                              'These are saved or proposed settings, not a connectivity test.'}

    def preview_internet(self, node_id, internet, expected_revision):
        with self.lab.lock:
            self.require_execution_revision(expected_revision)
            node = self._internet_node(node_id)
            self._expire()
            if sum(p['applied_revision'] is None for p in self.internet_proposals.values()) >= MAX_PROPOSALS:
                raise self.error('Too many Internet proposals; wait for expiry')
            while len(self.internet_proposals) >= 128:
                completed = next(key for key, value in self.internet_proposals.items() if value['applied_revision'])
                del self.internet_proposals[completed]
            before = internet_cloud.settings(node['internet'])
            after = internet_cloud.settings(internet)
            if before.get('ssh') != after.get('ssh'):
                raise self.error('SSH access must be configured by an administrator in the browser')
            internet_cloud.check_bindings(after if n['id'] == node_id else n['internet']
                for n in self.lab.topology['nodes'] if internet_cloud.is_cloud(n))
            network_changed = any(before[k] != after[k] for k in ('address', 'routes', 'uplink_subnet', 'port_forwards'))
            if node_id in self.lab.runtime and network_changed:
                raise self.error('Stop only this Internet node before changing address, return routes, uplink or port forwards; then read and preview again')
            ident = secrets.token_hex(16)
            self.internet_proposals[ident] = {
                'node_id': node_id, 'node': copy.deepcopy(node), 'before': before, 'after': after,
                'expires': time.monotonic() + PROPOSAL_TTL,
                'fingerprint': execution_revision(self.lab.topology),
                'runtime_revision': self.internet_runtime_revision(node_id), 'applied_revision': None}
            return {'proposal_id': ident, 'node_id': node_id, 'expires_in_seconds': PROPOSAL_TTL,
                    'live_firewall_update': node_id in self.lab.runtime,
                    'summary': {'address': after['address'], 'routes': len(after['routes']),
                                'firewall_rules': len(after['firewall']['rules']),
                                'default_action': after['firewall']['default_action'],
                                'port_forwards': len(after['port_forwards'])},
                    'notice': 'No settings changed. Read the proposed settings with get_internet_settings(proposal_id=...) '
                              'and review host bindings before applying. Apply does not restart any node.'}

    def apply_internet(self, proposal_id):
        with self.lab.lock:
            proposal = self.internet_proposal(proposal_id)
            node_id = proposal['node_id']
            self._internet_node(node_id)
            if proposal['applied_revision']:
                self.require_execution_revision(proposal['applied_revision'])
                return {'node_id': node_id, 'applied': True, 'revision': self._observe_revision(self.lab.topology)}
            if execution_revision(self.lab.topology) != proposal['fingerprint']:
                raise self.error('Workspace changed since Internet preview; read and preview again')
            if self.internet_runtime_revision(node_id) != proposal['runtime_revision']:
                raise self.error('Gateway runtime changed since preview; read and preview again')
            self.lab.update_internet(node_id, {'internet': proposal['after'], 'expected_internet': proposal['before']})
            current = self._observe_revision(self.lab.topology)
            self.internet_proposals[proposal_id]['applied_revision'] = current
            return {'node_id': node_id, 'applied': True, 'revision': current,
                    'notice': 'Settings applied and saved. No nodes restarted; verify runtime/traffic separately.'}

    def stop_internet(self, node_id, expected_revision, expected_runtime_revision):
        with self.lab.lock:
            self.require_execution_revision(expected_revision)
            self._internet_node(node_id)
            if self.internet_runtime_revision(node_id) != expected_runtime_revision:
                raise self.error('Gateway runtime changed; read its settings and review before stopping')
            self.lab.stop(node_id, dependents=False)
            return {'node_id': node_id, 'stopped': True, 'revision': self._observe_revision(self.lab.topology),
                    'runtime_revision': 'stopped', 'notice': 'Only this Internet node was stopped. Other devices remain running.'}

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
            keys(n, ('id', 'name', 'type', 'image', 'x', 'y', 'memory', 'ethernet', 'ipv4', 'gateway', 'startup_config', 'iol_l1', 'internet', 'size', 'color', 'width', 'height', 'notes', 'label_position', 'move_contents'), f'nodes[{index}]')
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
                    'start_order': [n['id'] for n in sorted((n for n in p['topology']['nodes'] if n['type'] not in ('network', 'group')), key=lambda n: 0 if n['type'] == 'cloud' else 2 if n['type'] == 'pc' else 1)],
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

    def start_internet(self, node_id, expected_revision):
        with self.lab.lock:
            self._internet_node(node_id)
            return self.start_node(node_id, expected_revision)
