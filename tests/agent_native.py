#!/usr/bin/env python3
"""Opt-in companion/Ollama integration, disposable lab only; no guest images/boot.
Build weblab-agent:dev first. Requires Docker and already installed Ollama models.
"""
import argparse
import base64
import struct
import zlib
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.request import Request, urlopen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_bridge
import lab_automation
import lab_server


def main(args):
    ident = 'weblab-agent-test-' + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='agent-native-') as directory:
        root = Path(directory)
        root.chmod(0o755)
        sockets = root/'sockets'; sockets.mkdir(mode=0o777); sockets.chmod(0o777)
        lab = lab_server.Lab(root/'lab',root/'images')
        bridge = agent_bridge.Bridge(lab,sockets,root/'bridge-state',args.ollama_url)
        server = lab_server.ThreadingHTTPServer(('127.0.0.1',0),lab_server.Handler)
        server.lab,server.agent=lab,bridge
        threading.Thread(target=server.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(server.server_port)
        token=''
        def api(action,data=None):
            req=Request(base+'/api/agent/'+action,None if data is None else json.dumps(data).encode(),
                        {'Content-Type':'application/json','X-Weblab-Agent-Session':token})
            with urlopen(req,timeout=20) as response: return json.load(response)
        def await_state(predicate,timeout=360):
            end=time.monotonic()+timeout
            last_status=None
            last_phase=None
            while time.monotonic()<end:
                state=api('state')
                if state['status']!=last_status:
                    print('State:',state['status'],state.get('error',''),flush=True);last_status=state['status']
                if state.get('phase') and state['phase']!=last_phase:
                    print('Activity:',state['phase'],flush=True);last_phase=state['phase']
                if predicate(state): return state
                time.sleep(1)
            raise AssertionError('Timed out waiting for companion state')
        def start_container():
            fixture_args = (['--entrypoint','/opt/mcp/bin/python','-v',
                             str(Path(__file__).with_name('agent_tool_fixture.py'))+':/tmp/tool_fixture.py:ro']
                            if args.tool_gateway_only else [])
            subprocess.run(['docker','run','-d','--name',ident,'--network','none','--read-only',
                '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128',
                '--memory','1g','--cpus','1','--init',
                '--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
                '--tmpfs','/workspace:rw,nosuid,nodev,uid=10001,gid=10001,size=32m',
                '-v',str(sockets)+':/run/weblab-agent','-v',ident+':/home/agent',*fixture_args,args.image,
                *(['/tmp/tool_fixture.py'] if args.tool_gateway_only else [])],check=True,stdout=subprocess.DEVNULL)
            end=time.monotonic()+20
            while time.monotonic()<end:
                try: api('state');return
                except Exception: time.sleep(.5)
            raise AssertionError('Companion did not become ready')
        before=copy.deepcopy(lab.topology)
        try:
            token=api('session',{})['token']
            start_container()
            # Confirm network isolation in the actual packaged companion.
            subprocess.run(['docker','exec',ident,'python3','-c',
                'import socket\ns=socket.socket();s.settimeout(1)\ntry: s.connect(("192.0.2.1",80))\nexcept OSError: pass\nelse: raise SystemExit("Unexpected egress")'],check=True)
            if args.tool_gateway_only:
                settings={**api('state')['settings'], 'provider':'openai',
                          'endpoint':agent_bridge.OPENAI_ENDPOINT, 'model':'gpt-5.6-sol'}
                api('settings',settings)
                def png_chunk(kind, content):
                    return struct.pack('>I',len(content))+kind+content+struct.pack('>I',zlib.crc32(kind+content))
                png=(b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+
                     png_chunk(b'IDAT',zlib.compress(b'\x00\xff\x00\x00'))+png_chunk(b'IEND',b''))
                api('turn',{'prompt':'Read the disposable lab state and inspect the attached reference files.',
                            'request_id':uuid.uuid4().hex,'attachments':[
                                {'name':'reference.txt','data':base64.b64encode(b'ATTACHMENT_TRANSPORT_PROBE').decode()},
                                {'name':'red-pixel.png','data':base64.b64encode(png).decode()}]})
                state=await_state(lambda s:s['status'] not in ('starting','running','stopping'),60)
                assert state['status']=='completed',state.get('error')
                assert any('Fixture verified:' in m['text'] for m in state['messages']),state['messages']
                assert lab.topology==before and not lab.runtime
                print('PASS: packaged text/image attachment transport and Code Mode model -> Weblab MCP -> gateway -> empty lab; only Weblab tools exposed, no credentials or cloud inference',flush=True)
                return
            if args.openai_login_only:
                settings={**api('state')['settings'], 'provider':'openai',
                          'endpoint':agent_bridge.OPENAI_ENDPOINT, 'model':'gpt-5.6-sol'}
                api('settings',settings)
                api('login-status',{})
                state=await_state(lambda s:s['auth']['status']!='connecting',60)
                assert state['auth']['status']=='signed-out',state['auth']['status']
                api('turn',{'prompt':'Test the unauthenticated guard only.','request_id':uuid.uuid4().hex})
                state=await_state(lambda s:s['status'] not in ('starting','running','stopping'),60)
                assert 'Sign in with OpenAI' in state.get('error',''),state.get('error')
                api('login',{})
                state=await_state(lambda s:s['auth']['status']!='connecting',60)
                # Never print the device code, login ID or credential material.
                assert state['auth']['status']=='waiting',state['auth']['status']
                api('login-cancel',{})
                state=await_state(lambda s:s['auth']['status']!='waiting',30)
                assert state['auth']['status']=='cancelled',state['auth']['status']
                assert lab.topology==before and not lab.runtime
                print('PASS: native account check, unauthenticated guard, device-code request and cancellation; no account login or inference',flush=True)
                return
            for model in ([] if args.approvals_only else args.model):
                settings={'endpoint':args.ollama_url,'model':model,'context_window':32768,'turn_timeout':300}
                api('settings',settings)
                assert api('check',settings)['available'],model+' not installed'
                request_id=uuid.uuid4().hex
                payload={'prompt':'Read the lab state, device profiles and authoring guide. Preview a connected topology with one FRR router, one LL2S switch and one Alpine PC, with a short original VLAN exercise. Use node aliases and omit link IDs. Do not apply or start anything.','request_id':request_id}
                if args.protocol_preview:
                    payload['prompt'] = (
                        'Create a topology with OSPF and STP, use only FRR routers and LL2S switches, '
                        'add some PCs so we can test connectivity. Use 3 FRR routers, 3 LL2S switches '
                        'and 3 Alpine PCs. Include working OSPF and STP configuration. '
                        'For this test preview only; do not apply or start anything.')
                api('turn',payload)
                api('turn',payload)  # Idempotent submission even if HTTP response were lost.
                state=await_state(lambda s:s['status'] not in ('starting','running','stopping'))
                assert state['status']=='completed',state
                assert state['proposals'],state
                proposal=api('proposal',{'id':state['proposals'][-1]})
                assert len(proposal['topology']['nodes'])==(9 if args.protocol_preview else 3),proposal
                if args.protocol_preview:
                    assert not lab_automation.switching_warnings(proposal['topology']),proposal['warnings']
                    for node in proposal['topology']['nodes']:
                        if node['type']=='router': assert 'router ospf' in node.get('startup_config',''),node
                        elif node['type']=='switch': assert 'spanning-tree' in node.get('startup_config',''),node
                        else: assert node.get('ipv4') and node.get('gateway'),node
                    print('PASS: protocol preview has a switch cycle, OSPF/STP snippets and PC addressing (not boot-tested)',flush=True)
                assert proposal['instructions_markdown'].strip(),proposal
                assert len([m for m in state['messages'] if m['role']=='user'])==1,state
                assert lab.topology==before and not lab.runtime
                print('PASS:',model,'preview and unchanged lab',flush=True)
                # A second turn tests thread resume with a fresh app-server process/relay config.
                api('turn',{'prompt':'Explain this topology in detail. Do not call tools.','request_id':uuid.uuid4().hex})
                await_state(lambda s:s['status']=='running')
                api('stop',{})
                state=await_state(lambda s:s['status'] not in ('starting','running','stopping'),30)
                assert state['status']=='interrupted',state
                retained=state['messages']
                subprocess.run(['docker','stop','-t','15',ident],check=True,stdout=subprocess.DEVNULL)
                subprocess.run(['docker','rm',ident],check=True,stdout=subprocess.DEVNULL)
                start_container()
                state=api('state')
                assert state['messages']==retained,state
                # Verify resumed conversation can actually complete after container recreation.
                api('turn',{'prompt':'Reply with one sentence naming the three device families in the earlier proposal. Do not call tools.','request_id':uuid.uuid4().hex})
                state=await_state(lambda s:s['status'] not in ('starting','running','stopping'))
                assert state['status']=='completed',state
                assert len([m for m in state['messages'] if m['role']=='user'])==3,state
                print('PASS:',model,'Stop, container recreation, retained conversation and continued turn',flush=True)
            if args.approvals or args.approvals_only:
                # Rebind only this disposable bridge to fake echo nodes. Never use the active lab.
                import test_lab
                fixture = test_lab.LabTests(); fixture.setUp()
                bridge.lab = bridge.automation.lab = fixture.lab
                try:
                    settings={'endpoint':args.ollama_url,'model':args.model[0],
                              'context_window':32768,'turn_timeout':300,
                              'lab_changes':True,'console_input':True}
                    api('settings',settings)
                    api('turn',{'prompt':
                        'This is an isolated transport test with fake echo devices, not vendor operating systems. '
                        'Read the lab state, then start_node with node_id r1 using the current revision. '
                        'After the browser approves start, also start r2 using the returned state revision. '
                        'Then call get_console_output for r1. '
                        'Then call send_console_input with exactly the text approval-probe followed by a carriage return, '
                        'using the read cursor/revision. The browser will approve that exact operation. '
                        'Do not send any other commands or change the topology. Report the returned console evidence.',
                        'request_id':uuid.uuid4().hex})
                    decided=[]
                    def approve_test_operations(state):
                        for item in state.get('approvals',[]):
                            if item['status']!='pending' or item['id'] in decided: continue
                            arguments=item['arguments']
                            allowed=(item['operation']=='start-node' and arguments.get('node_id') in ('r1','r2') or
                                item['operation']=='console-send' and arguments.get('node_id')=='r1' and
                                arguments.get('input')=='approval-probe\r')
                            if allowed:
                                moved=copy.deepcopy(fixture.lab.topology)
                                moved['nodes'][0]['x']+=5
                                fixture.lab.save(moved)
                            api('decision',{'id':item['id'],'approve':allowed})
                            decided.append(item['id'])
                            assert allowed, item
                            print('Approved disposable test operation:',item['operation'],flush=True)
                        return state['status'] not in ('starting','running','stopping')
                    state=await_state(approve_test_operations)
                    assert state['status']=='completed',state
                    completed=[a['operation'] for a in state['approvals'] if a['status']=='completed']
                    assert 'start-node' in completed and 'console-send' in completed,state
                    assert set(fixture.lab.runtime)=={'r1','r2'},state
                    output=bridge.automation.console_read('r1',wait_seconds=.2)['output']
                    assert 'RX:approval-probe' in output,output
                    print('PASS: real model started both nodes and sent approved input despite layout edits; echo verified',flush=True)
                finally:
                    bridge.lab=bridge.automation.lab=lab
                    fixture.tearDown()
            # Deliberately unavailable provider: surfaced failure and no infinite reconnect.
            bridge.providers.append('http://127.0.0.1:9/v1')
            offline={'endpoint':bridge.providers[-1],'model':args.model[-1],
                     'context_window':32768,'turn_timeout':30}
            api('settings',offline)
            api('turn',{'prompt':'Reply hello.','request_id':uuid.uuid4().hex})
            failed=await_state(lambda s:s['status'] not in ('starting','running','stopping'),45)
            assert failed['status'] in ('interrupted','failed') and failed['error'],failed
            assert lab.topology==before and not lab.runtime
            print('PASS: provider outage ends explicitly without changes',flush=True)
        finally:
            subprocess.run(['docker','logs','--tail','20',ident],check=False)
            subprocess.run(['docker','rm','-f',ident],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['docker','volume','rm',ident],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            server.shutdown();server.server_close();bridge.close();lab.file_lock.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ollama-url',default='http://127.0.0.1:11434/v1')
    parser.add_argument('--model',action='append')
    parser.add_argument('--tool-gateway-only',action='store_true',help='Use a simulated Code Mode model and real packaged MCP against an empty lab')
    parser.add_argument('--openai-login-only',action='store_true',help='Request/cancel a device code with disposable storage; never authenticate or infer')
    parser.add_argument('--approvals-only',action='store_true',help='Run only approved operations and provider outage checks')
    parser.add_argument('--approvals',action='store_true',help='Also test approved starts/input on disposable echo nodes')
    parser.add_argument('--protocol-preview',action='store_true',help='Check a nine-node OSPF/STP design instead of the small VLAN preview')
    parser.add_argument('--image',default='weblab-agent:dev')
    args=parser.parse_args()
    if not args.model: args.model=['gpt-oss:20b']
    if args.openai_login_only and args.tool_gateway_only:
        parser.error('Choose one isolated protocol test')
    if (args.openai_login_only or args.tool_gateway_only) and (args.approvals or args.approvals_only or args.protocol_preview):
        parser.error('--openai-login-only cannot be combined with inference tests')
    main(args)
