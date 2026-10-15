// Invoked by auth_packaging.py only, against its disposable packaged server.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const config = JSON.parse(fs.readFileSync(0, 'utf8'));

(async () => {
 const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
 const errors = [];
 try {
  async function login(name) {
   const context = await browser.newContext({viewport:{width:1280,height:900}});
   const page = await context.newPage();
   page.on('pageerror', error => errors.push(error.message));
   await page.goto(config.base);
   await page.locator('#auth-dialog[open]').waitFor();
   await page.selectOption('#auth-form select', name);
   await page.fill('#auth-form input[type=password]', config.passwords[name]);
   await page.click('#auth-form button');
   await page.waitForFunction(() => typeof loaded !== 'undefined' && loaded);
   await page.evaluate(() => openConsole('r1'));
   await page.waitForFunction(() => consoleSessions.get('r1')?.lockReady);
   return page;
  }
  const admin = await login('admin'), writer = await login('user'), reader = await login('guest');
  const mine = page => page.waitForFunction(() => consoleSessions.get('r1').lockMine);
  const observing = page => page.waitForFunction(() => consoleSessions.get('r1').locked && !consoleSessions.get('r1').lockMine);
  await writer.click('#lock-console'); await mine(writer); await observing(admin);
  await admin.click('#takeover-console'); await mine(admin); await observing(writer);
  assert.ok(await admin.locator('#takeover-dialog').isHidden());
  assert.ok(await reader.locator('#lock-console').isDisabled());
  await writer.click('#takeover-console');
  await admin.locator('#console-approval-dialog[open]').waitFor();
  await admin.click('#approve-console-control'); await mine(writer); await observing(admin);
  await reader.evaluate(() => sendConsoleInput(consoleSessions.get('r1'), 'READER-MUST-NOT-SEND\r'));
  await writer.evaluate(() => sendConsoleInput(consoleSessions.get('r1'), 'PACKAGED-WRITER-INPUT\r'));
  await admin.waitForFunction(() => {
   const b = consoleSessions.get('r1').terminal.buffer.active;
   return Array.from({length:b.length},(_,i)=>b.getLine(i)?.translateToString()).join('\n').includes('RX:PACKAGED-WRITER-INPUT');
  });
  const text = await admin.evaluate(() => {
   const b = consoleSessions.get('r1').terminal.buffer.active;
   return Array.from({length:b.length},(_,i)=>b.getLine(i)?.translateToString()).join('\n');
  });
  assert.ok(!text.includes('READER-MUST-NOT-SEND'));
  // Server logout revokes another tab's already-open WebSocket in that login.
  const sibling = await writer.context().newPage();
  const socketClosed = new Promise(resolve => sibling.on('websocket', socket => {
   if (new URL(socket.url()).pathname === '/ws/r1') socket.on('close', resolve);
  }));
  await sibling.goto(config.base); await sibling.waitForFunction(() => typeof loaded !== 'undefined' && loaded);
  await sibling.evaluate(() => openConsole('r1'));
  await sibling.waitForFunction(() => consoleSessions.get('r1')?.lockReady);
  await writer.click('#auth-logout');
  await writer.locator('#auth-dialog[open]').waitFor();
  // The page reloads on the cross-tab auth event, so keep the observer in Node.
  await Promise.race([socketClosed, new Promise((_,reject) => {
   const timer = setTimeout(() => reject(Error('Sibling console stayed connected after logout')), 10000);
   timer.unref();
  })]);
  await sibling.locator('#auth-dialog[open]').waitFor();
  await admin.waitForFunction(() => !consoleSessions.get('r1').locked);
  assert.equal((await reader.request.get(config.base + '/api/state')).status(), 200);
  assert.equal((await admin.request.get(config.base + '/api/state')).status(), 200);
  assert.equal((await writer.request.get(config.base + '/api/state')).status(), 401);
  assert.ok(await reader.locator('#open-agent').isHidden());
  if (config.agent) {
   await admin.click('#open-agent');
   await admin.locator('#agent-auth-binding').waitFor();
   const other = await login('admin');
   const saved = await admin.evaluate(() => Object.entries(sessionStorage).find(([key])=>key.startsWith('weblab.agent.session.v1.')));
   assert.ok(saved?.[1]);
   const denied = await other.evaluate(async token => (await fetch('/api/agent/state', {
    headers:{'X-Weblab-Agent-Session':token}})).status, saved[1]);
   assert.equal(denied,409);
   await admin.click('#auth-logout');
   await admin.locator('#auth-dialog[open]').waitFor();
   assert.equal((await other.request.get(config.base + '/api/state')).status(),200);
  } else {
   assert.ok(await admin.locator('#open-agent').isHidden());
  }
  assert.deepEqual(errors, []);
  console.log('PASS packaged browsers: role locks/approval, reader view-only, cross-tab logout, independent sessions, optional admin Agent');
 } finally {
  await browser.close();
 }
})().catch(error => { console.error(error); process.exitCode = 1; });
