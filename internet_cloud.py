"""Internet gateway lifecycle. Firewall/NAT live only in the helper namespace."""
import ipaddress
import json
import os
import re
import sys
import time

IMAGE = 'weblab-internet:dev'
LABEL = 'network.weblab.internet'
FIREWALL_LABEL = LABEL + '.firewall'
LIVE_FIREWALL_LABEL = FIREWALL_LABEL + '.live'
PORT_FORWARD_LABEL = LABEL + '.port-forwards'
MAX_FIREWALL_RULES = 64
MAX_PORT_FORWARDS = 32
FORWARD_PORT_BASE = 40000


def firewall_settings(raw):
    if not isinstance(raw, dict) or set(raw) - {'default_action', 'rules'}:
        raise ValueError('Firewall settings require default_action and rules')
    default = raw.get('default_action', 'allow')
    if default not in ('allow', 'deny'):
        raise ValueError('Firewall default action must be allow or deny')
    rules = raw.get('rules', [])
    if not isinstance(rules, list) or len(rules) > MAX_FIREWALL_RULES:
        raise ValueError(f'Firewall supports at most {MAX_FIREWALL_RULES} rules')
    result = {'default_action': default, 'rules': []}
    fields = {'action', 'protocol', 'source', 'destination', 'source_port', 'destination_port'}
    for number, rule in enumerate(rules, 1):
        try:
            if not isinstance(rule, dict) or set(rule) - fields:
                raise ValueError('Use action, protocol, source, destination, source_port and destination_port')
            action, protocol = rule.get('action'), rule.get('protocol')
            if action not in ('allow', 'deny') or protocol not in ('any', 'tcp', 'udp', 'icmp'):
                raise ValueError('Choose allow/deny and any, tcp, udp or icmp')
            normalized = {'action': action, 'protocol': protocol}
            for field in ('source', 'destination'):
                value = rule.get(field, '')
                if not isinstance(value, str):
                    raise ValueError(f'{field} must be an IPv4 address or network, or blank for any')
                normalized[field] = str(ipaddress.IPv4Network(value)) if value else ''
            for field in ('source_port', 'destination_port'):
                value = rule.get(field, '')
                if not isinstance(value, str) or (value and not re.fullmatch(r'[0-9]{1,5}(?:-[0-9]{1,5})?', value)):
                    raise ValueError(f'{field} must be a port or inclusive range such as 1024-65535')
                if value and protocol not in ('tcp', 'udp'):
                    raise ValueError('Ports require TCP or UDP')
                ports = [int(p) for p in value.split('-')] if value else []
                if ports and not 1 <= ports[0] <= ports[-1] <= 65535:
                    raise ValueError('Ports must be within 1–65535 and ranges must be ascending')
                normalized[field] = '-'.join(map(str, ports))
            result['rules'].append(normalized)
        except (ValueError, TypeError) as exc:
            raise ValueError(f'Firewall rule {number}: {exc}') from exc
    return result


def is_cloud(node):
    return node.get('type') == 'cloud'


