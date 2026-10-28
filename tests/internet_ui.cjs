// Disposable HTTP fixture; no Docker containers or current lab used.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fixture=spawn('python3',['-u','-c',`
import sys
sys.path.insert(0,'tests')
from test_lab import LabTests
t=LabTests();t.setUp()
try:
 t.lab.save({'name':'Cloud UI','nodes':[],'links':[]})
 print('http://127.0.0.1:'+str(t.port),flush=True)
 sys.stdin.readline()
finally:t.tearDown()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data',d=>log+=d);
(async()=>{
 let browser;
 try {
  const url=await new Promise((resolve,reject)=>{let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(out.trim());});fixture.once('exit',code=>reject(Error(log||String(code))));});
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  await page.addInitScript(()=>localStorage.setItem('weblab.theme.v1','light'));
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.waitForFunction(()=>loaded);
  await page.locator('.device-template[data-type=cloud]').dblclick();
  await page.waitForFunction(()=>topology.nodes.length===1&&!busy);
  assert.equal(await page.evaluate(()=>topology.nodes[0].type),'cloud');
  await page.locator('.map-node').click();
  const panel=page.locator('.internet-panel');await panel.waitFor({state:'visible'});
  assert.equal(await panel.locator('[data-part=address]').inputValue(),'192.0.2.1/24');
  await panel.locator('[data-part=add]').click();
  await panel.locator('[data-field=destination]').fill('10.44.0.0/16');
  await panel.locator('[data-field=via]').fill('192.0.2.2');
  await panel.locator('[data-part=add-forward]').click();
  const forwards=panel.locator('[data-part=forwards] > li');
  assert.equal(await forwards.nth(0).locator('[data-forward-field=bind_address]').inputValue(),'127.0.0.1');
  await forwards.nth(0).locator('[data-forward-field=external_port]').fill('22022');
  await forwards.nth(0).locator('[data-forward-field=target_address]').fill('10.44.0.2');
  await forwards.nth(0).locator('[data-forward-field=target_port]').fill('22');
  await panel.locator('[data-part=add-forward]').click();
  await forwards.nth(1).locator('[data-forward-remove]').click();
  await panel.locator('[data-part=default-action]').selectOption('deny');
  await panel.locator('[data-part=add-rule]').click();
  const rules=panel.locator('[data-part=rules] > li');
  assert.equal(await rules.nth(0).locator('[data-rule-up]').isDisabled(),true);
  assert.equal(await rules.nth(0).locator('[data-rule-field=destination_port]').isDisabled(),true);
  await rules.nth(0).locator('[data-rule-field=protocol]').selectOption('tcp');
  await rules.nth(0).locator('[data-rule-field=destination]').fill('203.0.113.7');
  await rules.nth(0).locator('[data-rule-field=destination_port]').fill('443');
  await panel.locator('[data-part=add-rule]').click();
  await rules.nth(1).locator('[data-rule-field=action]').selectOption('deny');
  await rules.nth(1).locator('[data-rule-field=destination]').fill('10.0.0.0/8');
  await rules.nth(1).locator('[data-rule-up]').click();
  assert.equal(await rules.nth(0).locator('[data-rule-field=destination]').inputValue(),'10.0.0.0/8');
  await rules.nth(0).locator('[data-rule-down]').click();
  await panel.locator('[data-part=add-rule]').click();
  await rules.nth(2).locator('[data-rule-field=protocol]').selectOption('udp');
  await rules.nth(2).locator('[data-rule-field=destination_port]').fill('53');
  await rules.nth(2).locator('[data-rule-field=protocol]').selectOption('icmp');
  assert.equal(await rules.nth(2).locator('[data-rule-field=destination_port]').inputValue(),'');
  await rules.nth(2).locator('[data-rule-remove]').click();
  await panel.locator('[data-part=save]').click();
  await page.waitForFunction(()=>!busy&&topology.nodes[0].internet.routes.length===1);
  assert.deepEqual(await page.evaluate(()=>topology.nodes[0].internet.routes),[{destination:'10.44.0.0/16',via:'192.0.2.2'}]);
  assert.deepEqual(await page.evaluate(()=>topology.nodes[0].internet.port_forwards),[{
    protocol:'tcp',bind_address:'127.0.0.1',external_port:22022,target_address:'10.44.0.2',target_port:22,source:''}]);
  assert.deepEqual(await page.evaluate(()=>topology.nodes[0].internet.firewall),{default_action:'deny',rules:[
    {action:'allow',protocol:'tcp',source:'',destination:'203.0.113.7/32',source_port:'',destination_port:'443'},
    {action:'deny',protocol:'any',source:'',destination:'10.0.0.0/8',source_port:'',destination_port:''}]});
  // Inspector apply must preserve cloud settings and its single port.
  await panel.locator('[data-part=close]').click();
  await page.locator('#node-name').fill('Uplink');await page.locator('#apply-node').click();
  await page.waitForFunction(()=>!busy&&topology.nodes[0].name==='Uplink');
  assert.equal(await page.evaluate(()=>topology.nodes[0].ethernet),1);
  await page.locator('#cloud-settings-open').click();
  await panel.locator('[data-part=maximize]').click();await panel.locator('[data-part=maximize]').click();
  await page.setViewportSize({width:390,height:740});
  const box=await panel.boundingBox();assert.ok(box.x>=0&&box.width<=390);
  await forwards.nth(0).scrollIntoViewIfNeeded();
  const forwardBox=await forwards.nth(0).boundingBox();
  assert.ok(forwardBox.x>=box.x&&forwardBox.x+forwardBox.width<=box.x+box.width);
  await page.screenshot({path:'/tmp/weblab-internet-port-forward-mobile.png'});
  await rules.nth(0).scrollIntoViewIfNeeded();
  const ruleBox=await rules.nth(0).boundingBox();
  assert.ok(ruleBox.x>=box.x&&ruleBox.x+ruleBox.width<=box.x+box.width);
  await page.screenshot({path:'/tmp/weblab-internet-mobile.png'});
  await page.setViewportSize({width:1440,height:1000});
  await panel.locator('[data-part=maximize]').click();
  await rules.nth(0).scrollIntoViewIfNeeded();
  await page.screenshot({path:'/tmp/weblab-internet-settings.png'});
  // A real disposable echo router keeps running while the stopped cloud is edited.
  await page.evaluate(async()=>{
    const peer=newNode('router',800,300);topology.nodes.push(peer);window.internetPeer=peer.id;
    accept(await api('/api/topology','PUT',topology));
    accept(await api(`/api/nodes/${peer.id}/start`,'POST',{}));
  });
  await panel.locator('[data-field=destination]').fill('10.45.0.0/16');
  await forwards.nth(0).locator('[data-forward-field=target_address]').fill('10.45.0.2');
  await panel.locator('[data-part=save]').click();
  await page.waitForFunction(()=>!busy&&topology.nodes[0].internet.routes[0].destination==='10.45.0.0/16');
  assert.equal(await page.evaluate(()=>state(internetPeer)),'running');
  assert.equal(await panel.locator('[data-part=address]').isDisabled(),false);
  // Simulate a running helper for UI checks; kernel updates have separate native tests.
  const cloudId=await page.evaluate(()=>topology.nodes[0].id);
  const runningResponse=async route=>{
    const response=await route.fetch(),result=await response.json();
    if(result.status)result.status[cloudId]={state:'running'};
    await route.fulfill({response,json:result});
  };
  await page.route('**/api/state',runningResponse);
  await page.route(`**/api/nodes/${cloudId}/internet`,runningResponse);
  await page.evaluate(()=>{statuses[topology.nodes[0].id]={state:'running'};updateControls();});
  assert.equal(await panel.locator('[data-part=save]').isDisabled(),false);
  assert.equal(await panel.locator('[data-part=save]').textContent(),'Apply firewall');
  assert.equal(await panel.locator('[data-part=address]').isDisabled(),true);
  assert.equal(await panel.locator('[data-part=uplink]').isDisabled(),true);
  assert.equal(await panel.locator('[data-field=destination]').isDisabled(),true);
  assert.equal(await panel.locator('[data-part=add-forward]').isDisabled(),true);
  assert.equal(await forwards.nth(0).locator('[data-forward-field=target_port]').isDisabled(),true);
  assert.equal(await forwards.nth(0).locator('[data-forward-remove]').isDisabled(),true);
  assert.equal(await panel.locator('[data-part=default-action]').isDisabled(),false);
  assert.equal(await rules.nth(0).locator('[data-rule-field=protocol]').isDisabled(),false);
  assert.equal(await rules.nth(0).locator('[data-rule-field=destination_port]').isDisabled(),false);
  assert.equal(await rules.nth(0).locator('[data-rule-remove]').isDisabled(),false);
  await panel.locator('[data-part=default-action]').selectOption('allow');
  const saved=page.waitForResponse(response=>response.url().endsWith(`/api/nodes/${cloudId}/internet`)&&response.request().method()==='PUT');
  await panel.locator('[data-part=save]').click();
  assert.equal((await saved).status(),200);
  await page.waitForFunction(()=>!busy&&topology.nodes[0].internet.firewall.default_action==='allow');
  assert.equal(await page.evaluate(()=>state(internetPeer)),'running');
  await page.unroute('**/api/state',runningResponse);
  await page.unroute(`**/api/nodes/${cloudId}/internet`,runningResponse);
  await page.reload();await page.waitForFunction(()=>loaded);
  await page.locator(`.map-node[data-id="${cloudId}"]`).click();
  assert.equal(await page.locator('.internet-panel [data-field=destination]').inputValue(),'10.45.0.0/16');
  assert.equal(await panel.locator('[data-part=default-action]').inputValue(),'allow');
  assert.equal(await forwards.nth(0).locator('[data-forward-field=target_address]').inputValue(),'10.45.0.2');
  assert.equal(await rules.nth(0).locator('[data-rule-field=destination_port]').inputValue(),'443');
  assert.equal(await rules.nth(1).locator('[data-rule-field=destination]').inputValue(),'10.0.0.0/8');
  // Another editor's saved policy cannot be silently overwritten by this open form.
  await page.evaluate(async()=>{
    const node=topology.nodes[0],previous=structuredClone(node.internet);
    accept(await api(`/api/nodes/${node.id}/internet`,'PUT',{
      expected_internet:previous,internet:{...previous,firewall:{...previous.firewall,default_action:'deny'}}}));
  });
  await rules.nth(0).locator('[data-rule-field=destination_port]').fill('444');
  const stale=page.waitForResponse(response=>response.url().endsWith(`/api/nodes/${cloudId}/internet`)&&response.request().method()==='PUT');
  await panel.locator('[data-part=save]').click();
  assert.equal((await stale).status(),409);
  await page.waitForFunction(()=>!busy);
  assert.match(await page.locator('#toast').textContent(),/settings changed/);
  assert.equal(await page.evaluate(()=>topology.nodes[0].internet.firewall.default_action),'deny');
  assert.equal(await rules.nth(0).locator('[data-rule-field=destination_port]').inputValue(),'444');
  await panel.locator('[data-part=close]').click();
  await page.locator('#cloud-settings-open').click();
  assert.equal(await panel.locator('[data-part=default-action]').inputValue(),'deny');
  assert.equal(await rules.nth(0).locator('[data-rule-field=destination_port]').inputValue(),'443');
  // Server permissions are separately checked over HTTP; exercise UI read-only controls.
  await page.evaluate(()=>{Object.assign(weblabAuth,{enabled:true,role:'writer'});weblabInternet.refresh();});
  assert.equal(await panel.locator('[data-part=default-action]').isDisabled(),true);
  assert.equal(await panel.locator('[data-part=add-rule]').isDisabled(),true);
  assert.equal(await panel.locator('[data-part=add-forward]').isDisabled(),true);
  assert.equal(await rules.nth(0).locator('[data-rule-field=action]').isDisabled(),true);
  await page.evaluate(()=>{weblabAuth.role='admin';weblabInternet.refresh();});
  assert.equal(await panel.locator('[data-part=default-action]').isDisabled(),false);
  assert.equal(await rules.nth(0).locator('[data-rule-field=destination_port]').isDisabled(),false);
  assert.deepEqual(errors,[]);
  console.log('PASS cloud library, ordered firewall editing, live apply UI, port-forward editing/persistence/scope/role controls, stopped-cloud edits with a running peer, inspector and responsive bounds');
 } finally {if(browser)await browser.close();fixture.stdin.end('\n');}
})().catch(e=>{console.error(e,log);process.exitCode=1;});
