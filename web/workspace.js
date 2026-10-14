// Browser-local workspace geometry. Moving a panel never recreates its session.
(() => {
  const KEY = 'weblab.workspace.v1', area = document.querySelector('.workarea');
  const defaults = {preset:'build', tool:'agent', sideTool:'console', column:.55, row:.5};
  let settings = {...defaults}, rendering = false, activating = false, normalizing = false;
  const detached = new Set();
  try {
    const saved = JSON.parse(localStorage.getItem(KEY));
    if (saved && ['build','side','stack','free'].includes(saved.preset)) {
      settings.preset = saved.preset;
      if (['agent','instructions'].includes(saved.tool)) settings.tool = saved.tool;
      if (['console','agent','instructions'].includes(saved.sideTool)) settings.sideTool = saved.sideTool;
      for (const key of ['column','row']) if (Number.isFinite(saved[key])) settings[key] = clampConsole(saved[key],.25,.75);
    }
  } catch (_) { /* Defaults also work without browser storage. */ }
  const active = () => ['side','stack'].includes(settings.preset);
  const narrow = matchMedia('(max-width: 1000px), (orientation: portrait)');
  const stacked = () => settings.preset === 'stack' && !narrow.matches;
  const available = kind => kind !== 'agent' || !$('open-agent').hidden;
  const panelFor = kind => $(kind === 'console' ? 'console-panel' : kind+'-panel');
  const kindFor = panel => panel.id.replace('-panel','');
  const effectiveTool = () => {
    const kind = stacked() ? settings.tool : settings.sideTool;
    return available(kind) ? kind : 'instructions';
  };
  function save() {
    try { localStorage.setItem(KEY,JSON.stringify(settings)); } catch (_) {}
  }
  const right = document.createElement('section');
  right.id = 'workspace-right'; right.hidden = true;
  right.setAttribute('aria-label','Workspace tools');
  right.innerHTML = `<div id="workspace-tool-header"><div id="workspace-tool-tabs" role="tablist" aria-label="Workspace tools">
    <button id="workspace-console-tab" role="tab" data-workspace-tool="console" aria-controls="console-panel">Consoles</button>
    <button id="workspace-agent-tab" role="tab" data-workspace-tool="agent" aria-controls="agent-panel">Agent</button>
    <button id="workspace-instructions-tab" role="tab" data-workspace-tool="instructions" aria-controls="instructions-panel">Instructions</button>
  </div><div id="workspace-tool-controls"></div></div><div id="workspace-document-slot" class="workspace-slot"><p class="workspace-empty">Select a tool above to reopen it.</p></div>
  <div id="workspace-row-divider" class="workspace-divider" role="separator" tabindex="0" aria-label="Resize tools and consoles" aria-orientation="horizontal" aria-controls="workspace-document-slot workspace-console-slot"></div>
  <div id="workspace-console-slot" class="workspace-slot"><p class="workspace-empty">Open a running node’s console from the topology.</p></div>`;
  const column = document.createElement('div');
  column.id = 'workspace-column-divider'; column.className = 'workspace-divider'; column.hidden = true;
  column.tabIndex = 0; column.setAttribute('role','separator');
  column.setAttribute('aria-label','Resize topology and tools'); column.setAttribute('aria-orientation','vertical');
  column.setAttribute('aria-controls','topology-panel workspace-right');
  area.append(column,right);
  const documentSlot = $('workspace-document-slot'), consoleSlot = $('workspace-console-slot');
  function fit() { requestAnimationFrame(() => { fitConsole(); if (loaded) renderLinks(); }); }
  function isDocked(panel) { return active() && !detached.has(kindFor(panel)); }
  function documentLayout(view) {
    if (!isDocked(view.panel)) return false;
    view.maximized = false;
    view.panel.classList.add('workspace-docked');
    view.panel.classList.remove('maximized');
    view.panel.removeAttribute('style');
    const toolbar = $(kindFor(view.panel)+'-toolbar');
    // Instructions has file/publication/font controls: give it its own row
    // beneath the workspace tabs, including narrow and side-by-side layouts.
    if (kindFor(view.panel) === 'instructions') {
      if (toolbar.parentElement !== view.panel) view.panel.prepend(toolbar);
    } else if (toolbar.parentElement !== $('workspace-tool-controls')) $('workspace-tool-controls').append(toolbar);
    toolbar.classList.add('workspace-docked-toolbar');
    return true;
  }
  function consoleLayout() {
    const panel = $('console-panel');
    if (!active() || consoleFloating) {
      panel.classList.remove('workspace-docked','workspace-inactive');
      return false;
    }
    if (panel.parentElement !== consoleSlot) consoleSlot.append(panel);
    panel.classList.add('workspace-docked'); panel.classList.remove('floating','maximized');
    panel.removeAttribute('style');
    $('console-divider').hidden = $('console-move').hidden = $('console-resize').hidden = true;
    $('detach-console').textContent = 'Float'; $('detach-console').title = 'Float console panel';
    $('detach-console').setAttribute('aria-label','Float console panel');
    $('detach-console').setAttribute('aria-pressed','false');
    requestAnimationFrame(fitConsole);
    return true;
  }
  function emptyStates() {
    if (!active()) return;
    for (const kind of ['agent','instructions']) {
      const panel = panelFor(kind), toolbar = $(kind+'-toolbar');
      if (!toolbar.classList.contains('workspace-docked-toolbar')) continue;
      const hidden = panel.hidden || panel.classList.contains('workspace-inactive');
      if (toolbar.hidden !== hidden) toolbar.hidden = hidden;
    }
    for (const slot of [documentSlot,consoleSlot]) {
      const empty = slot.querySelector('.workspace-empty');
      const occupied = [...slot.children].some(p => p.matches('.workspace-docked') && !p.hidden && !p.classList.contains('workspace-inactive'));
      if (empty.hidden !== occupied) empty.hidden = occupied;
    }
  }
  function render() {
    if (rendering) return;
    rendering = true;
    const enabled = active(), tool = effectiveTool(), stack = stacked();
    area.classList.toggle('workspace-split',enabled);
    area.classList.toggle('workspace-narrow',enabled && narrow.matches);
    right.hidden = column.hidden = !enabled;
    right.classList.toggle('workspace-stacked',enabled && stack);
    area.style.setProperty('--workspace-column',settings.column+'fr');
    area.style.setProperty('--workspace-other',(1-settings.column)+'fr');
    right.style.setProperty('--workspace-row',settings.row+'fr');
    right.style.setProperty('--workspace-bottom',(1-settings.row)+'fr');
    $('workspace-row-divider').hidden = !enabled || !stack;
    column.setAttribute('aria-orientation',narrow.matches ? 'horizontal' : 'vertical');
    for (const [id,value] of [['workspace-column-divider',settings.column],['workspace-row-divider',settings.row]]) {
      for (const [key,v] of [['min',25],['max',75],['now',Math.round(value*100)]]) $(id).setAttribute('aria-value'+key,String(v));
    }
    for (const button of document.querySelectorAll('[data-workspace-layout]')) {
      button.setAttribute('aria-pressed',String(button.dataset.workspaceLayout === settings.preset));
    }
    for (const button of right.querySelectorAll('[data-workspace-tool]')) {
      const kind = button.dataset.workspaceTool;
      button.hidden = !available(kind) || stack && kind === 'console';
      button.setAttribute('aria-selected',String(kind === tool));
      button.tabIndex = kind === tool ? 0 : -1;
    }
    if (enabled) {
      for (const view of documentWindows) {
        const kind = kindFor(view.panel);
        if (!['agent','instructions'].includes(kind) || detached.has(kind)) continue;
        if (view.panel.parentElement !== documentSlot) documentSlot.append(view.panel);
        documentLayout(view);
        view.panel.classList.toggle('workspace-inactive',kind !== tool);
      }
      consoleLayout();
      documentSlot.hidden = !stack && tool === 'console';
      consoleSlot.hidden = !stack && tool !== 'console';
    }
    rendering = false;
    emptyStates(); fit();
  }
  function reveal(kind, fromPanel = false) {
    if (!active()) return;
    detached.delete(kind);
    if (kind === 'console') {
      if (consoleFloating) { consoleFloating = false; consoleMaximized = false; }
      if (!fromPanel) consoleOpenMode = 'docked';
      if (consoleNode) $('console-panel').hidden = false;
    }
    if (!activating) {
      if (stacked()) { if (kind !== 'console') settings.tool = kind; }
      else settings.sideTool = kind;
    }
    render(); save();
  }
  function activateTool() {
    const kind = effectiveTool();
    if (kind !== 'console') {
      activating = true;
      try { $('open-'+kind).click(); } finally { activating = false; }
    }
  }
  function restoreToolbar(panel) {
    const toolbar = $(kindFor(panel)+'-toolbar');
    toolbar.hidden = false; toolbar.classList.remove('workspace-docked-toolbar');
    panel.prepend(toolbar);
  }
  function release() {
    for (const view of documentWindows) {
      if (!view.panel.classList.contains('workspace-docked')) continue;
      if (view.panel.classList.contains('workspace-inactive')) view.panel.hidden = true;
      restoreToolbar(view.panel);
      view.panel.classList.remove('workspace-docked','workspace-inactive');
      $('console-windows').append(view.panel); view.applyLayout();
    }
    $('console-panel').classList.remove('workspace-docked','workspace-inactive');
    applyConsoleLayout();
  }
  function selectPreset(preset) {
    const focus = document.activeElement;
    settings.preset = preset; compactPanel = null; consoleGesture = null;
    if (active()) {
      detached.clear();
      normalizing = true;
      try {
        dockTopology(); $('topology-panel').hidden = false;
        setAllConsolesFloating(false);
      } finally { normalizing = false; }
      render(); activateTool();
    } else {
      release(); render();
      if (preset === 'build') { dockTopology(); setAllConsolesFloating(false); }
    }
    applyPanelLayout(); save();
    // Layout-only changes must not drop a terminal's focus or input lock.
    if (focus?.isConnected && focus.closest('.xterm') && focus.getClientRects().length) focus.focus({preventScroll:true});
  }
  function floatDocument(kind) {
    detached.add(kind);
    const view = [...documentWindows].find(v => kindFor(v.panel) === kind);
    restoreToolbar(view.panel);
    view.panel.classList.remove('workspace-docked','workspace-inactive');
    $('console-windows').append(view.panel); view.applyLayout(); bringConsoleForward(view.panel);
    emptyStates();
  }
  window.weblabLayout = {active, consoleLayout, documentLayout, show:kind => { if (!normalizing && (kind !== 'console' || !consoleFloating)) reveal(kind,true); },
    leave:() => { if (active()) selectPreset('free'); },
    availability:() => { if (active()) { render(); activateTool(); } }
  };
  for (const kind of ['agent','instructions']) {
    const button = document.createElement('button');
    button.textContent = 'Float'; button.className = 'workspace-float-document';
    button.title = 'Float '+(kind === 'agent' ? 'Agent' : 'Instructions');
    button.onclick = () => floatDocument(kind);
    $(kind+'-maximize').before(button);
  }
  for (const button of document.querySelectorAll('[data-workspace-layout]')) button.onclick = () => {
    $('layout-menu').open = false; selectPreset(button.dataset.workspaceLayout);
  };
  $('workspace-reset').onclick = () => {
    $('layout-menu').open = false; settings = {...defaults};
    desktopPanels.palette = desktopPanels.inspector = true;
    try { localStorage.removeItem('iol-panels'); } catch (_) {}
    consoleDockHeight = null; $('console-panel').classList.remove('expanded'); selectPreset('build');
  };
  right.querySelectorAll('[data-workspace-tool]').forEach(button => {
    button.onclick = () => { reveal(button.dataset.workspaceTool); activateTool(); };
    button.onkeydown = event => {
      const tabs = [...right.querySelectorAll('[data-workspace-tool]')].filter(b=>!b.hidden);
      const index = tabs.indexOf(button);
      const target = {ArrowRight:(index+1)%tabs.length,ArrowLeft:(index+tabs.length-1)%tabs.length,Home:0,End:tabs.length-1}[event.key];
      if (target === undefined) return;
      event.preventDefault(); tabs[target].click(); tabs[target].focus();
    };
  });
  function bindDivider(handle,key,parent) {
    let gesture = null;
    const vertical = () => key === 'column' && !narrow.matches;
    handle.onpointerdown = event => {
      if (event.button !== 0) return;
      event.preventDefault(); handle.focus();
      gesture = event.pointerId; handle.setPointerCapture(event.pointerId);
    };
    handle.onpointermove = event => {
      if (gesture !== event.pointerId) return;
      const box = parent.getBoundingClientRect();
      const offset = key === 'row' ? $('workspace-tool-header').offsetHeight : 0;
      settings[key] = clampConsole(vertical() ? (event.clientX-box.left)/box.width : (event.clientY-box.top-offset)/(box.height-offset),.25,.75);
      render();
    };
    for (const name of ['pointerup','pointercancel','lostpointercapture']) handle.addEventListener(name,event=>{
      if (gesture === event.pointerId) { gesture = null; save(); }
    });
    handle.onkeydown = event => {
      const keys = vertical() ? ['ArrowLeft','ArrowRight'] : ['ArrowUp','ArrowDown'];
      if (![...keys,'Home','End'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      settings[key] = event.key === 'Home' ? .25 : event.key === 'End' ? .75 : clampConsole(settings[key]+(event.key === keys[0] ? -1 : 1)*(event.shiftKey ? .1 : .02),.25,.75);
      render(); save();
    };
  }
  bindDivider(column,'column',area);
  bindDivider($('workspace-row-divider'),'row',right);
  // Only inspect visibility here: observing must never re-open a hidden panel.
  new MutationObserver(emptyStates).observe(right,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','class']});
  narrow.addEventListener('change',()=>{ if (active()) { render(); activateTool(); } });
  new ResizeObserver(fit).observe(area);
  if (active()) selectPreset(settings.preset);
  else render();
})();
