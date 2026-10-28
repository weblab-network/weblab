#!/usr/bin/env python3
"""Opt-in kernel firewall checks in disposable namespaces, with no host networking."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('gateway', ROOT/'packaging/internet/gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=10, **kwargs).stdout


def exercise():
    if Path('/proc/self/ns/net').readlink() == Path('/proc/1/ns/net').readlink():
        raise RuntimeError('Refusing to run in the initial network namespace')
    children = []
    try:
        for role, subnet in [('w', '198.18.0'), ('l', '192.0.2')]:
            proc = subprocess.Popen(['unshare', '-n', 'sleep', '60'])
            children.append(proc)
            for attempt in range(100):
                if Path(f'/proc/{proc.pid}/ns/net').readlink() != Path('/proc/self/ns/net').readlink():
                    break
                time.sleep(.02)
            else:
                raise AssertionError('Child network namespace did not become ready')
            def peer(*args):
                return run('nsenter', '-t', str(proc.pid), '-n', *args)
            run('ip', 'link', 'add', 'g'+role, 'type', 'veth', 'peer', 'name', 'p'+role)
            run('ip', 'link', 'set', 'p'+role, 'netns', str(proc.pid))
            run('ip', 'addr', 'add', subnet+'.1/24', 'dev', 'g'+role)
            run('ip', 'link', 'set', 'g'+role, 'up')
            peer('ip', 'link', 'set', 'lo', 'up')
            peer('ip', 'addr', 'add', subnet+'.2/24', 'dev', 'p'+role)
            peer('ip', 'link', 'set', 'p'+role, 'up')
        run('sysctl', '-w', 'net.ipv4.ip_forward=1')
        config = {'address': '192.0.2.1/24', 'routes': [],
                  'firewall': {'default_action': 'deny', 'rules': []},
                  'port_forwards': [{'protocol': 'udp', 'bind_address': '0.0.0.0', 'external_port': 19999,
                                    'target_address': '192.0.2.2', 'target_port': 19999, 'source': ''}]}
        run('iptables-restore', input=gateway.firewall(config, 'gl', 'gw', '198.18.0.1', '198.18.0.254'))
        client, target = [str(child.pid) for child in children]
        probe = '''
import socket, errno, sys
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(2)
s.connect(('198.18.0.1',40000));s.send(b'test')
try: s.recv(1024)
except OSError as error:
 assert error.errno==int(sys.argv[1]),repr(error)
else: raise AssertionError('Expected related ICMP error')
'''
        # A closed forwarded service must report port-unreachable even under Deny.
        run('nsenter', '-t', client, '-n', 'python3', '-c', probe, '111')
        # Send a genuine fragmentation-needed error quoting the translated flow.
        sender = '''
import socket,struct
udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('0.0.0.0',19999))
r=socket.socket(socket.AF_INET,socket.SOCK_RAW,socket.IPPROTO_UDP);r.settimeout(5)
s=socket.socket(socket.AF_INET,socket.SOCK_RAW,socket.IPPROTO_ICMP)
print('ready',flush=True)
packet,_=r.recvfrom(65535)
header=struct.pack('!BBHHH',3,4,0,0,1200)+packet[:28]
words=struct.unpack('!%dH'%(len(header)//2),header)
c=sum(words);c=(c&65535)+(c>>16);c=(c&65535)+(c>>16)
header=header[:2]+struct.pack('!H',(~c)&65535)+header[4:]
s.sendto(header,('192.0.2.1',0))
'''
        error_sender = subprocess.Popen(['nsenter', '-t', target, '-n', 'python3', '-c', sender],
                                         stdout=subprocess.PIPE, text=True)
        children.append(error_sender)
        import select
        assert select.select([error_sender.stdout], [], [], 5)[0], 'ICMP sender did not start'
        assert error_sender.stdout.readline().strip() == 'ready'
        run('nsenter', '-t', client, '-n', 'python3', '-c', probe, '90')
        assert error_sender.wait(timeout=5) == 0
        # No broad ICMP exemption: unrelated outbound ping still hits Deny.
        run('nsenter', '-t', target, '-n', 'ip', 'route', 'add', '198.18.0.0/24', 'via', '192.0.2.1')
        denied = subprocess.run(['nsenter', '-t', target, '-n', 'ping', '-c', '1', '-W', '1', '198.18.0.2'],
                                capture_output=True, timeout=5)
        assert denied.returncode != 0, 'Unrelated outbound ICMP escaped Deny'
        gateway.apply_firewall({'default_action': 'allow', 'rules': []})
        run('nsenter', '-t', target, '-n', 'ping', '-c', '1', '-W', '1', '198.18.0.2')
        print('PASS forwarded UDP errors and PMTU under Deny; unrelated ICMP still denied; Allow control passes')
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            child.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--isolated', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.isolated:
        exercise()
    else:
        subprocess.run(['unshare', '-n', sys.executable, str(Path(__file__).resolve()), '--run', '--isolated'], check=True)
