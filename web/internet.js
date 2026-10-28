// Gateway-only saves allow live firewall updates without changing other lab devices.
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
      <h3>Inbound port forwarding</h3>
      <p class="field-help">Publish a host TCP/UDP port to a node's IPv4 address and service port. Defaults to localhost on the Docker host; enter a host LAN IPv4 address for remote access. Routed targets need a return route above. The node must have the service enabled.</p>
      <ul class="internet-firewall-rules" data-part="forwards" aria-label="Inbound port forwards"></ul>
      <button type="button" data-part="add-forward">Add port forward</button> <span data-part="forward-count" class="field-help"></span>
      <p class="field-help">Stop only this Internet node to change port forwards. Forwarded connections are separate from outbound firewall rules; targets see the Internet node's lab IP as their client. Allowed source restricts LAN clients before translation; leave it blank for localhost bindings.</p>
      <h3>Outbound firewall</h3>
      <p class="field-help">Rules run from top to bottom; the first match wins. They apply to IPv4 traffic forwarded from the cloud subnet and return networks through the uplink, including reachable host/LAN destinations.</p>
      <label>When no rule matches<select data-part="default-action"><option value="allow">Allow</option><option value="deny">Deny</option></select></label>
      <ol class="internet-firewall-rules" data-part="rules" aria-label="Ordered outbound firewall rules"></ol>
      <button type="button" data-part="add-rule">Add rule</button> <span data-part="rule-count" class="field-help"></span>
      <p class="field-help">Blank addresses and ports match any. Use IPv4 hosts or networks and a port or range such as 1024-65535. Ports apply only to TCP/UDP.</p>
      <p class="field-help">Reply traffic is accepted; other inbound traffic is blocked unless explicitly port forwarded. DNS and ping to the gateway remain available outside these forwarding rules.</p>
      <p data-part="notice" class="field-help"></p><button type="submit" data-part="save" class="primary">Apply settings</button>
      </form><button class="console-resize" data-part="resize" aria-label="Resize Internet settings">◢</button>`;
    const q = name => panel.querySelector(`[data-part="${name}"]`);
    const config = n.internet || {address:'192.0.2.1/24',routes:[],uplink_subnet:''};
    let savedConfig = structuredClone(config);
    q('address').value = config.address; q('uplink').value = config.uplink_subnet || '';
    q('default-action').value = config.firewall?.default_action || 'allow';
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
    function addForward(forward={protocol:'tcp',bind_address:'127.0.0.1'}) {
      const row=document.createElement('li');
      row.innerHTML='<div class="internet-rule-toolbar"><strong>Port forward</strong><button type="button" data-forward-remove>Remove</button></div><div class="internet-rule-fields"></div>';
      for (const [key,label] of [['protocol','Protocol'],['bind_address','Host bind IPv4'],['external_port','Host port'],
          ['target_address','Target node IPv4'],['target_port','Target service port'],['source','Allowed source IPv4 / network']]) {
        const wrapper=document.createElement('label'), input=document.createElement(key==='protocol'?'select':'input');
        wrapper.textContent=label;input.dataset.forwardField=key;
        if (key==='protocol') for(const protocol of ['tcp','udp']) input.add(new Option(protocol.toUpperCase(),protocol));
        else if (key.endsWith('_port')) {input.type='number';input.min=1;input.max=65535;input.step=1;input.required=true;}
        else {input.placeholder=key==='source'?'Any':key==='bind_address'?'127.0.0.1':'192.0.2.2';input.required=key!=='source';}
        input.value=forward[key] || '';wrapper.append(input);row.querySelector('.internet-rule-fields').append(wrapper);
      }
      row.querySelector('[data-forward-remove]').onclick=()=>{row.remove();controls();};
      q('forwards').append(row);
    }
    (config.port_forwards || []).forEach(addForward);
    q('add-forward').onclick=()=>{addForward();controls();};
    function addRule(rule={action:q('default-action').value==='allow'?'deny':'allow',protocol:'any'}) {
      const row=document.createElement('li');
      row.innerHTML='<div class="internet-rule-toolbar"><strong data-rule-number></strong><button type="button" data-rule-up aria-label="Move rule up">↑</button><button type="button" data-rule-down aria-label="Move rule down">↓</button><button type="button" data-rule-remove aria-label="Remove firewall rule">Remove</button></div><div class="internet-rule-fields"></div>';
      const fields=row.querySelector('.internet-rule-fields');
      for (const [key,label,choices] of [
        ['action','Action',[['allow','Allow'],['deny','Deny']]],
        ['protocol','Protocol',[['any','Any IPv4'],['tcp','TCP'],['udp','UDP'],['icmp','ICMP']]],
        ['source','Source IPv4'],['destination','Destination IPv4'],
        ['source_port','Source port'],['destination_port','Destination port']]) {
        const wrapper=document.createElement('label'), input=document.createElement(choices?'select':'input');
        wrapper.textContent=label;input.dataset.ruleField=key;
        if (choices) for (const [value,text] of choices) input.add(new Option(text,value));
        else {input.placeholder='Any';input.autocomplete='off';}
        if (key.endsWith('_port')) {input.pattern='[0-9]{1,5}(-[0-9]{1,5})?';input.title='Port 1–65535 or ascending range, e.g. 1024-65535';}
        input.value=rule[key] || (key==='action'?'deny':key==='protocol'?'any':'');
        wrapper.append(input);fields.append(wrapper);
      }
      row.querySelector('[data-rule-field=protocol]').onchange=()=>{
        if (!['tcp','udp'].includes(row.querySelector('[data-rule-field=protocol]').value)) {
          row.querySelectorAll('[data-rule-field$="_port"]').forEach(e=>e.value='');
        }
        controls();
      };
      row.querySelector('[data-rule-up]').onclick=()=>{if(row.previousElementSibling) row.previousElementSibling.before(row);controls();};
      row.querySelector('[data-rule-down]').onclick=()=>{if(row.nextElementSibling) row.nextElementSibling.after(row);controls();};
      row.querySelector('[data-rule-remove]').onclick=()=>{row.remove();controls();};
      q('rules').append(row);
    }
    (config.firewall?.rules || []).forEach(addRule);
    q('add-rule').onclick=()=>{addRule();controls();};
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
      const readonly=busy || !authCan('admin'), running=state(id)==='running';
      panel.querySelectorAll('form input, form select, form button').forEach(e=>e.disabled=readonly);
      panel.querySelectorAll('[data-part=address], [data-part=uplink], [data-part=routes] input, [data-part=routes] button, [data-part=add], [data-part=forwards] input, [data-part=forwards] select, [data-part=forwards] button, [data-part=add-forward]').forEach(e=>e.disabled=readonly || running);
      q('add').disabled ||= q('routes').children.length>=32;
      q('add-forward').disabled ||= q('forwards').children.length>=32;
      q('forward-count').textContent=q('forwards').children.length+' / 32 port forwards';
      q('add-rule').disabled ||= q('rules').children.length>=64;
      q('rule-count').textContent=q('rules').children.length+' / 64 rules';
      [...q('rules').children].forEach((row,index,rows)=>{
        row.querySelector('[data-rule-number]').textContent='Rule '+(index+1);
        row.querySelector('[data-rule-up]').disabled ||= index===0;
        row.querySelector('[data-rule-down]').disabled ||= index===rows.length-1;
        const ports=['tcp','udp'].includes(row.querySelector('[data-rule-field=protocol]').value);
        row.querySelectorAll('[data-rule-field$="_port"]').forEach(e=>e.disabled=readonly || !ports);
      });
      q('start').disabled=busy || state(id)==='running' || !authCan('admin');
      q('stop').disabled=busy || state(id)!=='running' || !authCan('write');
      q('save').textContent=running?'Apply firewall':'Apply settings';
      q('notice').textContent=!authCan('admin')?'Only administrators can change cloud settings.':running?'Firewall changes apply live and are saved. Stop only this Internet node to change its address, return routes, uplink subnet or port forwards.':'Settings apply on the next start. Other lab devices can keep running.';
    }
    panel.querySelector('form').onsubmit=async event=>{
      event.preventDefault();
      if (busy || !authCan('admin')) return;
      const routes=[...q('routes').rows].map(row=>Object.fromEntries([...row.querySelectorAll('input')].map(e=>[e.dataset.field,e.value.trim()])));
      const rules=[...q('rules').children].map(row=>Object.fromEntries([...row.querySelectorAll('[data-rule-field]')].map(e=>[e.dataset.ruleField,e.value.trim()])));
      const firewall={default_action:q('default-action').value,rules};
      const port_forwards=[...q('forwards').children].map(row=>Object.fromEntries([...row.querySelectorAll('[data-forward-field]')].map(e=>[
        e.dataset.forwardField,e.dataset.forwardField.endsWith('_port')?Number(e.value):e.value.trim()])));
      // Ignore unsaved network edits if the gateway was started while this window was open.
      const internet=state(id)==='running'?{...savedConfig,firewall}:
        {address:q('address').value.trim(),uplink_subnet:q('uplink').value.trim(),routes,firewall,port_forwards};
      await perform('Applying Internet settings…',async()=>{
        const result=await api(`/api/nodes/${id}/internet`,'PUT',{internet,expected_internet:savedConfig});
        savedConfig=structuredClone(result.topology.nodes.find(node=>node.id===id).internet);
        return result;
      });
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
