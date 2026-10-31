// Deployment-local SSH public-key grants. Never stored in topology exports.
(() => {
  const dialog=document.createElement('dialog');
  dialog.id='external-access-dialog';dialog.setAttribute('aria-labelledby','external-access-title');
  dialog.innerHTML=`<h2 id="external-access-title">External SSH access</h2>
    <p data-ssh-status role="status"></p><p class="field-help">Console keys join the shared device consoles. Internet keys grant a lab shell through the selected gateway. Private keys stay on your computer.</p>
    <form data-ssh-form><label>Label<input name="label" required maxlength="80" placeholder="My laptop"></label>
    <label>Public key<textarea name="key" required rows="3" placeholder="ssh-ed25519 AAAA…"></textarea></label>
    <label>Access<select name="scope"><option value="console">Device consoles</option><option value="gateway">Internet SSH shell</option></select></label>
    <label data-ssh-role>Console role<select name="role"><option value="reader">Reader</option><option value="writer">Writer</option><option value="admin">Administrator</option></select></label>
    <label data-ssh-node hidden>Internet node<select name="node"></select></label>
    <button type="submit">Add key</button></form><ul data-ssh-keys></ul>
    <p data-ssh-error role="alert"></p><div class="dialog-actions"><button data-ssh-close>Close</button></div>`;
  document.body.append(dialog);
  const form=dialog.querySelector('form'), error=dialog.querySelector('[data-ssh-error]');
  let active=false;
  async function refresh() {
    const data=await api('/api/external-access');
    dialog.querySelector('[data-ssh-status]').textContent=data.enabled && data.available
      ? `Console SSH ${data.bind}:${data.port} on the Docker host · ${data.host_key || 'SSH service has not registered its host key yet'}`
      : 'Console endpoint disabled. Enable the optional SSH Compose service (docs/external-access.md).';
    const list=dialog.querySelector('[data-ssh-keys]');list.replaceChildren();
    for(const key of data.keys) {
      const row=document.createElement('li'), label=document.createElement('p'), revoke=document.createElement('button');
      label.textContent=`${key.label} · ${key.scope==='console'?key.role:'Internet '+(nodeById(key.node)?.name || key.node)} · ${key.valid?'enabled':'inactive'} · ${key.fingerprint}`;
      revoke.textContent='Revoke';revoke.className='danger-text';
      revoke.onclick=async()=>{if(active || !confirm('Revoke this key and disconnect its SSH sessions?'))return;
        await change({action:'revoke',id:key.id});};
      row.append(label,revoke);list.append(row);
    }
    return data;
  }
  async function change(data) {
    active=true;error.textContent='';
    try {await api('/api/external-access','POST',data);await refresh();return true;}
    catch(e){error.textContent=e.message;return false;}
    finally {active=false;}
  }
  form.elements.scope.onchange=()=>{
    const gateway=form.elements.scope.value==='gateway';
    dialog.querySelector('[data-ssh-role]').hidden=gateway;
    dialog.querySelector('[data-ssh-node]').hidden=!gateway;
  };
  form.onsubmit=async event=>{event.preventDefault();if(active)return;
    const data=Object.fromEntries(new FormData(form));
    if(await change({...data,action:'add'})){form.elements.key.value='';form.elements.label.value='';}
  };
  dialog.querySelector('[data-ssh-close]').onclick=()=>dialog.close();
  async function open(node) {
    if(!authCan('admin'))return;
    form.elements.node.replaceChildren();
    for(const n of topology.nodes.filter(n=>n.type==='cloud'))form.elements.node.add(new Option(n.name,n.id));
    form.elements.scope.value=node?'gateway':'console';
    if(node)form.elements.node.value=node;
    form.elements.scope.onchange();error.textContent='';dialog.showModal();
    try {await refresh();} catch(e){error.textContent=e.message;}
  }
  async function copy(node) {
    try {
      const data=(await api('/api/state')).external_consoles;
      if(!data?.enabled)throw new Error('Enable external consoles first (External SSH access settings).');
      const command=`ssh -t -p ${data.port} console@${data.bind==='127.0.0.1'?'127.0.0.1':location.hostname} console ${node}`;
      await copyCommand(command);
    } catch(e){toast(e.message);}
  }
  async function copyCommand(command) {
    try {await navigator.clipboard.writeText(command);toast('SSH command copied');}
    catch {window.prompt('Copy this SSH command:',command);}
  }
  window.weblabExternalAccess={open,copy,copyCommand};
  $('external-access-open').onclick=()=>{ $('storage-dialog').close();open(); };
  $('copy-console-ssh').onclick=()=>copy(selected);
})();
