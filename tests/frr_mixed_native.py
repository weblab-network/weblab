#!/usr/bin/env python3
"""Opt-in disposable FRR–IOSv OSPF interoperability test; requires KVM/user image."""
import argparse
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import frr
import lab_server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-dir',type=Path,required=True)
    parser.add_argument('--iosv-image',required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='weblab-frr-iosv-') as tmp:
        lab=lab_server.Lab(Path(tmp),args.image_dir)
        try:
            lab.save({'name':'FRR–IOSv OSPF', 'nodes':[
                {'id':'frr_mixed_r','name':'FRR','type':'router','image':frr.IMAGE,'ethernet':1,
                 'startup_config':'hostname FRR\ninterface eth0\n ip address 10.0.12.1/30\n!\nrouter ospf\n ospf router-id 10.1.1.1\n network 10.0.12.0/30 area 0\n!\n'},
                {'id':'frr_mixed_iosv','name':'IOSV','type':'router','image':args.iosv_image,'ethernet':2,
                 'startup_config':'hostname IOSV\nno logging console\ninterface GigabitEthernet0/0\n ip address 10.0.12.2 255.255.255.252\n no shutdown\n!\ninterface Loopback0\n ip address 10.9.9.9 255.255.255.255\n!\nrouter ospf 1\n router-id 10.9.9.9\n network 10.0.12.0 0.0.0.3 area 0\n network 10.9.9.9 0.0.0.0 area 0\n!\nend\n'}],
                'links':[{'id':'mixed_transit','a':{'node':'frr_mixed_r','port':'eth0'},'b':{'node':'frr_mixed_iosv','port':'Gi0/0'}}]})
            print('Starting disposable FRR and IOSv routers',flush=True)
            lab.start_all()
            print('Waiting for IOSv boot and OSPF convergence (up to five minutes)',flush=True)
            container=lab.runtime['frr_mixed_r']['container']
            deadline=time.monotonic()+300
            while time.monotonic()<deadline:
                neighbors=lab_server.run('docker','exec',container,'vtysh','-c','show ip ospf neighbor')
                routes=lab_server.run('docker','exec',container,'vtysh','-c','show ip route ospf')
                if 'Full' in neighbors and '10.9.9.9/32' in routes:break
                time.sleep(2)
            else:raise AssertionError('FRR–IOSv OSPF did not converge')
            lab_server.run('docker','exec',container,'ping','-c','3','-W','2','10.9.9.9')
            print('PASS: FRR–IOSv OSPF adjacency, learned loopback and ping',flush=True)
        finally:
            lab.stop_all()
            lab.file_lock.close()
            print('Disposable mixed-vendor lab stopped',flush=True)


if __name__=='__main__':main()
