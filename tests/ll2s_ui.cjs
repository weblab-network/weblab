// Disposable HTTP fixture; no Docker containers or network devices are started.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fixture = spawn('python3', ['-u', '-c', `
import sys,json
sys.path.insert(0,'tests')
from test_lab import LabTests
test=LabTests();test.setUp()
try:
    test.lab.save({'name':'LL2S UI','nodes':[],'links':[]})
    print(json.dumps({'url':'http://127.0.0.1:'+str(test.port)}),flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
`], {cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data',data=>log+=data);
(async()=>{
 let browser;
 try {
  const info=await new Promise((resolve,reject)=>{
   let text='';fixture.stdout.on('data',data=>{text+=data;if(text.includes('\n'))resolve(JSON.parse(text.trim()));});
   fixture.once('exit',code=>reject(new Error(`Fixture exited ${code}: ${log}`)));
  });
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1600,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(info.url);await page.waitForFunction(()=>loaded);
  await page.locator('.device-template[data-type="switch"]').click();
  await page.waitForFunction(()=>!busy && topology.nodes.length===1);
  await page.locator('#node-image').selectOption('ghcr.io/weblab-network/ll2s:0.2.0');
  assert.equal(await page.locator('#node-memory').inputValue(),'256');
  assert.equal(await page.locator('#node-ethernet').inputValue(),'4');
  assert.match(await page.locator('#ethernet-help').innerText(),/ll2sh/);
  assert.equal(await page.locator('#ethernet-label').innerText(),'Ethernet interfaces');
  // Editing a stopped LL2S count must never switch to IOL slot numbering.
  for (const count of [3, 4]) {
    await page.locator('#node-ethernet').selectOption(String(count));
    await page.locator('#apply-node').click();
    await page.waitForFunction(count => !busy && topology.nodes[0].ethernet === count, count);
    assert.deepEqual(await page.evaluate(() => nodePorts(topology.nodes[0])),
      Array.from({length:count}, (_,i) => `eth${i}`));
    assert.equal(await page.locator('#ethernet-label').innerText(),'Ethernet interfaces');
  }
  await page.locator('#node-ethernet').selectOption('8');
  await page.locator('#apply-node').click();
  await page.waitForFunction(()=>!busy && topology.nodes[0].ethernet===8);
  assert.match(await page.locator('#interfaces').innerText(),/eth7/);
  assert.equal(await page.locator('.node-type').first().innerText(),'LL2S SWITCH');
  await page.locator('#export').click();
  assert.equal(await page.locator('#export-initial').isDisabled(),true);
  assert.equal(await page.locator('#export-saved').isDisabled(),false);
  assert.match(await page.locator('#export-message').innerText(),/LL2S/);
  await page.locator('#cancel-export').click();
  // Even a running LL2S cannot select Cisco live capture.
  await page.evaluate(()=>{statuses[topology.nodes[0].id]={state:'running'};});
  await page.locator('#export').click();
  assert.equal(await page.locator('#export-initial').isDisabled(),true);
  await page.locator('#cancel-export').click();
  await page.reload();await page.waitForFunction(()=>loaded);
  assert.equal(await page.evaluate(()=>nodePorts(topology.nodes[0]).join(',')),
    'eth0,eth1,eth2,eth3,eth4,eth5,eth6,eth7');
  await page.locator('.device-template[data-type="router"]').click();
  await page.waitForFunction(()=>!busy && topology.nodes.length===2);
  assert.equal(await page.locator('#node-image option[value="ghcr.io/weblab-network/ll2s:0.2.0"]').count(),0);
  await page.evaluate(()=>{
    setMode('link'); connectNode(topology.nodes[0].id); connectNode(topology.nodes[1].id);
  });
  assert.deepEqual(await page.locator('#link-a-port option').allTextContents(),
    ['eth0','eth1','eth2','eth3','eth4','eth5','eth6','eth7']);
  await page.locator('#link-a-port').selectOption('eth7');
  await page.locator('#confirm-link').click();
  await page.waitForFunction(()=>!busy && topology.links.length===1);
  assert.equal(await page.evaluate(()=>topology.links[0].a.port),'eth7');
  await page.reload();await page.waitForFunction(()=>loaded);
  assert.equal(await page.evaluate(()=>topology.links[0].a.port),'eth7');
  // Existing labs keep their legacy selector and Ethernet mapping after reload.
  await page.evaluate(async()=>{
    topology.nodes[0].image='ll2s:dev';
    await api('/api/topology','PUT',topology);
  });
  await page.reload();await page.waitForFunction(()=>loaded);
  await page.evaluate(()=>selectNode(topology.nodes[0].id, false));
  assert.equal(await page.locator('#node-image').inputValue(),'ll2s:dev');
  assert.equal(await page.evaluate(()=>nodePorts(topology.nodes[0]).length),8);
  assert.match(await page.locator('#interfaces').innerText(),/eth7/);
  assert.deepEqual(errors,[]);
  console.log('LL2S UI passed');
 } finally {
  if(browser)await browser.close();
  fixture.stdin.end('\n');await once(fixture,'exit');
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
