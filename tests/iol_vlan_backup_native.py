"""Opt-in real IOL VLAN/STP ZIP restore test; uses disposable storage only.
Run as root: python3 tests/iol_vlan_backup_native.py
Requires the locally supplied cisco_iol-l2-17.18.02.bin and any license it needs.
For an external user-supplied license, set IOURC=/path/to/iourc when running.
Uses private IOL socket mounts, no licensing or network configuration changes.
Evidence (archive, logs, restored files) remains in /tmp/iol-vlan-backup-*.
"""
import io
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if '--isolated' not in sys.argv:
    if os.geteuid() != 0:
        raise SystemExit('Root is required for a private mount namespace')
    for target in ('/tmp/netio0', '/tmp/netl10'):
        Path(target).mkdir(exist_ok=True)
    raise SystemExit(subprocess.run(['unshare', '--mount', '--propagation', 'private',
                                    sys.executable, '-u', str(Path(__file__).resolve()), '--isolated']).returncode)
if os.readlink('/proc/self/ns/mnt') == os.readlink(f'/proc/{os.getppid()}/ns/mnt'):
    raise SystemExit('Refusing mounts without a private namespace')
sys.path.insert(0, str(ROOT))
import lab_backup
import lab_server
from test_lab import WebSocket

root = Path(tempfile.mkdtemp(prefix='iol-vlan-backup-'))
print('EVIDENCE', root, flush=True)
for local, target in [('netio', '/tmp/netio0'), ('l1', '/tmp/netl10')]:
    (root / local).mkdir()
    subprocess.run(['mount', '--bind', str(root / local), target], check=True)
lab = lab_server.Lab(root / 'data', ROOT / 'images')
server = lab_server.ThreadingHTTPServer(('127.0.0.1', 0), lab_server.Handler)
server.daemon_threads = True
server.lab = lab
server.stopping = threading.Event()
http_thread = threading.Thread(target=server.serve_forever, daemon=True)
http_thread.start()
nodes, failures = {}, []
lock, stop = threading.Lock(), threading.Event()
worker = None
VLANS = {10: 'USERS', 20: 'SERVERS', 30: 'VOICE', 40: 'IOT', 999: 'UNUSED_NATIVE'}


def pump():
    try:
        while not stop.is_set():
            for sock in select.select([n['ws'].sock for n in nodes.values()], [], [], .05)[0]:
                n = next(n for n in nodes.values() if n['ws'].sock == sock)
                opcode, data = n['ws'].receive()
                if opcode == 9:
                    n['ws'].send(data, opcode=10)
                elif opcode in (1, 2):
                    with lock:
                        n['output'] += data
                elif opcode == 8:
                    raise EOFError('Console closed')
    except Exception as exc:
        if not stop.is_set():
            failures.append(repr(exc))


def command(name, cmd, timeout=10):
    n = nodes[name]
    with lock:
        start = len(n['output'])
    n['ws'].send(cmd.encode()+b'\r')
    deadline = time.monotonic()+timeout
    text = ''
    while time.monotonic() < deadline:
        with lock:
            text = n['output'][start:].decode(errors='replace').replace('\r', '')
        if text.rstrip().endswith(name+'#'):
            return text
        assert not failures, failures
        time.sleep(.05)
    raise AssertionError((name, cmd, text[-1500:]))


def connect():
    global worker
    stop.clear()
    for name in ('S', 'R'):
        nodes[name] = {'ws': WebSocket(server.server_port, name),
                       'output': nodes.get(name, {}).get('output', b'')}
    worker = threading.Thread(target=pump, daemon=True)
    worker.start()
    time.sleep(10)
    for name in nodes:
        command(name, '')
        command(name, 'terminal length 0')


def disconnect():
    stop.set()
    if worker:
        worker.join(timeout=5)
    for n in nodes.values():
        n['ws'].close()


def verify():
    for name in ('S', 'R'):
        output = command(name, 'show vlan brief')
        print(name, output, flush=True)
        for vlan, label in VLANS.items():
            assert re.search(rf'^\s*{vlan}\s+{label}\s+active', output, re.M), output
    deadline = time.monotonic() + 60
    while True:
        output = command('R', 'show spanning-tree vlan 10')
        if 'Root FWD' in output and 'Altn BLK' in output:
            break
        assert time.monotonic() < deadline, output
        time.sleep(1)
    print(output, flush=True)
    for attempt in range(5):
        output = command('S', 'ping 198.18.1.2 repeat 3 timeout 1')
        if 'Success rate is 100 percent' in output:
            break
        assert attempt < 4, output
    print(output, flush=True)


