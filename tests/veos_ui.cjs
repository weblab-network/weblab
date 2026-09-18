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
from test_veos import iso_bytes
test = LabTests(); test.setUp()
try:
    test.lab.save({'name':'IOSv UI test','nodes':[],'links':[]})
    disk = test.root / 'upload.qcow2'
    subprocess.run(['qemu-img','create','-f','qcow2',str(disk),'4M'], check=True, stdout=subprocess.DEVNULL)
    iso=test.root/'aboot.iso';iso.write_bytes(iso_bytes())
    print(json.dumps({'iso':str(iso),'url':'http://127.0.0.1:' + str(test.port), 'disk':str(disk)}), flush=True)
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
    for (const name of ['cisco_vios.qcow2','vEOS64-lab-4.36.1F.qcow2','Aboot-veos-serial-8.0.2.iso']) {
      await page.locator('#upload-image').click();
      await page.locator('#image-file').setInputFiles({name, mimeType:'application/octet-stream', buffer:fs.readFileSync(name.endsWith('.iso') ? info.iso : info.disk)});
      await page.locator('#submit-image').click();
      await page.waitForFunction(() => !busy && !$('image-dialog').open);
    }
    await page.locator('.device-template[data-type="switch"]').click();
    await page.waitForFunction(() => !busy && topology.nodes.length === 1);
    assert.equal(await page.locator('#node-image').inputValue(), 'vEOS64-lab-4.36.1F.qcow2');
    assert.equal(await page.locator('#node-memory').inputValue(), '6144');
    assert.equal(await page.locator('#node-ethernet').inputValue(), '5');
    assert.match(await page.locator('#interfaces').innerText(), /Management1/);
    assert.match(await page.locator('#interfaces').innerText(), /Ethernet4/);
    assert.equal(await page.locator('#node-image option').filter({hasText:'Aboot'}).count(), 0);
    await page.locator('#node-image').selectOption('cisco_vios.qcow2');
    assert.equal(await page.locator('#node-memory').inputValue(),'1024');
    await page.locator('#node-image').selectOption('vEOS64-lab-4.36.1F.qcow2');
    assert.equal(await page.locator('#node-memory').inputValue(),'6144');
    await page.locator('#node-ethernet').selectOption('16');
    await page.locator('#apply-node').click();
    await page.waitForFunction(()=>!busy && topology.nodes[0].ethernet===16);
    await page.locator('.device-template[data-type="router"]').click();
    await page.waitForFunction(()=>!busy && topology.nodes.length===2);
    assert.equal(await page.locator('#node-image option').filter({hasText:'vEOS64'}).count(),0);
    await page.evaluate(()=>edit(()=>{topology.nodes[0].x=180;topology.nodes[1].x=450;}));
    await page.waitForFunction(()=>!busy);
    await page.locator('#link-tool').click();
    await page.locator('.map-node').nth(0).click();await page.locator('.map-node').nth(1).click();
    await page.locator('#link-a-port').selectOption('Ethernet15');await page.locator('#link-b-port').selectOption('Gi0/0');
    await page.locator('#confirm-link').click();await page.waitForFunction(()=>!busy && topology.links.length===1);
    await page.reload();await page.waitForFunction(()=>loaded);
    assert.match(await page.locator('#links').textContent(),/Ethernet15/);
    assert.equal(await page.locator('.node-type').first().innerText(),'vEOS SWITCH');
    await page.locator('#export').click();
    assert.equal(await page.locator('#export-initial').isDisabled(),true);
    assert.equal(await page.locator('#export-saved').isDisabled(),true);
    assert.equal(await page.locator('#export-zip').isDisabled(),false);
    assert.match(await page.locator('#export-message').innerText(),/Arista/);
    assert.deepEqual(errors, []);
    console.log('Arista/Aboot uploads, defaults, vendor changes, port cabling, export guards and reload passed');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await once(fixture, 'exit');
  }
})().catch(error => { console.error(error, log); process.exitCode = 1; });
