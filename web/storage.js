/* Host filesystem metadata only; never reads guest filesystems. */
(() => {
  'use strict';
  const dialog = document.getElementById('storage-dialog');
  const content = document.getElementById('storage-content');
  const refresh = document.getElementById('storage-refresh');
  const buttons = document.querySelectorAll('[data-storage]');
  let loading = false;
  const size = n => n == null ? 'Unavailable' : n >= 1024 ** 3
    ? `${(n / 1024 ** 3).toFixed(2)} GiB` : n >= 1024 ** 2
    ? `${(n / 1024 ** 2).toFixed(1)} MiB` : n >= 1024
    ? `${(n / 1024).toFixed(1)} KiB` : `${n} B`;
  function element(tag, text, cls) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  function summary(data) {
    const filesystems = data?.filesystems || [];
    const severity = ['critical', 'low', 'unknown'].find(s => filesystems.some(f => f.level === s));
    const title = filesystems.map(f => `${f.label}: ${size(f.free_bytes)} free`).join('\n');
    for (const button of buttons) {
      button.textContent = `Storage${severity ? ' · ' + severity : ''}`;
      button.dataset.level = severity || 'ok';
      button.title = title || 'View server disk usage';
      button.setAttribute('aria-label', `Storage${severity ? ': ' + severity : ''}`);
    }
  }
  function table(headers, rows) {
    const table = element('table');
    const head = element('tr');
    for (const text of headers) { const th = element('th', text); th.scope = 'col'; head.append(th); }
    const thead = element('thead'); thead.append(head); table.append(thead);
    const body = element('tbody');
    for (const row of rows) {
      const tr = element('tr');
      for (const text of row) tr.append(element('td', text));
      body.append(tr);
    }
    table.append(body);
    const wrapper = element('div', null, 'storage-table'); wrapper.append(table);
    return wrapper;
  }
  async function load() {
    if (loading) return;
    loading = true; refresh.disabled = true;
    document.getElementById('storage-message').textContent = 'Reading storage usage…';
    try {
      const response = await fetch('/api/storage');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Storage report unavailable');
      summary(data);
      content.replaceChildren();
      content.append(table(['Filesystem use', 'Available', 'Capacity', 'Status'], data.filesystems.map(f =>
        [f.label, size(f.free_bytes), size(f.total_bytes), f.level])));
      content.append(element('p', 'Rows on the same filesystem share the available space. These are the server’s mounted volumes, not free space inside a guest or the Docker Engine’s separate storage.', 'muted'));
      content.append(element('h3', `Node storage · ${size(data.node_allocated_bytes)} allocated`));
      content.append(table(['Device / saved directory', 'Allocated', 'VM disks', 'Logs'], data.nodes.map(n =>
        [`${n.name}${n.retained ? ' (retained)' : ''}${n.complete ? '' : ' (partial)'}\n${n.id}`, size(n.allocated_bytes), size(n.disk_bytes), size(n.log_bytes)])));
      if (!data.nodes.length) content.append(element('p', 'No node storage yet.'));
      document.getElementById('storage-message').textContent =
        `${data.complete ? 'Measured' : 'Partial report; some entries could not be measured'}: ${new Date(data.measured_at).toLocaleString()}. Reports are cached for 10 seconds.`;
    } catch (error) {
      document.getElementById('storage-message').textContent = error.message + ' Any previous values below may be stale.';
    } finally { loading = false; refresh.disabled = false; }
  }
  window.addEventListener('weblab-storage', event => summary(event.detail));
  for (const button of buttons) button.onclick = () => { if (!dialog.open) dialog.showModal(); load(); };
  refresh.onclick = load;
  document.getElementById('storage-close').onclick = () => dialog.close();
  setInterval(() => { if (dialog.open && !document.hidden) load(); }, 15000);
})();
