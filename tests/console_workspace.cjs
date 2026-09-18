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

    await clickNode('r1'); await connected('r1');
    await type('WORKSPACE_HISTORY'); await hasText('r1','WORKSPACE_HISTORY');
    await clickNode('r2'); await connected('r2');
    await page.evaluate(()=>{window.originalSessions=[...consoleSessions.values()].map(s=>({id:s.id,terminal:s.terminal,socket:s.socket}));});
    const cols = await page.evaluate(()=>consoleSessions.get('r2').terminal.cols);
    for(let i=0;i<4;i++) await page.locator('#console-font-increase').click();
    assert.equal(await page.locator('#console-font-reset').innerText(),'16px');
    await page.waitForFunction(()=>[...consoleSessions.values()].every(s=>s.terminal.options.fontSize===16));
    await page.waitForFunction(cols=>consoleSessions.get('r2').terminal.cols<cols,cols);
    await page.locator('#console-font-decrease').click();
    assert.equal(await page.locator('#console-font-reset').innerText(),'15px');
    await page.locator('#console-font-reset').click();
    assert.equal(await page.locator('#console-font-reset').innerText(),'12px');
    await page.evaluate(()=>setConsoleFontSize(100));
    assert.equal(await page.locator('#console-font-increase').isDisabled(),true);
    await page.evaluate(()=>setConsoleFontSize(1));
    assert.equal(await page.locator('#console-font-decrease').isDisabled(),true);
    await page.locator('#console-font-reset').click();
    await page.locator('#console-font-increase').click();
    const panel=page.locator('#console-panel');
    async function drag(handle,dx,dy){const r=await handle.boundingBox();await page.mouse.move(r.x+r.width/2,r.y+r.height/2);await page.mouse.down();await page.mouse.move(r.x+r.width/2+dx,r.y+r.height/2+dy,{steps:8});await page.mouse.up();}
    let box=await panel.boundingBox();
    await drag(page.locator('#console-divider'),0,-65);
    assert.ok((await panel.boundingBox()).height>box.height+50,'Dock height follows drag');
    await page.locator('#console-divider').focus();await page.keyboard.press('ArrowDown');
    assert.ok(Number(await page.locator('#console-divider').getAttribute('aria-valuenow'))>0);
    await page.locator('#lock-console').click();await page.waitForFunction(()=>consoleSessions.get('r2').lockMine);
    await consoleAction(page.locator('#detach-console'));
    assert.equal(await page.locator('#detach-console').textContent(),'Dock');
    assert.equal(await page.locator('#console-divider').isVisible(),false);
    box=await panel.boundingBox();
    await drag(page.locator('#console-move'),-50,-40);
    let moved=await panel.boundingBox();
    assert.ok(moved.x<box.x-30 && moved.y<box.y-20,'Floating window moves');
    await drag(page.locator('#console-resize'),70,40);
    let resized=await panel.boundingBox();
    assert.ok(resized.width>moved.width+50,'Floating window resizes');
    await page.locator('#console-move').focus();await page.keyboard.press('ArrowRight');
    const beforeMax=await panel.boundingBox();
    await consoleAction(page.locator('#expand-console'));
    let maximized=await panel.boundingBox();
    assert.ok(maximized.width>=1420 && maximized.height>=980,'Maximize fills viewport');
    assert.equal(await page.locator('#console-resize').isVisible(),false);
    await consoleAction(page.locator('#expand-console'));
    assert.ok(Math.abs((await panel.boundingBox()).width-beforeMax.width)<2,'Restore retains floating dimensions');
    await page.screenshot({path:'/tmp/netlab-console-floating.png'});
    assert.equal(await page.evaluate(()=>consoleSessions.get('r2').lockMine),true);
    assert.equal(await page.evaluate(()=>originalSessions.every(s=>consoleSessions.get(s.id).terminal===s.terminal&&consoleSessions.get(s.id).socket===s.socket)),true,'Layout and font changes preserve terminals and sockets');
    assert.ok((await text('r1')).includes('WORKSPACE_HISTORY'));
    await consoleAction(page.locator('#close-console'));assert.equal(await panel.isVisible(),false);
    await page.evaluate(()=>openConsole('r2'));await page.waitForFunction(()=>!$('console-panel').hidden);
    assert.equal(await page.locator('#detach-console').textContent(),'Dock');
    await page.setViewportSize({width:390,height:740});
    await page.waitForFunction(()=>{const r=$('console-panel').getBoundingClientRect();return r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;});
    assert.equal(await page.locator('#console-toolbar .console-actions summary').isVisible(),true);
    await page.screenshot({path:'/tmp/netlab-console-floating-mobile.png'});
    await page.setViewportSize({width:740,height:390});
    await page.waitForFunction(()=>$('console-panel').getBoundingClientRect().bottom<=innerHeight);
    await consoleAction(page.locator('#expand-console'));
    await page.locator('#console-font-increase').click();
    await consoleAction(page.locator('#detach-console'));
    assert.equal(await page.locator('#detach-console').textContent(),'Float');
    await page.setViewportSize({width:1440,height:1000});
    await page.reload();await page.waitForFunction(()=>loaded);
    await clickNode('r1');await connected('r1');
    assert.equal(await page.locator('#console-font-reset').innerText(),'14px','Font preference survives reload');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').terminal.options.fontSize),14);
    assert.equal(await page.locator('#detach-console').textContent(),'Float','New page starts docked');
    const touchContext=await browser.newContext({viewport:{width:390,height:740},hasTouch:true,isMobile:true});
    const touchPage=await touchContext.newPage();touchPage.on('pageerror',e=>errors.push(e.message));
    await touchPage.goto(base);await touchPage.waitForFunction(()=>loaded);
    await touchPage.evaluate(()=>openConsole('r1'));await touchPage.waitForFunction(()=>consoleSessions.get('r1')?.lockReady);
    await consoleAction(touchPage.locator('#detach-console'), true);
    const cdp=await touchContext.newCDPSession(touchPage);
    async function touchDrag(selector,dx,dy){const r=await touchPage.locator(selector).boundingBox();const x=r.x+r.width/2,y=r.y+r.height/2;
      await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
      await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+dx,y:y+dy}]});
      await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    }
    const touchBefore=await touchPage.locator('#console-panel').boundingBox();
    await touchDrag('#console-move',0,-60);
    assert.ok((await touchPage.locator('#console-panel').boundingBox()).y<touchBefore.y-40,'Touch drag moves panel');
    const touchHeight=(await touchPage.locator('#console-panel').boundingBox()).height;
    await touchDrag('#console-resize',-20,-50);
    assert.ok((await touchPage.locator('#console-panel').boundingBox()).height<touchHeight-30,'Touch drag resizes panel');
    await touchPage.locator('#console-font-increase').tap();
    assert.equal(await touchPage.locator('#console-font-reset').innerText(),'13px');
    await touchContext.close();
    assert.deepEqual(errors,[]);
    console.log('PASS console font bounds/reset/persistence, dock resizing, floating move/resize/maximize/restore, history/socket/lock preservation, hide/reopen and mobile viewport bounds');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
