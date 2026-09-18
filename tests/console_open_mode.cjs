const {consoleAction} = require('./console_ui.cjs');
// Real xterm + WebSocket wrapper tests against disposable PTY echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/console_open_mode.cjs
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
    assert.equal(await page.evaluate(()=>consoleOpenMode),null);
    // Floating the whole group must keep unopened node consoles as tabs.
    await consoleAction(page.locator('#detach-console'));
    assert.equal(await page.evaluate(()=>consoleOpenMode),'tabbed');
    await page.evaluate(()=>openConsole('r2'));await connected('r2');
    assert.equal(await page.locator('#console-window-r2').count(),0);
    assert.equal(await page.evaluate(()=>consoleFloating),true);
    await page.evaluate(()=>closeConsole('r2'));
    await page.evaluate(()=>arrangeWindows());
    // Reproduce the reported state: floating topology plus floating tabbed panel.
    await page.evaluate(()=>{window.firstSocket=consoleSessions.get('r1').socket;});
    await clickNode('r2');await connected('r2');
    assert.equal(await page.locator('#console-window-r2').count(),0);
    assert.equal(await page.evaluate(()=>consoleFloating&&!!topologyWindow),true);
    assert.equal(await page.evaluate(()=>consoleSessions.get('r2').element.parentElement.id),'terminal');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').socket===firstSocket),true);
    await consoleAction(page.locator('#detach-console'));
    assert.equal(await page.evaluate(()=>consoleOpenMode),'docked');
    await page.evaluate(()=>closeConsole('r2'));
    await page.evaluate(()=>openConsole('r2'));await connected('r2');
    assert.equal(await page.locator('#console-window-r2').count(),0);
    assert.equal(await page.evaluate(()=>consoleFloating),false);
    await page.evaluate(()=>dockTopology());
    await consoleAction(page.locator('#console-toolbar [data-dock-consoles]'));
    await page.evaluate(()=>closeConsole('r2'));
    await consoleAction(page.locator('#detach-console-tab'));
    // A floating map must not detach the shared panel in individual-window mode.
    await consoleAction(page.locator('#console-window-r1 [data-show-topology]'));
    await page.locator('#zoom-fit').click();
    await clickNode('r2');await connected('r2');
    assert.equal(await page.locator('#console-window-r2').isVisible(),true);
    assert.equal(await page.evaluate(()=>consoleFloating),false,'Opening an individual window must leave the shared panel docked');
    assert.equal(await page.locator('#console-panel').evaluate(e=>e.classList.contains('floating')),false);
    await page.evaluate(()=>{window.reopenedSocket=consoleSessions.get('r2').socket;floatTopology();});
    await clickNode('r2');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r2').socket===reopenedSocket),true);
    assert.equal(await page.evaluate(()=>consoleFloating),false);
    await page.evaluate(()=>dockTopology());
    // Lock state follows the server acknowledgment, not the immediate click.
    await page.locator('#lock-console-r2').click();await page.waitForFunction(()=>consoleSessions.get('r2').lockMine);
    await page.evaluate(()=>activateConsole('r2'));await type('PRESERVE_ALL');await hasText('r2','PRESERVE_ALL');
    await page.evaluate(()=>window.originalSessions=[...consoleSessions.values()].map(s=>({id:s.id,terminal:s.terminal,socket:s.socket})));
    await consoleAction(page.locator('#console-window-r2 [data-dock-consoles]'));
    assert.equal(await page.evaluate(()=>consoleFloating),false);
    assert.equal(await page.locator('.console-window').count(),0);
    assert.equal(await page.evaluate(()=>consoleOpenMode),'docked');
    // Explicit docking is respected even while the topology floats.
    await page.evaluate(()=>floatTopology());
    await page.evaluate(()=>openConsole('r2'));
    assert.equal(await page.evaluate(()=>consoleFloating),false);
    await page.evaluate(()=>dockTopology());
    await consoleAction(page.locator('#console-toolbar [data-float-consoles]'));
    assert.equal(await page.locator('.console-window:visible').count(),2);
    assert.equal(await page.evaluate(()=>topologyWindow),null,'Float all affects consoles only');
    assert.equal(await page.evaluate(()=>consoleOpenMode),'floating');
    await page.evaluate(()=>{dockConsole('r1');floatConsole('r2');});
    assert.equal(await page.evaluate(()=>consoleOpenMode),'docked','Showing an existing window does not change preference');
    await consoleAction(page.locator('#console-window-r2 [data-float-consoles]'));
    await page.evaluate(()=>activateConsole('r2'));
    await consoleAction(page.locator('#close-console-r2'));
    await page.evaluate(()=>activateConsole('r1'));
    await consoleAction(page.locator('#console-window-r1 [data-float-consoles]'));
    assert.equal(await page.locator('.console-window:visible').count(),2,'Float all reveals hidden sessions');
    assert.equal(await page.evaluate(()=>originalSessions.every(s=>consoleSessions.get(s.id).terminal===s.terminal&&consoleSessions.get(s.id).socket===s.socket)),true);
    assert.equal(await page.evaluate(()=>consoleSessions.get('r2').lockMine),true);
    assert.ok((await text('r2')).includes('PRESERVE_ALL'));
    await page.setViewportSize({width:390,height:740});
    await page.evaluate(()=>activateConsole('r2'));
    await consoleAction(page.locator('#console-window-r2 [data-dock-consoles]'));
    await consoleAction(page.locator('#console-toolbar [data-float-consoles]'));
    await page.screenshot({path:'/tmp/netlab-console-open-mode-mobile.png'});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.reload();await page.waitForFunction(()=>loaded);
    await page.evaluate(()=>openConsole('r1'));await connected('r1');
    assert.equal(await page.evaluate(()=>consoleOpenMode),null,'Preference is per page session');
    assert.equal(await page.evaluate(()=>consoleFloating),false);
    assert.equal(await page.locator('.console-window').count(),0);
    assert.deepEqual(errors,[]);
    console.log('PASS tabbed opening after group Float, automatic floating after Float tab, dock preference, bulk controls, preserved sessions/locks/history, map independence, mobile controls and reload reset');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
