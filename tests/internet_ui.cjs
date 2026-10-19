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
  await panel.locator('[data-part=save]').click();
  await page.waitForFunction(()=>!busy&&topology.nodes[0].internet.routes.length===1);
  assert.deepEqual(await page.evaluate(()=>topology.nodes[0].internet.routes),[{destination:'10.44.0.0/16',via:'192.0.2.2'}]);
  // Inspector apply must preserve cloud settings and its single port.
  await panel.locator('[data-part=close]').click();
  await page.locator('#node-name').fill('Uplink');await page.locator('#apply-node').click();
  await page.waitForFunction(()=>!busy&&topology.nodes[0].name==='Uplink');
  assert.equal(await page.evaluate(()=>topology.nodes[0].ethernet),1);
  await page.locator('#cloud-settings-open').click();
  await panel.locator('[data-part=maximize]').click();await panel.locator('[data-part=maximize]').click();
  await page.setViewportSize({width:390,height:740});
  const box=await panel.boundingBox();assert.ok(box.x>=0&&box.width<=390);
  await page.screenshot({path:'/tmp/weblab-internet-mobile.png'});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:'/tmp/weblab-internet-settings.png'});
  await page.evaluate(()=>{statuses[topology.nodes[0].id]={state:'running'};updateControls();});
  assert.equal(await panel.locator('[data-part=save]').isDisabled(),true);
  assert.equal(await panel.locator('[data-part=address]').isDisabled(),true);
  await page.reload();await page.waitForFunction(()=>loaded);
  await page.locator('.map-node').click();
  assert.equal(await page.locator('.internet-panel [data-field=destination]').inputValue(),'10.44.0.0/16');
  assert.deepEqual(errors,[]);
  console.log('PASS cloud library, settings, persistence, inspector, bounds and running-state controls');
 } finally {if(browser)await browser.close();fixture.stdin.end('\n');}
})().catch(e=>{console.error(e,log);process.exitCode=1;});
