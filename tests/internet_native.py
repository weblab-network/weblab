#!/usr/bin/env python3
"""Opt-in disposable Docker/TAP test; never opens an existing lab directory."""
import argparse
import ipaddress
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import uuid
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_server import Lab, run, LabError, ports
import frr
import internet_cloud
import lab_backup
import lab_automation


PROBE_SERVER = '''
import http.server, subprocess, threading
class Handler(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def do_GET(self):
  body=b'firewall-ok\\n'
  self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
 def log_message(self,*args): pass
for port in (8080,8081,8082):
 server=http.server.ThreadingHTTPServer(('0.0.0.0',port),Handler)
 threading.Thread(target=server.serve_forever,daemon=True).start()
subprocess.run(['dnsmasq','--keep-in-foreground','--conf-file=/dev/null','--user=root',
 '--no-resolv','--no-hosts','--address=/fw.test/203.0.113.7','--log-facility=-'],check=True)
'''


def established_flow_check(lab, probe, cid, destination):
    client=probe+'-established'
    script='''
import http.client, time
from pathlib import Path
c=http.client.HTTPConnection(%r,8080,timeout=3)
c.request('GET','/'); assert c.getresponse().read()==b'firewall-ok\\n'
sock=c.sock
Path('/run/connected').touch()
deadline=time.monotonic()+15
while not Path('/run/check').exists():
 if time.monotonic()>deadline: raise RuntimeError('No policy check requested')
 time.sleep(.05)
c.request('GET','/'); assert c.getresponse().read()==b'firewall-ok\\n' and c.sock is sock
Path('/run/continued').touch()
while not Path('/run/check-deny').exists():
 if time.monotonic()>deadline: raise RuntimeError('No deny check requested')
 time.sleep(.05)
try:
 c.request('GET','/'); c.getresponse().read()
except OSError:
 print('existing-flow-blocked',flush=True)
else:
 raise AssertionError('Established outbound connection bypassed new deny')
''' % destination
    try:
        run('docker','run','-d','--name',client,'--network','container:'+cid,'--cap-drop','ALL',
            '--entrypoint','python3',os.environ.get('WL_INTERNET_IMAGE',internet_cloud.IMAGE),'-u','-c',script)
        def wait_file(path):
            deadline=time.monotonic()+10
            while True:
                try:run('docker','exec',client,'test','-f',path);break
                except LabError:
                    if time.monotonic()>deadline:raise
                    time.sleep(.1)
        wait_file('/run/connected')
        previous=json.loads(json.dumps(lab.node('cloud')['internet']))
        policy=previous['firewall']
        lab.update_internet('cloud',{'expected_internet':previous,'internet':{
            **previous,'firewall':{**policy,'rules':[*policy['rules'],
                {'action':'deny','protocol':'icmp','destination':'203.0.113.99'}]}}})
        run('docker','exec',client,'touch','/run/check')
        wait_file('/run/continued')
        previous=json.loads(json.dumps(lab.node('cloud')['internet']))
        lab.update_internet('cloud',{'expected_internet':previous,'internet':{
            **previous,'firewall':{'default_action':'deny','rules':[]}}})
        run('docker','exec',client,'touch','/run/check-deny')
        deadline=time.monotonic()+8
        while run('docker','inspect','--format','{{.State.Running}}',client)=='true':
            if time.monotonic()>deadline:raise AssertionError('Established-flow check did not complete')
            time.sleep(.1)
        assert run('docker','inspect','--format','{{.State.ExitCode}}',client)=='0',run('docker','logs',client)
        assert 'existing-flow-blocked' in run('docker','logs',client)
    finally:
        try:run('docker','rm','-f',client)
        except LabError:pass


