// PLAYWRIGHT_MODULE=/path/to/playwright node tests/junos_ui.cjs
// Disposable empty QCOW2 disks; requires qemu-utils, never boots a VM.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fs = require('node:fs');
const fixture = spawn('python3', ['-u', '-c', `
import sys, subprocess, json
sys.path.insert(0, 'tests')
from test_lab import LabTests
test = LabTests(); test.setUp()
try:
    test.lab.save({'name':'IOSv UI test','nodes':[],'links':[]})
    disk = test.root / 'upload.qcow2'
    subprocess.run(['qemu-img','create','-f','qcow2',str(disk),'4M'], check=True, stdout=subprocess.DEVNULL)
    print(json.dumps({'url':'http://127.0.0.1:' + str(test.port), 'disk':str(disk)}), flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
`], {cwd:path.resolve(__dirname, '..'), stdio:['pipe','pipe','pipe']});
let log = '';
fixture.stderr.on('data', data => log += data);
(async () => {
  let browser;
  try {
    const info = await new Promise((resolve, reject) => {
      let text = '';
      fixture.stdout.on('data', data => { text += data; if (text.includes('\n')) resolve(JSON.parse(text.trim())); });
      fixture.once('exit', code => reject(new Error(`Fixture exited ${code}: ${log}`)));
    });
    browser = await chromium.launch({headless:true, args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1600,height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(info.url);
    await page.waitForFunction(() => loaded);
    for (const name of ['vJunos-switch-26.2R1.7.qcow2','vJunosEvolved-26.2R1.7-EVO.qcow2']) {
      await page.evaluate(async ({name,data}) => {
        const response = await fetch('/api/images/'+encodeURIComponent(name), {method:'POST', headers:{'Content-Type':'application/octet-stream'}, body:new Uint8Array(data)});
        if (!response.ok) throw new Error(await response.text());
        accept(await api('/api/state'));
      }, {name,data:[...fs.readFileSync(info.disk)]});
    }
    await page.locator('.device-template[data-type="switch"]').click();
    await page.waitForFunction(()=>!busy && topology.nodes.length===1);
    assert.equal(await page.locator('#node-memory').inputValue(),'5120');
    assert.equal(await page.locator('#node-ethernet').inputValue(),'5');
    assert.match(await page.locator('#interfaces').innerText(),/ge-0\/0\/3/);
    assert.equal(await page.locator('#node-image option').filter({hasText:'vJunosEvolved'}).count(),0);
    await page.locator('.device-template[data-type="router"]').click();
    await page.waitForFunction(()=>!busy && topology.nodes.length===2);
    await page.locator('#node-image').selectOption('vJunosEvolved-26.2R1.7-EVO.qcow2');
    assert.equal(await page.locator('#node-memory').inputValue(),'8192');
    assert.equal(await page.locator('#node-ethernet').inputValue(),'5');
    await page.locator('#apply-node').click();await page.waitForFunction(()=>!busy);
    assert.match(await page.locator('#interfaces').innerText(),/et-0\/0\/3/);
    assert.equal(await page.locator('#node-image option').filter({hasText:'vJunos-switch'}).count(),0);
    await page.locator('#export').click();
    assert.equal(await page.locator('#export-initial').isDisabled(),true);
    assert.equal(await page.locator('#export-saved').isDisabled(),true);
    await page.locator('#cancel-export').click();
    await page.locator('.device-template[data-type="pc"]').click();
    await page.waitForFunction(()=>!busy && topology.nodes.length===3);
    // Model running status and intercept every Stop: no VM/container is started.
    const nodes=await page.evaluate(()=>topology.nodes);
    const [sw,router,pc]=nodes;
    const running=new Set(nodes.map(n=>n.id)), stops=[];
    const responseState=async()=>{
      const data=await (await page.request.get(info.url+'/api/state')).json();
      for(const n of data.topology.nodes) data.status[n.id]={state:running.has(n.id)?'running':'stopped',error:''};
      return data;
    };
    await page.route('**/api/state',async route=>route.fulfill({json:await responseState()}));
    await page.route('**/stop',async route=>{
      const pathname=new URL(route.request().url()).pathname;
      stops.push(pathname);
      if(pathname==='/api/lab/stop') running.clear();
      else running.delete(pathname.split('/')[3]);
      await route.fulfill({json:await responseState()});
    });
    await page.evaluate(async()=>accept(await api('/api/state')));
    await page.locator('#stop-node').click(); // Alpine needs no extra confirmation.
    await page.waitForFunction(()=>!busy);
    assert.deepEqual(stops,[`/api/nodes/${pc.id}/stop`]);
    await page.evaluate(id=>selectNode(id,false),sw.id);
    await page.locator('#stop-node').click();
    assert.equal(await page.locator('#stop-dialog').isVisible(),true);
    assert.equal(await page.locator('#stop-junos-names').innerText(),sw.name);
    assert.match(await page.locator('#stop-dialog').innerText(),/request system power-off/);
    assert.equal(await page.locator('#cancel-stop').evaluate(el=>el===document.activeElement),true);
    await page.locator('#cancel-stop').click();
    assert.equal(stops.length,1);
    await page.locator('#stop-node').click();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#stop-dialog').isVisible(),false);
    assert.equal(stops.length,1);
    await page.locator('#stop-node').click();
    // Selection changes cannot retarget a pending single-node stop.
    await page.evaluate(id=>selectNode(id,false),router.id);
    await page.locator('#confirm-stop').click();
    await page.waitForFunction(()=>!busy);
    assert.deepEqual(stops,[`/api/nodes/${pc.id}/stop`,`/api/nodes/${sw.id}/stop`]);
    running.add(sw.id);
    await page.evaluate(async()=>accept(await api('/api/state')));
    await page.setViewportSize({width:390,height:844});
    await page.locator('#stop-all').click();
    const names=await page.locator('#stop-junos-names').innerText();
    assert.ok(names.includes(sw.name) && names.includes(router.name));
    assert.ok(!names.includes(pc.name));
    const box=await page.locator('#stop-dialog').boundingBox();
    assert.ok(box.x>=0 && box.x+box.width<=390);
    await page.locator('#cancel-stop').click();
    assert.equal(stops.length,2);
    await page.locator('#stop-all').click();
    await page.locator('#confirm-stop').click();
    await page.waitForFunction(()=>!busy);
    assert.equal(stops[2],'/api/lab/stop');
    // A stopped Junos device must not prompt when stopping other devices.
    running.add(pc.id);
    await page.evaluate(async()=>accept(await api('/api/state')));
    await page.locator('#stop-all').click();
    await page.waitForFunction(()=>!busy);
    assert.equal(stops[3],'/api/lab/stop');
    assert.equal(await page.locator('#stop-dialog').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log('PASS Junos image selection, defaults, interface names, config-export guards and Junos Stop confirmations');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await once(fixture, 'exit');
  }
})().catch(error => { console.error(error, log); process.exitCode = 1; });
