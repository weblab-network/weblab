// Opt-in native test. Uses the locally installed VEOS image and Alpine only.
// VEOS_IMAGE=/absolute/path/vEOS64-lab-4.36.1F.qcow2 PLAYWRIGHT_MODULE=... node tests/veos_native.cjs
// Creates/stops only disposable nodes; never imports over the user's lab.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const path=require('node:path');
if(!process.env.VEOS_IMAGE)throw new Error('Set VEOS_IMAGE to a locally installed VEOS QCOW2 file');
const fixture=spawn('python3',['-u','-c',`
import os,sys,tempfile,threading,hashlib
from pathlib import Path
import lab_server, vios
source=Path(os.environ['VEOS_IMAGE']).resolve()
original=hashlib.file_digest(source.open('rb'),'sha256').digest()
aboot=source.parent/vios.ABOOT_IMAGE
aboot_hash=hashlib.file_digest(aboot.open('rb'),'sha256').digest()
with tempfile.TemporaryDirectory(prefix='veos-native-') as tmp:
 root=Path(tmp);images=root/'images';images.mkdir();(images/source.name).symlink_to(source);(images/aboot.name).symlink_to(aboot)
 lab=lab_server.Lab(root/'data',images)
 server=lab_server.ThreadingHTTPServer(('127.0.0.1',0),lab_server.Handler)
 server.daemon_threads=True;server.lab=lab;server.stopping=threading.Event()
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  lab.save({'name':'Disposable VEOS forwarding','nodes':[
   {'id':'veos','name':'VEOS','type':'switch','image':source.name,'iol_id':920,'ethernet':16,'x':450,'y':240},
   {'id':'pc1','iol_id':921,'name':'PC1','type':'pc','image':'alpine:latest','ipv4':'192.0.2.1/24','x':180,'y':440},
   {'id':'pc2','iol_id':922,'name':'PC2','type':'pc','image':'alpine:latest','ipv4':'192.0.2.12/24','x':680,'y':440}],
   'links':[{'id':'p1','a':{'node':'veos','port':'Ethernet1'},'b':{'node':'pc1','port':'eth0'}},
            {'id':'p12','a':{'node':'veos','port':'Ethernet15'},'b':{'node':'pc2','port':'eth0'}}]})
  lab.start_all()
  print('http://127.0.0.1:'+str(server.server_port),flush=True)
  sys.stdin.readline()
 finally:
  server.stopping.set();lab.stop_all();server.shutdown();server.server_close();thread.join();lab.file_lock.close()
  assert hashlib.file_digest(source.open('rb'),'sha256').digest()==original,'Base image changed'
  assert hashlib.file_digest(aboot.open('rb'),'sha256').digest()==aboot_hash,'Aboot changed'
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
  await connect(first,'veos');
  assert.deepEqual(await first.evaluate(()=>nodePorts(nodeById('veos'))),['Management1',...Array.from({length:15},(_,i)=>`Ethernet${i+1}`)]);
  await until(first,'veos','localhost login:',240000);
  await send(first,'veos','admin');await until(first,'veos','localhost>');
  await first.evaluate(()=>consoleSessions.get('veos').terminal.reset());
  await send(first,'veos','zerotouch disable');
  await until(first,'veos','localhost login:',240000);
  await send(first,'veos','admin');await until(first,'veos','localhost>');
  await send(first,'veos','enable');await until(first,'veos','localhost#');
  await send(first,'veos','terminal length 0');await until(first,'veos','Pagination disabled');
  console.log('PASS Arista boot, serial login, ZTP disable/reboot and privileged CLI');
  await connect(second,'veos');await until(second,'veos','Pagination disabled');
  await first.locator('#lock-console').click();
  await first.waitForFunction(()=>consoleSessions.get('veos').lockMine);
  await second.waitForFunction(()=>consoleSessions.get('veos').locked&&!consoleSessions.get('veos').lockMine);
  assert.equal(await second.evaluate(()=>consoleSessions.get('veos').terminal.options.disableStdin),true);
  await send(first,'veos','show version');await until(first,'veos','Software image version:');await until(second,'veos','Software image version:');
  await first.locator('#lock-console').click();await second.waitForFunction(()=>!consoleSessions.get('veos').locked);
  await send(second,'veos','show interfaces status');await until(first,'veos','Et15');
  await send(first,'veos','configure terminal');await until(first,'veos','localhost\\(config\\)#');
  await send(first,'veos','interface Ethernet1-15');
  await send(first,'veos','spanning-tree portfast');
  await send(first,'veos','end');
  console.log('PASS shared browser output/input and input locks, Ethernet1 through Ethernet15');
  await connect(first,'pc1');await until(first,'pc1','/ #');await send(first,'pc1','ping -c 3 -W 2 192.0.2.12');
  await until(first,'pc1',', 0% packet loss',90000);
  await connect(second,'pc2');await until(second,'pc2','/ #');await send(second,'pc2','ping -c 2 -W 2 192.0.2.1');await until(second,'pc2',', 0% packet loss',15000);
  console.log('PASS bidirectional Alpine ICMP through application links on Ethernet1 and Ethernet15');
  await send(first,'veos','configure terminal');await send(first,'veos','hostname AristaSaved');await send(first,'veos','end');
  await send(first,'veos','write memory');await until(first,'veos','Copy completed successfully');
  await first.evaluate(()=>api('/api/lab/stop','POST',{}));
  await first.evaluate(()=>api('/api/lab/start','POST',{}));
  await until(first,'veos','AristaSaved login:',240000);
  await send(first,'veos','admin');await until(first,'veos','AristaSaved>');
  console.log('PASS saved hostname persists through application stop/start');
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
