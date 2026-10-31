// Move live panes between documents; the workspace retains terminals, sockets,
// lock ownership and all API/auth handling. Children never start another app.
(() => {
  const windows = new Map();
  let shuttingDown = false;
  function has(panel) { return !!windows.get(panel)?.attached; }
  function focus(panel) {
    const view = windows.get(panel);
    if (!view || view.child.closed) return false;
    view.child.focus(); return true;
  }
  function refreshTerminals(panel) {
    if (!panel.classList.contains('console-panel')) return;
    for (const session of consoleSessions.values()) {
      if (!panel.contains(session.element)) continue;
      // xterm's public open() updates its owning window after DOM adoption.
      session.terminal.open(session.element);
      session.fitAddon.fit(); session.terminal.refresh(0, session.terminal.rows-1);
    }
  }
  function restore(panel, closing=false) {
    const view = windows.get(panel);
    if (!view) return;
    windows.delete(panel); clearTimeout(view.timeout); clearInterval(view.poll);
    if (view.attached) {
      view.controls.remove();
      if (view.scroll) view.scroll={left:$('canvas-scroll').scrollLeft,top:$('canvas-scroll').scrollTop};
      view.placeholder.replaceWith(panel);
      detachedPanes.delete(panel);
      panel.classList.remove('browser-detached');
      if (panel.id === 'console-panel') {
        consoleFloating = view.consoleFloating;
        applyConsoleLayout();
      } else if (panel.dataset.consoleId) {
        const floating = consoleSessions.get(panel.dataset.consoleId)?.floating;
        if (floating) applyFloatingConsoleLayout(floating);
      } else if (panel.id==='topology-panel') applyTopologyLayout();
      else if (panel.id==='agent-panel') {
        if(view.wasDocked)window.weblabLayout?.show('agent');
        [...documentWindows].find(v=>v.panel===panel)?.applyLayout();
      } else [...documentWindows].find(v=>v.panel===panel)?.applyLayout();
      panel.hidden = false;
      refreshTerminals(panel);
      if (view.scroll) { $('canvas-scroll').scrollLeft=view.scroll.left; $('canvas-scroll').scrollTop=view.scroll.top; }
    }
    if (!closing && !view.child.closed) view.child.close();
  }
  function restoreAll() { for (const panel of [...windows.keys()]) restore(panel); }
  function open(panel) {
    if (!window.weblabPopoutAllowed) return;
    if (focus(panel)) return;
    let child;
    try { child=window.open('/pane-popout.html', '_blank', 'popup,width=1100,height=800'); } catch (_) {}
    if (!child) { toast('Allow popups for Weblab to open this pane in a separate window.',true); return; }
    const view={panel,child,attached:false,consoleFloating};
    windows.set(panel,view);
    view.timeout=setTimeout(()=>{
      if (!view.attached) { restore(panel); toast('Could not open the detached pane.',true); }
    },10000);
    view.poll=setInterval(()=>{ if (child.closed) restore(panel,true); },500);
  }
  function attach(child) {
    const view=[...windows.values()].find(v=>v.child===child);
    if (!view || child.location.origin!==location.origin || view.attached) return;
    const {panel}=view, doc=child.document;
    clearTimeout(view.timeout);
    if(panel.id==='agent-panel') {
      view.wasDocked=panel.classList.contains('workspace-docked');
      if(view.wasDocked)window.weblabLayout.floatDocument('agent');
    }
    view.placeholder=document.createComment('Detached '+panel.id);
    panel.before(view.placeholder);
    if (panel.id==='topology-panel') view.scroll={left:$('canvas-scroll').scrollLeft,top:$('canvas-scroll').scrollTop};
    detachedPanes.add(panel);
    view.attached=true;
    panel.classList.remove('workspace-inactive'); panel.classList.add('browser-detached');
    doc.getElementById('pane-host').replaceChildren(panel);
    panel.hidden=false;
    const title=panel.dataset.consoleId ? panel.getAttribute('aria-label') : panel.id==='topology-panel'?'Topology':panel.id==='agent-panel'?'Agent':panel.id==='console-panel'?'Consoles':'Traffic monitor';
    doc.title=title+' · Weblab';
    view.controls=doc.getElementById('pane-controls');
    panel.querySelector('.console-toolbar').append(view.controls);
    doc.documentElement.dataset.theme=document.documentElement.dataset.theme;
    for (const args of workspaceListeners) doc.addEventListener(...args);
    // Dialogs remain in the owning workspace (link settings, approval, inspector).
    doc.addEventListener('click', event=>{
      if (event.target.closest('#toggle-palette,#toggle-inspector,[data-storage],[data-show-instructions]')) window.focus();
      if (event.target.closest('[data-show-instructions]')) $('open-instructions').click();
    });
    doc.getElementById('pane-return').onclick=()=>{restore(panel);window.focus();};
    const fs=doc.getElementById('pane-fullscreen');
    fs.onclick=async()=>{
      try { if(doc.fullscreenElement) await doc.exitFullscreen(); else await doc.documentElement.requestFullscreen(); }
      catch (_) { doc.getElementById('pane-notice').textContent='Fullscreen is unavailable in this browser.'; }
    };
    doc.addEventListener('fullscreenchange',()=>{
      fs.setAttribute('aria-pressed',String(!!doc.fullscreenElement));
      fs.title=doc.fullscreenElement?'Exit fullscreen':'Enter fullscreen';fs.setAttribute('aria-label',fs.title);
    });
    child.addEventListener('resize',()=>{refreshTerminals(panel);panel.querySelectorAll('.console-menu[open]').forEach(positionConsoleMenu);});
    child.addEventListener('pagehide',()=>{if(!shuttingDown)restore(panel,true);});
    refreshTerminals(panel);
    if (view.scroll) { $('canvas-scroll').scrollLeft=view.scroll.left; $('canvas-scroll').scrollTop=view.scroll.top; }
    child.focus();
  }
  function buttonFor(panel, label) {
    const button=document.createElement('button');button.className='popout-button';
    button.title='Open '+label+' in a separate window';button.setAttribute('aria-label','Pop out '+label);
    button.innerHTML=$('instructions-popout').innerHTML;button.onclick=()=>open(panel);return button;
  }
  window.weblabPopouts={has,focus,restore,restoreAll,open,attach,buttonFor};
  $('topology-popout').onclick=()=>open($('topology-panel'));
  $('consoles-popout').onclick=()=>open($('console-panel'));
  const agentButton=buttonFor($('agent-panel'),'Agent');
  agentButton.id='agent-popout';
  $('agent-hide').before(agentButton);
  window.addEventListener('weblab-theme-change',()=>{
    for(const view of windows.values()) if(view.attached&&!view.child.closed)
      view.child.document.documentElement.dataset.theme=document.documentElement.dataset.theme;
  });
  window.addEventListener('weblab-popout-policy',()=>{if(!window.weblabPopoutAllowed)restoreAll();});
  for(const dialog of document.querySelectorAll('dialog')) new MutationObserver(()=>{
    if(dialog.open&&windows.size)window.focus();
  }).observe(dialog,{attributes:true,attributeFilter:['open']});
  new MutationObserver(()=>{
    for(const view of windows.values()) if(view.attached&&!view.child.closed)
      view.child.document.getElementById('pane-notice').textContent=$('toast').hidden ? '' : $('toast').textContent;
  }).observe($('toast'),{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['hidden']});
  window.addEventListener('pagehide',()=>{
    shuttingDown=true;
    for(const view of windows.values()) if(!view.child.closed)view.child.close();
  });
})();
