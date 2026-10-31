// A reader connected only to its opener. No API calls or stored document copies.
(() => {
  // Browsers may copy the opener's tab storage into a new window. This reader
  // must not retain a duplicate preview or an Agent session capability.
  try { sessionStorage.clear(); } catch (_) {}
  const owner = window.opener, body = document.getElementById('reader-body');
  const get = id => document.getElementById(id);
  let connected = false, signature = '', timer;
  const send = data => { if (owner && !owner.closed) owner.postMessage(data, location.origin); };
  const position = () => ({top:body.scrollTop, left:body.scrollLeft});
  function end() {
    connected = false; body.replaceChildren();
    body.textContent = 'The workspace connection ended. Reopen Instructions from Weblab.';
    get('reader-title').textContent = 'Instructions'; get('reader-source').textContent = '';
    document.title = 'Weblab · Instructions';
    for (const id of ['reader-smaller','reader-font','reader-larger','reader-return']) get(id).disabled = true;
  }
  window.addEventListener('message', event => {
    if (!owner || event.source !== owner || event.origin !== location.origin) return;
    const data = event.data;
    if (data?.type === 'weblab-instructions-ended') { end(); return; }
    if (data?.type !== 'weblab-instructions-state' || typeof data.html !== 'string') return;
    connected = true;
    const next = data.name + '\n' + data.html;
    if (next !== signature) {
      // HTML comes exclusively from the opener's existing safe Markdown renderer.
      body.innerHTML = data.html; body.scrollTop = body.scrollLeft = 0; signature = next;
    }
    get('reader-title').textContent = data.name;
    document.title = data.name + ' · Weblab';
    get('reader-source').textContent = data.source;
    body.style.fontSize = data.fontSize + 'px'; get('reader-font').textContent = data.fontSize + 'px';
    get('reader-smaller').disabled = data.fontSize <= 12; get('reader-larger').disabled = data.fontSize >= 24;
    get('reader-font').disabled = get('reader-return').disabled = false;
    document.documentElement.dataset.theme = data.theme === 'light' ? 'light' : 'dark';
    if (data.position) { body.scrollTop = data.position.top; body.scrollLeft = data.position.left; }
  });
  body.addEventListener('scroll', () => {
    clearTimeout(timer);
    timer = setTimeout(() => send({type:'weblab-instructions-position', ...position()}), 80);
  });
  body.addEventListener('click', event => {
    const link = event.target.closest('a[data-anchor-link]');
    if (!link) return;
    event.preventDefault();
    let anchor; try { anchor = decodeURIComponent(link.dataset.anchorLink); } catch (_) { return; }
    const heading = Array.from(body.querySelectorAll('[data-anchor]')).find(h => h.dataset.anchor === anchor);
    if (heading) body.scrollTop += heading.getBoundingClientRect().top - body.getBoundingClientRect().top - 16;
  });
  for (const [id, delta] of [['reader-smaller',-1],['reader-larger',1],['reader-font',0]])
    get(id).onclick = () => { if (connected) send({type:'weblab-instructions-font', delta}); };
  get('reader-return').onclick = () => send({type:'weblab-instructions-return', ...position()});
  function fullscreenState() {
    const button = get('reader-fullscreen'), active = !!document.fullscreenElement;
    button.disabled = !document.fullscreenEnabled && !active;
    button.title = active ? 'Exit fullscreen' : 'Enter fullscreen';
    button.setAttribute('aria-label', button.title); button.setAttribute('aria-pressed', String(active));
  }
  get('reader-fullscreen').onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen({navigationUI:'hide'});
    } catch (_) { get('reader-fullscreen').title = 'Fullscreen is unavailable in this browser'; }
    fullscreenState();
  };
  document.addEventListener('fullscreenchange', fullscreenState); fullscreenState();
  window.addEventListener('pagehide', () => send({type:'weblab-instructions-position', ...position()}));
  setInterval(() => { if (!owner || owner.closed) end(); }, 500);
  send({type:'weblab-instructions-ready'});
})();
