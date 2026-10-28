// Real disposable relay + browser capture, filtering, details, PCAP and window controls.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const fs=require('node:fs');
const path=require('node:path');
const fixtureCode=`
import sys, socket, struct
sys.path.insert(0,'tests')
from test_lab import LabTests
from test_traffic_capture import ARP, TCP, protocol_frames
f=LabTests(); f.setUp()
try:
    f.lab.start('r1'); f.lab.start('r2')
    print('http://127.0.0.1:'+str(f.port),flush=True)
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sender:
        for command in sys.stdin:
            if command.strip() != 'frames': break
            fabric=f.lab.fabric; link=fabric.links['cable']
            frames=[ARP,TCP,TCP[:12]+bytes.fromhex('81000064')+TCP[12:],ARP[:12]+bytes.fromhex('88cc')+b'<script>window.packetInjection=true</script>',protocol_frames()['BGP'],protocol_frames()['DTP']]
            for index, frame in enumerate(frames):
                side='a' if index%2==0 else 'b'
                origin=link[side]
                header=struct.pack('>HHBBH',link['id_number'],origin['id'],0 if side=='a' else 16,origin['port'],0x100)
                sender.sendto(header+frame,str(f.lab.netio/str(link['id_number'])))
finally:
    f.tearDown()
`;
const fixtureArgs=process.env.TRAFFIC_TEST_IMAGE
 ? ['run','--rm','-i','--network','host','--hostname','traffic-test','--add-host','traffic-test:127.0.0.1',
    '--entrypoint','python3','-v',`${__dirname}:/app/tests:ro`,process.env.TRAFFIC_TEST_IMAGE,'-u','-c',fixtureCode]
 : ['-u','-c',fixtureCode];
