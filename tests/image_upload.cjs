// PLAYWRIGHT_MODULE=/path/to/playwright node tests/image_upload.cjs
// Uses a disposable server and synthetic ELF data; never executes uploaded files.
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
    print('http://127.0.0.1:' + str(test.port), flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
`], {cwd:path.resolve(__dirname, '..'), stdio:['pipe', 'pipe', 'pipe']});
let fixtureLog = '';
fixture.stderr.on('data', data => { fixtureLog += data; });

(async () => {
  let browser;
  try {
    const base = await new Promise((resolve, reject) => {
      let output = '';
      fixture.stdout.on('data', data => { output += data; if (output.includes('\n')) resolve(output.trim()); });
      fixture.once('exit', code => reject(new Error(`Fixture exited ${code}: ${fixtureLog}`)));
    });
    browser = await chromium.launch({headless:true, args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1440, height:1000}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    await page.waitForFunction(() => loaded);
    const before = (await (await page.request.get(base + '/api/state')).json()).topology;
    const buffer = Buffer.alloc(2 * 1024 * 1024);
    buffer.write('\x7fELF', 0, 'binary');
    const file = {name:'uploaded-l2.bin', mimeType:'application/octet-stream', buffer};
    await page.locator('#upload-image').click();
    await page.locator('#image-file').setInputFiles(file);
    await page.locator('#submit-image').click();
    await page.waitForFunction(() => !busy && !$('image-dialog').open);
    assert.ok((await page.locator('#toast').innerText()).includes('ready to use'));
    let state = await (await page.request.get(base + '/api/state')).json();
    assert.deepEqual(state.topology, before, 'Uploading leaves topology untouched');
    assert.ok(state.images.some(i => i.name === file.name && i.type === 'switch'));

    // Existing names and non-ELF data give recoverable errors in the dialog.
    await page.locator('#upload-image').click();
    await page.locator('#image-file').setInputFiles(file);
    await page.locator('#submit-image').click();
    await page.waitForFunction(() => !busy && $('image-message').textContent.includes('already exists'));
    assert.equal(await page.locator('#image-dialog').isVisible(), true);
    await page.locator('#image-file').setInputFiles({...file, name:'invalid.bin', buffer:Buffer.alloc(100)});
    await page.locator('#submit-image').click();
    await page.waitForFunction(() => !busy && $('image-message').textContent.includes('ELF'));
    await page.locator('#cancel-image').click();
    assert.equal(await page.locator('#image-dialog').isVisible(), false);

    // A completed upload survives a reload and is usable from the switch palette.
    await page.reload();
    await page.waitForFunction(() => loaded);
    await page.locator('.device-template[data-type="switch"]').click();
    await page.waitForFunction(() => !busy && topology.nodes.length === 3);
    assert.equal(await page.locator('#node-image').inputValue(), file.name);
    state = await (await page.request.get(base + '/api/state')).json();
    assert.ok(!state.images.some(i => i.name === 'invalid.bin'));
    assert.deepEqual(errors, []);
    console.log('Image upload browser checks passed');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await once(fixture, 'exit');
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
