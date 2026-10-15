// Real auth, bridge, companion/history; deterministic inference, disposable data.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fixture=spawn('python3',['-u','-c',`
import sys, threading
sys.path.insert(0,'tests')
from test_lab import LabTests
from test_agent import FakeRPC
from web_auth import Auth, ROLES
from agent_transport import UnixServer
import agent_bridge, agent_companion
class RPC(FakeRPC):
 def call(self, method, params, **kwargs):
  result=super().call(method, params, **kwargs)
  if method=='turn/start':
   self.events.put({'method':'item/completed','params':{'item':{'id':'answer','type':'agentMessage','text':'Private administrator answer'}}})
   self.events.put({'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}})
  return result
agent_companion.RPC=RPC
t=LabTests(); t.setUp()
t.server.auth=Auth(t.lab.directory/'.auth',{n:'test-unique-password1-'+n for n in ROLES})
root=t.root/'agent'
manager=agent_companion.Manager(root/'sockets',root/'state',root/'codex')
companion=UnixServer(root/'sockets'/'companion.sock',agent_companion.Handler); companion.manager=manager
thread=threading.Thread(target=companion.serve_forever,daemon=True); thread.start()
bridge=agent_bridge.Bridge(t.lab,root/'sockets',root/'bridge','http://127.0.0.1:11434/v1',auth=t.server.auth)
t.server.agent=bridge
try:
 print('http://127.0.0.1:'+str(t.port),flush=True)
 sys.stdin.readline()
finally:
 bridge.close(); companion.shutdown(); companion.server_close(); thread.join()
 for session in list(manager.sessions.values()): session.close()
 t.server.auth.close(); t.tearDown()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let logs='';fixture.stderr.on('data',d=>logs+=d);
(async()=>{
 let browser;
 try {
  const base=await new Promise((resolve,reject)=>{let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(out.trim());});fixture.once('exit',()=>reject(Error(logs)));});
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const context=await browser.newContext(), page=await context.newPage(), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const login=async(name)=>{
   await page.locator('#auth-dialog[open]').waitFor();
   await page.selectOption('#auth-form select',name);
   await page.fill('#auth-form input[type=password]','test-unique-password1-'+name);
   await page.click('#auth-form button');
   await page.waitForFunction(()=>typeof loaded !== 'undefined' && loaded);
  };
  await page.goto(base); await login('admin');
  await page.locator('#open-agent').waitFor(); await page.click('#open-agent');
  await page.locator('#agent-auth-binding').waitFor();
  assert.match(await page.locator('#agent-auth-binding').textContent(),/logout or expiry/);
  await page.check('#agent-remember');
  await page.click('#agent-chat-tab');
  await page.fill('#agent-prompt','Inspect this lab'); await page.click('#agent-send');
  await page.waitForFunction(()=>document.querySelector('#agent-status').textContent.includes('completed'));
  assert.match(await page.locator('#agent-messages').textContent(),/Private administrator answer/);
  const old=await page.evaluate(()=>Object.entries(sessionStorage).find(([key])=>key.startsWith('weblab.agent.session.v1.')));
  assert.ok(old && old[1]);
  await page.reload(); await page.waitForFunction(()=>typeof loaded !== 'undefined' && loaded); await page.click('#open-agent');
  await page.waitForFunction(()=>document.querySelector('#agent-messages').textContent.includes('Private administrator answer'));
  // Another admin login has its own scope and cannot reopen this capability.
  const second=await browser.newContext(), other=await second.newPage();
  await other.goto(base); await other.locator('#auth-dialog[open]').waitFor();
  await other.fill('#auth-form input[type=password]','test-unique-password1-admin');
  await other.click('#auth-form button'); await other.waitForFunction(()=>typeof loaded !== 'undefined' && loaded);
  const response=await other.evaluate(async token=>{
   const r=await fetch('/api/agent/state',{headers:{'X-Weblab-Agent-Session':token}});return r.status;
  },old[1]);
  assert.equal(response,409);
  await page.click('#auth-logout'); await login('user');
  assert.ok(await page.locator('#open-agent').isHidden());
  assert.ok(!(await page.locator('body').textContent()).includes('Private administrator answer'));
  const denied=await page.evaluate(async token=>{
   const r=await fetch('/api/agent/state',{headers:{'X-Weblab-Agent-Session':token}});return r.status;
  },old[1]);
  assert.equal(denied,403);
  await page.click('#auth-logout'); await login('admin');
  await page.locator('#open-agent').waitFor(); await page.click('#open-agent');
  await page.locator('#agent-auth-binding').waitFor();
  const fresh=await page.evaluate(()=>Object.entries(sessionStorage).filter(([key])=>key.startsWith('weblab.agent.session.v1.')));
  assert.ok(fresh.some(([key,value])=>key!==old[0] && value!==old[1]));
  assert.ok(!(await page.locator('#agent-messages').textContent()).includes('Private administrator answer'));
  assert.deepEqual(errors,[]);
  console.log('PASS admin Agent: real authenticated session, turn/history, reload, independent admin login, writer denial and no account crossover.');
 } catch(error) {console.error(logs);throw error;}
 finally {if(browser)await browser.close();fixture.stdin.end('\n');await new Promise(resolve=>fixture.once('exit',resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
