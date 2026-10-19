// Uses a disposable server fixture; never touches the active lab.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fixture=spawn('python3',['-u','-c',`
import sys
sys.path.insert(0,'tests')
from test_lab import LabTests
t=LabTests();t.setUp()
try:
 t.lab.save({'name':'Network cloud UI','nodes':[],'links':[]})
 print('http://127.0.0.1:'+str(t.port),flush=True)
 sys.stdin.readline()
finally:t.tearDown()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data',d=>log+=d);
(async()=>{let browser;try{
 const url=await new Promise((resolve,reject)=>{let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(out.trim());});fixture.once('exit',code=>reject(Error(log||String(code))));});
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await page.waitForFunction(()=>loaded);
 await page.locator('.device-template[data-type=network]').click();
 await page.waitForFunction(()=>topology.nodes.length===1&&!busy);
 assert.equal(await page.locator('#start-all').isDisabled(),true);
 assert.equal(await page.locator('#start-node').isVisible(),false);
 assert.equal(await page.locator('#open-console').isVisible(),false);
 await page.locator('#node-name').fill('Provider network');
 await page.locator('#node-size').fill('150');
 await page.locator('#node-color').fill('#d9a65d');
 await page.locator('#apply-node').click();
 await page.waitForFunction(()=>!busy&&topology.nodes[0].size===150);
 assert.equal(await page.evaluate(()=>topology.nodes[0].name),'Provider network');
 assert.equal(await page.locator('.network-cloud').evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
 assert.equal(await page.locator('.network-cloud svg path').count(),1);
 const width=await page.locator('.network-cloud').evaluate(el=>el.offsetWidth);assert.equal(width,270);
 // Build two real-device drawing endpoints without starting anything.
 await page.evaluate(async()=>{
   await structuralEdit(()=>{
     const cloud=topology.nodes[0];cloud.x=440;cloud.y=180;
     for (const [name,x,y] of [['R1',220,390],['R2',660,390]]) {
       const node=newNode('router',x,y);node.name=name;topology.nodes.push(node);
     }
   });
 });
 const ids=await page.evaluate(()=>topology.nodes.map(n=>n.id));
 for(const id of ids.slice(1)){
  await page.evaluate(([a,b])=>{setMode('link');connectNode(a);connectNode(b);},[id,ids[0]]);
  assert.equal(await page.locator('#link-b-port').isDisabled(),true);
  assert.equal(await page.locator('#link-b-port option').innerText(),'Connection (diagram only)');
  await page.locator('#confirm-link').click();
  await page.waitForFunction(()=>!busy);
 }
 assert.equal(await page.locator('.diagram-cable').count(),2);
 await page.evaluate(id=>{setMode('select');selectNode(id,false);},ids[0]);
 assert.equal(await page.locator('#interface-count').innerText(),'2');
 assert.equal(await page.locator('#interfaces [data-unlink]').count(),2);
 await page.evaluate(()=>openLinkActions(topology.links[0].id));
 assert.equal(await page.locator('#link-monitor').isDisabled(),true);
 assert.match(await page.locator('#link-actions-state').innerText(),/Diagram-only/);
 await page.locator('#link-actions-close').click();
 await page.locator('#zoom-fit').click();
 await page.screenshot({path:'/tmp/weblab-network-cloud.png'});
 await page.reload();await page.waitForFunction(()=>loaded);
 assert.equal(await page.evaluate(()=>topology.nodes.find(n=>n.type==='network').color),'#d9a65d');
 assert.equal(await page.locator('.diagram-cable').count(),2);
 await page.evaluate(id=>selectNode(id,false),ids[0]);
 // Visual edits remain enabled while other devices are running.
 await page.evaluate(id=>{statuses[id]={state:'running'};updateControls();},ids[1]);
 assert.equal(await page.locator('#node-size').isDisabled(),false);
 assert.equal(await page.locator('#node-color').isDisabled(),false);
 await page.setViewportSize({width:390,height:740});
 await page.evaluate(()=>setPanel('inspector',true));
 await page.locator('#node-size').scrollIntoViewIfNeeded();
 assert.equal(await page.locator('#node-size').isVisible(),true);
 assert.deepEqual(errors,[]);
 console.log('PASS diagram cloud appearance, settings, shared anchor, capture restrictions, persistence and mobile inspector');
}finally{if(browser)await browser.close();fixture.stdin.end('\n');}})().catch(e=>{console.error(e,log);process.exitCode=1;});
