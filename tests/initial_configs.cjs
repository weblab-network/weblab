// Stopped-PC settings and reusable initial-configuration export/import.
// PLAYWRIGHT_MODULE=/path/to/playwright node tests/initial_configs.cjs
// Requires Python 3 and Perl; never connects to or edits the user's lab.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');

const fixture = spawn('python3', ['-u', '-c', `
import sys, copy
sys.path.insert(0, 'tests')
from test_config_capture import CaptureTests
from test_saved_config import nvram
case=CaptureTests();case.setUp()
test=case.fixture
try:
    test.lab.stop_all()
    topology=copy.deepcopy(test.lab.topology)
    topology['nodes'][0].update(x=220,y=200)
    topology['nodes'][1].update(x=520,y=200)
    topology['nodes'].append({'id':'pc1','name':'PC1','type':'pc','image':'alpine:latest','x':350,'y':450})
    topology['links'].append({'id':'pc-link','a':{'node':'r1','port':'0/1'},'b':{'node':'pc1','port':'eth0'}})
    test.lab.save(topology)
    test.lab.start('r1');test.lab.start('r2')
    for node in test.lab.topology['nodes']:
        if node['type']!='pc':
            (test.lab.node_dir(node['id']) / ('nvram_%05d' % node['iol_id'])).write_bytes(nvram(b'hostname SavedFromDisk\\nend\\n'))
    print('http://127.0.0.1:'+str(test.port),flush=True)
    sys.stdin.readline()
finally:
    case.tearDown()
`], {cwd: path.resolve(__dirname, '..'), stdio: ['pipe', 'pipe', 'pipe']});
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
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.waitForFunction(()=>loaded);
    await page.evaluate(()=>selectNode('pc1',false));
    assert.equal(await page.locator('#node-ipv4').isEnabled(),true);
    assert.equal(await page.locator('#node-gateway').isEnabled(),true);
    assert.equal(await page.locator('#node-image').isDisabled(),true);
    await page.locator('#node-ipv4').fill('192.0.2.10/24');
    await page.locator('#node-gateway').fill('192.0.2.1');
    await page.locator('#apply-node').click();
    await page.waitForFunction(()=>!busy&&nodeById('pc1').ipv4==='192.0.2.10/24');
    assert.equal(await page.evaluate(()=>state('r1')),'running');
    assert.equal(await page.evaluate(()=>state('r2')),'running');
    await page.evaluate(()=>openConsole('r1'));
    await page.waitForFunction(()=>consoleSessions.get('r1').lockReady);
    await page.locator('#lock-console').check();
    await page.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    await page.locator('#export').click();
    assert.equal(await page.locator('#export-initial').isEnabled(),true);
    assert.equal(await page.locator('#export-saved').isDisabled(),true,'Saved configs require stopped Cisco nodes');
    await page.locator('#export-initial').click();
    await page.waitForFunction(()=>!busy&&$('export-message').textContent.includes('Release'));
    await page.locator('#cancel-export').click();
    await page.locator('#lock-console').uncheck();
    await page.waitForFunction(()=>!consoleSessions.get('r1').locked);
    await page.locator('#export').click();
    const download=page.waitForEvent('download');
    await page.locator('#export-initial').click();
    const file=await download;const filename=await file.path();
    const data=JSON.parse(require('node:fs').readFileSync(filename,'utf8'));
    assert.equal(data.nodes.find(n=>n.id==='pc1').ipv4,'192.0.2.10/24');
    assert.match(data.nodes.find(n=>n.id==='r1').startup_config,/hostname Capture/);
    assert.match(data.nodes.find(n=>n.id==='r2').startup_config,/ip address 192.0.2.1/);
    assert.equal(await page.evaluate(()=>!!nodeById('r1').startup_config),false,'Export does not change active topology');
    await page.waitForFunction(()=>!busy&&!consoleSessions.get('r1').locked);
    await page.request.post(base+'/api/lab/stop',{data:{}});
    await page.evaluate(async()=>accept(await api('/api/state')));
    await page.locator('#import-file').setInputFiles({name:'seeded.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
    await page.waitForFunction(()=>!busy&&nodeById('r1').startup_config?.includes('hostname Capture'));
    await page.locator('#export').click();
    assert.equal(await page.locator('#export-initial').isDisabled(),true,'Live capture requires Cisco nodes running');
    assert.equal(await page.locator('#export-json').isEnabled(),true,'Stored snippets can still be exported while stopped');
    assert.equal(await page.locator('#export-saved').isEnabled(),true);
    const savedDownload=page.waitForEvent('download');
    await page.locator('#export-saved').click();
    const savedFile=await savedDownload;
    const savedData=JSON.parse(require('node:fs').readFileSync(await savedFile.path(),'utf8'));
    assert.equal(savedData.nodes.find(n=>n.id==='r1').startup_config,'hostname SavedFromDisk\nend\n');
    assert.match(await page.evaluate(()=>nodeById('r1').startup_config),/hostname Capture/,'Export preserves active snippets');
    await page.locator('#import-file').setInputFiles({name:'saved.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(savedData))});
    await page.waitForFunction(()=>!busy&&nodeById('r1').startup_config?.includes('hostname SavedFromDisk'));
    assert.deepEqual(errors,[]);
    console.log('PASS: stopped-PC editing, console lock refusal, live/saved config JSON exports, source selection and snippet import round trips.');
  } finally {
    if (browser) await browser.close();
    const exited = once(fixture, 'exit');
    fixture.stdin.end('\n');
    if (fixture.exitCode === null) await exited;
  }
})().catch(error => { console.error(error, fixtureLog); process.exitCode = 1; });
