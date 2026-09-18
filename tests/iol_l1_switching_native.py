"""Opt-in IOL L2 stop/ZIP/start regression (root, local images/iourc).
Run: python3 tests/iol_l1_switching_native.py
Two disposable switches run rapid-PVST with loop guard and two 802.1Q trunks.
Repeatedly save device configuration, stop, download a ZIP and start the same lab.
Private socket/hosts mounts isolate active state. Evidence stays in /tmp.
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

root = Path(tempfile.mkdtemp(prefix='iol-l1-switching-'))
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
    topology = {'name': 'Disposable switching L1 restart check', 'nodes': [], 'links': [
        {'id': 'main', 'a': {'node': 'S', 'port': '0/1'}, 'b': {'node': 'R', 'port': '0/1'}},
        {'id': 'control', 'a': {'node': 'S', 'port': '0/2'}, 'b': {'node': 'R', 'port': '0/2'}}]}
    for name, addr in [('S', 1), ('R', 2)]:
        config = f'hostname {name}\nno service config\nno ip domain lookup\nspanning-tree mode rapid-pvst\nspanning-tree loopguard default\nspanning-tree vlan 10 priority {0 if name == "S" else 4096}\nvlan 10\nname TEST\ninterface Ethernet0/1\n switchport trunk encapsulation dot1q\n switchport mode trunk\n spanning-tree cost 10\n no shutdown\ninterface Ethernet0/2\n switchport trunk encapsulation dot1q\n switchport mode trunk\n spanning-tree cost 20\n no shutdown\ninterface Vlan10\n ip address 198.18.1.{addr} 255.255.255.0\n no shutdown\nline con 0\n privilege level 15\n exec-timeout 0 0\nend\n'
        topology['nodes'].append({'id': name, 'name': name, 'type': 'switch',
                                  'image': 'cisco_iol-l2-17.18.02.bin', 'memory': 1024, 'ethernet': 2, 'startup_config': config})
    lab.save(topology)
    for cycle in range(3):
        print('CYCLE', cycle+1, 'start', flush=True)
        api('lab/start', {})
        connect()
        if cycle == 0:
            for name in nodes:
                command(name, 'configure terminal\rvtp mode transparent\rvlan 10\rname TEST\rend')
        for name in nodes:
            waitstate(name, '0/1', True)
            waitstate(name, '0/2', True)
        time.sleep(25)
        output = command('R', 'show spanning-tree vlan 10')
        print(output, flush=True)
        assert 'Root FWD' in output and 'Altn BLK' in output, output
        ping('198.18.1.2', True, 3)
        for side in ('a', 'b'):
            api('links/main/carrier', {'side':side, 'up':False})
        for name in nodes: waitstate(name, '0/1', False)
        for side in ('a', 'b'):
            api('links/main/carrier', {'side':side, 'up':True})
        for name in nodes: waitstate(name, '0/1', True)
        time.sleep(5)
        for name in nodes: command(name, 'write memory\r')
        disconnect()
        api('lab/stop', {})
        result = api('export', {})
        with urlopen(f'http://127.0.0.1:{server.server_port}'+result['url'], timeout=30) as response:
            (root/f'cycle-{cycle}.zip').write_bytes(response.read())
        assert not list((root/'l1').iterdir())
    print('PASS repeated stop/ZIP/start with saved switch config, STP and carrier changes', flush=True)

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
