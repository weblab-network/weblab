// Opt-in IOSv–IOL–Alpine forwarding and carrier test, with disposable state.
// VIOS_IMAGE=/path/cisco_vios-....qcow2 IOL_SWITCH_IMAGE=/path/cisco_iol-l2-....bin
// IOL_LICENSE=/path/iourc PLAYWRIGHT_MODULE=/path/playwright node tests/link_faults_native.cjs
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const path=require('node:path');
for(const key of ['VIOS_IMAGE','IOL_SWITCH_IMAGE','IOL_LICENSE'])if(!process.env[key])throw Error('Set '+key);
const fixture=spawn('python3',['-u','-c',`
import os,sys,tempfile,threading,shutil
from pathlib import Path
import lab_server
with tempfile.TemporaryDirectory(prefix='link-native-') as tmp:
 root=Path(tmp);images=root/'images';images.mkdir()
 for key in ('VIOS_IMAGE','IOL_SWITCH_IMAGE'):
  source=Path(os.environ[key]).resolve();(images/source.name).symlink_to(source)
 shutil.copyfile(os.environ['IOL_LICENSE'],images/'iourc')
 lab=lab_server.Lab(root/'data',images)
 server=lab_server.ThreadingHTTPServer(('127.0.0.1',0),lab_server.Handler)
 server.daemon_threads=True;server.lab=lab;server.stopping=threading.Event()
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  lab.save({'name':'Disposable link faults','nodes':[
   {'id':'r','name':'R1','type':'router','image':Path(os.environ['VIOS_IMAGE']).name,'iol_id':910,'ethernet':2,'x':180,'y':180,
    'startup_config':'hostname R1\\nno service config\\nno ip domain lookup\\ninterface GigabitEthernet0/0\\n ip address 198.18.0.1 255.255.255.0\\n no shutdown\\nline con 0\\n exec-timeout 0 0\\nend\\n'},
   {'id':'s','name':'SW','type':'switch','image':Path(os.environ['IOL_SWITCH_IMAGE']).name,'iol_id':911,'ethernet':1,'x':450,'y':180,
    'startup_config':'hostname SW\\nno service config\\nno ip domain lookup\\ninterface Ethernet0/0\\n switchport mode access\\n no shutdown\\ninterface Ethernet0/1\\n switchport mode access\\n spanning-tree portfast\\n no shutdown\\nend\\n'},
   {'id':'pc','name':'PC','type':'pc','image':'alpine:latest','iol_id':912,'ipv4':'198.18.0.2/24','x':700,'y':180}],
   'links':[{'id':'rs','a':{'node':'r','port':'Gi0/0'},'b':{'node':'s','port':'0/0'}},
            {'id':'sp','a':{'node':'s','port':'0/1'},'b':{'node':'pc','port':'eth0'}}]})
  lab.start_all();print('http://127.0.0.1:'+str(server.server_port),flush=True);sys.stdin.readline()
 finally:
  server.stopping.set();lab.stop_all();server.shutdown();server.server_close();thread.join();lab.file_lock.close()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data',d=>log+=d);
(async()=>{
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{let s='';fixture.stdout.on('data',d=>{s+=d;if(s.includes('\n'))resolve(s.trim());});fixture.once('exit',()=>reject(Error(log)));});
  console.log('Disposable lab started',base);
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1400,height:1000}});page.on('pageerror',e=>console.error('PAGEERROR',e));
  await page.goto(base);await page.waitForFunction(()=>loaded);
  for(const id of ['r','s','pc']){await page.evaluate(id=>openConsole(id),id);await page.waitForFunction(id=>consoleSessions.get(id)?.lockReady,id);}
  const text=id=>page.evaluate(id=>{const b=consoleSessions.get(id).terminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n');},id);
  const send=(id,data)=>page.evaluate(({id,data})=>sendConsoleInput(consoleSessions.get(id),data),{id,data});
  await send('r','\r');await send('s','\r');
  const deadline=Date.now()+300000;
  while(Date.now()<deadline){
    if((await text('r')).includes('R1>')||(await text('r')).includes('R1#'))break;
    await send('r','\r');await page.waitForTimeout(3000);
  }
  console.log('IOSv boot output tail',(await text('r')).slice(-700));
  await send('r','enable\r');await page.waitForTimeout(800);await send('r','terminal length 0\r');
  let seq=0;
  async function ping(success, attempts=1){
   for(let attempt=1;attempt<=attempts;attempt++){
    const mark='DONE_'+(++seq);await send('pc',`ping -c 3 -W 1 198.18.0.1; echo ${mark}\r`);
    await page.waitForFunction(({mark})=>{const b=consoleSessions.get('pc').terminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).some(s=>s===mark);},{mark},{timeout:15000});
    const output=await text('pc'),end=output.lastIndexOf('\n'+mark),result=output.slice(output.lastIndexOf('PING ',end),end);
    console.log(mark,result);
    const expected=success?/, 0% packet loss/:/100% packet loss/;
    if(expected.test(result))return;
    if(attempt===attempts)assert.match(result,expected);
    await page.waitForTimeout(2000);
   }
  }
  // Initial STP/ARP convergence can precede the IOSv console prompt.
  await page.waitForTimeout(10000);await ping(true);
  const fault=async(a,b)=>{const res=await page.request.post(base+'/api/links/rs/traffic',{data:{blocked_a_to_b:a,blocked_b_to_a:b}});assert.equal(res.status(),200,await res.text());};
  for(const [a,b] of [[true,false],[false,true],[true,true]]){await fault(a,b);await ping(false);await fault(false,false);await ping(true);}
  // Verify link status in IOS, not just the QMP acknowledgement.
  async function interfaceState(up){
    await page.evaluate(()=>consoleSessions.get('r').terminal.clear());
    await send('r','show interfaces GigabitEthernet0/0\r');
    const expected=up?'GigabitEthernet0/0 is up, line protocol is up':'GigabitEthernet0/0 is down, line protocol is down';
    await page.waitForFunction(expected=>{const b=consoleSessions.get('r').terminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n').includes(expected);},expected,{timeout:20000});
    console.log(expected);
  }
  await interfaceState(true);
  await page.evaluate(async()=>accept(await api('/api/state')));
  // Horizontal SVG paths have zero-height geometry in Playwright; use the
  // cable's accessible action instead of its path bounding box.
  await page.locator('[data-link=rs]').focus();await page.keyboard.press('Enter');
  await page.locator('[data-link-carrier=a]').click();await page.waitForFunction(()=>!busy&&linkStates.rs.carrier_a==='down');
  await page.waitForTimeout(2000);await interfaceState(false);await ping(false);
  await page.locator('[data-link-carrier=a]').click();await page.waitForFunction(()=>!busy&&linkStates.rs.carrier_a==='up');
  await page.waitForTimeout(3000);await interfaceState(true);
  // Carrier restoration precedes ARP/forwarding convergence. Require a clean
  // three-packet run within a bounded number of attempts after reconnecting.
  await ping(true,5);
  console.log('PASS native IOSv–IOL–Alpine relay forwarding, all frame-loss modes, restore, IOSv carrier down/up and continued consoles');
 }finally{
  if(browser)await browser.close();const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
 }
})().catch(e=>{console.error(e,log);process.exitCode=1;});
