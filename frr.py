"""FRRouting container profile, tested-image policy and saved configuration."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time

IMAGE = 'quay.io/frrouting/frr:10.7.1'
LLDP_IMAGE = 'weblab-frr:10.7.1-lldp1'
SNMP_IMAGE = 'weblab-frr:10.7.1-lldp-snmp1'
VLAN_IMAGE = 'weblab-frr:10.7.1-lldp-snmp-vlan1'
VRRP_IMAGE = 'weblab-frr:10.7.1-lldp-snmp-vlan-vrrp1'
PUBLIC_IMAGE = 'ghcr.io/weblab-network/weblab-frr:10.7.1-wl1'
# Immutable Docker config identity; survives save/load and registry publication.
PUBLIC_IMAGE_ID = 'sha256:85775f0e1c09e8d92f7af0d0f8834cd92ddc8d8c60e5ebfbc9dc6d136562b423'
DIGEST = 'quay.io/frrouting/frr@sha256:e995beaa50fdc9edb35eadcfefa29b7f062cc06f2b812613789b68fa541554d2'
MAX_CONFIG = 1024 * 1024
DAEMONS = ('bgpd', 'ospfd', 'ospf6d', 'ripd', 'ripngd', 'isisd', 'bfdd', 'vrrpd')


def is_frr(node):
    return node.get('image', '').startswith('quay.io/frrouting/frr:') or node.get('image') in (LLDP_IMAGE, SNMP_IMAGE, VLAN_IMAGE, VRRP_IMAGE, PUBLIC_IMAGE)


def config_path(node, directory):
    return directory / ('frr-' + hashlib.sha256(node['image'].encode()).hexdigest()[:20]) / 'frr.conf'


def inspect_image(run, error, allow_untested=False, name=None):
    name = name or IMAGE
    if name not in (IMAGE, LLDP_IMAGE, SNMP_IMAGE, VLAN_IMAGE, VRRP_IMAGE, PUBLIC_IMAGE):
        raise error('Unsupported FRR image selector')
    try:
        image = json.loads(run('docker', 'image', 'inspect', name))[0]
    except (ValueError, IndexError, error) as exc:
        if name == PUBLIC_IMAGE:
            raise error(f'Install the Weblab FRR release image first: docker pull {PUBLIC_IMAGE}; or load its release archive') from exc
        if name in (LLDP_IMAGE, SNMP_IMAGE, VLAN_IMAGE, VRRP_IMAGE, PUBLIC_IMAGE):
            raise error(f'Build {name} first; see packaging/frr/README.md') from exc
        raise error(f'Pull the FRR image on the Docker host first: docker pull {IMAGE}') from exc
    if not isinstance(image, dict) or not re.fullmatch(r'sha256:[a-f0-9]{64}', str(image.get('Id', ''))):
        raise error('Docker returned an invalid FRR image ID')
    image['tested'] = ((name == IMAGE and DIGEST in (image.get('RepoDigests') or [])) or
                       (name == PUBLIC_IMAGE and image['Id'] == PUBLIC_IMAGE_ID))
    if name in (LLDP_IMAGE, SNMP_IMAGE, VLAN_IMAGE, VRRP_IMAGE, PUBLIC_IMAGE):
        labels = (image.get('Config') or {}).get('Labels') or {}
        if labels.get('network.weblab.frr.extension') != 'lldp-1':
            raise error('FRR LLDP image is missing its extension identity; rebuild it')
        if name in (SNMP_IMAGE, VLAN_IMAGE, VRRP_IMAGE, PUBLIC_IMAGE) and labels.get('network.weblab.frr.snmp') != 'v2c-1':
            raise error('FRR SNMP image is missing its extension identity; rebuild it')
        if name in (VLAN_IMAGE, VRRP_IMAGE, PUBLIC_IMAGE) and labels.get('network.weblab.frr.vlan') != '8021q-1':
            raise error('FRR VLAN image is missing its extension identity; rebuild it')
        if name in (VRRP_IMAGE, PUBLIC_IMAGE) and labels.get('network.weblab.frr.vrrp') != 'ipv4-1':
            raise error('FRR VRRP image is missing its extension identity; rebuild it')
    if not image['tested'] and not allow_untested:
        if name == PUBLIC_IMAGE:
            raise error('Weblab FRR image does not match the tested release ID. Pull the release image or load its archive; locally rebuilt images require WL_ALLOW_UNTESTED_FRR=1')
        if name in (LLDP_IMAGE, SNMP_IMAGE, VLAN_IMAGE, VRRP_IMAGE, PUBLIC_IMAGE):
            raise error('The experimental FRR extension image requires WL_ALLOW_UNTESTED_FRR=1 '
                        '(or --allow-untested-frr); see packaging/frr/README.md')
        raise error(f'FRR image does not match the tested digest; pull {DIGEST} and tag it {IMAGE}. '
                    'To explicitly allow a modified image, start Weblab with WL_ALLOW_UNTESTED_FRR=1 '
                    '(or --allow-untested-frr). Compatibility is not guaranteed.')
    return image


def check_image(run, error, allow_untested=False):
    image = inspect_image(run, error, allow_untested)
    return image['Id']


def archive_image(run, error, allow_untested=False, name=None):
    name = name or IMAGE
    image = inspect_image(run, error, allow_untested, name)
    # Locally committed images often have no registry digest. Record their
    # immutable image/config ID instead, never falsely label them as tested.
    return {'name': name, 'digest': DIGEST if name == IMAGE and image['tested'] else image['Id']}


def read_config(path):
    # Configuration only: never source restored shell scripts or daemon settings.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('FRR configuration must be a regular file')
        data = stream.read(MAX_CONFIG + 1)
    if len(data) > MAX_CONFIG or b'\0' in data:
        raise ValueError('Invalid or oversized FRR configuration (limit 1 MiB)')
    data.decode('utf-8')
    return data


def start(lab, node, run, error, root):
    image = inspect_image(run, error, lab.allow_untested_frr, node['image'])
    image_id = image['Id']
    node_id = node['id']
    runtime = lab.runtime[node_id]
    stem = f"wf-{lab.owner}-{node['iol_id']}"
    directory = lab.node_dir(node_id)
    config = config_path(node, directory)
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        hostname = re.sub(r'[^a-zA-Z0-9-]', '-', node['name']).strip('-') or 'router'
        config.write_text(node.get('startup_config') or f'frr defaults traditional\nhostname {hostname}\nservice integrated-vtysh-config\n!\n')
    read_config(config)
    hostname = re.sub(r'[^a-zA-Z0-9-]', '-', node['name']).strip('-') or 'router'
    runtime.update({'frr': True, 'container': stem, 'frr_ports': [],
                    'frr_image_id': image_id, 'frr_untested': not image['tested']})
    if not image['tested']:
        print(f'WARNING: node {node_id} is using untested FRR image {image_id}; '
              'compatibility is not guaranteed', file=sys.stderr, flush=True)
    lab.journal()
    # Every port has an isolated Docker macvlan over a TAP in Weblab's namespace.
    # No host PID namespace, host-path bind mounts, or Docker-managed uplink.
    for index in range(node['ethernet']):
        port = {'network': f'{stem}-{index}', 'tap': f"ft{lab.owner}{node['iol_id']:03x}{index:x}"}
        runtime['frr_ports'].append(port)
        lab.journal()
        run('ip', 'tuntap', 'add', 'dev', port['tap'], 'mode', 'tap')
        port['tap_created'] = True
        lab.journal()
        run('ip', 'link', 'set', 'dev', port['tap'], 'up')
        number = node['iol_id'] * 8 + index
        subnet = f'198.19.{number // 64}.{number % 64 * 4}/30'
        subnet6 = f'fd42:{lab.owner[:4]}:{lab.owner[4:]}:{number:x}::/64'
        run('docker', 'network', 'create', '-d', 'macvlan', '--internal', '--subnet', subnet,
            '--ipv6', '--subnet', subnet6,
            '--label', f'iol.lab={lab.owner}', '-o', f"parent={port['tap']}",
            # VRRP must receive frames addressed to virtual MACs too.
            *(('-o', 'macvlan_mode=passthru') if node['image'] in (VRRP_IMAGE, PUBLIC_IMAGE) else ()),
            port['network'])
        port['network_created'] = True
        lab.journal()
    run('docker', 'run', '-d', '--name', stem, '--label', f'iol.lab={lab.owner}',
        '--hostname', hostname, '--network', runtime['frr_ports'][0]['network'], '--dns', '127.0.0.1',
        '--cap-add', 'NET_ADMIN', '--cap-add', 'NET_RAW',
        '--cap-add', 'SYS_ADMIN', '--memory', f"{node['memory']}m", '--pids-limit', '256',
        '--sysctl', 'net.ipv4.ip_forward=1', '--sysctl', 'net.ipv6.conf.all.forwarding=1',
        '--sysctl', 'net.ipv4.conf.all.rp_filter=0', '--sysctl', 'net.ipv4.conf.default.rp_filter=0',
        # Keep VIP ARP replies on the virtual interface and ignore Backup routes.
        *(('--sysctl', 'net.ipv4.conf.all.arp_ignore=1',
           '--sysctl', 'net.ipv4.conf.all.arp_announce=2',
           '--sysctl', 'net.ipv4.conf.all.ignore_routes_with_linkdown=1')
          if node['image'] in (VRRP_IMAGE, PUBLIC_IMAGE) else ()),
        # Docker enables IPv6 on provisioned ports, but leaves its default off
        # for new links. Routed VLAN children need it before Zebra adds addresses.
        *(('--sysctl', 'net.ipv6.conf.default.disable_ipv6=0')
          if node['image'] in (VLAN_IMAGE, VRRP_IMAGE, PUBLIC_IMAGE) else ()),
        '--entrypoint', '/bin/sh', image_id, '-c',
        'while [ ! -e /run/weblab-ready ]; do sleep 0.1; done; exec /usr/lib/frr/docker-start')
    runtime['container_created'] = True
    lab.journal()
    for port in runtime['frr_ports'][1:]:
        run('docker', 'network', 'connect', port['network'], stem)
    info = json.loads(run('docker', 'inspect', stem))[0]['NetworkSettings']['Networks']
    interfaces = json.loads(run('docker', 'exec', stem, 'ip', '-j', 'link', 'show'))
    by_mac = {i['address'].lower(): i['ifname'] for i in interfaces if 'address' in i}
    # Docker's interface numbering can vary; rename by network endpoint MAC.
    for index, port in enumerate(runtime['frr_ports']):
        name = by_mac[info[port['network']]['MacAddress'].lower()]
        run('docker', 'exec', stem, 'ip', 'link', 'set', name, 'down')
        run('docker', 'exec', stem, 'ip', 'link', 'set', name, 'name', f'wl{index}')
    for index in range(node['ethernet']):
        name = f'eth{index}'
        if node['image'] in (VRRP_IMAGE, PUBLIC_IMAGE):
            # A private one-port bridge prevents Linux from flattening nested
            # macvlans into Docker's passthru endpoint. Each ethN remains its
            # own isolated cable; no bridge ever joins different lab ports.
            wire = f'wp{index}'
            mac = info[runtime['frr_ports'][index]['network']]['MacAddress']
            run('docker', 'exec', stem, 'ip', 'link', 'set', f'wl{index}', 'name', wire)
            run('docker', 'exec', stem, 'ip', 'addr', 'flush', 'dev', wire)
            run('docker', 'exec', stem, 'ip', 'link', 'set', wire, 'addrgenmode', 'none')
            run('docker', 'exec', stem, 'ip', 'link', 'add', 'name', name, 'address', mac,
                'type', 'bridge', 'stp_state', '0', 'mcast_snooping', '0',
                'group_fwd_mask', '16384')  # Deliver LLDP to the ethN endpoint.
            run('docker', 'exec', stem, 'ip', 'link', 'set', wire, 'master', name)
            run('docker', 'exec', stem, 'ip', 'link', 'set', wire, 'up')
        else:
            run('docker', 'exec', stem, 'ip', 'link', 'set', f'wl{index}', 'name', name)
        run('docker', 'exec', stem, 'ip', 'addr', 'flush', 'dev', name)
        run('docker', 'exec', stem, 'ip', 'link', 'set', name, 'up')
    run('docker', 'exec', stem, 'ip', '-4', 'route', 'flush', 'default')
    run('docker', 'exec', stem, 'ip', '-6', 'route', 'flush', 'default')
    run('docker', 'cp', str(config), f'{stem}:/etc/frr/frr.conf')
    run('docker', 'exec', stem, 'chown', 'frr:frr', '/etc/frr/frr.conf')
    run('docker', 'exec', stem, '/bin/sh', '-c',
        'printf "%s\\n" "service integrated-vtysh-config" > /etc/frr/vtysh.conf; '
        'chown frr:frrvty /etc/frr/vtysh.conf')
    # Edit the image's own daemon settings, never take shell code from a backup.
    for daemon in DAEMONS:
        run('docker', 'exec', stem, 'sed', '-i', f's/^{daemon}=no$/{daemon}=yes/', '/etc/frr/daemons')
    routes = {index: lab.fabric.peers[(node_id, f'eth{index}')]
              for index in range(node['ethernet']) if (node_id, f'eth{index}') in lab.fabric.peers}
    bridge_config = directory / 'frr-network.json'
    bridge_config.write_text(json.dumps({'id': node['iol_id'], 'netio': str(lab.netio),
                                       'taps': [p['tap'] for p in runtime['frr_ports']], 'routes': routes}))
    lab.spawn(node_id, 'bridge', [shutil.which('python3'), str(root / 'tap_net.py'), str(bridge_config)], directory)
    runtime['frr_configured'] = True
    lab.journal()
    run('docker', 'exec', stem, 'touch', '/run/weblab-ready')
    _wait_ready(stem, run, error)
    return sys.executable, [str(root / 'container_console.py'), '--frr', stem]


def _wait_ready(stem, run, error):
    # Slow hosts get more time; successful readiness still returns immediately.
    deadline = time.monotonic() + 120
    while True:
        try:
            # A reachable vtysh socket is not sufficient: watchfrr can still be
            # applying the baseline and overwrite a user's first CLI changes.
            output = run('docker', 'logs', '--tail', '100', stem, timeout=5)
            if 'all daemons up, doing startup-complete notify' in output:
                run('docker', 'exec', stem, 'vtysh', '-c', 'show version', timeout=5)
                break
        except error:
            pass
        if time.monotonic() >= deadline:
            raise error('FRR daemons did not become ready: ' + run('docker', 'logs', '--tail', '30', stem))
        time.sleep(.25)


def save(lab, node_id, run):
    runtime = lab.runtime[node_id]
    if not runtime.get('frr_configured') or not runtime.get('container_created'):
        return
    # copy only the last "write memory" result; never save running config implicitly.
    target = config_path(lab.node(node_id), lab.node_dir(node_id))
    with tempfile.TemporaryDirectory(prefix='.frr-save-', dir=lab.directory) as temp:
        copy = Path(temp) / 'frr.conf'
        run('docker', 'cp', f"{runtime['container']}:/etc/frr/frr.conf", str(copy))
        data = read_config(copy)
        temporary = target.with_suffix('.tmp')
        with temporary.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
        fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def cleanup_ports(lab, runtime, run, error):
    errors = []
    for port in reversed(runtime.get('frr_ports', [])):
        for flag, command in (('network_created', ['docker', 'network', 'rm', port['network']]),
                              ('tap_created', ['ip', 'link', 'delete', port['tap']])):
            if port.get(flag):
                try:
                    run(*command)
                except error as exc:
                    if not any(s in str(exc) for s in ('not found', 'Cannot find device', 'does not exist')):
                        errors.append(str(exc))
                        continue
                port.pop(flag)
                lab.journal()
    if errors:
        raise error('FRR port cleanup failed; retry Stop: ' + '; '.join(errors))
