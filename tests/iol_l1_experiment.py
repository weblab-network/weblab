"""Opt-in IOL L1 experiment. Requires root, unshare/mount and local images/iourc.
Run: python3 tests/iol_l1_experiment.py
Uses private mount views of /tmp/netio0, /tmp/netl10 and /etc/hosts, disposable
NVRAM and no application server. Evidence is retained in /tmp/iol-l1-pair-*.
Only tested with the two named IOL 17.18.02 images. Not a general IOL adapter.
"""
import os,sys,tempfile,subprocess,pty,time,select,signal,socket,struct,threading,re,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if '--isolated' not in sys.argv:
 if os.geteuid()!=0:
  raise SystemExit('This opt-in experiment requires root for a private mount namespace')
 for target in ('/tmp/netio0','/tmp/netl10'):
  Path(target).mkdir(exist_ok=True)
 raise SystemExit(subprocess.run(['unshare','--mount','--propagation','private',sys.executable,'-u',str(Path(__file__).resolve()),'--isolated']).returncode)
if os.readlink('/proc/self/ns/mnt')==os.readlink(f'/proc/{os.getppid()}/ns/mnt'):
 raise SystemExit('Refusing to mount over host paths without a separate mount namespace')
sys.path.insert(0,str(ROOT))
import initial_config,link_fabric
root=Path(tempfile.mkdtemp(prefix='iol-l1-pair-'));print('ROOT',root,flush=True)
for local,target in [('netio','/tmp/netio0'),('l1','/tmp/netl10')]:
 (root/local).mkdir();subprocess.run(['mount','--bind',str(root/local),target],check=True)
hosts=root/'hosts';hosts.write_text('\n'.join(l for l in Path('/etc/hosts').read_text().splitlines() if 'xml.cisco.com' not in l)+'\n');subprocess.run(['mount','--bind',str(hosts),'/etc/hosts'],check=True)
records=[{'id':'main','a':{'node':'S','interface':'0/0','id':930,'port':0},'b':{'node':'R','interface':'0/0','id':931,'port':0}},
 {'id':'control','a':{'node':'S','interface':'1/2','id':930,'port':0x21},'b':{'node':'R','interface':'0/1','id':931,'port':0x10}}]
fabric=link_fabric.LinkFabric(root,Path('/tmp/netio0'),records,{930,931})
controls={};nodes={};lock=threading.Lock();stop=threading.Event();failures=[];worker=None
for record in records:
 number=fabric.links[record['id']]['id_number'];s=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);s.bind(f'/tmp/netl10/L1{number}');s.setblocking(False)
 for side,source_port in [('a',0),('b',16)]:
  end=record[side];controls[(record['id'],side)]={'sock':s,'target':f"/tmp/netl10/L1{end['id']}",'data':struct.pack('>HHBBBB',end['id'],number,end['port'],source_port,3,0),'enabled':False}

def pump():
 last=0
 try:
  while not stop.is_set():
   if time.monotonic()-last>=.5:
    last=time.monotonic()
    for ctl in controls.values():
     if ctl['enabled']:
      try:ctl['sock'].sendto(ctl['data'],ctl['target'])
      except (FileNotFoundError,ConnectionRefusedError):pass
   for fd in select.select([n['fd'] for n in nodes.values()],[],[],.05)[0]:
    try:data=os.read(fd,65536)
    except OSError:continue
    with lock:
     next(n for n in nodes.values() if n['fd']==fd)['output']+=data
 except Exception as e:failures.append(repr(e))

def command(name,cmd,timeout=8):
 n=nodes[name]
 with lock:start=len(n['output'])
 os.write(n['fd'],cmd.encode()+b'\r')
 until=time.monotonic()+timeout
 while time.monotonic()<until:
  with lock:text=n['output'][start:].decode(errors='replace').replace('\r','')
  if text.rstrip().endswith(name+'#'):return text
  if n['p'].poll() is not None:raise AssertionError('Node exited: '+name)
  if failures:raise AssertionError(failures)
  time.sleep(.05)
 raise AssertionError('Console timeout '+name+' '+repr(text[-500:]))

def status(name,port):
 text=command(name,'show interfaces Ethernet'+port)
 m=re.search(r'Ethernet'+re.escape(port)+r' is ([^\n]+)',text)
 assert m,text
 return m.group(1)

