// PLAYWRIGHT_MODULE=/path/to/playwright node tests/link_labels.cjs
// Disposable topology matching diagonal and parallel cable layouts; no nodes run.
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
    (fixture.root / 'vios.qcow2').touch()
    nodes = [dict(id=id, name=id.upper(), type=kind, x=x, y=y, image=image, ethernet=count)
             for id,kind,x,y,image,count in [
             ('r1','router',146,101,'vios.qcow2',4), ('r2','router',144,349,'fake.bin',2),
             ('sw1','switch',512,62,'vios.qcow2',8), ('sw2','switch',522,427,'fake.bin',3),
             ('pc1','pc',872,80,'alpine:latest',2), ('pc2','pc',889,244,'alpine:latest',2),
             ('pc3','pc',869,468,'alpine:latest',2)]]
    links = [dict(id='c'+str(i), a=dict(node=a,port=ap), b=dict(node=b,port=bp))
             for i,(a,ap,b,bp) in enumerate([
             ('r1','Gi0/0','r2','0/0'), ('r1','Gi0/1','sw1','Gi0/0'),
             ('r2','0/1','sw2','0/0'), ('sw1','Gi0/1','sw2','0/1'),
             ('sw1','Gi0/2','pc1','eth0'), ('sw2','0/2','pc2','eth0'),
             ('r1','Gi0/2','sw2','0/3'), ('r2','0/2','sw1','Gi0/3'),
             ('r2','0/3','sw2','1/0'), ('sw2','1/1','pc3','eth0'),
             ('sw1','Gi1/0','sw2','1/2')])]
    fixture.lab.save(dict(name='Interface spacing',nodes=nodes,links=links))
    print('http://127.0.0.1:' + str(fixture.port), flush=True)
    sys.stdin.readline()
finally:
    fixture.tearDown()
`], {cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log = '';
fixture.stderr.on('data', data => log += data);
(async () => {
  let browser;
  try {
    const url = await new Promise((resolve,reject) => {
      let output = '';
      fixture.stdout.on('data',data => { output += data; if(output.includes('\n')) resolve(output.trim()); });
      fixture.once('exit', code => reject(new Error(`Fixture exited ${code}: ${log}`)));
    });
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1600,height:1000}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(url); await page.waitForFunction(() => loaded);
    const measure = () => page.evaluate(() => {
      const rect = el => {
        const r = el.getBoundingClientRect();
        const pad = el.matches('.port-label') ? parseFloat(getComputedStyle(el).strokeWidth)*mapZoom/2 : 0;
        return {left:r.left-pad,right:r.right+pad,top:r.top-pad,bottom:r.bottom+pad};
      };
      return {
        labels:[...document.querySelectorAll('.port-label')].map(el => ({...rect(el),text:el.textContent})),
        nodes:[...document.querySelectorAll('.map-node, .map-node .node-label')].map(rect)
      };
    });
    const clear = (a,b) => a.right+6 <= b.left || b.right+6 <= a.left || a.bottom+6 <= b.top || b.bottom+6 <= a.top;
    const check = async () => {
      const {labels,nodes} = await measure();
      assert.equal(labels.length,22,'Both ends of every cable remain visible');
      for(const [i,label] of labels.entries()) {
        for(const node of nodes) assert.ok(clear(label,node), `${label.text} must clear node/name bounds`);
        for(const other of labels.slice(i+1)) assert.ok(clear(label,other), `${label.text} must clear ${other.text}`);
      }
    };
    for(const zoom of [1,.512,.2,2]) {
      await page.evaluate(zoom => { setMapZoom(zoom); $('canvas-scroll').scrollLeft=0; $('canvas-scroll').scrollTop=0; },zoom);
      await check();
      if(zoom === 1 || zoom === .512) await page.locator('#canvas-scroll').screenshot({path:`/tmp/link-labels-${zoom}.png`});
    }
    await page.evaluate(() => { setMapZoom(.512); nodeById('r1').name='Router-with-a-long-hostname'; render(); });
    await check();
    // Relayout follows cards while dragging and after zoom compact mode changes height.
    await page.evaluate(() => {
      const n=nodeById('r2'); n.x=400; n.y=220;
      const el=document.querySelector('.map-node[data-id="r2"]');
      el.style.left=n.x+'px'; el.style.top=n.y+'px'; renderLinks();
    });
    await check();
    const before = (await measure()).labels;
    await page.evaluate(() => { topology.links.reverse(); topology.links.forEach(l => [l.a,l.b]=[l.b,l.a]); renderLinks(); });
    const after = (await measure()).labels;
    const sort = labels => labels.sort((a,b) => a.left-b.left || a.top-b.top);
    assert.deepEqual(sort(after),sort(before),'Label placement does not depend on cable order/direction');
    assert.deepEqual(errors,[]);
    console.log('PASS: label/node clearance, parallel labels, 20–200% zoom, long hostnames, movement and stable ordering.');
  } finally {
    if(browser) await browser.close();
    fixture.stdin.end('\n');
    if(fixture.exitCode===null) await once(fixture,'exit');
  }
})().catch(error => { console.error(error,log); process.exitCode=1; });
