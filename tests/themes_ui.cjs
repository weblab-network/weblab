// Disposable HTTP/PTY lab: preferences, contrast and live console continuity.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');

const fixture = spawn('python3', ['-u', '-c', `
import sys
sys.path.insert(0, 'tests')
from test_lab import LabTests
t=LabTests();t.setUp()
try:
 image=t.root/'fake.bin'
 image.write_text(image.read_text().replace("b'RX:' + data", "b'RX:' + data.replace(bytes([13]), bytes([13,10]))"))
 ansi=b''.join(bytes([27])+b'['+str(code).encode()+b'mANSI_'+str(code).encode()+bytes([27])+b'[0m'+
  (bytes([13,10]) if index%4==3 else b'  ') for index,code in enumerate([*range(30,38),*range(90,98)]))
 ansi+=bytes([27])+b'[38;2;255;255;255mTRUE_WHITE'+bytes([27])+b'[0m'+bytes([13,10])
 image.write_text(image.read_text().replace('while True:', 'os.write(1, '+repr(ansi)+')\\nwhile True:'))
 t.topology['nodes'][0].update(x=300,y=270)
 t.topology['nodes'][1].update(x=600,y=270)
 t.topology['nodes'] += [
  {'id':'diagram','type':'network','name':'Provider','x':790,'y':270,'color':'#8f71ba'},
  {'id':'notes','type':'group','name':'Practice network','notes':'Keep existing devices and controls.','x':470,'y':280,'width':650,'height':300,'color':'#467c9c'}]
 t.lab.save(t.topology)
 print('http://127.0.0.1:'+str(t.port),flush=True)
 sys.stdin.readline()
finally:t.tearDown()
`], {cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
let fixtureLog=''; fixture.stderr.on('data',data=>fixtureLog+=data);

function contrast(a,b) {
  const luminance=color=>{
    const values=color.startsWith('#')?color.slice(1).match(/../g).map(n=>parseInt(n,16)):color.match(/[\d.]+/g).slice(0,3).map(Number);
    const rgb=values.map(n=>{
      n/=255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4;
    });
    return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
  };
  const x=luminance(a),y=luminance(b);
  return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
}

(async()=>{
  let browser;
  try {
    const base=await new Promise((resolve,reject)=>{
      let output='';fixture.stdout.on('data',data=>{output+=data;if(output.includes('\n'))resolve(output.trim());});
      fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${fixtureLog}`)));
    });
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'dark'});
    const errors=[];context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
    const page=await context.newPage();let writes=0;
    page.on('request',request=>{if(request.url().includes('/api/')&&request.method()!=='GET')writes++;});
    await page.goto(base);await page.waitForFunction(()=>loaded&&document.documentElement.dataset.theme==='dark');
    const choose=async(value,target=page,control='theme-preference')=>{
      await target.locator('#layout-menu > summary').click();
      await target.locator('#'+control).selectOption(value);
      await target.locator('#layout-menu > summary').click();
      await target.waitForFunction(({value,control})=>document.getElementById(control).value===value,{value,control});
    };
    const chooseConsole=(value,target=page)=>choose(value,target,'console-theme-preference');
    const colors=selector=>page.locator(selector).evaluate(e=>{
      const s=getComputedStyle(e);return {text:s.color,bg:s.backgroundColor,opacity:s.opacity};
    });
    const consoleChrome=async()=>{
      // Let control color transitions finish before comparing the two preferences.
      await page.waitForFunction(()=>document.getAnimations().every(a=>!(a instanceof CSSTransition)||a.playState!=='running'));
      return page.evaluate(()=>Object.fromEntries([
        '#console-panel','#console-toolbar','#console-tabs','#console-window-r2',
        '#console-toolbar-r2','#console-resize-r2','#lock-console-r2',
        '#console-font-reset-r2','#console-toolbar-r2 .console-menu-list'
      ].map(selector=>{
        const element=document.querySelector(selector),s=getComputedStyle(element);
        return [selector,{text:s.color,bg:s.backgroundColor,border:s.borderColor,colorScheme:s.colorScheme}];
      })));
    };
    const terminalLight=async(id='r1')=>{
      const theme=await page.evaluate(id=>consoleSessions.get(id).terminal.options.theme,id);
      assert.equal(theme.background,'#ffffff');
      for(const key of ['foreground','cursor','black','red','green','yellow','blue','magenta','cyan','white',
                        'brightBlack','brightRed','brightGreen','brightYellow','brightBlue','brightMagenta','brightCyan','brightWhite']) {
        assert.ok(contrast(theme[key],theme.background)>=4.5,`${key}: ${theme[key]}`);
      }
      assert.equal(await page.evaluate(id=>consoleSessions.get(id).terminal.options.minimumContrastRatio,id),4.5);
    };
    const startContrast=async()=>{
      await page.locator('#map-workspace-menu > summary').click();
      await page.mouse.move(0,0);
      const start=await colors('#start-all');
      assert.equal(await page.locator('#start-all').isDisabled(),false);
      assert.equal(start.opacity,'1');assert.notEqual(start.bg,'rgba(0, 0, 0, 0)');
      assert.ok(contrast(start.text,start.bg)>=4.5,JSON.stringify(start));
    };
    assert.equal((await colors('body')).bg,'rgb(17, 21, 27)');
    await page.locator('#layout-menu > summary').click();
    await page.locator('[data-workspace-layout=map]').click();
    await startContrast();
    await page.screenshot({path:'/tmp/weblab-themes-map-dark.png'});
    await page.locator('#start-all').hover();
    await page.waitForTimeout(180);const hovered=await colors('#start-all');
    assert.ok(contrast(hovered.text,hovered.bg)>=4.5);
    await page.locator('#map-workspace-menu > summary').click();
    await choose('light');
    assert.equal(await page.locator('html').evaluate(e=>getComputedStyle(e).colorScheme),'light');
    for(const selector of ['body','.canvas','.topbar','.palette','#lab-name']) {
      const c=await colors(selector);
      if(c.bg!=='rgba(0, 0, 0, 0)')assert.ok(contrast(c.text,c.bg)>=4.5,selector+JSON.stringify(c));
    }
    await startContrast();
    await page.screenshot({path:'/tmp/weblab-themes-map-light.png'});
    await page.locator('#start-all').hover();
    await page.waitForTimeout(180);const lightHover=await colors('#start-all');
    assert.ok(contrast(lightHover.text,lightHover.bg)>=4.5);
    await page.locator('#start-all').click();
    await page.waitForFunction(()=>state('r1')==='running'&&state('r2')==='running');
    await page.locator('#layout-menu > summary').click();
    await page.locator('[data-workspace-layout=build]').click();
    await page.evaluate(()=>openConsole('r1'));
    await page.waitForFunction(()=>consoleSessions.get('r1')?.status==='Connected');
    await terminalLight();
    assert.equal((await colors('#terminal')).bg,'rgb(255, 255, 255)');
    const whiteGlyph=page.locator('#console-panel .xterm-rows span').filter({hasText:'TRUE_WHITE'}).first();
    await whiteGlyph.waitFor();
    assert.ok(contrast(await whiteGlyph.evaluate(e=>getComputedStyle(e).color),'#ffffff')>=4.5,'Truecolor white stays legible on Light');
    await page.locator('#lock-console').click();await page.waitForFunction(()=>consoleSessions.get('r1').lockMine);
    await page.keyboard.insertText('THEME_HISTORY');await page.keyboard.press('Enter');
    await page.waitForFunction(()=>{
      const b=consoleSessions.get('r1').terminal.buffer.active;
      return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n').includes('THEME_HISTORY')&&
        b.getLine(b.baseY+b.cursorY).translateToString(true)==='';
    });
    await page.evaluate(()=>{
      const s=consoleSessions.get('r1');
      window.themeSnapshot={session:s,terminal:s.terminal,socket:s.socket,revision:s.lockRevision,
        topology:JSON.stringify(topology),icon:$('nodes').querySelector('.device-icon').innerHTML,
        map:$('canvas'),panel:$('console-panel').getBoundingClientRect().toJSON(),
        output:Array.from({length:s.terminal.buffer.active.length},(_,i)=>s.terminal.buffer.active.getLine(i).translateToString(true)).join('\n')};
    });
    const beforeWrites=writes;
    await choose('dark');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').terminal.options.theme.background),'#0c1117');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').terminal.options.minimumContrastRatio),1);
    await choose('system');
    await page.emulateMedia({colorScheme:'light'});
    await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');
    await terminalLight();
    await page.emulateMedia({colorScheme:'dark'});
    await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').terminal.options.theme.background),'#0c1117');
    await choose('light');
    await page.emulateMedia({colorScheme:'dark'}); // An explicit choice ignores the OS.
    assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
    assert.equal(writes,beforeWrites,'Appearance must not send mutation requests');
    const preserved=await page.evaluate(()=>{
      const s=consoleSessions.get('r1'),before=themeSnapshot;
      return {session:s===before.session,terminal:s.terminal===before.terminal,socket:s.socket===before.socket,
        lock:s.lockMine,revision:s.lockRevision===before.revision,topology:JSON.stringify(topology)===before.topology,
        map:$('canvas')===before.map,icon:$('nodes').querySelector('.device-icon').innerHTML===before.icon,
        geometry:JSON.stringify($('console-panel').getBoundingClientRect().toJSON())===JSON.stringify(before.panel),
        output:Array.from({length:s.terminal.buffer.active.length},(_,i)=>s.terminal.buffer.active.getLine(i).translateToString(true)).join('\n')===before.output};
    });
    for(const [key,value] of Object.entries(preserved))assert.equal(value,true,'Preserved '+key);
    await terminalLight();
    assert.equal((await colors('#terminal')).bg,'rgb(255, 255, 255)');
    await page.evaluate(()=>openConsole('r2'));
    await page.waitForFunction(()=>consoleSessions.get('r2')?.status==='Connected');
    await terminalLight('r2');
    await page.evaluate(()=>floatConsole('r2'));
    assert.equal((await colors('#console-window-r2 .floating-terminal-host')).bg,'rgb(255, 255, 255)');
    await page.evaluate(()=>{const s=consoleSessions.get('r2');window.floatingThemeSnapshot={session:s,socket:s.socket,terminal:s.terminal};});
    await choose('dark');
    assert.equal((await colors('#terminal')).bg,'rgb(12, 17, 23)');
    assert.equal((await colors('#console-window-r2 .floating-terminal-host')).bg,'rgb(12, 17, 23)');
    await choose('light');
    await terminalLight('r1');await terminalLight('r2');
    assert.equal(await page.evaluate(()=>{
      const s=consoleSessions.get('r2');return s===floatingThemeSnapshot.session&&s.socket===floatingThemeSnapshot.socket&&s.terminal===floatingThemeSnapshot.terminal;
    }),true);
    await page.screenshot({path:'/tmp/weblab-themes-light-consoles.png'});
    // Console theme changes only the terminal; docked/floating controls follow the workspace.
    const lightChrome=await consoleChrome();
    await chooseConsole('dark');
    assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
    assert.deepEqual(await consoleChrome(),lightChrome,'Light workspace controls ignore Console theme');
    assert.equal((await colors('#terminal')).bg,'rgb(12, 17, 23)');
    assert.equal((await colors('#console-window-r2 .floating-terminal-host')).bg,'rgb(12, 17, 23)');
    assert.equal((await colors('#console-toolbar-r2')).bg,'rgb(237, 242, 246)');
    assert.equal((await colors('#console-toolbar-r2')).text,'rgb(36, 50, 68)');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r2').terminal.options.theme.background),'#0c1117');
    await page.screenshot({path:'/tmp/weblab-themes-dark-console-light-workspace.png'});
    await choose('dark');
    const darkChrome=await consoleChrome();
    await chooseConsole('light');
    await terminalLight('r1');await terminalLight('r2');
    assert.deepEqual(await consoleChrome(),darkChrome,'Dark workspace controls ignore Console theme');
    assert.equal((await colors('#terminal')).bg,'rgb(255, 255, 255)');
    assert.equal((await colors('#console-window-r2 .floating-terminal-host')).bg,'rgb(255, 255, 255)');
    assert.equal((await colors('#console-toolbar-r2')).bg,'rgb(26, 34, 43)');
    assert.equal((await colors('#console-toolbar-r2')).text,'rgb(229, 233, 240)');
    await choose('light');await chooseConsole('dark');
    // A reader can change local appearance without changing shared permissions/state.
    await page.evaluate(()=>{Object.assign(weblabAuth,{enabled:true,role:'reader'});updateControls();});
    await choose('dark');await choose('light');
    assert.equal(await page.evaluate(()=>weblabAuth.role),'reader');
    await page.reload();await page.waitForFunction(()=>loaded);
    assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
    assert.equal(await page.locator('#theme-preference').inputValue(),'light');
    assert.equal(await page.locator('#console-theme-preference').inputValue(),'dark');
    await page.evaluate(()=>openConsole('r1'));
    await page.waitForFunction(()=>consoleSessions.get('r1')?.status==='Connected');
    assert.equal(await page.evaluate(()=>consoleSessions.get('r1').terminal.options.theme.background),'#0c1117');
    const tab=await context.newPage();await tab.goto(base);await tab.waitForFunction(()=>loaded);
    await chooseConsole('light',tab);
    await page.waitForFunction(()=>consoleSessions.get('r1').terminal.options.theme.background==='#ffffff');
    await choose('dark',tab);
    await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
    await terminalLight();
    await chooseConsole('workspace',tab);
    await page.waitForFunction(()=>consoleSessions.get('r1').terminal.options.theme.background==='#0c1117');
    await tab.evaluate(()=>localStorage.removeItem('weblab.theme.v1'));
    await choose('light');
    // Exercise the real settings menu at phone width in Map workspace.
    await page.locator('#layout-menu > summary').click();
    await page.locator('[data-workspace-layout=map]').click();
    await page.setViewportSize({width:390,height:740});
    await page.locator('#layout-menu > summary').click();
    await page.waitForFunction(()=>{
      const r=document.getElementById('theme-preference').getBoundingClientRect();
      return r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;
    });
    const picker=await page.locator('#theme-preference').boundingBox();
    assert.ok(picker.x>=0&&picker.y>=0&&picker.x+picker.width<=390&&picker.y+picker.height<=740,JSON.stringify(picker));
    await page.locator('#theme-preference').selectOption('system');
    await page.locator('#layout-menu > summary').click();
    await page.screenshot({path:'/tmp/weblab-themes-mobile.png'});
    // Invalid values and blocked storage fall back without breaking the workspace.
    await context.close();
    const blocked=await browser.newContext();
    await blocked.addInitScript(()=>{
      for(const method of ['getItem','setItem']) {
        const original=Storage.prototype[method];
        Storage.prototype[method]=function(key,...args){
          if(['weblab.theme.v1','weblab.console.theme.v1'].includes(key))throw new DOMException('Storage unavailable','SecurityError');
          return original.call(this,key,...args);
        };
      }
    });
    const privatePage=await blocked.newPage();privatePage.on('pageerror',error=>errors.push(error.message));
    await privatePage.goto(base);await privatePage.waitForFunction(()=>loaded);
    await choose('light',privatePage);
    await chooseConsole('dark',privatePage);
    assert.equal(await privatePage.locator('html').getAttribute('data-theme'),'light');
    assert.equal(await privatePage.locator('html').getAttribute('data-console-theme'),'dark');
    await privatePage.reload();await privatePage.waitForFunction(()=>loaded);
    assert.equal(await privatePage.locator('html').getAttribute('data-theme'),'dark');
    await blocked.close();
    const invalid=await browser.newContext();
    await invalid.addInitScript(()=>{localStorage.setItem('weblab.theme.v1','invalid');localStorage.setItem('weblab.console.theme.v1','invalid');});
    const invalidPage=await invalid.newPage();invalidPage.on('pageerror',error=>errors.push(error.message));
    await invalidPage.goto(base);await invalidPage.waitForFunction(()=>loaded);
    assert.equal(await invalidPage.locator('html').getAttribute('data-theme'),'dark');
    await invalid.close();
    assert.deepEqual(errors,[]);
    console.log('PASS independent console/workspace themes, ANSI contrast, persistence/system/blocked storage, Map Start and live session/lock continuity');
  } finally {
    if(browser)await browser.close();
    const exited=once(fixture,'exit');fixture.stdin.end('\n');if(fixture.exitCode===null)await exited;
  }
})().catch(error=>{console.error(error,fixtureLog);process.exitCode=1;});
