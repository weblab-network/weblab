#!/usr/bin/env python3
"""Opt-in disposable Docker/TAP test; never opens an existing lab directory."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_server import Lab, run, LabError
import frr
import internet_cloud
import lab_backup


def exercise(routed):
    with tempfile.TemporaryDirectory(prefix='weblab-internet-native-') as td:
        lab = Lab(Path(td)/'data', Path(td)/'images')
        probe = 'wl-internet-probe-'+uuid.uuid4().hex[:12]
        try:
            cloud = {'id':'cloud','name':'Internet','type':'cloud','internet':{'address':'192.0.2.1/24','routes':[]}}
            pc = {'id':'pc','name':'PC','type':'pc'}
            nodes = [pc,cloud]  # Verify startup dependency ordering.
            links = [{'id':'wan','a':{'node':'cloud','port':'eth0'},'b':{'node':'pc','port':'eth0'}}]
            if routed:
                cloud['internet']['routes']=[{'destination':'10.244.60.0/24','via':'192.0.2.2'}]
                pc.update(ipv4='10.244.60.10/24',gateway='10.244.60.1')
                nodes.append({'id':'router','name':'R','type':'router','image':frr.PUBLIC_IMAGE,'ethernet':2,
                    'startup_config':'hostname R\ninterface eth0\n ip address 10.244.60.1/24\nexit\ninterface eth1\n ip address 192.0.2.2/24\nexit\nip route 0.0.0.0/0 192.0.2.1\nend\n'})
                links[0]['b']={'node':'router','port':'eth1'}
                links.append({'id':'lan','a':{'node':'router','port':'eth0'},'b':{'node':'pc','port':'eth0'}})
            lab.save({'name':'Disposable Internet test','nodes':nodes,'links':links})
            for attempt in range(2):
                lab.start_all()
                cid=lab.runtime['pc']['container']
                capture=lab.start_capture('wan',None)
                print(run('docker','exec',cid,'ping','-c','2','-W','3','1.1.1.1'))
                print(run('docker','exec',cid,'nslookup','dl-cdn.alpinelinux.org','192.0.2.1'))
                assert lab.captures.read(capture['id'],None)['total']>0, 'Cloud cable must be visible to capture'
                lab.captures.action(capture['id'],None,'close')
                runtime=lab.runtime['cloud']
                info=json.loads(run('docker','inspect',runtime['cloud_container_id']))[0]
                external=info['NetworkSettings']['Networks'][runtime['cloud_wan']]['IPAddress']
                run('docker','run','-d','--name',probe,'--network',runtime['cloud_wan'],'--cap-add','NET_ADMIN','alpine:latest','sleep','60')
                target='10.244.60.10' if routed else '192.0.2.2'
                run('docker','exec',probe,'ip','route','add',target+'/32','via',external)
                try:
                    run('docker','exec',probe,'ping','-c','1','-W','1',target)
                except LabError:
                    pass
                else:
                    raise AssertionError('Unsolicited inbound packet reached the lab')
                run('docker','rm','-f',probe)
                lab.stop_all()
                assert not lab.runtime
                assert not run('docker','ps','-aq','--filter','label='+internet_cloud.LABEL+'='+lab.owner+':cloud')
                assert not run('docker','network','ls','-q','--filter','label='+internet_cloud.LABEL+'='+lab.owner+':cloud')
            print('PASS', 'routed FRR' if routed else 'direct PC', 'NAT, DNS, capture, inbound denial, restart and cleanup')
        finally:
            try:run('docker','rm','-f',probe)
            except LabError:pass
            lab.stop_all()
            lab_backup.expire(lab,all_files=True)
            lab.file_lock.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='Explicitly permit disposable containers and Internet probes')
    args=parser.parse_args()
    if not args.run:parser.error('Use --run to opt into native networking tests')
    exercise(False)
    exercise(True)

if __name__=='__main__':main()
