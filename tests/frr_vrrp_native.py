#!/usr/bin/env python3
"""Opt-in isolated FRR VRRP, shell and custom-image checks (root, Docker, Alpine).
Run sequentially: python3 tests/frr_vrrp_native.py
Never changes the original FRR tag or an existing lab.
"""
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_capture
import frr
import lab_backup
import lab_server


def main():
    run = lab_server.run
    original_image = frr.IMAGE
    suffix = uuid.uuid4().hex[:10]
    custom_image = f'quay.io/frrouting/frr:wl-test-{suffix}'
    fixture = f'wl-frr-image-{suffix}'
    with tempfile.TemporaryDirectory(prefix='weblab-vrrp-') as temp:
        lab = lab_server.Lab(Path(temp)/'data', Path(temp)/'images')
        def cli(node, *commands):
            return run('docker','exec',lab.runtime[node]['container'],'vtysh',
                       *[part for command in commands for part in ('-c',command)])
        def shell(node, *args):
            return run('docker','exec',lab.runtime[node]['container'], *args)
        def wait(check, label):
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if check():
                    print(label,flush=True)
                    return
                time.sleep(.5)
            raise AssertionError(label)
        def state(node, value):
            return bool(re.search(r'Status \(v4\)\s+'+value, cli(node,'show vrrp')))
        def ping_vip():
            shell('pc','ping','-c','3','-W','2','10.77.0.254')
        try:
            nodes=[]
            for i in (1,2):
                nodes.append({'id':f'r{i}','name':f'R{i}','type':'router','image':frr.IMAGE,'ethernet':2,
                              'startup_config':f'hostname R{i}\nservice integrated-vtysh-config\ninterface eth0.10\n ip address 10.77.0.{i}/24\n!\ninterface eth1\n ip address 10.78.{i-1}.1/24\n!\n'})
            nodes.append({'id':'pc','name':'PC','type':'pc','ipv4':'10.78.0.10/24','gateway':'10.78.0.1'})
            lab.save({'name':'Isolated VRRP check','nodes':nodes,'links':[
                {'id':'trunk','a':{'node':'r1','port':'eth0'},'b':{'node':'r2','port':'eth0'}},
                {'id':'access','a':{'node':'r1','port':'eth1'},'b':{'node':'pc','port':'eth0'}}]})
            lab.start_all()
            image_container=lab.runtime['r1']['container']
            before=run('docker','inspect','-f','{{.Id}} {{.State.Pid}}',image_container)
            c=console_capture.Console(lab.runtime['r1']['port'])
            try:
                c.acquire()
                c.send(b'\rexit\r')
                output=b'';deadline=time.monotonic()+10
                while not re.search(r'(?:bash-[\d.]+|R1:[^\n]*)#', console_capture.clean_output(output)): output+=c.receive(deadline)
                c.send(b'echo $((2000+24))\r')
                output=b''
                while '\n2024\n' not in console_capture.clean_output(output): output+=c.receive(deadline)
                c.send(b'vtysh\r')
                output=b''
                while b'R1#' not in output: output+=c.receive(deadline)
                c.send(b'exit\r')
                output=b''
                while not re.search(r'(?:bash-[\d.]+|R1:[^\n]*)#', console_capture.clean_output(output)): output+=c.receive(deadline)
                c.send(b'\x04')
                output=b'';deadline=time.monotonic()+10
                while b'R1#' not in output: output+=c.receive(deadline)
                assert lab.runtime['r1']['container']==image_container
                assert run('docker','inspect','-f','{{.Id}} {{.State.Pid}}',image_container)==before
                print('Shared console: vtysh -> shell -> vtysh; shell logout recovers CLI',flush=True)
            except Exception:
                print('Console output:', repr(console_capture.clean_output(output)), flush=True)
                raise
            finally:
                c.close()
            for i in (1,2):
                node=f'r{i}'
                shell(node,'ip','link','add','link','eth0','name','eth0.10','type','vlan','id','10')
                shell(node,'ip','link','set','eth0.10','up')
                shell(node,'ip','link','add','vrrp10','link','eth0.10','type','macvlan','mode','bridge')
                shell(node,'ip','link','set','vrrp10','address','00:00:5e:00:01:0a')
                shell(node,'ip','addr','add','10.77.0.254/24','dev','vrrp10')
                shell(node,'ip','link','set','vrrp10','up')
                cli(node,'configure terminal','interface eth0.10','vrrp 10 version 3',
                    f'vrrp 10 priority {200 if i==1 else 100}','vrrp 10 ip 10.77.0.254','end')
            wait(lambda:state('r1','Master') and state('r2','Backup'),'VRRP elected R1 Master / R2 Backup over VLAN 10')
            ping_vip()
            cli('r1','configure terminal','interface eth0.10','vrrp 10 shutdown','end')
            wait(lambda:state('r2','Master'),'VRRP failed over to R2')
            ping_vip()
            cli('r1','configure terminal','interface eth0.10','no vrrp 10 shutdown','end')
            wait(lambda:state('r1','Master') and state('r2','Backup'),'VRRP preemption restored R1')
            print('Virtual IP reachable through either master',flush=True)
            lab.stop_all()
            # Make an isolated local commit without replacing the supported tag.
            run('docker','create','--name',fixture,'--network','none','--entrypoint','/bin/sh',original_image,'-c','true')
            run('docker','commit','--change',f'LABEL weblab.test={suffix}',fixture,custom_image)
            run('docker','rm',fixture)
            frr.IMAGE=custom_image
            lab.save({'name':'Custom image check','nodes':[{'id':'custom','name':'Custom','type':'router',
                      'image':custom_image,'ethernet':1}],'links':[]})
            try: lab.start('custom')
            except lab_server.LabError as error: assert 'WL_ALLOW_UNTESTED_FRR' in str(error)
            else: raise AssertionError('Untested image started without opt-in')
            lab.allow_untested_frr=True
            lab.start('custom')
            assert lab.snapshot()['status']['custom']['warning']
            cli('custom','write memory')
            custom_id=lab.runtime['custom']['frr_image_id']
            lab.stop_all()
            result=lab_backup.create(lab)
            stream,_=lab_backup.take(lab,result['url'].rsplit('/',1)[1])
            with stream: archive=stream.read()
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                assert json.loads(z.read('manifest.json'))['containers']==[{'name':custom_image,'digest':custom_id}]
            lab_backup.restore(lab,io.BytesIO(archive),run)
            print('Custom image: default denied, opt-in starts with warning, ZIP preserves exact image ID',flush=True)
        finally:
            try:
                lab.stop_all()
                lab_backup.expire(lab,all_files=True)
                assert not run('docker','ps','-aq','--filter',f'label=iol.lab={lab.owner}')
                assert not run('docker','network','ls','-q','--filter',f'label=iol.lab={lab.owner}')
            finally:
                lab.file_lock.close()
                frr.IMAGE=original_image
                for command in (('docker','rm','-f',fixture),('docker','image','rm',custom_image)):
                    try: run(*command)
                    except lab_server.LabError: pass
            print('Disposable FRR resources cleaned',flush=True)


if __name__=='__main__':
    main()
