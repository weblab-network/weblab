// PLAYWRIGHT_MODULE=/path/to/playwright node tests/panels.cjs
// Disposable fake-node lab: desktop preferences, portrait drawers and touch controls.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fixture = spawn('python3', ['-u','-c', `
import sys
sys.path.insert(0,'tests')
from test_lab import LabTests
fixture=LabTests(); fixture.setUp()
try:
    fixture.topology['nodes'][0].update(x=180,y=180)
    fixture.topology['nodes'][1].update(x=450,y=340)
    fixture.lab.save(fixture.topology)
    print('http://127.0.0.1:'+str(fixture.port),flush=True)
    sys.stdin.readline()
finally:
    fixture.tearDown()
`], {cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log=''; fixture.stderr.on('data',d=>log+=d);
(async()=>{
  let browser;
  try {
    const url=await new Promise((resolve,reject)=>{
      let output='';fixture.stdout.on('data',d=>{output+=d;if(output.includes('\n'))resolve(output.trim());});
      fixture.once('exit',code=>reject(new Error(`Fixture exited ${code}: ${log}`)));
    });
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(url);await page.waitForFunction(()=>loaded);
    const initial=await page.evaluate(()=>JSON.stringify(topology));
    const width=async()=> (await page.locator('#canvas-scroll').boundingBox()).width;
    const originalWidth=await width();
    await page.locator('#toggle-palette').click();
    assert.equal(await page.locator('#device-library').isVisible(),false);
    assert.ok(await width()>originalWidth+200);
    await page.locator('#toggle-inspector').click();
    assert.equal(await page.locator('#inspector').isVisible(),false);
    assert.ok(await width()>originalWidth+450);
    await page.reload();await page.waitForFunction(()=>loaded);
    assert.equal(await page.locator('#device-library').isVisible(),false);
    assert.equal(await page.locator('#inspector').isVisible(),false);
    await page.locator('#toggle-inspector').click();
    await page.setViewportSize({width:800,height:1100});
    await page.waitForFunction(()=>$("workspace").classList.contains("panels-compact") && !compactPanel);
    assert.equal(await page.locator('#inspector').isVisible(),false);
    assert.ok(await width()>=798,'Portrait canvas fills available width');
    await page.locator('#toggle-palette').focus();await page.keyboard.press('Enter');
    assert.equal(await page.locator('#close-palette').evaluate(el=>el===document.activeElement),true);
    assert.equal(await page.locator('#device-library').isVisible(),true);
    await page.locator('#toggle-inspector').click();
    assert.equal(await page.locator('#device-library').isVisible(),false);
    assert.equal(await page.locator('#inspector').isVisible(),true,'Inspector can open even without a selected node');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#inspector').isVisible(),false);
    await page.setViewportSize({width:1440,height:1000});
    await page.waitForFunction(()=>!$("workspace").classList.contains("panels-compact"));
    assert.equal(await page.locator('#device-library').isVisible(),false,'Restore desktop collapse preference');
    assert.equal(await page.locator('#inspector').isVisible(),true,'Restore desktop expanded preference');
    await page.locator('#toggle-palette').click();
    assert.equal(await page.evaluate(()=>JSON.stringify(topology)),initial);

    // Sidebar resizing must preserve an open console's session/history.
    await page.request.post(url+'/api/nodes/r1/start',{data:{}});
    await page.evaluate(async()=>{accept(await api('/api/state'));openConsole('r1');});
    await page.waitForFunction(()=>consoleSessions.get('r1')?.status==='Connected');
    await page.evaluate(()=>{window.savedConsole=consoleSessions.get('r1');savedConsole.terminal.write('PANEL HISTORY');});
    await page.locator('#toggle-palette').click();await page.locator('#toggle-inspector').click();
    assert.equal(await page.evaluate(()=>savedConsole===consoleSessions.get('r1') && savedConsole.socket.readyState===1),true);
    await page.request.post(url+'/api/nodes/r1/stop',{data:{}});
    await page.close();

    const touch=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    const mobile=await touch.newPage();mobile.on('pageerror',e=>errors.push(e.message));
    await mobile.goto(url);await mobile.waitForFunction(()=>loaded);
    assert.equal(await mobile.locator('#device-library').isVisible(),false);
    assert.equal(await mobile.locator('#inspector').isVisible(),false);
    assert.ok(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
    await mobile.locator('#toggle-palette').tap();
    assert.equal(await mobile.locator('#upload-image').isVisible(),true,'Full library available on touch');
    await mobile.locator('#upload-image').tap();
    await mobile.keyboard.press('Escape');
    assert.equal(await mobile.locator('#image-dialog').isVisible(),false);
    assert.equal(await mobile.locator('#device-library').isVisible(),true,'Escape dismisses a modal before its underlying drawer');
    await mobile.locator('.device-template[data-type="pc"]').tap();
    await mobile.waitForFunction(()=>!busy && topology.nodes.length===3 && compactPanel==='inspector');
    assert.equal(await mobile.locator('#device-library').isVisible(),false);
    await mobile.locator('#node-name').fill('Touch PC');
    await mobile.locator('#close-inspector').tap();
    await mobile.locator('#toggle-inspector').tap();
    assert.equal(await mobile.locator('#node-name').inputValue(),'Touch PC','Hiding preserves edits');
    await mobile.locator('#apply-node').tap();await mobile.waitForFunction(()=>!busy);
    await mobile.locator('#panel-backdrop').tap({position:{x:20,y:30}});
    assert.equal(await mobile.locator('#inspector').isVisible(),false);
    await mobile.screenshot({path:'/tmp/panels-mobile-closed.png'});
    await mobile.locator('#toggle-palette').tap();
    await mobile.screenshot({path:'/tmp/panels-mobile-open.png'});
    await mobile.locator('#close-palette').tap();
    await mobile.locator('#zoom-fit').tap();
    await mobile.locator('.map-node[data-id="r1"]').tap();
    assert.equal(await mobile.locator('#inspector').isVisible(),true,'Selecting a stopped node opens its settings');
    await touch.close();
    const dragPage=await browser.newPage({viewport:{width:900,height:800}});
    dragPage.on('pageerror',e=>errors.push(e.message));
    await dragPage.goto(url);await dragPage.waitForFunction(()=>loaded);
    const count=await dragPage.evaluate(()=>topology.nodes.length);
    await dragPage.locator('#toggle-palette').click();
    const source=await dragPage.locator('.device-template[data-type="pc"]').boundingBox();
    const target=await dragPage.locator('#canvas-scroll').boundingBox();
    await dragPage.mouse.move(source.x+source.width/2,source.y+source.height/2);
    await dragPage.mouse.down();
    await dragPage.mouse.move(source.x+source.width/2+25,source.y+source.height/2,{steps:4});
    await dragPage.waitForFunction(()=>!compactPanel);
    await dragPage.mouse.move(target.x+600,target.y+350,{steps:6});
    await dragPage.mouse.move(target.x+601,target.y+350);
    await dragPage.mouse.up();
    await dragPage.waitForFunction(count=>!busy && topology.nodes.length===count+1,count);
    assert.equal(await dragPage.locator('#device-library').isVisible(),false,'Dragging dismisses the overlay to expose the canvas');
    await dragPage.close();
    assert.deepEqual(errors,[]);
    console.log('PASS: panel collapse/persistence, responsive restoration, keyboard dismissal, touch library/settings and console continuity.');
  } finally {
    if(browser)await browser.close();
    fixture.stdin.end('\n');if(fixture.exitCode===null)await once(fixture,'exit');
  }
})().catch(error=>{console.error(error,log);process.exitCode=1;});
