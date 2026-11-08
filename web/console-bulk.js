/* Human-initiated pastes through the existing shared console connections. */
'use strict';
(() => {
  const dialog = $('console-bulk-dialog'), commands = $('console-bulk-commands');
  const targets = $('console-bulk-targets'), send = $('console-bulk-send');
  let rows = [], timer = null, sending = false, submitted = false, generation = 0;
  const identity = node => JSON.stringify([node.id, node.type, node.image, node.iol_id]);
  const family = node => node.type === 'pc' ? 'Alpine' : isLl2s(node) ? 'LL2S' : isFrr(node) ? 'FRR' :
    isJunos(node) ? 'Junos' : isExos(node) ? 'EXOS' : isVeos(node) ? 'Arista EOS' : 'Cisco';
  function unavailable(row) {
    const node = nodeById(row.id), session = consoleSessions.get(row.id);
    if (!authCan('write')) return 'Read-only account';
    if (!node || identity(node) !== row.identity) return 'Device changed';
    if (state(row.id) !== 'running') return 'Stopped';
    if (session?.readonly) return 'Read-only console';
    if (session?.agentBusy) return 'Agent using console';
    if (session?.locked && !session.lockMine) return 'Locked by another station';
    return '';
  }
  function input() {
    // Preserve leading/trailing empty lines. A final command without a newline
    // is submitted too. Literal backslash text is not interpreted as escapes.
    const text = commands.value.replace(/\r\n?/g, '\n');
    if (!text.length) throw new Error('Enter commands or blank lines to send.');
    const value = text.replace(/\n/g, '\r') + (text.endsWith('\n') ? '' : '\r');
    if (/[\x00-\x09\x0b-\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029]/.test(value))
      throw new Error('Use ordinary text and newlines; send control keys from the console toolbar.');
    if (value.split('\r').length - 1 > 32 || new TextEncoder().encode(value).length > 4096)
      throw new Error('Use at most 32 lines and 4096 UTF-8 bytes, including Enter keys.');
    return value;
  }
  function update() {
    for (const row of rows) {
      const reason = unavailable(row), session = consoleSessions.get(row.id);
      row.check.disabled = sending || submitted || !!reason;
      row.status.textContent = row.result || reason || (session?.socket?.readyState === WebSocket.OPEN && session.lockReady
        ? 'Connected' : session ? 'Console not connected' : 'Console will open on send');
    }
    let error = '';
    try { input(); } catch (e) { error = e.message; }
    $('console-bulk-summary').textContent = sending ? 'Sending… Closing cancels devices still waiting.' : submitted
      ? 'Batch finished. Inspect each console; submission does not confirm execution. Reopen this dialog for another batch.'
      : error || `${rows.filter(r => r.check.checked && !unavailable(r)).length} devices selected. Blank lines press Enter.`;
    send.disabled = sending || submitted || !!error || !rows.some(r => r.check.checked && !unavailable(r));
    commands.disabled = sending || submitted;
    for (const id of ['console-bulk-select', 'console-bulk-none', 'console-bulk-example']) $(id).disabled = sending || submitted;
  }
  function open() {
    if (dialog.open) { window.focus(); return; }
    generation++; sending = submitted = false;
    targets.replaceChildren(); rows = [];
    for (const node of topology.nodes.filter(n => !isDiagram(n) && n.type !== 'cloud')) {
      const label = document.createElement('label'), check = document.createElement('input');
      check.type = 'checkbox'; check.value = node.id;
      const name = document.createElement('span'), status = document.createElement('span');
      name.textContent = `${node.name} · ${family(node)}`; status.className = 'muted';
      label.append(check, name, status); targets.append(label);
      rows.push({id:node.id, identity:identity(node), check, status});
      check.addEventListener('change', update);
    }
    if (!rows.length) targets.textContent = 'Add and start devices to use their consoles.';
    update(); dialog.showModal(); window.focus();
    timer = setInterval(update, 250);
  }
  async function deliver(row, value, run) {
    const active = () => generation === run && dialog.open;
    if (!active()) return;
    const reason = unavailable(row);
    if (reason) { row.result = 'Skipped · ' + reason; return; }
    try {
      if (!consoleSessions.has(row.id)) openConsole(row.id);
    } catch (_) { row.result = 'Not sent · console could not open'; return; }
    const session = consoleSessions.get(row.id), socket = session?.socket;
    if (!socket) { row.result = 'Not sent · console unavailable'; return; }
    const deadline = Date.now() + 4000;
    while (active() && session.socket === socket && !session.lockReady &&
           socket.readyState < WebSocket.CLOSING && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    if (!active()) return;
    if (consoleSessions.get(row.id) !== session || session.socket !== socket || unavailable(row)) {
      row.result = 'Not sent · ' + (unavailable(row) || 'connection changed'); return;
    }
    try {
      row.result = sendConsoleInput(session, value) ? 'Submitted · verify console output' : 'Not sent · console not writable';
    } catch (_) { row.result = 'Delivery uncertain · inspect console before retrying'; }
  }
  $('console-bulk-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (sending || submitted || send.disabled) return;
    let value;
    try { value = input(); } catch (_) { update(); return; }
    const selected = rows.filter(r => r.check.checked), run = generation;
    sending = true; update();
    try {
      for (const row of selected) {
        await deliver(row, value, run);
        if (generation !== run || !dialog.open) break;
        update();
      }
    } finally {
      if (generation === run) { sending = false; submitted = true; update(); }
    }
  });
  $('console-bulk-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { generation++; clearInterval(timer); timer = null; });
  $('console-bulk-select').onclick = () => { rows.forEach(row => row.check.checked = !unavailable(row)); update(); };
  $('console-bulk-none').onclick = () => { rows.forEach(row => row.check.checked = false); update(); };
  $('console-bulk-example').onclick = () => {
    commands.setRangeText('\n\nenable\nterminal length 0\n', commands.selectionStart, commands.selectionEnd, 'end');
    commands.focus(); update();
  };
  commands.addEventListener('input', update);
  workspaceListen('click', event => {
    if (event.target.closest('[data-console-bulk]')) open();
  });
})();
