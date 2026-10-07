"""LL2S switch container lifecycle and saved startup configuration."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid

IMAGE = 'ghcr.io/weblab-network/ll2s:0.2.0'
LEGACY_IMAGE = 'll2s:dev'
IMAGES = (IMAGE, LEGACY_IMAGE)
MAX_CONFIG = 1024 * 1024


def is_ll2s(node):
    return node.get('image', '').startswith(('ll2s:', 'ghcr.io/weblab-network/ll2s:'))


def config_path(node, directory):
    return directory / ('ll2s-' + hashlib.sha256(node['image'].encode()).hexdigest()[:20]) / 'startup.conf'


def identity_path(config):
    return config.with_name('startup-image.json')


def atomic_write(path, data):
    # Configurations may contain SNMP communities. Keep host copies private too.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.ll2s-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)


def write_identity(config, data, image_id):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', str(image_id)):
        raise ValueError('Invalid saved LL2S image ID')
    record = {'version': 1, 'image_id': image_id, 'config_sha256': hashlib.sha256(data).hexdigest()}
    atomic_write(identity_path(config), (json.dumps(record) + '\n').encode())


def saved_image(config, name=IMAGE):
    data = read_config(config)
    try:
        record = json.loads(read_config(identity_path(config)))
    except FileNotFoundError as exc:
        raise ValueError('LL2S saved image identity is unknown. Start with the intended image, '
                         'verify the configuration, then Stop before ZIP export; '
                         'JSON + saved configs remains available.') from exc
    if (not isinstance(record, dict) or set(record) != {'version', 'image_id', 'config_sha256'} or
            type(record['version']) is not int or record['version'] != 1 or
            not re.fullmatch(r'sha256:[a-f0-9]{64}', str(record['image_id'])) or
            record['config_sha256'] != hashlib.sha256(data).hexdigest()):
        raise ValueError('LL2S saved image identity does not match the configuration; '
                         'retry Stop if running, otherwise verify and start/stop with the intended image.')
    return {'name': name, 'digest': record['image_id']}


def export_image(lab, run, error, name=IMAGE):
    identities = set()
    current = None
    for node in lab.topology['nodes']:
        if not is_ll2s(node) or node['image'] != name:
            continue
        config = config_path(node, lab.node_dir(node['id']))
        if os.path.lexists(config):
            identity = saved_image(config, name)
        else:
            if os.path.lexists(identity_path(config)):
                raise ValueError('LL2S image identity exists without its saved configuration')
            # Never-started nodes have no saved state to attribute to an old image.
            if current is None:
                current = archive_image(run, error, name)
            identity = current
        identities.add(identity['digest'])
    if len(identities) != 1:
        raise ValueError('LL2S nodes require different saved images. Start and verify all switches '
                         'with the intended image, then Stop before exporting one ZIP.')
    return {'name': name, 'digest': identities.pop()}


def inspect_image(run, error, name=IMAGE):
    if name not in IMAGES:
        raise error("Unsupported LL2S image")
    try:
        image = json.loads(run('docker', 'image', 'inspect', name))[0]
    except (ValueError, IndexError, error) as exc:
        command = f'docker build -t {name} /path/to/ll2s' if name == LEGACY_IMAGE else f'docker pull {name}'
        raise error('Install the LL2S image on the Docker host first: ' + command) from exc
    if not isinstance(image, dict) or not re.fullmatch(r'sha256:[a-f0-9]{64}', str(image.get('Id', ''))):
        raise error('Docker returned an invalid LL2S image ID')
    return image


def archive_image(run, error, name=IMAGE):
    # Development tags are mutable; archive restore requires the exact content.
    return {'name': name, 'digest': inspect_image(run, error, name)['Id']}


def baseline(node):
    hostname = re.sub(r'[^a-zA-Z0-9-]', '-', node['name']).strip('-') or 'switch'
    text = f'hostname {hostname}\nspanning-tree mode rstp\nvlan 1\n'
    for index in range(node['ethernet']):
        text += f'interface eth{index}\n switchport mode access\n switchport access vlan 1\n no shutdown\nexit\n'
    return text


def read_config(path):
    # Configuration only: never source restored shell scripts or daemon settings.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('LL2S configuration must be a regular file')
        data = stream.read(MAX_CONFIG + 1)
    if len(data) > MAX_CONFIG or b'\0' in data:
        raise ValueError('Invalid or oversized LL2S configuration (limit 1 MiB)')
    data.decode('utf-8')
    return data


def start(lab, node, run, error, root):
    if not Path('/sys/module/openvswitch').exists():
        raise error('Load Open vSwitch on the Docker host first: sudo modprobe openvswitch')
    image = inspect_image(run, error, node['image'])
    image_id = image['Id']
    node_id = node['id']
    runtime = lab.runtime[node_id]
    stem = f"wl-{lab.owner}-{node['iol_id']}"
    directory = lab.node_dir(node_id)
    config = config_path(node, directory)
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        atomic_write(config, (node.get('startup_config') or baseline(node)).encode())
    read_config(config)
    hostname = re.sub(r'[^a-zA-Z0-9-]', '-', node['name']).strip('-') or 'switch'
    runtime.update({'ll2s': True, 'container': stem, 'll2s_ports': [],
                    'll2s_image_id': image_id})
    lab.journal()
    # Each TAP has one passthru macvlan: receive downstream MACs, VLANs and BPDUs.
    # No host PID namespace, host-path bind mounts, or Docker-managed uplink.
    for index in range(node['ethernet']):
        port = {'network': f'{stem}-{index}', 'tap': f"lt{lab.owner}{node['iol_id']:03x}{index:x}"}
        runtime['ll2s_ports'].append(port)
        lab.journal()
        run('ip', 'tuntap', 'add', 'dev', port['tap'], 'mode', 'tap')
        port['tap_created'] = True
        lab.journal()
        port['tap_alias'] = 'weblab-ll2s:' + uuid.uuid4().hex
        run('ip', 'link', 'set', 'dev', port['tap'], 'alias', port['tap_alias'])
        lab.journal()
        run('ip', 'link', 'set', 'dev', port['tap'], 'up')
        number = node['iol_id'] * 8 + index
        subnet = f'198.19.{number // 64}.{number % 64 * 4}/30'
        subnet6 = f'fd42:{lab.owner[:4]}:{lab.owner[4:]}:{number:x}::/64'
        network_id = run('docker', 'network', 'create', '-d', 'macvlan', '--internal', '--subnet', subnet,
            '--ipv6', '--subnet', subnet6,
            '--label', f'iol.lab={lab.owner}', '-o', f"parent={port['tap']}", '-o', 'macvlan_mode=passthru', port['network'])
        port['network_id'] = network_id
        port['network_created'] = True
        lab.journal()
    container_id = run('docker', 'run', '-d', '--name', stem, '--label', f'iol.lab={lab.owner}',
        '--hostname', hostname, '--network', runtime['ll2s_ports'][0]['network'], '--dns', '127.0.0.1',
        '--cap-add', 'NET_ADMIN', '--cap-add', 'NET_RAW',
        '--memory', f"{node['memory']}m", '--pids-limit', '256',
        '--entrypoint', '/bin/sh', image_id, '-c',
        'while [ ! -e /run/weblab-ready ]; do sleep 0.1; done; exec /usr/local/bin/ll2s-entrypoint sleep infinity')
    runtime['ll2s_container_id'] = container_id
    runtime['container_created'] = True
    lab.journal()
    for port in runtime['ll2s_ports'][1:]:
        run('docker', 'network', 'connect', port['network'], stem)
    info = json.loads(run('docker', 'inspect', stem))[0]['NetworkSettings']['Networks']
    interfaces = json.loads(run('docker', 'exec', stem, 'ip', '-j', 'link', 'show'))
    by_mac = {i['address'].lower(): i['ifname'] for i in interfaces if 'address' in i}
    # Docker's interface numbering can vary; rename by network endpoint MAC.
    for index, port in enumerate(runtime['ll2s_ports']):
        name = by_mac[info[port['network']]['MacAddress'].lower()]
        run('docker', 'exec', stem, 'ip', 'link', 'set', name, 'down')
        run('docker', 'exec', stem, 'ip', 'link', 'set', name, 'name', f'wl{index}')
    for index in range(node['ethernet']):
        name = f'eth{index}'
        run('docker', 'exec', stem, 'ip', 'link', 'set', f'wl{index}', 'name', name)
        run('docker', 'exec', stem, 'ip', 'addr', 'flush', 'dev', name)
        run('docker', 'exec', stem, 'ip', 'link', 'set', name, 'up')
    run('docker', 'exec', stem, 'ip', '-4', 'route', 'flush', 'default')
    run('docker', 'exec', stem, 'ip', '-6', 'route', 'flush', 'default')
    run('docker', 'cp', str(config), f'{stem}:/etc/ll2s/startup.conf')
    run('docker', 'exec', stem, 'll2sh', '--check', '-f', '/etc/ll2s/startup.conf')
    routes = {index: lab.fabric.peers[(node_id, f'eth{index}')]
              for index in range(node['ethernet']) if (node_id, f'eth{index}') in lab.fabric.peers}
    bridge_config = directory / 'll2s-network.json'
    bridge_config.write_text(json.dumps({'id': node['iol_id'], 'netio': str(lab.netio),
                                       'taps': [p['tap'] for p in runtime['ll2s_ports']], 'routes': routes}))
    lab.spawn(node_id, 'bridge', [shutil.which('python3'), str(root / 'tap_net.py'), str(bridge_config)], directory)
    runtime['ll2s_configured'] = True
    runtime['ll2s_ready'] = False
    lab.journal()
    run('docker', 'exec', stem, 'touch', '/run/weblab-ready')
    deadline = time.monotonic() + 30
    while True:
        try:
            run('docker', 'exec', stem, 'test', '-f', '/run/ll2s/ready', timeout=5)
            break
        except error:
            pass
        if time.monotonic() >= deadline:
            raise error('LL2S daemons did not become ready: ' + run('docker', 'logs', '--tail', '30', stem))
        time.sleep(.25)
    runtime['ll2s_ready'] = True
    lab.journal()
    return sys.executable, [str(root / 'container_console.py'), '--ll2s', stem]


def save(lab, node_id, run):
    runtime = lab.runtime[node_id]
    if not runtime.get('ll2s_configured') or not runtime.get('container_created'):
        return
    # copy only the last "write memory" result; never save running config implicitly.
    target = config_path(lab.node(node_id), lab.node_dir(node_id))
    with tempfile.TemporaryDirectory(prefix='.ll2s-save-', dir=lab.directory) as temp:
        copy = Path(temp) / 'startup.conf'
        run('docker', 'cp', f"{runtime.get('ll2s_container_id') or runtime['container']}:/etc/ll2s/startup.conf", str(copy))
        data = read_config(copy)
        atomic_write(target, data)
        # Older journals predate ll2s_ready, but already record the launch ID.
        # Failed startup must not attribute an unvalidated config to a new image.
        if runtime.get('ll2s_ready', True):
            write_identity(target, data, runtime.get('ll2s_image_id'))


def inspect_resource(kind, name, run, error):
    """Only Docker's explicit not-found response means absent, never daemon failure."""
    try:
        result = run('docker', kind, 'inspect', name)
    except error as exc:
        message = str(exc)
        missing = (f'Error: No such {kind}: {name}',
                   f'Error response from daemon: No such {kind}: {name}',
                   f'Error response from daemon: {kind} {name} not found')
        if message in missing:
            return None
        raise
    try:
        values = json.loads(result)
        if len(values) != 1 or not isinstance(values[0], dict) or not values[0].get('Id'):
            raise ValueError()
        return values[0]
    except (TypeError, ValueError, KeyError) as exc:
        raise error(f'Invalid Docker {kind} inspection for {name}') from exc


