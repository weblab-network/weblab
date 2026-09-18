const {consoleAction} = require('./console_ui.cjs');
// Real xterm + WebSocket wrapper tests against disposable PTY echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/console_keys.cjs
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
    image.write_text(image.read_text().replace("b'RX:' + data", "b'HEX:' + data.hex().encode() + bytes([13, 10])"))
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
    const context = await browser.newContext({viewport:{width:800,height:1000},hasTouch:true,isMobile:true});
    const page = await context.newPage();
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

    await page.waitForFunction(()=>loaded);
    await page.evaluate(()=>openConsole('r1'));await connected('r1');
    await page.locator('#console-interrupt').tap();await hasText('r1','HEX:03');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').terminal.textarea===document.activeElement),true);
    const menu=page.locator('#console-toolbar .console-keys');
    for(const code of ['01','17','05','1e','09','1b5b41']) {
      await menu.locator('summary').tap();
      await menu.locator('[data-console-key="'+code+'"]').tap();
      await hasText('r1','HEX:'+code);
      assert.equal(await menu.evaluate(e=>e.open),false);
    }
    // Native keyboard disclosure navigation and Escape do not reach the terminal.
    const actions=page.locator('#console-toolbar .console-actions');
    await actions.locator('summary').focus();await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('#console-toolbar .console-actions').open);
    await page.keyboard.press('Escape');assert.equal(await actions.evaluate(e=>e.open),false);
    await consoleAction(page.locator('#detach-console-tab'),true);
    await page.evaluate(()=>openConsole('r2'));await connected('r2');
    await page.locator('#console-window-r2 .console-keys summary').tap();
    await page.locator('#console-window-r2 [data-console-key="15"]').tap();await hasText('r2','HEX:15');
    assert.ok(!(await text('r1')).includes('HEX:15'),'Keys target their own floating session');
    await page.locator('#lock-console-r2').check();await page.waitForFunction(()=>consoleSessions.get('r2').lockMine);
    const observer=await browser.newPage({viewport:{width:800,height:1000}});
    await observer.goto(base);await observer.waitForFunction(()=>loaded);
    await observer.evaluate(()=>openConsole('r2'));
    await observer.waitForFunction(()=>consoleSessions.get('r2').locked);
    assert.equal(await observer.locator('#console-interrupt').isDisabled(),true);
    assert.equal(await observer.locator('#console-toolbar [data-console-key="17"]').isDisabled(),true);
    // The common send path still refuses input even if UI state is bypassed.
    const old=await text('r2');
    await observer.evaluate(()=>{const b=$('console-interrupt');b.disabled=false;b.click();});
    await page.waitForTimeout(150);assert.equal(await text('r2'),old);
    await observer.close();
    await page.setViewportSize({width:390,height:420});
    await page.evaluate(()=>activateConsole('r2'));
    const windowActions=page.locator('#console-window-r2 .console-actions');
    await windowActions.locator('summary').tap();
    const box=await windowActions.locator('.console-menu-list').boundingBox();
    assert.ok(box.x>=0&&box.y>=0&&box.x+box.width<=390&&box.y+box.height<=420,'Menu fits with a small keyboard viewport');
    await page.screenshot({path:'/tmp/netlab-console-actions-mobile.png'});
    await consoleAction(page.locator('#console-window-r2 [data-dock-consoles]'),true);
    await page.evaluate(()=>activateConsole('r2'));
    await page.locator('#console-toolbar .console-keys summary').tap();
    await page.screenshot({path:'/tmp/netlab-console-keys-mobile.png'});
    await page.locator('#console-toolbar [data-console-key="17"]').tap();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.request.post(base+'/api/nodes/r2/stop',{data:{}});await refreshState();
    assert.equal(await page.locator('#console-interrupt').isDisabled(),true);
    assert.equal(await page.locator('#console-toolbar [data-console-key="17"]').isDisabled(),true);
    assert.deepEqual(errors,[]);
    console.log('PASS touch Ctrl+C and special keys, exact PTY bytes, window targeting, locks, disabled/disconnected keys, disclosure keyboard navigation, viewport bounds and docking');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