const fixture=spawn(process.env.TRAFFIC_TEST_IMAGE ? 'docker' : 'python3',fixtureArgs,{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let log='';fixture.stderr.on('data',data=>log+=data);
(async()=>{
 let browser;
 try {
  const url=await new Promise((resolve,reject)=>{let out='';fixture.stdout.on('data',d=>{out+=d;if(out.includes('\n'))resolve(out.trim());});fixture.once('exit',code=>reject(new Error(`Fixture ${code}: ${log}`)));});
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1000},hasTouch:true});
  await page.addInitScript(()=>localStorage.setItem('weblab.theme.v1','light'));
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.waitForFunction(()=>loaded);
  const topologyBefore=(await page.request.get(url+'/api/state').then(r=>r.json())).topology;
  await page.request.post(url+'/api/links/cable/traffic',{data:{blocked_a_to_b:true,blocked_b_to_a:false}});
  await page.evaluate(()=>openLinkActions('cable'));
  await page.locator('#link-monitor').click();
  const panel=page.locator('.traffic-panel');
  await panel.waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelector('.traffic-panel [data-part=status]').textContent.includes('Capturing'));
  const toolbar=panel.locator('[data-part=toolbar]');
  for (const part of ['start','stop','download','protocol','direction','follow']) {
    assert.equal(await panel.locator(`[data-part=${part}]`).evaluate(e=>!!e.closest('[data-part=toolbar]')),true);
  }
  const help=panel.locator('[data-part=help]'), helpButton=help.locator('summary');
  assert.equal(await help.getAttribute('open'),null);
  await helpButton.hover();
  await help.locator('.console-menu-list').waitFor({state:'visible'});
  assert.match(await help.textContent(),/2,000 frames/);
  assert.match(await help.textContent(),/not confirmed guest reception/);
  await page.mouse.move(0,0);
  await help.locator('.console-menu-list').waitFor({state:'hidden'});
  await page.keyboard.press('Tab'); await helpButton.focus();
  await help.locator('.console-menu-list').waitFor({state:'visible'});
  await page.keyboard.press('Escape');
  await help.locator('.console-menu-list').waitFor({state:'hidden'});
  const titleBox=await toolbar.boundingBox(), packetBox=await panel.locator('[data-part=scroll]').boundingBox();
  assert.ok(packetBox.y-titleBox.y-titleBox.height<40,'Only a compact status line separates controls from packets');

  fixture.stdin.write('frames\n');
  await page.waitForFunction(()=>document.querySelectorAll('.traffic-panel tbody tr').length===6);
  assert.equal(await panel.locator('tbody tr').filter({hasText:'blocked'}).count(),3);
  await panel.locator('[data-part=protocol]').selectOption('ARP');
  assert.equal(await panel.locator('tbody tr').count(),1);
  await panel.locator('tbody button').click();
  if (process.env.TRAFFIC_TEST_IMAGE) {
    await page.waitForFunction(()=>document.querySelector('.traffic-detail').textContent.includes('Address Resolution Protocol'));
    assert.equal(await panel.locator('[data-part=protocol] option[value=ETH]').count(),1);
  } else {
    await page.waitForFunction(()=>/needs TShark|Address Resolution Protocol/.test(document.querySelector('.traffic-detail').textContent));
  }
  await panel.locator('[data-part=hex]').click();
  await page.waitForFunction(()=>document.querySelector('.traffic-detail').textContent.includes('10.0.0.1'));
  const divider=panel.locator('[data-part=divider]');
  const detailPane=panel.locator('[data-part=detail-pane]');
  const initialHeight=(await detailPane.boundingBox()).height;
  const handle=await divider.boundingBox();
  await page.mouse.move(handle.x+handle.width/2,handle.y+handle.height/2);
  await page.mouse.down();await page.mouse.move(handle.x+handle.width/2,handle.y-65,{steps:5});await page.mouse.up();
  assert.ok((await detailPane.boundingBox()).height>initialHeight+40);
  await divider.focus();const share=Number(await divider.getAttribute('aria-valuenow'));
  await page.keyboard.press('ArrowDown');assert.equal(Number(await divider.getAttribute('aria-valuenow')),share+5);
  await page.keyboard.press('Home');assert.equal(await divider.getAttribute('aria-valuenow'),'20');
  await page.keyboard.press('End');assert.equal(await divider.getAttribute('aria-valuenow'),'80');
  await page.keyboard.press('ArrowUp');
  await panel.locator('[data-part=protocol]').selectOption('TCP');
  assert.equal(await panel.locator('tbody tr').count(),3);
  await panel.locator('[data-part=direction]').selectOption('b_to_a');
  assert.equal(await panel.locator('tbody tr').count(),1);
  await panel.locator('[data-part=direction]').selectOption('all');
  await panel.locator('[data-part=protocol]').selectOption('VLAN');
  assert.equal(await panel.locator('tbody tr').count(),1);
  assert.match(await panel.locator('tbody').textContent(),/VLAN 100/);
  await panel.locator('[data-part=protocol]').selectOption('LLDP');
  await panel.locator('tbody button').click();
  await page.waitForFunction(()=>document.querySelector('.traffic-detail').textContent.includes('script'));
  assert.equal(await page.evaluate(()=>window.packetInjection),undefined);
  if (process.env.TRAFFIC_TEST_IMAGE) {
    await panel.locator('[data-part=protocol]').selectOption('BGP');
    assert.equal(await panel.locator('tbody tr').count(),1);
    await panel.locator('tbody button').click();await panel.locator('[data-part=decoded]').click();
    await page.waitForFunction(()=>document.querySelector('.traffic-detail').textContent.includes('KEEPALIVE'));
    await panel.locator('[data-part=protocol]').selectOption('DTP');
    await panel.locator('tbody button').click();
    await page.waitForFunction(()=>document.querySelector('.traffic-detail').textContent.includes('Dynamic Trunk Protocol'));
  }
  if(process.env.TRAFFIC_SCREENSHOT) await page.screenshot({path:process.env.TRAFFIC_SCREENSHOT});
  await panel.locator('[data-part=stop]').click();
  await page.waitForFunction(()=>document.querySelector('.traffic-panel [data-part=status]').textContent.includes('Stopped by user'));
  const dl=page.waitForEvent('download');await panel.locator('[data-part=download]').click();
  const data=fs.readFileSync(await (await dl).path());
  assert.equal(data.readUInt32LE(0),0xa1b2c3d4);
  assert.equal(data.readUInt32LE(20),1);
  let count=0,pos=24;
  while(pos<data.length){const size=data.readUInt32LE(pos+8);assert.equal(size,data.readUInt32LE(pos+12));pos+=16+size;count++;}
  assert.equal(count,6);assert.equal(pos,data.length); // Filters did not change PCAP.
  const before=await panel.boundingBox();await panel.locator('[data-part=resize]').focus();await page.keyboard.press('ArrowLeft');
  const after=await panel.boundingBox();assert.ok(after.width<before.width);
  await page.setViewportSize({width:390,height:740});
  const small=await panel.boundingBox();assert.ok(small.x>=0 && small.width<=390);
  for(const part of ['move','help','maximize','close']) {
    const box=await panel.locator(`[data-part=${part}]`).boundingBox();
    assert.ok(box.x>=small.x && box.x+box.width<=small.x+small.width,'Window controls stay inside the narrow title bar');
  }
  await helpButton.tap();
  await help.locator('.console-menu-list').waitFor({state:'visible'});
  await page.waitForFunction(()=>{const b=document.querySelector('.traffic-help .console-menu-list').getBoundingClientRect();return b.x>=0 && b.right<=innerWidth;});
  const helpBox=await help.locator('.console-menu-list').boundingBox();
  assert.ok(helpBox.x>=0 && helpBox.x+helpBox.width<=390,JSON.stringify(helpBox));
  await page.keyboard.press('Escape');
  await panel.locator('.toolbar-scroll').evaluate(e=>e.scrollLeft=e.scrollWidth);
  await panel.locator('[data-part=direction]').selectOption('a_to_b');
  await panel.locator('[data-part=direction]').selectOption('all');

  await page.setViewportSize({width:1440,height:1000});
  await panel.locator('[data-part=protocol]').selectOption('LLDP');
  await panel.locator('[data-part=start]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.traffic-panel tbody tr').length===0);
  fixture.stdin.write('frames\n');
  await page.waitForFunction(()=>document.querySelectorAll('.traffic-panel tbody tr').length===1); // LLDP filter retained.
  await panel.locator('[data-part=close]').click();
  await panel.waitFor({state:'detached'});
  const current=await page.request.get(url+'/api/state').then(r=>r.json());
  assert.deepEqual(current.topology,topologyBefore);
  assert.equal(current.link_state.cable.blocked_a_to_b,true);
  assert.deepEqual(errors,[]);
  console.log('PASS: relay traffic capture, filters, safe details, PCAP, recapture and floating window');
 } finally {if(browser)await browser.close();fixture.stdin.end('\n');await once(fixture,'exit');}
})().catch(e=>{console.error(e);console.error(log);process.exitCode=1;});