def prepare_stop(lab, node_id, run, error):
    runtime = lab.runtime[node_id]
    if not runtime.get('container_created'):
        return
    info = inspect_resource('container', runtime['container'], run, error)
    if info is None:
        logging.warning('LL2S %s container is missing; retaining the last host-saved '
                        'configuration and cleaning journaled resources', node_id)
        runtime.pop('container_created')
        lab.journal()
        return
    if ((info.get('Config', {}).get('Labels') or {}).get('iol.lab') != lab.owner or
            (runtime.get('ll2s_container_id') and info['Id'] != runtime['ll2s_container_id'])):
        raise error('LL2S container identity/ownership changed; inspect it before retrying Stop')
    # Upgrade old journals to immutable identity before copy/removal. Do not touch
    # a replacement that acquires the same name between inspection and cleanup.
    runtime['ll2s_container_id'] = info['Id']
    lab.journal()
    save(lab, node_id, run)


def cleanup_ports(lab, runtime, run, error):
    errors = []
    for port in reversed(runtime.get('ll2s_ports', [])):
        try:
            if port.get('network_created'):
                info = inspect_resource('network', port['network'], run, error)
                if info is not None:
                    if ((info.get('Labels') or {}).get('iol.lab') != lab.owner or
                            info.get('Driver') != 'macvlan' or
                            (info.get('Options') or {}).get('parent') != port['tap'] or
                            (port.get('network_id') and info['Id'] != port['network_id'])):
                        raise error(f"Network {port['network']} ownership/identity changed")
                    if info.get('Containers'):
                        raise error(f"Network {port['network']} still has attached endpoints")
                    run('docker', 'network', 'rm', info['Id'])
                port.pop('network_created')
                lab.journal()
            # Never delete the parent TAP if its network could not be removed.
            if port.get('tap_created'):
                links = json.loads(run('ip', '-d', '-j', 'link', 'show'))
                found = [link for link in links if link.get('ifname') == port['tap']]
                if found:
                    link = found[0]
                    expected = f"lt{lab.owner}{runtime['iol_id']:03x}"
                    marker = port.get('tap_alias', '')
                    details = link.get('linkinfo', {})
                    if (not re.fullmatch(re.escape(expected) + r'[0-7]', port['tap']) or
                            link.get('ifalias', '') != marker or
                            details.get('info_kind') != 'tun' or
                            details.get('info_data', {}).get('type') != 'tap'):
                        raise error(f"TAP {port['tap']} ownership/type changed")
                    run('ip', 'link', 'delete', port['tap'])
                port.pop('tap_created')
                lab.journal()
        except (error, ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
    if errors:
        raise error('LL2S port cleanup failed; retry Stop: ' + '; '.join(errors))
