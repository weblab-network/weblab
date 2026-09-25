const {consoleAction} = require('./console_ui.cjs');
// Shared consoles across independent desktop/tablet browser sessions.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/console_locks.cjs
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
    const desktop = await browser.newContext({viewport:{width:1440,height:1000}});
    const mobile = await browser.newContext({viewport:{width:360,height:800},isMobile:true,hasTouch:true});
    const thirdContext = await browser.newContext({viewport:{width:1440,height:1000}});
    const first=await desktop.newPage(), second=await mobile.newPage(), third=await thirdContext.newPage();
    const errors=[];
    for(const page of [first,second,third]) {
      page.on('pageerror',e=>errors.push(e.message));
      await page.goto(base); await page.waitForFunction(()=>loaded);
      await page.evaluate(()=>openConsole('r1'));
      await page.waitForFunction(()=>consoleSessions.get('r1')?.lockReady);
    }
    const mine=page=>page.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    const observing=page=>page.waitForFunction(()=>consoleSessions.get('r1').locked&&!consoleSessions.get('r1').lockMine);
    const history=page=>page.evaluate(()=>{
      const b=consoleSessions.get('r1').terminal.buffer.active;
      return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n');
    });
    // The checkbox reflects server acknowledgment, not the immediate click.
    await first.locator('#lock-console').click(); await mine(first); await observing(second);
    assert.equal(await second.locator('#lock-console').isDisabled(),true);
    assert.equal(await second.evaluate(()=>consoleSessions.get('r1').terminal.options.disableStdin),true);
    await second.locator('.console-terminal:not([hidden]) textarea').focus();
    await second.keyboard.insertText('BLOCKED-BROWSER-INPUT');
    await first.evaluate(()=>sendConsoleInput(consoleSessions.get('r1'),'OWNER-WRITES\r\n'));
    await second.waitForFunction(()=>consoleSessions.get('r1').offset>0);
    assert.ok(!(await history(first)).includes('BLOCKED-BROWSER-INPUT'));
    await second.locator('#takeover-console').tap();
    assert.equal(await second.locator('#takeover-dialog button[value="cancel"]').evaluate(el=>el===document.activeElement),true);
    await second.keyboard.press('Escape');
    assert.equal(await second.locator('#takeover-dialog').isVisible(),false);
    assert.equal(await first.evaluate(()=>consoleSessions.get('r1').lockMine),true);
    await second.locator('#takeover-console').tap();
    // A third station takes the lock while the tablet's confirmation is open.
    await third.locator('#takeover-console').click();
    await third.locator('#confirm-takeover').click(); await mine(third);
    await second.waitForFunction(()=>consoleSessions.get('r1').lockRevision>=2);
    await second.locator('#confirm-takeover').tap();
    await second.waitForFunction(()=>$('toast').textContent.includes('lock changed'));
    assert.equal(await third.evaluate(()=>consoleSessions.get('r1').lockMine),true);
    await second.locator('#takeover-console').tap();
    await second.locator('#confirm-takeover').tap(); await mine(second); await observing(third);
    assert.equal(await second.locator('#lock-console').isEnabled(),true);
    assert.equal(await third.evaluate(()=>consoleSessions.get('r1').terminal.options.disableStdin),true);
    assert.match(await third.locator('#toast').textContent(),/took over input/);
    // Lock controls follow the selected tab; r2's owner is independent of r1.
    await first.evaluate(()=>openConsole('r2'));
    await first.waitForFunction(()=>consoleSessions.get('r2').lockReady);
    assert.equal(await first.locator('#lock-console').isChecked(),false);
    await first.locator('#lock-console').click();
    await first.waitForFunction(()=>consoleSessions.get('r2').lockMine);
    await first.evaluate(()=>activateConsole('r1'));
    assert.equal(await first.locator('#takeover-console').isVisible(),true);
    await second.locator('#lock-console').click();
    await first.waitForFunction(()=>!consoleSessions.get('r1').locked);
    assert.equal(await first.locator('#lock-console').isEnabled(),true);
    assert.equal(await first.evaluate(()=>consoleSessions.get('r2').lockMine),true);
    await second.locator('#lock-console').click(); await mine(second);
    await consoleAction(second.locator('#close-console'), true);
    assert.equal(await second.evaluate(()=>consoleSessions.get('r1').lockMine),true,'Hiding the pane retains the lock');
    await second.evaluate(()=>activateConsole('r1'));
    for(const width of [320,360,412]) {
      await second.setViewportSize({width,height:800});
      const box=await second.locator('#console-lock-label').boundingBox();
      assert.ok(box && box.x>=0 && box.x+box.width<=width,'Lock control fits mobile screen');
      assert.equal(await second.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    }
    await second.evaluate(()=>closeConsole('r1'));
    await first.waitForFunction(()=>!consoleSessions.get('r1').locked);
    assert.deepEqual(errors,[]);
    console.log('PASS: input locks, read-only observers, cancel/Escape, confirmed takeover, stale confirmation, per-tab ownership, disconnect release and mobile controls.');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