def port_number(value):
    if (type(value) is not int and (not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,5}', value))) or not 1 <= int(value) <= 65535:
        raise ValueError('Use a single port between 1 and 65535')
    return int(value)


def check_bindings(configs):
    bindings = {}
    for config in configs:
        for forward in config.get('port_forwards', []):
            key = (forward['protocol'], forward['external_port'])
            address = forward['bind_address']
            used = bindings.setdefault(key, set())
            if address in used or '0.0.0.0' in used or (address == '0.0.0.0' and used):
                raise ValueError(f'Port forward conflicts with another binding: {address}:{key[1]}/{key[0]}')
            used.add(address)


def port_forward_settings(raw, config):
    if not isinstance(raw, list) or len(raw) > MAX_PORT_FORWARDS:
        raise ValueError(f'Internet gateway supports at most {MAX_PORT_FORWARDS} port forwards')
    gateway = ipaddress.IPv4Interface(config['address'])
    prefixes = [gateway.network, *[ipaddress.IPv4Network(r['destination']) for r in config['routes']]]
    result = []
    for number, item in enumerate(raw, 1):
        try:
            if not isinstance(item, dict) or set(item) - {'protocol','bind_address','external_port','target_address','target_port','source'}:
                raise ValueError('Use protocol, bind_address, external_port, target_address, target_port and source')
            if item.get('protocol') not in ('tcp', 'udp'):
                raise ValueError('Port forwards support TCP or UDP')
            if not isinstance(item.get('bind_address', '127.0.0.1'), str) or not isinstance(item.get('target_address'), str):
                raise ValueError('Bind and target addresses must be IPv4 address strings')
            bind = ipaddress.IPv4Address(item.get('bind_address', '127.0.0.1'))
            if bind.is_multicast or bind.is_link_local or int(bind) == 0xffffffff or (bind.is_loopback and str(bind) != '127.0.0.1'):
                raise ValueError('Bind to 127.0.0.1, a host IPv4 address or 0.0.0.0')
            target = ipaddress.IPv4Address(item['target_address'])
            if (target == gateway.ip or int(target) == 0xffffffff or target.is_unspecified or target.is_loopback or target.is_multicast or target.is_link_local or
                    not any(target in prefix and (prefix.prefixlen >= 31 or target not in (
                        prefix.network_address, prefix.broadcast_address)) for prefix in prefixes)):
                raise ValueError('Target must be a usable node IPv4 address on the cloud subnet or a configured return network')
            source = item.get('source', '')
            if not isinstance(source, str):
                raise ValueError('Allowed source must be an IPv4 host/network or blank for any')
            source = str(ipaddress.IPv4Network(source)) if source else ''
            if bind.is_loopback and source:
                raise ValueError('Leave allowed source blank for a localhost binding; use it with a LAN binding')
            result.append({'protocol':item['protocol'], 'bind_address':str(bind),
                'external_port':port_number(item['external_port']), 'target_address':str(target),
                'target_port':port_number(item['target_port']), 'source':source})
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f'Port forward {number}: {exc}') from exc
    check_bindings([{'port_forwards':result}])
    return result


def settings(raw):
    if not isinstance(raw, dict) or set(raw) - {'address', 'routes', 'uplink_subnet', 'firewall', 'port_forwards'}:
        raise ValueError('Internet settings accept address, routes, uplink_subnet, firewall and port_forwards')
    if not isinstance(raw.get('address', ''), str) or not isinstance(raw.get('uplink_subnet', ''), str):
        raise ValueError('Cloud address and uplink subnet must be strings')
    address = ipaddress.IPv4Interface(raw.get('address', '192.0.2.1/24'))
    if not 8 <= address.network.prefixlen <= 30 or address.ip in (
            address.network.network_address, address.network.broadcast_address) or address.ip.is_multicast or address.ip.is_loopback or address.ip.is_unspecified or address.ip.is_link_local:
        raise ValueError('Cloud address must be a usable IPv4 host with prefix /8–/30')
    routes = raw.get('routes', [])
    if not isinstance(routes, list) or len(routes) > 32:
        raise ValueError('Cloud supports at most 32 return routes')
    result = {'address': str(address), 'routes': [], 'uplink_subnet': '',
              'firewall': firewall_settings(raw.get('firewall', {}))}
    destinations = set()
    for route in routes:
        if not isinstance(route, dict) or set(route) != {'destination', 'via'}:
            raise ValueError('Each return route requires destination and via')
        if not all(isinstance(route[k], str) for k in ('destination', 'via')):
            raise ValueError('Return destination and next hop must be strings')
        dest = ipaddress.IPv4Network(route['destination'])
        via = ipaddress.IPv4Address(route['via'])
        if (dest.prefixlen == 0 or dest.overlaps(address.network) or str(dest) in destinations or
                via not in address.network or via in (address.ip, address.network.network_address, address.network.broadcast_address)):
            raise ValueError('Use unique non-default return networks and a next hop on the cloud subnet')
        destinations.add(str(dest))
        result['routes'].append({'destination': str(dest), 'via': str(via)})
    if raw.get('uplink_subnet'):
        uplink = ipaddress.IPv4Network(raw['uplink_subnet'])
        if not 16 <= uplink.prefixlen <= 28:
            raise ValueError('Docker uplink subnet must have prefix /16–/28')
        check_uplink(result, uplink)
        result['uplink_subnet'] = str(uplink)
    result['port_forwards'] = port_forward_settings(raw.get('port_forwards', []), result)
    return result


def check_uplink(config, network):
    for prefix in [str(ipaddress.IPv4Interface(config['address']).network), *[r['destination'] for r in config['routes']]]:
        if network.overlaps(ipaddress.IPv4Network(prefix)):
            raise ValueError('Docker uplink overlaps a lab network; set a different uplink_subnet in cloud settings')


def pc_address(cloud):
    address = ipaddress.IPv4Interface(cloud['internet']['address'])
    host = next(ip for ip in address.network.hosts() if ip != address.ip)
    return f'{host}/{address.network.prefixlen}', str(address.ip)


