// ARM profile controls through a disposable HTTP fixture; no device launches.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fixture = spawn('python3', ['-u', '-c', `
import sys
sys.path.insert(0, 'tests')
from test_lab import LabTests
test = LabTests(); test.setUp()
try:
    test.lab.container_only = True
    test.lab.allow_untested_frr = True
    test.lab.save({'name':'ARM64 UI', 'nodes':[], 'links':[]})
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
      fixture.stdout.on('data', d => { output += d; if (output.includes('\n')) resolve(output.trim()); });
      fixture.once('exit', () => reject(Error(log)));
    });
    browser = await chromium.launch({headless:true, args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1500,height:950}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    await page.waitForFunction(() => loaded);
    assert.equal(await page.locator('#upload-image').isVisible(), false);
    assert.equal(await page.locator('[data-type="cloud"]').isDisabled(), true);
    assert.match(await page.locator('[data-type="cloud"]').getAttribute('title'), /ARM64/);
    assert.equal(await page.locator('[data-type="router"] small').innerText(), 'FRR · Layer 3');
    await page.locator('[data-type="router"]').click();
    await page.waitForFunction(() => !busy && topology.nodes.length === 1);
    assert.equal(await page.locator('#node-image').inputValue(), 'weblab-frr:10.7.1-wl1-arm64');
    assert.equal(await page.locator('#node-ethernet').inputValue(), '4');
    assert.match(await page.locator('#ethernet-help').innerText(), /Native ARM64/);
    assert.equal(await page.locator('.node-type').first().innerText(), 'FRR ROUTER');
    await page.locator('[data-type="switch"]').click();
    await page.waitForFunction(() => !busy && topology.nodes.length === 2);
    assert.equal(await page.locator('#node-image').inputValue(), 'll2s:0.2.0-ovs-rstp1-arm64');
    assert.equal(await page.locator('.node-type').last().innerText(), 'LL2S SWITCH');
    await page.locator('[data-type="pc"]').click();
    await page.waitForFunction(() => !busy && topology.nodes.length === 3);
    assert.equal(await page.locator('#node-image').inputValue(), 'alpine:latest');
    await page.reload();
    await page.waitForFunction(() => loaded);
    assert.equal(await page.locator('[data-type="cloud"]').isDisabled(), true);
    assert.equal(await page.locator('#upload-image').isVisible(), false);
    assert.deepEqual(errors, []);
    console.log('PASS: ARM64 library, native selectors, PC and unavailable controls');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end('\n');
    await once(fixture, 'exit');
  }
})().catch(error => { console.error(error, log); process.exitCode = 1; });
