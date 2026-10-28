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
from internet_cloud import settings, firewall_settings, FORWARD_PORT_BASE


def run(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError('Gateway setup failed: ' + exc.output[-3000:]) from exc


def egress_rules(raw):
    policy = firewall_settings(raw)
    lines = []
    for rule in policy['rules']:
        match = ['-A WL_EGRESS']
        if rule['protocol'] != 'any':
            match += ['-p', rule['protocol']]
        for field, option in (('source', '-s'), ('destination', '-d'),
                              ('source_port', '--sport'), ('destination_port', '--dport')):
            if rule[field]:
                value = rule[field].replace('-', ':') if field.endswith('_port') else rule[field]
                match += [option, value]
        match += ['-j', 'ACCEPT' if rule['action'] == 'allow' else 'DROP']
        lines.append(' '.join(match))
    lines.append('-A WL_EGRESS -j ' + ('ACCEPT' if policy['default_action'] == 'allow' else 'DROP'))
    return lines


def apply_firewall(raw):
    # --noflush rebuilds only the declared user chain, in one filter COMMIT.
    # NAT, source admission, reply handling and conntrack are left intact.
    rules = '\n'.join(['*filter', ':WL_EGRESS - [0:0]', *egress_rules(raw), 'COMMIT', ''])
    subprocess.run(['iptables-restore', '--wait', '5', '--noflush'],
                   input=rules, text=True, check=True, timeout=8)


def port_forward_rules(config, lan, wan, wan_address, wan_gateway):
    filters, nat = [], []
    for index, forward in enumerate(config.get('port_forwards', [])):
        outside = str(ipaddress.IPv4Address(wan_address))
        port = FORWARD_PORT_BASE + index
        target, target_port, protocol = forward['target_address'], forward['target_port'], forward['protocol']
        original = f'--ctorigdst {outside} --ctorigdstport {port}'
        # Restrict loopback publications inside the helper as well: older Docker
        # engines can expose loopback-published ports to peers on the host LAN.
        sources = [str(ipaddress.IPv4Address(wan_gateway))+'/32', '127.0.0.1/32'] if forward['bind_address']=='127.0.0.1' else [forward['source']]
        for source in sources:
            match = f'-i {wan} -p {protocol}' + (f' -s {source}' if source else '')
            nat.append(f'-A PREROUTING {match} -d {outside} --dport {port} -j DNAT --to-destination {target}:{target_port}')
            filters.append(f'-A FORWARD {match} -o {lan} -d {target} --dport {target_port} '
                           f'-m conntrack --ctstate DNAT --ctdir ORIGINAL {original} -j ACCEPT')
        # Replies to explicitly admitted forwards are independent of outbound policy.
        filters.append(f'-A FORWARD -i {lan} -o {wan} -p {protocol} -s {target} --sport {target_port} '
                       f'-m conntrack --ctstate ESTABLISHED,RELATED --ctdir REPLY {original} -j ACCEPT')
        # Errors can come from the target or a router on its path. Match the
        # admitted connection's original tuple, not the ICMP packet's source or
        # transport protocol. Keep unrelated ICMP subject to outbound policy.
        for icmp_type in (3, 11, 12):
            filters.append(f'-A FORWARD -i {lan} -o {wan} -p icmp --icmp-type {icmp_type} '
                           f'-m conntrack --ctstate RELATED --ctdir REPLY --ctproto {protocol} '
                           f'{original} -j ACCEPT')
    if config.get('port_forwards'):
        # Make the return path reliable even for directly connected targets without
        # a default route. Targets see the gateway's lab address as the client.
        address = str(ipaddress.IPv4Interface(config['address']).ip)
        nat.append(f'-A POSTROUTING -o {lan} -m conntrack --ctstate DNAT --ctdir ORIGINAL -j SNAT --to-source {address}')
    return filters, nat


def firewall(config, lan, wan, wan_address=None, wan_gateway=None):
    prefixes = [str(ipaddress.IPv4Interface(config['address']).network), *[r['destination'] for r in config['routes']]]
    lines = ['*filter', ':INPUT DROP [0:0]', ':FORWARD DROP [0:0]', ':OUTPUT ACCEPT [0:0]',
             ':WL_EGRESS - [0:0]',
             '-A INPUT -i lo -j ACCEPT',
             '-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT',
             '-A INPUT -m conntrack --ctstate INVALID -j DROP',
             '-A FORWARD -m conntrack --ctstate INVALID -j DROP',
             f'-A FORWARD -i {wan} -o {lan} -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT']
    inbound_filters, inbound_nat = port_forward_rules(config, lan, wan, wan_address, wan_gateway)
    lines += inbound_filters
    for prefix in prefixes:
        for proto in ('tcp', 'udp'):
            lines.append(f'-A INPUT -i {lan} -s {prefix} -p {proto} --dport 53 -j ACCEPT')
        lines.append(f'-A INPUT -i {lan} -s {prefix} -p icmp -j ACCEPT')
        lines.append(f'-A FORWARD -i {lan} -o {wan} -s {prefix} -j WL_EGRESS')
    # Only authorized lab sources enter this chain, including established packets.
    lines += egress_rules(config.get('firewall', {}))
    lines += ['COMMIT', '*nat', ':PREROUTING ACCEPT [0:0]', ':INPUT ACCEPT [0:0]',
              ':OUTPUT ACCEPT [0:0]', ':POSTROUTING ACCEPT [0:0]']
    for prefix in prefixes:
        lines.append(f'-A POSTROUTING -s {prefix} -o {wan} -j MASQUERADE')
    lines += inbound_nat
    return '\n'.join(lines + ['COMMIT', ''])


def main():
    raw = json.loads(os.environ['WL_INTERNET_CONFIG'])
    config = settings({k:v for k,v in raw.items() if k != 'provisioning_subnet'})
    provisioning = ipaddress.IPv4Network(raw['provisioning_subnet'])
    interfaces = json.loads(run('ip', '-j', '-4', 'addr'))
    lan = next(i['ifname'] for i in interfaces if any(ipaddress.IPv4Address(a['local']) in provisioning for a in i.get('addr_info', [])))
    default = json.loads(run('ip', '-j', '-4', 'route', 'show', 'default'))[0]
    wan = default['dev']
    wan_address = next(a['local'] for i in interfaces if i['ifname']==wan for a in i.get('addr_info', []))
    if lan == wan or not all(n.replace('_','').isalnum() for n in (lan,wan)):
        raise ValueError('Invalid gateway interfaces')
    subprocess.run(['iptables-restore', '--noflush'], input=firewall(config, lan, wan, wan_address, default.get('gateway')), text=True, check=True)
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
        if len(sys.argv) == 3 and sys.argv[1] == 'firewall':
            if not Path('/run/ready').is_file():
                raise RuntimeError('Internet gateway is not ready')
            apply_firewall(json.loads(sys.argv[2]))
        elif len(sys.argv) == 1:
            main()
        else:
            raise ValueError('Unsupported gateway operation')
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(1)
