#!/usr/bin/env python3
"""Opt-in Docker/TAP FRR integration test; disposable lab, requires root and Alpine.
Run sequentially: python3 tests/frr_native.py
"""
import json
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_capture
import frr
import lab_backup
import lab_server
import saved_config


def main():
    with tempfile.TemporaryDirectory(prefix='weblab-frr-native-') as tmp:
        lab = lab_server.Lab(Path(tmp)/'data', Path(tmp)/'images')
        def cli(node, *commands):
            args = [part for command in commands for part in ('-c', command)]
            return lab_server.run('docker', 'exec', lab.runtime[node]['container'], 'vtysh', *args)
        def wait(check, label, timeout=60):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if check():
                    print(label, flush=True)
                    return
                time.sleep(1)
            raise AssertionError(label)
        try:
            nodes = []
            for i in (1, 2):
                peer = 3-i
                config = f'''frr defaults traditional
hostname R{i}
service integrated-vtysh-config
interface eth0
 ip address 10.0.12.{i}/30
 ipv6 address 2001:db8:12::{i}/64
 ipv6 ospf6 area 0
 ipv6 ospf6 network point-to-point
 ipv6 ospf6 hello-interval 1
 ipv6 ospf6 dead-interval 4
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
!
interface eth1
 ip address 10.0.{i}.1/24
!
interface lo
 ip address 10.255.0.{i}/32
 ipv6 address 2001:db8:{i}::{i}/128
 ipv6 ospf6 area 0
!
router ospf
 ospf router-id 10.255.0.{i}
 network 10.0.0.0/16 area 0
!
router ospf6
 ospf6 router-id 10.255.0.{i}
!
router bgp {65000+i}
 bgp router-id 10.255.0.{i}
 no bgp ebgp-requires-policy
 neighbor 10.0.12.{peer} remote-as {65000+peer}
 address-family ipv4 unicast
  network 10.255.0.{i}/32
 exit-address-family
!
'''
                nodes.append({'id':f'r{i}', 'name':f'R{i}', 'type':'router','image':frr.IMAGE,
                              'ethernet':2, 'startup_config':config})
                nodes.append({'id':f'p{i}','name':f'PC{i}','type':'pc','ipv4':f'10.0.{i}.10/24','gateway':f'10.0.{i}.1'})
            links = [{'id':'transit','a':{'node':'r1','port':'eth0'},'b':{'node':'r2','port':'eth0'}}]
            links += [{'id':f'access{i}','a':{'node':f'r{i}','port':'eth1'},'b':{'node':f'p{i}','port':'eth0'}} for i in (1,2)]
            lab.save({'name':'FRR integration','nodes':nodes,'links':links})
            lab.start_all()
            wait(lambda: 'Full' in cli('r1','show ip ospf neighbor'), 'OSPF adjacency established')
            wait(lambda: 'Established' in cli('r1','show bgp neighbor 10.0.12.2'), 'BGP session established')
            wait(lambda: '10.0.2.0/24' in cli('r1','show ip route ospf'), 'OSPF remote LAN route installed')
            wait(lambda: 'Full' in cli('r1','show ipv6 ospf6 neighbor'), 'OSPFv3 adjacency established')
            wait(lambda: '2001:db8:2::2/128' in cli('r1','show ipv6 route ospf6'), 'OSPFv3 loopback route installed')
            lab_server.run('docker','exec',lab.runtime['r1']['container'],'ping','-6','-c','2','-W','2','2001:db8:2::2')
            print('IPv6 routing passed',flush=True)
            pc = lab.runtime['p1']['container']
            lab_server.run('docker','exec',pc,'ping','-c','3','-W','2','10.0.2.10')
            print('PC-to-PC forwarding through FRR routers passed',flush=True)
            # Existing shared transport still grants one input owner and observers.
            c1 = console_capture.Console(lab.runtime['r1']['port'])
            c2 = console_capture.Console(lab.runtime['r1']['port'])
            try:
                c1.acquire()
                try:
                    c2.acquire()
                except ValueError as error:
                    assert 'lock' in str(error).lower()
                else:
                    raise AssertionError('Second console acquired locked input')
                c1.send(b'\rshow version\r')
                output=b''; deadline=time.monotonic()+10
                while b'FRRouting' not in output: output+=c1.receive(deadline)
                print('Shared vtysh console and input lock passed',flush=True)
            finally:
                c1.close();c2.close()
            lab.set_link_traffic('transit', {'blocked_a_to_b':True,'blocked_b_to_a':False})
            wait(lambda: 'Full' not in cli('r2','show ip ospf neighbor'), 'One-way loss interrupted OSPF',timeout=15)
            assert lab.link_states()['transit']['blocked_b_to_a'] is False
            lab.set_link_traffic('transit', {'blocked_a_to_b':False,'blocked_b_to_a':False})
            wait(lambda: 'Full' in cli('r1','show ip ospf neighbor'), 'OSPF recovered after traffic restore')
            lab.set_link_carrier('transit', {'side':'a','up':False})
            def flags_now():
                return json.loads(lab_server.run('docker','exec',lab.runtime['r1']['container'],'ip','-j','link','show','eth0'))[0]['flags']
            wait(lambda: 'LOWER_UP' not in flags_now(), 'FRR sees physical carrier loss', timeout=5)
            flags=flags_now()
            assert 'UP' in flags and 'LOWER_UP' not in flags, flags
            assert lab.link_states()['transit']['carrier_a']=='down'
            lab.set_link_carrier('transit', {'side':'a','up':True})
            wait(lambda: 'Full' in cli('r1','show ip ospf neighbor'), 'Carrier unplug/reconnect recovered OSPF')
            for i in (1,2): cli(f'r{i}','write memory')
            cli('r1','configure terminal','interface eth1','description UNSAVED-CHANGE','end')
            lab.stop_all()
            exported=saved_config.export(lab,lab_server.run)
            assert all('router ospf' in n['startup_config'] for n in exported['nodes'] if frr.is_frr(n))
            original={n['id']: frr.read_config(frr.config_path(n,lab.node_dir(n['id']))) for n in lab.topology['nodes'] if frr.is_frr(n)}
            assert b'UNSAVED-CHANGE' not in original['r1']
            result=lab_backup.create(lab)
            stream,_=lab_backup.take(lab,result['url'].rsplit('/',1)[1])
            # Remove saved state, then exercise actual transactional restore.
            shutil.rmtree(lab.directory/'nodes')
            with stream: lab_backup.restore(lab,stream,lab_server.run)
            for node in lab.topology['nodes']:
                if frr.is_frr(node):
                    assert frr.read_config(frr.config_path(node,lab.node_dir(node['id'])))==original[node['id']]
                    node['startup_config']='hostname WRONG\n'
            lab.start_all()
            wait(lambda: 'Full' in cli('r1','show ip ospf neighbor'), 'ZIP-restored saved config takes precedence over snippets')
            assert 'hostname R1' in cli('r1','show running-config')
            wait(lambda: '10.0.2.0/24' in cli('r1','show ip route ospf'), 'Restored remote LAN route installed')
            lab_server.run('docker','exec',lab.runtime['p1']['container'],'ping','-c','3','-W','2','10.0.2.10')
            print('PASS: restored forwarding, config export, unsaved-change exclusion',flush=True)
            lab.stop_all()
            recovery_dir=Path(tmp)/'recovery'
            script = """
import os,sys
from pathlib import Path
import lab_server,frr
lab=lab_server.Lab(Path(sys.argv[1]),Path(sys.argv[2]))
lab.save({'name':'Crash recovery','nodes':[{'id':'r','name':'Recovery','type':'router','image':frr.IMAGE,'ethernet':1}],'links':[]})
lab.start('r')
lab_server.run('docker','exec',lab.runtime['r']['container'],'vtysh','-c','configure terminal','-c','interface eth0','-c','description RECOVERY-MARKER','-c','end','-c','write memory')
os._exit(0)
"""
            subprocess.run([sys.executable,'-c',script,str(recovery_dir),str(lab.image_dir)],check=True,cwd=lab_server.ROOT,timeout=90)
            recovered=lab_server.Lab(recovery_dir,lab.image_dir)
            try:
                assert not recovered.runtime
                assert b'description RECOVERY-MARKER' in frr.read_config(frr.config_path(recovered.node('r'),recovered.node_dir('r')))
                assert not lab_server.run('docker','ps','-aq','--filter',f'label=iol.lab={recovered.owner}')
                assert not lab_server.run('docker','network','ls','-q','--filter',f'label=iol.lab={recovered.owner}')
            finally:
                recovered.file_lock.close()
            print('Server-crash recovery preserved saved config and cleaned resources',flush=True)
        finally:
            lab.stop_all()
            lab_backup.expire(lab,all_files=True)
            assert not lab.runtime
            assert not lab_server.run('docker','ps','-aq','--filter',f'label=iol.lab={lab.owner}')
            assert not lab_server.run('docker','network','ls','-q','--filter',f'label=iol.lab={lab.owner}')
            lab.file_lock.close()
            print('Disposable resources cleaned up',flush=True)

if __name__=='__main__':
    main()