def start(lab, node, run, error, root):
    config = settings(node['internet'])
    image = os.environ.get('WL_INTERNET_IMAGE', IMAGE)
    try:
        metadata = json.loads(run('docker', 'image', 'inspect', image))[0]
        if (metadata.get('Config', {}).get('Labels') or {}).get(LABEL) != '1':
            raise ValueError('missing gateway image label')
    except (error, ValueError, KeyError, IndexError) as exc:
        if image != IMAGE:
            raise error(f'Install the configured Internet helper image on the Docker host: {image}. See docs/internet.md.') from exc
        raise error('Build the Internet gateway image first: docker build -f packaging/internet/Dockerfile -t weblab-internet:dev .') from exc
    if (metadata.get('Config', {}).get('Labels') or {}).get(PORT_FORWARD_LABEL) != '1':
        if config['port_forwards']:
            raise error('Rebuild the Internet gateway helper for port forwarding: '
                        'docker build -f packaging/internet/Dockerfile -t weblab-internet:dev .')
        config.pop('port_forwards')
    if (metadata.get('Config', {}).get('Labels') or {}).get(FIREWALL_LABEL) != '1':
        if config['firewall']['rules'] or config['firewall']['default_action'] != 'allow':
            raise error('Rebuild the Internet gateway image for firewall controls: '
                        'docker build -f packaging/internet/Dockerfile -t weblab-internet:dev .')
        # Older helpers reject unknown settings. Preserve their original allow policy.
        config.pop('firewall')
    runtime = lab.runtime[node['id']]
    stem = f'wl-cloud-{lab.owner}-{node["iol_id"]}'
    runtime.update(cloud=True, container=stem, cloud_label=f'{lab.owner}:{node["id"]}',
                   cloud_wan=stem+'-wan', cloud_lan=stem+'-lan', cloud_tap=f"ic{lab.owner}{node['iol_id']:x}")
    lab.journal()
    label = f'{LABEL}={runtime["cloud_label"]}'
    def mark(flag):
        runtime[flag] = True
        lab.journal()
    run('docker', 'network', 'create', '--driver', 'bridge', '--label', label,
        *(('--subnet', config['uplink_subnet']) if config['uplink_subnet'] else ()), runtime['cloud_wan'])
    mark('cloud_wan_created')
    wan = json.loads(run('docker', 'network', 'inspect', runtime['cloud_wan']))[0]
    try:
        for subnet in wan['IPAM']['Config']:
            check_uplink(config, ipaddress.IPv4Network(subnet['Subnet']))
    except ValueError as exc:
        raise error(str(exc)) from exc
    run('ip', 'tuntap', 'add', 'dev', runtime['cloud_tap'], 'mode', 'tap')
    mark('cloud_tap_created')
    runtime['cloud_tap_index'] = json.loads(run('ip', '-j', 'link', 'show', runtime['cloud_tap']))[0]['ifindex']
    lab.journal()
    run('ip', 'link', 'set', runtime['cloud_tap'], 'up')
    # Provisioning IP only; the helper replaces it with the requested lab address.
    subnet = f"198.18.{node['iol_id']//64}.{node['iol_id']%64*4}/30"
    run('docker', 'network', 'create', '-d', 'macvlan', '--internal', '--subnet', subnet,
        '--label', label, '-o', f'parent={runtime["cloud_tap"]}', runtime['cloud_lan'])
    mark('cloud_lan_created')
    config['provisioning_subnet'] = subnet
    cid = run('docker', 'create', '--name', stem, '--label', label,
        '--network', runtime['cloud_wan'], '--cap-drop', 'ALL', '--cap-add', 'NET_ADMIN',
        '--cap-add', 'NET_RAW', '--cap-add', 'NET_BIND_SERVICE',
        '--cap-add', 'SETUID', '--cap-add', 'SETGID', '--read-only',
        '--security-opt', 'no-new-privileges', '--memory', f'{node["memory"]}m', '--pids-limit', '32',
        '--tmpfs', '/run:rw,nosuid,nodev,size=8m',
        '--sysctl', 'net.ipv4.ip_forward=1', '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1',
        '--sysctl', 'net.ipv6.conf.default.disable_ipv6=1',
        '--env', 'WL_INTERNET_CONFIG=' + json.dumps(config),
        *[arg for index, forward in enumerate(config.get('port_forwards', [])) for arg in (
            '--publish', f'{forward["bind_address"]}:{forward["external_port"]}:{FORWARD_PORT_BASE+index}/{forward["protocol"]}')],
        metadata['Id'])
    runtime['cloud_container_id'] = cid
    mark('cloud_container_created')
    run('docker', 'network', 'connect', runtime['cloud_lan'], cid)
    run('docker', 'start', cid)
    deadline = time.monotonic() + 20
    while True:
        try:
            run('docker', 'exec', cid, 'test', '-f', '/run/ready', timeout=3)
            break
        except error:
            if time.monotonic() >= deadline:
                raise error('Internet gateway did not become ready: ' + run('docker', 'logs', '--tail', '30', cid))
            if run('docker', 'inspect', '--format', '{{.State.Running}}', cid) != 'true':
                raise error('Internet gateway failed: ' + run('docker', 'logs', '--tail', '30', cid))
            time.sleep(.2)
    config_path = lab.node_dir(node['id']) / 'internet-network.json'
    peer = lab.fabric.peers.get((node['id'], 'eth0'))
    config_path.write_text(json.dumps({'id': node['iol_id'], 'netio': str(lab.netio),
        'taps': [runtime['cloud_tap']], 'routes': {0: peer} if peer else {}}))
    lab.spawn(node['id'], 'bridge', [sys.executable, str(root/'tap_net.py'), str(config_path)], lab.node_dir(node['id']))
    lab.spawn(node['id'], 'gateway', ['docker', 'logs', '--follow', '--tail', '30', cid], lab.node_dir(node['id']))
    deadline = time.monotonic() + 5
    while not (lab.netio / str(node['iol_id'])).exists():
        if not lab.alive(runtime['bridge']) or time.monotonic() >= deadline:
            raise error('Internet cable adapter failed; inspect launcher logs')
        time.sleep(.05)


