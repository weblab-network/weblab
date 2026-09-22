// PLAYWRIGHT_MODULE=/path/to/playwright node tests/veos_ui.cjs
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
    assert.deepEqual(errors,[]);
    console.log('PASS Junos image selection, defaults, interface names and config-export guards');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await once(fixture, 'exit');
  }
})().catch(error => { console.error(error, log); process.exitCode = 1; });
