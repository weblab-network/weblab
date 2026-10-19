"""IPv4 NAT/DNS gateway, executed only inside the dedicated helper container."""
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import sys
import traceback
import subprocess
import time
from internet_cloud import settings


def run(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError('Gateway setup failed: ' + exc.output[-3000:]) from exc


def firewall(config, lan, wan):
    prefixes = [str(ipaddress.IPv4Interface(config['address']).network), *[r['destination'] for r in config['routes']]]
    lines = ['*filter', ':INPUT DROP [0:0]', ':FORWARD DROP [0:0]', ':OUTPUT ACCEPT [0:0]',
             '-A INPUT -i lo -j ACCEPT',
             '-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT',
             '-A INPUT -m conntrack --ctstate INVALID -j DROP',
             '-A FORWARD -m conntrack --ctstate INVALID -j DROP',
             f'-A FORWARD -i {wan} -o {lan} -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT']
    for prefix in prefixes:
        for proto in ('tcp', 'udp'):
            lines.append(f'-A INPUT -i {lan} -s {prefix} -p {proto} --dport 53 -j ACCEPT')
        lines.append(f'-A INPUT -i {lan} -s {prefix} -p icmp -j ACCEPT')
        lines.append(f'-A FORWARD -i {lan} -o {wan} -s {prefix} -j ACCEPT')
    lines += ['COMMIT', '*nat', ':PREROUTING ACCEPT [0:0]', ':INPUT ACCEPT [0:0]',
              ':OUTPUT ACCEPT [0:0]', ':POSTROUTING ACCEPT [0:0]']
    for prefix in prefixes:
        lines.append(f'-A POSTROUTING -s {prefix} -o {wan} -j MASQUERADE')
    return '\n'.join(lines + ['COMMIT', ''])


def main():
    raw = json.loads(os.environ['WL_INTERNET_CONFIG'])
    config = settings({k:v for k,v in raw.items() if k != 'provisioning_subnet'})
    provisioning = ipaddress.IPv4Network(raw['provisioning_subnet'])
    interfaces = json.loads(run('ip', '-j', '-4', 'addr'))
    lan = next(i['ifname'] for i in interfaces if any(ipaddress.IPv4Address(a['local']) in provisioning for a in i.get('addr_info', [])))
    wan = json.loads(run('ip', '-j', '-4', 'route', 'show', 'default'))[0]['dev']
    if lan == wan or not all(n.replace('_','').isalnum() for n in (lan,wan)):
        raise ValueError('Invalid gateway interfaces')
    subprocess.run(['iptables-restore', '--noflush'], input=firewall(config, lan, wan), text=True, check=True)
    run('ip', '-4', 'addr', 'flush', 'dev', lan)
    run('ip', 'addr', 'add', config['address'], 'dev', lan)
    run('ip', 'link', 'set', lan, 'up')
    for route in config['routes']:
        run('ip', 'route', 'add', route['destination'], 'via', route['via'], 'dev', lan)
    dns = subprocess.Popen(['dnsmasq', '--keep-in-foreground', '--conf-file=/dev/null',
        '--user=dnsmasq', '--group=dnsmasq', '--bind-interfaces', '--interface='+lan,
        '--listen-address='+str(ipaddress.IPv4Interface(config['address']).ip),
        '--except-interface=lo', '--no-hosts', '--no-resolv', '--server=127.0.0.11',
        '--cache-size=150', '--pid-file=', '--log-facility=-'], stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 10
    while True:
        if dns.poll() is not None:
            raise RuntimeError('DNS forwarder failed')
        try:
            with socket.create_connection((str(ipaddress.IPv4Interface(config['address']).ip), 53), timeout=.2):
                break
        except OSError:
            if time.monotonic() >= deadline:
                dns.terminate()
                raise RuntimeError('DNS listener did not become ready')
            time.sleep(.1)
    Path('/run/ready').touch()
    def stop(*_):
        run('iptables', '-F', 'FORWARD')
        dns.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print('Internet gateway ready: '+config['address'], flush=True)
    code = dns.wait()
    run('iptables', '-F', 'FORWARD')
    raise SystemExit(code)

if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(1)
