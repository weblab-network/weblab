#!/usr/bin/env python3
"""Opt-in native ARM64 core smoke: six disposable nodes, never the active lab.

Run sequentially on an ARM host with the images from docs/arm64.md installed.
Requires root, Docker, /dev/net/tun, openvswitch and the core runtime dependencies.
"""
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_capture
import frr
import lab_backup
import lab_server
import ll2s_device
import saved_config


def main():
    assert platform.machine() in ('aarch64', 'arm64'), 'Run natively on ARM64'
    with tempfile.TemporaryDirectory(prefix='weblab-arm64-native-') as directory:
        root = Path(directory)
        lab = lab_server.Lab(root/'data', root/'images', allow_untested_frr=True)
        topology = json.loads((lab_server.ROOT/'examples/arm64-ospf-rstp.json').read_text())
        ids = {n['name']: n['id'] for n in topology['nodes']}
        for index, node in enumerate(topology['nodes']):
            node['iol_id'] = 900 + index

        def run(name, *args):
            return lab_server.run('docker', 'exec', lab.runtime[ids[name]]['container'], *args)

        def cli(name, *commands):
            return run(name, 'vtysh', *(part for cmd in commands for part in ('-c', cmd)))

        def wait(check, label, timeout=90):
            deadline, last = time.monotonic()+timeout, ''
            while time.monotonic() < deadline:
                try:
                    if check():
                        print(label, flush=True)
                        return
                except lab_server.LabError as exc:
                    last = str(exc)
                time.sleep(1)
            raise AssertionError(label + ': ' + last)

        def ping():
            return '0% packet loss' in run('PC1', 'ping', '-c', '3', '-W', '1', '10.0.2.10')

        def port_status(port):
            return run('SW2', 'ovs-vsctl', '--db=unix:/run/ll2s/ovs/db.sock', 'get', 'Port', port, 'rstp_status')

        try:
            lab.save(topology)
            assert lab.container_only
            lab.start_all()
            print('Six native ARM64 devices started', flush=True)
            wait(lambda: any('Alternate' in port_status(p) for p in ('eth0', 'eth1')), 'LL2S RSTP blocks the redundant trunk')
            wait(lambda: 'Full' in cli('R1', 'show ip ospf neighbor'), 'FRR OSPF reaches Full through LL2S VLAN 100')
            wait(ping, 'Alpine PC-to-PC routed forwarding passed')
            wait(lambda: 'SW1' in cli('R1', 'show lldp neighbors'), 'Custom FRR LLDP discovers LL2S')
            cli('R1', 'configure terminal', 'snmp-server community armTest ro',
                'snmp-server listen 10.100.0.1', 'snmp-server source 10.100.0.2/32', 'snmp-server enable', 'end')
            wait(lambda: 'R1' in run('R2', 'snmpget', '-v2c', '-c', 'armTest', '-t', '1', '-r', '0',
                                     '-Oqv', '10.100.0.1', '.1.3.6.1.2.1.1.5.0'), 'Custom FRR SNMPv2c responds over the lab VLAN')

            console = console_capture.Console(lab.runtime[ids['R1']]['port'])
            observer = console_capture.Console(lab.runtime[ids['R1']]['port'])
            try:
                console.acquire()
                try:
                    observer.acquire()
                except ValueError as exc:
                    assert 'lock' in str(exc).lower()
                else:
                    raise AssertionError('Second console acquired the input lock')
                console.send(b'\rshow version\r')
                output, deadline = b'', time.monotonic()+15
                while b'FRRouting' not in output:
                    output += console.receive(deadline)
                print('Shared browser-console transport and input lock passed', flush=True)
            finally:
                console.close()
                observer.close()

            trunk_port = next(p for p in ('eth0', 'eth1') if 'Root' in port_status(p))
            link = next(l for l in lab.topology['links'] if l['b'] == {'node': ids['SW2'], 'port': trunk_port})
            capture = lab.start_capture(link['id'], 'arm-test', 60)
            wait(ping, 'Forwarding while capturing passed')
            pcap = lab.captures.pcap(capture['id'], 'arm-test')
            assert len(pcap) > 24
            (root/'capture.pcap').write_bytes(pcap)
            decoded = lab_server.run('tshark', '-r', str(root/'capture.pcap'), '-T', 'fields', '-e', '_ws.col.Protocol')
            assert 'ICMP' in decoded, decoded
            lab.captures.action(capture['id'], 'arm-test', 'close')
            print('Native TShark decoded the captured PC traffic', flush=True)
            lab.set_link_carrier(link['id'], {'side': 'b', 'up': False})
            wait(ping, 'RSTP failover restores PC forwarding after cable unplug')
            lab.set_link_carrier(link['id'], {'side': 'b', 'up': True})
            wait(lambda: 'Full' in cli('R1', 'show ip ospf neighbor'), 'OSPF healthy after cable restoration')
            wait(ping, 'Forwarding restored after reconnect')

            for name in ('R1', 'R2'):
                cli(name, 'write memory')
            lab.stop_all()
            exported = saved_config.export(lab, lab_server.run)
            assert all(n.get('startup_config') for n in exported['nodes'] if frr.is_frr(n) or ll2s_device.is_ll2s(n))
            result = lab_backup.create(lab)
            stream, _ = lab_backup.take(lab, result['url'].rsplit('/', 1)[1])
            shutil.rmtree(lab.directory/'nodes')
            with stream:
                lab_backup.restore(lab, stream, lab_server.run)
            lab.start_all()
            wait(ping, 'Native-image ZIP restore and saved-config forwarding passed')
            print('PASS: ARM64 core profile', flush=True)
        finally:
            lab.stop_all()
            lab_backup.expire(lab, all_files=True)
            assert not lab_server.run('docker', 'ps', '-aq', '--filter', f'label=iol.lab={lab.owner}')
            assert not lab_server.run('docker', 'network', 'ls', '-q', '--filter', f'label=iol.lab={lab.owner}')
            lab.file_lock.close()
            print('Disposable nodes and networks cleaned up', flush=True)


if __name__ == '__main__':
    main()
