const {consoleAction} = require('./console_ui.cjs');
// Real xterm + WebSocket wrapper tests against disposable PTY echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/console_clipboard.cjs
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
    const base = await new Promise((resolve,reject)=>{let output='';fixture.stdout.on('data',d=>{output+=d;if(output.includes('\n'))resolve(output.trim());});fixture.once('exit',()=>reject(new Error(fixtureLog)));});
    browser=await chromium.launch({headless:true,args:['--no-sandbox','--no-proxy-server','--host-resolver-rules=MAP netlab.test 127.0.0.1']});
    const context=await browser.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']});
    const page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.waitForFunction(()=>loaded);
    await page.evaluate(()=>openConsole('r1'));await page.waitForFunction(()=>consoleSessions.get('r1')?.lockReady);
    const settings=async()=>{await consoleAction(page.locator('#console-toolbar [data-console-clipboard]'));};
    const select=async text=>{
      await page.evaluate(text=>new Promise(resolve=>consoleSessions.get('r1').terminal.write('\x1b[2J\x1b[H'+text,resolve)),text);
      await page.evaluate(length=>consoleSessions.get('r1').terminal.select(0,consoleSessions.get('r1').terminal.buffer.active.baseY,length),text.length);
    };
    const received=hex=>page.waitForFunction(hex=>{
      const b=consoleSessions.get('r1').terminal.buffer.active;
      return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n').includes('HEX:'+hex);
    },hex);
    const rightClick=()=>page.locator('#console-terminal-r1 .xterm-screen').click({button:'right',position:{x:20,y:20}});
    assert.equal(await page.title(),'Weblab · Topology workspace');
    assert.equal(await page.locator('.brand').getAttribute('aria-label'),'Weblab home');
    await settings();assert.equal(await page.locator('#clipboard-enabled').isChecked(),false);
    await page.locator('#clipboard-source').selectOption('workspace');await page.locator('#clipboard-enabled').check();await page.locator('#clipboard-close').click();
    await page.evaluate(()=>navigator.clipboard.writeText('SYSTEM_UNCHANGED'));
    // Real mouse selection, rather than only the terminal selection API.
    await page.evaluate(()=>new Promise(resolve=>consoleSessions.get('r1').terminal.write('\x1b[2J\x1b[HSELECT_ME',resolve)));
    const screen=await page.locator('#console-terminal-r1 .xterm-screen').boundingBox();
    const cell=screen.width/await page.evaluate(()=>consoleSessions.get('r1').terminal.cols);
    await page.mouse.move(screen.x+1,screen.y+7);await page.mouse.down();await page.mouse.move(screen.x+9*cell,screen.y+7,{steps:10});await page.mouse.up();
    await page.waitForFunction(()=>workspaceClipboard==='SELECT_ME');
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'SYSTEM_UNCHANGED');
    await rightClick();await received(Buffer.from('SELECT_ME').toString('hex'));
    // Floating terminals inherit the same preference and preserve the session.
    await page.evaluate(()=>floatConsole('r1'));
    assert.equal(await page.locator('#console-window-r1 [data-console-clipboard]').textContent(),'Clipboard: workspace');
    await select('FLOATING');await rightClick();await received(Buffer.from('FLOATING').toString('hex'));
    await page.evaluate(()=>openConsole('r2'));await page.waitForFunction(()=>consoleSessions.get('r2')?.lockReady);
    await page.locator('#console-terminal-r2 .xterm-screen').click({button:'right',position:{x:20,y:20}});
    await page.waitForFunction(()=>{const b=consoleSessions.get('r2').terminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n').includes('HEX:464c4f4154494e47');});
    await page.evaluate(()=>{closeConsole('r2');activateConsole('r1');});
    // Instructions share selection copying, while the map and cross-area selections do not.
    await page.locator('#instructions-file').setInputFiles({name:'commands.md',mimeType:'text/markdown',buffer:Buffer.from('```text\nshow vlan brief\nshow spanning-tree\n```')});
    await page.waitForFunction(()=>$('instructions-panel').hidden===false);
    const chooseInstructions=(selector='code')=>page.evaluate(selector=>{const range=document.createRange();range.selectNodeContents($('instructions-body').querySelector(selector));const selection=getSelection();selection.removeAllRanges();selection.addRange(range);},selector);
    await chooseInstructions();await page.waitForFunction(()=>workspaceClipboard==='show vlan brief\nshow spanning-tree');
    assert.equal(await page.evaluate(()=>{const e=new MouseEvent('contextmenu',{bubbles:true,cancelable:true});$('instructions-body').dispatchEvent(e);return e.defaultPrevented;}),false);
    await page.locator('#instructions-hide').click();
    await page.evaluate(()=>activateConsole('r1'));
    await rightClick();await received(Buffer.from('show vlan brief\rshow spanning-tree').toString('hex'));
    const copied=await page.evaluate(()=>workspaceClipboard);
    await page.evaluate(()=>{const r=document.createRange();r.selectNodeContents($('link-count'));getSelection().removeAllRanges();getSelection().addRange(r);});
    await page.waitForTimeout(180);assert.equal(await page.evaluate(()=>workspaceClipboard),copied);
    await page.locator('#open-instructions').click();
    await page.evaluate(()=>{const r=document.createRange();r.setStart($('lab-name').parentElement,0);r.setEnd($('instructions-body'),$('instructions-body').childNodes.length);getSelection().removeAllRanges();getSelection().addRange(r);});
    await page.waitForTimeout(180);assert.equal(await page.evaluate(()=>workspaceClipboard),copied);
    await page.locator('#instructions-hide').click();
    // Shift, touch context menus and nonterminal areas are left to the browser.
    assert.deepEqual(await page.evaluate(()=>{
      const terminal=$('console-terminal-r1');
      return [new MouseEvent('contextmenu',{bubbles:true,cancelable:true,shiftKey:true}),new PointerEvent('contextmenu',{bubbles:true,cancelable:true,pointerType:'touch'})].map(e=>{terminal.dispatchEvent(e);return e.defaultPrevented;});
    }),[false,false]);
    assert.equal(await page.evaluate(()=>{const e=new MouseEvent('contextmenu',{bubbles:true,cancelable:true});$('lab-name').dispatchEvent(e);return e.defaultPrevented;}),false);
    await page.evaluate(()=>dockConsole('r1'));
    // Real system clipboard: includes text copied outside the terminal.
    await settings();await page.locator('#clipboard-source').selectOption('system');await page.locator('#clipboard-close').click();
    await page.locator('#open-instructions').click();
    await chooseInstructions();await page.waitForFunction(async()=>await navigator.clipboard.readText()==='show vlan brief\nshow spanning-tree');
    await page.locator('#instructions-hide').click();
    await select('SYSTEM_COPY');await page.waitForFunction(async()=>await navigator.clipboard.readText()==='SYSTEM_COPY');
    await page.locator('#instructions-file').setInputFiles({name:'commands.txt',mimeType:'text/plain',buffer:Buffer.from('show interfaces status')});
    await page.waitForFunction(()=>$('instructions-title').textContent==='commands.txt');
    const commandBox=await page.evaluate(()=>{const r=document.createRange();r.selectNodeContents($('instructions-body').querySelector('pre'));return r.getBoundingClientRect().toJSON();});
    await page.mouse.move(commandBox.x+.5,commandBox.y+commandBox.height/2);await page.mouse.down();
    await page.mouse.move(commandBox.right+.5,commandBox.y+commandBox.height/2,{steps:10});await page.mouse.up();
    await page.waitForFunction(async()=>await navigator.clipboard.readText()==='show interfaces status');
    await page.locator('#instructions-hide').click();
    await rightClick();await received(Buffer.from('show interfaces status').toString('hex'));
    await page.evaluate(()=>navigator.clipboard.writeText('EXTERNAL_TEXT'));
    await rightClick();await received(Buffer.from('EXTERNAL_TEXT').toString('hex'));
    // The normal xterm paste path preserves bracketed paste and CR normalization.
    await page.evaluate(()=>new Promise(resolve=>consoleSessions.get('r1').terminal.write('\x1b[?2004h',resolve)));
    await page.evaluate(()=>navigator.clipboard.writeText('one\ntwo'));
    await rightClick();await received(Buffer.from('\x1b[200~one\rtwo\x1b[201~').toString('hex'));
    await page.evaluate(()=>new Promise(resolve=>consoleSessions.get('r1').terminal.write('\x1b[?2004l',resolve)));
    // Permission denial must not paste a stale workspace value.
    await page.evaluate(()=>{window.originalClipboard=navigator.clipboard;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('denied')},readText:async()=>{throw Error('denied')}}});});
    await rightClick();await page.waitForFunction(()=>$('toast').textContent.includes('System paste was denied'));
    // A lock gained by another station while readText is pending cancels paste.
    await page.evaluate(()=>{window.inputFrames=[];const s=consoleSessions.get('r1'),send=s.socket.send.bind(s.socket);s.socket.send=data=>{if(typeof data!=='string')inputFrames.push(Array.from(data));send(data);};Object.defineProperty(navigator,'clipboard',{configurable:true,value:{readText:()=>new Promise(resolve=>window.resolveClipboard=resolve)}});});
    await rightClick();await page.waitForFunction(()=>!!window.resolveClipboard);
    const observer=await context.newPage();await observer.goto(base);await observer.waitForFunction(()=>loaded);await observer.evaluate(()=>openConsole('r1'));await observer.waitForFunction(()=>consoleSessions.get('r1')?.lockReady);
    await observer.locator('#lock-console').click();await observer.waitForFunction(()=>consoleSessions.get('r1').lockMine);await page.waitForFunction(()=>consoleSessions.get('r1').locked&&!consoleSessions.get('r1').lockMine);
    await page.evaluate(()=>resolveClipboard('MUST_NOT_PASTE'));await page.waitForTimeout(100);
    assert.deepEqual(await page.evaluate(()=>inputFrames),[]);
    await rightClick();await page.waitForFunction(()=>$('toast').textContent.includes('locked for observation'));
    await observer.close();
    // Saved preference survives reload; workspace text does not.
    await settings();await page.locator('#clipboard-source').selectOption('workspace');await page.locator('#clipboard-close').click();
    await page.reload();await page.waitForFunction(()=>loaded);
    assert.equal(await page.evaluate(()=>clipboardEnabled),true);assert.equal(await page.evaluate(()=>workspaceClipboard),'');
    // Unavailable API follows the explicit workspace path, with System disabled.
    const http=await context.newPage();
    await http.goto(base.replace('127.0.0.1','netlab.test'));assert.equal(await http.evaluate(()=>isSecureContext),false);await http.waitForFunction(()=>loaded);await http.evaluate(()=>openConsole('r1'));await http.waitForFunction(()=>consoleSessions.get('r1')?.lockReady);
    await consoleAction(http.locator('#console-toolbar [data-console-clipboard]'));
    console.log('HTTP_CLIPBOARD',await http.evaluate(()=>({secure:isSecureContext,available:systemClipboardAvailable,option:$('clipboard-source').outerHTML,help:$('clipboard-help').textContent})));
    assert.equal(await http.locator('#clipboard-source option[value=system]').evaluate(e=>e.disabled),true);
    assert.match(await http.locator('#clipboard-help').innerText(),/unavailable.*does not change your system clipboard/);
    await http.screenshot({path:'/tmp/netlab-clipboard-settings.png'});await http.close();
    assert.deepEqual(errors,[]);
    console.log('PASS mouse selection, workspace/system clipboard, external text, floating sessions, bracketed paste, native menu bypass, permission denial, pending-read lock race and preference persistence');
  } finally {
    if(browser)await browser.close();
    const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
  }
})().catch(e=>{console.error(e,fixtureLog);process.exitCode=1;});
