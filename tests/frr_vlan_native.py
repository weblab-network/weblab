#!/usr/bin/env python3
"""Real tagged forwarding, isolation, ownership and persistence in disposable FRR nodes."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import frr
import lab_backup
import lab_server


def main(image=frr.VLAN_IMAGE):
    with tempfile.TemporaryDirectory(prefix='weblab-frr-vlan-') as directory:
        root = Path(directory)
        lab = lab_server.Lab(root/'data', root/'images', allow_untested_frr=image != frr.PUBLIC_IMAGE)

        def run(node, *args):
            return lab_server.run('docker', 'exec', lab.runtime[node]['container'], *args)

        def cli(node, *commands):
            return run(node, 'vtysh', *(part for cmd in commands for part in ('-c', cmd)))

        def wait(check, label, timeout=60):
            deadline = time.monotonic() + timeout
            last = ''
            while time.monotonic() < deadline:
                try:
                    if check():
                        print(label, flush=True)
                        return
                except lab_server.LabError as error:
                    last = str(error)
                time.sleep(.5)
            raise AssertionError(label + ': ' + last)

        def ping(address='10.90.10.2', family='-4'):
            return '0% packet loss' in run('r1', 'ping', family, '-c', '1', '-W', '1', address)

        def links(node='r1'):
            return {item['ifname']: item for item in json.loads(run(node, 'ip', '-d', '-j', 'link', 'show'))}

        def rejected(*commands):
            try:
                cli('r1', 'conf t', *commands, 'end')
            except lab_server.LabError:
                return
            raise AssertionError('Unsafe command accepted: ' + repr(commands))

        try:
            nodes = []
            for i in (1, 2):
                config = f'''hostname VLAN-R{i}
service integrated-vtysh-config
vlan interface eth0.10
vlan interface eth0.20
interface eth0.10
 ip address 10.90.10.{i}/30
 ipv6 address fd90:10::{i}/64
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
exit
interface eth0.20
 ip address 10.90.20.{i}/30
exit
router ospf
 ospf router-id 10.255.2.{i}
 network 10.90.10.0/30 area 0
exit
lldp run
'''
                nodes.append({'id': f'r{i}', 'name': f'VLAN-R{i}', 'type': 'router',
                              'image': image, 'ethernet': 2, 'startup_config': config})
            # Third router provides an independent subnet for a forwarding test;
            # no nested namespaces or extra container privileges are needed.
            nodes.append({'id': 'r3', 'name': 'VLAN-R3', 'type': 'router',
                          'image': image, 'ethernet': 2, 'startup_config': """hostname VLAN-R3
service integrated-vtysh-config
vlan interface eth0.20
interface eth0.20
 ip address 10.91.20.2/30