def waitstate(name,port,up,timeout=20):
 started=time.monotonic();expected='up, line protocol is up' if up else 'down, line protocol is down'
 while time.monotonic()-started<timeout:
  result=status(name,port)
  if result.startswith(expected):
   print('STATE',name,port,result,'after',round(time.monotonic()-started,2),'s',flush=True);return
  time.sleep(.5)
 raise AssertionError((name,port,expected,result))

def ping(name,address,ok,attempts=1):
 for attempt in range(attempts):
  text=command(name,f'ping {address} repeat 3 timeout 1',timeout=10)
  print('PING',name,address,re.findall(r'Success rate[^\n]+',text),flush=True)
  if f'Success rate is {100 if ok else 0} percent' in text:return
  time.sleep(1)
 raise AssertionError(text)

try:
 for name,num,image,port,addr in [('S',930,'cisco_iol-l2-17.18.02.bin','1/2',1),('R',931,'cisco_iol-17.18.02.bin','0/1',2)]:
  cwd=root/name;cwd.mkdir()
  lines=[]
  for record in records:
   side='a' if name=='S' else 'b';end=record[side];peer=fabric.peers[(name,end['interface'])]
   lines.append(f"{num}:{end['interface']}@{socket.gethostname()} {peer['id']}:{peer['port']&15}/{peer['port']>>4}@{socket.gethostname()}")
  (cwd/'NETMAP').write_text('\n'.join(lines)+'\n')
  routed=' no switchport\n' if name=='S' else ''
  cfg=f'hostname {name}\nno service config\nno ip domain lookup\nno logging console\ninterface Ethernet0/0\n{routed} ip address 198.18.1.{addr} 255.255.255.0\n no shutdown\ninterface Ethernet{port}\n{routed} ip address 198.18.2.{addr} 255.255.255.0\n no shutdown\nline con 0\n privilege level 15\n exec-timeout 0 0\nend\n'
  initial_config.seed_iol(cwd/f'nvram_{num:05d}',cfg)
  master,slave=pty.openpty();p=subprocess.Popen([str(ROOT/'images'/image),'-l','-e','2','-s','0','-m','1024','-n','64',str(num)],cwd=cwd,stdin=slave,stdout=slave,stderr=slave,start_new_session=True,env={**os.environ,'IOURC':str(ROOT/'images'/'iourc')});os.close(slave)
  nodes[name]={'p':p,'fd':master,'output':b''}
 worker=threading.Thread(target=pump);worker.start();time.sleep(8)
 for name in nodes:
  command(name,'');command(name,'terminal length 0')
 for name in nodes:waitstate(name,'0/0',False)
 print('PHASE enable L1 on both links',flush=True)
 for ctl in controls.values():ctl['enabled']=True
 for name in nodes:waitstate(name,'0/0',True)
 waitstate('S','1/2',True);waitstate('R','0/1',True)
 ping('S','198.18.1.2',True,3);ping('S','198.18.2.2',True,3)
 print('PHASE silent loss A to B, heartbeat continues',flush=True)
 fabric.set_blocked('main',True,False);time.sleep(15)
 for name in nodes:waitstate(name,'0/0',True)
 ping('S','198.18.1.2',False);ping('S','198.18.2.2',True)
 fabric.set_blocked('main',False,False);ping('S','198.18.1.2',True,3)
 for side,name in [('a','S'),('b','R')]:
  print('PHASE unplug only',name,flush=True)
  controls[('main',side)]['enabled']=False;fabric.set_carrier('main',side,'down')
  waitstate(name,'0/0',False)
  waitstate('R' if name=='S' else 'S','0/0',True)
  waitstate('S','1/2',True);waitstate('R','0/1',True)
  ping('S','198.18.1.2',False);ping('S','198.18.2.2',True)
  print('PHASE reconnect',name,flush=True)
  controls[('main',side)]['enabled']=True;fabric.set_carrier('main',side,'up')
  waitstate(name,'0/0',True);ping('S','198.18.1.2',True,3)
 print('PASS IOL L2 and L3 carrier control, data-loss independence, mixed port numbering, unaffected second cable and forwarding recovery',flush=True)
finally:
 stop.set()
 if worker:worker.join(timeout=3)
 for name,n in nodes.items():
  if n['p'].poll() is None:
   os.killpg(n['p'].pid,signal.SIGTERM)
   try:n['p'].wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(n['p'].pid,signal.SIGKILL);n['p'].wait()
  os.close(n['fd']);(root/(name+'.console.log')).write_bytes(n['output'])
 fabric.close()
 for s in {ctl['sock'] for ctl in controls.values()}:s.close()
 print('CLEANED processes; evidence',root,flush=True)
