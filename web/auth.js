/* Fixed-account login. The server remains authoritative for every permission. */
'use strict';
(() => {
  const nativeFetch = window.fetch.bind(window);
  const state = window.weblabAuth = {enabled:true, role:null, csrf:'', ready:null};
  const dialog = document.createElement('dialog');
  dialog.id = 'auth-dialog';
  dialog.setAttribute('aria-labelledby', 'auth-title');
  dialog.innerHTML = `<form id="auth-form"><h2 id="auth-title">Sign in to Weblab</h2>
    <label>Account<select name="name"><option value="admin">admin · Administrator</option><option value="user">user · Writer</option><option value="guest">guest · Reader</option></select></label>
    <label>Password<input name="password" type="password" autocomplete="current-password" required maxlength="256"></label>
    <label class="export-log-option"><input name="remember" type="checkbox"> Remember this browser for 14 days</label>
    <p id="auth-error" role="alert"></p><p id="auth-progress" role="status" hidden><span class="auth-spinner" aria-hidden="true"></span>Checking credentials…</p><button class="primary" type="submit">Sign in</button></form>`;
  document.body.append(dialog);
  dialog.addEventListener('cancel', e => e.preventDefault());
  const logout = document.createElement('button');
  logout.id = 'auth-logout'; logout.hidden = true; logout.type = 'button';
  logout.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 4H4v16h5M9 12h12m-4-4 4 4-4 4"/></svg>';
  document.querySelector('.topbar').append(logout);
  const notifyTabs = () => { try { localStorage.setItem('weblab-auth-changed', String(Date.now()) + Math.random()); } catch (_) {} };
  window.addEventListener('storage', event => { if (event.key === 'weblab-auth-changed') location.reload(); });
  const showLogin = () => { if (!dialog.open) dialog.showModal(); };
  state.ready = nativeFetch('/api/auth/status').then(async response => {
    if (!response.ok) throw new Error('Unable to check login status. Reload to try again.');
    Object.assign(state, await response.json());
    document.documentElement.dataset.authRole = state.role || 'anonymous';
    if (state.enabled && !state.role) showLogin();
    if (state.enabled && state.role) {
      logout.hidden = false;
      logout.title = `Log out · ${state.name} (${state.role})`;
      logout.setAttribute('aria-label', logout.title);
    }
  }).catch(error => {
    showLogin(); document.getElementById('auth-error').textContent = error.message;
    throw error;
  });
  // Cover fetch callers without putting a session credential in browser storage.
  window.fetch = async (resource, options = {}) => {
    const url = new URL(resource instanceof Request ? resource.url : resource, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return nativeFetch(resource, options);
    await state.ready;
    const headers = new Headers(options.headers || (resource instanceof Request ? resource.headers : undefined));
    if (state.csrf) headers.set('X-Weblab-CSRF', state.csrf);
    const response = await nativeFetch(resource, {...options, headers});
    if (response.status === 401 && state.enabled) {
      if (state.role) location.reload(); // Remove previous user's topology, consoles and transient UI.
      else showLogin();
    }
    return response;
  };
  document.getElementById('auth-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget, button = form.querySelector('button');
    if (button.disabled) return;
    const progress = document.getElementById('auth-progress');
    document.getElementById('auth-error').textContent = '';
    form.setAttribute('aria-busy', 'true'); progress.hidden = false;
    for (const control of form.elements) control.disabled = true;
    try {
      const response = await nativeFetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json', 'X-Weblab-Login':'1'},
        body:JSON.stringify({name:form.elements.name.value, password:form.elements.password.value, remember:form.elements.remember.checked})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Login failed');
      form.elements.password.value = '';
      notifyTabs();
      location.reload();
    } catch (error) { document.getElementById('auth-error').textContent = error.message; }
    finally {
      form.setAttribute('aria-busy', 'false'); progress.hidden = true;
      for (const control of form.elements) control.disabled = false;
    }
  });
  logout.onclick = async () => {
    try {
      const response = await window.fetch('/api/auth/logout', {method:'POST'});
      if (!response.ok) throw new Error('Logout failed. Please try again.');
      notifyTabs();
      location.reload();
    } catch (error) { alert(error.message); }
  };
  // Updates also arrive from other tabs on the next API poll; cookies are shared.
  state.can = capability => !state.enabled || (capability === 'admin' ? state.role === 'admin' : ['admin','writer'].includes(state.role));

  const activityButton = document.createElement('button');
  activityButton.id = 'auth-activity'; activityButton.hidden = true;
  activityButton.title = 'Activity records'; activityButton.setAttribute('aria-label', 'Activity records');
  activityButton.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></svg>';
  logout.before(activityButton);
  const activity = document.createElement('dialog');
  activity.id = 'activity-dialog'; activity.setAttribute('aria-labelledby','activity-title');
  activity.innerHTML = `<div class="logs-heading"><h2 id="activity-title">Activity records</h2><button id="activity-close" aria-label="Close activity records">✕</button></div>
    <p class="muted">Account and session activity in this shared lab. These are operation records, not human console command history. HTTP/tool success does not verify device configuration.</p>
    <div class="activity-filters"><label>Account <select id="activity-account"><option value="">All accounts</option><option>admin</option><option>user</option><option>guest</option></select></label>
    <label>Source <select id="activity-source"><option value="">All sources</option><option value="browser">Browser</option><option value="agent">Agent tools</option></select></label><button id="activity-refresh">Refresh</button></div>
    <p id="activity-status" role="status"></p><div class="activity-scroll" tabindex="0" aria-label="Activity table"><table><thead><tr><th>Time</th><th>Account / role</th><th>Session</th><th>Source</th><th>Operation</th><th>Node / link</th><th>Result</th></tr></thead><tbody id="activity-records"></tbody></table></div>
    <button id="activity-older" hidden>Older records</button><p class="muted">Up to 10,000 records or 30 days, whichever is reached first. An unfinished entry may still be running or may have been interrupted. Failed sign-ins identify a claimed account, not a verified person. Shared accounts do not identify individuals.</p>`;
  document.body.append(activity);
  const find = id => activity.querySelector('#'+id);
  let nextBefore = null, generation = 0;
  async function loadActivity(older = false) {
    const current = ++generation;
    const params = new URLSearchParams();
    for (const key of ['account','source']) if (find('activity-'+key).value) params.set(key, find('activity-'+key).value);
    if (older && nextBefore) params.set('before',nextBefore);
    find('activity-status').textContent = 'Loading…';
    find('activity-older').disabled = true;
    try {
      const response = await window.fetch('/api/activity?'+params, {signal:AbortSignal.timeout(15000)});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to read activity');
      if (current !== generation) return;
      const rows = document.createDocumentFragment();
      for (const record of data.records) {
        const row = document.createElement('tr');
        for (const value of [new Date(record.started*1000).toLocaleString(),
          record.account ? record.account+' / '+record.role : 'Unauthenticated',
          record.session_ref ? record.session_ref.slice(0,12) : '—', record.source,
          record.operation, record.target || '—', record.result.replaceAll('_',' ') + (record.http_status ? ' · HTTP '+record.http_status : '')]) {
          const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
        }
        row.children[2].title = record.session_ref || 'No authenticated session';
        if (record.finished) row.children[6].title = 'Finished '+new Date(record.finished*1000).toLocaleString();
        rows.append(row);
      }
      if (!older) find('activity-records').replaceChildren();
      find('activity-records').append(rows);
      nextBefore = data.next_before;
      find('activity-older').hidden = !nextBefore;
      find('activity-status').textContent = data.recording_error
        ? 'Activity recording has failed since startup; records may be incomplete. Check server storage.'
        : find('activity-records').children.length ? 'Newest first. Refresh for current results.' : 'No activity records yet.';
    } catch (error) { if (current === generation) find('activity-status').textContent = error.message; }
    finally { if (current === generation) find('activity-older').disabled = false; }
  }
  activityButton.onclick = () => { activity.showModal(); loadActivity(); };
  find('activity-close').onclick = () => activity.close();
  find('activity-refresh').onclick = () => loadActivity();
  find('activity-older').onclick = () => loadActivity(true);
  for (const key of ['account','source']) find('activity-'+key).onchange = () => loadActivity();
  state.ready.then(() => { activityButton.hidden = !(state.enabled && state.role === 'admin'); }).catch(() => {});
})();