def firewall_container(lab, node, run, error):
    runtime = lab.runtime[node['id']]
    cid = runtime.get('cloud_container_id')
    if not runtime.get('cloud') or not cid:
        raise error('Internet gateway runtime is unavailable; stop and start this Internet node')
    metadata = json.loads(run('docker', 'container', 'inspect', cid))[0]
    labels = metadata.get('Config', {}).get('Labels') or {}
    if labels.get(LABEL) != f'{lab.owner}:{node["id"]}':
        raise error('Internet resource ownership changed; refusing firewall update')
    if not metadata.get('State', {}).get('Running'):
        raise error('Internet gateway is not running; stop and start this Internet node')
    if labels.get(LIVE_FIREWALL_LABEL) != '1':
        raise error('Rebuild the Internet gateway helper for live firewall updates, then stop/start '
                    'only this Internet node: docker build -f packaging/internet/Dockerfile '
                    '-t weblab-internet:dev .')
    return cid


def update_firewall(cid, policy, run):
    run('docker', 'exec', cid, 'python3', '/opt/weblab/gateway.py', 'firewall',
        json.dumps(firewall_settings(policy)), timeout=12)


def cleanup(lab, runtime, run, error):
    # Never remove another owner's replacement resources during recovery.
    for flag, kind, name in (
        ('cloud_container_created', 'container', runtime.get('cloud_container_id')),
        ('cloud_lan_created', 'network', runtime.get('cloud_lan')),
        ('cloud_wan_created', 'network', runtime.get('cloud_wan')),
    ):
        if not runtime.get(flag):
            continue
        try:
            value = json.loads(run('docker', kind, 'inspect', name))[0]
        except error as exc:
            if not any(s in str(exc).lower() for s in ('no such', 'not found')):
                raise
        else:
            labels = value.get('Config', {}).get('Labels') if kind == 'container' else value.get('Labels')
            if (labels or {}).get(LABEL) != runtime['cloud_label']:
                raise error('Internet resource ownership changed; refusing cleanup')
            run('docker', 'rm', '-f', name) if kind == 'container' else run('docker', 'network', 'rm', name)
        runtime.pop(flag)
        lab.journal()
    if runtime.get('cloud_tap_created'):
        try:
            link = json.loads(run('ip', '-j', 'link', 'show', runtime['cloud_tap']))[0]
        except error as exc:
            if not any(s in str(exc).lower() for s in ('does not exist', 'cannot find')):
                raise
        else:
            if not runtime.get('cloud_tap_index') or link['ifindex'] != runtime['cloud_tap_index']:
                raise error('Internet TAP identity changed; refusing cleanup')
            run('ip', 'link', 'delete', runtime['cloud_tap'])
        runtime.pop('cloud_tap_created')
        lab.journal()
