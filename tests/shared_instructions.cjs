// Published exercises across authenticated browsers, using a disposable server.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fixture = spawn('python3', ['-u','-c', `
import sys
sys.path.insert(0, 'tests')
from test_lab import LabTests
from web_auth import Auth, ROLES
t = LabTests(); t.setUp()
t.server.auth = Auth(t.lab.directory/'.auth', {n:'test-unique-password1-'+n for n in ROLES})
try:
 print('http://127.0.0.1:'+str(t.port), flush=True)
 sys.stdin.readline()
finally:
 t.tearDown()
 t.server.auth.close()
`], {cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let logs=''; fixture.stderr.on('data', d=>logs+=d);
(async()=>{
 let browser;
 try {
  const base=await new Promise((resolve,reject)=>{
   let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(out.trim());});
   fixture.once('exit',()=>reject(Error(logs)));
  });
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const errors=[], external=[];
  async function login(name) {
   const context=await browser.newContext({viewport:{width:1440,height:1000}});
   const page=await context.newPage();
   page.on('pageerror',e=>errors.push(e.message));
   page.on('request',r=>{if(!r.url().startsWith(base))external.push(r.url());});
   await page.goto(base);await page.locator('#auth-dialog[open]').waitFor();
   await page.selectOption('#auth-form select',name);
   await page.fill('#auth-form input[type=password]','test-unique-password1-'+name);
   await page.click('#auth-form button');await page.waitForFunction(()=>loaded);
   await page.waitForFunction(()=>$('instructions-source').textContent==='No published exercise');
   return page;
  }
  const admin=await login('admin'), writer=await login('user'), guest=await login('guest'), peer=await login('admin');
  for(const page of [writer,guest]) {
   await page.click('#open-instructions');
   await page.locator('#instructions-panel').waitFor({state:'visible'});
   assert.match(await page.locator('#instructions-body').textContent(),/not published/);
   assert.ok(await page.locator('#instructions-open').isHidden());
   assert.ok(await page.locator('#instructions-publish').isHidden());
  }
  const choose=async(page,name,text)=>{
   await page.locator('#instructions-file').setInputFiles({name,mimeType:'text/markdown',buffer:Buffer.from(text)});
   await page.waitForFunction(name=>$('instructions-title').textContent===name,name);
  };
  const exercise='# Shared exercise\n\n<script>window.badScript=1</script>\n\n![remote](https://invalid.example/image.png)\n\n'+('Read this step.\n\n'.repeat(180));
  await choose(admin,'first.md',exercise);
  assert.match(await admin.locator('#instructions-source').textContent(),/Local preview/);
  assert.match(await guest.locator('#instructions-body').textContent(),/not published/);
  await admin.click('#instructions-publish');
  for(const page of [admin,writer,guest,peer]) {
   await page.waitForFunction(()=>$('instructions-title').textContent==='first.md' && $('instructions-source').textContent.startsWith('Published'));
   assert.equal(await page.locator('#instructions-body script,#instructions-body img').count(),0);
   assert.equal(await page.evaluate(()=>window.badScript),undefined);
  }
  assert.equal(await guest.locator('#instructions-body').evaluate(e=>getComputedStyle(e).scrollbarWidth),'thin');
  await guest.locator('#instructions-body').evaluate(e=>e.scrollTop=240);
  const scroll=await guest.locator('#instructions-body').evaluate(e=>e.scrollTop);
  await guest.waitForResponse(r=>r.url().includes('/api/instructions?revision=') && r.request().method()==='GET');
  assert.equal(await guest.locator('#instructions-body').evaluate(e=>e.scrollTop),scroll);
  // Private local previews from a prior account must not survive guest reload.
  await guest.evaluate(()=>sessionStorage.setItem('netlab.instructions.v1',JSON.stringify({file:{name:'private.md',text:'SECRET LOCAL DRAFT'},open:true,fontSize:16})));
  await guest.reload();await guest.waitForFunction(()=>$('instructions-title').textContent==='first.md');
  assert.ok(!(await guest.locator('#instructions-body').textContent()).includes('SECRET'));
  assert.equal(await guest.locator('#instructions-body').evaluate(e=>getComputedStyle(e).fontSize),'16px');
  // Two administrators: preserve the local draft but reject its stale publish.
  await choose(peer,'draft.md','# Peer draft');
  await choose(admin,'second.md','# Second exercise');
  await admin.click('#instructions-publish');
  await guest.waitForFunction(()=>$('instructions-title').textContent==='second.md');
  await peer.waitForFunction(()=>$('instructions-source').textContent.includes('published version changed'));
  assert.equal(await peer.locator('#instructions-title').textContent(),'draft.md');
  await peer.click('#instructions-publish');
  await peer.waitForFunction(()=>$('toast').textContent.includes('Published instructions changed'));
  const state=await guest.request.get(base+'/api/instructions');
  const saved=await state.json();assert.equal(saved.file.name,'second.md');
  for(const page of [writer,guest]) {
   const result=await page.evaluate(async saved=>{
    const r=await fetch('/api/instructions',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:saved.revision,file:null})});
    return r.status;
   },saved);
   assert.equal(result,403);
  }
  await peer.click('#instructions-published');
  assert.equal(await peer.locator('#instructions-title').textContent(),'second.md');
  admin.once('dialog',d=>d.accept());await admin.click('#instructions-unpublish');
  await guest.waitForFunction(()=>$('instructions-source').textContent==='No published exercise');
  assert.match(await guest.locator('#instructions-body').textContent(),/not published/);
  // Re-publish from an empty server version and load in a new browser document.
  await choose(admin,'final.txt','Final plain exercise');await admin.click('#instructions-publish');
  await writer.waitForFunction(()=>$('instructions-title').textContent==='final.txt');
  await writer.reload();await writer.waitForFunction(()=>$('instructions-title').textContent==='final.txt');
  assert.equal(await writer.locator('#instructions-body pre').textContent(),'Final plain exercise');
  // A failed refresh is labelled; recovery with an unchanged revision keeps text.
  await writer.route('**/api/instructions*', route=>route.fulfill({status:503,contentType:'application/json',body:'{"error":"offline"}'}));
  await writer.waitForFunction(()=>$('instructions-source').textContent.startsWith('Server unavailable'));
  assert.equal(await writer.locator('#instructions-body pre').textContent(),'Final plain exercise');
  await writer.unroute('**/api/instructions*');
  await writer.waitForFunction(()=>$('instructions-source').textContent.startsWith('Published'));
  await writer.setViewportSize({width:390,height:844});
  await writer.screenshot({path:'/tmp/weblab-published-instructions-phone.png'});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log('PASS shared instructions: preview/publish, all roles, safe rendering, thin scrollbar, stable scroll, stale admin edits, unpublish, reload and no cached private preview crossover.');
 } finally {
  if(browser)await browser.close();
  const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
 }
})().catch(e=>{console.error(e,logs);process.exitCode=1;});
