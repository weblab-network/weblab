// Browser login and role controls against the real HTTP permission boundary.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fixture=spawn('python3',['-u','-c',`
import sys
sys.path.insert(0,'tests')
from test_lab import LabTests
from web_auth import Auth, ROLES
t=LabTests();t.setUp()
t.topology['nodes'][1]['x']=400
t.lab.save(t.topology)
t.server.auth=Auth(t.lab.directory/'.auth',{n:'test-unique-password1-'+n for n in ROLES})
try:
 print('http://127.0.0.1:'+str(t.port),flush=True)
 sys.stdin.readline()
finally:
 t.tearDown()
 t.server.auth.close()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let logs='';fixture.stderr.on('data',d=>logs+=d);
(async()=>{
 let browser;
 try {
 const base=await new Promise((resolve,reject)=>{let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(out.trim());});fixture.once('exit',()=>reject(Error(logs)));});
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const context=await browser.newContext(), page=await context.newPage(), errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const clickCable=async()=>{
  // A horizontal SVG path has zero bounding-box height despite its clickable stroke.
  const point=await page.locator('.cable-hit').evaluate(el=>{
   const p=el.getPointAtLength(el.getTotalLength()/2);
   const screen=new DOMPoint(p.x,p.y).matrixTransform(el.getScreenCTM());
   return {x:screen.x,y:screen.y};
  });
  await page.mouse.click(point.x,point.y);
 };
 await page.goto(base); await page.locator('#auth-dialog[open]').waitFor();
 assert.equal((await page.request.get(base+'/api/state')).status(),401);
 // The fourth attempt exercises the real backend delay, with visible progress.
 for(let attempt=0;attempt<3;attempt++) {
  await page.fill('#auth-form input[type=password]','incorrect-password');
  await page.click('#auth-form button');
  await page.waitForFunction(()=>!document.querySelector('#auth-form button').disabled && document.getElementById('auth-error').textContent.includes('Invalid'));
  assert.ok(await page.locator('#auth-progress').isHidden());
 }
 for(const name of ['admin','user','guest']) {
  await page.selectOption('#auth-form select',name);
  await page.fill('#auth-form input[type=password]','test-unique-password1-'+name);
  await page.check('#auth-form input[type=checkbox]');
  await page.click('#auth-form button');
  if(name==='admin') {
   await page.locator('#auth-progress').waitFor();
   assert.match(await page.locator('#auth-progress').textContent(),/Checking credentials/);
   assert.equal(await page.locator('#auth-form').getAttribute('aria-busy'),'true');
   assert.ok(await page.locator('#auth-form button').isDisabled());
   assert.ok(await page.locator('#auth-form input[type=password]').isDisabled());
  }
  await page.waitForFunction(()=>typeof loaded !== 'undefined' && loaded);
  assert.ok(await page.locator('#auth-dialog').isHidden());
  assert.ok((await page.locator('#auth-logout').boundingBox()).width <= 40);
  assert.match(await page.locator('#auth-logout').getAttribute('aria-label'),new RegExp('Log out.*'+name));
  assert.ok(await page.locator('#auth-logout').getAttribute('title'));
  assert.equal(await page.locator('#export').isEnabled(),name==='admin');
  assert.equal(await page.locator('#upload-image').isEnabled(),name==='admin');
  assert.equal(await page.locator('#instructions-open').isEnabled(),name==='admin','Instructions picker follows the authenticated role after startup');
  assert.equal(await page.locator('#start-all').isEnabled(),name!=='guest');
  assert.ok(await page.locator('#open-agent').isHidden());
  await page.reload();await page.waitForFunction(()=>typeof loaded !== 'undefined' && loaded);
  assert.ok(await page.locator('#auth-dialog').isHidden());
  // Stopped cables offer deletion only to administrators.
  await clickCable();
  assert.equal(await page.locator('#delete-dialog').isVisible(),name==='admin');
  if(name==='admin') await page.locator('#cancel-delete').click();
  assert.equal(await page.evaluate(()=>topology.links.length),1);
  if(name!=='admin') {
   // Mouse release must keep selection, including through refresh and forbidden drags.
   const node=page.locator('.map-node[data-id=r1]');
   const original=await page.evaluate(()=>topology.nodes.map(n=>({id:n.id,x:n.x,y:n.y})));
   let topologyWrites=0;
   const onRequest=request=>{if(request.method()==='PUT' && request.url().endsWith('/api/topology'))topologyWrites++;};
   page.on('request',onRequest);
   const pressed=await node.elementHandle(), before=await node.boundingBox();
   await page.mouse.move(before.x+before.width/2,before.y+before.height/2);
   await page.mouse.down();
   // Replacing the pressed element makes some browsers retarget the later click
   // to the canvas, whose background handler then clears the selection.
   assert.ok(await pressed.evaluate(el=>el.isConnected),'Keep the pressed node attached until release');
   await page.mouse.up();
   assert.equal(await page.evaluate(()=>selected),'r1');
   assert.ok(await node.evaluate(el=>el.classList.contains('selected')));
   assert.ok(await page.locator('#node-name').isVisible());
   await page.evaluate(()=>refresh());
   assert.equal(await page.evaluate(()=>selected),'r1');
   const box=await node.boundingBox();
   await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
   await page.mouse.move(box.x+box.width/2+50,box.y+box.height/2+30,{steps:4});await page.mouse.up();
   assert.equal(await page.evaluate(()=>selected),'r1');
   assert.deepEqual(await page.evaluate(()=>topology.nodes.map(n=>({id:n.id,x:n.x,y:n.y}))),original);
   assert.equal(topologyWrites,0);
   page.off('request',onRequest);
  }
  if(name==='user') {
   // Selecting a running node must still open its console and keep its inspector.
   await page.evaluate(async()=>accept(await api('/api/nodes/r1/start','POST',{})));
   await page.locator('.map-node[data-id=r1]').click();
   await page.waitForFunction(()=>consoleSessions.has('r1'));
   assert.equal(await page.evaluate(()=>selected),'r1');
   assert.ok(await page.locator('#node-name').isVisible());
   assert.ok(await page.locator('#apply-node').isDisabled());
   await page.keyboard.press('l');
   assert.equal(await page.evaluate(()=>mode),'select');
   assert.equal(await page.evaluate(async()=> (await fetch('/api/topology',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(topology)})).status),403);
   await clickCable();
   assert.ok(await page.locator('#link-actions-dialog').isVisible());
   assert.ok(await page.locator('#link-actions-delete').isDisabled());
   await page.locator('#link-actions-close').click();
   await page.evaluate(async()=>accept(await api('/api/lab/stop','POST',{})));
  }
  await page.click('#auth-logout');await page.locator('#auth-dialog[open]').waitFor();
  assert.equal((await page.request.get(base+'/api/state')).status(),401);
 }
 assert.deepEqual(errors,[]);
 console.log('Auth UI passed: login, remember/reload, logout, roles and direct API denial.');
 } finally { if(browser)await browser.close();fixture.stdin.end('\n'); }
})().catch(e=>{console.error(e);console.error(logs);process.exitCode=1;});
