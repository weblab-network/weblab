"""Opt-in production-launcher IOL L1 integration test (root, local images/iourc).
Run: python3 tests/iol_l1_native.py
Private mount namespaces isolate both IOU socket trees and the /etc/hosts view.
No Docker or active app state is touched. Evidence remains in /tmp/iol-l1-native-*.
"""
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import Request, urlopen

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
import lab_server
from test_lab import WebSocket

root = Path(tempfile.mkdtemp(prefix='iol-l1-native-'))
print('EVIDENCE', root, flush=True)
for local, target in [('netio', '/tmp/netio0'), ('l1', '/tmp/netl10')]:
    (root/local).mkdir()
    subprocess.run(['mount', '--bind', str(root/local), target], check=True)
hosts = root/'hosts'
hosts.write_text('\n'.join(line for line in Path('/etc/hosts').read_text().splitlines()
                           if 'xml.cisco.com' not in line)+'\n')
subprocess.run(['mount', '--bind', str(hosts), '/etc/hosts'], check=True)
assert (ROOT/'images/iourc').is_file(), 'A local image license is required'
lab = lab_server.Lab(root/'data', ROOT/'images')
server = lab_server.ThreadingHTTPServer(('127.0.0.1', 0), lab_server.Handler)
server.daemon_threads = True
server.lab = lab
server.stopping = threading.Event()
http_thread = threading.Thread(target=server.serve_forever, daemon=True)
http_thread.start()
nodes, failures = {}, []
lock, stop = threading.Lock(), threading.Event()
worker = None


def api(path, data):
    with urlopen(Request(f'http://127.0.0.1:{server.server_port}/api/'+path,
                         data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'}), timeout=30) as response:
        return json.load(response)


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


def waitstate(name, port, up, timeout=22):
    started = time.monotonic()
    expected = 'up, line protocol is up' if up else 'down, line protocol is down'
    while time.monotonic()-started < timeout:
        output = command(name, 'show interfaces Ethernet'+port)
        match = re.search(r'Ethernet'+re.escape(port)+r' is ([^\n]+)', output)
        if match and match.group(1).startswith(expected):
            print('STATE', name, port, expected, 'after', round(time.monotonic()-started, 2), 's', flush=True)
            return
        time.sleep(.5)
    raise AssertionError(output)


def ping(address, ok, attempts=1):
    for _ in range(attempts):
        output = command('S', f'ping {address} repeat 3 timeout 1')
        print('PING', address, re.findall(r'Success rate[^\n]+', output), flush=True)
        if f'Success rate is {100 if ok else 0} percent' in output:
            return
        time.sleep(1)
    raise AssertionError(output)


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


try:
    topology = {'name': 'Disposable production IOL L1 check', 'nodes': [], 'links': [
        {'id': 'main', 'a': {'node': 'S', 'port': '0/0'}, 'b': {'node': 'R', 'port': '0/0'}},
        {'id': 'control', 'a': {'node': 'S', 'port': '1/2'}, 'b': {'node': 'R', 'port': '0/1'}}]}
    for name, image, port, addr in [('S', 'cisco_iol-l2-17.18.02.bin', '1/2', 1), ('R', 'cisco_iol-17.18.02.bin', '0/1', 2)]:
        routed = ' no switchport\n' if name == 'S' else ''
        config = f'hostname {name}\nno service config\nno ip domain lookup\nno logging console\ninterface Ethernet0/0\n{routed} ip address 198.18.1.{addr} 255.255.255.0\n no shutdown\ninterface Ethernet{port}\n{routed} ip address 198.18.2.{addr} 255.255.255.0\n no shutdown\nline con 0\n privilege level 15\n exec-timeout 0 0\nend\n'
        topology['nodes'].append({'id': name, 'name': name, 'type': 'switch' if name == 'S' else 'router',
                                  'image': image, 'memory': 1024, 'ethernet': 2, 'iol_l1': True, 'startup_config': config})
    lab.save(topology)
    api('lab/start', {})
    connect()
    assert all(lab.link_states()['main']['carrier_supported_'+side] for side in ('a', 'b'))
    for name in nodes:
        waitstate(name, '0/0', True)
    waitstate('S', '1/2', True)
    waitstate('R', '0/1', True)
    ping('198.18.1.2', True, 3)
    ping('198.18.2.2', True, 3)
    print('PHASE directional loss keeps carrier up', flush=True)
    api('links/main/traffic', {'blocked_a_to_b': True, 'blocked_b_to_a': False})
    time.sleep(15)
    for name in nodes:
        waitstate(name, '0/0', True)
    ping('198.18.1.2', False)
    ping('198.18.2.2', True)
    api('links/main/traffic', {'blocked_a_to_b': False, 'blocked_b_to_a': False})
    ping('198.18.1.2', True, 3)
    for side, name in [('a', 'S'), ('b', 'R')]:
        print('PHASE unplug', name, flush=True)
        api('links/main/carrier', {'side': side, 'up': False})
        assert lab.link_states()['main']['carrier_pending_'+side] > 0
        waitstate(name, '0/0', False)
        waitstate('R' if name == 'S' else 'S', '0/0', True)
        waitstate('S', '1/2', True)
        waitstate('R', '0/1', True)
        ping('198.18.1.2', False)
        ping('198.18.2.2', True)
        api('links/main/carrier', {'side': side, 'up': True})
        waitstate(name, '0/0', True)
        ping('198.18.1.2', True, 3)
    print('PHASE one-node restart with peer still running', flush=True)
    disconnect()
    api('nodes/R/stop', {})
    api('nodes/R/start', {})
    connect()
    for name in nodes:
        waitstate(name, '0/0', True)
    ping('198.18.1.2', True, 3)
    ping('198.18.2.2', True, 3)
    print('PASS production launcher, HTTP actions, L2/L3 guest carrier, independent control cable, directional loss, reconnect and one-node restart', flush=True)
finally:
    disconnect()
    server.stopping.set()
    lab.stop_all()
    server.shutdown()
    server.server_close()
    http_thread.join()
    lab.file_lock.close()
    for name, n in nodes.items():
        (root/(name+'.console.log')).write_bytes(n['output'])
    assert not list((root/'l1').iterdir()), 'L1 socket leak'
    assert not list((root/'netio').iterdir()), 'Data socket leak'
    print('CLEANED disposable resources; evidence', root, flush=True)
