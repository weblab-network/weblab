const {consoleAction} = require('./console_ui.cjs');
// Shared consoles across independent desktop/tablet browser sessions.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/shared_consoles.cjs
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
    const tablet = await browser.newContext({viewport:{width:820,height:1180},isMobile:true,hasTouch:true});
    const first = await desktop.newPage(), second = await tablet.newPage();
    const errors = [];
    for (const page of [first,second]) page.on('pageerror',e=>errors.push(e.message));
    const connect = async page => {
      await page.goto(base); await page.waitForFunction(()=>loaded);
      await page.evaluate(()=>openConsole('r1'));
      await page.waitForFunction(()=>consoleSessions.get('r1')?.status==='Connected');
    };
    const output = page => page.evaluate(()=>{
      const buffer=consoleSessions.get('r1').terminal.buffer.active;
      return Array.from({length:buffer.length},(_,i)=>buffer.getLine(i).translateToString(true)).join('\n');
    });
    const until = (page,text) => page.waitForFunction(text=>{
      const b=consoleSessions.get('r1').terminal.buffer.active;
      return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n').includes(text);
    },text);
    const send = async (page,text) => {
      await page.locator('.console-terminal:not([hidden]) textarea').focus();
      await page.keyboard.insertText(text); await page.keyboard.press('Enter');
    };
    await connect(first);
    await send(first,'FROM-DESKTOP'); await until(first,'FROM-DESKTOP');
    await connect(second); await until(second,'FROM-DESKTOP');
    assert.match(await second.locator('#console-status').textContent(),/shared/);
    await send(second,'FROM-TABLET');
    await Promise.all([until(first,'FROM-TABLET'),until(second,'FROM-TABLET')]);
    await consoleAction(first.locator('#reconnect-console'));
    await first.waitForFunction(()=>consoleSessions.get('r1').status==='Connected');
    await send(second,'AFTER-RECONNECT'); await until(first,'AFTER-RECONNECT');
    assert.equal((await output(first)).split('FROM-DESKTOP').length-1,1,'Reconnect does not duplicate history');
    // Stop reconnecting temporarily to emulate an offline workstation.
    await first.evaluate(()=>closeSocket(consoleSessions.get('r1')));
    await send(second,'WHILE-DESKTOP-AWAY'); await until(second,'WHILE-DESKTOP-AWAY');
    await consoleAction(first.locator('#reconnect-console')); await until(first,'WHILE-DESKTOP-AWAY');
    assert.equal((await output(first)).split('FROM-TABLET').length-1,1);
    assert.equal((await output(first)).split('WHILE-DESKTOP-AWAY').length-1,1);
    await desktop.close();
    await send(second,'DESKTOP-CLOSED'); await until(second,'DESKTOP-CLOSED');
    await second.reload(); await second.waitForFunction(()=>loaded);
    await second.evaluate(()=>openConsole('r1')); await until(second,'DESKTOP-CLOSED');
    // Restarting the device changes the stream epoch and starts a new history.
    await second.request.post(base+'/api/nodes/r1/stop',{data:{}});
    await second.evaluate(async()=>accept(await api('/api/state')));
    await second.request.post(base+'/api/nodes/r1/start',{data:{}});
    await second.evaluate(async()=>accept(await api('/api/state')));
    await until(second,'Console restarted or older output is no longer available');
    await send(second,'AFTER-DEVICE-RESTART'); await until(second,'AFTER-DEVICE-RESTART');
    assert.deepEqual(errors,[]);
    console.log('PASS: separate desktop/tablet contexts share live input/output, replay history, resume without duplicates, and survive peer closure/device restart.');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
