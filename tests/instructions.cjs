// Local document rendering + window integration against disposable echo nodes.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/instructions.cjs
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
    test.lab.start_all()
    print('http://127.0.0.1:' + str(test.port), flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
`], {cwd:path.resolve(__dirname,'..'), stdio:['pipe','pipe','pipe']});
let log = '';
fixture.stderr.on('data', d => log += d);
(async () => {
  let browser;
  try {
    const base = await new Promise((resolve,reject) => {
      let output = '';
      fixture.stdout.on('data', d => {output += d;if(output.includes('\n'))resolve(output.trim());});
      fixture.once('exit', () => reject(new Error(log)));
    });
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    await page.addInitScript(()=>localStorage.setItem('weblab.theme.v1','light'));
    const errors = [], writes = [], external = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => {
      if(r.url().startsWith(base+'/api/') && r.method() !== 'GET') writes.push(r.url());
      if(!r.url().startsWith(base)) external.push(r.url());
    });
    await page.goto(base);await page.waitForFunction(()=>loaded);
    await page.evaluate(()=>{window.beforeTopology=JSON.stringify(topology);openConsole('r1');});
    await page.waitForFunction(()=>consoleSessions.get('r1')?.status === 'Connected');
    await page.evaluate(()=>{window.beforeSocket=consoleSessions.get('r1').socket;window.beforeTerminal=consoleSessions.get('r1').terminal;});
    const chooserPromise=page.waitForEvent('filechooser');
    await page.locator('#open-instructions').click();
    await (await chooserPromise).setFiles(path.resolve(__dirname,'../examples/ospf-practice.md'));
    const panel=page.locator('#instructions-panel'), body=page.locator('#instructions-body');
    await panel.waitFor({state:'visible'});
    const gripBox=await page.locator('#instructions-resize').boundingBox();
    assert.ok((await body.boundingBox()).y+(await body.boundingBox()).height<=gripBox.y,'Instructions scrollbar ends above the resize grip');
    assert.ok(await body.locator('h1').count());assert.ok(await body.locator('table').count());
    await page.locator('#instructions-move').focus();
    const initial=await panel.boundingBox();await page.keyboard.press('ArrowRight');
    assert.equal((await panel.boundingBox()).x,initial.x+10);
    await page.locator('#instructions-resize').focus();await page.keyboard.press('ArrowDown');
    assert.equal((await panel.boundingBox()).height,initial.height+10);
    await page.locator('#instructions-larger').click();
    assert.equal(await body.evaluate(e=>getComputedStyle(e).fontSize),'16px');
    await page.locator('#instructions-maximize').click();
    assert.equal(await panel.evaluate(e=>e.classList.contains('maximized')),true);
    await page.locator('#instructions-maximize').click();
    await page.locator('#instructions-toolbar [data-arrange-windows]').click();
    const boxes=await page.locator('#console-windows>.floating:visible').evaluateAll(els=>els.map(el=>{
      const r=el.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom};
    }));
    assert.equal(boxes.length,3);
    for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++) {
      const a=boxes[i],b=boxes[j];assert.ok(a.right<=b.x+1||b.right<=a.x+1||a.bottom<=b.y+1||b.bottom<=a.y+1);
    }
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').socket===beforeSocket && consoleSessions.get('r1').terminal===beforeTerminal),true);
    assert.equal(await page.evaluate(()=>JSON.stringify(topology)===beforeTopology),true);
    await page.screenshot({path:'/tmp/netlab-instructions-tiled.png'});
    await page.evaluate(()=>document.documentElement.requestFullscreen());
    assert.equal(await panel.isVisible(),true);
    await page.evaluate(()=>document.exitFullscreen());
    await page.locator('#instructions-hide').click();assert.equal(await panel.isVisible(),false);
    await page.locator('#topology-window-toolbar [data-show-instructions]').click();assert.equal(await panel.isVisible(),true);
    const upload=async(name,text)=>{
      await page.locator('#instructions-file').setInputFiles({name,mimeType:'text/plain',buffer:Buffer.from(text)});
      await page.waitForFunction(name=>$('instructions-title').textContent===name,name);
    };
    const hostile = '# Checklist\n\n[Jump](#verification)\n\n- [x] Address interfaces\n- [ ] Verify OSPF\n\n'+
      '<script>window.docExecuted=true</script>\n\n<img src="/api/export" onerror="window.docExecuted=true">\n\n'+
      '[bad](javascript:alert(1)) [encoded](jav&#x61;script:alert(1)) [local](/api/export)\n\n'+
      '![remote](https://example.invalid/image.png)\n\n[Official](https://example.com)\n\n'+
      '```ios\nshow ip ospf neighbor\n<script>unsafe()</script>\n```\n\n'+
      Array(30).fill('Read the configuration.\n\n').join('')+'## Verification\n\nDone.\n';
    await upload('exercise.md',hostile);
    assert.equal(await body.locator('script,img,iframe,object,style').count(),0);
    assert.equal(await body.locator('a[href^="javascript:"]').count(),0);
    assert.equal(await page.evaluate(()=>window.docExecuted),undefined);
    assert.equal(await body.locator('input[type=checkbox]').count(),2);
    assert.equal(await body.locator('a[href="/api/export"]').count(),0);
    assert.equal(await body.locator('a[href="https://example.com"]').getAttribute('rel'),'noopener noreferrer');
    await body.locator('a[data-anchor-link]').click();
    assert.ok(await body.evaluate(e=>e.scrollTop)>100);
    assert.equal(new URL(page.url()).hash,'');
    // Blocked popups preserve the pane. Detached readers preserve content/position,
    // have no API access, and never inherit Agent capabilities in tab storage.
    await page.evaluate(()=>{window.savedOpen=window.open;window.open=()=>null;});
    await page.locator('#instructions-popout').click();
    assert.ok(await panel.isVisible());
    await page.evaluate(()=>{window.open=window.savedOpen;sessionStorage.setItem('weblab.agent.session.v1','fixture-capability');});
    await body.evaluate(e=>e.scrollTop=180);
    const popupEvent=page.waitForEvent('popup');await page.locator('#instructions-popout').click();
    const popup=await popupEvent;popup.on('pageerror',e=>errors.push(e.message));
    const reader=popup.locator('#reader-body');
    // A popup event does not guarantee that the reader DOM is ready yet.
    await popup.locator('#reader-title').waitFor();
    await popup.waitForFunction(()=>document.querySelector('#reader-title').textContent==='exercise.md');
    await panel.waitFor({state:'hidden'});
    assert.equal(await reader.innerHTML(),await body.innerHTML());
    assert.equal(await reader.evaluate(e=>e.scrollTop),180);
    assert.equal(await popup.evaluate(()=>sessionStorage.length),0);
    await popup.evaluate(()=>window.postMessage({type:'weblab-instructions-state',html:'FORGED',name:'forged'},location.origin));
    await popup.waitForTimeout(100);
    assert.equal(await reader.innerHTML(),await body.innerHTML());
    assert.equal(await reader.locator('script,img,iframe,object,style').count(),0);
    await popup.locator('#reader-larger').click();
    await popup.waitForFunction(()=>document.querySelector('#reader-font').textContent==='17px');
    await reader.evaluate(e=>e.scrollTop=240);
    await popup.waitForTimeout(120);
    await popup.reload();
    await popup.waitForFunction(()=>document.querySelector('#reader-body').scrollTop===240);
    await popup.locator('#reader-fullscreen').click();
    await popup.waitForFunction(()=>!!document.fullscreenElement);
    await popup.locator('#reader-fullscreen').click();
    await popup.locator('#reader-return').click();
    await panel.waitFor({state:'visible'});
    assert.equal(await body.evaluate(e=>e.scrollTop),240);
    assert.equal(await body.evaluate(e=>getComputedStyle(e).fontSize),'17px');
    await page.locator('#instructions-smaller').click();
    const secondEvent=page.waitForEvent('popup');await page.locator('#instructions-popout').click();
    const second=await secondEvent;
    await second.locator('#reader-title').waitFor();
    await second.waitForFunction(()=>document.querySelector('#reader-title').textContent==='exercise.md');
    await second.close();await panel.waitFor({state:'visible'});
    await page.locator('#instructions-fullscreen').click();
    await page.waitForFunction(()=>document.fullscreenElement?.id==='instructions-panel');
    await page.locator('#instructions-fullscreen').click();
    await page.waitForFunction(()=>!document.fullscreenElement);
    const literal='# Not a heading\n<iframe src="https://example.invalid"></iframe>\n  preserve indent';
    await upload('commands.txt',literal);
    assert.equal(await body.locator('h1,iframe').count(),0);
    assert.equal(await body.innerText(),literal);
    await page.reload();await page.waitForFunction(()=>loaded);
    assert.equal(await panel.isVisible(),true);
    assert.equal(await body.innerText(),literal);
    assert.equal(await body.evaluate(e=>getComputedStyle(e).fontSize),'16px');
    await page.locator('#instructions-file').setInputFiles({name:'too-big.md',mimeType:'text/plain',buffer:Buffer.alloc(512*1024+1,65)});
    await page.waitForFunction(()=>$('toast').textContent.includes('512 KiB'));
    assert.equal(await body.innerText(),literal,'Invalid replacement keeps the current document');
    await page.setViewportSize({width:390,height:740});
    await page.waitForFunction(()=>$('instructions-panel').getBoundingClientRect().right<=innerWidth);
    const mobile=await panel.boundingBox();assert.ok(mobile.x>=0&&mobile.x+mobile.width<=390);
    await require('./toolbar_ui.cjs').pinnedToolbar(page, '#instructions-toolbar', '#instructions-move', '#instructions-hide');
    await page.locator('#instructions-toolbar .toolbar-scroll').evaluate(el=>el.scrollLeft=el.scrollWidth);
    await page.locator('#instructions-hide').click();
    assert.equal(await panel.isVisible(),false);
    await page.locator('#open-instructions').click();assert.equal(await panel.isVisible(),true);
    await page.screenshot({path:'/tmp/netlab-instructions-phone.png'});
    assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);assert.deepEqual(external,[]);
    console.log('PASS Markdown/txt, tables/code/tasks, inert HTML/links/images, local anchors, session restore, bounds, font controls, tiled consoles, fullscreen, phone toolbar and zero lab writes');
  } finally {
    if(browser)await browser.close();
    const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
  }
})().catch(e=>{console.error(e,log);process.exitCode=1;});