exit
ip route 10.90.10.0/30 10.91.20.1
lldp run
"""})
            lab.save({'name': 'Disposable routed VLAN regression', 'nodes': nodes, 'links': [
                {'id': 'trunk', 'a': {'node': 'r1', 'port': 'eth0'},
                 'b': {'node': 'r2', 'port': 'eth0'}},
                {'id': 'second-trunk', 'a': {'node': 'r1', 'port': 'eth1'},
                 'b': {'node': 'r3', 'port': 'eth0'}}]})
            lab.start_all()
            wait(lambda: ping(), 'VLAN 10 IPv4 forwarding from startup configuration')
            assert ping('10.90.20.2')
            wait(lambda: ping('fd90:10::2', '-6'), 'Tagged IPv6 forwarding')
            wait(lambda: 'Full' in cli('r1', 'show ip ospf neighbor'), 'OSPF adjacency over VLAN 10')
            assert 'eth0.20' in cli('r1', 'sh vlan int')
            detail = links()['eth0.10']['linkinfo']['info_data']
            assert detail['id'] == 10 and detail['protocol'] == '802.1Q'
            # Wrong tag carrying the same IP subnet must not communicate.
            cli('r2', 'conf t', 'no vlan interface eth0.20', 'vlan interface eth0.21',
                'interface eth0.21', 'ip address 10.90.20.2/30', 'end')
            try:
                ping('10.90.20.2')
            except lab_server.LabError:
                pass
            else:
                raise AssertionError('Different VLAN IDs communicated')
            cli('r2', 'conf t', 'interface eth0.21', 'no ip address 10.90.20.2/30',
                'exit', 'no vlan interface eth0.21', 'vlan interface eth0.20', 'end')
            wait(lambda: ping('10.90.20.2'), 'Tag isolation and VLAN recreation passed')
            cli('r1', 'conf t', 'vlan interface eth1.20', 'interface eth1.20',
                'ip address 10.91.20.1/30', 'end')
            cli('r2', 'conf t', 'ip route 10.91.20.0/30 10.90.10.1', 'end')
            wait(lambda: '0% packet loss' in run('r2', 'ping', '-I', '10.90.10.2',
                 '-c', '1', '-W', '1', '10.91.20.2'), 'Inter-VLAN routing through R1 passed')
            # Creation while the parent is down must recover when it comes up.
            cli('r1', 'conf t', 'interface eth1', 'shutdown', 'exit',
                'vlan interface eth1.30', 'end')
            assert 'eth1.30' in links()
            cli('r1', 'conf t', 'interface eth1', 'no shutdown', 'end')
            wait(lambda: 'UP' in links()['eth1.30']['flags'], 'VLAN recovered after parent shutdown')
            cli('r1', 'conf t', 'no vlan interface eth1.30', 'end')
            # Explicit admin-down must survive unrelated configuration and backend restart.
            cli('r1', 'conf t', 'interface eth0.20', 'shutdown', 'end')
            cli('r1', 'conf t', 'snmp-server contact VLAN Test', 'end')
            assert 'UP' not in links()['eth0.20']['flags']
            pid = run('r1', 'pgrep', '-f', '^python3 /usr/lib/frr/weblab_lldp.py$')
            run('r1', 'kill', '-KILL', pid)
            wait(lambda: run('r1', 'pgrep', '-f', '^python3 /usr/lib/frr/weblab_lldp.py$') != pid
                 and 'eth0.20' in cli('r1', 'show vlan interfaces'), 'Backend recovered VLAN ownership')
            assert 'UP' not in links()['eth0.20']['flags']
            cli('r1', 'conf t', 'interface eth0.20', 'no shutdown', 'end')
            wait(lambda: ping('10.90.20.2'), 'FRR shutdown/no shutdown preserved')
            # SNMP can bind a VLAN address; IF-MIB exposes the VLAN interfaces.
            cli('r1', 'conf t', 'snmp-server community vlanMonitor ro',
                'snmp-server listen 10.90.10.1', 'snmp-server source 10.90.10.2/32',
                'snmp-server enable', 'end')
            def poll(oid, tool='snmpget'):
                return run('r2', tool, '-v', '2c', '-c', 'vlanMonitor', '-t', '1', '-r', '0',
                           '-On', '10.90.10.1', oid)
            wait(lambda: 'extensions (weblab.network)' in poll('.1.3.6.1.2.1.1.1.0'), 'SNMP listener on VLAN address')
            assert 'eth0.10' in poll('.1.3.6.1.2.1.31.1.1.1.1', 'snmpwalk')
            # Foreign names and replacement links must remain untouched.
            run('r1', 'ip', 'link', 'add', 'link', 'eth0', 'name', 'eth0.30', 'type', 'vlan', 'id', '30')
            rejected('vlan interface eth0.30')
            assert 'eth0.30' in links()
            run('r1', 'ip', 'link', 'delete', 'eth0.30')
            cli('r1', 'conf t', 'vlan interface eth0.30', 'end')
            run('r1', 'ip', 'link', 'delete', 'eth0.30')
            run('r1', 'ip', 'link', 'add', 'link', 'eth0', 'name', 'eth0.30', 'type', 'vlan', 'id', '31')
            rejected('no vlan interface eth0.30')
            assert links()['eth0.30']['linkinfo']['info_data']['id'] == 31
            run('r1', 'ip', 'link', 'delete', 'eth0.30')
            cli('r1', 'conf t', 'no vlan interface eth0.30', 'end')
            print('Foreign/replaced interface protection passed', flush=True)
            cli('r1', 'conf t', 'vlan interface eth1.50', 'interface eth1.50', 'shutdown', 'end')
            for node in ('r1', 'r2', 'r3'):
                cli(node, 'write memory')
            run('r1', 'vtysh', '-C', '-f', '/etc/frr/frr.conf')
            run('r1', 'vtysh', '-f', '/etc/frr/frr.conf')
            saved = run('r1', 'cat', '/etc/frr/frr.conf')
            # frr-reload removal/re-add ordering, including deferred addresses.
            without = '\n'.join(line for line in saved.splitlines() if line != 'vlan interface eth0.20')+'\n'
            for config, present in ((without, False), (saved, True)):
                run('r1', 'python3', '-c', 'import pathlib,sys;pathlib.Path("/tmp/reload.conf").write_text(sys.argv[1])', config)
                run('r1', '/usr/lib/frr/frr-reload.py', '--reload', '/tmp/reload.conf')
                assert ('eth0.20' in links()) == present
            wait(lambda: ping('10.90.20.2'), 'Reload removed/recreated VLAN and restored addresses')
            cli('r1', 'conf t', 'vlan interface eth0.40', 'end')  # Unsaved.
            lab.stop_all()
            result = lab_backup.create(lab)
            stream, _ = lab_backup.take(lab, result['url'].rsplit('/', 1)[1])
            shutil.rmtree(lab.directory/'nodes')
            with stream:
                lab_backup.restore(lab, stream, lab_server.run)
            lab.node('r1')['startup_config'] = 'hostname WRONG\n'
            lab.start_all()
            wait(lambda: ping() and ping('10.90.20.2'), 'ZIP restore recreated saved VLANs and forwarding')
            assert 'eth0.40' not in links()
            assert 'UP' not in links()['eth1.50']['flags']
            wait(lambda: 'Full' in cli('r1', 'show ip ospf neighbor'), 'Saved routing restored')
            wait(lambda: 'extensions (weblab.network)' in poll('.1.3.6.1.2.1.1.1.0'), 'SNMP restored on VLAN')
            print('FRR VLAN native tests passed', flush=True)
        finally:
            lab.stop_all()
            if lab.fabric:
                lab.fabric.close()
            lab_backup.expire(lab, all_files=True)
            lab.file_lock.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--vrrp', action='store_true')
    parser.add_argument('--release', action='store_true', help='Test the pinned public Weblab FRR image')
    args = parser.parse_args()
    main(frr.PUBLIC_IMAGE if args.release else frr.VRRP_IMAGE if args.vrrp else frr.VLAN_IMAGE)
