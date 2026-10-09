// Agent window controls and safe rendering against a disposable HTTP fixture.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fixture=spawn('python3',['-u','-c',`
import sys
sys.path.insert(0,'tests')
from test_lab import LabTests
t=LabTests();t.setUp()
try:
 print('http://127.0.0.1:'+str(t.port),flush=True)
 sys.stdin.readline()
finally: t.tearDown()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let logs='';fixture.stderr.on('data',d=>logs+=d);
(async()=>{
 let browser;
 try {
 const base=await new Promise((resolve,reject)=>{let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(out.trim());});fixture.once('exit',()=>reject(Error(logs)));});
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1280,height:950}}), errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await page.waitForFunction(()=>loaded);
 assert.ok(await page.locator('#open-agent').isHidden());
 let status='idle',messages=[],count=0,stop=0,proposals=[],approvals=[],decisions=[],phase='Thinking',toolError=null,auth={status:'unknown',signed_in:false,models:[]};
 const settings={endpoint:'http://model:11434/v1',model:'gpt-oss:20b',lab_changes:false,console_input:false,auto_approve:false,context_window:32768,turn_timeout:900};
 let conversationId='c'.repeat(32), conversationNumber=0;
 const historyRecords=new Map();
 const sentFiles=new Map();
 await page.route('**/api/agent/*',async route=>{
  const action=route.request().url().split('/').pop();
  let body={};if(route.request().method()==='POST')body=route.request().postDataJSON();
  const state=()=>{
   const record=historyRecords.get(conversationId)||{id:conversationId,title:'',updated:1700000000,settings:{...settings}};
   record.messages=messages;if(!record.title && messages.length)record.title=messages[0].text.slice(0,100);
   historyRecords.set(conversationId,record);
   const history=[...historyRecords.values()].map(r=>({id:r.id,title:r.title||'New conversation',updated:r.updated,model:r.settings.model,active:r.id===conversationId,message_count:r.messages.length}));
   return {status,messages,settings,proposals,approvals,history,conversation_id:conversationId,activity:'',error:'',phase,elapsed_seconds:5,quiet_seconds:0,tool_error:toolError,auth};
  };
  let result;
  if(action==='info')result={enabled:true,providers:[settings.endpoint]};
  else if(action==='session')result={token:'browser-capability',settings};
  else {
   assert.equal(route.request().headers()['x-weblab-agent-session'],'browser-capability');
   if(action==='state')result=state();
   else if(action==='settings'){Object.assign(settings,body);result={settings};}
   else if(action==='login'){auth={status:'waiting',signed_in:false,models:[],code:'ABCD-1234'};result=auth;}
   else if(action==='login-cancel'){auth={status:'cancelled',signed_in:false,models:[]};result=auth;}
   else if(action==='login-status'){auth={status:'signed-in',signed_in:true,models:['gpt-5.6-sol']};result=auth;}
   else if(action==='logout'){auth={status:'signed-out',signed_in:false,models:[]};result=auth;}
   else if(action==='check')result={available:true,models:[settings.model]};
   else if(action==='turn'){
    count++;status='running';proposals=['a'.repeat(32)];
    const attachments=(body.attachments||[]).map((a,i)=>{
      const file={...a,id:String(i).padStart(32,'0'),mime:a.name.endsWith('.png')?'image/png':'text/plain',size:Buffer.from(a.data,'base64').length};
      if(file.mime==='text/plain')file.text=Buffer.from(a.data,'base64').toString('utf8');
      sentFiles.set(file.id,file);const {data,...meta}=file;return meta;
    });
    messages=[{role:'user',text:body.prompt,attachments},{role:'assistant',text:'<img src=x onerror="window.injected=true">'}];result=state();
   }
   else if(action==='attachment'){assert.equal(body.conversation_id,conversationId);result=sentFiles.get(body.id);}
   else if(action==='stop'){stop++;status='interrupted';result=state();}
   else if(action==='reset'){state();conversationId=(++conversationNumber).toString(16).padStart(32,'0');messages=[];proposals=[];approvals=[];status='idle';result=state();}
   else if(action==='history-read')result=historyRecords.get(body.id);
   else if(action==='history-export'){
    const record=historyRecords.get(body.id);
    result={format:'weblab-agent-transcript',version:1,title:record.title,model:record.settings.model,
      messages:record.messages.map(m=>({role:m.role,text:m.text}))};
   }
   else if(action==='history-open'){state();conversationId=body.id;messages=historyRecords.get(body.id).messages;proposals=[];approvals=[];status='completed';result=state();}
   else if(action==='history-rename'){historyRecords.get(body.id).title=body.title;result=state();}
   else if(action==='history-delete'){historyRecords.delete(body.id);result=state();}
   else if(action==='decision'){decisions.push(body);if(body.auto_approve)settings.auto_approve=true;approvals.find(a=>a.id===body.id).status=body.approve?'completed':'denied';result=state();}
   else if(action==='proposal')result={topology:{version:1,name:'VLAN practice',nodes:[],links:[]},instructions_markdown:'# Exercise',warnings:['Review before import']};
   else throw Error(action);
  }
  await route.fulfill({json:result});
 });
 await page.reload();await page.locator('#open-agent').waitFor({state:'visible'});await page.locator('#open-agent').click();
 await page.locator('#agent-settings').waitFor({state:'visible'});
 assert.equal(await page.locator('#agent-tabs #agent-chat-tab').count(),1);
 assert.equal(await page.locator('#agent-tabs #agent-settings-tab').count(),1);
 assert.equal(await page.locator('#agent-auto-approve').isChecked(),false);
 assert.equal(await page.locator('#agent-deadline').getAttribute('max'),'3600');
 await page.locator('#agent-check').click();await page.waitForFunction(()=>document.querySelector('#agent-check-result').textContent.includes('available'));
 assert.equal(await page.locator('#agent-model').getAttribute('placeholder'),'gpt-oss:20b');
 await page.locator('#agent-model').fill('vision-test');
 await page.locator('#agent-lab-changes').check();await page.locator('#agent-console-input').check();
 await page.locator('#agent-save').click();await page.locator('#agent-chat').waitFor({state:'visible'});
 assert.ok(await page.locator('#agent-intro').isVisible());
 assert.ok((await page.locator('#agent-prompt').getAttribute('placeholder')).startsWith('Create an FRR'));
 const cfg={name:'router.cfg',mimeType:'text/plain',buffer:Buffer.from('router ospf\n<img src=x onerror="window.injected=true">')};
 const png={name:'topology.png',mimeType:'image/png',buffer:await page.screenshot()};
 await page.locator('#agent-files').setInputFiles([cfg,png]);
 await page.locator('#agent-attachments summary').nth(1).waitFor();
 await page.locator('#agent-attachments summary').nth(0).click();
 assert.ok((await page.locator('#agent-attachments pre').textContent()).includes('router ospf'));
 assert.equal(await page.evaluate(()=>window.injected),undefined);
 await page.locator('#agent-attachments summary').nth(1).click();
 await page.waitForFunction(()=>document.querySelector('#agent-attachments img')?.naturalWidth>0);
 await page.getByRole('button',{name:'Remove topology.png',exact:true}).click();
 assert.equal(await page.locator('#agent-attachments summary').count(),1);
 await page.locator('#agent-files').setInputFiles(png);await page.locator('#agent-attachments summary').nth(1).waitFor();
 await page.locator('#agent-files').setInputFiles({name:'manual.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF')});
 await page.waitForFunction(()=>document.querySelector('#agent-file-error').textContent.includes('not supported yet'));
 assert.equal(await page.locator('#agent-attachments summary').count(),2,'Rejected files preserve the existing draft');
 await page.locator('#agent-files').setInputFiles({name:'big.txt',mimeType:'text/plain',buffer:Buffer.alloc(65537,65)});
 await page.waitForFunction(()=>document.querySelector('#agent-file-error').textContent.includes('64 KiB'));
 await page.locator('#agent-prompt').fill('Create a VLAN practice lab');await page.locator('#agent-send').click();
 await page.waitForFunction(()=>document.querySelector('#agent-messages').textContent.includes('<img'));
 assert.ok(await page.locator('#agent-intro').isHidden());
 assert.equal(await page.locator('#agent-prompt').getAttribute('placeholder'),'Message your agent…');
 assert.equal(await page.locator('#agent-messages img').count(),0);assert.equal(await page.evaluate(()=>window.injected),undefined);
 assert.equal(sentFiles.size,2);assert.equal(await page.locator('#agent-attachments').isHidden(),true);
 await page.locator('#agent-messages .agent-attachment summary').nth(0).click();
 await page.waitForFunction(()=>document.querySelector('#agent-messages .agent-attachment pre')?.textContent.includes('router ospf'));
 const fileDownload=page.waitForEvent('download');await page.getByRole('button',{name:'Download attachment',exact:true}).click();
 assert.equal((await fileDownload).suggestedFilename(),'router.cfg');
 assert.ok(await page.locator('#agent-progress').isVisible());
 assert.ok((await page.locator('#agent-progress-text').textContent()).includes('Thinking'));
 assert.ok((await page.locator('#agent-progress-text').textContent()).includes('/ 15m 0s'));
 toolError={operation:'preview',message:'links[0]: unexpected endpoints <img src=x onerror="window.injected=true">'};
 await page.locator('#agent-tool-error').waitFor({state:'visible'});
 await page.locator('#agent-tool-error summary').click();
 assert.ok((await page.locator('#agent-tool-error pre').textContent()).includes('links[0]'));
 assert.equal(await page.locator('#agent-tool-error img').count(),0);
 toolError=null;await page.locator('#agent-tool-error').waitFor({state:'hidden'});
 phase='Writing response';await page.waitForFunction(()=>document.querySelector('#agent-progress-text').textContent.includes('Writing response'));
 assert.equal(settings.lab_changes,true);assert.equal(settings.console_input,true);
 approvals=[{id:'approval1',operation:'console-send',status:'pending',arguments:{node_id:'r1',input:'show version\r'},context:{console_output:'<img src=x onerror="window.injected=true">'},expires_at:Date.now()/1000+60}];
 await page.locator('#agent-pending').waitFor({state:'visible'});
 assert.ok(await page.locator('#agent-chat').isVisible());
 assert.ok(await page.locator('#agent-operations').isHidden());
 await page.locator('#agent-approval-dialog').waitFor({state:'visible'});
 assert.equal(await page.locator('#agent-approval-dialog img').count(),0);
 assert.ok((await page.locator('#agent-approval-detail').textContent()).includes('show version\\r'));
 await page.locator('#agent-approval-later').click();
 await page.waitForTimeout(1200);
 assert.ok(await page.locator('#agent-approval-dialog').isHidden());
 await page.locator('#agent-pending').click();
 await page.getByRole('button',{name:'Approve once',exact:true}).waitFor();
 assert.ok((await page.locator('#agent-progress-text').textContent()).includes('Waiting for your approval'));
 assert.equal(await page.locator('#agent-approvals img').count(),0);
 assert.ok((await page.locator('#agent-approvals pre').textContent()).includes('show version\\r'));
 await page.getByRole('button',{name:'Approve once',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#agent-approvals').textContent.includes('completed'));
 assert.deepEqual(decisions,[{id:'approval1',approve:true}]);
 approvals=[{...approvals[0],id:'approval2',status:'pending'}];
 await page.locator('#agent-approval-dialog').waitFor({state:'visible'});
 await page.locator('#agent-approval-yolo').check();
 await page.locator('#agent-approval-deny').click();
 await page.waitForFunction(()=>document.querySelector('#agent-approvals').textContent.includes('denied'));
 assert.deepEqual(decisions[1],{id:'approval2',approve:false});
 assert.equal(settings.auto_approve,false,'Deny must not enable YOLO');
 await page.locator('#agent-hide').click();
 approvals=[{...approvals[0],id:'approval3',status:'pending'}];
 await page.locator('#agent-approval-dialog').waitFor({state:'visible'});
 assert.equal(await page.locator('#agent-approval-yolo').isChecked(),false);
 await page.keyboard.press('Escape');
 await page.waitForTimeout(1200);
 assert.ok(await page.locator('#agent-approval-dialog').isHidden());
 approvals=[{...approvals[0],id:'approval4',status:'pending'}];
 await page.locator('#agent-approval-dialog').waitFor({state:'visible'});
 approvals[0].status='expired';
 await page.locator('#agent-approval-dialog').waitFor({state:'hidden'});
 approvals=[{...approvals[0],id:'approval5',status:'pending'}];
 await page.locator('#agent-approval-dialog').waitFor({state:'visible'});
 await page.setViewportSize({width:390,height:750});
 const modalBox=await page.locator('#agent-approval-dialog').boundingBox();
 assert.ok(modalBox.x>=0 && modalBox.x+modalBox.width<=390 && modalBox.height<=750);
 await page.locator('#agent-approval-yolo').check();
 await page.getByRole('button',{name:'Approve and enable YOLO',exact:true}).click();
 await page.locator('#agent-approval-dialog').waitFor({state:'hidden'});
 assert.deepEqual(decisions[2],{id:'approval5',approve:true,auto_approve:true});
 assert.equal(settings.auto_approve,true);
 await page.setViewportSize({width:1280,height:950});
 await page.locator('#open-agent').click();
 await page.waitForFunction(()=>document.querySelector('#agent-mode').textContent==='YOLO');
 await page.locator('#agent-proposals-tab').click();
 await page.getByRole('button',{name:'Review',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.agent-proposal-detail')?.textContent.includes('VLAN practice'));
 await page.waitForTimeout(1100);assert.ok(await page.locator('.agent-proposal-detail').isVisible());
 const downloadEvent=page.waitForEvent('download');await page.getByRole('button',{name:'Download JSON',exact:true}).click();
 assert.equal((await downloadEvent).suggestedFilename(),'exercise.json');
 assert.ok(await page.locator('#agent-send').isDisabled());assert.ok(await page.locator('#agent-new').isDisabled());
 await page.locator('#agent-hide').click();await page.locator('#open-agent').click();assert.equal(count,1);
 await page.reload();await page.locator('#open-agent').click();await page.locator('#agent-chat-tab').click();
 await page.waitForFunction(()=>!document.querySelector('#agent-stop').disabled);assert.equal(count,1);
 await page.locator('#agent-stop').click();await page.waitForFunction(()=>document.querySelector('#agent-stop').disabled);assert.equal(stop,1);assert.ok(await page.locator('#agent-progress').isHidden());
 await page.locator('#agent-settings-tab').click();await page.locator('#agent-auto-approve').check();
 await page.locator('#agent-deadline').fill('1800');await page.locator('#agent-save').click();
 await page.waitForFunction(()=>document.querySelector('#agent-mode').textContent==='YOLO');
 assert.equal(settings.auto_approve,true);assert.equal(settings.turn_timeout,1800);
 approvals=[{id:'automatic',operation:'start-node',status:'completed',auto_approved:true,arguments:{node_id:'r1'},context:{}}];
 await page.waitForFunction(()=>document.querySelector('#agent-approvals').textContent.includes('completed · YOLO'));
 assert.equal(await page.locator('#agent-approvals').getByRole('button',{name:'Approve once',exact:true}).count(),0);
 assert.ok(await page.locator('#agent-approval-dialog').isHidden());
 await page.locator('#agent-chat-tab').click();
 const chatHeight=await page.locator('#agent-messages').evaluate(e=>e.clientHeight);
 approvals=Array.from({length:12},(_,i)=>({...approvals[0],id:'automatic'+i}));
 proposals=['a'.repeat(32),'b'.repeat(32)];
 messages=[{role:'assistant',text:'Protocol evidence\n'.repeat(120)}];
 await page.waitForFunction(()=>document.querySelector('#agent-operations-tab').textContent.includes('12'));
 assert.equal(await page.locator('#agent-messages').evaluate(e=>e.clientHeight),chatHeight);
 assert.ok(chatHeight>200,'Conversation retains usable space with many operations and proposals');
 await page.locator('#agent-prompt').fill('Keep this draft');
 await page.locator('#agent-messages').evaluate(e=>e.scrollTop=80);
 await page.locator('#agent-operations-tab').click();
 assert.ok(await page.locator('#agent-operations').evaluate(e=>e.clientHeight>300));
 assert.ok(await page.locator('#agent-stop').isVisible());
 await page.locator('#agent-chat-tab').click();
 assert.equal(await page.locator('#agent-prompt').inputValue(),'Keep this draft');
 assert.equal(await page.locator('#agent-messages').evaluate(e=>e.scrollTop),80);
 await page.locator('#agent-chat-tab').focus();await page.keyboard.press('ArrowRight');
 assert.equal(await page.locator('#agent-operations-tab').getAttribute('aria-selected'),'true');
 await page.keyboard.press('End');assert.ok(await page.locator('#agent-settings').isVisible());
 await page.keyboard.press('Home');assert.ok(await page.locator('#agent-chat').isVisible());
 const panel=page.locator('#agent-panel');await page.locator('#agent-move').focus();const before=await panel.boundingBox();await page.keyboard.press('ArrowRight');assert.equal((await panel.boundingBox()).x,before.x+10);
 await page.locator('#agent-maximize').click();assert.ok(await panel.evaluate(e=>e.classList.contains('maximized')));
 await page.locator('#agent-files').setInputFiles(cfg);await page.locator('#agent-attachments summary').waitFor();
 await page.locator('#agent-new').click();await page.waitForFunction(()=>!document.querySelector('#agent-messages').textContent);
 assert.ok(await page.locator('#agent-intro').isVisible());
 assert.ok((await page.locator('#agent-prompt').getAttribute('placeholder')).startsWith('Create an FRR'));
 assert.ok(await page.locator('#agent-attachments').isHidden());
 await page.locator('#agent-history-tab').click();
 const old=page.locator('.agent-history-card[data-id="'+ 'c'.repeat(32)+'"]');
 await old.getByRole('button',{name:'Rename',exact:true}).click();
 await old.getByRole('textbox',{name:'Conversation title'}).fill('OSPF <img src=x onerror="window.injected=true">');
 await old.getByRole('button',{name:'Save title'}).click();
 await page.waitForFunction(()=>document.querySelector('.agent-history-card strong')?.textContent.startsWith('OSPF'));
 assert.equal(await page.locator('#agent-history-list img').count(),0);
 const downloadHistory=page.waitForEvent('download');await old.getByRole('button',{name:'Download transcript'}).click();
 assert.equal((await downloadHistory).suggestedFilename(),'conversation-'+ 'c'.repeat(32)+'.md');
 await old.getByRole('button',{name:'Open',exact:true}).click();
 await page.locator('#agent-chat').waitFor({state:'visible'});
 assert.ok((await page.locator('#agent-messages').textContent()).includes('Protocol evidence'));
 assert.ok(await page.locator('#agent-intro').isHidden());
 assert.equal(await page.locator('#agent-prompt').getAttribute('placeholder'),'Message your agent…');
 assert.equal(await page.locator('#agent-prompt').inputValue(),'Keep this draft');
 assert.ok((await page.locator('#agent-attachments summary').textContent()).includes('router.cfg'));
 assert.equal(count,1,'Opening history must not submit another turn');
 // Lab exports use the selected browser conversation, without changing its session or topology.
 await page.locator('#export').evaluate(e=>e.click());
 assert.equal(await page.locator('#export-agent').isChecked(),false);
 await page.locator('#export-agent').check();
 const jsonDownload=page.waitForEvent('download');await page.locator('#export-json').click();
 const exported=JSON.parse(require('node:fs').readFileSync(await (await jsonDownload).path(),'utf8'));
 assert.equal(exported.agent_history.format,'weblab-agent-transcript');
 assert.equal(exported.agent_history.title,historyRecords.get(conversationId).title);
 assert.equal(await page.evaluate(()=>Object.hasOwn(topology,'agent_history')),false);
 assert.ok(!JSON.stringify(exported).includes('browser-capability'));
 let zipRequest;
 await page.route('**/api/export',async route=>{zipRequest=route.request().postDataJSON();await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'ZIP fixture response'})});});
 await page.locator('#export').evaluate(e=>e.click());await page.locator('#export-zip').click();
 await page.waitForFunction(()=>document.querySelector('#export-message').textContent==='ZIP fixture response');
 assert.deepEqual(zipRequest.agent_history,exported.agent_history);
 await page.locator('#export-agent').uncheck();await page.locator('#cancel-export').click();
 await page.unroute('**/api/export');
 await page.locator('#agent-history-tab').click();assert.ok(await old.getByRole('button',{name:'Delete',exact:true}).isDisabled());
 await page.locator('#agent-chat-tab').click();await page.locator('#agent-new').click();await page.locator('#agent-history-tab').click();
 page.once('dialog',dialog=>dialog.accept());await old.getByRole('button',{name:'Delete',exact:true}).click();
 await old.waitFor({state:'detached'});
 await page.locator('#agent-settings-tab').click();
 assert.equal(await page.locator('#agent-remember').isChecked(),false);
 await page.locator('#agent-remember').check();
 assert.equal(await page.evaluate(()=>localStorage.getItem('weblab.agent.session.v1')),'browser-capability');
 // Clearing tab storage simulates reopening in a fresh tab of the remembered browser.
 await page.evaluate(()=>sessionStorage.clear());await page.reload();
 await page.locator('#open-agent').click();await page.locator('#agent-settings-tab').click();
 assert.equal(await page.locator('#agent-remember').isChecked(),true);
 await page.locator('#agent-remember').uncheck();
 assert.equal(await page.evaluate(()=>localStorage.getItem('weblab.agent.session.v1')),null);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('weblab.agent.session.v1')),'browser-capability');
 await page.locator('#agent-provider').selectOption('openai');
 assert.ok(await page.locator('#agent-openai-auth').isVisible());
 assert.equal(await page.locator('#agent-model').getAttribute('placeholder'),'gpt-5.6-sol');
 assert.equal(await page.locator('#agent-model').inputValue(),'gpt-5.6-sol');
 assert.ok(await page.locator('#agent-login').isDisabled());
 await page.locator('#agent-save').click();await page.waitForFunction(()=>!document.querySelector('#agent-login').disabled);
 assert.equal(settings.provider,'openai');
 assert.ok(await page.locator('#agent-settings').isVisible());
 assert.ok(await page.locator('#agent-endpoint').isDisabled());
 await page.locator('#agent-login').click();await page.locator('#agent-device-code').waitFor({state:'visible'});
 assert.equal(await page.locator('#agent-device-code strong').textContent(),'ABCD-1234');
 assert.equal(await page.locator('#agent-device-code a').getAttribute('href'),'https://auth.openai.com/codex/device');
 assert.equal(await page.locator('#agent-device-code a').getAttribute('target'),'_blank');
 assert.ok(await page.locator('#agent-save').isDisabled());
 await page.locator('#agent-login-cancel').click();await page.locator('#agent-device-code').waitFor({state:'hidden'});
 await page.locator('#agent-account').click();await page.waitForFunction(()=>document.querySelector('#agent-auth-status').textContent.includes('Signed in'));
 assert.equal(await page.locator('#agent-model-list option').getAttribute('value'),'gpt-5.6-sol');
 await page.locator('#agent-logout').click();await page.waitForFunction(()=>document.querySelector('#agent-auth-status').textContent.includes('signed-out'));
 await page.locator('#agent-provider').selectOption('ollama');
 assert.ok(await page.locator('#agent-openai-auth').isHidden());
 await page.locator('#agent-save').click();
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);
 const rect=await panel.boundingBox();assert.ok(rect.x>=0 && rect.x+rect.width<=391);
 await require('./toolbar_ui.cjs').scrollingTabs(page,'#agent-tabs');
 await page.locator('#agent-settings-tab').click();assert.ok(await page.locator('#agent-settings').isVisible());
 await page.locator('#agent-chat-tab').click();assert.ok(await page.locator('#agent-chat').isVisible());
 assert.equal(await page.locator('#agent-prompt').evaluate(e=>e.scrollWidth<=e.clientWidth+1),true);
 assert.deepEqual(errors,[]);console.log('PASS: disabled mode, settings, safe output, session reload, Stop, window geometry and phone viewport');
 } finally {if(browser)await browser.close();fixture.stdin.end('\n');}
})().catch(e=>{console.error(e);process.exitCode=1;});
