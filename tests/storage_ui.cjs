// Metadata-only disposable lab. No VM/container is started.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fixture = spawn('python3', ['-u', '-c', `
import sys
sys.path.insert(0, 'tests')
from test_lab import LabTests
fixture = LabTests(); fixture.setUp()
try:
    node = fixture.lab.node_dir('r1')
    with (node / 'test.qcow2').open('wb') as disk:
        disk.write(b'x' * 4096); disk.truncate(512 * 1024**2)
    (node / 'console.log').write_text('sample')
    fixture.lab.node_dir('old-node').joinpath('old.log').write_text('retained')
    fixture.lab.topology['nodes'][0]['name'] = '<b>R1</b>'
    print('http://127.0.0.1:' + str(fixture.port), flush=True)
    sys.stdin.readline()
finally:
    fixture.tearDown()
`], {cwd:path.resolve(__dirname, '..'), stdio:['pipe','pipe','pipe']});
let log = ''; fixture.stderr.on('data', d => log += d);
(async () => {
  let browser;
  try {
    const url = await new Promise((resolve, reject) => {
      let output = '';
      fixture.stdout.on('data', d => { output += d; if (output.includes('\n')) resolve(output.trim()); });
      fixture.once('exit', code => reject(new Error(`Fixture exited ${code}: ${log}`)));
    });
    browser = await chromium.launch({headless:true, args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(url); await page.waitForFunction(() => loaded);
    await page.locator('.header-actions [data-storage]').click();
    await page.waitForFunction(() => document.getElementById('storage-message').textContent.startsWith('Measured'));
    const text = await page.locator('#storage-content').innerText();
    assert.match(text, /<b>R1<\/b>/);
    assert.equal(await page.locator('#storage-content b').count(), 0);
    assert.match(text, /old-node \(retained\)/);
    assert.match(text, /VM disks/);
    await page.screenshot({path:'/tmp/weblab-storage-desktop.png'});
    await page.locator('#storage-close').click();
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('weblab-storage', {detail:{filesystems:[{label:'Lab data',free_bytes:32*1024**2,level:'critical'}]}})));
    assert.equal(await page.locator('.header-actions [data-storage]').innerText(), 'Storage · critical');
    await page.locator('#float-topology').click();
    await page.locator('#topology-window-toolbar [data-storage]').click();
    await page.waitForFunction(() => document.getElementById('storage-dialog').open);
    await page.keyboard.press('Escape');
    await page.setViewportSize({width:390,height:844});
    // Scrollable floating toolbar remains reachable on phones.
    await page.locator('#topology-window-toolbar [data-storage]').click();
    await page.waitForFunction(() => !document.getElementById('storage-refresh').disabled);
    const bounds = await page.locator('#storage-dialog').boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 391, JSON.stringify(bounds));
    await page.screenshot({path:'/tmp/weblab-storage-phone.png'});
    await page.route('**/api/storage', route => route.fulfill({status:503,json:{error:'Report unavailable'}}));
    await page.locator('#storage-refresh').click();
    await page.waitForFunction(() => document.getElementById('storage-message').textContent.includes('may be stale'));
    assert.equal(await page.locator('#storage-refresh').isEnabled(), true);
    assert.deepEqual(errors, []);
    console.log('Storage desktop/mobile, escaping, warnings, floating access and failure-state checks passed');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await once(fixture, 'exit');
  }
})().catch(error => { console.error(error, log); process.exitCode = 1; });
