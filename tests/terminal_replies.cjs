// Terminal status queries must not inject stale or duplicate replies into a shared CLI.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const path=require('node:path');
const fixture=spawn('python3',['-u','-c',`
import sys
sys.path.insert(0,'tests')
from test_lab import LabTests
test=LabTests();test.setUp()
try:
 image=test.root/'fake.bin'
 image.write_text(image.read_text().split('while True:')[0] + '''
nl=bytes([13,10]);esc=bytes([27])
os.write(1,b'READY'+nl)
replies=0
while True:
 data=os.read(0,65536)
 if not data:break
 if b'QUERY' in data:os.write(1,esc+b'[6n')
 if esc+b'[' in data and b'R' in data:
  replies+=1;os.write(1,('REPLY-'+str(replies)).encode()+nl)
''')
 test.lab.start('r1');print('http://127.0.0.1:'+str(test.port),flush=True);sys.stdin.readline()
finally:test.tearDown()
`],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data',d=>log+=d);
(async()=>{
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{
   let output='';fixture.stdout.on('data',d=>{output+=d;if(output.includes('\n'))resolve(output.trim());});
   fixture.once('exit',code=>reject(new Error(`Fixture exited ${code}: ${log}`)));
  });
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const first=await browser.newPage(),second=await browser.newPage();
  const errors=[];
  async function connect(p){p.on('pageerror',e=>errors.push(e.message));await p.goto(base);await p.waitForFunction(()=>loaded);await p.evaluate(()=>openConsole('r1'));await p.waitForFunction(()=>consoleSessions.get('r1').lockReady&&consoleSessions.get('r1').outputQueue.length===0);}
  const output=p=>p.evaluate(()=>{const b=consoleSessions.get('r1').terminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n');});
  const query=p=>p.evaluate(()=>sendConsoleInput(consoleSessions.get('r1'),'QUERY\r'));
  async function until(p,n){await p.waitForFunction(n=>{const b=consoleSessions.get('r1').terminal.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n').includes('REPLY-'+n);},n);}
  await connect(first);await query(first);await until(first,1);
  await connect(second);await until(second,1);
  await second.waitForFunction(()=>consoleSessions.get('r1').outputQueue.length===0);
  await second.waitForTimeout(150);
  assert.ok(!(await output(first)).includes('REPLY-2'),'History must not answer the old query');
  await query(second);await until(second,2);await second.waitForTimeout(150);
  assert.ok(!(await output(first)).includes('REPLY-3'),'Only one station should answer a live query');
  await second.locator('#lock-console').click();
  await second.waitForFunction(()=>consoleSessions.get('r1').lockMine&&consoleSessions.get('r1').terminalResponder);
  await first.waitForFunction(()=>!consoleSessions.get('r1').terminalResponder);
  await query(second);await until(first,3);
  await second.locator('#lock-console').click();await second.waitForFunction(()=>!consoleSessions.get('r1').locked);
  await first.close();await second.waitForFunction(()=>consoleSessions.get('r1').terminalResponder);
  await query(second);await until(second,4);
  assert.deepEqual(errors,[]);
  console.log('PASS: history suppresses terminal replies; one live responder follows input ownership and disconnects');
 }finally{
  if(browser)await browser.close();const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
 }
})().catch(e=>{console.error(e,log);process.exitCode=1;});
