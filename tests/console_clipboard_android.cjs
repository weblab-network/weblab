// Opt-in: uses Android emulators exposed by ADB (ADB_HOST/ADB_PORT; defaults 127.0.0.1:5037).
// Restarts their Chrome test browser; launches only disposable echo nodes.
const {consoleAction} = require('./console_ui.cjs');
// Real xterm + WebSocket wrapper tests against disposable PTY echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/console_clipboard_android.cjs
// Requires Python 3 and Perl; never connects to or edits the user's lab.
const {_android} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
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


(async()=>{
 let context,devices=[];
 try {
  const base=await new Promise((resolve,reject)=>{let output='';fixture.stdout.on('data',d=>{output+=d;if(output.includes('\n'))resolve(output.trim());});fixture.once('exit',()=>reject(new Error(fixtureLog)));});
  devices=await _android.devices({host:process.env.ADB_HOST||'127.0.0.1',port:Number(process.env.ADB_PORT||5037),omitDriverInstall:true});
  assert.ok(devices.length,'No Android emulator available');
  for (const device of devices.filter(d=>d.serial().startsWith('emulator-'))) {
    context=await device.launchBrowser({viewport:null,hasTouch:true});
    await context.route('**/*',async route=>{try{await route.fulfill({response:await context.request.fetch(route.request())});}catch(e){await route.abort();}});
    const {ws:WebSocket}=require(path.join(path.dirname(require.resolve(process.env.PLAYWRIGHT_MODULE || 'playwright')),'../playwright-core/lib/utilsBundle'));
    await context.routeWebSocket('**/*',route=>{
      const sock=new WebSocket(route.url(),'netlab.console.v2',{origin:base}),queue=[];
      route.onMessage(m=>{if(sock.readyState===1)sock.send(m);else queue.push(m);});route.onClose(()=>sock.close());
      sock.on('open',()=>queue.splice(0).forEach(m=>sock.send(m)));sock.on('message',(m,binary)=>route.send(binary?m:m.toString()));sock.on('error',()=>{});
    });
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.waitForFunction(()=>loaded);
    await page.evaluate(()=>openConsole('r1'));await page.waitForFunction(()=>consoleSessions.get('r1')?.lockReady);
    await consoleAction(page.locator('#console-toolbar [data-console-clipboard]'),true);
    await page.locator('#clipboard-source').selectOption('workspace');await page.locator('#clipboard-enabled').check();
    assert.match(await page.locator('#clipboard-help').innerText(),/does not change your system clipboard/);
    await page.screenshot({path:'/tmp/netlab-clipboard-'+device.serial()+'.png'});
    await page.locator('#clipboard-close').tap();
    await page.evaluate(()=>new Promise(resolve=>consoleSessions.get('r1').terminal.write('\x1b[2J\x1b[HANDROID_COPY',resolve)));
    await page.evaluate(()=>{const t=consoleSessions.get('r1').terminal;t.select(0,t.buffer.active.baseY,12);});
    assert.equal(await page.evaluate(()=>workspaceClipboard),'ANDROID_COPY');
    // Touch context menus are deliberately not replaced by right-click paste.
    assert.equal(await page.evaluate(()=>{const e=new PointerEvent('contextmenu',{bubbles:true,cancelable:true,pointerType:'touch'});$('console-terminal-r1').dispatchEvent(e);return e.defaultPrevented;}),false);
    await page.locator('#instructions-file').setInputFiles({name:'commands.txt',mimeType:'text/plain',buffer:Buffer.from('show vlan brief')});
    await page.waitForFunction(()=>$('instructions-panel').hidden===false);
    await page.evaluate(()=>{const range=document.createRange();range.selectNodeContents($('instructions-body').querySelector('pre'));getSelection().removeAllRanges();getSelection().addRange(range);});
    await page.waitForFunction(()=>workspaceClipboard==='show vlan brief');
    await page.locator('#instructions-hide').tap();
    // Existing on-screen terminal key path still works after closing settings.
    await page.locator('#console-tab').tap();
    await page.waitForFunction(()=>{const b=consoleSessions.get('r1').terminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n').includes('HEX:09');});
    assert.deepEqual(errors,[]);
    console.log('PASS Android clipboard preferences, terminal/document selection hooks, touch-menu bypass and Tab:',device.serial());
    await context.close();context=null;
  }
 } finally {
  if(context)await context.close();for(const device of devices)await device.close();
  const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
 }
})().catch(e=>{console.error(e,fixtureLog);process.exitCode=1;});
