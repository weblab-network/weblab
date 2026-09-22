// Disposable lab: saved ZIP/JSON UI, fullscreen and shared scrollbar styling.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fixture = spawn('python3', ['-u','-c', `
import sys
sys.path.insert(0,'tests')
from test_lab import LabTests
import lab_backup
fixture=LabTests(); fixture.setUp()
try:
    node=fixture.lab.topology['nodes'][0]
    (fixture.lab.node_dir(node['id']) / ('nvram_%05d' % node['iol_id'])).write_bytes(b'saved config\\x00\\xff')
    print('http://127.0.0.1:'+str(fixture.port),flush=True)
    sys.stdin.readline()
finally:
    lab_backup.expire(fixture.lab,all_files=True)
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
    await page.locator('#fullscreen').click();
    await page.waitForFunction(()=>document.fullscreenElement===document.documentElement);
    assert.equal(await page.locator('#fullscreen').getAttribute('aria-label'),'Exit fullscreen');
    await page.locator('#fullscreen').click();
    await page.waitForFunction(()=>!document.fullscreenElement);
    await page.locator('#fullscreen').click();
    await page.evaluate(()=>document.exitFullscreen());
    await page.waitForFunction(()=>$('fullscreen').getAttribute('aria-pressed')==='false');
    assert.equal(await page.evaluate(()=>JSON.stringify(topology)),initial);

    await page.request.post(url+'/api/nodes/r1/start',{data:{}});
    await page.evaluate(async()=>{accept(await api('/api/state'));openConsole('r1');});
    await page.waitForFunction(()=>consoleSessions.get('r1')?.status==='Connected');
    const styles=await page.evaluate(()=>['.canvas-scroll','.palette','.inspector','.xterm-viewport','.console-tabs','#log-content'].map(selector=>{
      const style=getComputedStyle(document.querySelector(selector));return [style.scrollbarWidth,style.scrollbarColor];
    }));
    assert.ok(styles.every(style=>JSON.stringify(style)===JSON.stringify(styles[0])),JSON.stringify(styles));
    await page.locator('#export').click();
    assert.equal(await page.locator('#export-zip').isDisabled(),true);
    assert.equal(await page.locator('#export-json').isDisabled(),false);
    const jsonDownload=page.waitForEvent('download');await page.locator('#export-json').click();
    const jsonFile=await jsonDownload;assert.match(jsonFile.suggestedFilename(),/\.json$/);
    await page.request.post(url+'/api/lab/stop',{data:{}});
    await page.evaluate(async()=>accept(await api('/api/state')));

    await page.locator('#export').click();
    assert.equal(await page.locator('#export-logs').isChecked(),false);
    await page.locator('#export-logs').check();
    const exportRequest=page.waitForRequest(r=>r.url()===url+'/api/export' && r.method()==='POST');
    const zipDownload=page.waitForEvent('download');await page.locator('#export-zip').click();
    assert.deepEqual((await exportRequest).postDataJSON(),{include_logs:true,compact_veos:true});
    const zipFile=await zipDownload;assert.match(zipFile.suggestedFilename(),/\.zip$/);
    const zipPath=await zipFile.path();assert.ok(zipPath);
    await page.waitForFunction(()=>!busy);
    await page.evaluate(()=>edit(()=>topology.name='Changed lab'));
    await page.locator('#import-file').setInputFiles({name:'backup.zip',mimeType:'application/zip',buffer:require('node:fs').readFileSync(zipPath)});
    await page.locator('#cancel-import').click();
    assert.equal(await page.locator('#lab-name').inputValue(),'Changed lab');
    await page.locator('#import-file').setInputFiles({name:'backup.zip',mimeType:'application/zip',buffer:require('node:fs').readFileSync(zipPath)});
    await page.locator('#confirm-import').click();
    await page.waitForFunction(()=>!busy && !$('import-dialog').open);
    assert.equal(await page.evaluate(()=>JSON.stringify(topology)),initial);
    assert.equal(await page.evaluate(()=>consoleSessions.size),0);
    await page.locator('#import-file').setInputFiles({name:'bad.zip',mimeType:'application/zip',buffer:Buffer.from('invalid archive')});
    await page.locator('#confirm-import').click();await page.waitForFunction(()=>!busy);
    assert.match(await page.locator('#import-message').textContent(),/Invalid lab backup/);
    assert.equal(await page.evaluate(()=>JSON.stringify(topology)),initial);
    await page.locator('#cancel-import').click();
    const json=JSON.parse(initial);json.name='JSON imported';
    await page.locator('#import-file').setInputFiles({name:'lab.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(json))});
    await page.waitForFunction(()=>!busy && topology.name==='JSON imported');
    await page.setViewportSize({width:390,height:740});
    const dockedToolbar=page.locator('.canvas-toolbar');
    await dockedToolbar.hover();await page.mouse.wheel(0,200);
    await page.waitForFunction(()=>document.querySelector('.canvas-toolbar').scrollLeft>0);

    // Touch viewport checks are emulation, not a physical Android device.
    const mobile=await browser.newPage({viewport:{width:360,height:800},isMobile:true,hasTouch:true});
    mobile.on('pageerror',e=>errors.push(e.message));
    await mobile.goto(url);await mobile.waitForFunction(()=>loaded);
    await mobile.locator('#fullscreen').tap();
    await mobile.waitForFunction(()=>!!document.fullscreenElement);
    await mobile.locator('#toggle-palette').tap();
    assert.equal(await mobile.locator('#device-library').isVisible(),true);
    await mobile.locator('#close-palette').tap();
    await mobile.locator('#fullscreen').tap();
    await mobile.waitForFunction(()=>!document.fullscreenElement);
    for (const width of [320,360,412,800]) {
      await mobile.setViewportSize({width,height:900});
      assert.equal(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`Page fits ${width}px`);
      for (const id of ['fullscreen','toggle-inspector','export','import']) {
        // Narrow toolbars now scroll horizontally instead of wrapping.
        await mobile.locator('#'+id).scrollIntoViewIfNeeded();
        const box=await mobile.locator('#'+id).boundingBox();
        assert.ok(box && box.x>=0 && box.x+box.width<=width,`${id} accessible at ${width}px: ${JSON.stringify(box)}`);
      }
    }
    await mobile.evaluate(()=>{Object.defineProperty(document,'fullscreenEnabled',{value:false,configurable:true});updateFullscreen();});
    assert.equal(await mobile.locator('#fullscreen').isDisabled(),true);
    assert.match(await mobile.locator('#fullscreen').getAttribute('title'),/unavailable/);
    await mobile.close();
    assert.deepEqual(errors,[]);
    console.log('PASS: ZIP/JSON import/export, saved-state controls, fullscreen, mobile layout and matching scrollbars');
  } finally {
    if(browser)await browser.close();fixture.stdin.end('\n');await once(fixture,'exit');
  }
})().catch(error=>{console.error(error);console.error(log);process.exitCode=1;});
