// Real role-aware console handoff; disposable echo nodes and independent logins.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const fixture = spawn('python3', ['-u', '-c', `
import sys, json
sys.path.insert(0, 'tests')
from test_lab import LabTests
from web_auth import Auth, ROLES, digest, console_identity
from console_capture import Console
t = LabTests(); t.setUp()
t.server.auth = Auth(t.lab.directory / '.auth', {n: 'test-unique-password1-' + n for n in ROLES})
agent = None
try:
 t.lab.start_all()
 print('http://127.0.0.1:' + str(t.port), flush=True)
 for line in sys.stdin:
  if not line.strip(): break
  command = json.loads(line)
  if agent: agent.close(); agent = None
  if command['action'] == 'borrow':
   agent = Console(t.lab.runtime['r1']['port'], identity=('admin',console_identity(digest(command['token']))))
   agent.acquire()
  print('agent-ready', flush=True)
finally:
 if agent: agent.close()
 t.tearDown()
 t.server.auth.close()
`], {cwd:path.resolve(__dirname, '..'), stdio:['pipe','pipe','pipe']});
let logs = '';
fixture.stderr.on('data', data => logs += data);
(async () => {
 let browser;
 try {
  const base = await new Promise((resolve,reject) => {
   let out = '';
   fixture.stdout.on('data', data => {out += data; if(out.includes('\n')) resolve(out.trim());});
   fixture.once('exit', () => reject(Error(logs)));
  });
  browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const errors = [];
  async function login(name) {
   const context = await browser.newContext({viewport:{width:1280,height:900}});
   const page = await context.newPage();
   page.on('pageerror', error => errors.push(error.message));
   await page.goto(base);
   await page.locator('#auth-dialog[open]').waitFor();
   await page.selectOption('#auth-form select', name);
   await page.fill('#auth-form input[type=password]', 'test-unique-password1-' + name);
   await page.click('#auth-form button');
   await page.waitForFunction(() => loaded);
   await page.evaluate(() => openConsole('r1'));
   await page.waitForFunction(() => consoleSessions.get('r1')?.lockReady);
   return page;
  }
  const admin = await login('admin'), writer = await login('user'), reader = await login('guest');
  async function agentAccess(action) {
   const token = (await admin.context().cookies()).find(c => c.name === 'weblab_session').value;
   const ready = once(fixture.stdout, 'data');
   fixture.stdin.write(JSON.stringify({action,token})+'\n');
   await ready;
  }
  const mine = page => page.waitForFunction(() => consoleSessions.get('r1').lockMine);
  const observing = page => page.waitForFunction(() => consoleSessions.get('r1').locked && !consoleSessions.get('r1').lockMine);
  await writer.click('#lock-console'); await mine(writer); await observing(admin);
  await admin.click('#takeover-console'); await mine(admin); await observing(writer);
  assert.ok(await admin.locator('#takeover-dialog').isHidden(), 'Admin takes over writer without confirmation');
  assert.equal(await writer.locator('#takeover-console').textContent(), 'Request control');
  assert.match(await writer.locator('#console-status').getAttribute('title'), /admin/);
  assert.ok(await reader.locator('#takeover-console').isHidden());
  assert.ok(await reader.locator('#lock-console').isDisabled());
  // Same-login Agent borrowing is exclusive, in docked and floating views.
  for (const floating of [false, true]) {
   if (floating) await admin.evaluate(() => floatConsole('r1'));
   await agentAccess('borrow');
   await admin.waitForFunction(() => consoleSessions.get('r1').agentBusy);
   assert.equal(await admin.evaluate(() => consoleSessions.get('r1').terminal.options.disableStdin), true);
   assert.equal(await admin.evaluate(() => consoleSessions.get('r1').lockMine), true);
   const control = floating ? admin.locator('#takeover-console-r1') : admin.locator('#takeover-console');
   await control.waitFor({state:'visible'});
   assert.equal(await control.textContent(), 'Take back control');
   await admin.evaluate(() => sendConsoleInput(consoleSessions.get('r1'), 'BLOCKED-BROWSER-INPUT'));
   await control.click();
   await admin.waitForFunction(() => !consoleSessions.get('r1').agentBusy);
   assert.equal(await admin.evaluate(() => consoleSessions.get('r1').lockMine), true);
   assert.equal(await admin.evaluate(() => consoleSessions.get('r1').terminal.options.disableStdin), false);
   await agentAccess('release');
  }
  await admin.evaluate(() => dockConsole('r1'));
  await agentAccess('borrow');
  await admin.waitForFunction(() => consoleSessions.get('r1').agentBusy);
  await agentAccess('release');
  await admin.waitForFunction(() => !consoleSessions.get('r1').agentBusy);
  assert.equal(await admin.evaluate(() => consoleSessions.get('r1').lockMine), true);
  assert.equal(await admin.evaluate(() => consoleSessions.get('r1').terminal.options.disableStdin), false);
  // Approval must be visible even when its console is hidden/backgrounded.
  await admin.evaluate(() => {openConsole('r2'); $('close-console').click();});
  await writer.click('#takeover-console');
  await admin.locator('#console-approval-dialog[open]').waitFor();
  assert.match(await admin.locator('#console-approval-description').textContent(), /user \(writer\).*r1/);
  // These browsers receive independent WebSocket updates; admin's modal does
  // not establish that the requester's acknowledgement has arrived yet.
  await writer.waitForFunction(() => $('takeover-console').textContent === 'Cancel request');
  assert.equal(await writer.locator('#takeover-console').textContent(), 'Cancel request');
  assert.equal(await writer.evaluate(() => consoleSessions.get('r1').terminal.options.disableStdin), true);
  await admin.keyboard.press('Escape');
  await writer.waitForFunction(() => $('toast').textContent.includes('denied'));
  assert.equal(await admin.evaluate(() => consoleSessions.get('r1').lockMine), true);
  await writer.click('#takeover-console');
  await admin.locator('#console-approval-dialog[open]').waitFor();
  await writer.waitForFunction(() => $('takeover-console').textContent === 'Cancel request');
  await writer.click('#takeover-console'); // Cancel request.
  await admin.locator('#console-approval-dialog').waitFor({state:'hidden'});
  // A request arriving during another modal waits until that modal closes.
  await admin.evaluate(() => $('logs-dialog').showModal());
  await writer.waitForFunction(() => !consoleSessions.get('r1').pendingTakeover);
  await writer.click('#takeover-console');
  await admin.waitForFunction(() => !!consoleSessions.get('r1').pendingTakeover);
  assert.ok(await admin.locator('#console-approval-dialog').isHidden());
  await admin.click('#close-logs');
  await admin.locator('#console-approval-dialog[open]').waitFor();
  await admin.click('#approve-console-control'); await mine(writer); await observing(admin);
  await writer.evaluate(() => sendConsoleInput(consoleSessions.get('r1'), 'APPROVED-INPUT\r'));
  await admin.waitForFunction(() => {
   const b = consoleSessions.get('r1').terminal.buffer.active;
   return Array.from({length:b.length},(_,i)=>b.getLine(i)?.translateToString()).join('\n').includes('APPROVED-INPUT');
  });
  await admin.evaluate(() => activateConsole('r1'));
  await admin.click('#takeover-console'); await mine(admin); await observing(writer);
  await writer.click('#takeover-console');
  await admin.locator('#console-approval-dialog[open]').waitFor();
  // Reconnecting a requester invalidates the pending approval rather than inheriting it.
  await writer.evaluate(() => connectConsole(consoleSessions.get('r1')));
  await admin.locator('#console-approval-dialog').waitFor({state:'hidden'});
  await writer.waitForFunction(() => consoleSessions.get('r1').lockReady && !consoleSessions.get('r1').pendingTakeover);
  await writer.click('#takeover-console');
  await admin.locator('#console-approval-dialog[open]').waitFor();
  await admin.setViewportSize({width:390,height:844});
  const box = await admin.locator('#console-approval-dialog').boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 390, 'Approval fits mobile viewport');
  await admin.screenshot({path:'/tmp/weblab-aaa-console-approval.png'});
  await admin.click('#deny-console-control');
  await writer.waitForFunction(() => !consoleSessions.get('r1').pendingTakeover);
  // Requests from two nodes are queued separately, including a floated console.
  await admin.setViewportSize({width:1280,height:900});
  await admin.evaluate(() => {activateConsole('r2'); consoleControl(consoleSessions.get('r2'), 'lock');});
  await admin.waitForFunction(() => consoleSessions.get('r2').lockMine);
  await admin.evaluate(() => floatConsole('r2'));
  await writer.evaluate(() => openConsole('r2'));
  await writer.waitForFunction(() => consoleSessions.get('r2').lockReady);
  await writer.evaluate(() => requestConsoleTakeover(consoleSessions.get('r1')));
  await admin.locator('#console-approval-dialog[open]').waitFor();
  await writer.evaluate(() => requestConsoleTakeover(consoleSessions.get('r2')));
  await admin.waitForFunction(() => !!consoleSessions.get('r2').pendingTakeover);
  assert.match(await admin.locator('#console-approval-description').textContent(), /control of r1/);
  await admin.click('#deny-console-control');
  await admin.waitForFunction(() => $('console-approval-description').textContent.includes('control of r2') && $('console-approval-dialog').open);
  await admin.click('#approve-console-control');
  await writer.waitForFunction(() => consoleSessions.get('r2').lockMine);
  assert.equal(await admin.evaluate(() => consoleSessions.get('r1').lockMine), true);
  // Same-role authenticated takeovers still ask the requester to confirm.
  const peer = await login('user');
  await peer.evaluate(() => openConsole('r2'));
  await peer.waitForFunction(() => consoleSessions.get('r2').lockReady);
  await peer.click('#takeover-console');
  await peer.locator('#takeover-dialog[open]').waitFor();
  await peer.keyboard.press('Escape');
  assert.equal(await writer.evaluate(() => consoleSessions.get('r2').lockMine), true);
  await peer.click('#takeover-console'); await peer.click('#confirm-takeover');
  await peer.waitForFunction(() => consoleSessions.get('r2').lockMine);
  assert.deepEqual(errors, []);
  console.log('PASS: immediate admin takeover, writer request/deny/cancel/approve, reader UI, background/floating consoles, modal and multi-node queues, reconnect cancellation, mobile approval and same-role confirmation.');
 } finally {
  if(browser) await browser.close();
  const exited = once(fixture, 'exit');
  fixture.stdin.end('\n');
  if(fixture.exitCode === null) await exited;
 }
})().catch(error => {console.error(error, logs); process.exitCode = 1;});