def firewall_checks(lab, probe, routed, subnet):
    """Reach a local uplink endpoint; all denies are bounded, real packet tests."""
    destination = str(ipaddress.IPv4Network(subnet).network_address + 10)
    source = '10.244.60.10' if routed else '192.0.2.2'
    allow_tcp = {'action':'allow','protocol':'tcp','source':source,'destination':destination,
                 'source_port':'1024-65535','destination_port':'8080-8081'}
    deny_tcp = {'action':'deny','protocol':'tcp','destination_port':'8080-8081'}
    allow_udp = {'action':'allow','protocol':'udp','destination':destination,'destination_port':'53'}
    deny_udp = {'action':'deny','protocol':'udp','destination_port':'53'}
    nonmatches = [
        {'action':'deny','protocol':'any','source':'192.0.2.99'},
        {'action':'deny','protocol':'any','destination':'203.0.113.9'}]
    cases = [
        ('deny',[*nonmatches,allow_tcp,deny_tcp,deny_udp,allow_udp,
                 {'action':'allow','protocol':'icmp','destination':destination}],(True,True,False,False,True)),
        ('allow',[*nonmatches,deny_tcp,allow_tcp,allow_udp,deny_udp,
                  {'action':'deny','protocol':'icmp','destination':destination}],(False,False,True,True,False)),
        ('deny',[],(False,False,False,False,False))]
    for default,rules,expected in cases:
        topology=json.loads(json.dumps(lab.topology))
        topology['nodes'][next(i for i,n in enumerate(topology['nodes']) if n['id']=='cloud')]['internet'].update(
            uplink_subnet=subnet,firewall={'default_action':default,'rules':rules})
        lab.save(topology)
        if default=='deny' and rules:
            # Restore the restrictive policy through the real stopped-lab ZIP path.
            original=json.loads(json.dumps(lab.node('cloud')['internet']))
            backup=lab_backup.create(lab)
            stream,_=lab_backup.take(lab,backup['url'].rsplit('/',1)[1])
            with stream: archive=stream.read()
            lab.save({'name':'Blank','nodes':[],'links':[]})
            lab_backup.restore(lab,io.BytesIO(archive),run)
            assert lab.node('cloud')['internet']==original
        try:
            lab.start_all()
            runtime=lab.runtime['cloud']
            run('docker','run','-d','--name',probe,'--network',runtime['cloud_wan'],
                '--ip',destination,'--entrypoint','python3',
                os.environ.get('WL_INTERNET_IMAGE',internet_cloud.IMAGE),'-u','-c',PROBE_SERVER)
            # Check the target itself before attributing failures to policy.
            deadline=time.monotonic()+10
            while True:
                try:
                    run('docker','exec',probe,'python3','-c',
                        "import socket; socket.create_connection(('127.0.0.1',8082),1).close(); socket.create_connection(('127.0.0.1',53),1).close()")
                    break
                except LabError:
                    if time.monotonic()>=deadline:raise
                    time.sleep(.1)
            cid=lab.runtime['pc']['container']
            def check(allowed,*command):
                try: output=run('docker','exec',cid,*command,timeout=8)
                except LabError:
                    if allowed: raise
                else:
                    assert allowed, f'Firewall permitted denied traffic: {command}: {output}'
            def verify_policy(expected):
                for port,allowed in zip((8080,8081,8082),expected[:3]):
                    check(allowed,'wget','-q','-T','2','-O','-',f'http://{destination}:{port}/')
                check(expected[3],'timeout','4','nslookup','-type=A','fw.test',destination)
                check(expected[4],'ping','-c','1','-W','1',destination)
            verify_policy(expected)
            # Local services are separate from the forwarding policy.
            check(True,'ping','-c','1','-W','2','192.0.2.1')
            check(True,'timeout','4','nslookup','dl-cdn.alpinelinux.org','192.0.2.1')
            # Each denial must be real policy filtering, with a live target.
            run('docker','exec',probe,'python3','-c',
                "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8080/').read()==b'firewall-ok\\n'")
            print('PASS', 'routed' if routed else 'direct', 'firewall',default,
                  'first-match TCP/UDP, address/port filters, ICMP and gateway services',flush=True)
            if default=='deny' and rules:
                identities={ident:{k:value for k,value in state.items() if k in (
                    'container','cloud_container_id','bridge','gateway','console')} for ident,state in lab.runtime.items()}
                gateway_id=lab.runtime['cloud']['cloud_container_id']
                nat_before=run('docker','exec',gateway_id,'iptables','-t','nat','-S')
                forward_before=run('docker','exec',gateway_id,'iptables','-S','FORWARD')
                established_flow_check(lab,probe,cid,destination)
                # Change policy in both directions without recreating any node or probe.
                for next_default,next_rules,next_expected in (cases[1],cases[2],cases[0]):
                    previous=json.loads(json.dumps(lab.node('cloud')['internet']))
                    lab.update_internet('cloud',{'expected_internet':previous,'internet':{
                        **previous,'firewall':{'default_action':next_default,'rules':next_rules}}})
                    verify_policy(next_expected)
                    check(True,'ping','-c','1','-W','2','192.0.2.1')
                    check(True,'timeout','4','nslookup','dl-cdn.alpinelinux.org','192.0.2.1')
                    assert identities=={ident:{k:value for k,value in state.items() if k in (
                        'container','cloud_container_id','bridge','gateway','console')} for ident,state in lab.runtime.items()}
                    assert json.loads(lab.topology_path.read_text())==lab.topology
                    assert run('docker','exec',gateway_id,'iptables','-t','nat','-S')==nat_before
                    assert run('docker','exec',gateway_id,'iptables','-S','FORWARD')==forward_before
                # Network settings can be saved with just the gateway stopped.
                run('docker','rm','-f',probe)
                lab.stop('cloud')
                assert 'pc' in lab.runtime and ('router' in lab.runtime if routed else True)
                previous=json.loads(json.dumps(lab.node('cloud')['internet']))
                lab.update_internet('cloud',{'expected_internet':previous,'internet':{
                    **previous,'routes':[*previous['routes'],{'destination':'10.245.0.0/24','via':'192.0.2.2'}]}})
                lab.start('cloud')
                # Inspect the saved policy after the gateway's uplink was recreated.
                active=run('docker','exec',lab.runtime['cloud']['cloud_container_id'],'iptables','-S','WL_EGRESS')
                assert '--dport 8080:8081' in active and '-A WL_EGRESS -j DROP' in active
                assert lab.runtime['pc']['container']==cid
                print('PASS', 'routed' if routed else 'direct', 'live firewall swaps, established-flow denial, unchanged node identities/NAT, saved policy and gateway-only stop/edit/start',flush=True)
        finally:
            try:run('docker','rm','-f',probe)
            except LabError:pass
            lab.stop_all()
        assert not lab.runtime
        assert not run('docker','network','ls','-q','--filter','label='+internet_cloud.LABEL+'='+lab.owner+':cloud')


