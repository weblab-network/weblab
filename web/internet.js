// Internet settings use the normal administrator-only topology save contract.
(() => {
  const windows = new Map();
  function open(id) {
    const n = nodeById(id);
    if (!n || n.type !== 'cloud') return;
    if (windows.has(id)) { bringConsoleForward(windows.get(id).panel); return; }
    const panel = document.createElement('section');
    panel.className = 'instructions-panel floating internet-panel';
    panel.setAttribute('aria-label', 'Internet gateway settings');
    panel.innerHTML = `<div class="console-toolbar" data-part="toolbar">
      <button class="console-move" data-part="move" aria-label="Move Internet settings">⠿</button>
      <strong class="toolbar-scroll" data-part="title"></strong><button data-part="start">Start</button><button data-part="stop">Stop</button>
      <button data-part="maximize" aria-label="Maximize Internet settings">↗</button><button data-part="close" aria-label="Close Internet settings">×</button></div>
      <form class="internet-content"><p data-part="status" role="status"></p>
      <label>Gateway IPv4 / prefix<input data-part="address" placeholder="192.0.2.1/24" required></label>
      <p class="field-help">Connect eth0 to your lab. A directly connected PC with blank addressing gets an address, this gateway and DNS automatically. Configure routers manually.</p>
      <h3>Return routes</h3><p class="field-help">For networks behind a router, add their prefixes and the router's address on the cloud subnet. These networks also receive outbound NAT.</p>
      <div class="internet-routes"><table><thead><tr><th>Destination network</th><th>Via router</th><th></th></tr></thead><tbody data-part="routes"></tbody></table></div>
      <button type="button" data-part="add">Add route</button>
      <details><summary>Docker uplink</summary><label>Subnet <span class="optional">optional</span><input data-part="uplink" placeholder="Automatic"></label><p class="field-help">Choose a non-overlapping subnet if Docker's automatic allocation conflicts with your lab. Uses the host's outbound connectivity.</p></details>
      <h3>Access policy</h3><p>Outbound IPv4 from the cloud subnet and listed return networks is allowed. Reply traffic is accepted; unsolicited inbound traffic is blocked. DNS is available at the gateway address.</p>
      <p class="field-help">This can reach host/LAN destinations too; NAT is not an Internet-only filter. Custom firewall rules and inbound port forwarding are not available yet.</p>
      <p data-part="notice" class="field-help"></p><button type="submit" data-part="save" class="primary">Apply settings</button>
      </form><button class="console-resize" data-part="resize" aria-label="Resize Internet settings">◢</button>`;
    const q = name => panel.querySelector(`[data-part="${name}"]`);
    const config = n.internet || {address:'192.0.2.1/24',routes:[],uplink_subnet:''};
    q('address').value = config.address; q('uplink').value = config.uplink_subnet || '';
    function addRoute(route={destination:'',via:''}) {
      const row = document.createElement('tr');
      for (const [key,label] of [['destination','Destination network'],['via','Next-hop address']]) {
        const cell=document.createElement('td'), input=document.createElement('input');
        input.value=route[key]; input.dataset.field=key; input.setAttribute('aria-label',label); input.required=true;
        cell.append(input);row.append(cell);
      }
      const cell=document.createElement('td'), remove=document.createElement('button');
      remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Remove return route');
      remove.onclick=()=>{row.remove();refresh();};cell.append(remove);row.append(cell);q('routes').append(row);
    }
    config.routes.forEach(addRoute);
    q('add').onclick=()=>{addRoute();refresh();};
    $('console-windows').append(panel);
    const view={panel,rect:null,maximized:false,applyLayout,controls,close};
    windows.set(id,view);documentWindows.add(view);
    function close() { windows.delete(id);documentWindows.delete(view);panel.remove(); }
    function applyLayout() {
      const bounds=consoleViewport();
      view.rect=boundedConsoleRect(view.rect || {x:bounds.x+60,y:bounds.y+40,width:680,height:670});
      const r=view.maximized?bounds:view.rect;
      Object.assign(panel.style,{left:r.x+'px',top:r.y+'px',width:r.width+'px',height:r.height+'px'});
      q('move').hidden=q('resize').hidden=view.maximized;
      q('maximize').setAttribute('aria-pressed',String(view.maximized));
    }
    function controls() {
      const node=nodeById(id);
      if (!node) {close();return;}
      q('title').textContent=node.name+' · Internet';
      q('status').textContent=state(id)+(statuses[id]?.error ? ' · '+statuses[id].error : '');
      const readonly=busy || hasRunning() || !authCan('admin');
      panel.querySelectorAll('form input, form button').forEach(e=>e.disabled=readonly);
      q('add').disabled ||= q('routes').children.length>=32;
      q('start').disabled=busy || state(id)==='running' || !authCan('admin');
      q('stop').disabled=busy || state(id)!=='running' || !authCan('write');
      q('notice').textContent=!authCan('admin')?'Only administrators can change cloud settings.':hasRunning()?'Stop all nodes before changing gateway settings.':'Settings are saved with the topology; they apply on the next start.';
    }
    panel.querySelector('form').onsubmit=async event=>{
      event.preventDefault();
      if (busy || hasRunning() || !authCan('admin')) return;
      const routes=[...q('routes').rows].map(row=>Object.fromEntries([...row.querySelectorAll('input')].map(e=>[e.dataset.field,e.value.trim()])));
      await structuralEdit(()=>{nodeById(id).internet={address:q('address').value.trim(),uplink_subnet:q('uplink').value.trim(),routes};});
      controls();
    };
    q('start').onclick=()=>perform('Starting Internet gateway…',()=>api(`/api/nodes/${id}/start`,'POST',{}));
    q('stop').onclick=()=>stopDevices(id);
    q('close').onclick=close;
    q('maximize').onclick=()=>{view.maximized=!view.maximized;applyLayout();};
    for (const [part,kind] of [['toolbar','move'],['resize','resize']]) bindConsolePointer(q(part),kind,view);
    for (const [part,kind] of [['move','move'],['resize','resize']]) bindConsoleKeys(q(part),kind,view);
    for (const event of ['pointerdown','focusin']) panel.addEventListener(event,()=>bringConsoleForward(panel));
    applyLayout();controls();bringConsoleForward(panel);
  }
  function refresh() { for (const view of [...windows.values()]) view.controls(); }
  window.weblabInternet={open,refresh};
  $('cloud-settings-open').onclick=()=>open(selected);
})();
