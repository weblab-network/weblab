// Disposable grant-management UI fixture. No active lab or SSH listener.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fixture=spawn('python3',['-u','-c',`
import sys,json,subprocess
sys.path.insert(0,'tests')
from test_lab import LabTests
from external_access import Access
t=LabTests();t.setUp();a=Access(t.lab);t.server.access=a;t.lab.access=a
try:
 t.lab.save({'name':'SSH UI','nodes':[t.node('r1'),{'id':'cloud','type':'cloud','name':'Internet'}],'links':[]})
 key=t.root/'key';subprocess.run(['ssh-keygen','-q','-t','ed25519','-C','example-workstation','-N','','-f',str(key)],check=True)
 print(json.dumps({'url':'http://127.0.0.1:'+str(t.port),'key':key.with_suffix('.pub').read_text().strip()}),flush=True)
 sys.stdin.readline()
finally:a.close();t.tearDown()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data',d=>log+=d);
(async()=>{let browser;
 try {
  const config=await new Promise((resolve,reject)=>{let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(JSON.parse(out.trim()));});fixture.once('exit',code=>reject(Error(log||String(code))));});
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.goto(config.url);await page.waitForFunction(()=>loaded);
  await page.locator('[data-storage]').first().click();await page.locator('#external-access-open').click();
  const dialog=page.locator('#external-access-dialog');await dialog.locator('[data-ssh-status]').getByText('Console endpoint disabled.',{exact:false}).waitFor();
  await dialog.locator('[name=label]').fill('<img src=x onerror=alert(1)>');
  await dialog.locator('[name=key]').fill(config.key);await dialog.locator('[name=role]').selectOption('writer');
  await dialog.locator('[type=submit]').click();await dialog.locator('[data-ssh-keys] li').waitFor();
  assert.equal(await dialog.locator('img').count(),0);
  assert.match(await dialog.locator('[data-ssh-keys]').innerText(),/writer.*enabled.*SHA256:/);
  await dialog.locator('[name=scope]').selectOption('gateway');
  await dialog.locator('[name=node]').selectOption('cloud');
  await dialog.locator('[name=label]').fill('Gateway key');await dialog.locator('[name=key]').fill(config.key);
  await dialog.locator('[type=submit]').click();await page.waitForFunction(()=>document.querySelectorAll('[data-ssh-keys] li').length===2);
  page.once('dialog',d=>d.accept());await dialog.locator('[data-ssh-keys] li').first().getByText('Revoke',{exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-ssh-keys] li').length===1);
  await dialog.locator('[data-ssh-close]').click();
  await page.evaluate(()=>{setSelection('cloud');renderInspector();});
  await page.locator('#cloud-ssh-enabled').check();
  const panel=page.locator('.internet-panel'),tab=name=>panel.getByRole('tab',{name,exact:true}).click();
  assert.equal(await panel.getByRole('tab',{name:'SSH access',exact:true}).getAttribute('aria-selected'),'true');
  assert.equal(await panel.locator('[data-part=ssh-enabled]').isChecked(),true);
  assert.equal(await page.evaluate(()=>!!nodeById('cloud').internet.ssh?.enabled),false);
  const checkbox=await panel.locator('[data-part=ssh-enabled]').boundingBox();
  assert.ok(checkbox.width<25,'SSH checkbox must not stretch to the form width');
  assert.equal(await panel.locator('[data-part=ssh-enabled]').evaluate(e=>getComputedStyle(e.parentElement).display),'flex');
  await panel.locator('[data-part=ssh-port]').fill('22444');
  // Tabs preserve edits; opening the Inspector shortcut reuses the same window.
  await tab('Network');await page.locator('#cloud-ssh-open').click();
  assert.equal(await page.locator('.internet-panel').count(),1);
  assert.equal(await panel.locator('[data-part=ssh-port]').inputValue(),'22444');
  // An invalid field on a hidden tab must be revealed before browser validation.
  await tab('Port forwards');await panel.locator('[data-part=add-forward]').click();
  await tab('SSH access');await panel.locator('[data-part=save]').click();
  assert.equal(await panel.getByRole('tab',{name:'Port forwards',exact:true}).getAttribute('aria-selected'),'true');
  assert.equal(await panel.locator('[data-forward-field=external_port]').evaluate(e=>e===document.activeElement),true);
  await panel.locator('[data-forward-remove]').click();await tab('SSH access');
  await panel.getByRole('tab',{name:'SSH access',exact:true}).focus();await page.keyboard.press('ArrowLeft');
  assert.equal(await panel.getByRole('tab',{name:'Firewall',exact:true}).getAttribute('aria-selected'),'true');
  await page.keyboard.press('End');
  assert.equal(await panel.getByRole('tab',{name:'SSH access',exact:true}).getAttribute('aria-selected'),'true');
  await panel.locator('[data-part=save]').click();await page.waitForFunction(()=>nodeById('cloud').internet.ssh?.external_port===22444&&!busy);
  assert.equal(await page.locator('#cloud-ssh-enabled').isChecked(),true);
  await panel.locator('[data-part=ssh-enabled]').uncheck();
  assert.equal(await page.locator('#cloud-ssh-enabled').isChecked(),false);
  await panel.locator('[data-part=close]').click();
  assert.equal(await page.locator('#cloud-ssh-enabled').isChecked(),true,'Closing without Apply discards the draft');
  await page.locator('#cloud-ssh-open').click();
  await panel.screenshot({path:'/tmp/weblab-internet-ssh-tabs.png'});
  await page.evaluate(()=>document.documentElement.dataset.theme='light');
  await page.setViewportSize({width:390,height:740});
  await tab('SSH access');
  const frame=await panel.boundingBox(),save=await panel.locator('[data-part=save]').boundingBox();
  assert.ok(save.y>=frame.y&&save.y+save.height<=frame.y+frame.height,'Apply remains visible on mobile');
  await panel.screenshot({path:'/tmp/weblab-internet-ssh-tabs-mobile.png'});
  await panel.locator('[data-part=ssh-enabled]').uncheck();
  await panel.locator('[data-part=save]').click();
  await page.waitForFunction(()=>nodeById('cloud').internet.ssh?.enabled===false&&!busy);
  assert.equal(await page.locator('#cloud-ssh-enabled').isChecked(),false);
  assert.deepEqual(errors,[]);
  console.log('PASS key grants, SSH Inspector draft/apply, tabs, hidden-field validation, keyboard navigation and mobile layout');
 }finally{if(browser)await browser.close();fixture.stdin.end('\n');await new Promise(resolve=>fixture.once('exit',resolve));}
})().catch(e=>{console.error(e,log);process.exitCode=1;});
