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
    await type('HISTORY_ONE'); await hasText('r1', 'HISTORY_ONE');
    await page.evaluate(() => window.firstTerminal = consoleSessions.get('r1').terminal);
    await clickNode('r2'); await connected('r2');
    await type('HISTORY_TWO'); await hasText('r2', 'HISTORY_TWO');
    assert.ok(!(await text('r1')).includes('HISTORY_TWO'), 'Keystrokes stay with the selected console');
    await page.evaluate(() => sendConsoleInput(consoleSessions.get('r1'), 'BACKGROUND_OUTPUT\r\n'));
    await hasText('r1', 'BACKGROUND_OUTPUT');
    assert.equal(await page.evaluate(() => consoleNode), 'r2', 'Background output does not steal focus');
    await clickNode('r1');
    assert.equal(await page.getByRole('tab').count(), 2);
    assert.equal(connections.length, 2, 'Switching consoles never replaces their connections');
    assert.equal(await page.evaluate(() => consoleSessions.get('r1').terminal === window.firstTerminal), true);
    assert.ok((await text('r1')).includes('HISTORY_ONE'));

    // Preserve the scroll position and complete scrollback, not just the visible rows.
    await page.evaluate(() => sendConsoleInput(consoleSessions.get('r1'), Array.from({length:100}, (_, i) => `LINE_${i}\r\n`).join('')));
    await hasText('r1', 'LINE_99');
    const viewport = await page.evaluate(() => { const term = consoleSessions.get('r1').terminal; term.scrollToLine(10); return term.buffer.active.viewportY; });
    await clickNode('r2'); await clickNode('r1');
    assert.equal(await page.evaluate(() => consoleSessions.get('r1').terminal.buffer.active.viewportY), viewport);
    await consoleAction(page.locator('#close-console'));
    assert.equal(await page.locator('#console-panel').isVisible(), false);
    await page.evaluate(() => sendConsoleInput(consoleSessions.get('r1'), 'WHILE_HIDDEN\r\n'));
    await hasText('r1', 'WHILE_HIDDEN');
    await clickNode('r1');
    assert.equal(connections.length, 2, 'Hiding the pane keeps sessions connected');
    await consoleAction(page.locator('#reconnect-console')); await connected('r1');
    assert.ok((await text('r1')).includes('HISTORY_ONE'), 'Reconnect preserves local scrollback');

    await page.getByRole('tab', {name:'r1', exact:true}).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('tab', {name:'r2', exact:true}).getAttribute('aria-selected'), 'true');
    await page.getByRole('tab', {name:'r2', exact:true}).click();
    await type('ONLY_TWO'); await hasText('r2', 'ONLY_TWO');
    await consoleAction(page.locator('#clear-console'));
    assert.ok((await text('r1')).includes('HISTORY_ONE'), 'Clear affects only the active terminal');

    await page.request.post(base + '/api/nodes/r1/stop', {data:{}}); await refreshState();
    assert.equal(await page.evaluate(() => consoleSessions.get('r1').status), 'Stopped');
    await clickNode('r1');
    assert.equal(await page.locator('#reconnect-console').isDisabled(), true);
    assert.ok((await text('r1')).includes('HISTORY_ONE'), 'Stopped nodes retain their history');
    await page.request.post(base + '/api/nodes/r1/start', {data:{}}); await refreshState(); await connected('r1');
    assert.ok((await text('r1')).includes('HISTORY_ONE'), 'Restart reuses the same terminal');
    const snapshot = await (await page.request.get(base + '/api/state')).json();
    snapshot.topology.nodes[0].name = 'Renamed router';
    await page.request.put(base + '/api/topology', {data:snapshot.topology}); await refreshState();
    assert.equal(await page.getByRole('tab', {name:'Renamed router', exact:true}).count(), 1);
    await page.evaluate(()=>consoleSessions.get('r2').terminal.write('\r\nLOCAL_ONLY_HISTORY\r\n'));
    await hasText('r2', 'LOCAL_ONLY_HISTORY');
    await page.getByRole('button', {name:'Close r2 console', exact:true}).click();
    assert.equal(await page.getByRole('tab').count(), 1, 'Closing a background tab leaves the active tab alone');
    assert.equal(await page.evaluate(() => consoleNode), 'r1');
    await clickNode('r2'); await connected('r2');
    await hasText('r2', 'HISTORY_TWO');
    assert.ok(!(await text('r2')).includes('LOCAL_ONLY_HISTORY'), 'Reopening creates a new terminal and replays shared output, not discarded local history');
    await page.getByRole('button', {name:'Close r2 console', exact:true}).click();
    assert.equal(await page.evaluate(() => consoleNode), 'r1', 'Closing the active tab selects its neighbor');
    await consoleAction(page.locator('#expand-console'));
    await page.setViewportSize({width:1100, height:800});
    await page.waitForFunction(() => consoleSessions.get('r1').terminal.cols > 0);
    await page.screenshot({path:'/tmp/iol-console-tabs.png'});

    // Removing a node closes its session and any pending retry timer.
    await page.request.post(base + '/api/lab/stop', {data:{}}); await refreshState();
    await page.request.put(base + '/api/topology', {data:{name:'Empty', nodes:[], links:[]}}); await refreshState();
    assert.equal(await page.getByRole('tab').count(), 0);
    assert.equal(await page.locator('#console-panel').isVisible(), false);
    assert.equal(await page.evaluate(() => consoleSessions.size), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: independent sessions, background output, history/scroll position, hide/reopen, reconnect, keyboard navigation, clear isolation, stop/restart, rename, close/reopen, resize, node removal; no browser errors.');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
