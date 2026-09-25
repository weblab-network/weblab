// Disposable echo devices: no Cisco images, Docker or active-lab access.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fixture = spawn('python3', ['-u', '-c', `
import sys
sys.path.insert(0, 'tests')
from test_lab import LabTests
from test_iol_l1 import L1LifecycleTests
from unittest.mock import patch
test=LabTests(); test.setUp()
try:
    with patch('iol_l1.PROFILES', {'fake.bin'}):
        L1LifecycleTests.prepare(test)
        for node in test.topology['nodes']: node.pop('iol_l1', None)
        test.lab.save(test.topology)
        print('http://127.0.0.1:'+str(test.port), flush=True)
        sys.stdin.readline()
finally:
    test.tearDown()
`], {cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log=''; fixture.stderr.on('data',data=>log+=data);
(async()=>{
 let browser;
 try {
  const url=await new Promise((resolve,reject)=>{
   let output=''; fixture.stdout.on('data',data=>{output+=data;if(output.includes('\n'))resolve(output.trim());});
   fixture.once('exit',()=>reject(new Error(log)));
  });
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1400,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.waitForFunction(()=>loaded);
  await page.evaluate(()=>selectNode('r1', false));
  assert.equal(await page.locator('#iol-l1-settings').isVisible(),true);
  assert.equal(await page.locator('#node-iol-l1').isChecked(),false);
  assert.match(await page.locator('#iol-l1-help').innerText(),/full CPU core/);
  await page.locator('#start-all').click();
  await page.waitForFunction(()=>!busy && state('r1')==='running' && state('r2')==='running');
  assert.equal(await page.locator('#node-iol-l1').isDisabled(),true);
  await page.evaluate(()=>openLinkActions('cable'));
  assert.match(await page.locator('[data-link-carrier=a]').innerText(),/L1 off/);
  assert.equal(await page.locator('#link-iol-l1-help').isVisible(),true);
  assert.equal(await page.locator('[data-link-carrier=a]').isDisabled(),true);
  await page.locator('[data-link-traffic=a]').click();
  await page.waitForFunction(()=>!busy && linkStates.cable.blocked_a_to_b);
  await page.locator('#link-actions-close').click();
  await page.locator('#stop-all').click();await page.waitForFunction(()=>!busy && !hasRunning());
  await page.locator('#node-iol-l1').check();await page.locator('#apply-node').click();
  await page.waitForFunction(()=>!busy && nodeById('r1').iol_l1===true);
  await page.reload();await page.waitForFunction(()=>loaded);
  await page.evaluate(()=>selectNode('r1', false));
  assert.equal(await page.locator('#node-iol-l1').isChecked(),true);
  await page.locator('#start-all').click();
  await page.waitForFunction(()=>!busy && linkStates.cable.carrier_supported_a);
  assert.equal(await page.evaluate(()=>linkStates.cable.carrier_supported_b),false);
  await page.evaluate(()=>openLinkActions('cable'));
  assert.equal(await page.locator('[data-link-carrier=a]').isDisabled(),false);
  assert.equal(await page.locator('[data-link-carrier=b]').isDisabled(),true);
  await page.locator('[data-link-carrier=a]').click();
  await page.waitForFunction(()=>!busy && linkStates.cable.carrier_a==='down');
  await page.locator('#link-actions-close').click();
  await page.locator('#stop-all').click();await page.waitForFunction(()=>!busy && !hasRunning());
  await page.evaluate(()=>edit(()=>{topology.links=[];}));
  await page.locator('#node-image').selectOption('quay.io/frrouting/frr:10.7.1');
  assert.equal(await page.locator('#iol-l1-settings').isVisible(),false);
  await page.locator('#apply-node').click();await page.waitForFunction(()=>!busy && isFrr(nodeById('r1')));
  assert.equal(await page.evaluate(()=>Object.hasOwn(nodeById('r1'),'iol_l1')),false);
  assert.deepEqual(errors,[]);
  console.log('PASS IOL L1 default-off, saved checkbox, per-node launch, frame blocking, carrier controls and image change');
 } finally {
  if(browser)await browser.close();
  fixture.stdin.end('\n');await once(fixture,'exit');
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
