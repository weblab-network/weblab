#!/usr/bin/env python3
"""Real SNMPv2c/AgentX and persistence in a disposable two-router lab.

Requires the locally built LLDP/SNMP image, root, Docker and TAP access.
Run sequentially with other node-launching suites. Never touches the active lab.
"""
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

DESCRIPTION = 'Weblab FRR 10.7.1 - LLDP and SNMP extensions (weblab.network)'
SYS_DESCR = '.1.3.6.1.2.1.1.1.0'
SYS_CONTACT = '.1.3.6.1.2.1.1.4.0'
IF_NAME = '.1.3.6.1.2.1.31.1.1.1.1'
LLDP_NAMES = '.1.0.8802.1.1.2.1.4.1.1.9'


def main(image=frr.SNMP_IMAGE):
    description = DESCRIPTION.replace("LLDP and SNMP", "LLDP, SNMP and VLAN") if image == frr.VLAN_IMAGE else DESCRIPTION
    if image in (frr.VRRP_IMAGE, frr.PUBLIC_IMAGE):
        description = DESCRIPTION.replace("LLDP and SNMP", "LLDP, SNMP, VLAN and IPv4 VRRP")
    with tempfile.TemporaryDirectory(prefix='weblab-frr-snmp-') as directory:
        root = Path(directory)
        lab = lab_server.Lab(root/'data', root/'images', allow_untested_frr=image != frr.PUBLIC_IMAGE)

        def run(node, *args):
            return lab_server.run('docker', 'exec', lab.runtime[node]['container'], *args)

        def cli(node, *commands):
            return run(node, 'vtysh', *(part for cmd in commands for part in ('-c', cmd)))

        def poll(oid=SYS_DESCR, tool='snmpget', community='labMonitor', version='2c', extra=()):
            return run('r2', tool, '-v', version, '-c', community, '-t', '0.5', '-r', '0',
                       '-On', '10.88.0.1', oid, *extra)

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

        def denied(**kwargs):
            try:
                poll(**kwargs)
            except lab_server.LabError:
                return
            raise AssertionError('Unauthorized SNMP request succeeded')

        try:
            nodes = []
            for i in (1, 2):
                config = f'''hostname SNMP-R{i}
service integrated-vtysh-config
interface eth0
 ip address 10.88.0.{i}/30
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
exit
router ospf
 ospf router-id 10.255.1.{i}
 network 10.88.0.0/30 area 0
exit
lldp run
'''
                nodes.append({'id': f'r{i}', 'name': f'SNMP-R{i}', 'type': 'router',
                              'image': image, 'ethernet': 2, 'startup_config': config})
            lab.save({'name': 'Disposable SNMP regression', 'nodes': nodes, 'links': [
                {'id': 'transit', 'a': {'node': 'r1', 'port': 'eth0'},
                 'b': {'node': 'r2', 'port': 'eth0'}}]})
            lab.start_all()
            assert 'disabled' in cli('r1', 'show snmp')
            denied()
            cli('r1', 'conf t', 'snmp-s community labMonitor ro',
                'snmp-s listen 10.88.0.1', 'snmp-s source 10.88.0.2/32',
                'snmp-s contact Lab Operations', 'snmp-s location Rack A', 'snmp-s en', 'end')
            wait(lambda: description in poll(), 'SNMPv2c sysDescr received over the lab link')
            assert 'Lab Operations' in poll(SYS_CONTACT)
            assert 'Rack A' in poll('.1.3.6.1.2.1.1.6.0')
            assert 'SNMP-R1' in poll('.1.3.6.1.2.1.1.5.0')
            assert 'eth0' in poll(IF_NAME, 'snmpwalk')
            assert 'Counter32:' in poll('.1.3.6.1.2.1.2.2.1.10', 'snmpwalk')
            assert 'labMonitor' not in cli('r1', 'show snmp')
            denied(community='wrongCommunity')
            denied(version='1')
            denied(oid=SYS_CONTACT, tool='snmpset', extra=('s', 'UNAUTHORIZED'))
            assert 'Lab Operations' in poll(SYS_CONTACT)
            cli('r1', 'conf t', 'snmp-server source 192.0.2.0/24', 'end')
            wait(lambda: 'running' in cli('r1', 'show snmp'), 'Restricted-source listener restarted')
            denied()
            cli('r1', 'conf t', 'snmp-server source 10.88.0.2/32', 'end')
            wait(lambda: description in poll(), 'Source allowlist restored')
            print('Read-only access, v1 denial, community and source restrictions passed', flush=True)
            wait(lambda: 'SNMP-R2' in poll(LLDP_NAMES, 'snmpwalk'), 'LLDP-MIB remote system name received through AgentX', 90)
            assert description in cli('r2', 'show lldp neighbors detail')
            wait(lambda: 'Full' in cli('r1', 'show ip ospf neighbor'), 'OSPF remains established')
            cli('r1', 'conf t', 'no lldp run', 'end')
            assert description in poll()
            cli('r1', 'conf t', 'lldp run', 'end')
            cli('r1', 'conf t', 'no snmp-server enable', 'end')
            denied()
            assert 'service running' in cli('r1', 'show lldp')
            cli('r1', 'conf t', 'snmp-server enable', 'end')
            wait(lambda: description in poll(), 'SNMP enable/disable is independent of LLDP')

            # Crash service then shared backend; polling must recover unsaved intent.
            cli('r1', 'conf t', 'snmp-server contact Unsaved Operations', 'end')
            pid = json.loads(run('r1', 'cat', '/run/frr/weblab-snmp-child.json'))['pid']
            run('r1', 'kill', '-KILL', str(pid))
            wait(lambda: 'Unsaved Operations' in poll(SYS_CONTACT), 'Net-SNMP recovered independently')
            pid = run('r1', 'pgrep', '-f', '^python3 /usr/lib/frr/weblab_lldp.py$')
            run('r1', 'kill', '-KILL', pid)
            wait(lambda: 'Unsaved Operations' in poll(SYS_CONTACT) and
                 run('r1', 'pgrep', '-f', '^python3 /usr/lib/frr/weblab_lldp.py$') != pid,
                 'Backend recovered unsaved SNMP settings')
            wait(lambda: 'SNMP-R2' in poll(LLDP_NAMES, 'snmpwalk'), 'AgentX reconnected after recovery', 90)
            assert 'Full' in cli('r1', 'show ip ospf neighbor')

            # Preserve only saved configuration, including SNMP in sorted replay.
            cli('r1', 'conf t', 'snmp-server contact Saved Operations', 'end', 'write memory')
            cli('r2', 'write memory')
            run('r1', 'vtysh', '-C', '-f', '/etc/frr/frr.conf')
            run('r1', 'vtysh', '-f', '/etc/frr/frr.conf')
            run('r1', '/usr/lib/frr/frr-reload.py', '--test', '/etc/frr/frr.conf')
            cli('r1', 'conf t', 'snmp-server contact Discard Me', 'end')
            run('r1', '/usr/lib/frr/frr-reload.py', '--reload', '/etc/frr/frr.conf')
            wait(lambda: 'Saved Operations' in poll(SYS_CONTACT), 'Real reload restored saved SNMP settings')
            saved_text = run('r1', 'cat', '/etc/frr/frr.conf')
            without_snmp = '\n'.join(line for line in saved_text.splitlines()
                                     if not line.startswith('snmp-server ')) + '\n'
            for contents, enabled in ((without_snmp, False), (saved_text, True)):
                run('r1', 'python3', '-c',
                    'import pathlib,sys; pathlib.Path("/etc/frr/frr.conf").write_text(sys.argv[1])',
                    contents)
                run('r1', '/usr/lib/frr/frr-reload.py', '--reload', '/etc/frr/frr.conf')
                if enabled:
                    wait(lambda: 'Saved Operations' in poll(SYS_CONTACT), 'Reload re-enabled SNMP from saved intent')
                else:
                    assert 'disabled' in cli('r1', 'show snmp')
                    assert 'snmp-server' not in cli('r1', 'show running-config')
                    denied()
            print('Reload can remove all SNMP settings and restore them', flush=True)
            cli('r1', 'conf t', 'no snmp-server enable', 'end')
            lab.stop_all()
            result = lab_backup.create(lab)
            stream, _ = lab_backup.take(lab, result['url'].rsplit('/', 1)[1])
            shutil.rmtree(lab.directory/'nodes')
            with stream:
                lab_backup.restore(lab, stream, lab_server.run)
            lab.node('r1')['startup_config'] = 'hostname WRONG\n'
            lab.start_all()
            wait(lambda: 'Saved Operations' in poll(SYS_CONTACT), 'ZIP restore recovered saved SNMP state')
            wait(lambda: 'SNMP-R2' in poll(LLDP_NAMES, 'snmpwalk'), 'ZIP restore recovered LLDP-MIB', 90)
            wait(lambda: 'Full' in cli('r1', 'show ip ospf neighbor'), 'OSPF restored alongside SNMP')
            print('FRR SNMP native tests passed', flush=True)
        finally:
            lab.stop_all()
            if lab.fabric:
                lab.fabric.close()
            lab_backup.expire(lab, all_files=True)
            lab.file_lock.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--vlan', action='store_true')
    parser.add_argument('--vrrp', action='store_true', help='Test the IPv4 VRRP extension image')
    parser.add_argument('--release', action='store_true', help='Test the pinned public Weblab FRR image')
    args = parser.parse_args()
    main(frr.PUBLIC_IMAGE if args.release else frr.VRRP_IMAGE if args.vrrp else frr.VLAN_IMAGE if args.vlan else frr.SNMP_IMAGE)
