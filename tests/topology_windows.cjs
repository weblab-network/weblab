const {consoleAction} = require('./console_ui.cjs');
const {pinnedToolbar} = require('./toolbar_ui.cjs');
// Real xterm + WebSocket wrapper tests against disposable PTY echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/topology_windows.cjs
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
    for i in range(3, 7):
        node = test.node('r' + str(i))
        node.update(x=180 + ((i-1) % 3)*240, y=160 + ((i-1)//3)*220)
        test.topology['nodes'].append(node)
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

    await page.waitForFunction(()=>loaded);
    await page.evaluate(()=>{window.originalMap=$('canvas');window.originalTopology=JSON.stringify(topology);});
    await page.locator('#float-topology').click();
    const map=page.locator('#topology-panel');
    assert.equal(await map.evaluate(e=>e.parentElement.id),'console-windows');
    assert.equal(await page.locator('.canvas-toolbar').evaluate(e=>e.parentElement.id),'topology-toolbar-scroll');
    for(const id of ['toggle-palette','toggle-inspector']) assert.equal(await page.locator('#'+id).isVisible(),false);
    await page.locator('#zoom-fit').click();
    await clickNode('r1');await connected('r1');
    await type('MAP_WINDOW_HISTORY');await hasText('r1','MAP_WINDOW_HISTORY');
    // The checkbox reflects server acknowledgment, not the immediate click.
    await page.locator('#lock-console').click();await page.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    await page.evaluate(()=>{window.originalSocket=consoleSessions.get('r1').socket;window.originalTerminal=consoleSessions.get('r1').terminal;});
    await page.evaluate(()=>floatConsole('r1',false)); // Geometry test: do not change the new-console preference.
    await page.evaluate(()=>{floatTopology();});
    await page.locator('#topology-move').focus();
    const before=await map.boundingBox();await page.keyboard.press('ArrowRight');
    assert.ok((await map.boundingBox()).x>before.x);
    await page.locator('#topology-resize').focus();await page.keyboard.press('ArrowDown');
    assert.ok((await map.boundingBox()).height>before.height);
    await page.locator('#expand-topology').click();assert.equal(await map.evaluate(e=>e.classList.contains('maximized')),true);
    await page.locator('#expand-topology').click();
    const zoom=await page.evaluate(()=>mapZoom);
    await page.locator('#dock-topology').click();
    assert.equal(await map.evaluate(e=>e.parentElement.className),'workarea');
    assert.equal(await page.locator('.canvas-toolbar').evaluate(e=>e.parentElement.id),'topology-panel');
    assert.equal(await page.evaluate(()=>mapZoom),zoom);
    assert.equal(await page.evaluate(()=>$('canvas')===originalMap),true);
    // Arrange also floats the live map and a visible docked tabbed console.
    await page.evaluate(()=>openConsole('r2'));await connected('r2');
    await consoleAction(page.locator('#console-toolbar [data-arrange-windows]'));
    const boxes=()=>page.locator('#console-windows>.floating:visible').evaluateAll(els=>els.map(e=>{const r=e.getBoundingClientRect();return {id:e.id,x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};}));
    function tiled(rects) {
      for(let i=0;i<rects.length;i++)for(let j=i+1;j<rects.length;j++) {
        const a=rects[i],b=rects[j];assert.ok(a.right<=b.x+1||b.right<=a.x+1||a.bottom<=b.y+1||b.bottom<=a.y+1,`${a.id} overlaps ${b.id}`);
      }
    }
    tiled(await boxes());assert.equal((await boxes()).length,3);
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').socket===originalSocket&&consoleSessions.get('r1').terminal===originalTerminal&&consoleSessions.get('r1').lockMine),true);
    // Six independent consoles plus topology, matching a busy lab workspace.
    await page.evaluate(()=>floatConsole('r2'));
    for(let i=3;i<=6;i++) {await page.evaluate(id=>{openConsole(id);floatConsole(id);},'r'+i);await connected('r'+i);}
    await page.setViewportSize({width:1720,height:1400});
    await consoleAction(page.locator('#console-window-r6 [data-arrange-windows]'));
    tiled(await boxes());assert.equal((await boxes()).length,7);
    assert.equal(await page.evaluate(()=>JSON.stringify(topology)===originalTopology),true);
    await page.screenshot({path:'/tmp/netlab-topology-tiled.png'});
    // Map hit testing still opens the existing session, with no new socket.
    const count=connections.length;
    await clickNode('r1');assert.equal(connections.length,count);
    await type('AFTER_ARRANGE');await hasText('r1','AFTER_ARRANGE');
    await consoleAction(page.locator('#close-console-r1'));
    await consoleAction(page.locator('#topology-window-toolbar .console-actions [data-arrange-windows]'));
    assert.equal((await boxes()).length,6);tiled(await boxes());
    assert.equal(await page.locator('#console-window-r1').isVisible(),false,'Hidden windows stay hidden');
    // Portrait: insufficient room falls back to bounded, selectable windows.
    await page.setViewportSize({width:390,height:740});
    await page.evaluate(()=>arrangeWindows());
    assert.match(await page.locator('#toast').innerText(),/cascaded/);
    for(const r of await boxes())assert.ok(r.x>=0&&r.y>=0&&r.right<=390&&r.bottom<=740);
    await page.evaluate(()=>floatTopology());
    await page.locator('#dock-topology').click();
    for(const id of ['toggle-palette','toggle-inspector']) assert.equal(await page.locator('#'+id).isVisible(),true);
    // Cascaded consoles cover the docked toolbar; exercise drawer state directly.
    await page.locator('#toggle-inspector').evaluate(e=>e.click());
    assert.equal(await page.locator('#console-windows').evaluate(e=>e.inert),true);
    await page.locator('#close-inspector').click();
    assert.equal(await page.locator('#console-windows').evaluate(e=>e.inert),false);
    assert.equal(await page.evaluate(()=>$('canvas')===originalMap),true);
    await consoleAction(page.locator('#console-window-r6 [data-show-topology]'));
    assert.equal(await page.locator('#topology-window-toolbar').evaluate(e=>getComputedStyle(e).flexWrap),'nowrap');
    assert.equal(await page.locator('.canvas-toolbar').evaluate(e=>getComputedStyle(e).display),'contents');
    const row=await page.locator('#topology-window-toolbar').boundingBox();
    for(const id of ['topology-move','select-tool','zoom-fit','dock-topology']) {
      const b=await page.locator('#'+id).boundingBox();assert.ok(b.y>=row.y&&b.y+b.height<=row.y+row.height);
    }
    await page.screenshot({path:'/tmp/netlab-topology-mobile.png'});
    await pinnedToolbar(page, '#topology-window-toolbar', '#topology-move', '#hide-topology');
    await page.locator('#hide-topology').click();
    assert.equal(await map.isVisible(),false);
    // The placeholder is covered by cascaded consoles on a phone; use Map.
    await page.evaluate(()=>activateConsole('r6'));
    await consoleAction(page.locator('#console-window-r6 [data-show-topology]'));
    assert.equal(await map.isVisible(),true);
    assert.equal(await page.evaluate(()=>$('canvas')===originalMap),true);
    await page.evaluate(()=>activateConsole('r6'));
    await pinnedToolbar(page, '#console-toolbar-r6', '#console-move-r6', '#close-console-r6');
    await page.locator('#close-console-r6').click();
    assert.equal(await page.locator('#console-window-r6').isVisible(),false);
    await page.evaluate(()=>activateConsole('r6'));
    await type('AFTER_PINNED_HIDE');await hasText('r6','AFTER_PINNED_HIDE');
    // Dropdown scrolls vertically without moving the toolbar underneath it.
    const menu = page.locator('#console-window-r6 .console-actions.console-menu');
    await menu.locator('summary').click();
    const stripOffset = await page.locator('#console-toolbar-r6 .toolbar-scroll').evaluate(e=>e.scrollLeft);
    await menu.locator('.console-menu-list').hover();await page.mouse.wheel(0,100);
    assert.equal(await page.locator('#console-toolbar-r6 .toolbar-scroll').evaluate(e=>e.scrollLeft),stripOffset);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.deepEqual(errors,[]);
    console.log('PASS floating topology controls/dock/DOM preservation, seven-window tiling, map hit testing, live sessions/locks/history, hidden windows, mobile fallback and drawers');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
