"""Internet gateway lifecycle. Firewall/NAT live only in the helper namespace."""
import ipaddress
import json
import os
import sys
import time

IMAGE = 'weblab-internet:dev'
LABEL = 'network.weblab.internet'


def is_cloud(node):
    return node.get('type') == 'cloud'


def settings(raw):
    if not isinstance(raw, dict) or set(raw) - {'address', 'routes', 'uplink_subnet'}:
        raise ValueError('Internet settings require address, routes and optional uplink_subnet')
    if not isinstance(raw.get('address', ''), str) or not isinstance(raw.get('uplink_subnet', ''), str):
        raise ValueError('Cloud address and uplink subnet must be strings')
    address = ipaddress.IPv4Interface(raw.get('address', '192.0.2.1/24'))
    if not 8 <= address.network.prefixlen <= 30 or address.ip in (
            address.network.network_address, address.network.broadcast_address) or address.ip.is_multicast or address.ip.is_loopback or address.ip.is_unspecified or address.ip.is_link_local:
        raise ValueError('Cloud address must be a usable IPv4 host with prefix /8–/30')
    routes = raw.get('routes', [])
    if not isinstance(routes, list) or len(routes) > 32:
        raise ValueError('Cloud supports at most 32 return routes')
    result = {'address': str(address), 'routes': [], 'uplink_subnet': ''}
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
        raise error('Build the Internet gateway image first: docker build -f packaging/internet/Dockerfile -t weblab-internet:dev .') from exc
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
        '--env', 'WL_INTERNET_CONFIG=' + json.dumps(config), metadata['Id'])
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