TARGET_SERVICES = '''
import http.server, json, socket, threading
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  body=json.dumps({'client':self.client_address[0]}).encode()
  self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
 def log_message(self,*args): pass
server=http.server.ThreadingHTTPServer(('0.0.0.0',11800),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('0.0.0.0',11801))
while True:
 data,address=s.recvfrom(1024)
 s.sendto(json.dumps({'client':address[0],'payload':data.decode()}).encode(),address)
'''


def free_host_port():
    with socket.socket() as tcp, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        tcp.bind(('127.0.0.1',0));port=tcp.getsockname()[1]
        udp.bind(('127.0.0.1',port))
        return port


def port_forward_checks(lab, routed):
    """Real host publications to direct/routed targets, with explicit inbound policy."""
    names=[]
    stem='wl-forward-test-'+uuid.uuid4().hex[:10]
    image=os.environ.get('WL_INTERNET_IMAGE',internet_cloud.IMAGE)
    target='10.244.60.10' if routed else '192.0.2.2'
    automation = lab_automation.Automation(lab, LabError, ports, run)
    def stop_gateway():
        page = automation.internet_settings('cloud')
        automation.stop_internet('cloud', page['revision'], page['runtime_revision'])
    def update_gateway(config):
        proposal = automation.preview_internet('cloud', config, automation.state()['revision'])
        return automation.apply_internet(proposal['proposal_id'])
    def start_gateway():
        result = automation.start_internet('cloud', automation.state()['revision'])
        if not result['started']:
            raise LabError(result['error'])
    try:
        lab.start_all()
        pc=lab.runtime['pc']['container']
        router=lab.runtime['router']['container'] if routed else None
        gateway=lab.runtime['cloud']
        ipam=json.loads(run('docker','network','inspect',gateway['cloud_wan']))[0]['IPAM']['Config'][0]
        info=json.loads(run('docker','inspect',gateway['cloud_container_id']))[0]
        subnet,bind=ipam['Subnet'],info['NetworkSettings']['Networks'][gateway['cloud_wan']]['Gateway']
        allowed=str(ipaddress.IPv4Network(subnet).network_address+10)
        denied=str(ipaddress.IPv4Network(subnet).network_address+11)
        port,restricted_port,router_port=free_host_port(),free_host_port(),free_host_port()
        def mapping(protocol,host=bind,source='',external=port,destination=target):
            return {'protocol':protocol,'bind_address':host,'external_port':external,
                    'target_address':destination,'target_port':11800 if protocol=='tcp' else 11801,'source':source}
        forwards=[mapping('tcp','127.0.0.1'),mapping('udp','127.0.0.1'),mapping('tcp'),mapping('udp'),
                  mapping('tcp',source=allowed,external=restricted_port),mapping('udp',source=allowed,external=restricted_port)]
        if routed:forwards.append(mapping('tcp','127.0.0.1',external=router_port,destination='10.244.60.1'))
        stop_gateway()
        assert lab.runtime['pc']['container']==pc
        previous=json.loads(json.dumps(lab.node('cloud')['internet']))
        if routed:
            # Exercise a genuine return-route edit through preview/apply, then
            # restore it with the forwards while the router and PC stay running.
            update_gateway({**previous, 'routes': []})
            assert not lab.node('cloud')['internet']['routes']
        update_gateway({**previous,'uplink_subnet':subnet,'firewall':{'default_action':'deny','rules':[]},'port_forwards':forwards})
        for role,container in [('pc',pc),*([('router',router)] if routed else [])]:
            name=stem+'-'+role;names.append(name)
            run('docker','run','-d','--name',name,'--network','container:'+container,'--cap-drop','ALL',
                '--entrypoint','python3',image,'-u','-c',TARGET_SERVICES)
        start_gateway()
        gateway=lab.runtime['cloud']
        info=json.loads(run('docker','inspect',gateway['cloud_container_id']))[0]
        outside=info['NetworkSettings']['Networks'][gateway['cloud_wan']]['IPAddress']
        published=info['HostConfig']['PortBindings']
        assert published['40000/tcp']==[{'HostIp':'127.0.0.1','HostPort':str(port)}]
        def host_request(protocol,host,external):
            if protocol=='tcp':
                with urllib.request.urlopen(f'http://{host}:{external}/',timeout=2) as response:return json.load(response)
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
                udp.settimeout(2);udp.sendto(b'udp-forward',(host,external));return json.loads(udp.recv(1024))
        deadline=time.monotonic()+10
        while True:
            try:assert host_request('tcp','127.0.0.1',port)['client']=='192.0.2.1';break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(.2)
        for protocol,host in [('udp','127.0.0.1'),('tcp',bind),('udp',bind)]:
            result=host_request(protocol,host,port)
            assert result['client']=='192.0.2.1',result
            if protocol=='udp':assert result['payload']=='udp-forward'
        if routed:assert host_request('tcp','127.0.0.1',router_port)['client']=='192.0.2.1'
        for suffix,address in [('allowed',allowed),('denied',denied)]:
            name=stem+'-'+suffix;names.append(name)
            run('docker','run','-d','--name',name,'--network',gateway['cloud_wan'],'--ip',address,
                '--cap-drop','ALL','--entrypoint','sleep',image,'120')
        probe_script='''
import json,socket,sys,urllib.request
protocol,host,port,allowed=sys.argv[1:];port=int(port)
try:
 if protocol=='tcp':
  with urllib.request.urlopen('http://%s:%d/'%(host,port),timeout=1) as r:result=json.load(r)
 else:
  with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
   s.settimeout(1);s.sendto(b'udp-forward',(host,port));result=json.loads(s.recv(1024))
except OSError:
 assert allowed=='no','Allowed inbound traffic failed'
else:
 assert allowed=='yes','Denied inbound traffic reached target'
 assert result['client']=='192.0.2.1',result
'''
        for protocol,index in [('tcp',4),('udp',5)]:
            for suffix,accept in [('allowed','yes'),('denied','no')]:
                run('docker','exec',stem+'-'+suffix,'python3','-c',probe_script,protocol,outside,
                    str(internet_cloud.FORWARD_PORT_BASE+index),accept,timeout=5)
            # A peer cannot bypass localhost scope by addressing its internal published port.
            run('docker','exec',stem+'-allowed','python3','-c',probe_script,protocol,outside,
                str(internet_cloud.FORWARD_PORT_BASE+(0 if protocol=='tcp' else 1)),'no',timeout=5)
        # Live outbound changes leave inbound translations and connections available.
        previous=json.loads(json.dumps(lab.node('cloud')['internet']))
        update_gateway({**previous,'firewall':{'default_action':'allow','rules':[]}})
        assert host_request('tcp','127.0.0.1',port)['client']=='192.0.2.1'
        assert lab.runtime['pc']['container']==pc
        if routed:assert lab.runtime['router']['container']==router
        # Remove client endpoints before the owned uplink is deleted.
        for suffix in ('allowed','denied'):run('docker','rm','-f',stem+'-'+suffix);names.remove(stem+'-'+suffix)
        stop_gateway()
        previous=json.loads(json.dumps(lab.node('cloud')['internet']))
        update_gateway({**previous,'port_forwards':[]})
        start_gateway()
        for protocol in ('tcp','udp'):
            try:host_request(protocol,'127.0.0.1',port)
            except OSError:pass
            else:raise AssertionError('Removed host publication remained accessible')
        assert lab.runtime['pc']['container']==pc
        # A busy host port fails without adopting the listener or stopping targets.
        stop_gateway()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen(1)
            previous=json.loads(json.dumps(lab.node('cloud')['internet']))
            update_gateway({**previous,'port_forwards':[mapping('tcp','127.0.0.1',external=occupied.getsockname()[1])]})
            try:start_gateway()
            except LabError as exc:
                assert any(reason in str(exc).lower() for reason in ('bind','allocated','in use')),exc
            else:raise AssertionError('Gateway started with an occupied host port')
            assert 'cloud' not in lab.runtime and lab.runtime['pc']['container']==pc
            assert not run('docker','network','ls','-q','--filter',
                'label='+internet_cloud.LABEL+'='+lab.owner+':cloud')
        print('PASS', 'routed PC/router' if routed else 'direct PC',
              'TCP/UDP host port forwards, loopback scope, explicit bind, source restriction, default-deny replies, live firewall coexistence, removal, occupied-port rollback and unchanged target identities',flush=True)
    finally:
        for name in reversed(names):
            try:run('docker','rm','-f',name)
            except LabError:pass
        lab.stop_all()


