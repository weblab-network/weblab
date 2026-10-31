#!/usr/bin/env python3
"""Opt-in real gateway/shell/ProxyJump test in a disposable lab; no vendor images."""
import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lab_server import Lab, run, LabError
from external_access import Access
import internet_cloud


def main():
    image=os.environ['WL_TEST_SSH_IMAGE']
    os.environ['WL_INTERNET_IMAGE']=image
    tag='alpine:ssh-test-'+uuid.uuid4().hex[:8]
    subprocess.run(['docker','build','-q','-t',tag,'-'],input=f'FROM {image}\nENTRYPOINT []\nCMD ["sleep","infinity"]\n',text=True,check=True)
    volume=None
    try:
        with tempfile.TemporaryDirectory(prefix='wl-ssh-native-') as temporary:
            root=Path(temporary)
            key=root/'key'
            run('ssh-keygen','-q','-t','ed25519','-N','','-f',str(key))
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1',0));port=port_socket.getsockname()[1]
            lab=Lab(root/'data',root/'images')
            access=Access(lab);lab.access=access
            try:
                lab.save({'name':'Disposable SSH test','nodes':[
                    {'id':'cloud','name':'Internet','type':'cloud',
                     'internet':{'ssh':{'enabled':True,'external_port':port}}},
                    {'id':'pc','name':'Target','type':'pc','image':tag}],
                    'links':[{'id':'cable','a':{'node':'cloud','port':'eth0'},'b':{'node':'pc','port':'eth0'}}]})
                access.change({'action':'add','scope':'gateway','node':'cloud','key':key.with_suffix('.pub').read_text().strip(),'label':'Test'})
                volume=f'wl-ssh-{lab.owner}-'+hashlib.sha256(b'cloud').hexdigest()[:16]
                lab.start_all()
                runtime=lab.runtime['cloud']
                helper=runtime['cloud_ssh_id'];pc=lab.runtime['pc']['container']
                cfg=root/'ssh-config'
                cfg.write_text(f'''Host *
  IdentityFile {key}
  IdentitiesOnly yes
  IdentityAgent none
  StrictHostKeyChecking no
  UserKnownHostsFile {root}/known_hosts
  BatchMode yes
  ConnectTimeout 3
Host jump
  HostName 127.0.0.1
  Port {port}
  User lab
Host target
  HostName 192.0.2.2
  User lab
  ProxyJump jump
''')
                def ssh(host,command,allowed=True):
                    result=subprocess.run(['ssh','-F',str(cfg),host,command],capture_output=True,text=True,timeout=10)
                    if allowed is True:assert result.returncode==0,(result.stdout,result.stderr,run('docker','logs',helper))
                    elif allowed is False:assert result.returncode!=0,result.stdout
                    return result.stdout
                last_error=None
                for _ in range(5):
                    try:
                        assert ssh('jump','id -u').strip()=='10003';break
                    except AssertionError as exc:
                        last_error=exc
                        time.sleep(.1)
                else:
                    for command in [('docker','exec',helper,'ps','aux'), ('docker','exec',helper,'ss','-ltnp'),
                                    ('docker','exec',helper,'cat','/run/ssh/sshd_config'),
                                    ('docker','exec',runtime['cloud_container_id'],'iptables','-nvL','INPUT'),
                                    ('docker','exec',runtime['cloud_container_id'],'ip','-4','addr')]:
                        print(run(*command),flush=True)
                    raise AssertionError('Gateway SSH not ready: '+str(last_error))
                setup='''
import pathlib,subprocess
p=pathlib.Path('/run/ssh-target');p.mkdir(exist_ok=True)
subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(p/'host')],check=True)
(p/'keys').write_text(%r+'\\n')
(p/'config').write_text('Port 22\\nHostKey /run/ssh-target/host\\nAuthorizedKeysFile /run/ssh-target/keys\\nPasswordAuthentication no\\nAllowUsers lab\\nStrictModes no\\n')
pathlib.Path('/run/sshd').mkdir(exist_ok=True)
subprocess.run(['/usr/sbin/sshd','-f',str(p/'config')],check=True)
''' % key.with_suffix('.pub').read_text().strip()
                run('docker','exec',pc,'python3','-c',setup)
                assert 'target-ok' in ssh('target','echo target-ok')
                logs=subprocess.check_output(['docker','logs',helper],text=True,stderr=subprocess.STDOUT)
                assert 'Unsupported option' not in logs,logs
                run('docker','exec','-d',pc,'python3','-c',
                    "import socket; s=socket.socket(); s.bind(('0.0.0.0',8023)); s.listen(); "
                    "c,_=s.accept(); c.sendall(b'lab-telnet-ok\\r\\n'); c.close()")
                # BusyBox telnet exits nonzero when the peer closes normally.
                assert 'lab-telnet-ok' in ssh('jump','sleep 1 | telnet 192.0.2.2 8023',None)
                assert '192.0.2.2' in ssh('jump','ip route get 192.0.2.2')
                ssh('jump','ip link add denied-dummy type dummy',False)
                # The same shell must not reach the control plane or Docker uplink.
                outside=json.loads(run('docker','network','inspect',runtime['cloud_wan']))[0]['IPAM']['Config'][0]['Gateway']
                for address in ['127.0.0.1',outside,'1.1.1.1']:
                    ssh('jump',f"python3 -c \"import socket; socket.create_connection(('{address}',22),1)\"",False)
                    forward=subprocess.run(['ssh','-F',str(cfg),'-W',address+':22','jump'],
                                           capture_output=True,timeout=6)
                    assert forward.returncode!=0, 'TCP forward bypassed shell destination policy'
                wan=json.loads(run('docker','inspect',runtime['cloud_container_id']))[0]['NetworkSettings']['Networks'][runtime['cloud_wan']]['IPAddress']
                denied=subprocess.run(['docker','run','--rm','--network',runtime['cloud_wan'],
                    '--entrypoint','python3',image,'-c',
                    f"import socket; socket.create_connection(('{wan}',2223),1)"],capture_output=True,timeout=6)
                assert denied.returncode!=0,'Uplink peer bypassed localhost source guard'
                print('PASS non-root shell, SSH/Telnet, ProxyJump, lab-only egress and source guard',flush=True)
                # Revoke while a long-lived shell is connected.
                session=subprocess.Popen(['ssh','-F',str(cfg),'jump','sleep 60'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    time.sleep(.3)
                    access.change({'action':'revoke','id':access.grants[0]['id']})
                    internet_cloud.sync_ssh(lab,'cloud',runtime,run,LabError)
                    session.wait(timeout=5)
                    assert session.returncode!=0
                    ssh('jump','true',False)
                finally:
                    if session.poll() is None:session.kill();session.wait()
                print('PASS live key revocation closes active SSH session',flush=True)
                fp=runtime['cloud_ssh_fingerprint']
                access.change({'action':'add','scope':'gateway','node':'cloud','key':key.with_suffix('.pub').read_text().strip(),'label':'Test again'})
                lab.stop_all();lab.start_all()
                assert lab.runtime['cloud']['cloud_ssh_fingerprint']==fp
                print('PASS gateway host key survives recreation',flush=True)
            finally:
                access.close();lab.stop_all();lab.file_lock.close()
    finally:
        if volume:subprocess.run(['docker','volume','rm',volume],check=False,stdout=subprocess.DEVNULL)
        subprocess.run(['docker','image','rm',tag],check=False,stdout=subprocess.DEVNULL)


if __name__=='__main__':main()