try:
    topology = {'name': 'Disposable VLAN database ZIP restore', 'nodes': [], 'links': [
        {'id': 'main', 'a': {'node': 'S', 'port': '0/1'}, 'b': {'node': 'R', 'port': '0/1'}},
        {'id': 'other', 'a': {'node': 'S', 'port': '0/2'}, 'b': {'node': 'R', 'port': '0/2'}}]}
    for name, addr in [('S', 1), ('R', 2)]:
        config = f"""hostname {name}
no service config
no ip domain lookup
no logging console
spanning-tree mode rapid-pvst
spanning-tree vlan 10 priority {0 if name == 'S' else 4096}
interface Ethernet0/1
 switchport trunk encapsulation dot1q
 switchport mode trunk
 spanning-tree cost 10
 no shutdown
interface Ethernet0/2
 switchport trunk encapsulation dot1q
 switchport mode trunk
 spanning-tree cost 20
 no shutdown
interface Vlan10
 ip address 198.18.1.{addr} 255.255.255.0
 no shutdown
line con 0
 privilege level 15
 exec-timeout 0 0
end
"""
        topology['nodes'].append({'id': name, 'name': name, 'type': 'switch',
                                  'image': 'cisco_iol-l2-17.18.02.bin', 'memory': 1024,
                                  'ethernet': 1, 'startup_config': config})
    lab.save(topology)
    lab.start_all()
    connect()
    for name in ('S', 'R'):
        commands = ['configure terminal', 'vtp domain ZIPTEST', 'vtp mode server']
        for vlan, label in VLANS.items():
            commands.extend([f'vlan {vlan}', f'name {label}'])
        commands.append('end')
        command(name, '\r'.join(commands))
        # IOS asks to confirm replacing the launcher-generated initial NVRAM.
        command(name, 'write memory\r')
    verify()
    disconnect()
    lab.stop_all()
    saved = {}
    for node in lab.topology['nodes']:
        filename = f"vlan.dat-{node['iol_id']:05d}"
        saved[node['id']] = (filename, (lab.node_dir(node['id']) / filename).read_bytes())
    result = lab_backup.create(lab)
    stream, _ = lab_backup.take(lab, result['url'].rsplit('/', 1)[1])
    with stream:
        backup = stream.read()
    (root / 'saved.zip').write_bytes(backup)
    with zipfile.ZipFile(io.BytesIO(backup)) as archive:
        for name, (filename, data) in saved.items():
            assert archive.read(f'nodes/{name}/{filename}') == data
    # Remove the source storage: this must boot from the archive, not local files.
    shutil.rmtree(lab.directory / 'nodes')
    for node in lab.topology['nodes']:
        node['iol_id'] += 100
        node['startup_config'] = ''
    lab.persist()
    lab_backup.restore(lab, io.BytesIO(backup), lab_server.run)
    for node in lab.topology['nodes']:
        directory = lab.node_dir(node['id'])
        filename = f"vlan.dat-{node['iol_id']:05d}"
        assert (directory / filename).read_bytes() == saved[node['id']][1]
        assert not (directory / saved[node['id']][0]).exists()
        # Neither restored seed text nor existing source files may recreate VLANs.
        node['startup_config'] = ''
    lab.persist()
    lab.start_all()
    connect()
    verify()
    print('PASS VLAN names, STP root/alternate and SVI traffic after ZIP restore with remapped IDs', flush=True)
finally:
    disconnect()
    server.stopping.set()
    lab.stop_all()
    lab_backup.expire(lab, all_files=True)
    server.shutdown()
    server.server_close()
    http_thread.join()
    lab.file_lock.close()
    for name, node in nodes.items():
        (root / (name + '.console.log')).write_bytes(node['output'])
    assert not list((root / 'l1').iterdir()), 'L1 socket leak'
    assert not list((root / 'netio').iterdir()), 'Data socket leak'
    print('CLEANED disposable resources; evidence', root, flush=True)
