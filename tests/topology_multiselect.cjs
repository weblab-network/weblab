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
    image = test.root / 'fake.bin'
    image.write_text(image.read_text().replace("b'RX:' + data", "b'RX:' + data.replace(bytes([13]), bytes([13, 10]))"))
    test.topology['nodes'][0].update(x=180, y=160)
    test.topology['nodes'][1].update(x=420, y=180)
    for i in range(3, 7):
        node = test.node('r' + str(i))
        node.update(x=180 + ((i-1) % 3)*240, y=160 + ((i-1)//3)*220)
        test.topology['nodes'].append(node)
    test.lab.save(test.topology)
    test.lab.start_all()
    print('http://127.0.0.1:' + str(test.port), flush=True)
    sys.stdin.readline()
finally:
    test.tearDown()
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
    const page = await browser.newPage({viewport:{width:1500,height:1000},hasTouch:true});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    await page.waitForFunction(()=>loaded);
    await page.evaluate(()=>setMapZoom(.75));
    const node = id => page.locator(`.map-node[data-id="${id}"]`);
    const selection = () => page.evaluate(()=>[...selectedNodes].sort());
    const positions = () => page.evaluate(()=>topology.nodes.map(n=>({id:n.id,x:n.x,y:n.y})));
    const drag = async (id, dx, dy, cancel=false) => {
      const b = await node(id).boundingBox();
      await page.mouse.move(b.x+b.width/2,b.y+b.height/2); await page.mouse.down();
      await page.mouse.move(b.x+b.width/2+dx,b.y+b.height/2+dy,{steps:5});
      if(cancel) await node(id).dispatchEvent('pointercancel',{pointerId:1});
      await page.mouse.up();
      await page.waitForFunction(()=>!busy&&!dragging);
    };
    await node('r1').click({modifiers:['Shift']});
    await node('r2').click({modifiers:['Control']});
    assert.deepEqual(await selection(),['r1','r2']);
    assert.equal(await page.evaluate(()=>consoleSessions.size),0);
    const original = await positions();
    await drag('r1',60,30);
    let changed = await positions();
    for(let i=0;i<2;i++) {
      assert.equal(changed[i].x-original[i].x,80);
      assert.equal(changed[i].y-original[i].y,40);
    }
    assert.deepEqual(changed.slice(2),original.slice(2));
    assert.deepEqual(await page.evaluate(async()=> (await api('/api/state')).topology.nodes.map(n=>({id:n.id,x:n.x,y:n.y}))),changed);
    await page.evaluate(async()=>accept(await api('/api/state')));
    assert.deepEqual(await selection(),['r1','r2']);
    await drag('r2',35,25,true);
    assert.deepEqual(await positions(),changed);
    assert.equal(await page.evaluate(()=>consoleSessions.size),0);
    await drag('r1',-500,-500);
    changed = await positions();
    assert.equal(changed[0].x,70); assert.equal(changed[0].y,60);
    assert.equal(changed[1].x-changed[0].x,original[1].x-original[0].x);
    assert.equal(changed[1].y-changed[0].y,original[1].y-original[0].y);
    await node('r2').click({modifiers:['Meta']});
    assert.deepEqual(await selection(),['r1']);
    await page.keyboard.press('Escape');
    assert.deepEqual(await selection(),[]);
    await page.locator('#float-topology').click();
    await page.locator('#zoom-fit').click();
    await page.locator('#multi-select-tool').tap();
    await node('r1').tap(); await node('r2').tap();
    assert.deepEqual(await selection(),['r1','r2']);
    assert.equal(await page.evaluate(()=>consoleSessions.size),0);
    await page.locator('#multi-select-tool').tap();
    await node('r1').click();
    await page.waitForFunction(()=>consoleSessions.get('r1')?.status==='Connected');
    assert.deepEqual(await selection(),['r1']);
    await page.evaluate(async()=>{closeConsole('r1'); accept(await api('/api/lab/stop','POST',{})); multiSelect=true;setMode('link');});
    assert.equal(await page.locator('#multi-select-tool').getAttribute('aria-pressed'),'false');
    await node('r1').click(); await node('r3').click();
    assert.equal(await page.locator('#link-dialog').isVisible(),true);
    assert.deepEqual(errors,[]);
    console.log('PASS multi-selection, zoomed group movement, persistence, bounds, cancellation, touch/floating map, consoles and Connect');
  } finally {
    if(browser) await browser.close();
    const exited=once(fixture,'exit'); fixture.stdin.end('\n');
    if(fixture.exitCode===null) await exited;
  }
})().catch(error=>{console.error(error,fixtureLog);process.exitCode=1;});
