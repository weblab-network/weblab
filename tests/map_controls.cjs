// PLAYWRIGHT_MODULE=/path/to/playwright node tests/map_controls.cjs
// Edits only a disposable test topology; no Cisco images or Docker required.
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
    test.topology['nodes'][0].update(x=250, y=240)
    test.topology['nodes'][1].update(x=650, y=380)
    test.lab.save(test.topology)
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
    const idle = () => page.waitForFunction(() => !busy);
    const topology = async () => (await (await page.request.get(base + '/api/state')).json()).topology;
    const initial = await topology();
    const center = () => page.evaluate(() => { const a = $('canvas-scroll'); return {x:(a.scrollLeft+a.clientWidth/2)/mapZoom, y:(a.scrollTop+a.clientHeight/2)/mapZoom}; });
    const before = await center();
    await page.locator('#zoom-in').click();
    const after = await center();
    assert.ok(Math.abs(before.x-after.x)<1 && Math.abs(before.y-after.y)<1, 'Zoom preserves viewport center');
    assert.equal(await page.locator('#zoom-reset').innerText(), '125%');
    await page.locator('#zoom-reset').click();
    await page.locator('#zoom-out').click(); await page.locator('#zoom-out').click();
    assert.equal(await page.locator('#zoom-reset').innerText(), '64%');
    assert.equal(await page.locator('.map-node .node-type').first().isVisible(), false);
    const smallWidth = (await page.locator('.map-node').first().boundingBox()).width;
    await page.locator('#zoom-reset').click();
    assert.equal(await page.locator('.map-node .node-type').first().isVisible(), true);
    assert.ok((await page.locator('.map-node').first().boundingBox()).width > smallWidth * 1.5);

    // Hand dragging changes only the viewport, and retains the current selection.
    await page.locator('.map-node[data-id="r1"]').click();
    await page.evaluate(() => { $('canvas-scroll').scrollLeft=300; $('canvas-scroll').scrollTop=200; });
    const area = await page.locator('#canvas-scroll').boundingBox();
    const pan = {x:area.x+700, y:area.y+450};
    await page.mouse.move(pan.x, pan.y); await page.mouse.down();
    assert.equal(await page.evaluate(() => getComputedStyle($('canvas-scroll')).cursor), 'grabbing');
    await page.mouse.move(pan.x-100, pan.y-70, {steps:8}); await page.mouse.up();
    assert.deepEqual(await page.evaluate(() => ({left:$('canvas-scroll').scrollLeft, top:$('canvas-scroll').scrollTop, selected})), {left:400, top:270, selected:'r1'});
    assert.deepEqual(await topology(), initial, 'Panning and zoom do not change saved topology');

    // Ctrl+wheel preserves the world point under the cursor.
    const anchor = {x:area.x+300, y:area.y+250};
    const worldBefore = await page.evaluate(p => mapPoint(p.x,p.y), anchor);
    await page.mouse.move(anchor.x,anchor.y); await page.keyboard.down('Control');
    await page.mouse.wheel(0,-100); await page.keyboard.up('Control');
    await page.waitForFunction(() => mapZoom > 1);
    const worldAfter = await page.evaluate(p => mapPoint(p.x,p.y), anchor);
    assert.ok(Math.abs(worldBefore.x-worldAfter.x)<1 && Math.abs(worldBefore.y-worldAfter.y)<1);

    // Device movement uses world coordinates at both ends of the zoom range.
    for (const zoom of [.5,2]) {
      await page.evaluate(zoom => { setMapZoom(zoom); $('canvas-scroll').scrollLeft=0; $('canvas-scroll').scrollTop=0; }, zoom);
      const n = (await topology()).nodes.find(n => n.id==='r1');
      const box = await page.locator('.map-node[data-id="r1"]').boundingBox();
      await page.mouse.move(box.x+box.width/2,box.y+box.height/2); await page.mouse.down();
      await page.mouse.move(box.x+box.width/2+50*zoom,box.y+box.height/2+20*zoom,{steps:6}); await page.mouse.up(); await idle();
      const moved = (await topology()).nodes.find(n => n.id==='r1');
      assert.equal(moved.x,n.x+50); assert.equal(moved.y,n.y+20);
    }

    // Palette drops land under the pointer after zoom and pan.
    await page.evaluate(() => { setMapZoom(.8); $('canvas-scroll').scrollLeft=150; $('canvas-scroll').scrollTop=100; });
    const drop = {x:area.x+350,y:area.y+100};
    const expected = await page.evaluate(p => mapPoint(p.x,p.y),drop);
    const data = await page.evaluateHandle(() => new DataTransfer());
    await data.evaluate(d => d.setData('application/x-iol-device','pc'));
    await page.locator('#canvas-scroll').dispatchEvent('drop',{dataTransfer:data,clientX:drop.x,clientY:drop.y}); await idle();
    const pc = (await topology()).nodes.find(n => n.type==='pc');
    assert.equal(pc.x,Math.round(expected.x)); assert.equal(pc.y,Math.round(expected.y));
    await page.locator('#zoom-fit').click();
    for (const box of await page.locator('.map-node').all()) {
      const rect = await box.boundingBox();
      assert.ok(rect.x>=area.x && rect.y>=area.y && rect.x+rect.width<=area.x+area.width && rect.y+rect.height<=area.y+area.height, 'Fit brings nodes into view');
    }

    // Connection clicks do not begin a pan; scaled cables remain clickable.
    await page.locator('#link-tool').click();
    await page.locator(`.map-node[data-id="${pc.id}"]`).click(); await page.locator('.map-node[data-id="r2"]').click();
    await page.locator('#confirm-link').click(); await idle();
    assert.equal((await topology()).links.length,2);
    await page.locator('#select-tool').click();
    const link = (await topology()).links.find(l => l.id!=='cable');
    const midpoint = await page.evaluate(link => {
      const a=nodeById(link.a.node), b=nodeById(link.b.node), rect=$('canvas').getBoundingClientRect();
      return {x:rect.left+(a.x+b.x)/2*mapZoom,y:rect.top+(a.y+b.y)/2*mapZoom};
    },link);
    await page.mouse.click(midpoint.x,midpoint.y);
    await page.locator('#confirm-delete').click(); await idle();
    assert.equal((await topology()).links.length,1);
    await page.evaluate(() => setMapZoom(.001));
    assert.equal(await page.locator('#zoom-out').isDisabled(),true);
    await page.evaluate(() => setMapZoom(100));
    assert.equal(await page.locator('#zoom-in').isDisabled(),true);
    await page.locator('#zoom-fit').click();
    await page.screenshot({path:'/tmp/iol-map-controls.png'});

    // Parallel cables must stay distinct even if their endpoint direction differs.
    const parallel = await topology();
    parallel.links.push(
      {id:'parallel-a',a:{node:'r1',port:'0/1'},b:{node:'r2',port:'0/1'}},
      {id:'parallel-z',a:{node:'r2',port:'0/2'},b:{node:'r1',port:'0/3'}}
    );
    const save = async value => {
      const result=await page.request.put(base+'/api/topology',{data:value});
      assert.equal(result.ok(),true);
      await page.evaluate(async()=>accept(await api('/api/state')));
    };
    await save(parallel);
    const paths = () => page.locator('.cable-group').evaluateAll(groups => Object.fromEntries(groups.map(g=>[g.dataset.link,g.querySelector('.cable').getAttribute('d')])));
    const initialPaths = await paths();
    assert.equal(await page.locator('.cable-bundle').count(),1);
    assert.equal(new Set(Object.values(initialPaths)).size,3);
    assert.equal(Object.values(initialPaths).filter(p=>p.includes(' Q')).length,2);
    assert.deepEqual(await page.locator('[data-link="parallel-z"] .port-label').allTextContents(),['0/3','0/2']);
    parallel.links.reverse();
    parallel.links.forEach(link=>{[link.a,link.b]=[link.b,link.a];});
    await save(parallel);
    assert.deepEqual(await paths(),initialPaths,'Lane positions do not depend on link order or direction');

    const assertLanes = async () => {
      const midpoints=await page.locator('.cable').evaluateAll(paths=>paths.map(path=>{const p=path.getPointAtLength(path.getTotalLength()/2);return {x:p.x,y:p.y};}));
      for(let i=0;i<midpoints.length;i++) for(let j=i+1;j<midpoints.length;j++)
        assert.ok(Math.hypot(midpoints[i].x-midpoints[j].x,midpoints[i].y-midpoints[j].y)>25,'Each cable has its own lane');
    };
    await assertLanes();
    const router=parallel.nodes.find(n=>n.id==='r2'), original={x:router.x,y:router.y};
    for(const position of [{x:350,y:620},{x:700,y:280},{x:100,y:500}]) {
      Object.assign(router,position); await save(parallel); await assertLanes();
      assert.notDeepEqual(await paths(),initialPaths,'Curves follow moved nodes');
    }
    Object.assign(router,original); await save(parallel);
    const cablePoint = id => page.locator(`[data-link="${id}"] .cable`).evaluate(path=>{
      const p=path.getPointAtLength(path.getTotalLength()/2),screen=new DOMPoint(p.x,p.y).matrixTransform(path.getScreenCTM());
      return {x:screen.x,y:screen.y};
    });
    for(const zoom of [.5,1.5]) {
      await page.evaluate(zoom=>{setMapZoom(zoom);$('canvas-scroll').scrollLeft=0;$('canvas-scroll').scrollTop=0;},zoom);
      for(const id of Object.keys(initialPaths)) {
        const point=await cablePoint(id);
        assert.equal(await page.evaluate(p=>document.elementFromPoint(p.x,p.y)?.closest('.cable-group')?.dataset.link,point),id,'Scaled cables have independent click targets');
      }
    }
    await page.locator('#zoom-fit').click();
    await page.screenshot({path:'/tmp/iol-parallel-links.png'});
    const cancelPoint=await cablePoint('parallel-z');
    await page.mouse.click(cancelPoint.x,cancelPoint.y);
    assert.equal(await page.locator('#delete-dialog').isVisible(),true);
    assert.match(await page.locator('#delete-description').innerText(),/r1 0\/3 ↔ r2 0\/2/);
    assert.equal(await page.locator('#cancel-delete').evaluate(button=>button===document.activeElement),true);
    assert.equal((await topology()).links.length,3,'Clicking a cable alone never deletes it');
    await page.locator('#cancel-delete').click();
    assert.equal((await topology()).links.length,3,'Cancel preserves all parallel cables');
    for(const id of ['parallel-z','parallel-a']) {
      const point=await cablePoint(id); await page.mouse.click(point.x,point.y);
      await page.locator('#confirm-delete').click(); await idle();
      assert.equal((await topology()).links.some(link=>link.id===id),false,'Click removes exactly the selected cable');
    }
    assert.equal((await topology()).links.length,1);
    assert.ok(!(await paths()).cable.includes(' Q'),'A single remaining cable returns to a straight line');
    await page.locator('.map-node[data-id="r1"]').click();
    await page.locator('[data-unlink="cable"]').click();
    await page.keyboard.press('Escape');
    assert.equal((await topology()).links.length,1,'Escape also cancels deletion from the interface inspector');
    await page.locator('#delete-node').click();
    assert.match(await page.locator('#delete-description').innerText(),/1 connected cable/);
    assert.equal((await topology()).nodes.length,3);
    await page.locator('#cancel-delete').click();
    assert.equal((await topology()).nodes.length,3,'Cancel preserves the node');
    await page.locator('#delete-node').click(); await page.locator('#confirm-delete').click(); await idle();
    assert.equal((await topology()).nodes.length,2);
    assert.equal((await topology()).links.length,0,'Confirmed node deletion removes its attached cables');
    assert.deepEqual(errors,[]);
    console.log('PASS: zoom/pan, scaled movement/drop/cables, fit, parallel links, reversed endpoints/order, routing, independent cable deletion, cancellation/Escape, confirmed node deletion; no browser errors.');
  } finally {
    if (browser) await browser.close();
    const exited=once(fixture,'exit'); fixture.stdin.end('\n');
    if(fixture.exitCode===null) await exited;
  }
})().catch(error=>{console.error(error,fixtureLog);process.exitCode=1;});
