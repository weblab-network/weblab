const {consoleAction} = require('./console_ui.cjs');
// Real xterm + WebSocket wrapper tests against disposable PTY echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/console_compact.cjs
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

    const touchClient=await context.newCDPSession(page);
    async function scrollToolbar(selector) {
      const bar=page.locator(selector);
      await page.waitForTimeout(250);
      await bar.evaluate(e=>e.scrollLeft=0);
      assert.equal(await bar.evaluate(e=>getComputedStyle(e).flexWrap),'nowrap');
      assert.ok(await bar.evaluate(e=>e.scrollWidth>e.clientWidth),'Narrow toolbar overflows');
      const before=await bar.boundingBox();
      // A horizontal swipe over controls must scroll without activating them.
      const x=before.x+before.width-20,y=before.y+before.height/2;
      await touchClient.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
      await page.waitForTimeout(100);
      await touchClient.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-120,y}]});
      await page.waitForTimeout(100);
      await touchClient.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
      assert.ok(await bar.evaluate(e=>e.scrollLeft)>20,'Touch scrolls toolbar '+selector);
      assert.deepEqual(await bar.boundingBox(),before,'Scrolling does not move or resize window');
      await bar.evaluate(e=>e.scrollLeft=0);
    }
    await page.waitForFunction(()=>loaded);
    await page.evaluate(()=>openConsole('r1'));await connected('r1');
    // Android's keyboard can shrink the visual viewport without changing vh.
    await page.evaluate(()=>{
      window.keyboardSocket=consoleSessions.get('r1').socket;
      setDockHeight(350);
      Object.defineProperty(visualViewport,'height',{configurable:true,get:()=>560});
      visualViewport.dispatchEvent(new Event('resize'));
    });
    await page.waitForFunction(()=>$('console-panel').getBoundingClientRect().bottom<=561);
    assert.equal(await page.evaluate(()=>innerHeight),1000,'Layout viewport remains full height');
    assert.ok((await page.locator('#terminal').boundingBox()).height>=100,'Terminal remains usable above keyboard');
    await page.evaluate(()=>{
      delete visualViewport.height;
      visualViewport.dispatchEvent(new Event('resize'));
    });
    await page.waitForFunction(()=>Math.abs($('console-panel').getBoundingClientRect().height-350)<1);
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').socket===keyboardSocket),true);
    await page.setViewportSize({width:390,height:740});
    await scrollToolbar('.canvas-toolbar');
    await scrollToolbar('#console-toolbar > .toolbar-scroll');
    await page.setViewportSize({width:800,height:1000});
    await consoleAction(page.locator('#detach-console-tab'),true);
    const pane=page.locator('#console-window-r1');
    await page.waitForFunction(()=>$('console-window-r1').classList.contains('console-compact'));
    assert.equal(await pane.locator('.console-status-text').isVisible(),false);
    assert.equal(await pane.locator('.console-lock-text').isVisible(),false);
    assert.match(await pane.locator('.console-connection').getAttribute('aria-label'),/Connected.*shared/);
    assert.equal(await pane.locator('.console-status-icon').isVisible(),true);
    for(const code of ['1b5b44','1b5b43','1b5b41','1b5b42']) {
      await pane.locator('.console-arrow-keys [data-console-key="'+code+'"]').tap();
      await hasText('r1','HEX:'+code);
    }
    await pane.locator('#console-tab-r1').tap();
    await hasText('r1','HEX:09');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').terminal.textarea===document.activeElement),true,'Tab keeps terminal focus');
    await page.locator('#lock-console-r1').check();await page.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    assert.match(await pane.locator('.console-connection').getAttribute('aria-label'),/Input locked/);
    await page.evaluate(()=>activateConsole('r1'));
    await page.evaluate(()=>{window.menuBlurs=0;consoleSessions.get('r1').terminal.textarea.addEventListener('blur',()=>menuBlurs++);});
    await pane.locator('.console-actions summary').tap();
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').terminal.textarea===document.activeElement),true,'Opening Actions keeps terminal focus');
    await pane.locator('[data-arrange-windows]').tap();
    assert.equal(await page.evaluate(()=>menuBlurs),0,'Floating Arrange never blurs the terminal');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').terminal.textarea===document.activeElement),true);
    await page.setViewportSize({width:800,height:520});
    await pane.locator('.console-actions summary').tap();await pane.locator('[data-arrange-windows]').tap();
    const bounds=await pane.boundingBox();assert.ok(bounds.y+bounds.height<=520);
    await page.screenshot({path:'/tmp/netlab-console-compact-tablet.png'});
    const mapbar=page.locator('#topology-toolbar-scroll');
    assert.equal(await mapbar.evaluate(e=>e.scrollWidth>e.clientWidth),true);
    await mapbar.evaluate(e=>e.scrollLeft=0);
    const mb=await mapbar.boundingBox(),mx=mb.x+mb.width-20,my=mb.y+mb.height/2;
    const oldMap=await page.evaluate(()=>({...topologyWindow.rect}));
    const cdp=await context.newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:mx,y:my}]});
    await page.waitForTimeout(100);
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:mx-120,y:my}]});
    await page.waitForTimeout(100);
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    assert.ok(await mapbar.evaluate(e=>e.scrollLeft)>20,'Narrow topology controls scroll with touch');
    assert.deepEqual(await page.evaluate(()=>topologyWindow.rect),oldMap,'Scrolling controls does not move the map window');

    // Panes on a wide desktop still compact individually; widening restores labels.
    await page.setViewportSize({width:1500,height:1000});
    await page.evaluate(()=>{const v=consoleSessions.get('r1').floating;v.rect={x:100,y:100,width:1100,height:450};applyFloatingConsoleLayout(v);});
    await page.waitForFunction(()=>!$('console-window-r1').classList.contains('console-compact'));
    assert.equal(await pane.locator('.console-status-text').isVisible(),true);
    assert.equal(await pane.locator('.console-lock-text').isVisible(),true);
    await page.evaluate(()=>{const v=consoleSessions.get('r1').floating;v.rect.width=600;applyFloatingConsoleLayout(v);});
    await page.waitForFunction(()=>$('console-window-r1').classList.contains('console-compact'));
    assert.equal(await pane.locator('.console-lock-text').isVisible(),false);
    const observer=await browser.newPage({viewport:{width:800,height:600}});
    await observer.goto(base);await observer.waitForFunction(()=>loaded);
    await observer.evaluate(()=>openConsole('r1'));await observer.waitForFunction(()=>consoleSessions.get('r1').locked);
    assert.equal(await observer.locator('#console-toolbar .console-observer-icon').isVisible(),true);
    assert.equal(await observer.locator('#console-toolbar .console-arrow-keys button').first().isDisabled(),true);
    assert.equal(await observer.locator('#console-tab').isDisabled(),true);
    await observer.close();
    await page.evaluate(()=>openConsole('r2'));await connected('r2');
    await page.locator('#console-window-r2 .console-arrow-keys [data-console-key="1b5b44"]').tap();await hasText('r2','HEX:1b5b44');
    await page.setViewportSize({width:390,height:420});
    await page.evaluate(()=>activateConsole('r2'));
    assert.equal(await page.locator('#console-window-r2 .console-arrow-keys').isVisible(),true);
    await scrollToolbar('#console-toolbar-r2 > .toolbar-scroll');
    await page.screenshot({path:'/tmp/netlab-console-compact-phone.png'});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.deepEqual(errors,[]);
    console.log('PASS per-pane compact status/lock icons, direct touch arrows, exact bytes, focus-preserving Actions/Arrange, viewport bounds, wide-label restoration and read-only keys');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
