#!/usr/bin/env python3
"""Opt-in packaging smoke: fresh named volumes, no host files, network or real lab."""
import argparse
import json
import subprocess
import time
import uuid


def main(args):
    name='weblab-agent-package-'+uuid.uuid4().hex[:10]
    core,companion=name+'-core',name+'-companion'
    sockets,history,bridge=name+'-sockets',name+'-history',name+'-bridge'
    def docker(*items,check=True):
        return subprocess.run(['docker',*items],check=check,text=True,capture_output=True)
    def start_companion():
        docker('run','-d','--name',companion,'--network','none','--read-only','--init',
               '--cap-drop','ALL','--security-opt','no-new-privileges','--memory','1g',
               '--user','10001:10001','--pids-limit','128','--cpus','1',
               '--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
               '--tmpfs','/workspace:rw,nosuid,nodev,uid=10001,gid=10001,size=32m',
               '-v',sockets+':/run/weblab-agent','-v',history+':/home/agent',args.agent)
    def start_core():
        docker('run','-d','--name',core,'--network','none',
               '-v',sockets+':/run/weblab-agent','-e','WL_AGENT_SOCKET_DIR=/run/weblab-agent',
               '-v',bridge+':/agent-state','-e','WL_AGENT_STATE_DIR=/agent-state',
               '-e','WL_AGENT_PROVIDERS=http://127.0.0.1:9/v1',args.core)
    def check(program):
        end=time.monotonic()+20
        while True:
            result=docker('exec',core,'python3','-c',program,check=False)
            if result.returncode==0:return result.stdout
            if time.monotonic()>end:raise AssertionError(result.stderr+'\n'+docker('logs',companion,check=False).stderr)
            time.sleep(.5)
    def run_check(program):
        # Mutating requests must not be retried as a readiness probe.
        result=docker('exec',core,'python3','-c',program,check=False)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout
    try:
        start_companion()
        start_core()
        # Distribution must carry licenses and all runtime modules, but no private assets.
        docker('exec',companion,'/opt/mcp/bin/python','-c', '''
import sys
from pathlib import Path
sys.path.insert(0,'/app')
import agent_companion, agent_auth, agent_attachments
assert Path('/usr/local/share/licenses/codex/Codex-LICENSE.txt').is_file()
assert Path('/usr/local/share/licenses/codex/Codex-NOTICE.txt').is_file()
for name in ('private-notes', '.git', 'images', 'lab-data'):
 assert not (Path('/app')/name).exists(), name
''')
        info=json.loads(docker('inspect',companion).stdout)[0]
        assert info['Config']['User']=='10001:10001'
        assert info['HostConfig']['NetworkMode']=='none' and info['HostConfig']['ReadonlyRootfs']
        assert {m['Destination'] for m in info['Mounts'] if m['Type']=='volume'}=={'/run/weblab-agent','/home/agent'}
        program='''
import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError
base='http://127.0.0.1:8080'
def api(path,data=None,token=''):
 req=Request(base+path,None if data is None else json.dumps(data).encode(),
  {'Content-Type':'application/json','X-Weblab-Agent-Session':token})
 try: return json.load(urlopen(req,timeout=3))
 except HTTPError as exc: raise RuntimeError(exc.read().decode()) from None
assert api('/api/agent/info')['enabled']
'''
        ready=program+'''\nfrom pathlib import Path
assert Path('/run/weblab-agent/companion.sock').exists()
'''
        check(ready)
        record=json.loads(run_check(program+'''
token=api('/api/agent/session',{})['token']
# Explicit New conversation persists a draft; an untouched empty draft is ephemeral.
api('/api/agent/reset',{},token)
state=api('/api/agent/state',token=token)
assert state['status']=='idle'
assert not state['settings']['lab_changes'] and not state['settings']['console_input']
assert not state['settings']['auto_approve']
assert not state['auth']['signed_in']
assert api('/api/state')['topology']['nodes']==[]
print(json.dumps({'token':token,'conversation_id':state['conversation_id']}))
'''))
        for container in (core,companion):docker('rm','-f',container)
        start_companion();start_core()
        check(ready)
        run_check(program+f'\ntoken={record["token"]!r}\nconversation_id={record["conversation_id"]!r}\n'+'''
state=api('/api/agent/state',token=token)
assert state['status']=='idle' and state['conversation_id']==conversation_id
assert api('/api/agent/reset',{},token)['conversation_id']!=conversation_id
assert api('/api/agent/close',{},token)['closed']
''')
        print('PASS: packaged modules/notices, isolation, fresh volumes, session survives recreation, reset/close; no inference or guests')
    finally:
        for container in (core,companion):docker('rm','-f',container,check=False)
        for volume in (sockets,history,bridge):docker('volume','rm',volume,check=False)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',default='weblab-agent-core-test:local')
    p.add_argument('--agent',default='weblab-agent:dev')
    main(p.parse_args())
