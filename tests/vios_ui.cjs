// PLAYWRIGHT_MODULE=/path/to/playwright node tests/vios_ui.cjs
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
    for (const name of ['cisco_vios.qcow2','vios_l2.qcow2']) {
      await page.locator('#upload-image').click();
      await page.locator('#image-file').setInputFiles({name, mimeType:'application/octet-stream', buffer:fs.readFileSync(info.disk)});
      await page.locator('#submit-image').click();
      await page.waitForFunction(() => !busy && !$('image-dialog').open);
    }
    await page.locator('.device-template[data-type="router"]').click();
    await page.waitForFunction(() => !busy && topology.nodes.length === 1);
    assert.equal(await page.locator('#node-image').inputValue(), 'cisco_vios.qcow2');
    assert.equal(await page.locator('#ethernet-label').innerText(), 'Ethernet interfaces');
    await page.locator('#node-ethernet').selectOption('16');
    await page.locator('#apply-node').click();
    await page.waitForFunction(() => !busy && topology.nodes[0].ethernet === 16);
    assert.ok((await page.locator('#interfaces').innerText()).includes('Gi0/15'));
    await page.locator('.device-template[data-type="switch"]').click();
    await page.waitForFunction(() => !busy && topology.nodes.length === 2);
    await page.locator('#node-ethernet').selectOption('8');
    await page.locator('#apply-node').click();
    await page.waitForFunction(() => !busy && topology.nodes[1].ethernet === 8);
    assert.ok((await page.locator('#interfaces').innerText()).includes('Gi1/3'));
    // Move overlapping palette additions apart before exercising cable selection.
    await page.evaluate(() => edit(() => { topology.nodes[0].x = 180; topology.nodes[1].x = 450; }));
    await page.waitForFunction(() => !busy);
    await page.locator('#link-tool').click();
    await page.locator('.map-node').nth(0).click();
    await page.locator('.map-node').nth(1).click();
    await page.locator('#link-a-port').selectOption('Gi0/15');
    await page.locator('#link-b-port').selectOption('Gi1/3');
    await page.locator('#confirm-link').click();
    await page.waitForFunction(() => !busy && topology.links.length === 1);
    await page.reload();
    await page.waitForFunction(() => loaded);
    assert.match(await page.locator('#links').textContent(), /Gi0\/15/);
    assert.match(await page.locator('#links').textContent(), /Gi1\/3/);
    assert.equal(await page.locator('.node-type').first().innerText(), 'IOSv ROUTER');
    assert.deepEqual(errors, []);
    console.log('IOSv upload, settings, cabling and reload browser checks passed');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await once(fixture, 'exit');
  }
})().catch(error => { console.error(error, log); process.exitCode = 1; });
