#!/usr/bin/env python3
"""Opt-in real LLDP/OSPF/ZIP checks; separate Docker/TAP lab, no active-lab edits.

Build weblab-frr:10.7.1-lldp1 first. Run sequentially with other native suites.
"""
import argparse
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


def main(image=frr.LLDP_IMAGE):
    with tempfile.TemporaryDirectory(prefix='weblab-frr-lldp-') as directory:
        root = Path(directory)
        lab = lab_server.Lab(root/'data', root/'images', allow_untested_frr=image != frr.PUBLIC_IMAGE)

        def run(node, *args):
            return lab_server.run('docker', 'exec', lab.runtime[node]['container'], *args)

        def cli(node, *commands):
            return run(node, 'vtysh', *(part for command in commands for part in ('-c', command)))

        def wait(check, label, timeout=50):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if check():
                    print(label, flush=True)
                    return
                time.sleep(.5)
            raise AssertionError(label)

        try:
            nodes = []
            for i, port in ((1, 'eth0'), (2, 'eth1')):
                config = f'''frr defaults traditional
hostname LLDP-R{i}
service integrated-vtysh-config
interface {port}
 ip address 10.123.0.{i}/30
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
exit
router ospf
 ospf router-id 10.255.0.{i}
 network 10.123.0.0/30 area 0
exit
'''
                nodes.append({'id': f'r{i}', 'name': f'LLDP-R{i}', 'type': 'router',
                              'image': image, 'ethernet': 8 if i == 1 else 2,
                              'startup_config': config})
            lab.save({'name': 'Isolated LLDP regression', 'nodes': nodes, 'links': [
                {'id': 'transit', 'a': {'node': 'r1', 'port': 'eth0'},
                 'b': {'node': 'r2', 'port': 'eth1'}}]})
            lab.start_all()
            for node in ('r1', 'r2'):
                assert 'disabled; service stopped' in cli(node, 'show lldp')
                assert 'lldp run' not in cli(node, 'show running-config')
                cli(node, 'conf t', 'lldp r', 'end')
            wait(lambda: 'LLDP-R2' in cli('r1', 'sh lldp nei'), 'Real LLDP neighbor R2 discovered')
            detail = cli('r1', 'show lldp neighbors detail')
            assert 'eth0' in detail and 'eth1' in detail and 'Chassis ID:' in detail, detail
            assert 'Capabilities: Router' in detail, detail
            assert ('System description: Weblab FRR 10.7.1 - ' +
                    ('LLDP, SNMP, VLAN and IPv4 VRRP extensions' if image in (frr.VRRP_IMAGE, frr.PUBLIC_IMAGE) else 'LLDP, SNMP and VLAN extensions' if image == frr.VLAN_IMAGE else 'LLDP and SNMP extensions' if image == frr.SNMP_IMAGE else 'LLDP extension') +
                    ' (weblab.network)') in detail, detail
            assert 'Management addresses: 10.123.0.2' in detail, detail
            assert 'Advertised TTL: 120 seconds' in detail, detail
            assert 'Age: 0 day,' in detail, detail
            wait(lambda: 'LLDP-R1' in cli('r2', 'show lldp neighbors'), 'Reverse neighbor discovered')
            wait(lambda: 'Full' in cli('r1', 'show ip ospf neighbor'), 'OSPF still converges with LLDP')
            cli('r1', 'conf t', 'int eth0', 'no lldp rec', 'end')
            assert 'LLDP-R2' not in cli('r1', 'show lldp neighbors')
            cli('r1', 'conf t', 'int eth0', 'lldp rec', 'int eth1', 'no lldp tr', 'end')
            assert 'no lldp transmit' in cli('r1', 'show run')
            interfaces = json.loads(run('r1', 'lldpcli', '-u', '/run/weblab-lldpd.socket',
                                         '-f', 'json0', 'show', 'interfaces', 'ports', 'eth1'))
            assert interfaces['lldp'][0]['interface'][0]['status'][0]['value'] == 'RX only', interfaces
            # Normal save uses watchfrr's vtysh -w. Direct save must also include LLDP.
            cli('r1', 'write memory')
            run('r2', 'vtysh', '-w')
            assert 'lldp run' in run('r1', 'cat', '/etc/frr/frr.conf')
            assert 'no lldp transmit' in run('r1', 'cat', '/etc/frr/frr.conf')
            run('r1', 'vtysh', '-C', '-f', '/etc/frr/frr.conf')
            run('r1', 'vtysh', '-f', '/etc/frr/frr.conf')
            run('r1', 'vtysh', '--no-fork', '-f', '/etc/frr/frr.conf')
            run('r1', 'vtysh', '-b')  # FRR's parallel per-daemon startup path.
            run('r1', '/usr/lib/frr/frr-reload.py', '--test', '/etc/frr/frr.conf')
            # Exercise a real diff/apply, including a high-numbered physical port.
            saved_text = run('r1', 'cat', '/etc/frr/frr.conf')
            changed_text = saved_text + '\ninterface eth7\n no lldp receive\nexit\n'
            run('r1', 'python3', '-c',
                'import pathlib,sys; pathlib.Path("/etc/frr/frr.conf").write_text(sys.argv[1])',
                changed_text)
            run('r1', '/usr/lib/frr/frr-reload.py', '--reload', '/etc/frr/frr.conf')
            assert 'interface eth7\n no lldp receive' in cli('r1', 'show running-config')
            run('r1', 'python3', '-c',
                'import pathlib,sys; pathlib.Path("/etc/frr/frr.conf").write_text(sys.argv[1])',
                saved_text)
            run('r1', '/usr/lib/frr/frr-reload.py', '--reload', '/etc/frr/frr.conf')
            assert 'no lldp receive' not in cli('r1', 'show running-config')
            print('Native configuration replay, dry-run and both save paths passed', flush=True)
            # A daemon-filtered or non-integrated save cannot silently omit LLDP.
            before = run('r1', 'cat', '/etc/frr/frr.conf')
            for args in (('vtysh', '-d', 'ospfd', '-w'),
                         ('vtysh', '-c', 'conf t', '-c', 'no service integrated-vtysh-config',
                          '-c', 'end', '-c', 'write memory')):
                try:
                    run('r1', *args)
                except lab_server.LabError:
                    pass
                else:
                    raise AssertionError('Partial save unexpectedly succeeded')
                assert run('r1', 'cat', '/etc/frr/frr.conf') == before
            # An unavailable backend must not discard its configuration on save.
            before = run('r1', 'cat', '/etc/frr/frr.conf')
            run('r1', 'mv', '/run/frr/weblab-lldpd.vty', '/run/frr/weblab-lldpd.offline')
            try:
                for args in (('vtysh', '-w'), ('vtysh', '-c', 'write memory')):
                    try:
                        run('r1', *args)
                    except lab_server.LabError:
                        pass
                    else:
                        raise AssertionError('Save succeeded with missing LLDP backend')
                assert run('r1', 'cat', '/etc/frr/frr.conf') == before
                assert 'Full' in cli('r1', 'show ip ospf neighbor')
            finally:
                run('r1', 'mv', '/run/frr/weblab-lldpd.offline', '/run/frr/weblab-lldpd.vty')
            print('Unavailable backend preserves saved config and running OSPF', flush=True)
            # Service/backend recovery must preserve unsaved intent and routing.
            cli('r1', 'conf t', 'int eth1', 'no lldp receive', 'end')
            child = json.loads(run('r1', 'cat', '/run/frr/weblab-lldp-child.json'))['pid']
            run('r1', 'kill', '-KILL', str(child))
            def healthy():
                try:
                    return 'enabled; service running' in cli('r1', 'show lldp')
                except lab_server.LabError:
                    return False
            wait(lambda: healthy() and json.loads(run('r1', 'cat',
                 '/run/frr/weblab-lldp-child.json'))['pid'] != child,
                 'lldpd recovered independently of routing')
            backend_pid = run('r1', 'pgrep', '-f', '^python3 /usr/lib/frr/weblab_lldp.py$')
            run('r1', 'kill', '-KILL', backend_pid)
            wait(lambda: healthy() and run('r1', 'pgrep', '-f',
                 '^python3 /usr/lib/frr/weblab_lldp.py$') != backend_pid,
                 'CLI backend recovered its unsaved intent')
            assert 'no lldp receive' in cli('r1', 'show running-config')
            assert 'Full' in cli('r1', 'show ip ospf neighbor')
            cli('r1', 'conf t', 'no lldp run', 'end')
            assert 'disabled; service stopped' in cli('r1', 'show lldp')
            lab.stop_all()
            saved = frr.read_config(frr.config_path(lab.node('r1'), lab.node_dir('r1')))
            assert b'lldp run' in saved  # Unsaved disable must not replace startup intent.
            result = lab_backup.create(lab)
            stream, _ = lab_backup.take(lab, result['url'].rsplit('/', 1)[1])
            shutil.rmtree(lab.directory/'nodes')
            with stream:
                lab_backup.restore(lab, stream, lab_server.run)
            lab.node('r1')['startup_config'] = 'hostname WRONG\n'
            lab.start_all()
            wait(lambda: 'LLDP-R2' in cli('r1', 'show lldp neighbors'), 'ZIP restore rediscovered real neighbor')
            assert 'no lldp transmit' in cli('r1', 'show running-config')
            wait(lambda: 'Full' in cli('r1', 'show ip ospf neighbor'), 'OSPF restored alongside LLDP')
            print('FRR LLDP native tests passed', flush=True)
        finally:
            lab.stop_all()
            if lab.fabric:
                lab.fabric.close()
            lab_backup.expire(lab, all_files=True)
            lab.file_lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snmp', action='store_true')
    parser.add_argument('--vlan', action='store_true', help='Test the LLDP/SNMP/VLAN extension image')
    parser.add_argument('--vrrp', action='store_true', help='Test the IPv4 VRRP extension image')
    parser.add_argument('--release', action='store_true', help='Test the pinned public Weblab FRR image')
    args = parser.parse_args()
    main(frr.PUBLIC_IMAGE if args.release else frr.VRRP_IMAGE if args.vrrp else frr.VLAN_IMAGE if args.vlan else frr.SNMP_IMAGE if args.snmp else frr.LLDP_IMAGE)