def exercise(routed, port_forwards_only=False):
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
            if port_forwards_only:
                port_forward_checks(lab,routed)
                return
            for attempt in range(2):
                lab.start_all()
                cid=lab.runtime['pc']['container']
                capture=lab.start_capture('wan',None)
                print(run('docker','exec',cid,'ping','-c','2','-W','3','1.1.1.1'))
                print(run('docker','exec',cid,'nslookup','dl-cdn.alpinelinux.org','192.0.2.1'))
                assert lab.captures.read(capture['id'],None)['total']>0, 'Cloud cable must be visible to capture'
                lab.captures.action(capture['id'],None,'close')
                runtime=lab.runtime['cloud']
                subnet=json.loads(run('docker','network','inspect',runtime['cloud_wan']))[0]['IPAM']['Config'][0]['Subnet']
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
            firewall_checks(lab,probe,routed,subnet)
            port_forward_checks(lab,routed)
        finally:
            try:run('docker','rm','-f',probe)
            except LabError:pass
            lab.stop_all()
            lab_backup.expire(lab,all_files=True)
            lab.file_lock.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='Explicitly permit disposable containers and Internet probes')
    parser.add_argument('--port-forwards-only',action='store_true',help='Run only inbound publication checks')
    args=parser.parse_args()
    if not args.run:parser.error('Use --run to opt into native networking tests')
    exercise(False,args.port_forwards_only)
    exercise(True,args.port_forwards_only)

if __name__=='__main__':main()
