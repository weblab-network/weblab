const {consoleAction} = require('./console_ui.cjs');
// Real xterm + WebSocket wrapper tests against disposable PTY echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/console_tabs.cjs
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
test = LabTests()
test.setUp()
try:
    image = test.root / 'fake.bin'
    image.write_text(image.read_text().replace("b'RX:' + data", "b'RX:' + data.replace(bytes([13]), bytes([13, 10]))"))
    test.topology['nodes'][0].update(x=180, y=160)
    test.topology['nodes'][1].update(x=420, y=180)
    test.lab.save(test.topology)
    test.lab.start_all()
    print('http://127.0.0.1:' + str(test.port), flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
`], {cwd: path.resolve(__dirname, '..'), stdio: ['pipe', 'pipe', 'pipe']});
let fixtureLog = '';
fixture.stderr.on('data', data => { fixtureLog += data; });

(async () => {
  let browser;
  try {
    const base = await new Promise((resolve, reject) => {
      let output = '';
      fixture.stdout.on('data', data => { output += data; if (output.includes('\n')) resolve(output.trim()); });
      fixture.once('exit', code => reject(new Error(`Fixture exited ${code}: ${fixtureLog}`)));
    });
    browser = await chromium.launch({headless:true, args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1440, height:1000}});
    const errors = [], connections = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('websocket', ws => connections.push(ws.url()));
    await page.goto(base);
    const clickNode = id => page.locator(`.map-node[data-id="${id}"]`).click();
    const connected = id => page.waitForFunction(id => consoleSessions.get(id)?.status === 'Connected', id);
    const text = id => page.evaluate(id => {
      const buffer = consoleSessions.get(id).terminal.buffer.active;
      return Array.from({length:buffer.length}, (_, i) => buffer.getLine(i).translateToString(true)).join('\n');
    }, id);
    const hasText = (id, marker) => page.waitForFunction(({id, marker}) => {
      const buffer = consoleSessions.get(id).terminal.buffer.active;
      return Array.from({length:buffer.length}, (_, i) => buffer.getLine(i).translateToString(true)).join('\n').includes(marker);
    }, {id, marker});
    const type = async value => { await page.keyboard.insertText(value); await page.keyboard.press('Enter'); };
    const refreshState = async () => page.evaluate(async () => accept(await api('/api/state')));

    await clickNode('r1');await connected('r1');
    await type('FIRST_HISTORY');await hasText('r1','FIRST_HISTORY');
    await clickNode('r2');await connected('r2');
    await type('SECOND_HISTORY');await hasText('r2','SECOND_HISTORY');
    await page.evaluate(()=>window.originalSessions=[...consoleSessions.values()].map(s=>({id:s.id,terminal:s.terminal,socket:s.socket})));
    await consoleAction(page.locator('#detach-console-tab'));
    const one=page.locator('#console-window-r1'),two=page.locator('#console-window-r2');
    assert.equal(await two.isVisible(),true);
    assert.equal(await page.evaluate(()=>consoleNode),'r1');
    await page.evaluate(()=>floatConsole('r1'));
    assert.equal(await one.isVisible(),true);
    assert.equal(await two.isVisible(),true);
    assert.equal(await page.evaluate(()=>consoleNode),null);
    assert.equal(await page.locator('#terminal').isVisible(),false);
    await page.locator('#lock-console-r1').check();await page.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    assert.equal(await page.locator('#lock-console-r2').isChecked(),false);
    await page.evaluate(()=>activateConsole('r1'));
    await type('INPUT_ONE_ONLY');await hasText('r1','INPUT_ONE_ONLY');
    assert.ok(!(await text('r2')).includes('INPUT_ONE_ONLY'));
    await page.evaluate(()=>activateConsole('r2'));
    await type('INPUT_TWO_ONLY');await hasText('r2','INPUT_TWO_ONLY');
    assert.ok(!(await text('r1')).includes('INPUT_TWO_ONLY'));
    const observerContext=await browser.newContext({viewport:{width:390,height:740},hasTouch:true,isMobile:true});
    const observer=await observerContext.newPage();observer.on('pageerror',e=>errors.push(e.message));
    await observer.goto(base);await observer.waitForFunction(()=>loaded);
    await observer.evaluate(()=>{openConsole('r1');floatConsole('r1');});
    await observer.waitForFunction(()=>consoleSessions.get('r1')?.lockReady&&consoleSessions.get('r1').locked);
    assert.equal(await observer.locator('#lock-console-r1').isDisabled(),true);
    const cdp=await observerContext.newCDPSession(observer);
    const touchBox=await observer.locator('#console-move-r1').boundingBox();
    const tx=touchBox.x+touchBox.width/2,ty=touchBox.y+touchBox.height/2;
    const oldTop=(await observer.locator('#console-window-r1').boundingBox()).y;
    // Give Chromium a realistic gesture duration; zero-duration CDP swipes can suppress the following tap.
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:tx,y:ty}]});
    await observer.waitForTimeout(100);
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:tx,y:ty-40}]});
    await observer.waitForTimeout(100);
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    assert.ok((await observer.locator('#console-window-r1').boundingBox()).y<oldTop-25);
    await observer.locator('#takeover-console-r1').tap();
    await observer.locator('#confirm-takeover').tap();
    await page.waitForFunction(()=>!consoleSessions.get('r1').lockMine);
    await observer.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    await page.evaluate(()=>activateConsole('r1'));
    await page.locator('#takeover-console-r1').click();await page.locator('#confirm-takeover').click();
    await page.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    await observerContext.close();
    const twoBox=await two.boundingBox();
    await page.evaluate(()=>activateConsole('r1'));
    await page.locator('#console-move-r1').focus();await page.keyboard.press('ArrowLeft');
    assert.deepEqual(await two.boundingBox(),twoBox,'Moving one window does not move the other');
    const firstSize=await one.boundingBox();
    await page.locator('#console-resize-r1').focus();await page.keyboard.press('Shift+ArrowRight');
    assert.ok((await one.boundingBox()).width>firstSize.width+30);
    await consoleAction(page.locator('#expand-console-r1'));
    assert.ok((await one.boundingBox()).width>=1420);
    assert.deepEqual(await two.boundingBox(),twoBox,'Maximize is per window');
    await consoleAction(page.locator('#expand-console-r1'));
    await page.locator('#console-font-increase-r1').click();
    assert.equal(await page.locator('#console-font-reset-r2').innerText(),'13px');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r2').terminal.options.fontSize),13);
    await consoleAction(page.locator('#close-console-r1'));assert.equal(await one.isVisible(),false);
    await page.getByRole('tab',{name:'r1 🔒',exact:true}).click();assert.equal(await one.isVisible(),true);
    await consoleAction(page.locator('#detach-console-r1'));
    assert.equal(await page.locator('#console-panel').evaluate(p=>p.style.zIndex),'','Docked panel does not retain floating stack level');
    assert.equal(await one.count(),0);
    assert.equal(await two.isVisible(),true);
    assert.equal(await page.evaluate(()=>consoleNode),'r1');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').lockMine),true);
    assert.equal(await page.evaluate(()=>originalSessions.every(s=>consoleSessions.get(s.id).terminal===s.terminal&&consoleSessions.get(s.id).socket===s.socket)),true);
    assert.ok((await text('r1')).includes('FIRST_HISTORY'));
    await consoleAction(page.locator('#detach-console'));
    assert.equal(await page.locator('#console-panel').evaluate(e=>e.classList.contains('floating')),true);
    assert.equal(await two.isVisible(),true);
    await page.screenshot({path:'/tmp/netlab-multiple-consoles.png'});
    await page.setViewportSize({width:390,height:740});
    await page.waitForFunction(()=>[...document.querySelectorAll('#console-windows>.console-panel')].every(p=>{const r=p.getBoundingClientRect();return r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;}));
    await page.screenshot({path:'/tmp/netlab-multiple-consoles-mobile.png'});
    await page.setViewportSize({width:1440,height:1000});
    // Floating control targets r2 even though r1 is the selected docked console.
    const beforeReconnect=connections.length;
    await page.evaluate(()=>activateConsole('r2'));await consoleAction(page.locator('#reconnect-console-r2'));await connected('r2');
    assert.equal(connections.length,beforeReconnect+1);
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').socket===originalSessions[0].socket),true);
    await page.request.post(base+'/api/nodes/r2/stop',{data:{}});await refreshState();
    assert.equal(await page.locator('#reconnect-console-r2').isDisabled(),true);
    assert.match(await page.locator('#console-status-r2').getAttribute('aria-label'),/Stopped/);
    await page.request.post(base+'/api/nodes/r2/start',{data:{}});await refreshState();await connected('r2');
    await page.evaluate(()=>closeConsole('r2'));
    assert.equal(await two.count(),0);
    assert.equal(await page.evaluate(()=>consoleSessions.has('r1')),true);
    await page.request.post(base+'/api/lab/stop',{data:{}});await refreshState();
    await page.request.put(base+'/api/topology',{data:{name:'Empty',nodes:[],links:[]}});await refreshState();
    assert.equal(await page.locator('#console-windows>.console-panel:not(#console-panel)').count(),0);
    assert.equal(await page.locator('#console-panel').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log('PASS multiple floating consoles, input isolation, independent geometry, focus/stacking, dock/group coexistence, font/history/socket/lock preservation, mobile bounds and lifecycle cleanup');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
