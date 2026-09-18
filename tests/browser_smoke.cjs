const {consoleAction} = require('./console_ui.cjs');
// Optional real-browser check. Use a disposable lab: this edits and starts it.
// PLAYWRIGHT_MODULE=/path/to/playwright LAB_URL=http://127.0.0.1:8091 node tests/browser_smoke.cjs
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const base = process.env.LAB_URL;
  if (!base) throw new Error('Set LAB_URL to a disposable running lab server');
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const saved = (await (await page.request.get(base + '/api/state')).json()).topology;
  const idle = () => page.waitForFunction(() => !document.body.classList.contains('busy'));
  try {
    await page.request.post(base + '/api/lab/stop', {data: {}});
    await page.request.put(base + '/api/topology', {data: {name: 'Browser test', nodes: [], links: []}});
    await page.goto(base);
    await page.locator('#starter').click(); await idle();
    assert.equal(await page.locator('.map-node').count(), 3);
    assert.equal(await page.locator('.cable-group').count(), 2);
    const first = page.locator('.map-node').first();
    const before = await first.boundingBox();
    await page.mouse.move(before.x + 60, before.y + 50);
    await page.mouse.down(); await page.mouse.move(before.x + 150, before.y + 90, {steps: 8}); await page.mouse.up(); await idle();
    const after = await first.boundingBox();
    assert.ok(after.x > before.x + 70, 'Moving a node persists its new position');
    const data = await page.evaluateHandle(() => new DataTransfer());
    await data.evaluate(d => d.setData('application/x-iol-device', 'pc'));
    await page.locator('#canvas').dispatchEvent('drop', {dataTransfer: data, clientX: 530, clientY: 230}); await idle();
    assert.equal(await page.locator('.map-node').count(), 4);
    await page.locator('#node-name').fill('Extra PC');
    await page.locator('#node-ipv4').fill('10.0.10.11/24');
    await page.locator('#apply-node').click(); await idle();
    const extra = page.locator('.map-node').filter({hasText: 'Extra PC'});
    await page.locator('#link-tool').click();
    await extra.click(); await page.locator('.map-node').filter({hasText: 'R1'}).click();
    await page.locator('#link-dialog').waitFor({state: 'visible'});
    await page.locator('#confirm-link').click(); await idle();
    assert.equal(await page.locator('.cable-group').count(), 3);
    await page.locator('#select-tool').click(); await extra.click();
    await page.locator('#delete-node').click(); await page.locator('#confirm-delete').click(); await idle();
    assert.equal(await page.locator('.map-node').count(), 3);
    assert.equal(await page.locator('.cable-group').count(), 2);
    const download = page.waitForEvent('download'); await page.locator('#export').click();
    assert.ok((await download).suggestedFilename().endsWith('.json'));
    await page.locator('#import-file').setInputFiles({name:'saved.json', mimeType:'application/json', buffer:Buffer.from(JSON.stringify(saved))}); await idle();
    assert.equal(await page.locator('#lab-name').inputValue(), saved.name);
    await page.reload(); await page.waitForFunction(() => document.querySelectorAll('.map-node').length === 3);
    await page.locator('#start-all').click(); await idle();
    await page.waitForFunction(() => document.getElementById('running-count').textContent === '3 running');
    await page.locator('.map-node[data-id="test-p"]').click();
    await page.waitForFunction(() => document.getElementById('console-status').textContent === 'Connected');
    await page.waitForTimeout(1000);
    await page.keyboard.type('echo BROWSER_SMOKE_OK', {delay: 30}); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.getElementById('terminal').innerText.includes('BROWSER_SMOKE_OK\nBROWSER_SMOKE_OK'));
    await consoleAction(page.locator('#close-console'));
    await page.locator('.map-node[data-id="test-p"]').click();
    await page.waitForFunction(() => document.getElementById('console-status').textContent === 'Connected');
    await page.keyboard.press('Enter');
    await page.screenshot({path: '/tmp/iol-browser-tested.png'});
    assert.deepEqual(errors, []);
    console.log('PASS: starter, move, palette drop, settings, cables, delete, export/import, persistence, start lab, console I/O, reconnect; no browser errors.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
