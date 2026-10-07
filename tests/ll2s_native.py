#!/usr/bin/env python3
"""Disposable LL2S triangle: Docker/TAP, released LL2S image, Alpine and host OVS required.
Run sequentially: python3 tests/ll2s_native.py. Never touches an existing lab.
Add --monitoring for LLDP/SNMP-capable LL2S builds: discovery/polling and ZIP restore.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_capture
import lab_backup
import lab_server
import ll2s_device
import saved_config


def topology(monitoring=False):
    nodes = []
    for i in (1, 2, 3):
        config = f'hostname SW{i}\nspanning-tree mode rstp\nspanning-tree priority {4096*i}\nvlan 1,10,20\n'
        if monitoring:
            config += (f'lldp run\nmanagement vlan 10\nmanagement ip address 10.0.10.{100+i}/24\n'
                       'snmp-server community lab-test ro\nsnmp-server source 10.0.10.0/24\n')
        for port in (0, 1):
            config += f'interface eth{port}\n switchport mode trunk\n switchport trunk allowed vlan 10,20\nexit\n'
        config += f'interface eth2\n switchport mode access\n switchport access vlan {20 if i == 2 else 10}\n spanning-tree portfast\nexit\n'
        nodes.append({'id': f's{i}', 'name': f'SW{i}', 'type': 'switch', 'image': ll2s_device.IMAGE,
                      'ethernet': 3, 'startup_config': config, 'x': 300+400*(i-1), 'y': 250})
        nodes.append({'id': f'p{i}', 'name': f'PC{i}', 'type': 'pc', 'ipv4': f'10.0.10.{i}/24',
                      'x': 300+400*(i-1), 'y': 550})
    links = [{'id': 's12', 'a': {'node': 's1', 'port': 'eth0'}, 'b': {'node': 's2', 'port': 'eth0'}},
             {'id': 's23', 'a': {'node': 's2', 'port': 'eth1'}, 'b': {'node': 's3', 'port': 'eth0'}},
             {'id': 's13', 'a': {'node': 's1', 'port': 'eth1'}, 'b': {'node': 's3', 'port': 'eth1'}}]
    links += [{'id': f'access{i}', 'a': {'node': f's{i}', 'port': 'eth2'},
               'b': {'node': f'p{i}', 'port': 'eth0'}} for i in (1, 2, 3)]
    return {'name': 'LL2S RSTP and VLANs', 'nodes': nodes, 'links': links}


def main(monitoring=False):
    with tempfile.TemporaryDirectory(prefix='weblab-ll2s-native-') as tmp:
        lab = lab_server.Lab(Path(tmp)/'data', Path(tmp)/'images')
        def wait(check, label, timeout=45):
            deadline = time.monotonic()+timeout
            while time.monotonic() < deadline:
                if check():
                    print(label, flush=True)
                    return
                time.sleep(.5)
            raise AssertionError(label)
        def ovs(node, *args):
            return lab_server.run('docker', 'exec', lab.runtime[node]['container'], 'ovs-vsctl',
                                  '--db=unix:/run/ll2s/ovs/db.sock', *args)
        def ping(dest):
            try:
                lab_server.run('docker', 'exec', lab.runtime['p1']['container'], 'ping', '-c', '1', '-W', '1', dest)
                return True
            except lab_server.LabError:
                return False
        def poll(expected_name):
            try:
                result = lab_server.run('docker', 'exec', lab.runtime['s3']['container'],
                                        'snmpget', '-v2c', '-c', 'lab-test', '-t', '1', '-r', '0',
                                        '-Oqv', '10.0.10.101', '.1.3.6.1.2.1.1.5.0')
                return result.strip().strip('"') == expected_name
            except lab_server.LabError:
                return False
        def discovered(first_name):
            expected = {'s1': {('eth0', 'SW2', 'eth0'), ('eth1', 'SW3', 'eth1')},
                        's2': {('eth0', first_name, 'eth0'), ('eth1', 'SW3', 'eth0')},
                        's3': {('eth0', 'SW2', 'eth1'), ('eth1', first_name, 'eth1')}}
            for node, peers in expected.items():
                try:
                    doc = json.loads(lab_server.run('docker', 'exec', lab.runtime[node]['container'],
                         'lldpcli', '-u', '/run/ll2s/lldp.sock', '-f', 'json0', 'show', 'neighbors', 'details'))
                except lab_server.LabError:
                    return False
                actual = {(p['name'], p['chassis'][0]['name'][0]['value'], p['port'][0]['id'][0]['value'])
                          for group in doc['lldp'] for p in group.get('interface', [])}
                if actual != peers:
                    return False
            return True
        def read_prompt(console, pattern, timeout=15):
            output = b''
            deadline = time.monotonic()+timeout
            while not pattern.search(output):
                output += console.receive(deadline)
            return output
        def cli(console, command):
            console.send(command.encode()+b'\r')
            output = read_prompt(console, re.compile(rb'(?m)^[^\r\n]*# '))
            assert b'% ' not in output, output
            return output
        try:
            data = topology(monitoring)
            for index, node in enumerate(data["nodes"]):
                node["iol_id"] = 900 + index
            lab.save(data)
            lab.start_all()
            wait(lambda: 'Alternate' in ovs('s3', 'get', 'Port', 'eth0', 'rstp_status'), 'RSTP redundant path blocks')
            wait(lambda: ping('10.0.10.3'), 'Tagged VLAN 10 forwarding between PCs')
            assert not ping('10.0.10.2'), 'VLAN 20 leaked into VLAN 10'
            print('VLAN isolation passed', flush=True)
            if monitoring:
                wait(lambda: discovered('SW1'), 'LLDP neighbors and ports, including RSTP blocked link', timeout=65)
                wait(lambda: poll('SW1'), 'SNMPv2c polls switch management through lab VLAN 10')
            c1 = console_capture.Console(lab.runtime['s1']['port'])
            c2 = console_capture.Console(lab.runtime['s1']['port'])
            try:
                c1.acquire()
                try:
                    c2.acquire()
                except ValueError as error:
                    assert 'lock' in str(error).lower()
                else:
                    raise AssertionError('Second station obtained locked console')
                cli(c1, '')
                assert b'Linux Layer 2 Switch' in cli(c1, 'show version')
                for command in ('configure terminal', 'hostname SAVED', 'commit', 'end', 'write memory',
                                'configure terminal', 'hostname UNSAVED', 'commit', 'end'):
                    cli(c1, command)
                c1.send(b'exit\r')
                read_prompt(c1, re.compile(rb'/ # '), timeout=10)
                c1.send(b'll2sh\r')
                read_prompt(c1, re.compile(rb'UNSAVED# '), timeout=10)
                print('Shared console, input locks, commit/save and shell return passed', flush=True)
            finally:
                c1.close()
                c2.close()
            # Wait for the deliberate configuration commits to reconverge first.
            wait(lambda: ping('10.0.10.3'), 'Forwarding after commit')
            lab.set_link_carrier('s13', {'side': 'a', 'up': False})
            flags = json.loads(lab_server.run('docker', 'exec', lab.runtime['s1']['container'], 'ip', '-j', 'link', 'show', 'eth1'))[0]['flags']
            assert 'UP' in flags and 'LOWER_UP' not in flags, flags
            wait(lambda: ping('10.0.10.3'), 'RSTP alternate path recovers after cable unplug', timeout=10)
            lab.set_link_carrier('s13', {'side': 'a', 'up': True})
            lab.stop_all()
            exported = saved_config.export(lab, lab_server.run)
            assert 'hostname SAVED\n' in exported['nodes'][0]['startup_config']
            assert 'UNSAVED' not in exported['nodes'][0]['startup_config']
            result = lab_backup.create(lab)
            stream, _ = lab_backup.take(lab, result['url'].rsplit('/', 1)[1])
            shutil.rmtree(lab.directory/'nodes')
            with stream:
                lab_backup.restore(lab, stream, lab_server.run)
            lab.node('s1')['startup_config'] = 'hostname WRONG\n'
            lab.start_all()
            config = lab_server.run('docker', 'exec', lab.runtime['s1']['container'], 'cat', '/run/ll2s/running.json')
            assert json.loads(config)['hostname'] == 'SAVED', config
            wait(lambda: ping('10.0.10.3'), 'ZIP-restored saved configuration forwards traffic')
            if monitoring:
                wait(lambda: discovered('SAVED'), 'ZIP restores LLDP settings and saved hostname', timeout=65)
                wait(lambda: poll('SAVED'), 'ZIP restores management address and SNMP community')
            print('PASS: saved precedence, unsaved exclusion, native RSTP/VLANs and restore', flush=True)
        finally:
            lab.stop_all()
            lab_backup.expire(lab, all_files=True)
            assert not lab.runtime
            assert not lab_server.run('docker', 'ps', '-aq', '--filter', f'label=iol.lab={lab.owner}')
            assert not lab_server.run('docker', 'network', 'ls', '-q', '--filter', f'label=iol.lab={lab.owner}')
            lab.file_lock.close()
            print('Disposable LL2S resources cleaned', flush=True)


def recovery():
    """Emulate lost containers/TAPs with surviving Docker networks, not a host reboot."""
    with tempfile.TemporaryDirectory(prefix='weblab-ll2s-recovery-') as tmp:
        directory, images = Path(tmp)/'data', Path(tmp)/'images'
        lab = lab_server.Lab(directory, images)
        try:
            lab.save({'name': 'Disposable recovery', 'nodes': [
                {'id': 's', 'iol_id': 930, 'name': 'SAVED', 'type': 'switch',
                 'image': ll2s_device.IMAGE, 'ethernet': 2}], 'links': []})
            lab.start('s')
            lab.stop('s')
            config = ll2s_device.config_path(lab.node('s'), lab.node_dir('s'))
            saved = (config.read_bytes(), ll2s_device.identity_path(config).read_bytes())
            # Exercise both current IDs/aliases and the pre-fix journal format.
            for legacy in (False, True):
                lab.start('s')
                runtime = lab.runtime['s']
                if legacy:
                    runtime.pop('ll2s_container_id')
                    for port in runtime['ll2s_ports']:
                        port.pop('network_id')
                        port.pop('tap_alias')
                    lab.journal()
                for key in ('console', 'bridge'):
                    lab.terminate(runtime[key])
                lab_server.run('docker', 'rm', '-f', runtime['container'])
                for port in runtime['ll2s_ports']:
                    lab_server.run('ip', 'link', 'delete', port['tap'])
                    info = json.loads(lab_server.run('docker', 'network', 'inspect', port['network']))[0]
                    assert not info['Containers']
                lab.fabric.close()
                lab.fabric = None
                lab.file_lock.close()
                lab = lab_server.Lab(directory, images)
                assert not lab.runtime
                assert not lab_server.run('docker', 'network', 'ls', '-q', '--filter', f'label=iol.lab={lab.owner}')
                assert saved == (config.read_bytes(), ll2s_device.identity_path(config).read_bytes())
                # Reuses exactly the same IPAM subnets: orphan reservations must be gone.
                lab.start('s')
                running = json.loads(lab_server.run('docker', 'exec', lab.runtime['s']['container'],
                                                    'cat', '/run/ll2s/running.json'))
                assert running['hostname'] == 'SAVED'
                lab.stop('s')
                lab.stop('s')
                print(f"PASS: {'legacy' if legacy else 'current'} journal recovery, saved config and subnet reuse", flush=True)
        finally:
            lab.stop_all()
            assert not lab_server.run('docker', 'ps', '-aq', '--filter', f'label=iol.lab={lab.owner}')
            assert not lab_server.run('docker', 'network', 'ls', '-q', '--filter', f'label=iol.lab={lab.owner}')
            lab.file_lock.close()
            print('Disposable recovery resources cleaned', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monitoring', action='store_true', help='Require LLDP and management/SNMP support')
    parser.add_argument('--recovery-only', action='store_true', help='Test missing-container startup recovery')
    args = parser.parse_args()
    if args.recovery_only:
        recovery()
    else:
        main(args.monitoring)
