// Opt-in native test. Uses the locally installed EXOS image and Alpine only.
// EXOS_IMAGE=/absolute/path/EXOS-VM_33.1.1.31.qcow2 PLAYWRIGHT_MODULE=... node tests/exos_native.cjs
// Creates/stops only disposable nodes; never imports over the user's lab.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const path=require('node:path');
if(!process.env.EXOS_IMAGE)throw new Error('Set EXOS_IMAGE to a locally installed EXOS QCOW2 file');
const fixture=spawn('python3',['-u','-c',`
import os,sys,tempfile,threading,hashlib
from pathlib import Path
import lab_server
source=Path(os.environ['EXOS_IMAGE']).resolve()
original=hashlib.sha256(source.read_bytes()).digest()
with tempfile.TemporaryDirectory(prefix='exos-native-') as tmp:
 root=Path(tmp);images=root/'images';images.mkdir();(images/source.name).symlink_to(source)
 lab=lab_server.Lab(root/'data',images)
 server=lab_server.ThreadingHTTPServer(('127.0.0.1',0),lab_server.Handler)
 server.daemon_threads=True;server.lab=lab;server.stopping=threading.Event()
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  lab.save({'name':'Disposable EXOS forwarding','nodes':[
   {'id':'exos','name':'EXOS','type':'switch','image':source.name,'iol_id':920,'ethernet':13,'x':450,'y':240},
   {'id':'pc1','name':'PC1','type':'pc','image':'alpine:latest','iol_id':921,'ipv4':'192.0.2.1/24','x':180,'y':440},
   {'id':'pc2','name':'PC2','type':'pc','image':'alpine:latest','iol_id':922,'ipv4':'192.0.2.12/24','x':680,'y':440}],
   'links':[{'id':'p1','a':{'node':'exos','port':'1'},'b':{'node':'pc1','port':'eth0'}},
            {'id':'p12','a':{'node':'exos','port':'12'},'b':{'node':'pc2','port':'eth0'}}]})
  lab.start_all()
  print('http://127.0.0.1:'+str(server.server_port),flush=True)
  sys.stdin.readline()
 finally:
  server.stopping.set();lab.stop_all();server.shutdown();server.server_close();thread.join();lab.file_lock.close()
  assert hashlib.sha256(source.read_bytes()).digest()==original,'Base image changed'
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data',d=>log+=d);
(async()=>{
 let browser; const pages=[];
 try{
  const url=await new Promise((resolve,reject)=>{
   let output='';fixture.stdout.on('data',d=>{output+=d;if(output.includes('\n'))resolve(output.trim());});
   fixture.once('exit',code=>reject(new Error(`Fixture exited ${code}: ${log}`)));
  });
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const first=await browser.newPage({viewport:{width:1440,height:1000}});
  const otherContext=await browser.newContext({viewport:{width:800,height:1100},hasTouch:true});
  const second=await otherContext.newPage();pages.push(first,second);const errors=[];
  for(const p of [first,second]){p.on('pageerror',e=>errors.push(e.message));await p.goto(url);await p.waitForFunction(()=>loaded);}
  async function connect(p,id){await p.evaluate(id=>openConsole(id),id);await p.waitForFunction(id=>consoleSessions.get(id)?.lockReady,id);}
  async function until(p,id,pattern,timeout=180000){await p.waitForFunction(({id,pattern})=>{
   const b=consoleSessions.get(id)?.terminal.buffer.active;if(!b)return false;
   return new RegExp(pattern).test(Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n'));
  },{id,pattern},{timeout});}
  async function send(p,id,text){await p.evaluate(({id,text})=>sendConsoleInput(consoleSessions.get(id),text+'\r'),{id,text});}
  await connect(first,'exos');
  assert.deepEqual(await first.evaluate(()=>nodePorts(nodeById('exos'))),['Mgmt',...Array.from({length:12},(_,i)=>String(i+1))]);
  await until(first,'exos','Authentication Service \\(AAA\\).*available|EXOS-VM login:');
  await send(first,'exos','admin');await until(first,'exos','password:');await send(first,'exos','');
  await until(first,'exos','\\[y/N/q\\]');await send(first,'exos','q');
  await until(first,'exos','EXOS-VM\\.\\d+ #');
  await send(first,'exos','disable cli paging');await until(first,'exos','EXOS-VM\\.2 #');
  console.log('PASS EXOS serial boot, admin login and first-run prompts');
  await connect(second,'exos');await until(second,'exos','EXOS-VM\\.2 #');
  await first.locator('#lock-console').click();
  await first.waitForFunction(()=>consoleSessions.get('exos').lockMine);
  await second.waitForFunction(()=>consoleSessions.get('exos').locked&&!consoleSessions.get('exos').lockMine);
  assert.equal(await second.evaluate(()=>consoleSessions.get('exos').terminal.options.disableStdin),true);
  await send(first,'exos','show version');await until(first,'exos','33\\.1\\.1');await until(second,'exos','33\\.1\\.1');
  await until(first,'exos','EXOS-VM\\.3 #');
  await first.locator('#lock-console').click();
  await second.waitForFunction(()=>!consoleSessions.get('exos').locked);
  await send(second,'exos','show ports information');await until(first,'exos','MACLearning');
  console.log('PASS browser consoles share output, enforce input lock and accept commands from either station');
  await connect(first,'pc1');await until(first,'pc1','/ #');await send(first,'pc1','ping -c 3 -W 2 192.0.2.12');
  await until(first,'pc1',', 0% packet loss',90000);
  await connect(second,'pc2');await until(second,'pc2','/ #');await send(second,'pc2','ping -c 2 -W 2 192.0.2.1');await until(second,'pc2',', 0% packet loss',15000);
  console.log('PASS bidirectional Alpine ICMP through EXOS ports 1 and 12');
  assert.deepEqual(errors,[]);
 }catch(error){
  for(const p of pages)console.error(await p.evaluate(()=>Array.from(consoleSessions.values()).map(s=>{const b=s.terminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n').slice(-1800);})));
  throw error;
 }finally{
  if(browser)await browser.close();
  const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
  if(fixture.exitCode!==0)throw new Error('Native fixture cleanup failed: '+log);
 }
})().catch(e=>{console.error(e,log);process.exitCode=1;});
