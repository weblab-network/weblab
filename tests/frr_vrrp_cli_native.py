#!/usr/bin/env python3
"""Disposable FRR/LL2S: CLI-only IPv4 VRRP, real gateway failover and persistence."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import frr
import lab_backup
import lab_server
import ll2s_device


def main(image=frr.VRRP_IMAGE):
    with tempfile.TemporaryDirectory(prefix='weblab-frr-vrrp-') as directory:
        root=Path(directory)
        lab=lab_server.Lab(root/'data',root/'images',allow_untested_frr=image != frr.PUBLIC_IMAGE)
        def run(node,*args):
            return lab_server.run('docker','exec',lab.runtime[node]['container'],*args)
        def cli(node,*commands):
            return run(node,'vtysh',*(part for cmd in commands for part in ('-c',cmd)))
        def wait(check,label,timeout=60):
            deadline=time.monotonic()+timeout;last=''
            while time.monotonic()<deadline:
                try:
                    if check():
                        print(label,flush=True);return
                except (lab_server.LabError,ValueError,KeyError) as e:
                    last=str(e)
                time.sleep(.5)
            for node in ('r1','r2'):
                print(node,cli(node,'show vrrp platform','show vrrp'),flush=True)
            raise AssertionError(label+': '+last)
        def states(node):
            return {g['vrid']:g['v4']['status'] for g in json.loads(cli(node,'show vrrp json'))}
        def links(node):
            return {p['ifname']:p for p in json.loads(run(node,'ip','-d','-j','address','show'))}
        def ping(node,dest):
            run(node,'ping','-c','1','-W','1',dest);return True
        def addresses(node,name):
            return [a.get('local') for a in links(node).get(name,{}).get('addr_info',[])]
        try:
            nodes=[]
            for i in (1,2):
                config=f'hostname VRRP-R{i}\nservice integrated-vtysh-config\nlldp run\n'
                for vid in (10,20,30):
                    parent='eth1' if vid==30 else f'eth0.{vid}'
                    if vid!=30:config+=f'vlan interface {parent}\n'
                    config+=(f'interface {parent}\n ip address 10.77.{vid}.{i+1}/24\n'
                             f' vrrp {vid} version {2 if vid==30 else 3}\n vrrp {vid} priority {200 if i==1 else 100}\n'
                             f' vrrp {vid} ip 10.77.{vid}.1\nexit\n')
                nodes.append({'id':f'r{i}','name':f'VRRP-R{i}','type':'router','image':image,
                              'ethernet':2,'startup_config':config})
            config='hostname VRRP-SW\nlldp run\nvlan 1,10,20,30\nspanning-tree mode rstp\n'
            for port in (0,1):
                config+=f'interface eth{port}\n switchport mode trunk\n switchport trunk allowed vlan 10,20\nexit\n'
            for port,vid in ((2,10),(3,20),(4,30),(5,30),(6,30)):
                config+=f'interface eth{port}\n switchport mode access\n switchport access vlan {vid}\n spanning-tree portfast\nexit\n'
            nodes.append({'id':'sw','name':'VRRP-SW','type':'switch','image':ll2s_device.IMAGE,'ethernet':7,'startup_config':config})
            for vid in (10,20,30):
                nodes.append({'id':f'p{vid}','name':f'PC{vid}','type':'pc',
                              'ipv4':f'10.77.{vid}.10/24','gateway':f'10.77.{vid}.1'})
            connections=[('r1','eth0',0),('r2','eth0',1),('p10','eth0',2),('p20','eth0',3),
                         ('r1','eth1',4),('r2','eth1',5),('p30','eth0',6)]
            lab.save({'name':'Disposable IPv4 VRRP through LL2S','nodes':nodes,'links':[
                {'id':f'c{i}','a':{'node':node,'port':port},'b':{'node':'sw','port':f'eth{swport}'}}
                for i,(node,port,swport) in enumerate(connections)]})
            lab.start_all()
            wait(lambda: all(states('r1').get(v)=='Master' for v in (10,20,30)) and
                 all(states('r2').get(v)=='Backup' for v in (10,20,30)), 'Master/Backup elected on VLAN and physical parents')
            wait(lambda: ping('p10','10.77.20.10') and ping('p30','10.77.20.10'), 'LL2S clients route through virtual gateways')
            for vid in (10,30):
                neighbor=run(f'p{vid}','ip','neigh','show',f'10.77.{vid}.1')
                assert f'00:00:5e:00:01:{vid:02x}' in neighbor,neighbor
            # Administrative shutdown and priority/preemption remain FRR-owned.
            for v in (10,20,30):
                parent='eth1' if v==30 else f'eth0.{v}'
                cli('r1','conf t',f'interface {parent}',f'vrrp {v} shutdown','end')
            wait(lambda: all(states('r2').get(v)=='Master' for v in (10,20,30)), 'Backup took over all virtual gateways')
            wait(lambda: ping('p10','10.77.20.10') and ping('p30','10.77.20.10'), 'Backup forwards client traffic', timeout=15)
            for v in (10,20,30):
                parent='eth1' if v==30 else f'eth0.{v}'
                cli('r1','conf t',f'interface {parent}',f'no vrrp {v} shutdown','end')
            wait(lambda: all(states('r1').get(v)=='Master' for v in (10,20,30)) and all(states('r2').get(v)=='Backup' for v in (10,20,30)), 'Higher-priority router preempted')
            cli('r1','conf t','interface eth0','shutdown','end')
            wait(lambda: states('r2').get(10)=='Master' and states('r2').get(20)=='Master', 'Trunk outage triggers peer takeover')
            wait(lambda: ping('p10','10.77.20.10'), 'Traffic recovers after trunk outage', timeout=15)
            cli('r1','conf t','interface eth0','no shutdown','end')
            wait(lambda: states('r1').get(10)=='Master' and states('r2').get(10)=='Backup','Trunk recovery and preemption')
            cli('r1','conf t','interface eth1','shutdown','end')
            wait(lambda: states('r2').get(30)=='Master', 'Physical parent outage triggers takeover')
            # Only VLAN 30 moves here. Test the peer's own routed address;
            # PC20's gateway remains R1 and needs alternate routing for return traffic.
            wait(lambda: ping('p30','10.77.30.1') and ping('p30','10.77.20.3'),
                 'Physical gateway traffic recovers', timeout=15)
            cli('r1','conf t','interface eth1','no shutdown','end')
            wait(lambda: states('r1').get(30)=='Master' and states('r2').get(30)=='Backup', 'Physical parent recovery and preemption')
            wait(lambda: 'VRRP-SW' in cli('r1','show lldp neighbors'), 'LLDP sees LL2S through VRRP port attachment')
            # Add/remove VIPs and the last VIP/group without shell-created state.
            cli('r1','conf t','interface eth0.10','vrrp 10 ip 10.77.10.5','end')
            wait(lambda: '10.77.10.5' in addresses('r1','wv0v10g10'),'Additional VIP provisioned')
            cli('r1','conf t','interface eth0.10','no vrrp 10 ip 10.77.10.5','end')
            wait(lambda: '10.77.10.5' not in addresses('r1','wv0v10g10'),'Removed VIP withdrawn')
            cli('r1','conf t','interface eth1','no vrrp 30','end')
            wait(lambda: 'wv1g30' not in links('r1'),'Removed group deletes its owned macvlan')
            cli('r1','conf t','interface eth1','vrrp 30 version 2','vrrp 30 priority 200','vrrp 30 ip 10.77.30.1','end')
            wait(lambda: states('r1').get(30)=='Master','Group recreated through native CLI')
            # Failed prerequisites must report pending status without an active VIP.
            cli('r1','conf t','interface eth1','vrrp 50 ip 192.0.2.1','end')
            wait(lambda: '192.0.2.1: configure' in cli('r1','show vrrp platform'),'Missing parent subnet reported')
            assert 'wv1g50' not in links('r1')
            cli('r1','conf t','interface eth1','ip address 192.0.2.2/24','end')
            wait(lambda: '192.0.2.1' in addresses('r1','wv1g50'),'Late parent address enables provisioning')
            cli('r1','conf t','interface eth1','no ip address 192.0.2.2/24','end')
            wait(lambda: 'wv1g50' not in links('r1'),'Missing prerequisite withdraws managed interface')
            cli('r1','conf t','interface eth1','no vrrp 50','end')
            # A same-named external object must not be adopted or deleted.
            run('r1','ip','link','add','wv1g40','type','dummy')
            index=links('r1')['wv1g40']['ifindex']
            cli('r1','conf t','interface eth1','vrrp 40 ip 10.77.30.40','end')
            wait(lambda: 'wv1g40 exists outside' in cli('r1','show vrrp platform'),'Foreign name conflict reported')
            cli('r1','conf t','interface eth1','no vrrp 40','end')
            time.sleep(2)
            assert links('r1')['wv1g40']['ifindex']==index
            run('r1','ip','link','delete','wv1g40')
            # Backend restart must not flap/rewrite existing Master/Backup state.
            before=links('r2')['wv0v10g10']['ifindex']
            pid=run('r2','pgrep','-f','^python3 /usr/lib/frr/weblab_lldp.py$')
            run('r2','kill','-KILL',pid)
            wait(lambda: run('r2','pgrep','-f','^python3 /usr/lib/frr/weblab_lldp.py$')!=pid and
                 'wv0v10g10 ready' in cli('r2','show vrrp platform'),'Provisioner recovered ownership')
            assert links('r2')['wv0v10g10']['ifindex']==before and states('r2')[10]=='Backup'
            for node in ('r1','r2'):cli(node,'write memory')
            run('r1','vtysh','-C','-f','/etc/frr/frr.conf')
            run('r1','vtysh','-f','/etc/frr/frr.conf')
            cli('r1','conf t','interface eth0.10','no vrrp 10','end')
            wait(lambda: 'wv0v10g10' not in links('r1'),'Reload prerequisite: group removed')
            run('r1','/usr/lib/frr/frr-reload.py','--reload','/etc/frr/frr.conf')
            wait(lambda: states('r1').get(10)=='Master','Saved native configuration reload restores VRRP')
            cli('r1','conf t','interface eth0.10','vrrp 10 priority 50','end') # Unsaved.
            lab.stop_all()
            result=lab_backup.create(lab)
            stream,_=lab_backup.take(lab,result['url'].rsplit('/',1)[1])
            shutil.rmtree(lab.directory/'nodes')
            with stream:lab_backup.restore(lab,stream,lab_server.run)
            lab.node('r1')['startup_config']='hostname WRONG\n'
            lab.start_all()
            wait(lambda: all(states('r1').get(v)=='Master' for v in (10,20,30)) and
                 all(states('r2').get(v)=='Backup' for v in (10,20,30)),'ZIP restores VRRP groups and macvlans; unsaved changes excluded')
            wait(lambda: ping('p10','10.77.20.10') and ping('p30','10.77.20.10'),'Restored virtual gateways forward through LL2S')
            print('FRR IPv4 VRRP native tests passed',flush=True)
        finally:
            lab.stop_all()
            if lab.fabric:lab.fabric.close()
            lab_backup.expire(lab,all_files=True)
            lab.file_lock.close()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',action='store_true')
    args=parser.parse_args()
    main(frr.PUBLIC_IMAGE if args.release else frr.VRRP_IMAGE)
