// Local exercise reader. File contents stay in this browser tab, outside lab state.
(() => {
  const MAX_BYTES = 512 * 1024, STORAGE_KEY = 'netlab.instructions.v1';
  const panel = $('instructions-panel'), body = $('instructions-body');
  let selecting = false, selectionTimer, lastSelection = '';
  function copySelection() {
    clearTimeout(selectionTimer);
    const selection = window.getSelection();
    if (panel.hidden || !selection || selection.isCollapsed || !body.contains(selection.anchorNode) || !body.contains(selection.focusNode)) {
      lastSelection = ''; return;
    }
    // A browser selection spanning the document and topology is not a copy source.
    for (let i = 0; i < selection.rangeCount; i++) {
      const range = selection.getRangeAt(i);
      if (!body.contains(range.startContainer) || !body.contains(range.endContainer)) return;
    }
    const text = selection.toString(), signature = clipboardGeneration + ':' + text;
    if (signature === lastSelection || !clipboardEnabled) return;
    lastSelection = signature;
    copyWorkspaceSelection(text, () => !panel.hidden);
  }
  body.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    selecting = true; lastSelection = ''; clearTimeout(selectionTimer);
  });
  document.addEventListener('pointerup', () => { if (selecting) { selecting = false; copySelection(); } });
  document.addEventListener('pointercancel', () => { selecting = false; clearTimeout(selectionTimer); });
  body.addEventListener('keyup', copySelection);
  document.addEventListener('selectionchange', () => {
    clearTimeout(selectionTimer);
    // Keyboard selection, Android selection handles, and programmatic selections.
    if (!selecting) selectionTimer = setTimeout(copySelection, 120);
  });
  let documentFile = null, fontSize = 15, readGeneration = 0;
  const view = {panel, maximized:false, rect:null, applyLayout};
  documentWindows.add(view);
  const markdown = window.markdownit({html:false, linkify:false, typographer:false, maxNesting:32});
  // A selected text file does not grant access to nearby files or load remote images.
  markdown.renderer.rules.image = (tokens, index) => '<span class="instructions-image">' +
    markdown.utils.escapeHtml(`[Image: ${tokens[index].content || tokens[index].attrGet('src')}]`) + '</span>';

  function remember() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({file:documentFile, fontSize, open:!panel.hidden}));
    } catch (_) { /* Reading still works when browser storage is disabled or full. */ }
  }
  function applyLayout() {
    const bounds = consoleViewport();
    view.rect = boundedConsoleRect(view.rect || {x:bounds.x+40, y:bounds.y+55, width:720, height:640});
    const rect = view.maximized ? bounds : view.rect;
    Object.assign(panel.style, {left:rect.x+'px', top:rect.y+'px', width:rect.width+'px', height:rect.height+'px'});
    panel.classList.toggle('maximized', view.maximized);
    $('instructions-move').hidden = $('instructions-resize').hidden = view.maximized;
    const button = $('instructions-maximize');
    button.title = view.maximized ? 'Restore instructions size' : 'Maximize instructions';
    button.setAttribute('aria-label', button.title);
    button.setAttribute('aria-pressed', String(view.maximized));
    button.textContent = view.maximized ? '↙' : '↗';
  }
  function setFont(size) {
    fontSize = clampConsole(size, 12, 24);
    body.style.fontSize = fontSize+'px';
    $('instructions-font').textContent = fontSize+'px';
    $('instructions-smaller').disabled = fontSize === 12;
    $('instructions-larger').disabled = fontSize === 24;
  }
  function renderFile(file) {
    const content = document.createElement('div');
    if (/\.(md|markdown)$/i.test(file.name)) {
      // Markdown-it escapes raw HTML and code. No HTML/highlighter plugins enabled.
      content.innerHTML = markdown.render(file.text);
      const used = new Set();
      for (const heading of content.querySelectorAll('h1,h2,h3,h4,h5,h6')) {
        const base = heading.textContent.toLowerCase().replace(/[^\p{L}\p{N}_ -]/gu, '').replace(/ /g, '-') || 'section';
        let slug = base, suffix = 0;
        while (used.has(slug)) slug = base+'-'+(++suffix);
        used.add(slug);
        heading.dataset.anchor = slug;
      }
      for (const link of content.querySelectorAll('a')) {
        const href = link.getAttribute('href') || '';
        if (href.startsWith('#')) {
          link.dataset.anchorLink = href.slice(1);
        } else if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) {
          link.target = '_blank'; link.rel = 'noopener noreferrer';
        } else {
          link.removeAttribute('href');
          link.title = 'Open referenced local files using Open file';
          link.classList.add('instructions-local-link');
        }
      }
      // Render common task-list markers as read-only checkboxes.
      for (const item of content.querySelectorAll('li')) {
        const first = item.firstElementChild?.tagName === 'P' ? item.firstElementChild : item;
        const text = first.firstChild;
        if (text?.nodeType !== Node.TEXT_NODE) continue;
        const match = text.textContent.match(/^\[([ xX])\] /);
        if (!match) continue;
        const box = document.createElement('input');
        box.type = 'checkbox'; box.disabled = true; box.checked = match[1] !== ' ';
        box.setAttribute('aria-label', box.checked ? 'Completed task' : 'Incomplete task');
        text.textContent = text.textContent.slice(4);
        first.prepend(box);
      }
    } else {
      const pre = document.createElement('pre');
      pre.className = 'instructions-plain'; pre.textContent = file.text;
      content.append(pre);
    }
    if (!file.text.trim()) content.textContent = 'This file is empty.';
    body.replaceChildren(content);
    body.scrollTop = body.scrollLeft = 0;
    $('instructions-title').textContent = file.name;
    $('instructions-title').title = file.name;
    body.setAttribute('aria-label', file.name);
  }
  function show() {
    dismissCompactPanel();
    panel.hidden = false;
    applyLayout(); bringConsoleForward(panel);
    $('open-instructions').setAttribute('aria-expanded', 'true');
    body.focus({preventScroll:true});
    remember();
  }
  function hide() {
    if (consoleGesture?.floating === view) consoleGesture = null;
    panel.hidden = true;
    floatingConsoleOrder = floatingConsoleOrder.filter(p => p !== panel);
    $('open-instructions').setAttribute('aria-expanded', 'false');
    $('open-instructions').focus({preventScroll:true});
    remember();
  }
  const open = () => documentFile ? show() : $('instructions-file').click();
  $('open-instructions').onclick = open;
  document.addEventListener('click', event => { if (event.target.closest('[data-show-instructions]')) open(); });
  $('instructions-open').onclick = () => $('instructions-file').click();
  $('instructions-hide').onclick = hide;
  $('instructions-maximize').onclick = () => { view.maximized = !view.maximized; applyLayout(); };
  for (const [id, delta] of [['instructions-smaller',-1],['instructions-larger',1],['instructions-font',0]]) {
    $(id).onclick = () => { setFont(delta ? fontSize+delta : 15); remember(); };
  }
  $('instructions-file').onchange = async event => {
    const file = event.target.files[0]; event.target.value = '';
    if (!file) return;
    const generation = ++readGeneration;
    try {
      if (!/\.(md|markdown|txt)$/i.test(file.name)) throw new Error('Choose a .md, .markdown or .txt file.');
      if (file.size > MAX_BYTES) throw new Error('Instructions files can be up to 512 KiB.');
      const bytes = await file.arrayBuffer();
      if (generation !== readGeneration) return;
      let text;
      try { text = new TextDecoder('utf-8', {fatal:true}).decode(bytes); }
      catch (_) { throw new Error('Save this document as UTF-8 text, then open it again.'); }
      if (text.includes('\0')) throw new Error('This file contains binary data. Choose a text document.');
      const next = {name:file.name, text};
      renderFile(next); documentFile = next; show();
    } catch (error) { if (generation === readGeneration) toast(error.message, true); }
  };
  body.addEventListener('click', event => {
    const link = event.target.closest('a[data-anchor-link]');
    if (!link) return;
    event.preventDefault();
    let anchor;
    try { anchor = decodeURIComponent(link.dataset.anchorLink); } catch (_) { return; }
    const heading = Array.from(body.querySelectorAll('[data-anchor]')).find(h => h.dataset.anchor === anchor);
    if (heading) body.scrollTop += heading.getBoundingClientRect().top - body.getBoundingClientRect().top - 16;
  });
  for (const [id, kind] of [['instructions-toolbar','move'],['instructions-resize','resize']]) bindConsolePointer($(id), kind, view);
  for (const [id, kind] of [['instructions-move','move'],['instructions-resize','resize']]) bindConsoleKeys($(id), kind, view);
  for (const event of ['pointerdown','focusin']) panel.addEventListener(event, () => bringConsoleForward(panel));
  setFont(fontSize);
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY));
    const file = saved?.file;
    if (file && typeof file.name === 'string' && typeof file.text === 'string' &&
        /\.(md|markdown|txt)$/i.test(file.name) && new TextEncoder().encode(file.text).length <= MAX_BYTES) {
      renderFile(file); documentFile = file;
      if (Number.isInteger(saved.fontSize)) setFont(saved.fontSize);
      if (saved.open) show();
    }
  } catch (_) { /* Invalid/inaccessible tab storage starts with no document open. */ }
})();
