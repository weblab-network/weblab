// Optional assistant with browser-approved lab operations. Session capability stays in this browser tab.
(() => {
  const button = $('open-agent'), KEY = 'weblab.agent.session.v1';
  let token = '', info, state, polling = false, busy = false, signature = '', proposalSignature = '', approvalSignature = '', settingsDirty = false;
  let progressReceived = 0, connected = true, selectedTab = 'chat', historySignature = '';
  const drafts = new Map();
  const fileDrafts = new Map();
  const starterPrompt = 'Create an FRR router, an LL2S switch and two Alpine PCs for VLAN practice.';
  let attachments = [], readingFiles = false;
  const imageTypes = {png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp'};
  const textTypes = new Set(['txt','md','json','yaml','yml','cfg','conf','config','log','csv','ini','xml']);
  const tabs = ['chat','operations','proposals','history','settings'];
  let remember = false;
  try {
    token = sessionStorage.getItem(KEY) || localStorage.getItem(KEY) || '';
    remember = !!token && localStorage.getItem(KEY) === token;
    if (token) sessionStorage.setItem(KEY,token);
  } catch (_) {}
  const panel = document.createElement('section');
  panel.id = 'agent-panel'; panel.className = 'agent-panel floating'; panel.hidden = true;
  panel.setAttribute('aria-label', 'Weblab Agent');
  panel.innerHTML = `
    <div id="agent-toolbar" class="console-toolbar">
      <button id="agent-move" class="console-move" aria-label="Move Agent window" title="Drag or use arrow keys">⠿</button>
      <div class="toolbar-scroll"><strong>Agent</strong><span id="agent-mode" class="agent-mode">Read & preview</span>
      <button id="agent-stop" type="button" disabled>Stop</button><button data-arrange-windows>Arrange</button><button id="agent-maximize" aria-label="Maximize Agent" aria-pressed="false">↗</button></div>
      <button id="agent-hide" class="window-hide" aria-label="Hide Agent">−</button>
    </div>
    <div id="agent-tabs" class="agent-tabs console-tabs" role="tablist" aria-label="Agent views">
      <button id="agent-chat-tab" role="tab" aria-selected="true" aria-controls="agent-chat">Conversation</button>
      <button id="agent-operations-tab" role="tab" tabindex="-1" aria-selected="false" aria-controls="agent-operations">Operations</button>
      <button id="agent-proposals-tab" role="tab" tabindex="-1" aria-selected="false" aria-controls="agent-proposals">Proposals</button>
      <button id="agent-history-tab" role="tab" tabindex="-1" aria-selected="false" aria-controls="agent-history">History</button>
      <button id="agent-settings-tab" role="tab" tabindex="-1" aria-selected="false" aria-controls="agent-settings">Settings</button>
    </div>
    <button id="agent-pending" type="button" hidden></button>
    <p id="agent-status" role="status">Connecting…</p>
    <div id="agent-progress" hidden><span class="agent-spinner" aria-hidden="true"></span><span id="agent-progress-text"></span></div>
    <details id="agent-tool-error" hidden><summary>Latest tool error</summary><pre></pre></details>
    <div id="agent-chat" role="tabpanel" aria-labelledby="agent-chat-tab">
      <p id="agent-intro" class="agent-hint">Ask for a practice topology or inspect the current lab. Enable lab changes or console input in Settings. Review changes in Operations, or enable YOLO to preapprove enabled actions.</p>
      <div id="agent-messages" tabindex="0" aria-label="Conversation"></div>
      <form id="agent-prompt-form"><label for="agent-prompt">Message</label><textarea id="agent-prompt" rows="3" maxlength="16000" placeholder="${starterPrompt}"></textarea>
      <input id="agent-files" type="file" multiple accept=".png,.jpg,.jpeg,.webp,.txt,.md,.json,.yaml,.yml,.cfg,.conf,.config,.log,.csv,.ini,.xml" hidden>
      <div id="agent-attachments" aria-label="Pending attachments" hidden></div>
      <p id="agent-file-error" role="alert" hidden></p>
      <div class="agent-actions"><button id="agent-send" class="primary" type="submit">Send</button><button id="agent-attach" type="button">Attach files</button><button id="agent-new" type="button">New conversation</button><button id="agent-reconnect" type="button">Reconnect</button></div>
      <p id="agent-file-hint" class="agent-hint">Up to 4 files · 2 MiB total · text/configs up to 64 KiB total. Screenshots need an image-capable model. Files are sent with your message.</p></form>
    </div>
    <div id="agent-operations" role="tabpanel" aria-labelledby="agent-operations-tab" hidden>
      <p id="agent-operations-empty" class="agent-hint">No operations yet. Requests to change the lab and their results appear here.</p>
      <div id="agent-approvals" aria-label="Operation approvals"></div>
    </div>
    <div id="agent-proposals" role="tabpanel" aria-labelledby="agent-proposals-tab" hidden></div>
    <div id="agent-history" role="tabpanel" aria-labelledby="agent-history-tab" hidden>
      <p class="agent-hint">Conversations in this Agent session. Open one to continue with its model and your current permissions. Old approvals and proposal links are not restored.</p>
      <div id="agent-history-list"></div>
    </div>
    <form id="agent-settings" role="tabpanel" aria-labelledby="agent-settings-tab" hidden>
      <label>Provider<select id="agent-provider"><option value="ollama">Ollama</option><option value="openai">OpenAI · ChatGPT sign-in</option></select></label>
      <label>Endpoint<select id="agent-endpoint"></select></label>
      <div id="agent-openai-auth" hidden>
        <p class="agent-hint">Save OpenAI settings, then sign in with your ChatGPT account in a separate tab. You may need to enable device-code login in your ChatGPT security settings. Model access and usage limits depend on your account. This is for your private, self-hosted workspace.</p>
        <div class="agent-actions"><button id="agent-login" type="button">Sign in with OpenAI</button><button id="agent-account" type="button">Refresh account / models</button><button id="agent-logout" type="button">Sign out</button><button id="agent-login-cancel" type="button" hidden>Cancel sign-in</button></div>
        <p id="agent-auth-status" role="status">Not checked</p>
        <p id="agent-device-code" hidden>Enter code <strong></strong> in the OpenAI tab. <a href="https://auth.openai.com/codex/device" target="_blank" rel="noopener noreferrer">Open OpenAI sign-in ↗</a></p>
      </div>
      <label>Model<input id="agent-model" list="agent-model-list" maxlength="160" placeholder="gpt-oss:20b" required><datalist id="agent-model-list"></datalist></label>
      <label>Context window (tokens)<input id="agent-context" type="number" min="4096" max="131072" step="1024" required></label>
      <label>Turn deadline (seconds)<input id="agent-deadline" type="number" min="30" max="3600" required></label>
      <p class="agent-hint">Total time for one message, including model generation, tools and approval waits. New sessions default to 900 seconds (15 minutes). Completed actions remain if time runs out; inspect and continue in another turn.</p>
      <label class="agent-permission"><input id="agent-lab-changes" type="checkbox"> Allow topology apply and node starts</label>
      <label class="agent-permission"><input id="agent-console-input" type="checkbox"> Allow console input</label>
      <label class="agent-permission"><input id="agent-auto-approve" type="checkbox"> YOLO — automatically approve enabled actions</label>
      <p class="agent-hint">YOLO lets the agent replace a stopped topology, start nodes and send console commands when those permissions are enabled, without asking each time. It applies to this session and is off by default. Operation details remain visible. Console input can configure or disrupt devices; topology checks and human console locks still apply. Stop prevents further actions, but cannot undo accepted work.</p>
      <p class="agent-hint">Prompts, discovered topology and console/tool results go to the selected provider. OpenAI sends them to the cloud. Sign-in tokens remain in this session's companion storage, outside lab exports. Ollama uses your installed models and their temperature configuration; Weblab does not pull models.</p>
      <p class="agent-hint">Changing provider, endpoint, model or context size starts a new conversation. Deadline and permission changes keep the conversation; save them between turns. Context size must fit your model server. This is an owner-operated workspace, not a public multi-user service.</p>
      <label class="agent-permission"><input id="agent-remember" type="checkbox"> Remember this Agent session on this browser</label>
      <p class="agent-hint">Off by default. Remembering lets this browser profile reopen history after closing the tab. Anyone using this profile can access the session. Access expires after 24 hours of inactivity. Close session removes its history and sign-in storage.</p>
      <div class="agent-actions"><button class="primary" id="agent-save" type="submit">Save settings</button><button id="agent-check" type="button">Check model</button><button id="agent-end" type="button">Close session</button></div>
      <p id="agent-check-result" role="status"></p>
    </form>
    <button id="agent-resize" class="console-resize" aria-label="Resize Agent window" title="Drag or use arrow keys">◢</button>`;
  $('console-windows').append(panel);
  const approvalDialog = document.createElement('dialog');
  approvalDialog.id = 'agent-approval-dialog';
  approvalDialog.setAttribute('aria-labelledby','agent-approval-title');
  approvalDialog.innerHTML = `
    <h2 id="agent-approval-title">Agent approval required</h2>
    <p id="agent-approval-summary"></p>
    <details open><summary>Exact operation and context</summary><pre id="agent-approval-detail"></pre></details>
    <p id="agent-approval-expiry"></p>
    <label class="agent-permission"><input id="agent-approval-yolo" type="checkbox"> Automatically approve subsequent operations in this session (YOLO)</label>
    <p>Only actions enabled in Settings are allowed. Console commands can change or disrupt devices. You can turn YOLO off in Settings between turns.</p>
    <p id="agent-approval-error" role="alert" hidden></p>
    <div class="dialog-actions"><button id="agent-approval-later" type="button" autofocus>Later</button><button id="agent-approval-deny" type="button">Deny</button><button id="agent-approval-accept" class="primary" type="button">Approve once</button></div>`;
  document.body.append(approvalDialog);
  let approvalId = '', deciding = false;
  const dismissedApprovals = new Set();
  const operationNames = {'apply':'Replace topology','start-node':'Start node','console-send':'Send console input'};
  function approvalDetail(item) { return JSON.stringify({arguments:item.arguments,context:item.context},null,2); }
  function syncApprovalDialog() {
    const pending = (state?.approvals || []).filter(item=>item.status==='pending' && item.expires_at*1000>Date.now());
    for (const id of dismissedApprovals) if (!pending.some(item=>item.id===id)) dismissedApprovals.delete(id);
    if (approvalId && !pending.some(item=>item.id===approvalId)) {
      approvalId=''; approvalDialog.close();
    }
    if (approvalId || deciding || !connected) return;
    const item = pending.find(item=>!dismissedApprovals.has(item.id));
    if (!item) return;
    approvalId=item.id;
    $('agent-approval-summary').textContent=operationNames[item.operation] || item.operation;
    $('agent-approval-detail').textContent=approvalDetail(item);
    $('agent-approval-expiry').textContent=`Expires at ${new Date(item.expires_at*1000).toLocaleTimeString()}. Topology, console and input-lock checks still apply.`;
    $('agent-approval-yolo').checked=false;
    $('agent-approval-accept').textContent='Approve once';
    $('agent-approval-error').hidden=true;
    approvalDialog.querySelector('details').open=true;
    approvalDialog.showModal();
  }
  function dismissApproval() {
    if (approvalId) dismissedApprovals.add(approvalId);
    approvalId=''; approvalDialog.close();
  }
  async function decideApproval(id, approve, autoApprove=false) {
    if (deciding) return;
    deciding=true;
    for (const control of approvalDialog.querySelectorAll('button,input')) control.disabled=true;
    for (const control of $('agent-approvals').querySelectorAll('button')) control.disabled=true;
    try {
      await api('decision',{id,approve,...(autoApprove?{auto_approve:true}:{})});
      if (autoApprove) $('agent-auto-approve').checked=true;
      dismissedApprovals.add(id);
      if (approvalId===id) {approvalId='';approvalDialog.close();}
    } catch(e) {
      error(e);
      $('agent-approval-error').textContent=e.message || String(e);
      $('agent-approval-error').hidden=false;
    } finally {
      approvalSignature=''; await refresh();
      deciding=false;
      for (const control of approvalDialog.querySelectorAll('button,input')) control.disabled=false;
      syncApprovalDialog();
    }
  }
  approvalDialog.addEventListener('cancel',event=>{event.preventDefault();if(!deciding)dismissApproval();});
  $('agent-approval-later').onclick=dismissApproval;
  $('agent-approval-yolo').onchange=()=>{$('agent-approval-accept').textContent=$('agent-approval-yolo').checked?'Approve and enable YOLO':'Approve once';};
  $('agent-approval-accept').onclick=()=>decideApproval(approvalId,true,$('agent-approval-yolo').checked);
  $('agent-approval-deny').onclick=()=>decideApproval(approvalId,false);
  const view = {panel, maximized: false, rect: null, applyLayout};
  documentWindows.add(view);
  function applyLayout() {
    const bounds = consoleViewport();
    view.rect = boundedConsoleRect(view.rect || {x:bounds.x+60,y:bounds.y+45,width:640,height:680});
    const r = view.maximized ? bounds : view.rect;
    Object.assign(panel.style,{left:r.x+'px',top:r.y+'px',width:r.width+'px',height:r.height+'px'});
    panel.classList.toggle('maximized',view.maximized);
    $('agent-move').hidden = $('agent-resize').hidden = view.maximized;
    $('agent-maximize').textContent = view.maximized ? '↙' : '↗';
    $('agent-maximize').setAttribute('aria-pressed',String(view.maximized));
  }
  async function api(action, data) {
    const response = await fetch('/api/agent/'+action, {
      method: data === undefined ? 'GET' : 'POST',
      headers: {'Content-Type':'application/json','X-Weblab-Agent-Session':token},
      body: data === undefined ? undefined : JSON.stringify(data),
      signal: AbortSignal.timeout(15000)
    });
    const result = await response.json();
    if (!response.ok) {
      if (result.error === 'Agent session expired. Open a new session.') storeToken('');
      throw new Error(result.error || 'Agent request failed');
    }
    return result;
  }
  function error(e) { $('agent-status').textContent = e.message || String(e); }
  // Export only this browser's selected conversation; never expose its capability.
  window.weblabAgentTranscript = async () => {
    if (!token) throw new Error('Open the Agent conversation to include it in the export.');
    const current = await api('state');
    if (!current.conversation_id || !current.messages?.length) throw new Error('The current Agent conversation is empty.');
    return api('history-export', {id:current.conversation_id});
  };
  function storeToken(value) {
    const previous = token;
    token = value;
    try { if (value) sessionStorage.setItem(KEY,value); else sessionStorage.removeItem(KEY); } catch (_) {}
    try {
      if (remember && value) localStorage.setItem(KEY,value);
      else if (localStorage.getItem(KEY) === previous) localStorage.removeItem(KEY);
    } catch (_) {}
    if (!value) {remember=false;$('agent-remember').checked=false;}
  }
  function fillSettings(settings) {
    $('agent-provider').value = settings.provider || 'ollama';
    providerControls();
    $('agent-endpoint').value = settings.endpoint;
    $('agent-model').value = settings.model;
    $('agent-context').value = settings.context_window;
    $('agent-deadline').value = settings.turn_timeout;
    $('agent-lab-changes').checked = !!settings.lab_changes;
    $('agent-console-input').checked = !!settings.console_input;
    $('agent-auto-approve').checked = !!settings.auto_approve;
    settingsDirty = false;
  }
  function settings() {
    return {provider:$('agent-provider').value,endpoint:$('agent-endpoint').value,model:$('agent-model').value.trim(),
      context_window:Number($('agent-context').value),turn_timeout:Number($('agent-deadline').value),
      lab_changes:$('agent-lab-changes').checked,console_input:$('agent-console-input').checked,
      auto_approve:$('agent-auto-approve').checked};
  }
  function providerControls() {
    const openai = $('agent-provider').value === 'openai';
    const endpoint = $('agent-endpoint'), previous = endpoint.value;
    endpoint.replaceChildren();
    for (const url of openai ? [info?.openai_endpoint || 'https://chatgpt.com/backend-api/codex'] : info?.providers || []) {
      const option = document.createElement('option'); option.value = option.textContent = url; endpoint.append(option);
    }
    if ([...endpoint.options].some(o=>o.value===previous)) endpoint.value=previous;
    endpoint.disabled = openai;
    $('agent-openai-auth').hidden = !openai;
    $('agent-check').hidden = openai;
    $('agent-model').placeholder = openai ? 'gpt-5.6-sol' : 'gpt-oss:20b';
    if (!openai) $('agent-model-list').replaceChildren();
  }
  function switchTab(name) {
    selectedTab = name;
    for (const tab of tabs) {
      $('agent-'+tab).hidden = tab !== name;
      const button = $('agent-'+tab+'-tab');
      button.setAttribute('aria-selected',String(tab===name));
      button.tabIndex = tab===name ? 0 : -1;
    }
    $('agent-'+name+'-tab').scrollIntoView({block:'nearest',inline:'nearest'});
    updateTabCounts();
  }
  function updateTabCounts() {
    const operations = state?.approvals || [], proposals = state?.proposals || [];
    const pending = operations.filter(item=>item.status==='pending').length;
    $('agent-operations-tab').textContent = pending ? `Operations · ${pending} pending` : `Operations${operations.length ? ' · '+operations.length : ''}`;
    $('agent-operations-tab').classList.toggle('needs-attention',pending>0);
    $('agent-proposals-tab').textContent = `Proposals${proposals.length ? ' · '+proposals.length : ''}`;
    $('agent-operations-empty').hidden = operations.length > 0;
    const notice = $('agent-pending');
    notice.hidden = !pending || selectedTab==='operations';
    notice.textContent = pending ? `${pending} operation${pending===1?'':'s'} waiting for approval — Review` : '';
  }
  function controls() {
    const active = ['starting','running','stopping'].includes(state?.status);
    const authenticating = ['connecting','waiting'].includes(state?.auth?.status);
    $('agent-send').disabled = busy || readingFiles || active || authenticating || !token;
    $('agent-attach').disabled = busy || readingFiles || active || authenticating || !token;
    for (const control of $('agent-attachments').querySelectorAll('button')) control.disabled=busy || readingFiles;
    $('agent-stop').disabled = !active;
    for (const id of ['agent-new','agent-save','agent-check','agent-end']) $(id).disabled = busy || readingFiles || active || authenticating || !token;
    for (const id of ['agent-login','agent-account','agent-logout']) $(id).disabled = busy || active || authenticating || !token || state?.settings?.provider !== 'openai' || settingsDirty;
    $('agent-login-cancel').hidden = !authenticating;
    $('agent-remember').disabled = !token;
    for (const control of $('agent-history-list').querySelectorAll('[data-history-change]')) {
      control.disabled = busy || readingFiles || active || authenticating || !token || control.dataset.current === 'true';
    }
  }
  function downloadTranscript(record) {
    const content = `# ${record.title || 'Conversation'}\n\nModel: ${record.settings.model}\n\n` +
      record.messages.map(m=>`## ${m.role==='user'?'You':'Agent'}\n\n${m.text}\n` +
        (m.attachments||[]).map(a=>`\nAttachment: ${a.name} (${a.mime}, ${a.size} bytes)\n${a.mime==='text/plain'?a.text:'[Image; download separately from the conversation.]'}\n`).join('')).join('\n');
    const url = URL.createObjectURL(new Blob([content],{type:'text/markdown'}));
    const link = document.createElement('a'); link.href=url; link.download=`conversation-${record.id}.md`; link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function renderHistory(items) {
    const sig = JSON.stringify(items);
    if (sig === historySignature) return;
    historySignature = sig;
    const list = $('agent-history-list'); list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement('p'); empty.className='agent-hint'; empty.textContent='No conversations yet.'; list.append(empty);
    }
    for (const item of items) {
      const card=document.createElement('article'), title=document.createElement('strong'), meta=document.createElement('p'), actions=document.createElement('div');
      card.className='agent-history-card'; card.dataset.id=item.id;
      title.textContent=item.title; meta.className='agent-hint';
      meta.textContent=`${item.active?'Current · ':''}${item.model} · ${new Date(item.updated*1000).toLocaleString()} · ${item.message_count} messages`;
      actions.className='agent-actions';
      const addButton=(label,handler,changes=false)=>{
        const button=document.createElement('button');button.type='button';button.textContent=label;
        if(changes)button.dataset.historyChange='true';button.onclick=handler;actions.append(button);return button;
      };
      const open=addButton('Open',()=>action(async()=>{
        drafts.set(state?.conversation_id,$('agent-prompt').value);
        fileDrafts.set(state?.conversation_id,attachments);
        const next=await api('history-open',{id:item.id});
        settingsDirty=false;render(next);$('agent-prompt').value=drafts.get(item.id)||'';
        attachments=fileDrafts.get(item.id)||[];renderAttachments();
        $('agent-messages').scrollTop=$('agent-messages').scrollHeight;switchTab('chat');
      }),true);
      open.dataset.current=String(item.active);
      const rename=document.createElement('form'), input=document.createElement('input'), save=document.createElement('button'), cancel=document.createElement('button');
      rename.hidden=true;rename.className='agent-history-rename';input.value=item.title;input.maxLength=100;input.required=true;input.setAttribute('aria-label','Conversation title');
      save.textContent='Save title';save.type='submit';save.dataset.historyChange='true';
      cancel.textContent='Cancel';cancel.type='button';cancel.onclick=()=>{rename.hidden=true;};
      rename.append(input,save,cancel);
      rename.onsubmit=event=>{event.preventDefault();action(async()=>{await api('history-rename',{id:item.id,title:input.value});await refresh();});};
      addButton('Rename',()=>{rename.hidden=false;input.focus();input.select();},true);
      addButton('Download transcript',()=>action(async()=>downloadTranscript(await api('history-read',{id:item.id}))));
      const remove=addButton('Delete',()=>{
        if (!confirm(`Delete “${item.title}” from Agent history? This does not change the lab. Underlying Codex logs may remain on disk.`)) return;
        action(async()=>{await api('history-delete',{id:item.id});drafts.delete(item.id);fileDrafts.delete(item.id);await refresh();});
      },true);
      remove.dataset.current=String(item.active);
      if(item.active)remove.title='Open another conversation or start a new one before deleting this conversation';
      card.append(title,meta,actions,rename);list.append(card);
    }
  }
  function attachmentView(item, conversationId) {
    const detail=document.createElement('details'), summary=document.createElement('summary'), body=document.createElement('div');
    detail.className='agent-attachment';summary.textContent=`${item.name} · ${Math.ceil(item.size/1024)} KiB`;
    detail.append(summary,body);
    let loaded=false;
    detail.ontoggle=async()=>{
      if(!detail.open || loaded)return;
      loaded=true;body.textContent='Loading attachment…';
      try {
        const file=conversationId ? await api('attachment',{conversation_id:conversationId,id:item.id}) : item;
        body.replaceChildren();
        if(Object.values(imageTypes).includes(file.mime)) {
          const img=document.createElement('img');img.alt=file.name;img.src=`data:${file.mime};base64,${file.data}`;body.append(img);
        } else {
          const pre=document.createElement('pre');pre.textContent=file.text;body.append(pre);
        }
        if(conversationId) {
          const download=document.createElement('button');download.type='button';download.textContent='Download attachment';
          download.onclick=()=>{
            const raw=Uint8Array.from(atob(file.data),c=>c.charCodeAt(0));
            const url=URL.createObjectURL(new Blob([raw],{type:'application/octet-stream'}));
            const a=document.createElement('a');a.href=url;a.download=file.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
          };body.append(download);
        }
      } catch(e) {body.textContent=e.message;loaded=false;}
    };
    return detail;
  }
  function renderAttachments() {
    const list=$('agent-attachments');list.replaceChildren();list.hidden=!attachments.length;
    attachments.forEach((item,index)=>{
      const row=document.createElement('div'), remove=document.createElement('button');remove.type='button';remove.textContent='Remove';remove.setAttribute('aria-label','Remove '+item.name);
      remove.onclick=()=>{attachments.splice(index,1);fileError('');renderAttachments();};row.append(attachmentView(item),remove);list.append(row);
    });
    controls();
  }
  function fileError(message) {
    $('agent-file-error').textContent=message;$('agent-file-error').hidden=!message;
  }
  async function addFiles(files) {
    readingFiles=true;controls();
    try {
      if(attachments.length+files.length>4)throw Error('Attach up to 4 files per message.');
      const next=[...attachments];
      for(const file of files) {
        const extension=file.name.split('.').pop().toLowerCase(), mime=imageTypes[extension]||'text/plain';
        if(!imageTypes[extension] && !textTypes.has(extension))throw Error('Use PNG/JPEG/WebP screenshots or UTF-8 text/config files. PDF and office documents are not supported yet.');
        if(!file.size || file.size+next.reduce((n,a)=>n+a.size,0)>2*1024*1024)throw Error('Attachments must be nonempty and total at most 2 MiB.');
        if(mime==='text/plain' && file.size+next.filter(a=>a.mime==='text/plain').reduce((n,a)=>n+a.size,0)>64*1024)throw Error('Text attachments may total at most 64 KiB.');
        const bytes=new Uint8Array(await file.arrayBuffer());
        let text;
        if(mime==='text/plain') {
          try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch(_){throw Error('Text attachments must use UTF-8 encoding.');}
          if(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(text))throw Error('Binary or control characters are not allowed in text attachments.');
        }
        const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('Could not read attachment.'));reader.readAsDataURL(file);});
        next.push({name:file.name,mime,size:file.size,data,...(text===undefined?{}:{text})});
      }
      attachments=next;fileError('');renderAttachments();
    } catch(e){fileError(e.message);error(e);}finally{readingFiles=false;controls();}
  }
  function renderApprovals(items) {
    const sig = JSON.stringify(items);
    if (sig === approvalSignature) return;
    approvalSignature = sig;
    const container = $('agent-approvals'); container.replaceChildren();
    for (const item of items) {
      const card = document.createElement('article'), title = document.createElement('strong');
      card.className = 'agent-approval';
      card.dataset.status = item.status;
      title.textContent = `${operationNames[item.operation] || item.operation} · ${item.status}${item.auto_approved ? ' · YOLO' : ''}${item.enabled_auto_approve ? ' · Enabled session YOLO' : ''}`;
      const detail = document.createElement('details'), summary = document.createElement('summary'), content = document.createElement('pre');
      summary.textContent = 'Exact operation and context';
      // JSON escaping makes carriage returns/control bytes visible. Never render device text as HTML.
      content.textContent = approvalDetail(item);
      detail.append(summary,content); detail.open = item.status === 'pending';
      card.append(title,detail);
      if (item.status === 'pending') {
        const note = document.createElement('p');
        note.textContent = `Approve once by ${new Date(item.expires_at*1000).toLocaleTimeString()}. Changed topology, console output or a human input lock can still prevent execution.`;
        card.append(note);
        for (const approve of [true,false]) {
          const decision = document.createElement('button'); decision.textContent = approve ? 'Approve once' : 'Deny';
          decision.onclick = () => decideApproval(item.id,approve);
          card.append(decision);
        }
      }
      if (item.error || item.input_status) {
        const result = document.createElement('p');
        result.textContent = item.error || `Input status: ${item.input_status}. Read console output to verify execution.`;
        card.append(result);
      }
      container.append(card);
    }
  }
  function progress() {
    const active = ['starting','running','stopping'].includes(state?.status);
    const line = $('agent-progress'); line.hidden = !active;
    line.classList.toggle('disconnected', !connected);
    if (!active) return;
    const elapsed = Math.max(0, (state.elapsed_seconds || 0) + Math.floor((Date.now()-progressReceived)/1000));
    const quiet = Math.max(0, (state.quiet_seconds || 0) + Math.floor((Date.now()-progressReceived)/1000));
    const pending = (state.approvals || []).some(a=>a.status==='pending');
    const labels = {get_lab_state:'Reading lab state',get_console_output:'Reading console',get_node_logs:'Reading launcher logs',
      get_storage_report:'Checking storage',list_device_profiles:'Reading device profiles',get_authoring_guide:'Reading authoring guide',
      preview_topology:'Validating topology',get_proposal:'Reading proposal',apply_topology:'Applying approved topology',
      start_node:'Starting approved node',send_console_input:'Sending approved input'};
    const raw = state.phase || 'Waiting for model';
    let phase = raw.startsWith('Using ') ? (labels[raw.slice(6)] || 'Using Weblab tool') : raw;
    if (pending) phase = 'Waiting for your approval';
    if (state.status==='stopping') phase = 'Stopping';
    if (!connected) phase = 'Connection lost — reconnect to check progress';
    const duration = seconds => seconds < 60 ? `${seconds}s` : `${Math.floor(seconds/60)}m ${seconds%60}s`;
    const timing = `${duration(elapsed)} / ${duration(state.settings.turn_timeout)}`;
    $('agent-progress-text').textContent = `${phase} · ${timing}${quiet>=15 && !pending && connected ? ` · last event ${quiet}s ago` : ''}`;
  }
  function conversationHints(messages = []) {
    $('agent-intro').hidden = messages.length > 0;
    $('agent-prompt').placeholder = messages.some(m => m.role === 'assistant' && m.text?.trim())
      ? 'Message your agent…' : starterPrompt;
  }
  function render(next) {
    state = next; connected = true; progressReceived = Date.now();
    conversationHints(next.messages);
    const messages = $('agent-messages');
    const sig = JSON.stringify(next.messages);
    if (signature !== sig) {
      const bottom = messages.scrollHeight-messages.scrollTop-messages.clientHeight < 60;
      messages.replaceChildren();
      for (const message of next.messages || []) {
        const article = document.createElement('article'), label = document.createElement('strong'), text = document.createElement('div');
        label.textContent = message.role === 'user' ? 'You' : 'Agent';
        text.textContent = message.text; // Deliberately plain text: no model HTML, images or arbitrary links.
        article.append(label,text); messages.append(article);
        for (const item of message.attachments || []) article.append(attachmentView(item,next.conversation_id));
      }
      if (bottom) messages.scrollTop = messages.scrollHeight;
      signature = sig;
    }
    const proposals = $('agent-proposals');
    const nextProposals = JSON.stringify(next.proposals || []);
    if (proposalSignature !== nextProposals) {
    proposalSignature = nextProposals;
    proposals.replaceChildren();
    if (!(next.proposals || []).length) {
      const empty = document.createElement('p'); empty.className='agent-hint';
      empty.textContent='No proposals yet. Topology previews and downloads appear here.'; proposals.append(empty);
    }
    for (const [index,id] of (next.proposals || []).entries()) {
      const row = document.createElement('div'), label = document.createElement('span');
      label.textContent = `Proposal ${index+1} `; row.append(label);
      const review = document.createElement('button'), detail = document.createElement('div');
      detail.className='agent-proposal-detail'; detail.hidden=true;
      review.textContent='Review';
      review.onclick=async()=>{
        try {
          if (!detail.hidden) {detail.hidden=true;return;}
          const p=await api('proposal',{id});
          const summary=document.createElement('p'), warnings=document.createElement('p'), json=document.createElement('pre');
          summary.textContent=`${p.topology.name}: ${p.topology.nodes.length} nodes, ${p.topology.links.length} links. Structural validation only.`;
          warnings.textContent=(p.warnings||[]).join(' ');
          json.textContent=JSON.stringify(p.topology,null,2);
          detail.replaceChildren(summary,warnings,json);detail.hidden=false;
        } catch(e) {error(e);}
      };
      row.append(review);
      for (const kind of ['topology','instructions']) {
        const download = document.createElement('button'); download.textContent = kind === 'topology' ? 'Download JSON' : 'Download exercise';
        download.onclick = async () => {
          try {
            const proposal = await api('proposal',{id});
            const content = kind === 'topology' ? JSON.stringify(proposal.topology,null,2)+'\n' : proposal.instructions_markdown;
            const url = URL.createObjectURL(new Blob([content],{type:kind==='topology'?'application/json':'text/markdown'}));
            const link = document.createElement('a'); link.href=url; link.download='exercise.'+(kind==='topology'?'json':'md'); link.click();
            setTimeout(()=>URL.revokeObjectURL(url),1000);
          } catch(e) { error(e); }
        };
        row.append(download);
      }
      row.append(detail);proposals.append(row);
    }
    }
    renderApprovals(next.approvals || []); renderHistory(next.history || []); updateTabCounts(); progress(); syncApprovalDialog();
    const toolError = $('agent-tool-error'); toolError.hidden = !next.tool_error;
    toolError.querySelector('pre').textContent = next.tool_error ? `${next.tool_error.operation}: ${next.tool_error.message}` : '';
    $('agent-mode').textContent = (next.settings.lab_changes || next.settings.console_input) ?
      (next.settings.auto_approve ? 'YOLO' : 'Approval required') : 'Read & preview';
    $('agent-status').textContent = next.error || `${next.settings.model} · ${next.status}${next.activity ? ' · '+next.activity : ''}`;
    if (!settingsDirty) fillSettings(next.settings);
    const auth = next.auth || {};
    $('agent-auth-status').textContent = auth.error || (auth.signed_in ? `Signed in${auth.plan ? ' · '+auth.plan : ''}. Model list is a catalog; access is verified when you send a message.` : `Sign-in: ${auth.status || 'not checked'}`);
    $('agent-device-code').hidden = auth.status !== 'waiting';
    $('agent-device-code').querySelector('strong').textContent = auth.code || '';
    if ($('agent-provider').value === 'openai') {
      $('agent-model-list').replaceChildren(...(auth.models || []).map(model=>{
        const option=document.createElement('option'); option.value=model; return option;
      }));
    }
    controls();
  }
  async function refresh() {
    if (!token || polling) return;
    polling = true;
    try { render(await api('state')); } catch(e) {
      connected=false; progress();
      if (e.message.startsWith('Agent session expired')) {storeToken('');state=null;controls();syncApprovalDialog();}
      error(e);
    }
    finally { polling = false; }
  }
  async function ensureSession() {
    if (!token) {
      const created = await api('session',{}); storeToken(created.token); fillSettings(created.settings);
      switchTab('settings');
    }
    await refresh();
  }
  async function action(fn) {
    busy = true; controls();
    try { await fn(); } catch(e) { error(e); }
    finally { busy = false; controls(); }
  }
  button.onclick = () => {
    dismissCompactPanel(); panel.hidden=false; applyLayout(); bringConsoleForward(panel);
    button.setAttribute('aria-expanded','true'); action(ensureSession);
  };
  $('agent-hide').onclick = () => { panel.hidden=true; button.setAttribute('aria-expanded','false'); button.focus(); };
  $('agent-maximize').onclick = () => {view.maximized=!view.maximized;applyLayout();};
  for (const tab of tabs) {
    const button = $('agent-'+tab+'-tab');
    button.onclick = () => switchTab(tab);
    button.onkeydown = event => {
      const index = tabs.indexOf(tab);
      const target = {ArrowRight:(index+1)%tabs.length,ArrowLeft:(index+tabs.length-1)%tabs.length,Home:0,End:tabs.length-1}[event.key];
      if (target === undefined) return;
      event.preventDefault(); switchTab(tabs[target]); $('agent-'+tabs[target]+'-tab').focus();
    };
  }
  $('agent-pending').onclick = () => {
    switchTab('operations'); $('agent-operations-tab').focus();
    $('agent-approvals').querySelector('[data-status="pending"]')?.scrollIntoView({block:'nearest'});
  };
  $('agent-remember').checked = remember;
  $('agent-remember').onchange = () => {
    remember=$('agent-remember').checked;
    try {
      if(remember)localStorage.setItem(KEY,token);
      else if(localStorage.getItem(KEY)===token)localStorage.removeItem(KEY);
    } catch (_) {remember=false;$('agent-remember').checked=false;error(new Error('Browser storage is unavailable. This session remains in this tab only.'));}
  };
  $('agent-settings').oninput = event => {if(event.target.id==='agent-remember')return;settingsDirty=true;controls();};
  $('agent-provider').onchange = () => {
    providerControls(); settingsDirty=true;
    $('agent-model').value = $('agent-provider').value==='openai' ? 'gpt-5.6-sol' : 'gpt-oss:20b';
    controls();
  };
  $('agent-settings').onsubmit = event => {event.preventDefault();action(async()=>{
    const result = await api('settings',settings()); fillSettings(result.settings); await refresh(); switchTab(result.settings.provider==='openai' ? 'settings' : 'chat');
  });};
  for (const [id, operation] of [['agent-login','login'],['agent-account','login-status'],['agent-logout','logout'],['agent-login-cancel','login-cancel']]) {
    $(id).onclick = () => action(async()=>{await api(operation,{}); await refresh();});
  }
  $('agent-check').onclick = () => action(async()=>{
    const result=await api('check',settings());
    $('agent-check-result').textContent=result.available?'Model is available. Responses/tool compatibility is checked when you send a prompt.':'Model was not found on this endpoint. Check the exact name.';
  });
  $('agent-prompt-form').onsubmit = event => {event.preventDefault();action(async()=>{
    if(readingFiles)return;
    const prompt=$('agent-prompt').value.trim(); if (!prompt) return;
    const bytes=crypto.getRandomValues(new Uint8Array(16));
    const request_id=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
    fileError('');
    try {
      await api('turn',{prompt,request_id,...(attachments.length?{attachments:attachments.map(({name,data})=>({name,data}))}:{})});
    } catch(e) {fileError(e.message);throw e;}
    $('agent-prompt').value=''; attachments=[];fileDrafts.delete(state?.conversation_id);renderAttachments();await refresh();
  });};
  $('agent-attach').onclick=()=>$('agent-files').click();
  $('agent-files').onchange=async()=>{await addFiles([...$('agent-files').files]);$('agent-files').value='';};
  $('agent-stop').onclick = () => action(async()=>{await api('stop',{});await refresh();});
  $('agent-new').onclick = () => action(async()=>{
    drafts.set(state?.conversation_id,$('agent-prompt').value);
    fileDrafts.set(state?.conversation_id,attachments);
    await api('reset',{});await refresh();$('agent-prompt').value='';attachments=[];renderAttachments();switchTab('chat');
  });
  $('agent-end').onclick = () => action(async()=>{await api('close',{});storeToken('');state=null;conversationHints();signature='';proposalSignature='';approvalSignature='';historySignature='';drafts.clear();fileDrafts.clear();attachments=[];renderAttachments();$('agent-history-list').replaceChildren();$('agent-approvals').replaceChildren();$('agent-messages').replaceChildren();$('agent-proposals').replaceChildren();$('agent-tool-error').hidden=true;updateTabCounts();$('agent-status').textContent='Session closed. Reconnect to start a new one.';});
  $('agent-reconnect').onclick = () => action(ensureSession);
  for (const [id,kind] of [['agent-toolbar','move'],['agent-resize','resize']]) bindConsolePointer($(id),kind,view);
  for (const [id,kind] of [['agent-move','move'],['agent-resize','resize']]) bindConsoleKeys($(id),kind,view);
  for (const name of ['pointerdown','focusin']) panel.addEventListener(name,()=>bringConsoleForward(panel));
  setInterval(()=>{syncApprovalDialog();if (!panel.hidden || ['starting','running','stopping'].includes(state?.status)) {progress();refresh();}},1000);
  api('info').then(result=>{
    info=result; button.hidden=!info.enabled;
    if(info.enabled) providerControls();
  }).catch(()=>{});
})();
