const {consoleAction} = require('./console_ui.cjs');
const {pinnedToolbar} = require('./toolbar_ui.cjs');
// Real xterm + WebSocket wrapper tests against disposable PTY echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/workspace_layouts.cjs
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
    const base = await new Promise((resolve,reject) => {
      let output=''; fixture.stdout.on('data',data=>{output+=data;if(output.includes('\n'))resolve(output.trim());});
      fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${fixtureLog}`)));
    });
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    const page=await browser.newPage({viewport:{width:1600,height:1000}}), errors=[], connections=[];
    page.on('pageerror',e=>{errors.push(e.message);console.error('Browser error:',e.message);}); page.on('websocket',ws=>connections.push(ws.url()));
    let sessions=0;
    const settings={endpoint:'http://model:11434/v1',model:'gpt-oss:20b',context_window:32768,turn_timeout:900};
    await page.route('**/api/agent/*',route=>{
      const action=route.request().url().split('/').pop();
      const result=action==='info'?{enabled:true,providers:[settings.endpoint]}:
        action==='session'?(sessions++,{token:'fixture',settings}):
        {status:'running',messages:[{role:'assistant',text:'Checking the practice lab.'}],settings,proposals:[],approvals:[],history:[],auth:{status:'unknown'},phase:'Thinking'};
      return route.fulfill({json:result});
    });
    await page.goto(base); await page.waitForFunction(()=>loaded && window.weblabLayout && !$('open-agent').hidden);
    const preset=async name=>{
      await page.locator('#layout-menu > summary').click();
      await page.locator(`[data-workspace-layout="${name}"]`).click();
    };
    await page.evaluate(()=>openConsole('r1'));
    await page.waitForFunction(()=>consoleSessions.get('r1')?.status==='Connected');
    await page.keyboard.insertText('LAYOUT_HISTORY'); await page.keyboard.press('Enter');
    await page.locator('#lock-console').click(); await page.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    await page.evaluate(()=>{window.layoutSession=consoleSessions.get('r1');window.layoutSocket=layoutSession.socket;window.layoutTopology=JSON.stringify(topology);});
    await preset('stack');
    await page.waitForFunction(()=>$('agent-messages').textContent.includes('Checking'));
    await page.locator('#agent-chat-tab').click();
    await page.locator('#agent-prompt').fill('Keep this draft');
    const map=await page.locator('#topology-panel').boundingBox(), agent=await page.locator('#agent-panel').boundingBox(), con=await page.locator('#console-panel').boundingBox();
    assert.ok(map.height>850 && map.x+map.width<=agent.x && agent.y+agent.height<=con.y,JSON.stringify({map,agent,con}));
    assert.equal(await page.locator('#device-library').isVisible(),false);
    assert.equal(await page.locator('#inspector').isVisible(),false);
    // Drawers overlay the layout without changing its split widths.
    await page.locator('#toggle-inspector').click(); await page.locator('#inspector').waitFor({state:'visible'});
    assert.equal((await page.locator('#topology-panel').boundingBox()).width,map.width);
    assert.equal(await page.locator('#workspace-right').evaluate(e=>e.inert),true);
    await page.keyboard.press('Escape');
    await page.locator('#workspace-column-divider').focus(); await page.keyboard.press('ArrowLeft');
    assert.ok((await page.locator('#topology-panel').boundingBox()).width<map.width);
    const rowBefore=await page.locator('#console-panel').boundingBox();
    const handle=await page.locator('#workspace-row-divider').boundingBox();
    await page.mouse.move(handle.x+handle.width/2,handle.y+4);await page.mouse.down();await page.mouse.move(handle.x+handle.width/2,handle.y-65);await page.mouse.up();
    assert.ok((await page.locator('#console-panel').boundingBox()).height>rowBefore.height+30);
    await page.evaluate(()=>openConsole('r2')); await page.waitForFunction(()=>consoleSessions.get('r2')?.status==='Connected');
    assert.equal((await page.locator('#topology-panel').boundingBox()).height,map.height);
    await page.locator('#workspace-instructions-tab').click();
    assert.equal(await page.locator('#instructions-open').isEnabled(),true,'File picker recovers after authentication lookup');
    assert.match(await page.locator('#instructions-body').textContent(),/No instructions file is open/);
    const chooser = page.waitForEvent('filechooser');
    await page.locator('#instructions-open').click();
    await (await chooser).setFiles({name:'layout.md',mimeType:'text/markdown',buffer:Buffer.from('# Practice\nKeep this exercise open.')});
    assert.equal(await page.locator('#instructions-panel').evaluate(e=>getComputedStyle(e).borderLeftWidth),'0px');
    assert.equal(await page.locator('#agent-panel').evaluate(e=>getComputedStyle(e).borderLeftWidth),'0px');
    await page.locator('#instructions-body h1').waitFor();
    assert.equal(await page.locator('#agent-panel').isVisible(),false);
    await page.locator('#workspace-agent-tab').click();
    assert.equal(await page.locator('#agent-prompt').inputValue(),'Keep this draft');
    await page.screenshot({path:'/tmp/weblab-workspace-stack.png'});
    // Hide and reopen a pane, then float an individual console and dock it again.
    await page.locator('#agent-hide').click(); assert.equal(await page.locator('#agent-panel').isVisible(),false);
    await page.locator('#workspace-agent-tab').click(); await page.locator('#agent-panel').waitFor({state:'visible'});
    await page.evaluate(()=>floatConsole('r2'));
    assert.equal(await page.locator('#console-window-r2').isVisible(),true);
    await page.evaluate(()=>dockConsole('r2')); assert.equal(await page.locator('#console-window-r2').count(),0);
    await consoleAction(page.locator('#detach-console'));
    assert.equal(await page.locator('#console-panel').evaluate(e=>e.classList.contains('floating')),true);
    await page.evaluate(()=>openConsole('r1'));
    assert.equal(await page.locator('#console-panel').evaluate(e=>e.classList.contains('floating')),true);
    await consoleAction(page.locator('#detach-console'));
    assert.equal(await page.locator('#console-panel').evaluate(e=>e.parentElement.id),'workspace-console-slot');
    await preset('side'); await page.locator('#workspace-console-tab').click();
    assert.ok((await page.locator('#console-panel').boundingBox()).height>800);
    await page.locator('#workspace-agent-tab').click(); assert.equal(await page.locator('#console-panel').isVisible(),false);
    await page.evaluate(()=>openConsole('r1')); assert.equal(await page.locator('#console-panel').isVisible(),true);
    await page.locator('#workspace-agent-tab').click(); await page.locator('#agent-toolbar .workspace-float-document').click();
    assert.equal(await page.locator('#agent-panel').evaluate(e=>e.parentElement.id),'console-windows');
    assert.equal(await page.locator('#agent-panel').evaluate(e=>getComputedStyle(e).borderLeftWidth),'1px');
    await page.locator('#workspace-agent-tab').click();
    assert.equal(await page.locator('#agent-panel').evaluate(e=>e.parentElement.id),'workspace-document-slot');
    await preset('build'); await preset('side');
    assert.equal(await page.locator('#agent-panel').isVisible(),true,'Remember the side tool when regrouping consoles');
    // Breakpoint fallback uses one tool at a time, preserving the full desktop preset.
    await preset('stack'); await page.setViewportSize({width:800,height:1000});
    await page.waitForFunction(()=>document.querySelector('.workspace-narrow'));
    await page.locator('#workspace-console-tab').click();
    assert.equal(await page.locator('#agent-panel').isVisible(),false);
    assert.equal(await page.locator('#console-panel').isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.setViewportSize({width:1600,height:1000});
    await page.locator('#agent-panel').waitFor({state:'visible'});
    await preset('build');
    assert.equal(await page.locator('#device-library').isVisible(),true);
    await preset('side'); await preset('free');
    await page.evaluate(()=>arrangeWindows());
    await preset('stack');
    assert.equal(await page.evaluate(()=>layoutSession===consoleSessions.get('r1') && layoutSocket===layoutSession.socket && layoutSession.lockMine),true);
    assert.equal(await page.evaluate(()=>JSON.stringify(topology)===layoutTopology),true);
    assert.equal(connections.length,2,'No reconnects during layout changes');
    assert.equal(sessions,1,'Layout changes reuse the Agent session');
    const saved=await page.evaluate(()=>localStorage.getItem('weblab.workspace.v1'));
    await page.reload(); await page.waitForFunction(()=>window.weblabLayout?.active());
    assert.deepEqual(JSON.parse(await page.evaluate(()=>localStorage.getItem('weblab.workspace.v1'))),JSON.parse(saved));
    // Agent unavailable: the saved preset remains useful through Instructions.
    await page.unroute('**/api/agent/*'); await page.reload();
    await page.waitForFunction(()=>window.weblabLayout?.active());
    assert.equal(await page.locator('#workspace-agent-tab').isVisible(),false);
    assert.equal(await page.locator('#instructions-panel').isVisible(),true);
    await page.locator('#layout-menu > summary').click(); await page.locator('#workspace-reset').click();
    assert.equal(await page.evaluate(()=>weblabLayout.active()),false);
    assert.deepEqual(errors,[]);
    console.log('PASS workspace presets, dividers, drawers, responsive tools, persistence, optional Agent, live console/Agent continuity');
  } finally {
    if(browser)await browser.close();const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
  }
})().catch(e=>{console.error(e,fixtureLog);process.exitCode=1;});
