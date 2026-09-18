// Real xterm + WebSocket wrapper tests against disposable PTY echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/link_actions.cjs
// Requires Python 3 and Perl; never connects to or edits the user's lab.
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
test = LabTests()
test.setUp()
profile = patch('iol_l1.supported', return_value=True)
profile.start()
try:
    L1LifecycleTests.prepare(test)
    image = test.root / 'fake.bin'
    code = image.read_text().replace("os.write(1, b'BOOT READY", "import threading\\nthreading.Thread(target=lambda: [l1.recv(100) for _ in iter(int, 1)], daemon=True).start()\\nos.write(1, b'BOOT READY")
    image.write_text(code)
    image = test.root / 'fake.bin'
    image.write_text(image.read_text().replace("b'RX:' + data", "b'HEX:' + data.hex().encode() + bytes([13, 10])"))
    test.topology['nodes'][0].update(x=180, y=160)
    test.topology['nodes'][1].update(x=420, y=180)
    test.lab.save(test.topology)
    test.lab.start_all()
    print('http://127.0.0.1:' + str(test.port), flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
    profile.stop()
`], {cwd: path.resolve(__dirname, '..'), stdio: ['pipe', 'pipe', 'pipe']});
let fixtureLog = '';
fixture.stderr.on('data', data => { fixtureLog += data; });

(async()=>{
 let browser;
 try {
  const base=await new Promise((resolve,reject)=>{let output='';fixture.stdout.on('data',d=>{output+=d;if(output.includes('\n'))resolve(output.trim());});fixture.once('exit',()=>reject(new Error(fixtureLog)));});
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1200,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.waitForFunction(()=>loaded);
  const initial=await page.evaluate(()=>JSON.stringify(topology));
  const processes=await page.request.get(base+'/api/state');assert.equal(processes.status(),200);
  await page.locator('.cable-hit').click();
  assert.equal(await page.locator('#link-actions-dialog').isVisible(),true);
  assert.equal(await page.locator('#link-actions-delete').isDisabled(),true);
  assert.equal(await page.locator('#link-carrier-actions').isVisible(),true);
  await page.waitForFunction(()=>!linkStates.cable.carrier_pending_a&&!linkStates.cable.carrier_pending_b);
  await page.locator('[data-link-traffic=a]').click();
  await page.waitForFunction(()=>!busy&&linkStates.cable.blocked_a_to_b&&!linkStates.cable.blocked_b_to_a);
  assert.equal(await page.locator('[data-link-traffic=a]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('[data-link-traffic=none]').getAttribute('aria-pressed'),'false');
  assert.match(await page.locator('#link-actions-state').innerText(),/r1 0\/0 → r2 0\/0/);
  assert.equal(await page.locator('.cable.interrupted').count(),1);
  assert.equal(await page.locator('.link-fault-marker').count(),1);
  await page.locator('#link-actions-close').click();
  const observer=await browser.newPage({viewport:{width:800,height:900}});
  await observer.goto(base);await observer.waitForFunction(()=>loaded);
  assert.equal(await observer.locator('.cable.interrupted').count(),1,'Other stations see the runtime fault');
  await observer.close();
  await page.locator('.cable-hit').click();
  await page.locator('[data-link-traffic=b]').click();
  await page.waitForFunction(()=>!busy&&!linkStates.cable.blocked_a_to_b&&linkStates.cable.blocked_b_to_a);
  await page.locator('[data-link-traffic=both]').click();
  await page.waitForFunction(()=>!busy&&linkStates.cable.blocked_a_to_b&&linkStates.cable.blocked_b_to_a);
  await page.locator('[data-link-traffic=none]').click();
  await page.waitForFunction(()=>!busy&&!linkStates.cable.blocked_a_to_b&&!linkStates.cable.blocked_b_to_a);
  assert.equal(await page.locator('.cable.interrupted').count(),0);
  assert.equal(await page.locator('[data-link-traffic=none]').getAttribute('aria-pressed'),'true');
  await page.locator('[data-link-carrier=a]').click();
  await page.waitForFunction(()=>!busy&&linkStates.cable.carrier_a==='down');
  assert.equal(await page.locator('[data-link-traffic][aria-pressed=true]').count(),0,'Unplug cannot show a green all-clear selection');
  assert.equal(await page.locator('[data-link-carrier=a]').getAttribute('class'),'carrier-fault');
  assert.match(await page.locator('#link-actions-state').innerText(),/unplug requested \(IOL\)/);
  await page.locator('[data-link-traffic=none]').click();
  await page.waitForFunction(()=>!busy);
  assert.equal(await page.locator('[data-link-traffic][aria-pressed=true]').count(),0,'Clearing frame loss does not reconnect the cable');
  await page.screenshot({path:'/tmp/netlab-link-actions-iol-unplug.png'});
  await page.locator('[data-link-traffic=a]').click();
  await page.waitForFunction(()=>!busy&&linkStates.cable.blocked_a_to_b);
  await page.locator('[data-link-carrier=a]').click();
  await page.waitForFunction(()=>!busy&&linkStates.cable.carrier_a==='up'&&!linkStates.cable.carrier_pending_a);
  assert.equal(await page.locator('[data-link-traffic=a]').getAttribute('aria-pressed'),'true','Reconnect retains directional loss');
  await page.waitForFunction(()=>getComputedStyle(document.querySelector('[data-link-traffic=a]')).backgroundColor==='rgb(69, 52, 40)');
  const color=await page.locator('[data-link-traffic=a]').evaluate(el=>getComputedStyle(el).backgroundColor);
  assert.equal(color,'rgb(69, 52, 40)','A fault is amber, not the green all-clear color');
  await page.locator('[data-link-traffic=none]').click();
  await page.waitForFunction(()=>!busy&&!linkStates.cable.blocked_a_to_b);
  assert.equal(await page.evaluate(()=>JSON.stringify(topology)),initial,'Faults do not edit topology');
  await page.evaluate(async()=>accept(await api('/api/nodes/r1/stop','POST',{})));
  assert.equal(await page.locator('[data-link-carrier=a]').isVisible(),true,'Stopped endpoint keeps an explanatory disabled action');
  assert.equal(await page.locator('[data-link-carrier=a]').isDisabled(),true);
  assert.equal(await page.locator('[data-link-carrier=b]').isDisabled(),false);
  assert.equal(await page.locator('[data-link-traffic][aria-pressed=true]').count(),0,'Stopped peer is not an all-clear link');
  assert.match(await page.locator('#link-actions-state').innerText(),/r1 0\/0: stopped/);
  const failedDescription=await page.evaluate(()=>{
    statuses.r1={state:'error',error:'Console process exited. Open launcher logs for details.'};
    updateLinkActions();return $('link-actions-state').textContent;
  });
  assert.match(failedDescription,/r1 0\/0: error: Console process exited/);
  await page.screenshot({path:'/tmp/netlab-failed-link-endpoint.png'});
  await page.evaluate(async()=>accept(await api('/api/nodes/r1/start','POST',{})));
  await page.waitForFunction(()=>!linkStates.cable.carrier_pending_a);
  await page.locator('#link-actions-close').click();
  await page.locator('.cable-group').focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('#link-actions-dialog').isVisible(),true);
  await page.locator('#link-actions-close').click();
  await page.evaluate(()=>floatTopology());
  await page.locator('.cable-hit').click({button:'right'});
  assert.equal(await page.locator('#link-actions-dialog').isVisible(),true);
  await page.locator('[data-link-traffic=both]').click();
  await page.waitForFunction(()=>!busy&&linkStates.cable.blocked_a_to_b);
  await page.locator('#link-actions-close').click();
  await page.evaluate(async()=>accept(await api('/api/lab/stop','POST',{})));
  await page.locator('.cable-hit').click({button:'right'});
  assert.equal(await page.locator('[data-link-traffic=a]').isDisabled(),true);
  assert.equal(await page.locator('#link-actions-delete').isDisabled(),false);
  assert.equal(await page.locator('.cable.interrupted').count(),0);
  await page.locator('#link-actions-close').click();
  const mobile=await browser.newPage({viewport:{width:390,height:740},isMobile:true,hasTouch:true});
  await page.evaluate(async()=>accept(await api('/api/lab/start','POST',{})));
  await mobile.goto(base);await mobile.waitForFunction(()=>loaded);await mobile.locator('#zoom-fit').tap();
  await mobile.locator('.cable-hit').tap();
  await mobile.locator('[data-link-traffic=a]').tap();
  await mobile.waitForFunction(()=>!busy&&linkStates.cable.blocked_a_to_b&&!linkStates.cable.carrier_pending_a&&!linkStates.cable.carrier_pending_b);
  await mobile.waitForFunction(()=>getComputedStyle(document.querySelector('[data-link-traffic=a]')).backgroundColor==='rgb(69, 52, 40)');
  const bounds=await mobile.locator('#link-actions-dialog').boundingBox();
  assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390);
  await mobile.screenshot({path:'/tmp/netlab-live-link-actions-phone.png'});
  await mobile.locator('#link-actions-close').tap();
  assert.equal(await mobile.locator('#link-actions-dialog').isVisible(),false,'Phone users can scroll to and close the taller dialog');
  await mobile.close();assert.deepEqual(errors,[]);
  console.log('PASS directional and carrier controls, effective-state highlighting, independent restore/reconnect, shared state, unchanged topology, keyboard/context menu, floating map, stopped guards and phone touch/close');
 }finally{
  if(browser)await browser.close();const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
 }
})().catch(e=>{console.error(e,fixtureLog);process.exitCode=1;});
