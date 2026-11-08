// Real shared WebSockets/PTYs in a disposable lab. No live devices or model calls.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {consoleAction} = require('./console_ui.cjs');
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
    test.topology['nodes'] += [dict(test.topology['nodes'][0], id='stopped', name='Stopped', iol_id=3),
                               dict(id='drawing', name='Diagram', type='network', image='')]
    test.lab.save(test.topology)
    test.lab.start('r1')
    test.lab.start('r2')
    print('http://127.0.0.1:' + str(test.port), flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
`], {cwd:path.resolve(__dirname, '..'), stdio:['pipe', 'pipe', 'pipe']});
let log = '';
fixture.stderr.on('data', data => log += data);
(async () => {
  let browser;
  try {
    const base = await new Promise((resolve, reject) => {
      let output = '';
      fixture.stdout.on('data', data => { output += data; if (output.includes('\n')) resolve(output.trim()); });
      fixture.once('exit', code => reject(new Error(`Fixture exited ${code}: ${log}`)));
    });
    browser = await chromium.launch({headless:true, args:['--no-sandbox']});
    const context = await browser.newContext({viewport:{width:1360, height:900}});
    const page = await context.newPage(), other = await context.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base); await page.waitForFunction(() => loaded);
    await other.goto(base); await other.waitForFunction(() => loaded);
    const open = () => consoleAction(page.locator('.canvas-toolbar [data-console-bulk]'));
    const close = () => page.locator('#console-bulk-close').click();
    const check = id => page.locator(`#console-bulk-targets input[value="${id}"]`);
    const text = page.locator('#console-bulk-commands'), send = page.locator('#console-bulk-send');
    const received = (id, value) => page.waitForFunction(({id, hex}) => {
      const buffer = consoleSessions.get(id)?.terminal.buffer.active;
      return buffer && Array.from({length:buffer.length}, (_,i) => buffer.getLine(i).translateToString(true)).join('\n').includes('HEX:' + hex);
    }, {id, hex:Buffer.from(value).toString('hex')});
    await open();
    assert.equal(await page.locator('#console-bulk-targets input').count(), 3);
    assert.equal(await check('stopped').isDisabled(), true);
    assert.equal(await check('r1').isChecked(), false);
    await check('r1').check();
    await page.locator('#console-bulk-example').click();
    assert.equal(await text.inputValue(), '\n\nenable\nterminal length 0\n');
    await send.click(); await received('r1', '\r\renable\rterminal length 0\r');
    await page.waitForFunction(() => $('console-bulk-summary').textContent.startsWith('Batch finished'));
    assert.equal(await page.evaluate(() => consoleSessions.has('r2')), false, 'Unselected console stays closed');
    assert.equal(await send.isDisabled(), true, 'No accidental second click replay');
    await close();
    // Two selected nodes, final line gets Enter, even when the textarea has none.
    await open(); await page.locator('#console-bulk-select').click();
    await text.fill('show clock\nshow version'); await send.click();
    await received('r1', 'show clock\rshow version\r'); await received('r2', 'show clock\rshow version\r');
    await page.waitForFunction(() => $('console-bulk-summary').textContent.startsWith('Batch finished'));
    await page.screenshot({path:'/tmp/netlab-console-bulk.png'});
    await close();
    // Another station's lock is checked again after selection, before delivery.
    await open(); await check('r1').check(); await check('r2').check(); await text.fill('LOCK_PROBE');
    await other.evaluate(() => openConsole('r2')); await other.waitForFunction(() => consoleSessions.get('r2')?.lockReady);
    await other.evaluate(() => consoleControl(consoleSessions.get('r2'), 'lock'));
    await page.waitForFunction(() => consoleSessions.get('r2')?.locked);
    await send.click(); await received('r1', 'LOCK_PROBE\r');
    await page.waitForFunction(() => $('console-bulk-targets').textContent.includes('Skipped · Locked by another station'));
    assert.equal(await page.evaluate(() => {
      const b=consoleSessions.get('r2').terminal.buffer.active;
      return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n').includes('4c4f434b5f50524f4245');
    }), false);
    await close();
    await other.evaluate(() => consoleControl(consoleSessions.get('r2'), 'unlock'));
    await page.waitForFunction(() => !consoleSessions.get('r2').locked);
    // A lock this station already owns remains owned after bulk input.
    await page.evaluate(() => consoleControl(consoleSessions.get('r1'), 'lock'));
    await page.waitForFunction(() => consoleSessions.get('r1').lockMine);
    await open(); await check('r1').check(); await text.fill('OWNED'); await send.click();
    await received('r1', 'OWNED\r'); assert.equal(await page.evaluate(() => consoleSessions.get('r1').lockMine), true);
    await close();
    await open(); await check('r1').check();
    for (const value of ['é'.repeat(2048), 'x\n'.repeat(33), 'show\tclock']) {
      await text.fill(value); assert.equal(await send.isDisabled(), true, value.slice(0,20));
    }
    await text.fill('CANCELLED'); await close(); // Cancellation never sends.
    // Reader UI cannot select devices or submit, even with an existing socket.
    await page.evaluate(() => { window.bulkSavedCan = window.weblabAuth.can; window.weblabAuth.can = () => false; });
    await open(); assert.equal(await check('r1').isDisabled(), true); assert.equal(await send.isDisabled(), true); await close();
    await page.evaluate(() => { window.weblabAuth.can = window.bulkSavedCan; });
    // Entry point survives cloned toolbars and adopted pop-out panes.
    await page.evaluate(() => floatConsole('r1'));
    const popupPromise = page.waitForEvent('popup');
    await page.locator('#console-window-r1 .popout-button').click();
    const popup = await popupPromise; await popup.waitForLoadState();
    await consoleAction(popup.locator('[data-console-bulk]'));
    assert.equal(await page.locator('#console-bulk-dialog').isVisible(), true);
    await close(); await popup.close();
    await page.evaluate(() => dockConsole('r1'));
    // Small viewport keeps the modal inside the screen and scrolls as needed.
    await page.setViewportSize({width:390,height:700}); await open();
    const box=await page.locator('#console-bulk-dialog').boundingBox();
    assert.ok(box.x>=0 && box.x+box.width<=391 && box.y>=0 && box.y+box.height<=701, JSON.stringify(box));
    await page.screenshot({path:'/tmp/netlab-console-bulk-mobile.png'});
    await close(); assert.deepEqual(errors, []);
    console.log('Bulk console UI passed: selection, exact bytes, locks, bounds, cancellation, reader and pop-out.');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end('\n'); await once(fixture, 'exit');
  }
})().catch(error => { console.error(error, log); process.exitCode=1; });
