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
 t.lab.save({'name':'Diagram groups UI','nodes':[],'links':[]})
 print('http://127.0.0.1:'+str(t.port),flush=True)
 sys.stdin.readline()
finally:t.tearDown()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data',d=>log+=d);
(async()=>{let browser;try{
 const url=await new Promise((resolve,reject)=>{let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(out.trim());});fixture.once('exit',code=>reject(Error(log||String(code))));});
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await page.waitForFunction(()=>loaded);
 await page.locator('.device-template[data-type=group]').click();
 await page.waitForFunction(()=>topology.nodes.length===1&&!busy);
 assert.equal(await page.locator('#start-all').isDisabled(),true);
 assert.equal(await page.locator('#start-node').isVisible(),false);
 assert.equal(await page.locator('#open-console').isVisible(),false);
 await page.locator('#node-name').fill('Core');
 await page.locator('#group-notes').fill('AS 65000\nOSPF area 0');
 await page.locator('#group-width').fill('600');await page.locator('#group-height').fill('400');
 await page.locator('#group-color').fill('#67d8bc');
 await page.locator('#apply-node').click();await page.waitForFunction(()=>!busy&&topology.nodes[0].width===600);
 const gid=await page.evaluate(()=>topology.nodes[0].id);
 const group=page.locator(`.diagram-group[data-id="${gid}"]`);
 await page.evaluate(async()=>{
  await structuralEdit(()=>{
   Object.assign(topology.nodes[0],{x:420,y:320});
   for(const [id,x,y] of [['r1',280,300],['r2',550,300],['r3',900,300],['edge',720,440]]){
    const n=newNode('router',x,y);n.id=id;n.name=id.toUpperCase();topology.nodes.push(n);
   }
   topology.links.push({id:'corelink',a:{node:'r1',port:'0/0'},b:{node:'r2',port:'0/0'}});
  });
  setMapZoom(.75);$('canvas-scroll').scrollLeft=0;$('canvas-scroll').scrollTop=0;
 });
 const positions=()=>page.evaluate(()=>Object.fromEntries(topology.nodes.map(n=>[n.id,{x:n.x,y:n.y}])));
 const drag=async(locator,dx,dy,cancel=false)=>{
  const b=await locator.boundingBox();
  await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.down();
  await page.mouse.move(b.x+b.width/2+dx,b.y+b.height/2+dy,{steps:5});
  if(cancel)await page.keyboard.press('Escape');
  await page.mouse.up();await page.waitForFunction(()=>!busy&&!dragging);
 };
 // Title drag moves fully enclosed nodes, excluding an overlapping and an outside node.
 let before=await positions();await drag(group.locator('.group-label'),60,30);let after=await positions();
 for(const id of [gid,'r1','r2'])assert.deepEqual(after[id],{x:before[id].x+80,y:before[id].y+40});
 for(const id of ['r3','edge'])assert.deepEqual(after[id],before[id]);
 assert.equal(await page.evaluate(()=>selected),gid);
 // Cancel is atomic, including the nodes borrowed into the movement.
 before=await positions();await drag(group.locator('.group-label'),35,20,true);assert.deepEqual(await positions(),before);
 // Clicking a device through the rectangle still selects that device.
 await page.locator('.map-node[data-id=r1]').click();assert.equal(await page.evaluate(()=>selected),'r1');
 // Cables remain interactive above the rectangle.
 const cable=page.locator('[data-link=corelink] .cable-hit');
 const midpoint=await cable.evaluate(el=>{const p=el.getPointAtLength(el.getTotalLength()/2);const screen=new DOMPoint(p.x,p.y).matrixTransform(el.getScreenCTM());return {x:screen.x,y:screen.y};});
 await page.mouse.click(midpoint.x,midpoint.y,{button:'right'});assert.equal(await page.locator('#link-actions-dialog').isVisible(),true);
 await page.locator('#link-actions-close').click();
 await page.evaluate(id=>selectNode(id,false),gid);
 // Borders have a generous hit area, while the empty interior stays transparent to input.
 const border=await group.boundingBox();before=await positions();
 await page.mouse.move(border.x+border.width/2,border.y+border.height-2);await page.mouse.down();
 await page.mouse.move(border.x+border.width/2+15,border.y+border.height+13,{steps:4});
 assert.equal((await positions())[gid].x,before[gid].x+20);
 await page.keyboard.press('Escape');await page.mouse.up();await page.waitForFunction(()=>!busy&&!dragging);
 assert.deepEqual(await positions(),before);
 // Resize never moves devices; Escape restores geometry.
 const dimensions=()=>page.evaluate(id=>{const n=nodeById(id);return {x:n.x,y:n.y,width:n.width,height:n.height};},gid);
 before=await positions();let d=await dimensions();
 await drag(group.locator('.group-resize'),30,30);let resized=await dimensions();
 assert.equal(resized.width,d.width+40);assert.equal(resized.height,d.height+40);
 for(const id of ['r1','r2','r3','edge'])assert.deepEqual((await positions())[id],before[id]);
 d=await dimensions();await drag(group.locator('.group-resize'),30,30,true);assert.deepEqual(await dimensions(),d);
 // The same group can serve as a non-sticky annotation, with notes outside it.
 await page.locator('#group-move-contents').uncheck();
 await page.locator('#group-label-position').selectOption('above');
 await page.locator('#apply-node').click();await page.waitForFunction(()=>!busy);
 before=await positions();await drag(group.locator('.group-label'),30,0);after=await positions();
 assert.equal(after[gid].x,before[gid].x+40);assert.deepEqual(after.r1,before.r1);
 // Keyboard size/movement controls are usable without a pointer.
 await group.locator('.group-resize').focus();d=await dimensions();await page.keyboard.press('Shift+ArrowRight');
 await page.waitForFunction(()=>!busy);assert.equal((await dimensions()).width,d.width+20);
 await group.locator('.group-label').focus();before=await positions();await page.keyboard.press('ArrowDown');
 await page.waitForFunction(()=>!busy);assert.equal((await positions())[gid].y,before[gid].y+2);
 // Imported notes remain text, never markup. Nested/overlapping selections move once.
 await page.evaluate(async id=>{
  await edit(()=>{
   const n=nodeById(id);n.notes='<img src=x onerror="alert(1)">\nAS 65000';n.move_contents=true;n.label_position='inside';
   const inner=newNode('group',n.x,n.y);Object.assign(inner,{id:'inner',name:'Routing',width:280,height:180,move_contents:true});topology.nodes.push(inner);
  });
 },gid);
 assert.equal(await group.locator('img').count(),0);
 await page.evaluate(id=>{selectedNodes.clear();[id,'inner','r1'].forEach(x=>selectedNodes.add(x));selected=id;render();renderInspector();},gid);
 before=await positions();await drag(group.locator('.group-label'),15,0);after=await positions();
 for(const id of [gid,'inner','r1','r2'])assert.equal(after[id].x,before[id].x+20);
 // Group geometry and text persist on reload.
 const saved=await dimensions();await page.reload();await page.waitForFunction(()=>loaded);
 assert.deepEqual(await dimensions(),saved);
 assert.match(await group.locator('.group-notes').innerText(),/<img src=x/);
 // Read-only users can inspect, but cannot move/resize/change annotations.
 await page.evaluate(id=>{selectNode(id,false);window.originalCan=window.weblabAuth.can;window.weblabAuth.can=()=>false;updateControls();},gid);
 assert.equal(await page.locator('#group-width').isDisabled(),true);
 before=await positions();await drag(group.locator('.group-label'),30,10);assert.deepEqual(await positions(),before);
 await page.evaluate(()=>{window.weblabAuth.can=window.originalCan;updateControls();});
 // Notes are editable with another node running; no new runtime behavior is introduced.
 await page.evaluate(id=>{selectNode(id,false);statuses.r1={state:'running'};updateControls();},gid);
 assert.equal(await page.locator('#group-notes').isDisabled(),false);
 await page.evaluate(()=>{statuses={};updateControls();});
 // Remove only the rectangle, preserving enclosed nodes and links.
 await page.locator('#delete-node').click();assert.match(await page.locator('#delete-description').innerText(),/Enclosed devices stay/);
 await page.locator('#delete-form button[value=delete]').click();await page.waitForFunction(()=>!busy);
 assert.equal(await group.count(),0);assert.equal(await page.locator('.map-node[data-id=r1]').count(),1);
 assert.equal(await page.evaluate(()=>topology.links.length),1);
 await page.evaluate(async()=>{
  await edit(()=>{
   const inner=nodeById('inner');Object.assign(inner,{name:'Core · AS 65000',notes:'OSPF area 0\nMPLS backbone',width:640,height:380,x:470,y:330,color:'#67d8bc'});
  });
  selectNode('inner',false);fitMap();
 });
 await page.screenshot({path:'/tmp/weblab-diagram-groups.png'});
 await page.setViewportSize({width:390,height:740});
 await page.waitForFunction(()=>panelMedia.matches&&$('workspace').classList.contains('panels-compact'));
 await page.evaluate(()=>setPanel('inspector',true));
 await page.locator('#group-notes').scrollIntoViewIfNeeded();assert.equal(await page.locator('#group-notes').isVisible(),true);
 assert.deepEqual(errors,[]);
 console.log('PASS diagram groups: editing, sticky/overlapping/nested movement, zoom, cancellation, resize, keyboard, role controls, persistence and deletion');
}finally{if(browser)await browser.close();fixture.stdin.end('\n');}})().catch(e=>{console.error(e,log);process.exitCode=1;});
