// Temporary per-link captures. Packet bytes/text are always rendered as text.
(() => {
  const windows = new Map();
  const protocols = ['All','ARP','IPv4','IPv6','ICMP','ICMPv6','TCP','UDP','OSPF','BGP','VRRP','IS-IS','RIP','EIGRP','LLDP','STP','PVST+','CDP','DTP','VTP','LACP','VLAN','Other'];
  function open(linkId) {
    if (!authCan('write')) return;
    if (windows.has(linkId)) { bringConsoleForward(windows.get(linkId).panel); return; }
    if (windows.size >= 4) { toast('Close a traffic monitor before opening another.', true); return; }
    const link = topology.links.find(l => l.id === linkId);
    if (!link) return;
    const panel = document.createElement('section');
    panel.className = 'instructions-panel floating traffic-panel';
    panel.setAttribute('aria-label','Traffic monitor');
    panel.innerHTML = `<div class="console-toolbar" data-part="toolbar">
      <button class="console-move" data-part="move" aria-label="Move traffic monitor">⠿</button>
      <div class="toolbar-scroll">
      <strong class="topology-window-title">Traffic monitor</strong>
      <div class="traffic-buttons"><button data-part="start">Start capture</button><button data-part="stop" disabled>Stop</button>
      <button data-part="download" disabled>Download PCAP</button>
      <label>Protocol <select data-part="protocol" aria-label="Filter protocol"></select></label>
      <label>Direction <select data-part="direction" aria-label="Filter direction"><option value="all">Both</option><option value="a_to_b">A → B</option><option value="b_to_a">B → A</option></select></label>
      <label><input type="checkbox" data-part="follow" checked> Follow</label></div></div>
      <details class="console-menu traffic-help" data-part="help"><summary aria-label="Traffic capture information">ⓘ</summary>
      <div class="console-menu-list"><p>Up to 60 seconds, 2,000 frames or 8 MiB. Filters affect this view; PCAP includes every captured frame. New capture replaces this buffer.</p>
      <p>Capture observes link ingress. Forwarded means sent to the peer socket, not confirmed guest reception.</p></div></details>
      <button data-part="maximize" aria-label="Maximize traffic monitor" aria-pressed="false">↗</button>
      <button data-part="close" aria-label="Close traffic monitor" title="Close and discard capture">×</button>
      </div><div class="traffic-meta">
      <span class="traffic-endpoints" data-part="endpoints"></span>
      <span data-part="status" role="status">Ready</span></div>
      <div class="traffic-panes" data-part="panes"><div class="traffic-table" data-part="scroll"><table><thead><tr><th>#</th><th>Time (s)</th><th>Direction</th><th>Source</th><th>Destination</th><th>Protocol</th><th>Bytes</th><th>Relay</th><th>Info</th></tr></thead><tbody data-part="rows"></tbody></table></div>
      <div class="traffic-divider" data-part="divider" role="separator" tabindex="0" aria-label="Resize packet list and details" aria-orientation="horizontal" aria-valuemin="20" aria-valuemax="80" aria-valuenow="60" title="Drag to resize packet details; use arrow keys when focused"><span></span></div>
      <div class="traffic-detail-pane" data-part="detail-pane">
      <div class="traffic-detail-toolbar"><button data-part="decoded" aria-pressed="true">Decoded</button><button data-part="hex" aria-pressed="false">Hex / ASCII</button><span data-part="decode-status" role="status"></span></div>
      <pre class="traffic-detail" data-part="detail" tabindex="0">Select a packet number to inspect its protocols and bytes.</pre></div></div>
      <button class="console-resize" data-part="resize" aria-label="Resize traffic monitor">◢</button>`;
    const q = name => panel.querySelector(`[data-part="${name}"]`);
    const help = q('help'), helpButton = help.querySelector('summary');
    let helpTimer;
    help.addEventListener('pointerenter', event => { clearTimeout(helpTimer); if (event.pointerType === 'mouse') help.open = true; });
    help.addEventListener('pointerleave', () => {
      helpTimer = setTimeout(() => { if (!help.contains(document.activeElement)) help.open = false; },150);
    });
    helpButton.addEventListener('focus', () => { if (helpButton.matches(':focus-visible')) help.open = true; });
    help.addEventListener('focusout', event => { if (!help.contains(event.relatedTarget)) help.open = false; });
    for (const protocol of protocols) q('protocol').add(new Option(protocol, protocol));
    const label = side => `${nodeById(link[side].node).name} · ${link[side].port}`;
    q('endpoints').textContent = `A: ${label('a')} ↔ B: ${label('b')}`;
    $('console-windows').append(panel);
    let id = null, packets = [], cursor = 0, running = false, closed = false, pending = false, polling = false, complete = false, selected = null;
    let detailMode = 'decoded', decodedText = '', hexText = '', tableShare = 60;
    function split(value) {
      tableShare = Math.max(20, Math.min(80, value));
      q('scroll').style.flex = `${tableShare} 1 0px`;
      q('detail-pane').style.flex = `${100-tableShare} 1 0px`;
      q('divider').setAttribute('aria-valuenow', String(Math.round(tableShare)));
      q('divider').setAttribute('aria-valuetext', `Packet list ${Math.round(tableShare)}%, details ${100-Math.round(tableShare)}%`);
    }
    let splitDrag = null;
    q('divider').onpointerdown = event => {
      if (event.button !== 0 || splitDrag) return;
      event.preventDefault();
      q('divider').focus(); q('divider').setPointerCapture(event.pointerId);
      splitDrag = {id:event.pointerId, y:event.clientY, share:tableShare, height:q('panes').clientHeight-q('divider').offsetHeight};
    };
    q('divider').onpointermove = event => {
      if (splitDrag && event.pointerId === splitDrag.id) split(splitDrag.share + 100*(event.clientY-splitDrag.y)/Math.max(1,splitDrag.height));
    };
    q('divider').onpointerup = event => {
      if (splitDrag && event.pointerId === splitDrag.id) { splitDrag=null; q('divider').releasePointerCapture(event.pointerId); }
    };
    q('divider').onpointercancel = q('divider').onlostpointercapture = () => {splitDrag=null;};
    q('divider').ondblclick = () => split(60);
    q('divider').onkeydown = event => {
      const values = {ArrowUp:tableShare-5, ArrowDown:tableShare+5, Home:20, End:80};
      if (Object.hasOwn(values,event.key)) {event.preventDefault();split(values[event.key]);}
    };
    function showDetail() {
      q('decoded').setAttribute('aria-pressed',String(detailMode==='decoded'));
      q('hex').setAttribute('aria-pressed',String(detailMode==='hex'));
      q('detail').textContent = (detailMode === 'decoded' ? decodedText : hexText) || 'Select a packet number to inspect its protocols and bytes.';
    }
    q('decoded').onclick = () => {detailMode='decoded';showDetail();};
    q('hex').onclick = () => {detailMode='hex';showDetail();};
    split(tableShare);
    const view = {panel, rect:null, maximized:false, applyLayout};
    windows.set(linkId, view); documentWindows.add(view);
    function applyLayout() {
      const bounds = consoleViewport();
      view.rect = boundedConsoleRect(view.rect || {x:bounds.x+30+windows.size*16,y:bounds.y+35+windows.size*16,width:1000,height:620});
      const rect = view.maximized ? bounds : view.rect;
      Object.assign(panel.style,{left:rect.x+'px',top:rect.y+'px',width:rect.width+'px',height:rect.height+'px'});
      q('move').hidden = q('resize').hidden = view.maximized;
      q('maximize').setAttribute('aria-pressed', String(view.maximized));
      q('maximize').setAttribute('aria-label', view.maximized ? 'Restore traffic monitor size' : 'Maximize traffic monitor');
      q('maximize').textContent = view.maximized ? '↙' : '↗';
      if (help.open) positionConsoleMenu(help);
    }
    function controls() {
      q('start').disabled = pending || running;
      q('start').textContent = id ? 'New capture' : 'Start capture';
      q('stop').disabled = pending || !running;
      q('download').disabled = pending || !id || !packets.length;
    }
    function render() {
      const filter = q('protocol').value, direction = q('direction').value;
      const rows = document.createDocumentFragment();
      for (const p of packets) {
        if (filter !== 'All' && !(filter === 'Other' ? p.protocol === 'Other' : p.tags.includes(filter))) continue;
        if (direction !== 'all' && p.direction !== direction) continue;
        const row = document.createElement('tr');
        const values = [p.number, (p.time-packets[0].time).toFixed(3), p.direction === 'a_to_b' ? 'A → B' : 'B → A', p.source, p.destination,
          p.protocol+(p.vlans.length ? ` · VLAN ${p.vlans.join('/')}` : ''), p.length, p.outcome, p.info];
        values.forEach((value, index) => {
          const td = document.createElement('td');
          if (!index) { const button = document.createElement('button'); button.textContent = value; button.onclick = () => detail(p); td.append(button); }
          else td.textContent = value;
          row.append(td);
        });
        rows.append(row);
      }
      q('rows').replaceChildren(rows);
      if (q('follow').checked) q('scroll').scrollTop = q('scroll').scrollHeight;
    }
    async function detail(packet) {
      const current = id; selected = packet.number;
      decodedText = hexText = `#${packet.number} · Loading packet details…`;
      q('decode-status').textContent = 'Decoding…'; showDetail();
      try {
        const data = await api(`/api/captures/${current}/packets/${packet.number}`);
        if (closed || id !== current || selected !== packet.number) return;
        const lines = [];
        for (let i=0; i<data.hex.length; i+=32) {
          const bytes = data.hex.slice(i,i+32).match(/../g) || [];
          const text = bytes.map(b=>{const n=parseInt(b,16);return n>=32 && n<127 ? String.fromCharCode(n) : '.';}).join('');
          lines.push((i/2).toString(16).padStart(4,'0')+'  '+bytes.join(' ').padEnd(47)+'  '+text);
        }
        hexText = `#${packet.number} · ${packet.protocol} · ${packet.length} bytes · ${packet.outcome}\n${packet.source} → ${packet.destination}\n${packet.info}\n\n${lines.join('\n')}${data.truncated ? '\nPreview limited to 4 KiB; PCAP includes the full frame.' : ''}`;
        decodedText = `#${packet.number} · ${packet.protocol} · ${packet.length} bytes · ${packet.outcome}\n\n${data.decode.text}`;
        q('decode-status').textContent = data.decode.status === 'ok' ? 'TShark · single packet' : `Decoder: ${data.decode.status}`;
        showDetail();
      } catch (error) {
        if (!closed && id === current && selected === packet.number) {
          decodedText=hexText=error.message; q('decode-status').textContent=''; showDetail();
        }
      }
    }
    async function poll() {
      if (!id || closed || pending || polling || complete) return;
      polling = true;
      const current = id;
      try {
        const data = await api(`/api/captures/${current}?after=${cursor}`);
        if (closed || id !== current) return;
        for (const packet of data.packets) {
          for (const name of [packet.protocol, ...packet.tags]) {
            if (![...q('protocol').options].some(option=>option.value===name)) q('protocol').add(new Option(name,name));
          }
        }
        packets.push(...data.packets); cursor = data.next; running = data.running;
        complete = !running && cursor >= data.total;
        q('status').textContent = `${running ? `Capturing · ${data.seconds_left}s left` : data.reason} · ${data.total} frames · ${(data.bytes/1024).toFixed(1)} KiB${data.capture_busy_drops ? ` · ${data.capture_busy_drops} monitor-busy skips across captures` : ''}${data.summary_decoder && data.summary_decoder !== 'ok' ? ' · Basic summaries (decoder '+data.summary_decoder+')' : ''}`;
        if (data.packets.length) render();
        controls();
      } catch (error) { if (!closed && id === current) { running = false; complete = true; q('status').textContent = error.message; controls(); } }
      finally { polling = false; }
    }
    async function start() {
      if (pending || running || closed) return;
      pending = true; controls();
      try {
        if (id) await api(`/api/captures/${id}/close`, 'POST', {}).catch(()=>{});
        id = null; packets = []; cursor = 0; complete = false; selected = null; render();
        decodedText=hexText=''; q('decode-status').textContent=''; showDetail();
        const result = await api(`/api/links/${linkId}/capture`, 'POST', {});
        if (closed) { await api(`/api/captures/${result.id}/close`, 'POST', {}); return; }
        id = result.id; running = true;
      } catch (error) { if (!closed) q('status').textContent = error.message; }
      finally { pending = false; controls(); }
      await poll();
    }
    q('start').onclick = start;
    q('stop').onclick = async () => {
      if (!id || pending) return;
      pending = true; controls();
      try { await api(`/api/captures/${id}/stop`, 'POST', {}); running = false; }
      catch (error) { q('status').textContent = error.message; }
      finally { pending = false; controls(); }
      await poll();
    };
    q('download').onclick = async () => {
      if (!id || pending) return;
      try {
        const response = await fetch(`/api/captures/${id}/pcap`);
        if (!response.ok) throw new Error((await response.json()).error || 'Download failed');
        const url = URL.createObjectURL(await response.blob()), a = document.createElement('a');
        a.href=url; a.download='weblab-traffic.pcap'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch (error) { q('status').textContent = error.message; }
    };
    const timer = setInterval(poll, 750);
    const leaving = () => { if (id) fetch(`/api/captures/${id}/close`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}', keepalive:true}).catch(()=>{}); };
    window.addEventListener('pagehide', leaving);
    q('close').onclick = () => {
      window.removeEventListener('pagehide', leaving);
      closed = true; clearInterval(timer); clearTimeout(helpTimer);
      if (id) api(`/api/captures/${id}/close`, 'POST', {}).catch(()=>{});
      documentWindows.delete(view); windows.delete(linkId); panel.remove();
    };
    q('maximize').onclick = () => { view.maximized=!view.maximized; applyLayout(); };
    q('protocol').onchange = q('direction').onchange = render;
    for (const [part, kind] of [['toolbar','move'],['resize','resize']]) bindConsolePointer(q(part),kind,view);
    for (const [part, kind] of [['move','move'],['resize','resize']]) bindConsoleKeys(q(part),kind,view);
    for (const event of ['pointerdown','focusin']) panel.addEventListener(event,()=>bringConsoleForward(panel));
    applyLayout(); bringConsoleForward(panel); start();
  }
  $('link-monitor').onclick = () => { const id=activeLinkAction; $('link-actions-dialog').close(); open(id); };
})();
