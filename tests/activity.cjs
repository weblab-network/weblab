// Admin-only activity UI with real role boundaries and persisted metadata.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fixture=spawn('python3',['-u','-c',`
import sys
sys.path.insert(0,'tests')
from test_lab import LabTests
from web_auth import Auth, ROLES
t=LabTests();t.setUp()
auth=Auth(t.lab.directory/'.auth',{n:'test-unique-password1-'+n for n in ROLES})
t.server.auth=auth
for i in range(105):
 ident=auth.activity_begin('user','seed','browser','node.stop','r1')
 auth.activity_finish(ident,'http_ok',200)
ident=auth.activity_begin('admin','seed-agent','agent','tool.console-send','r2')
auth.activity_finish(ident,'input_sent')
try:
 print('http://127.0.0.1:'+str(t.port),flush=True)
 sys.stdin.readline()
finally:
 t.tearDown();auth.close()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let logs='';fixture.stderr.on('data',d=>logs+=d);
(async()=>{
 let browser;
 try {
 const base=await new Promise((resolve,reject)=>{let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(out.trim());});fixture.once('exit',()=>reject(Error(logs)));});
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const context=await browser.newContext(),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const login=async name=>{
  await page.locator('#auth-dialog[open]').waitFor();
  await page.selectOption('#auth-form select',name);
  await page.fill('#auth-form input[type=password]','test-unique-password1-'+name);
  await page.click('#auth-form button');await page.waitForFunction(()=>loaded);
 };
 const ready=async()=>page.waitForFunction(()=>document.querySelector('#activity-status').textContent.includes('Newest first'));
 await page.goto(base);await login('admin');
 await page.locator('#auth-activity').waitFor();await page.click('#auth-activity');await ready();
 assert.equal(await page.locator('#activity-records tr').count(),100);
 assert.match(await page.locator('#activity-records').textContent(),/auth.login/);
 await page.click('#activity-older');await page.waitForFunction(()=>document.querySelectorAll('#activity-records tr').length>100);
 assert.ok(await page.locator('#activity-older').isHidden());
 await page.selectOption('#activity-source','agent');await ready();
 assert.equal(await page.locator('#activity-records tr').count(),1);
 assert.match(await page.locator('#activity-records').textContent(),/input sent/);
 await page.selectOption('#activity-source','');await ready();
 await page.selectOption('#activity-account','user');await ready();
 assert.equal(await page.locator('#activity-records tr').count(),100);
 assert.ok(!(await page.locator('#activity-records').textContent()).includes('auth.login'));
 await page.selectOption('#activity-account','');await ready();
 await page.evaluate(async()=>{
  await fetch('/api/instructions',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:'0',file:{name:'private-title.md',text:'private-content-do-not-record'}})});
 });
 await page.click('#activity-refresh');await ready();
 const records=await page.locator('#activity-records').textContent();
 assert.ok(records.includes('instructions.update'));
 assert.ok(!records.includes('private-content-do-not-record') && !records.includes('private-title'));
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'/tmp/weblab-activity-phone.png'});
 const box=await page.locator('#activity-dialog').boundingBox();assert.ok(box.width<=390);
 await page.click('#activity-close');await page.click('#auth-logout');await login('user');
 assert.ok(await page.locator('#auth-activity').isHidden());
 assert.equal((await page.request.get(base+'/api/activity')).status(),403);
 await page.click('#auth-logout');await login('guest');
 assert.ok(await page.locator('#auth-activity').isHidden());
 assert.equal((await page.request.get(base+'/api/activity')).status(),403);
 assert.deepEqual(errors,[]);
 console.log('PASS activity: admin-only viewer, paging, filters, live refresh, private-content exclusion and phone layout.');
 } catch(error) {console.error(logs);throw error;}
 finally {if(browser)await browser.close();fixture.stdin.end('\n');await new Promise(resolve=>fixture.once('exit',resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
