// Live pane transfer against disposable echo nodes; never uses the active lab.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/pane_popouts.cjs
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
    test.topology['nodes'][0].update(x=150,y=180)
    test.topology['nodes'][1].update(x=450,y=180)
    test.lab.save(test.topology)
    test.lab.start_all()
    print('http://127.0.0.1:' + str(test.port), flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
`], {cwd:path.resolve(__dirname,'..'), stdio:['pipe','pipe','pipe']});
let log = '';
fixture.stderr.on('data', d => log += d);
// Returning closes the clicked page. Observe closure, then check the parent pane below.
async function returnToWorkspace(child) {
  const closed=child.waitForEvent('close');
  await child.locator('#pane-return').click({noWaitAfter:true}).catch(error=>{
    if (!child.isClosed() || !error.message.includes('Target page, context or browser has been closed')) throw error;
  });
  await closed;
}
(async()=>{
  let browser;
  try {
    const base=await new Promise((resolve,reject)=>{
      let output='';fixture.stdout.on('data',d=>{output+=d;if(output.includes('\n'))resolve(output.trim());});
      fixture.once('exit',()=>reject(Error(log)));
    });
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],sockets=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('websocket',ws=>sockets.push(ws.url()));
    const settings={endpoint:'http://model:11434/v1',model:'test',context_window:32768,turn_timeout:900};
    let sessions=0,turns=0;
    await page.route('**/api/agent/*',route=>{
      const action=route.request().url().split('/').pop();
      const result=action==='info'?{enabled:true,providers:[settings.endpoint]}:
        action==='session'?(sessions++,{token:'fixture',settings}):
        action==='turn'?(turns++,{status:'running'}):
        {status:'running',messages:[{role:'assistant',text:'Exercise in progress.'}],settings,proposals:[],approvals:[],history:[],auth:{status:'unknown'},phase:'Thinking'};
      return route.fulfill({json:result});
    });
    await page.goto(base);await page.waitForFunction(()=>loaded && window.weblabPopouts);
    await page.evaluate(()=>openConsole('r1'));
    await page.waitForFunction(()=>consoleSessions.get('r1')?.lockReady);
    await page.locator('#lock-console').click();
    await page.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    await page.evaluate(()=>{window.savedSession=consoleSessions.get('r1');window.savedSocket=savedSession.socket;window.savedTerminal=savedSession.terminal;window.savedOpen=window.open;window.open=()=>null;});
    await page.locator('#consoles-popout').click();assert.ok(await page.locator('#console-panel').isVisible());
    await page.evaluate(()=>window.open=window.savedOpen);
    const pop=async selector=>{
      const promise=page.waitForEvent('popup');await page.locator(selector).click();const child=await promise;
      child.on('pageerror',e=>errors.push(e.message));await child.locator('.browser-detached').waitFor();return child;
    };
    const consoles=await pop('#consoles-popout');
    assert.equal(await page.locator('#console-panel').count(),0);
    assert.equal(await consoles.evaluate(()=>sessionStorage.length),0);
    await consoles.locator('.xterm-helper-textarea').focus();await consoles.keyboard.insertText('POP_TEST');await consoles.keyboard.press('Enter');
    await page.waitForFunction(()=>{const b=savedTerminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString()).join('\n').includes('POP_TEST');});
    assert.ok(await page.evaluate(()=>savedSocket===savedSession.socket && savedTerminal===savedSession.terminal && savedSession.lockMine));
    // Selection-copy notices must disappear in the child with the parent toast.
    await page.evaluate(()=>{
      navigator.clipboard.writeText=async()=>{};
      clipboardEnabled=true;clipboardSource='system';savedTerminal.selectAll();
    });
    await consoles.waitForFunction(()=>document.getElementById('pane-notice').textContent==='Copied to system clipboard');
    await consoles.locator('#pane-notice').waitFor({state:'hidden',timeout:6500});
    assert.ok(await page.locator('#toast').isHidden());
    await page.evaluate(()=>{clipboardEnabled=false;savedTerminal.clearSelection();});
    await consoles.setViewportSize({width:900,height:650});
    await consoles.locator('#console-tab').click(); // delegated console key control
    await consoles.locator('#pane-fullscreen').click();await consoles.waitForFunction(()=>!!document.fullscreenElement);
    await consoles.locator('#pane-fullscreen').click();
    // The click intentionally closes its own page; verify restoration in the parent.
    await returnToWorkspace(consoles);await page.locator('#console-panel').waitFor();
    assert.ok(await page.evaluate(()=>savedSocket===savedSession.socket && savedSession.lockMine));
    const closing=await pop('#consoles-popout');await closing.close();await page.locator('#console-panel').waitFor();
    assert.equal(sockets.length,1,'Moving/returning a terminal must not create another WebSocket');
    // Each floated console has its own live pop-out, not a cloned inert button.
    await page.evaluate(()=>floatConsole('r1'));
    const checkResizeEdge=async()=>{
      const grip=await page.locator('#console-resize-r1').boundingBox();
      const terminal=await page.locator('#console-window-r1 .console-terminal').boundingBox();
      const viewport=await page.locator('#console-window-r1 .xterm-viewport').boundingBox();
      const panel=await page.locator('#console-window-r1').boundingBox();
      assert.ok(grip.y>=terminal.y+terminal.height,'Resize grip must sit below terminal text');
      assert.ok(grip.y>=viewport.y+viewport.height,'Resize grip must not cover the scrollbar');
      assert.ok(panel.x+panel.width-terminal.x-terminal.width<=4,'No full-height gutter beside the scrollbar');
    };
    await checkResizeEdge();
    const individual=await pop('#consoles-popout-r1');
    assert.match(await individual.title(),/r1 console/);
    assert.equal(await individual.locator('#console-window-r1').count(),1);
    assert.equal(await individual.locator('#expand-console-r1').isVisible(),false);
    await individual.waitForFunction(()=>{
      const panel=document.querySelector('.console-window'),screen=panel.querySelector('.xterm-screen');
      const rows=window.opener.savedTerminal.rows;
      const bottomGap=panel.getBoundingClientRect().bottom-screen.getBoundingClientRect().bottom;
      return bottomGap>=0 && bottomGap<screen.getBoundingClientRect().height/rows+6;
    });
    await individual.screenshot({path:'/tmp/weblab-console-popout-spacing.png'});
    await individual.locator('.xterm-helper-textarea').focus();
    await individual.keyboard.insertText('INDIVIDUAL_TEST');await individual.keyboard.press('Enter');
    await page.waitForFunction(()=>{const b=savedTerminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString()).join('\n').includes('INDIVIDUAL_TEST');});
    await individual.locator('#console-tab-r1').click();
    await page.evaluate(()=>activateConsole('r1'));
    assert.equal(await page.locator('#console-window-r1').count(),0,'Selecting the tab must retain the browser pop-out');
    await returnToWorkspace(individual);await page.locator('#console-window-r1').waitFor();
    await checkResizeEdge();
    const individualClose=await pop('#consoles-popout-r1');
    await individualClose.close();await page.locator('#console-window-r1').waitFor();
    const individualDock=await pop('#consoles-popout-r1');
    const dockClosed=individualDock.waitForEvent('close');
    await page.evaluate(()=>dockConsole('r1'));
    await dockClosed;
    assert.ok(individualDock.isClosed());
    assert.ok(await page.evaluate(()=>savedTerminal.element.ownerDocument===document && savedSocket===savedSession.socket && savedTerminal===savedSession.terminal && savedSession.lockMine));
    assert.equal(sockets.length,1,'Individual pop-outs must keep the existing socket');
    await page.evaluate(()=>floatConsole('r1'));
    const individualDispose=await pop('#consoles-popout-r1');
    const disposedClosed=individualDispose.waitForEvent('close');
    await page.evaluate(()=>closeConsole('r1'));
    await disposedClosed;
    assert.ok(individualDispose.isClosed());
    assert.equal(await page.locator('#console-window-r1').count(),0);
    await page.locator('#float-topology').click();
    const map=await pop('#topology-popout');
    await map.locator('#zoom-in').click();assert.notEqual(await map.locator('#zoom-reset').textContent(),'100%');
    await map.locator('#zoom-fit').click();
    const original=await page.evaluate(()=>({x:topology.nodes[0].x,y:topology.nodes[0].y}));
    const nodeBox=await map.locator('.map-node[data-id=r1]').boundingBox();
    await map.mouse.move(nodeBox.x+nodeBox.width/2,nodeBox.y+nodeBox.height/2);
    await map.mouse.down();await map.mouse.move(nodeBox.x+nodeBox.width/2+50,nodeBox.y+nodeBox.height/2+40);
    await map.keyboard.press('Escape');await map.mouse.up();
    assert.deepEqual(await page.evaluate(()=>({x:topology.nodes[0].x,y:topology.nodes[0].y})),original);
    await page.evaluate(()=>{document.documentElement.dataset.theme='light';window.dispatchEvent(new Event('weblab-theme-change'));});
    assert.equal(await map.locator('html').getAttribute('data-theme'),'light');
    await map.screenshot({path:'/tmp/weblab-map-popout.png'});
    await returnToWorkspace(map);await page.locator('#topology-panel').waitFor();
    await page.locator('#open-agent').click();await page.waitForFunction(()=>$('agent-messages').textContent.includes('Exercise in progress'));
    await page.locator('#agent-chat-tab').click();
    await page.locator('#agent-prompt').fill('Unsent draft');
    const beforeSessions=sessions;
    const agent=await pop('#agent-popout');
    assert.equal(await agent.locator('#agent-prompt').inputValue(),'Unsent draft');
    await agent.locator('#agent-settings-tab').click();await agent.locator('#agent-chat-tab').click();
    assert.equal(await agent.evaluate(()=>sessionStorage.length),0);
    await returnToWorkspace(agent);await page.locator('#agent-panel').waitFor();
    assert.equal(sessions,beforeSessions);assert.equal(turns,0);
    assert.equal(await page.locator('#agent-prompt').inputValue(),'Unsent draft');
    // Close children on parent reload; never leave a detached authenticated pane.
    const last=await pop('#agent-popout');await page.reload();await page.waitForFunction(()=>loaded);
    assert.ok(last.isClosed());
    await page.route('**/api/state',async route=>{
      const result=await (await route.fetch()).json();result.allow_popout=false;await route.fulfill({json:result});
    });
    await page.evaluate(()=>refresh());
    assert.equal(await page.locator('.popout-button:visible').count(),0);
    const pageCount=page.context().pages().length;
    await page.evaluate(()=>{ $('consoles-popout').click(); $('instructions-popout').click(); $('agent-popout').click(); });
    await page.waitForTimeout(150);assert.equal(page.context().pages().length,pageCount);
    assert.deepEqual(errors,[]);
    console.log('PASS live pane pop-outs: console input/socket/lock continuity, return/close, map controls/theme, Agent draft/session continuity and parent reload');
  } finally {
    if(browser)await browser.close();const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
  }
})().catch(e=>{console.error(e,log);process.exitCode=1;});
