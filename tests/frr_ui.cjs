// No Docker/node starts: disposable HTTP fixture and built-in FRR catalog entry.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fixture = spawn('python3', ['-u', '-c', `
import sys,json,shutil
sys.path.insert(0,'tests')
from test_lab import LabTests
test=LabTests();test.setUp()
try:
    shutil.copy(test.root/'fake.bin', test.root/'fake-l2.bin')
    test.lab.allow_untested_frr=True
    test.lab.save({'name':'FRR UI','nodes':[],'links':[]})
    print(json.dumps({'url':'http://127.0.0.1:'+str(test.port)}),flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
`], {cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data', data=>log+=data);
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
  await page.locator('.device-template[data-type="router"]').click();
  await page.waitForFunction(()=>!busy && topology.nodes.length===1);
  await page.locator('#node-image').selectOption('quay.io/frrouting/frr:10.7.1');
  assert.equal(await page.locator('#node-memory').inputValue(),'512');
  assert.equal(await page.locator('#node-ethernet').inputValue(),'4');
  assert.match(await page.locator('#ethernet-help').innerText(),/vtysh/);
  await page.locator('#node-ethernet').selectOption('8');
  await page.locator('#apply-node').click();await page.waitForFunction(()=>!busy && topology.nodes[0].ethernet===8);
  assert.match(await page.locator('#interfaces').innerText(),/eth7/);
  assert.match(await page.locator('#node-warning').innerText(),/Untested FRR images/);
  assert.match(await page.locator('#ethernet-help').innerText(),/Linux shell/);
  assert.equal(await page.locator('.node-type').first().innerText(),'FRR ROUTER');
  await page.locator('#export').click();
  assert.equal(await page.locator('#export-initial').isDisabled(),true);
  assert.equal(await page.locator('#export-saved').isDisabled(),false);
  assert.match(await page.locator('#export-message').innerText(),/FRR/);
  await page.locator('#cancel-export').click();
  await page.reload();await page.waitForFunction(()=>loaded);
  assert.equal(await page.evaluate(()=>nodePorts(topology.nodes[0]).join(',')),'eth0,eth1,eth2,eth3,eth4,eth5,eth6,eth7');
  await page.locator('.device-template[data-type="switch"]').click();await page.waitForFunction(()=>!busy && topology.nodes.length===2);
  assert.equal(await page.locator('#node-image option').filter({hasText:'quay.io/frrouting'}).count(),0);
  assert.equal(await page.locator('#node-warning').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log('FRR UI passed');
 } finally {
  if(browser)await browser.close();
  fixture.stdin.end('\n');await once(fixture,'exit');
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
