/* Weblab: no build step, all assets served locally. */
"use strict";
const $ = id => document.getElementById(id);
const icons = {
  switch: '<svg viewBox="0 0 40 36" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="8" width="34" height="21" rx="4"/><path d="M8 15h11m-3-3 3 3-3 3m16 4H21m3-3-3 3 3 3"/><path d="M8 24h2m3 0h2m13-10h2m3 0h1"/></svg>',
  router: '<svg viewBox="0 0 40 36" fill="none" stroke="currentColor" stroke-width="1.7"><ellipse cx="20" cy="12" rx="16" ry="7"/><path d="M4 12v12c0 4 7 7 16 7s16-3 16-7V12M10 12h9m-3-3 3 3-3 3m14-3h-8m3-3-3 3 3 3"/></svg>',
  pc: '<svg viewBox="0 0 40 36" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="4" width="32" height="23" rx="3"/><path d="M4 22h32M16 27v5m8-5v5m-13 0h18M12 11l4 3-4 3m8 0h7"/></svg>'
};
document.querySelectorAll("[data-icon]").forEach(el => el.innerHTML = icons[el.dataset.icon]);
let topology = {version: 1, name: "Untitled lab", nodes: [], links: []};
let statuses = {}, images = [], selected = null, busy = false, loaded = false, dragging = false;
let mode = "select", linkSource = null, pendingLink = null, toastTimer;
let pendingDelete = null;
const MAP_WIDTH = 2400, MAP_HEIGHT = 1600, MIN_ZOOM = .2, MAX_ZOOM = 2;
let mapZoom = 1, suppressMapClick = false;
const consoleSessions = new Map();
let linkStates = {}, linkError = '', activeLinkAction = null;
let consoleNode = null;
// Null retains automatic map behavior until the user chooses a console layout.
let consoleOpenMode = null;
const uid = () => "n" + (crypto.randomUUID ? crypto.randomUUID().replaceAll("-", "").slice(0, 16) : Math.random().toString(36).slice(2));
const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
const nodeById = id => topology.nodes.find(n => n.id === id);
const state = id => statuses[id]?.state || "stopped";
const hasRunning = () => Object.values(statuses).some(s => s.state === "running");
const isQemu = n => n.type !== "pc" && n.image?.endsWith(".qcow2");
const isExos = n => isQemu(n) && /^exos-vm[_-]/i.test(n.image);
const isVeos = n => isQemu(n) && /^veos(?:64)?-lab-/i.test(n.image);
const noConfigExport = n => isExos(n) || isVeos(n);
const nodePorts = n => n.type === "pc" ? ["eth0"] : isVeos(n) ? ["Management1", ...Array.from({length:n.ethernet - 1}, (_, i) => `Ethernet${i + 1}`)] : isExos(n) ? ["Mgmt", ...Array.from({length:n.ethernet - 1}, (_, i) => String(i + 1))] : isQemu(n)
  ? Array.from({length: n.ethernet}, (_, i) => n.type === "switch" ? `Gi${Math.floor(i / 4)}/${i % 4}` : `Gi0/${i}`)
  : Array.from({length: n.ethernet * 4}, (_, i) => `${Math.floor(i / 4)}/${i % 4}`);
const portLink = (id, port) => topology.links.find(l => [l.a, l.b].some(e => e.node === id && e.port === port));
const freePorts = n => nodePorts(n).filter(p => !portLink(n.id, p));

const panelMedia = matchMedia("(max-width: 1000px), (orientation: portrait)");
const desktopPanels = {palette:true, inspector:true};
let compactPanel = null;
try {
  const saved = JSON.parse(localStorage.getItem("iol-panels") || "null");
  for (const key of Object.keys(desktopPanels)) if (typeof saved?.[key] === "boolean") desktopPanels[key] = saved[key];
} catch (_) { /* Storage can be unavailable in private or restricted browsers. */ }

function applyPanelLayout() {
  const area = $("canvas-scroll");
  const center = {x:area.scrollLeft+area.clientWidth/2, y:area.scrollTop+area.clientHeight/2};
  const compact = panelMedia.matches;
  $("workspace").classList.toggle("panels-compact", compact);
  for (const [key,id,label] of [["palette","device-library","device library"],["inspector","inspector","inspector"]]) {
    const open = compact ? compactPanel === key : desktopPanels[key];
    const panel = $(id), toggle = $("toggle-"+key);
    if (!open && panel.contains(document.activeElement)) toggle.focus({preventScroll:true});
    panel.hidden = !open;
    $("workspace").classList.toggle(key+"-collapsed", !open);
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", `${open ? "Hide" : "Show"} ${label}`);
    toggle.title = `${open ? "Hide" : "Show"} ${label}`;
  }
  $("panel-backdrop").hidden = !compact || !compactPanel;
  // Keep keyboard focus out of canvas/console controls covered by the drawer.
  area.inert = $("console-panel").inert = $("console-windows").inert = compact && !!compactPanel;
  area.scrollLeft = center.x-area.clientWidth/2;
  area.scrollTop = center.y-area.clientHeight/2;
  requestAnimationFrame(() => { if (loaded) renderLinks(); fitConsole(); });
}

function setPanel(key, open, keyboard = false) {
  if (panelMedia.matches) compactPanel = open ? key : (compactPanel === key ? null : compactPanel);
  else {
    desktopPanels[key] = open;
    try { localStorage.setItem("iol-panels", JSON.stringify(desktopPanels)); } catch (_) { /* Keep this session's choice. */ }
  }
  applyPanelLayout();
  if (keyboard && open) $("close-"+key).focus({preventScroll:true});
}

function dismissCompactPanel() {
  if (panelMedia.matches && compactPanel) setPanel(compactPanel, false);
}

for (const key of Object.keys(desktopPanels)) {
  $("toggle-"+key).onclick = event => setPanel(key, $("toggle-"+key).getAttribute("aria-expanded") !== "true", event.detail === 0);
  $("close-"+key).onclick = () => setPanel(key, false);
}
// A touch release that opens a drawer can synthesize its click on the newly
// exposed backdrop. Dismiss only gestures that actually started on the backdrop.
let backdropPress = false;
document.addEventListener("pointerdown", event => { backdropPress = event.target === $("panel-backdrop"); }, true);
$("panel-backdrop").onclick = () => { if (backdropPress) dismissCompactPanel(); backdropPress = false; };
panelMedia.addEventListener("change", () => { compactPanel = null; applyPanelLayout(); });
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && panelMedia.matches && compactPanel && !document.querySelector("dialog[open]")) {
    event.preventDefault(); event.stopImmediatePropagation(); dismissCompactPanel();
  }
}, true);

function updateFullscreen() {
  const active = !!document.fullscreenElement;
  const supported = !!document.fullscreenEnabled && typeof document.documentElement.requestFullscreen === "function";
  const label = active ? "Exit fullscreen" : "Enter fullscreen";
  $("fullscreen").disabled = !supported && !active;
  $("fullscreen").setAttribute("aria-pressed", String(active));
  $("fullscreen").setAttribute("aria-label", label);
  $("fullscreen").title = supported || active ? label : "Fullscreen is unavailable in this browser";
}
$("fullscreen").onclick = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen({navigationUI:"hide"});
  } catch (error) { toast("Could not change fullscreen: " + error.message, true); }
  updateFullscreen();
};
document.addEventListener("fullscreenchange", () => {
  updateFullscreen();
  requestAnimationFrame(() => { fitConsole(); if (loaded) renderLinks(); });
});
updateFullscreen();

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").className = error ? "error" : "";
  $("toast").hidden = false;
  toastTimer = setTimeout(() => $("toast").hidden = true, error ? 10000 : 4500);
}

async function api(path, method = "GET", body) {
  const response = await fetch(path, {method, headers: method === "GET" ? {} : {"Content-Type": "application/json"}, body: body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}

function accept(result, updateForm = true) {
  topology = result.topology; statuses = result.status; images = result.images; loaded = true;
  linkStates = result.link_state || {}; linkError = result.link_error || "";
  if (selected && !nodeById(selected)) selected = null;
  syncConsoles();
  if (document.activeElement !== $("lab-name")) $("lab-name").value = topology.name;
  render();
  if (updateForm) renderInspector(); else updateInspectorStatus();
  updateLinkActions();
}

async function perform(label, operation) {
  if (busy || !loaded) return;
  busy = true;
  document.body.classList.add("busy");
  $("save-state").textContent = label;
  updateControls();
  try {
    accept(await operation());
    $("save-state").textContent = "All changes saved";
  } catch (error) {
    toast(error.message, true);
    try { accept(await api("/api/state")); } catch (_) { /* Keep the last visible topology. */ }
    $("save-state").textContent = "Action failed";
  } finally {
    busy = false;
    document.body.classList.remove("busy");
    updateControls();
    updateLinkActions();
  }
}

function edit(change) {
  return perform("Saving…", async () => { change(); return api("/api/topology", "PUT", topology); });
}
function structuralEdit(change) {
  if (hasRunning()) return toast("Stop the lab before changing devices or cables.", true);
  return edit(change);
}

function newNode(type, x, y) {
  const prefix = {switch: "SW", router: "R", pc: "PC"}[type];
  let number = 1;
  while (topology.nodes.some(n => n.name === prefix + number)) number++;
  const image = type === "pc" ? "alpine:latest" : (images.find(i => i.type === type)?.name || "");
  return {id: uid(), type, name: prefix + number, image,
          memory: isVeos({type,image}) ? 6144 : 1024, ethernet: isVeos({type,image}) ? 5 : isExos({type,image}) ? 13 : image.endsWith(".qcow2") ? 4 : 2, x: Math.round(Math.max(70, Math.min(2330, x))), y: Math.round(Math.max(60, Math.min(1540, y))), ipv4: "", gateway: ""};
}

function addNode(type, x, y) {
  if (type !== "pc" && !images.some(i => i.type === type)) return toast(`No ${type} image was found in the image directory.`, true);
  structuralEdit(() => { const n = newNode(type, x, y); topology.nodes.push(n); selected = n.id; })?.then(() => {
    if (panelMedia.matches && selected) setPanel("inspector", true);
  });
}

function render() {
  $("empty-state").hidden = topology.nodes.length > 0;
  $("nodes").innerHTML = topology.nodes.map(n => `<button class="map-node ${selected === n.id ? "selected" : ""} ${linkSource === n.id ? "linking" : ""}" data-id="${esc(n.id)}" style="left:${n.x}px;top:${n.y}px" aria-label="${esc(n.name)}, ${n.type}, ${state(n.id)}"><span class="little-dot ${state(n.id)}"></span><span class="device-icon ${n.type}">${icons[n.type]}</span><span class="node-label">${esc(n.name)}</span><span class="node-type">${n.type === "pc" ? "ALPINE PC" : `${isVeos(n) ? "vEOS" : isExos(n) ? "EXOS" : isQemu(n) ? "IOSv" : "IOL"} ${n.type.toUpperCase()}`}</span></button>`).join("");
  $("node-list").innerHTML = topology.nodes.map(n => `<button data-id="${esc(n.id)}" class="${selected === n.id ? "selected" : ""}"><span class="mini-type">${{switch:"L2",router:"L3",pc:"PC"}[n.type]}</span>${esc(n.name)}<span class="little-dot ${state(n.id)}"></span></button>`).join("");
  renderLinks();
  $("node-count").textContent = String(topology.nodes.length).padStart(2, "0");
  $("link-count").textContent = `${topology.nodes.length} devices · ${topology.links.length} links${topology.links.some(l => linkFault(l.id)) ? ` · ${topology.links.filter(l => linkFault(l.id)).length} interrupted` : ""}`;
  $("running-count").textContent = `${Object.values(statuses).filter(s => s.state === "running").length} running`;
  updateControls();
}

function linkBundles() {
  const bundles = new Map();
  for (const link of topology.links) {
    // Use one direction for each node pair, including cables created in reverse.
    const [a, b] = link.a.node < link.b.node ? [link.a, link.b] : [link.b, link.a];
    const key = JSON.stringify([a.node, b.node]);
    if (!bundles.has(key)) bundles.set(key, []);
    bundles.get(key).push({id: link.id, a, b});
  }
  return [...bundles.values()].map(links => {
    // Keep lanes stable when the API returns the same links in a different order.
    links.sort((a, b) => a.id.localeCompare(b.id));
    return links.map((link, index) => {
      const a = nodeById(link.a.node), b = nodeById(link.b.node);
      const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
      const bend = (index - (links.length - 1) / 2) * 84;
      const control = {x: (a.x + b.x) / 2 - dy / length * bend,
                       y: (a.y + b.y) / 2 + dx / length * bend};
      const point = t => ({x: (1-t)**2*a.x + 2*(1-t)*t*control.x + t*t*b.x,
                          y: (1-t)**2*a.y + 2*(1-t)*t*control.y + t*t*b.y});
      return {link, a, b, control, point, bend, length,
              path: bend === 0 ? `M${a.x},${a.y} L${b.x},${b.y}` :
                    `M${a.x},${a.y} Q${control.x},${control.y} ${b.x},${b.y}`};
    });
  });
}

function linkFault(linkId) {
  const value = linkStates[linkId];
  return !!value && (value.blocked_a_to_b || value.blocked_b_to_a ||
    ['a','b'].some(side => value['carrier_'+side] && value['carrier_'+side] !== 'up'));
}
function linkDescription(link) {
  const label = side => `${nodeById(link[side].node)?.name || link[side].node} ${link[side].port}`;
  const value = linkStates[link.id] || {}, parts = [];
  if (value.blocked_a_to_b) parts.push(`Blocked ${label('a')} → ${label('b')}`);
  if (value.blocked_b_to_a) parts.push(`Blocked ${label('b')} → ${label('a')}`);
  for (const side of ['a','b']) {
    const endpoint = link[side].node, status = state(endpoint);
    if (status !== 'running') parts.push(`${label(side)}: ${status}${statuses[endpoint]?.error ? ': '+statuses[endpoint].error : ''}. Carrier controls require this node to be running.`);
    if (value['carrier_'+side] === 'down') parts.push(`${label(side)}: ${value['carrier_iol_'+side] ? 'unplug requested (IOL)' : 'cable unplugged'}`);
    if (value['carrier_pending_'+side]) parts.push(`${label(side)}: allow about ${value['carrier_pending_'+side]}s for carrier detection`);
    if (value['carrier_error_'+side]) parts.push(`${label(side)}: ${value['carrier_error_'+side]}`);
    if (value['carrier_'+side] === 'unknown') parts.push(`${label(side)}: link status uncertain; traffic blocked`);
  }
  return parts.join('; ') || (linkError || 'Traffic allowed in both directions');
}
function renderLinks() {
  const bundles = linkBundles();
  $("links").innerHTML = bundles.map(routes => `<g class="cable-bundle">${routes.map(({link, a, b, path, point}) => {
    const text = port => `<text class="port-label" text-anchor="middle" x="0" y="0">${esc(port)}</text>`;
    const original = topology.links.find(item => item.id === link.id), fault = linkFault(link.id), middle = point(.5);
    const description = `${a.name} ${link.a.port} ↔ ${b.name} ${link.b.port} · ${linkDescription(original)} · ${hasRunning() ? 'Click for link actions' : 'Click to remove; right-click for actions'}`;
    return `<g class="cable-group" data-link="${esc(link.id)}" tabindex="0" role="button" aria-label="${esc(description)}"><title>${esc(description)}</title><path class="cable ${state(a.id) === "running" && state(b.id) === "running" ? "running" : ""} ${fault ? 'interrupted' : ''}" d="${path}"/><path class="cable-hit" d="${path}"/>${text(link.a.port)}${text(link.b.port)}${fault ? `<text class="link-fault-marker" text-anchor="middle" x="${middle.x}" y="${middle.y}">×</text>` : ''}</g>`;
  }).join("")}</g>`).join("");
  placeLinkLabels(bundles.flat());
}

function placeLinkLabels(routes) {
  // Measure in canvas coordinates so node cards, overflowing hostnames and text
  // outlines all keep a screen-space gap, including at the smallest zoom.
  const origin = $("canvas").getBoundingClientRect(), gap = 7 / mapZoom;
  const scale = Math.max(1, 1 / mapZoom), outline = 3 * scale;
  const obstacles = [...$("nodes").querySelectorAll(".map-node, .node-label")].map(el => {
    const r = el.getBoundingClientRect();
    return {left:(r.left-origin.left)/mapZoom, right:(r.right-origin.left)/mapZoom,
            top:(r.top-origin.top)/mapZoom, bottom:(r.bottom-origin.top)/mapZoom};
  });
  const groups = new Map([...$("links").querySelectorAll(".cable-group")].map(el => [el.dataset.link, el]));
  const labels = routes.flatMap(route => [...groups.get(route.link.id).querySelectorAll(".port-label")].map((el, side) =>
    ({route, el, side, box:el.getBBox(), node:side ? route.b : route.a})));
  // Deterministic placement even when imported links change order/direction.
  labels.sort((a,b) => a.node.id.localeCompare(b.node.id) || a.route.link.id.localeCompare(b.route.link.id));
  const overlaps = (a,b) => a.left < b.right+gap && a.right > b.left-gap && a.top < b.bottom+gap && a.bottom > b.top-gap;
  for (const label of labels) {
    const {route, box, node, side, el} = label;
    const halfW = box.width/2 + outline, halfH = box.height/2 + outline;
    let best = null;
    // Prefer the outside of a curved cable; straight cables prefer above/right.
    const normalSign = route.bend ? Math.sign(route.bend) : (route.b.x >= route.a.x ? -1 : 1);
    for (let step = 1; step <= 50; step++) {
      const t = side ? 1-step/100 : step/100, point = route.point(t);
      const dx = 2*((1-t)*(route.control.x-route.a.x)+t*(route.b.x-route.control.x));
      const dy = 2*((1-t)*(route.control.y-route.a.y)+t*(route.b.y-route.control.y));
      const length = Math.hypot(dx,dy) || 1;
      for (const offset of [12,-12,26,-26,42,-42,60,-60]) {
        const x = point.x - dy/length*offset*scale*normalSign;
        const y = point.y + dx/length*offset*scale*normalSign;
        const rect = {left:x-halfW, right:x+halfW, top:y-halfH, bottom:y+halfH};
        const cost = Math.hypot(x-node.x,y-node.y) + Math.abs(offset)*scale*2 + (offset<0 ? 4*scale : 0);
        if ((!best || cost < best.cost) && rect.left >= 0 && rect.top >= 0 && !obstacles.some(r => overlaps(rect,r)))
          best = {x,y,rect,cost,point,offset};
      }
    }
    // Very short/dense connections may have no room along their visible half.
    // Fan out a label with a leader rather than covering a node or another label.
    if (!best) {
      const point = route.point(side ? .75 : .25);
      search: for (let radius = 24; ; radius += 16) {
        for (let angle = 0; angle < 16; angle++) {
          const x = point.x + Math.cos(angle*Math.PI/8)*radius*scale;
          const y = point.y + Math.sin(angle*Math.PI/8)*radius*scale;
          const rect = {left:x-halfW, right:x+halfW, top:y-halfH, bottom:y+halfH};
          if (rect.left >= 0 && rect.top >= 0 && !obstacles.some(r => overlaps(rect,r))) {
            best = {x,y,rect,point,offset:radius}; break search;
          }
        }
      }
    }
    obstacles.push(best.rect);
    el.setAttribute("x", best.x-box.x-box.width/2);
    el.setAttribute("y", best.y-box.y-box.height/2);
    if (Math.abs(best.offset) > 18) {
      const leader = document.createElementNS("http://www.w3.org/2000/svg", "line");
      leader.setAttribute("class", "port-leader");
      for (const [name,value] of Object.entries({x1:best.point.x,y1:best.point.y,x2:best.x,y2:best.y})) leader.setAttribute(name,value);
      el.before(leader);
    }
  }
}

function updateControls() {
  $("upload-image").disabled = busy || !loaded;
  for (const id of ["start-all", "stop-all", "import", "export", "starter", "link-tool", "select-tool", "apply-node", "start-node", "stop-node", "open-console", "delete-node", "logs"]) $(id).disabled = busy || !loaded;
  $("start-all").disabled ||= !topology.nodes.length || topology.nodes.every(n => state(n.id) === "running");
  $("stop-all").disabled ||= !hasRunning();
  $("import").disabled ||= hasRunning();
  $("link-tool").disabled ||= hasRunning();
  $("lab-name").disabled = busy || !loaded;
  document.querySelectorAll(".device-template").forEach(el => { el.disabled = busy || !loaded || hasRunning(); el.draggable = !el.disabled; });
  $("delete-node").disabled ||= hasRunning();
  if (selected) {
    $("start-node").disabled ||= state(selected) === "running";
    $("stop-node").disabled ||= state(selected) !== "running";
    $("open-console").disabled ||= state(selected) !== "running" && !consoleSessions.has(selected);
  }
  for (const id of ["node-image", "node-memory", "node-ethernet"]) $(id).disabled = busy || hasRunning();
  const pc = nodeById(selected);
  for (const id of ["node-ipv4", "node-gateway"]) $(id).disabled = busy || !loaded || !pc || pc.type !== "pc" || state(pc.id) === "running";
  updateViewControls();
}

function updateViewControls() {
  $("zoom-out").disabled = dragging || mapZoom <= MIN_ZOOM;
  $("zoom-in").disabled = dragging || mapZoom >= MAX_ZOOM;
  $("zoom-reset").disabled = dragging;
  $("zoom-fit").disabled = dragging || !topology.nodes.length;
  $("zoom-reset").textContent = `${Math.round(mapZoom * 100)}%`;
}

function mapPoint(clientX, clientY) {
  const rect = $("canvas").getBoundingClientRect();
  return {x: (clientX - rect.left) / mapZoom, y: (clientY - rect.top) / mapZoom};
}

function setMapZoom(value, anchor) {
  if (dragging) return;
  const area = $("canvas-scroll");
  const point = anchor || {x: area.clientWidth / 2, y: area.clientHeight / 2};
  const world = {x: (area.scrollLeft + point.x) / mapZoom, y: (area.scrollTop + point.y) / mapZoom};
  mapZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, value));
  $("canvas-extent").style.width = `${MAP_WIDTH * mapZoom}px`;
  $("canvas-extent").style.height = `${MAP_HEIGHT * mapZoom}px`;
  $("canvas").style.transform = `scale(${mapZoom})`;
  $("canvas").style.setProperty("--node-label-scale", Math.max(1, 1 / mapZoom));
  $("canvas").classList.toggle("zoom-compact", mapZoom < .75);
  renderLinks();
  area.scrollLeft = world.x * mapZoom - point.x;
  area.scrollTop = world.y * mapZoom - point.y;
  updateViewControls();
}

function fitMap() {
  if (!topology.nodes.length || dragging) return;
  const area = $("canvas-scroll");
  // Bézier curves lie inside their control-point bounds. Include the outer lanes.
  const bounds = [...topology.nodes, ...linkBundles().flat().map(route => route.control)];
  const left = Math.min(...bounds.map(n => n.x)) - 75;
  const right = Math.max(...bounds.map(n => n.x)) + 75;
  const top = Math.min(...bounds.map(n => n.y)) - 65;
  const bottom = Math.max(...bounds.map(n => n.y)) + 65;
  setMapZoom(Math.min((area.clientWidth - 48) / (right - left), (area.clientHeight - 48) / (bottom - top)));
  area.scrollLeft = (left + right) / 2 * mapZoom - area.clientWidth / 2;
  area.scrollTop = (top + bottom) / 2 * mapZoom - area.clientHeight / 2;
}

$("zoom-out").onclick = () => setMapZoom(mapZoom / 1.25);
$("zoom-in").onclick = () => setMapZoom(mapZoom * 1.25);
$("zoom-reset").onclick = () => setMapZoom(1);
$("zoom-fit").onclick = fitMap;
$("canvas-scroll").addEventListener("wheel", event => {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  const area = $("canvas-scroll"), rect = area.getBoundingClientRect();
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? area.clientHeight : 1);
  setMapZoom(mapZoom * Math.exp(-Math.max(-200, Math.min(200, delta)) * .003),
             {x: event.clientX - rect.left, y: event.clientY - rect.top});
}, {passive: false});

$("canvas-scroll").addEventListener("pointerdown", event => {
  if (event.button !== 0 || dragging || busy || event.target.closest(".map-node, .cable-group, button")) return;
  const area = $("canvas-scroll"), rect = area.getBoundingClientRect();
  // Leave native scrollbar interaction to the browser.
  if (event.clientX >= rect.left + area.clientWidth || event.clientY >= rect.top + area.clientHeight) return;
  event.preventDefault();
  const start = {x: event.clientX, y: event.clientY, left: area.scrollLeft, top: area.scrollTop};
  let moved = false;
  dragging = true;
  area.setPointerCapture(event.pointerId);
  area.classList.add("panning");
  updateViewControls();
  const move = e => {
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    if (Math.hypot(dx, dy) > 4) moved = true;
    if (!moved) return;
    area.scrollLeft = start.left - dx;
    area.scrollTop = start.top - dy;
  };
  const finish = e => {
    area.removeEventListener("pointermove", move);
    area.removeEventListener("pointerup", finish);
    area.removeEventListener("pointercancel", finish);
    if (area.hasPointerCapture(e.pointerId)) area.releasePointerCapture(e.pointerId);
    area.classList.remove("panning");
    dragging = false;
    suppressMapClick = moved;
    // A drag's synthetic click must not deselect a node or remove a cable.
    setTimeout(() => { suppressMapClick = false; }, 0);
    updateViewControls();
  };
  area.addEventListener("pointermove", move);
  area.addEventListener("pointerup", finish);
  area.addEventListener("pointercancel", finish);
});
$("canvas-scroll").addEventListener("click", event => {
  if (suppressMapClick) { event.stopPropagation(); return; }
  if (!event.target.closest(".map-node, .cable-group, button")) {
    selected = null; render(); renderInspector();
  }
}, true);

function renderInspector() {
  const n = nodeById(selected);
  $("inspector-empty").hidden = !!n; $("node-form").hidden = !n;
  if (!n) return;
  $("selected-icon").className = `device-icon ${n.type}`;
  $("selected-icon").innerHTML = icons[n.type];
  $("selected-title").textContent = n.name;
  $("node-name").value = n.name;
  $("node-image").innerHTML = (n.type === "pc" ? [{name: n.image}] : images.filter(i => n.type !== "router" || !noConfigExport({image:i.name}))).map(i => `<option value="${esc(i.name)}">${esc(i.name)}</option>`).join("");
  $("node-image").value = n.image;
  $("node-image").dataset.previousImage = n.image;
  $("node-memory").value = n.memory; renderEthernetSettings(n);
  $("node-ipv4").value = n.ipv4; $("node-gateway").value = n.gateway;
  $("iol-settings").hidden = n.type === "pc"; $("pc-settings").hidden = n.type !== "pc";
  $("interface-count").textContent = nodePorts(n).length;
  $("interfaces").innerHTML = nodePorts(n).map(port => {
    const link = portLink(n.id, port), other = link && (link.a.node === n.id ? link.b : link.a);
    return `<div class="interface ${link ? "connected" : ""}"><code>${port}</code><span>${link ? `${esc(nodeById(other.node).name)} · ${esc(other.port)}` : "Available"}</span>${link ? `<button type="button" data-unlink="${esc(link.id)}" title="Remove cable" ${hasRunning() ? "disabled" : ""}>×</button>` : ""}</div>`;
  }).join("");
  updateInspectorStatus(); updateControls();
}

function renderEthernetSettings(n) {
  const qcow = isQemu(n), exos = isExos(n), veos = isVeos(n);
  $("ethernet-label").textContent = qcow ? "Ethernet interfaces" : "Ethernet slots";
  $("node-ethernet").innerHTML = Array.from({length: exos ? 12 : veos ? 15 : qcow ? 16 : 8}, (_, i) => `<option>${i + (exos || veos ? 2 : 1)}</option>`).join("");
  $("node-ethernet").value = n.ethernet;
  $("ethernet-help").textContent = veos ? "Includes Management1 plus Ethernet data ports. Uses 2 vCPUs; tested with 6144 MB RAM. Requires Aboot-veos-serial-8.0.2.iso. On first boot, log in as admin and run zerotouch disable (reboots)." : exos ? "Includes Mgmt plus numbered data ports (1–12). Allow a few minutes for EXOS to boot." : qcow
    ? "Up to 16 GigabitEthernet interfaces. Allow a few minutes for IOSv to boot."
    : "Four Ethernet ports per slot. Interfaces are shown as slot/port.";
}
$("node-image").addEventListener("change", () => {
  const n = nodeById(selected);
  if (!n) return;
  const previous = {...n, image: $("node-image").dataset.previousImage || n.image};
  const changed = {...n, image: $("node-image").value, ethernet: Number($("node-ethernet").value)};
  if (isQemu(previous) !== isQemu(changed) || isExos(previous) !== isExos(changed) || isVeos(previous) !== isVeos(changed)) changed.ethernet = isVeos(changed) ? 5 : isExos(changed) ? 13 : isQemu(changed) ? 4 : 2;
  if (isVeos(previous) !== isVeos(changed)) $("node-memory").value = isVeos(changed) ? 6144 : 1024;
  $("node-image").dataset.previousImage = changed.image;
  renderEthernetSettings(changed);
});

function updateInspectorStatus() {
  if (!selected) return;
  $("selected-status").textContent = state(selected);
  $("node-error").textContent = statuses[selected]?.error || "";
  $("node-error").hidden = !statuses[selected]?.error;
}

function selectNode(id, open = true) {
  selected = id; render(); renderInspector();
  if (panelMedia.matches && state(id) !== "running") setPanel("inspector", true);
  if (open && (state(id) === "running" || consoleSessions.has(id))) openConsole(id);
}

function setMode(next) {
  if (busy) return;
  if (next === "link" && hasRunning()) return toast("Stop the lab before adding connections.", true);
  mode = next; linkSource = null;
  $("select-tool").classList.toggle("active", mode === "select");
  $("link-tool").classList.toggle("active", mode === "link");
  $("canvas").classList.toggle("link-mode", mode === "link");
  $("canvas-hint").textContent = mode === "link" ? "Choose the first device, then the second" : "";
  render();
}

function connectNode(id) {
  if (!linkSource) { linkSource = id; render(); $("canvas-hint").textContent = "Now choose a second device"; return; }
  if (linkSource === id) { linkSource = null; render(); return; }
  const a = nodeById(linkSource), b = nodeById(id);
  if (a.type === "pc" && b.type === "pc") return toast("Connect each PC to a switch or router.", true);
  if (!freePorts(a).length || !freePorts(b).length) return toast("One of these devices has no available interfaces.", true);
  pendingLink = {a: a.id, b: b.id};
  $("link-a-name").textContent = a.name; $("link-b-name").textContent = b.name;
  for (const [side, node] of [["a", a], ["b", b]]) $("link-" + side + "-port").innerHTML = freePorts(node).map(p => `<option>${p}</option>`).join("");
  $("link-dialog").showModal();
}

document.querySelectorAll(".device-template").forEach(el => {
  el.addEventListener("dragstart", e => { e.dataTransfer.setData("application/x-iol-device", el.dataset.type); e.dataTransfer.effectAllowed = "copy"; setTimeout(dismissCompactPanel, 0); });
  el.addEventListener("click", () => { const area = $("canvas-scroll"); addNode(el.dataset.type, (area.scrollLeft + area.clientWidth / 2) / mapZoom, (area.scrollTop + Math.min(area.clientHeight / 2, 350)) / mapZoom); });
});
$("canvas-scroll").addEventListener("dragover", e => { if (!hasRunning() && !busy) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
$("canvas-scroll").addEventListener("drop", e => { e.preventDefault(); const type = e.dataTransfer.getData("application/x-iol-device"); if (!icons[type]) return; const point = mapPoint(e.clientX, e.clientY); addNode(type, point.x, point.y); });
$("nodes").addEventListener("pointerdown", e => {
  const target = e.target.closest(".map-node");
  if (!target || e.button !== 0 || busy) return;
  e.preventDefault();
  const id = target.dataset.id;
  if (mode === "link") { connectNode(id); return; }
  const n = nodeById(id), original = {x: n.x, y: n.y}, start = {x: e.clientX, y: e.clientY};
  let moved = false;
  dragging = true; target.setPointerCapture(e.pointerId);
  updateViewControls();
  const move = event => {
    const dx = event.clientX - start.x, dy = event.clientY - start.y;
    if (Math.hypot(dx, dy) > 4) moved = true;
    if (!moved) return;
    n.x = Math.round(Math.max(70, Math.min(2330, original.x + dx / mapZoom)));
    n.y = Math.round(Math.max(60, Math.min(1540, original.y + dy / mapZoom)));
    target.style.left = n.x + "px"; target.style.top = n.y + "px"; renderLinks();
  };
  const up = event => {
    target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up); target.removeEventListener("pointercancel", cancel);
    dragging = false;
    updateViewControls();
    if (moved) { selected = id; edit(() => {}); } else selectNode(id);
  };
  const cancel = () => { n.x = original.x; n.y = original.y; moved = false; up(); };
  target.addEventListener("pointermove", move); target.addEventListener("pointerup", up); target.addEventListener("pointercancel", cancel);
});
$("nodes").addEventListener("keydown", e => { const target = e.target.closest(".map-node"); if (target && ["Enter", " "].includes(e.key)) { e.preventDefault(); mode === "link" ? connectNode(target.dataset.id) : selectNode(target.dataset.id); } });
$("node-list").addEventListener("click", e => { const target = e.target.closest("[data-id]"); if (target) selectNode(target.dataset.id); });
function requestDelete(kind, id) {
  if (busy || !loaded) return;
  if (hasRunning()) return toast("Stop the lab before deleting devices or cables.", true);
  if (kind === "node") {
    const node = nodeById(id);
    if (!node) return;
    const count = topology.links.filter(link => link.a.node === id || link.b.node === id).length;
    $("delete-title").textContent = `Delete ${node.name}?`;
    $("delete-description").textContent = count ? `This removes the device and its ${count} connected ${count === 1 ? "cable" : "cables"} from the topology.` : "This removes the device from the topology.";
  } else {
    const link = topology.links.find(link => link.id === id);
    if (!link) return;
    $("delete-title").textContent = "Delete this cable?";
    $("delete-description").textContent = `${nodeById(link.a.node).name} ${link.a.port} ↔ ${nodeById(link.b.node).name} ${link.b.port}`;
  }
  pendingDelete = {kind, id};
  $("delete-dialog").showModal();
}
$("delete-form").addEventListener("submit", event => {
  if (event.submitter?.value !== "delete" || !pendingDelete) return;
  const {kind, id} = pendingDelete;
  structuralEdit(() => {
    if (kind === "node") {
      topology.links = topology.links.filter(link => link.a.node !== id && link.b.node !== id);
      topology.nodes = topology.nodes.filter(node => node.id !== id);
      if (selected === id) selected = null;
    } else topology.links = topology.links.filter(link => link.id !== id);
  });
});
$("delete-dialog").addEventListener("close", () => {
  // A queued close event from the previous prompt can arrive after reopening it.
  if (!$("delete-dialog").open) pendingDelete = null;
});
const unlink = id => requestDelete("link", id);
function openLinkActions(id) {
  if (busy || !topology.links.some(link => link.id === id)) return;
  activeLinkAction = id; updateLinkActions(); $('link-actions-dialog').showModal();
}
function updateLinkActions() {
  if (!activeLinkAction) return;
  const link = topology.links.find(link => link.id === activeLinkAction);
  if (!link) { $('link-actions-dialog').close(); activeLinkAction = null; return; }
  const value = linkStates[link.id] || {}, label = side => `${nodeById(link[side].node).name} ${link[side].port}`;
  $('link-actions-endpoints').textContent = `A: ${label('a')} ↔ B: ${label('b')}`;
  $('link-actions-state').textContent = linkDescription(link);
  $('link-actions-help').textContent = linkError || (value.available ? 'Closing this window leaves faults active. Stopping the whole lab clears them.' : 'Start a connected node to enable traffic controls.');
  document.querySelectorAll('[data-link-traffic]').forEach(button => {
    const mode = button.dataset.linkTraffic;
    button.disabled = busy || !value.available;
    button.setAttribute('aria-pressed', String(value.available && !linkError && ['a','b'].every(side => state(link[side].node) === 'running' && value['carrier_'+side] === 'up' && !value['carrier_pending_'+side] && !value['carrier_error_'+side]) && !!value.blocked_a_to_b === ['a','both'].includes(mode) && !!value.blocked_b_to_a === ['b','both'].includes(mode)));
    if (mode === 'a') button.textContent = `Block ${label('a')} → ${label('b')}`;
    if (mode === 'b') button.textContent = `Block ${label('b')} → ${label('a')}`;
  });
  $('link-carrier-actions').hidden = !['a','b'].some(side => value['carrier_capable_'+side] || value['carrier_supported_'+side]);
  document.querySelectorAll('[data-link-carrier]').forEach(button => {
    const side = button.dataset.linkCarrier, down = value['carrier_'+side] !== 'up';
    button.hidden = !value['carrier_capable_'+side] && !value['carrier_supported_'+side];
    button.disabled = busy || !value.available || !value['carrier_supported_'+side];
    button.textContent = `${down ? 'Reconnect' : 'Unplug'} ${label(side)}${!value['carrier_supported_'+side] ? ' (unavailable)' : ''}`;
    button.title = value['carrier_supported_'+side] ? '' : `Start ${nodeById(link[side].node).name} to enable carrier controls`;
    button.classList.toggle('carrier-fault', down);
    button.setAttribute('aria-label', `${button.textContent}${down ? ' (currently interrupted)' : ''}`);
  });
  $('link-actions-delete').disabled = busy || hasRunning();
}
$('link-actions-close').onclick = () => $('link-actions-dialog').close();
$('link-actions-dialog').addEventListener('close', () => { if (!$('link-actions-dialog').open) activeLinkAction = null; });
$('link-actions-delete').onclick = () => { const id = activeLinkAction; $('link-actions-dialog').close(); unlink(id); };
document.querySelectorAll('[data-link-traffic]').forEach(button => button.onclick = () => {
  const id = activeLinkAction, mode = button.dataset.linkTraffic;
  perform('Updating link traffic…', () => api(`/api/links/${encodeURIComponent(id)}/traffic`, 'POST', {
    blocked_a_to_b: ['a','both'].includes(mode), blocked_b_to_a: ['b','both'].includes(mode)
  }));
});
document.querySelectorAll('[data-link-carrier]').forEach(button => button.onclick = () => {
  const id = activeLinkAction, side = button.dataset.linkCarrier;
  perform('Updating cable state…', () => api(`/api/links/${encodeURIComponent(id)}/carrier`, 'POST', {
    side, up: linkStates[id]['carrier_'+side] !== 'up'
  }));
});
$('links').addEventListener('click', e => { const target = e.target.closest('[data-link]'); if (target) hasRunning() ? openLinkActions(target.dataset.link) : unlink(target.dataset.link); });
$('links').addEventListener('contextmenu', e => { const target = e.target.closest('[data-link]'); if (target) { e.preventDefault(); openLinkActions(target.dataset.link); } });
$('links').addEventListener('keydown', e => { const target = e.target.closest('[data-link]'); if (target && ['Enter',' '].includes(e.key)) { e.preventDefault(); openLinkActions(target.dataset.link); } });
$("interfaces").addEventListener("click", e => { const target = e.target.closest("[data-unlink]"); if (target) unlink(target.dataset.unlink); });
$("select-tool").onclick = () => setMode("select"); $("link-tool").onclick = () => setMode("link");
$("link-form").addEventListener("submit", e => {
  if (e.submitter?.value !== "connect") return;
  structuralEdit(() => topology.links.push({id: uid(), a: {node: pendingLink.a, port: $("link-a-port").value}, b: {node: pendingLink.b, port: $("link-b-port").value}}));
  linkSource = null;
});
$("link-dialog").addEventListener("close", () => { linkSource = null; render(); $("canvas-hint").textContent = "Choose the first device, then the second"; });
let imageUpload = null;
$("upload-image").onclick = () => {
  $("image-form").reset();
  $("image-progress").hidden = true;
  $("image-message").textContent = "";
  $("image-message").className = "";
  $("image-dialog").showModal();
};
$("cancel-image").onclick = () => imageUpload ? imageUpload.abort() : $("image-dialog").close();
$("image-dialog").addEventListener("cancel", event => {
  if (imageUpload) { event.preventDefault(); imageUpload.abort(); }
});
$("image-form").addEventListener("submit", async event => {
  event.preventDefault();
  const file = $("image-file").files[0];
  if (!file || busy) return;
  busy = true; updateControls();
  $("submit-image").disabled = $("image-file").disabled = true;
  $("image-progress").hidden = false;
  $("image-progress").value = 0;
  $("image-message").className = "";
  $("image-message").textContent = "Uploading…";
  try {
    const result = await new Promise((resolve, reject) => {
      const request = imageUpload = new XMLHttpRequest();
      request.open("POST", `/api/images/${encodeURIComponent(file.name)}`);
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.responseType = "json";
      request.upload.onprogress = e => {
        if (e.lengthComputable) $("image-progress").value = e.loaded / e.total * 100;
        if (e.loaded === e.total) $("image-message").textContent = "Saving image…";
      };
      request.onload = () => request.status === 201 ? resolve(request.response) : reject(new Error(request.response?.error || "Image upload failed"));
      request.onerror = () => reject(new Error("Connection lost. Check the image library before retrying."));
      request.onabort = () => reject(new Error("Upload cancelled. If saving had already finished, the image may be available."));
      request.send(file);
    });
    accept(result);
    $("image-dialog").close();
    toast(`${file.name} is ready to use.`);
  } catch (error) {
    $("image-message").className = "error";
    $("image-message").textContent = error.message;
  } finally {
    imageUpload = null; busy = false;
    $("submit-image").disabled = $("image-file").disabled = false;
    updateControls();
  }
});

$("node-form").addEventListener("submit", e => {
  e.preventDefault();
  edit(() => { const n = nodeById(selected); n.name = $("node-name").value.trim(); n.image = $("node-image").value; n.memory = Number($("node-memory").value); n.ethernet = Number($("node-ethernet").value); n.ipv4 = $("node-ipv4").value.trim(); n.gateway = $("node-gateway").value.trim(); });
});
$("lab-name").addEventListener("change", () => edit(() => topology.name = $("lab-name").value.trim()));
$("delete-node").onclick = () => requestDelete("node", selected);
$("start-all").onclick = () => { setMode("select"); perform("Starting lab…", () => api("/api/lab/start", "POST", {})); };
$("stop-all").onclick = () => perform("Stopping lab…", () => api("/api/lab/stop", "POST", {}));
$("start-node").onclick = () => { setMode("select"); perform("Starting device…", () => api(`/api/nodes/${selected}/start`, "POST", {})); };
$("stop-node").onclick = () => perform("Stopping device…", () => api(`/api/nodes/${selected}/stop`, "POST", {}));
$("open-console").onclick = () => openConsole(selected);
$("starter").onclick = () => structuralEdit(() => {
  const r = newNode("router", 180, 180), s = newNode("switch", 420, 320), p = newNode("pc", 660, 180);
  p.ipv4 = "10.0.10.10/24"; p.gateway = "10.0.10.1";
  topology.name = "My first network"; topology.nodes.push(r, s, p);
  topology.links.push({id: uid(), a: {node:r.id, port:nodePorts(r)[0]}, b:{node:s.id, port:nodePorts(s)[noConfigExport(s) ? 1 : 0]}}, {id:uid(), a:{node:s.id,port:nodePorts(s)[noConfigExport(s) ? 2 : 1]}, b:{node:p.id,port:"eth0"}});
  selected = r.id;
});
function updateExportControls() {
  $("export-initial").disabled = busy || topology.nodes.some(n => noConfigExport(n) || n.type !== "pc" && state(n.id) !== "running");
  $("export-saved").disabled = busy || topology.nodes.some(n => noConfigExport(n) || n.type !== "pc" && state(n.id) === "running");
  $("export-zip").disabled = busy || hasRunning();
  $("export-json").disabled = $("cancel-export").disabled = $("export-logs").disabled = busy;
}
$("export").onclick = () => {
  updateExportControls();
  $("export-message").textContent = topology.nodes.some(noConfigExport) ? "EXOS and Arista config snippets and extraction are not supported yet. Use Topology JSON or, with all devices stopped, Saved lab ZIP." : hasRunning() ? "Saved configs require stopped Cisco devices; saved lab ZIP requires all devices stopped. Live configs require running Cisco devices with ready consoles." : "";
  $("export-dialog").showModal();
};
$("cancel-export").onclick = () => $("export-dialog").close();
function downloadTopology(data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + "\n"], {type:"application/json"}));
  const a = document.createElement("a"); a.href = url; a.download = (data.name.replace(/[^a-z0-9_-]/gi, "-") || "lab") + ".json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export-json").onclick = () => { downloadTopology(topology); $("export-dialog").close(); };
async function exportConfigs(saved) {
  if (busy) return;
  busy = true; updateControls();
  updateExportControls();
  $("export-message").textContent = saved ? "Reading saved configurations from device storage… IOSv disks can take a minute. Unsaved changes are excluded." : "Reading running configurations… Consoles are locked during capture; original console logging settings are restored before export.";
  try {
    downloadTopology(await api(saved ? "/api/export/saved-configs" : "/api/export/initial-configs", "POST", {}));
    $("export-dialog").close(); toast("Initial configurations exported. They apply only to fresh devices.");
  } catch (error) { $("export-message").textContent = error.message; }
  finally {
    busy = false; updateControls();
    updateExportControls();
  }
}
$("export-initial").onclick = () => exportConfigs(false);
$("export-saved").onclick = () => exportConfigs(true);
$("export-zip").onclick = async () => {
  if (busy) return;
  busy = true; updateControls();
  updateExportControls();
  $("export-message").textContent = "Preparing saved lab… Large disks can take a few minutes.";
  try {
    const result = await api("/api/export", "POST", {include_logs:$("export-logs").checked});
    const a = document.createElement("a"); a.href = result.url; a.download = result.filename;
    document.body.append(a); a.click(); a.remove();
    $("export-dialog").close(); toast("Backup ready. Your browser will download the ZIP.");
  } catch (error) { $("export-message").textContent = error.message; }
  finally {
    busy = false; updateControls();
    updateExportControls();
  }
};
$("export-dialog").addEventListener("cancel", event => { if (busy) event.preventDefault(); });
let importBackup = null, restoringBackup = false;
$("import").onclick = () => $("import-file").click();
$("import-file").onchange = async () => {
  const file = $("import-file").files[0]; if (!file) return;
  try {
    if (file.name.toLowerCase().endsWith(".zip")) {
      if (file.size > 8 * 1024 ** 3) throw new Error("Backup exceeds 8 GiB");
      importBackup = file;
      $("import-message").textContent = file.name;
      $("import-progress").hidden = true;
      $("import-dialog").showModal();
    } else {
      if (file.size > 1000000) throw new Error("Topology file is too large");
      const data = JSON.parse(await file.text());
      await structuralEdit(() => { topology = data; selected = null; });
    }
  } catch (error) { toast(error.message, true); }
  $("import-file").value = "";
};
$("cancel-import").onclick = () => $("import-dialog").close();
$("import-dialog").addEventListener("close", () => { if (!$("import-dialog").open) importBackup = null; });
$("import-dialog").addEventListener("cancel", event => { if (restoringBackup) event.preventDefault(); });
$("confirm-import").onclick = async () => {
  if (busy || !importBackup) return;
  if (hasRunning()) { $("import-message").textContent = "Stop all devices before restoring a backup."; return; }
  busy = restoringBackup = true; updateControls();
  $("confirm-import").disabled = $("cancel-import").disabled = true;
  $("import-progress").hidden = false; $("import-progress").value = 0;
  $("import-message").textContent = "Uploading backup…";
  try {
    const result = await new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", "/api/import");
      request.setRequestHeader("Content-Type", "application/zip"); request.responseType = "json";
      request.upload.onprogress = event => {
        if (event.lengthComputable) $("import-progress").value = event.loaded / event.total * 100;
        if (event.loaded === event.total) $("import-message").textContent = "Checking images and restoring saved state…";
      };
      request.onload = () => request.status === 200 ? resolve(request.response) : reject(new Error(request.response?.error || "Backup import failed"));
      request.onerror = () => reject(new Error("Connection lost. Check the lab state before retrying."));
      request.send(importBackup);
    });
    // Restored nodes may reuse IDs; their old console histories belong to the previous lab.
    for (const id of [...consoleSessions.keys()]) closeConsole(id);
    selected = null; accept(result);
    $("import-dialog").close(); toast("Saved lab restored. Devices are stopped and ready to start.");
  } catch (error) { $("import-message").textContent = error.message; }
  finally {
    busy = restoringBackup = false; updateControls();
    $("confirm-import").disabled = $("cancel-import").disabled = false;
  }
};
$("logs").onclick = async () => {
  try { $("log-content").textContent = (await api(`/api/nodes/${selected}/logs`)).logs; $("logs-dialog").showModal(); }
  catch (e) { toast(e.message, true); }
};
$("close-logs").onclick = () => $("logs-dialog").close();

function closeSocket(session) {
  clearTimeout(session.reconnectTimer);
  session.reconnectTimer = null;
  const old = session.socket; session.socket = null;
  session.lockReady = false;
  if (old) old.close();
}

function setConsoleStatusLabel(element, text, state = 'offline') {
  element.querySelector('.console-status-text').textContent = text;
  element.title = text; element.setAttribute('aria-label',text);
  element.dataset.connection = state;
}
function consoleStatus(session, text) {
  session.status = text;
  const connected = session.socket?.readyState === WebSocket.OPEN && session.lockReady;
  const observing = session.locked && !session.lockMine;
  session.terminal.options.disableStdin = !connected || observing;
  const detail = observing ? "Read-only · locked elsewhere" : session.lockMine ? "Input locked · this station" : "Connected · shared";
  session.dot.className = `little-dot ${text === "Connected" ? "running" : text === "Stopped" ? "stopped" : "error"}`;
  session.tab.title = `${session.name} · ${text === "Connected" ? detail : text}`;
  session.label.textContent = session.name + (session.locked ? " 🔒" : "");
  const views = [];
  if (consoleNode === session.id) views.push($);
  if (session.floating) views.push(session.floating.q);
  for (const q of views) {
    setConsoleStatusLabel(q("console-status"),text === "Connected" ? detail : text,
      connected ? observing ? 'observing' : session.lockMine ? 'owned' : 'shared' : 'offline');
    q("reconnect-console").disabled = !session.running;
    q("lock-console").checked = !!session.locked;
    q("lock-console").disabled = !connected || observing;
    q("console-lock-label").title = observing ? "Another station holds the input lock" : session.lockMine ? "Release the lock so all stations can type" : "Allow only this station to type in this console";
    q("console-lock-label").dataset.lockOwner = session.lockMine ? 'mine' : observing ? 'other' : 'none';
    q("takeover-console").hidden = !observing;
    q("takeover-console").disabled = !connected;
    q("console-toolbar").querySelectorAll('[data-console-key]').forEach(button => button.disabled = !connected || observing);
  }
}

function sendConsoleInput(session, data) {
  if (session.socket?.readyState === WebSocket.OPEN && session.lockReady && (!session.locked || session.lockMine)) {
    session.socket.send(new TextEncoder().encode(data));
  }
}

function consoleControl(session, action, revision = session.lockRevision) {
  if (session.socket?.readyState !== WebSocket.OPEN || !session.lockReady) return;
  session.socket.send(JSON.stringify({action, revision}));
}

$("lock-console").onchange = () => {
  const session = consoleSessions.get(consoleNode);
  if (!session) return;
  const action = $("lock-console").checked ? "lock" : "unlock";
  consoleStatus(session, session.status); // Show confirmed server state until acknowledged.
  consoleControl(session, action);
};
let pendingTakeover = null;
function requestConsoleTakeover(session) {
  if (!session || !session.locked || session.lockMine) return;
  pendingTakeover = {session, socket:session.socket, revision:session.lockRevision};
  $("takeover-description").textContent = `${session.name} is locked by another station. Take over to lock input to this station; the previous station will still see output but can no longer type.`;
  $("takeover-dialog").showModal();
}
$("takeover-console").onclick = () => requestConsoleTakeover(consoleSessions.get(consoleNode));
$("takeover-form").onsubmit = event => {
  if (event.submitter?.value !== "takeover" || !pendingTakeover) return;
  const {session, socket, revision} = pendingTakeover;
  if (consoleSessions.get(session.id) !== session || session.socket !== socket || !session.lockReady) {
    toast("The console connection changed. Please try again.", true);
    return;
  }
  consoleControl(session, "takeover", revision);
};
$("takeover-dialog").addEventListener("close", () => { if (!$("takeover-dialog").open) pendingTakeover = null; });

function writeConsoleOutput(session, data, replay = false) {
  session.outputQueue.push({data, replay});
  if (session.outputQueue.length !== 1) return;
  const next = () => {
    if (consoleSessions.get(session.id) !== session) return;
    const entry = session.outputQueue[0];
    session.replaying = entry.replay;
    session.terminal.write(entry.data, () => {
      session.outputQueue.shift(); session.replaying = false;
      if (session.outputQueue.length) next();
    });
  };
  next();
}

function connectConsole(session) {
  closeSocket(session);
  session.lockReady = false;
  if (consoleSessions.get(session.id) !== session || !session.running) return;
  consoleStatus(session, "Connecting…");
  const cursor = session.epoch ? `?cursor=${session.epoch}:${session.offset}` : "";
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/${encodeURIComponent(session.id)}${cursor}`, "netlab.console.v2");
  session.socket = ws; ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    if (session.socket !== ws) return;
    session.reconnectDelay = 1000;
    consoleStatus(session, "Connected");
  };
  ws.onmessage = event => {
    if (session.socket !== ws) return;
    if (typeof event.data === "string") {
      const message = JSON.parse(event.data);
      if (message.type === "console-lock") {
        const lost = session.lockMine && message.locked && !message.mine;
        session.lockReady = true; session.locked = message.locked; session.lockMine = message.mine; session.lockRevision = message.revision;
        session.terminalResponder = message.terminal_responder ?? true;
        consoleStatus(session, session.status);
        if (message.error) toast(message.error, true);
        else if (lost) toast(`${session.name}: another station took over input. This console is now read-only.`);
        return;
      }
      if (message.type !== "console-start") return;
      if (message.gap) session.terminal.write("\r\n[Console restarted or older output is no longer available]\r\n");
      session.epoch = message.epoch; session.offset = message.offset;
      session.replayRemaining = message.replay_bytes || 0;
    } else {
      const data = new Uint8Array(event.data);
      const replay = Math.min(session.replayRemaining, data.byteLength);
      if (replay) writeConsoleOutput(session, data.subarray(0, replay), true);
      if (replay < data.byteLength) writeConsoleOutput(session, data.subarray(replay));
      session.replayRemaining -= replay;
      session.offset += data.byteLength;
    }
  };
  ws.onerror = () => { if (session.socket === ws) consoleStatus(session, "Connection error"); };
  ws.onclose = () => {
    if (session.socket !== ws) return;
    session.socket = null;
    session.lockReady = false;
    if (!session.running) return;
    consoleStatus(session, `Retry in ${session.reconnectDelay / 1000}s`);
    session.reconnectTimer = setTimeout(() => connectConsole(session), session.reconnectDelay);
    session.reconnectDelay = Math.min(10000, session.reconnectDelay * 2);
  };
}

const systemClipboardAvailable = !!(window.isSecureContext && navigator.clipboard?.readText && navigator.clipboard?.writeText);
let clipboardEnabled = false, clipboardSource = systemClipboardAvailable ? 'system' : 'workspace';
let workspaceClipboard = '', clipboardGeneration = 0, clipboardCopySequence = 0;
try {
  const saved = JSON.parse(localStorage.getItem('netlab.console-clipboard'));
  clipboardEnabled = saved?.enabled === true;
  if (['system','workspace'].includes(saved?.source)) clipboardSource = saved.source;
} catch (_) { /* Clipboard preferences are optional. */ }
if (!systemClipboardAvailable) clipboardSource = 'workspace';
function updateClipboardControls() {
  document.querySelectorAll('[data-console-clipboard]').forEach(button => {
    button.textContent = `Clipboard: ${clipboardEnabled ? clipboardSource : 'off'}`;
    button.title = 'Copy on selection / right-click paste preferences';
  });
  $('clipboard-enabled').checked = clipboardEnabled;
  $('clipboard-source').value = clipboardSource;
  $('clipboard-source').querySelector('[value=system]').disabled = !systemClipboardAvailable;
  $('clipboard-help').textContent = clipboardSource === 'workspace'
    ? `${systemClipboardAvailable ? '' : 'Automatic system clipboard access is unavailable on this connection. '}Selections from terminals and Instructions are kept in this page for pasting into its terminals. This does not change your system clipboard. To paste from another application, use the browser's Paste action or keyboard shortcut.`
    : 'Selections are copied to your system clipboard. Right-click reads its current text, including text copied from other applications. Your browser may request clipboard permission. If access is denied, use normal browser copy/paste or select Workspace clipboard here.';
}
function changeClipboardPreference() {
  clipboardEnabled = $('clipboard-enabled').checked;
  clipboardSource = $('clipboard-source').value;
  clipboardGeneration++;
  try { localStorage.setItem('netlab.console-clipboard', JSON.stringify({enabled:clipboardEnabled, source:clipboardSource})); } catch (_) {}
  updateClipboardControls();
}
$('clipboard-enabled').onchange = $('clipboard-source').onchange = changeClipboardPreference;
$('clipboard-close').onclick = () => $('clipboard-dialog').close();
document.addEventListener('click', event => {
  if (!event.target.closest('[data-console-clipboard]')) return;
  updateClipboardControls(); $('clipboard-dialog').showModal();
});
updateClipboardControls();
function clipboardTextFits(text) {
  if (new TextEncoder().encode(text).length <= 1_000_000) return true;
  toast('Terminal clipboard text is limited to 1 MB.', true); return false;
}
async function copyWorkspaceSelection(text, alive = () => true) {
  if (!clipboardEnabled || !text || !alive() || !clipboardTextFits(text)) return;
  const sequence = ++clipboardCopySequence, generation = clipboardGeneration;
  if (clipboardSource === 'workspace') {
    workspaceClipboard = text; toast('Copied to workspace clipboard'); return;
  }
  try {
    await navigator.clipboard.writeText(text);
    if (sequence === clipboardCopySequence && generation === clipboardGeneration && alive()) toast('Copied to system clipboard');
  } catch (_) {
    if (sequence === clipboardCopySequence && generation === clipboardGeneration && alive()) toast('System copy was denied. Use normal Copy or choose Workspace clipboard in Clipboard preferences.', true);
  }
}
function bindConsoleClipboard(session) {
  let pasteSequence = 0;
  let pointerType = '';
  session.element.addEventListener('pointerdown', event => { pointerType = event.pointerType; }, true);
  const alive = () => consoleSessions.get(session.id) === session;
  const writable = () => alive() && session.socket?.readyState === WebSocket.OPEN && session.lockReady && (!session.locked || session.lockMine);
  session.terminal.onSelectionChange(() => copyWorkspaceSelection(session.terminal.getSelection(), alive));
  const nativeMenu = event => !clipboardEnabled || event.shiftKey || event.pointerType === 'touch' || pointerType === 'touch' || event.sourceCapabilities?.firesTouchEvents;
  // Prevent xterm's right-button handling from altering the selection or sending
  // mouse-reporting bytes before the paste action. Shift/touch remain native.
  session.element.addEventListener('mousedown', event => {
    if (event.button === 2 && !nativeMenu(event)) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  session.element.addEventListener('contextmenu', async event => {
    if (nativeMenu(event)) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (!writable()) { toast('This console is disconnected or locked for observation.', true); return; }
    const socket = session.socket, revision = session.lockRevision, generation = clipboardGeneration, sequence = ++pasteSequence;
    let text;
    try {
      text = clipboardSource === 'workspace' ? workspaceClipboard : await navigator.clipboard.readText();
    } catch (_) {
      toast('System paste was denied. Use normal Paste or choose Workspace clipboard in Actions → Clipboard.', true); return;
    }
    // Permission prompts may outlive a lock change, restart or settings change.
    if (!writable() || session.socket !== socket || session.lockRevision !== revision || generation !== clipboardGeneration || sequence !== pasteSequence || session.element.closest('[hidden]')) return;
    if (!text) { toast('The selected clipboard has no text.'); return; }
    if (!clipboardTextFits(text)) return;
    session.terminal.paste(text); // Preserve xterm's newline and bracketed-paste handling.
    session.terminal.focus();
  }, true);
}

const DEFAULT_CONSOLE_FONT = 12, MIN_CONSOLE_FONT = 8, MAX_CONSOLE_FONT = 28;
let consoleFontSize = DEFAULT_CONSOLE_FONT;
try {
  const saved = Number(localStorage.getItem("iol-console-font"));
  if (Number.isInteger(saved) && saved >= MIN_CONSOLE_FONT && saved <= MAX_CONSOLE_FONT) consoleFontSize = saved;
} catch (_) { /* Use the default when browser storage is unavailable. */ }

function updateConsoleFontControls() {
  const views = [$, ...[...consoleSessions.values()].filter(s => s.floating).map(s => s.floating.q)];
  for (const q of views) {
    q("console-font-reset").textContent = `${consoleFontSize}px`;
    q("console-font-decrease").disabled = consoleFontSize === MIN_CONSOLE_FONT;
    q("console-font-increase").disabled = consoleFontSize === MAX_CONSOLE_FONT;
  }
}
function setConsoleFontSize(size) {
  consoleFontSize = Math.max(MIN_CONSOLE_FONT, Math.min(MAX_CONSOLE_FONT, size));
  for (const session of consoleSessions.values()) session.terminal.options.fontSize = consoleFontSize;
  try { localStorage.setItem("iol-console-font", String(consoleFontSize)); } catch (_) { /* Keep this session's choice. */ }
  updateConsoleFontControls();
  requestAnimationFrame(fitConsole);
}
$("console-font-decrease").onclick = () => setConsoleFontSize(consoleFontSize - 1);
$("console-font-increase").onclick = () => setConsoleFontSize(consoleFontSize + 1);
$("console-font-reset").onclick = () => setConsoleFontSize(DEFAULT_CONSOLE_FONT);
updateConsoleFontControls();

function fitConsole() {
  document.querySelectorAll('.console-panel').forEach(panel => {
    if (!panel.hidden) panel.classList.toggle('console-compact',panel.clientWidth < 850);
  });
  for (const session of consoleSessions.values()) {
    if (session.floating ? !session.floating.panel.hidden : session.id === consoleNode && !$("console-panel").hidden) session.fitAddon.fit();
  }
}

function activateConsole(id, focusTerminal = true) {
  const session = consoleSessions.get(id);
  if (!session) return;
  if (session.floating) {
    session.floating.panel.hidden = false; bringConsoleForward(session.floating.panel);
    applyFloatingConsoleLayout(session.floating);
    if (focusTerminal) session.terminal.focus();
    return;
  }
  consoleNode = id;
  $("console-panel").hidden = false;
  for (const entry of consoleSessions.values()) {
    const active = entry === session;
    entry.element.hidden = !active && !entry.floating;
    entry.tab.setAttribute("aria-selected", String(active));
    entry.tab.tabIndex = active ? 0 : -1;
    entry.tabGroup.classList.toggle("active", active);
  }
  updateDockedConsoleState();
  applyConsoleLayout();
  if (consoleFloating) bringConsoleForward($("console-panel"));
  $("console-name").textContent = session.name;
  consoleStatus(session, session.status);
  session.tabGroup.scrollIntoView({block: "nearest", inline: "nearest"});
  if (focusTerminal) session.terminal.focus();
  requestAnimationFrame(() => {
    if (consoleNode !== id || $("console-panel").hidden) return;
    fitConsole();
  });
}

function openConsole(id) {
  dismissCompactPanel();
  // Raise the group above a floating map only in default/tabbed mode. Individual
  // windows already float; opening one must not also detach the shared panel.
  if (topologyWindow && (consoleOpenMode === null || consoleOpenMode === 'tabbed') && !consoleSessions.get(id)?.floating && (consoleSessions.has(id) || state(id) === 'running')) consoleFloating = true;
  if (consoleSessions.has(id)) { activateConsole(id); return; }
  if (state(id) !== "running") return toast("Start the device before opening its console.", true);
  if (!window.Terminal || !window.FitAddon) return toast("Terminal assets are missing. Run python3 fetch_assets.py and reload.", true);
  const element = document.createElement("div");
  element.id = `console-terminal-${id}`;
  element.className = "console-terminal";
  element.setAttribute("role", "tabpanel");
  element.setAttribute("aria-labelledby", `console-tab-${id}`);
  element.hidden = true;
  $("terminal").append(element);
  const tabGroup = document.createElement("div");
  tabGroup.className = "console-tab-group";
  tabGroup.setAttribute("role", "presentation");
  const tab = document.createElement("button");
  tab.id = `console-tab-${id}`;
  tab.className = "console-tab";
  tab.setAttribute("role", "tab");
  tab.setAttribute("aria-controls", element.id);
  const dot = document.createElement("span"), label = document.createElement("span");
  dot.setAttribute("aria-hidden", "true");
  tab.append(dot, label);
  const close = document.createElement("button");
  close.className = "console-tab-close";
  close.textContent = "×";
  const detach = document.createElement("button");
  detach.className = "console-tab-float"; detach.textContent = "↗";
  detach.title = `Float ${nodeById(id).name} console`; detach.setAttribute("aria-label", detach.title);
  detach.onclick = () => floatConsole(id);
  tabGroup.append(tab, detach, close);
  $("console-tabs").append(tabGroup);
  const terminal = new Terminal({cursorBlink:true, fontSize:consoleFontSize, fontFamily:'"DejaVu Sans Mono",Consolas,monospace', scrollback:10000, theme:{background:"#0c1117",foreground:"#dae3ee",cursor:"#67d8bc",selectionBackground:"#40645c"}});
  const fitAddon = new FitAddon.FitAddon();
  terminal.loadAddon(fitAddon);
  const session = {id, name: nodeById(id).name, terminal, fitAddon, element, tabGroup, tab, dot, label, close, detach,
                   outputQueue: [], replayRemaining: 0, replaying: false, terminalResponder: false,
                   lockReady: false, locked: false, lockMine: false, lockRevision: 0, epoch: null, offset: 0, socket: null, reconnectTimer: null, reconnectDelay: 1000, running: true, status: "Connecting…"};
  consoleSessions.set(id, session);
  // Give xterm a visible container for its initial measurement.
  $("console-panel").hidden = false;
  element.hidden = false;
  terminal.open(element);
  // Old terminal status/device queries are display history, not new requests.
  // For live queries only one station responds, avoiding duplicate CLI input.
  for (const final of ["n", "c"]) for (const prefix of ["", "?", ">", "="]) {
    terminal.parser.registerCsiHandler({prefix, final}, () => session.replaying || !session.terminalResponder);
  }
  for (const code of [10, 11, 12]) terminal.parser.registerOscHandler(code,
    data => data === "?" && (session.replaying || !session.terminalResponder));
  terminal.onData(data => sendConsoleInput(session, data));
  bindConsoleClipboard(session);
  tab.onclick = () => { selectNode(id, false); activateConsole(id); };
  close.onclick = () => closeConsole(id);
  tab.onkeydown = event => {
    const ids = [...consoleSessions.keys()], index = ids.indexOf(id);
    let next;
    if (event.key === "ArrowRight") next = ids[(index + 1) % ids.length];
    if (event.key === "ArrowLeft") next = ids[(index - 1 + ids.length) % ids.length];
    if (event.key === "Home") next = ids[0];
    if (event.key === "End") next = ids[ids.length - 1];
    if (event.key === "Delete") { event.preventDefault(); closeConsole(id); return; }
    if (next) {
      event.preventDefault(); selectNode(next, false); activateConsole(next, false);
      consoleSessions.get(next).tab.focus();
    }
  };
  syncConsoles();
  activateConsole(id);
  connectConsole(session);
  if (consoleOpenMode === 'floating') floatConsole(id,false);
}

function closeConsole(id) {
  const session = consoleSessions.get(id);
  if (!session) return;
  const ids = [...consoleSessions.keys()], index = ids.indexOf(id);
  const wasHidden = $("console-panel").hidden;
  closeSocket(session);
  consoleSessions.delete(id);
  session.terminal.dispose(); session.element.remove(); session.tabGroup.remove();
  removeFloatingConsole(session);
  if (consoleNode === id) {
    consoleNode = null;
    const next = [...consoleSessions.values()].find(s => !s.floating);
    if (next) activateConsole(next.id);
  }
  updateDockedConsoleState();
  if (!consoleSessions.size || wasHidden) $("console-panel").hidden = true;
  if (!consoleSessions.size) setConsoleStatusLabel($("console-status"),"Disconnected");
  updateControls();
}

function syncConsoles() {
  for (const session of consoleSessions.values()) {
    const node = nodeById(session.id);
    if (!node) { closeConsole(session.id); continue; }
    session.name = node.name;
    session.label.textContent = node.name;
    session.close.setAttribute("aria-label", `Close ${node.name} console`);
    session.close.title = `Close ${node.name} console (discard local history)`;
    session.detach.title = `${session.floating ? "Show" : "Float"} ${node.name} console`;
    session.detach.setAttribute("aria-label", session.detach.title);
    if (session.floating) { session.floating.q("console-name").textContent = node.name; session.floating.panel.setAttribute("aria-label", `${node.name} console`); }
    if (consoleNode === session.id) $("console-name").textContent = node.name;
    const running = state(session.id) === "running";
    if (session.running !== running) {
      session.running = running;
      if (running) connectConsole(session);
      else { closeSocket(session); consoleStatus(session, "Stopped"); }
    }
    consoleStatus(session, session.status);
  }
}

$("close-console").onclick = () => { $("console-panel").hidden = true; };
$("reconnect-console").onclick = () => { const session = consoleSessions.get(consoleNode); if (session) connectConsole(session); };
$("clear-console").onclick = () => { const session = consoleSessions.get(consoleNode); if (session) { session.terminal.clear(); session.terminal.focus(); } };
// Floating and docked layouts reuse the same DOM, terminals and WebSockets.
let consoleFloating = false, consoleMaximized = false, consoleRect = null;
let consoleDockHeight = null, consoleGesture = null;
const consoleDockParent = $("console-panel").parentElement;
let floatingConsoleOrder = [];
let topologyWindow = null;
// Other local document views participate in the same floating-window gestures.
const documentWindows = new Set();
const clampConsole = (value, low, high) => Math.max(low, Math.min(high, value));
function consoleViewport() {
  const view = window.visualViewport;
  return {x:(view?.offsetLeft || 0) + 8, y:(view?.offsetTop || 0) + 8,
          width:Math.max(1, (view?.width || innerWidth) - 16), height:Math.max(1, (view?.height || innerHeight) - 16)};
}
function boundedConsoleRect(rect) {
  const bounds = consoleViewport();
  const width = clampConsole(rect.width, Math.min(360, bounds.width), bounds.width);
  const height = clampConsole(rect.height, Math.min(240, bounds.height), bounds.height);
  return {x:clampConsole(rect.x, bounds.x, bounds.x + bounds.width - width),
          y:clampConsole(rect.y, bounds.y, bounds.y + bounds.height - height), width, height};
}
function fitWorkspaceViewport() {
  const view = window.visualViewport;
  // The keyboard can shrink only the visual viewport, leaving vh/innerHeight
  // unchanged. Keep the dock above its bottom; do not reflow during pinch zoom.
  if (view && Math.abs(view.scale - 1) > .01) return;
  const bottom = view ? view.offsetTop + view.height : innerHeight;
  document.documentElement.style.setProperty('--app-height', bottom+'px');
  document.documentElement.style.setProperty('--app-header-height', document.querySelector('.topbar').offsetHeight+'px');
}
function dockHeightLimits() {
  const area = document.querySelector('.workarea');
  const reserved = document.querySelector('.canvas-toolbar').offsetHeight + document.querySelector('.canvas-footer').offsetHeight + 80;
  const max = Math.max(120, area.clientHeight - reserved);
  return {min:Math.min(240, max), max};
}
function applyConsoleLayout() {
  fitWorkspaceViewport();
  const panel = $("console-panel");
  const parent = consoleFloating ? $("console-windows") : consoleDockParent;
  if (panel.parentElement !== parent) { parent.append(panel); if (consoleFloating) bringConsoleForward(panel); }
  panel.classList.toggle('floating', consoleFloating);
  panel.classList.toggle('maximized', consoleFloating && consoleMaximized);
  $("console-divider").hidden = consoleFloating;
  $("console-move").hidden = !consoleFloating || consoleMaximized;
  $("console-resize").hidden = !consoleFloating || consoleMaximized;
  $("detach-console").textContent = consoleFloating ? 'Dock' : 'Float';
  $("detach-console").title = consoleFloating ? 'Dock console panel' : 'Float console panel';
  $("detach-console").setAttribute('aria-label', $("detach-console").title);
  $("detach-console").setAttribute('aria-pressed', String(consoleFloating));
  const expanded = consoleFloating ? consoleMaximized : panel.classList.contains('expanded');
  $("expand-console").title = expanded ? 'Restore console size' : consoleFloating ? 'Maximize console' : 'Expand console';
  $("expand-console").setAttribute('aria-label', $("expand-console").title);
  $("expand-console").setAttribute('aria-pressed', String(expanded));
  $("expand-console").textContent = $("expand-console").title;
  if (consoleFloating) {
    const bounds = consoleViewport();
    consoleRect = boundedConsoleRect(consoleRect || {x:bounds.x + 40, y:bounds.y + 80, width:900, height:500});
    const rect = consoleMaximized ? bounds : consoleRect;
    Object.assign(panel.style, {left:rect.x+'px', top:rect.y+'px', width:rect.width+'px', height:rect.height+'px'});
  } else {
    const limits = dockHeightLimits();
    // Clamp only the displayed size, preserving the requested size when the
    // keyboard closes or the viewport grows again.
    panel.style.height = "";
    const requestedHeight = consoleDockHeight ?? parseFloat(getComputedStyle(panel).height);
    floatingConsoleOrder = floatingConsoleOrder.filter(p => p !== panel);
    Object.assign(panel.style, {zIndex:'', left:'', top:'', width:'', height:(expanded ? limits.max : clampConsole(requestedHeight, limits.min, limits.max))+'px'});
    $("console-divider").setAttribute('aria-valuemin', String(Math.round(limits.min)));
    $("console-divider").setAttribute('aria-valuemax', String(Math.round(limits.max)));
    $("console-divider").setAttribute('aria-valuenow', String(Math.round(clampConsole(panel.getBoundingClientRect().height, limits.min, limits.max))));
  }
  requestAnimationFrame(fitConsole);
}
$("detach-console").onclick = () => {
  consoleGesture = null;
  if (!consoleFloating && consoleRect === null) {
    const rect = $("console-panel").getBoundingClientRect();
    consoleRect = boundedConsoleRect({x:rect.x, y:Math.max(8,rect.y-80), width:Math.max(640,rect.width), height:Math.max(400,rect.height)});
  }
  consoleFloating = !consoleFloating;
  // Floating the group keeps new sessions together; Float tab selects windows.
  consoleOpenMode = consoleFloating ? 'tabbed' : 'docked';
  consoleMaximized = false;
  applyConsoleLayout();
};
$("expand-console").onclick = () => {
  consoleGesture = null;
  if (consoleFloating) consoleMaximized = !consoleMaximized;
  else $("console-panel").classList.toggle('expanded');
  applyConsoleLayout();
};
function setDockHeight(height) {
  const limits = dockHeightLimits();
  consoleDockHeight = clampConsole(height, limits.min, limits.max);
  $("console-panel").classList.remove('expanded');
  applyConsoleLayout();
}
function startConsoleGesture(event, kind, floating = null) {
  if (event.button !== 0 || consoleGesture || kind !== 'dock' && (floating ? floating.maximized : !consoleFloating || consoleMaximized)) return;
  if (kind === 'move' && !event.target.closest('.console-move,.console-title,.topology-window-title')) return;
  if (kind === 'move' && event.target.closest('button,input,label,summary,select') && !event.target.closest('.console-move')) return;
  event.preventDefault();
  const panel = floating ? floating.panel : $("console-panel");
  const box = panel.getBoundingClientRect();
  consoleGesture = {pointer:event.pointerId, kind, floating, x:event.clientX, y:event.clientY,
                    rect:{x:box.x,y:box.y,width:box.width,height:box.height}};
  event.currentTarget.setPointerCapture(event.pointerId);
}
function bindConsolePointer(handle, kind, floating = null) {
  handle.addEventListener('pointerdown', event => startConsoleGesture(event,kind,typeof floating === 'function' ? floating() : floating));
  handle.addEventListener('pointermove', event => {
    const g = consoleGesture;
    if (!g || g.pointer !== event.pointerId) return;
    const dx = event.clientX-g.x, dy = event.clientY-g.y;
    if (g.kind === 'dock') setDockHeight(g.rect.height-dy);
    else {
      const rect = boundedConsoleRect(g.kind === 'move' ? {...g.rect,x:g.rect.x+dx,y:g.rect.y+dy}
        : {...g.rect,width:g.rect.width+dx,height:g.rect.height+dy});
      if (g.floating) { g.floating.rect = rect; applyFloatingConsoleLayout(g.floating); }
      else { consoleRect = rect; applyConsoleLayout(); }
    }
  });
  for (const name of ['pointerup','pointercancel','lostpointercapture']) handle.addEventListener(name, event => {
    if (consoleGesture?.pointer === event.pointerId) consoleGesture = null;
  });
}
function bindConsoleKeys(handle, kind, view = null) {
  handle.addEventListener('keydown', event => {
    const floating = typeof view === 'function' ? view() : view;
    if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const step = event.shiftKey ? 40 : 10;
    const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
    const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
    if (kind === 'dock') {
      const limits = dockHeightLimits();
      setDockHeight(event.key === 'Home' ? limits.min : event.key === 'End' ? limits.max : $("console-panel").getBoundingClientRect().height-dy);
    } else if (floating ? !floating.maximized : consoleFloating && !consoleMaximized) {
      const old = floating ? floating.rect : consoleRect;
      const rect = boundedConsoleRect(kind === 'move' ? {...old,x:old.x+dx,y:old.y+dy}
        : {...old,width:old.width+dx,height:old.height+dy});
      if (floating) { floating.rect = rect; applyFloatingConsoleLayout(floating); }
      else { consoleRect = rect; applyConsoleLayout(); }
    }
  });
}
for (const [id,kind] of [['console-toolbar','move'],['console-resize','resize'],['console-divider','dock']]) bindConsolePointer($(id),kind);
for (const [id,kind] of [['console-move','move'],['console-resize','resize'],['console-divider','dock']]) bindConsoleKeys($(id),kind);
function resizeConsoleLayouts() {
  applyConsoleLayout();
  for (const session of consoleSessions.values()) if (session.floating) applyFloatingConsoleLayout(session.floating);
  if (topologyWindow) applyTopologyLayout();
  for (const view of documentWindows) if (!view.panel.hidden) view.applyLayout();
}
function bringConsoleForward(panel) {
  floatingConsoleOrder = floatingConsoleOrder.filter(p => p.isConnected && p.classList.contains('floating') && p !== panel);
  floatingConsoleOrder.push(panel);
  floatingConsoleOrder.forEach((p,i) => p.style.zIndex = String(i+1));
}
function updateDockedConsoleState() {
  const active = consoleSessions.get(consoleNode);
  const empty = !active || !!active.floating;
  $("console-panel").classList.toggle('no-docked-console', empty);
  for (const id of ['detach-console-tab','detach-console','expand-console','clear-console']) $(id).disabled = empty;
  $("detach-console").disabled = empty && !consoleFloating;
  if (empty) {
    $("console-name").textContent = 'Floating consoles';
    setConsoleStatusLabel($("console-status"),'Select a tab to show its window');
    $("reconnect-console").disabled = $("lock-console").disabled = true;
    $("lock-console").checked = false;
    $("console-lock-label").dataset.lockOwner = 'none';
    $("takeover-console").hidden = true;
    $("console-toolbar").querySelectorAll('[data-console-key]').forEach(button => button.disabled = true);
  }
}
function applyFloatingConsoleLayout(view) {
  if (view.applyLayout) { view.applyLayout(); return; }
  if (view === topologyWindow) { applyTopologyLayout(); return; }
  view.rect = boundedConsoleRect(view.rect);
  const rect = view.maximized ? consoleViewport() : view.rect;
  Object.assign(view.panel.style,{left:rect.x+'px',top:rect.y+'px',width:rect.width+'px',height:rect.height+'px'});
  view.panel.classList.toggle('maximized',view.maximized);
  view.q('console-move').hidden = view.q('console-resize').hidden = view.maximized;
  const expand = view.q('expand-console');
  expand.title = view.maximized ? 'Restore console size' : 'Maximize console';
  expand.setAttribute('aria-label',expand.title); expand.setAttribute('aria-pressed',String(view.maximized));
  expand.textContent = expand.title;
  requestAnimationFrame(fitConsole);
}
function removeFloatingConsole(session) {
  const view = session.floating;
  if (!view) return;
  if (consoleGesture?.floating === view) consoleGesture = null;
  view.observer.disconnect(); view.panel.remove();
  floatingConsoleOrder = floatingConsoleOrder.filter(p => p !== view.panel);
  session.floating = null;
}
function dockConsole(id, remember = true) {
  const session = consoleSessions.get(id);
  if (!session?.floating) return;
  if (remember) consoleOpenMode = 'docked';
  $("terminal").append(session.element);
  removeFloatingConsole(session);
  session.tabGroup.classList.remove('detached');
  session.detach.textContent = '↗';
  session.detach.title = `Float ${session.name} console`; session.detach.setAttribute('aria-label',session.detach.title);
  activateConsole(id);
}
function floatConsole(id, remember = true) {
  const session = consoleSessions.get(id);
  if (!session) return;
  if (session.floating) { activateConsole(id); return; }
  if (remember) consoleOpenMode = 'floating';
  dismissCompactPanel();
  const panel = document.createElement('section');
  panel.id = `console-window-${id}`; panel.className = 'console-panel floating console-window';
  panel.dataset.consoleId = id;
  panel.setAttribute('aria-label',`${session.name} console`);
  const toolbar = $("console-toolbar").cloneNode(true), resize = $("console-resize").cloneNode(true);
  toolbar.querySelectorAll('details').forEach(menu => menu.open = false);
  for (const root of [toolbar,resize]) {
    for (const el of [root,...root.querySelectorAll('[id]')]) if (el.id) el.id += '-'+id;
  }
  const host = document.createElement('div'); host.className = 'floating-terminal-host';
  panel.append(toolbar,host,resize); $("console-windows").append(panel);
  const q = name => panel.querySelector('#'+name+'-'+id);
  const offset = [...consoleSessions.values()].filter(s => s.floating).length*32;
  const bounds = consoleViewport();
  const view = {panel,q,maximized:false,rect:boundedConsoleRect({x:bounds.x+50+offset,y:bounds.y+90+offset,width:720,height:430})};
  session.floating = view;
  q('detach-console-tab').remove();
  q('console-name').textContent = session.name;
  q('detach-console').textContent = 'Dock'; q('detach-console').title = 'Dock this console';
  q('detach-console').setAttribute('aria-label','Dock this console'); q('detach-console').setAttribute('aria-pressed','true');
  for (const name of ['detach-console','expand-console','clear-console']) q(name).disabled = false;
  q('detach-console').onclick = () => dockConsole(id);
  q('expand-console').onclick = () => { view.maximized = !view.maximized; applyFloatingConsoleLayout(view); };
  q('close-console').title = 'Hide window (keep console connected)'; q('close-console').setAttribute('aria-label','Hide console window');
  q('close-console').onclick = () => { panel.hidden = true; };
  q('clear-console').onclick = () => { session.terminal.clear(); session.terminal.focus(); };
  q('reconnect-console').onclick = () => connectConsole(session);
  q('lock-console').onchange = () => {
    const action = q('lock-console').checked ? 'lock' : 'unlock';
    consoleStatus(session,session.status); consoleControl(session,action);
  };
  q('takeover-console').onclick = () => requestConsoleTakeover(session);
  q('console-font-decrease').onclick = () => setConsoleFontSize(consoleFontSize-1);
  q('console-font-increase').onclick = () => setConsoleFontSize(consoleFontSize+1);
  q('console-font-reset').onclick = () => setConsoleFontSize(DEFAULT_CONSOLE_FONT);
  host.append(session.element); session.element.hidden = false;
  session.tabGroup.classList.add('detached'); session.tabGroup.classList.remove('active');
  session.tab.setAttribute('aria-selected','false'); session.tab.tabIndex = 0;
  session.detach.textContent = '▣'; session.detach.title = `Show ${session.name} console`; session.detach.setAttribute('aria-label',session.detach.title);
  if (consoleNode === id) {
    consoleNode = null;
    const next = [...consoleSessions.values()].find(s => !s.floating);
    if (next) activateConsole(next.id,false);
  }
  updateDockedConsoleState(); updateConsoleFontControls(); consoleStatus(session,session.status);
  for (const [name,kind] of [['console-toolbar','move'],['console-resize','resize']]) bindConsolePointer(q(name),kind,view);
  for (const [name,kind] of [['console-move','move'],['console-resize','resize']]) bindConsoleKeys(q(name),kind,view);
  for (const event of ['pointerdown','focusin']) panel.addEventListener(event,()=>bringConsoleForward(panel));
  view.observer = new ResizeObserver(fitConsole); view.observer.observe(host);
  updateClipboardControls();
  applyFloatingConsoleLayout(view); bringConsoleForward(panel); session.terminal.focus();
}
$("detach-console-tab").onclick = () => floatConsole(consoleNode);
function setAllConsolesFloating(floating) {
  dismissCompactPanel(); consoleGesture = null;
  consoleOpenMode = floating ? 'floating' : 'docked';
  const active = consoleNode || [...consoleSessions.keys()][0];
  for (const session of consoleSessions.values()) {
    if (floating) floatConsole(session.id,false);
    else dockConsole(session.id,false);
  }
  // Return the group to its dock, either as a tab strip or the combined console.
  consoleFloating = false; consoleMaximized = false;
  applyConsoleLayout(); updateDockedConsoleState();
  if (active) {
    $("console-panel").hidden = false;
    activateConsole(active);
  }
}
function applyTopologyLayout() {
  const view = topologyWindow;
  if (!view) return;
  view.rect = boundedConsoleRect(view.rect);
  const rect = view.maximized ? consoleViewport() : view.rect;
  Object.assign(view.panel.style,{left:rect.x+'px',top:rect.y+'px',width:rect.width+'px',height:rect.height+'px'});
  view.panel.classList.toggle('maximized',view.maximized);
  $("topology-move").hidden = $("topology-resize").hidden = view.maximized;
  const expand = $("expand-topology");
  expand.title = view.maximized ? 'Restore topology size' : 'Maximize topology';
  expand.setAttribute('aria-label',expand.title);
  expand.setAttribute('aria-pressed',String(view.maximized));
  expand.textContent = view.maximized ? '↙' : '↗';
}
function floatTopology() {
  dismissCompactPanel();
  if (topologyWindow) { topologyWindow.panel.hidden = false; applyTopologyLayout(); bringConsoleForward(topologyWindow.panel); return; }
  const panel = $("topology-panel"), area = $("canvas-scroll");
  const scroll = {left:area.scrollLeft,top:area.scrollTop};
  const box = panel.getBoundingClientRect();
  topologyWindow = {panel,maximized:false,rect:boundedConsoleRect({x:box.x,y:box.y,width:Math.max(640,box.width),height:Math.max(460,box.height)})};
  panel.classList.add('floating');
  const toolbar = $("topology-toolbar-scroll");
  toolbar.insertBefore(panel.querySelector('.canvas-toolbar'),toolbar.querySelector('.console-actions'));
  $("console-windows").append(panel);
  $("topology-placeholder").hidden = false;
  $("topology-window-toolbar").hidden = false;
  $("float-topology").hidden = true;
  applyTopologyLayout(); bringConsoleForward(panel);
  area.scrollLeft = scroll.left; area.scrollTop = scroll.top;
  applyConsoleLayout();
}
function dockTopology() {
  if (!topologyWindow) return;
  const panel = topologyWindow.panel, area = $("canvas-scroll");
  const scroll = {left:area.scrollLeft,top:area.scrollTop};
  if (consoleGesture?.floating === topologyWindow) consoleGesture = null;
  topologyWindow = null;
  panel.hidden = false;
  panel.classList.remove('floating','maximized'); panel.removeAttribute('style');
  $("topology-placeholder").after(panel);
  $("topology-window-toolbar").after(panel.querySelector('.canvas-toolbar'));
  $("topology-placeholder").hidden = true;
  $("topology-window-toolbar").hidden = $("topology-resize").hidden = true;
  $("float-topology").hidden = false;
  floatingConsoleOrder = floatingConsoleOrder.filter(p => p !== panel);
  area.scrollLeft = scroll.left; area.scrollTop = scroll.top;
  applyConsoleLayout();
  $("float-topology").focus({preventScroll:true});
}
$("float-topology").onclick = $("show-topology").onclick = floatTopology;
$("dock-topology").onclick = dockTopology;
$("hide-topology").onclick = () => {
  if (consoleGesture?.floating === topologyWindow) consoleGesture = null;
  $("topology-panel").hidden = true;
  $("show-topology").focus({preventScroll:true});
};
$("expand-topology").onclick = () => {
  topologyWindow.maximized = !topologyWindow.maximized; applyTopologyLayout();
};
// Resolve the current view when a gesture starts; docking/reopening reuses listeners.
for (const [id,kind] of [['topology-window-toolbar','move'],['topology-resize','resize']]) bindConsolePointer($(id),kind,() => topologyWindow);
for (const [id,kind] of [['topology-move','move'],['topology-resize','resize']]) bindConsoleKeys($(id),kind,() => topologyWindow);
for (const event of ['pointerdown','focusin']) $("topology-panel").addEventListener(event,()=>{
  if (topologyWindow) bringConsoleForward(topologyWindow.panel);
});
function arrangeWindows() {
  const terminalFocus = document.activeElement?.closest('.xterm') ? document.activeElement : null;
  consoleGesture = null;
  floatTopology();
  if (consoleFloating && !consoleNode) {
    consoleFloating = false; consoleMaximized = false;
    applyConsoleLayout(); updateDockedConsoleState();
  }
  const windows = [{panel:topologyWindow.panel,set:rect => {
    topologyWindow.maximized = false; topologyWindow.rect = rect; applyTopologyLayout();
  }}];
  if (!$("console-panel").hidden && consoleNode) {
    consoleFloating = true;
    windows.push({panel:$("console-panel"),set:rect => {
      consoleMaximized = false; consoleRect = rect; applyConsoleLayout();
    }});
  }
  for (const session of consoleSessions.values()) {
    const view = session.floating;
    if (view && !view.panel.hidden) windows.push({panel:view.panel,set:rect => {
      view.maximized = false; view.rect = rect; applyFloatingConsoleLayout(view);
    }});
  }
  for (const view of documentWindows) if (!view.panel.hidden) windows.push({panel:view.panel,set:rect => {
    view.maximized = false; view.rect = rect; view.applyLayout();
  }});
  const bounds = consoleViewport(), gap = 8, count = windows.length;
  // Prefer readable landscape panes. Never squeeze terminals below their manual
  // resize minimum; cascade if this viewport cannot accommodate a complete grid.
  let grid = null;
  for (let cols = 1; cols <= count; cols++) {
    const rows = Math.ceil(count/cols);
    const width = (bounds.width-gap*(cols-1))/cols, height = (bounds.height-gap*(rows-1))/rows;
    if (width < Math.min(360,bounds.width) || height < Math.min(300,bounds.height)) continue;
    const score = Math.abs(Math.log(width/height/1.5)) + (cols*rows-count)/count;
    if (!grid || score < grid.score) grid = {cols,rows,width,height,score};
  }
  let row = 0, col = 0;
  windows.forEach((win,i) => {
    let rect;
    if (grid) {
      const cols = Math.floor(count/grid.rows) + (row < count%grid.rows ? 1 : 0);
      const width = (bounds.width-gap*(cols-1))/cols;
      rect = {x:bounds.x+col*(width+gap),y:bounds.y+row*(grid.height+gap),width,height:grid.height};
      if (++col === cols) { row++; col = 0; }
    } else {
      const offset = (i%6)*28;
      rect = boundedConsoleRect({x:bounds.x+offset,y:bounds.y+offset,width:Math.min(720,bounds.width-56),height:Math.min(460,bounds.height-56)});
    }
    win.set(rect); bringConsoleForward(win.panel);
  });
  // Reparenting a docked terminal can blur it. Restore focus in the same user
  // gesture so arranging does not dismiss a touch keyboard.
  if (terminalFocus?.isConnected) terminalFocus.focus({preventScroll:true});
  requestAnimationFrame(() => { fitMap(); fitConsole(); });
  if (!grid) toast('Not enough room to tile every window. Windows have been cascaded; hide some or enlarge the view, then arrange again.');
}
let touchConsoleControl = null;
const consoleTouchControlSelector = '.console-menu summary,.console-menu-list button,[data-console-key],[data-arrange-windows]';
document.addEventListener('click',event => {
  const control = event.target.closest(consoleTouchControlSelector);
  if (control && touchConsoleControl === control && control.matches('summary')) {
    // The native summary click may take focus even when pointerdown was
    // cancelled. Toggle explicitly for touch, retaining native keyboard behavior.
    event.preventDefault(); control.parentElement.open = !control.parentElement.open;
  }
  touchConsoleControl = null;
  const key = event.target.closest('[data-console-key]');
  if (key && !key.disabled) {
    const panel = key.closest('.console-panel');
    const session = consoleSessions.get(panel?.dataset.consoleId || consoleNode);
    if (session) {
      const data = key.dataset.consoleKey.match(/../g).map(hex => String.fromCharCode(parseInt(hex,16))).join('');
      sendConsoleInput(session,data); session.terminal.focus();
    }
  }
  if (event.target.closest('[data-arrange-windows]')) arrangeWindows();
  if (event.target.closest('[data-show-topology]')) floatTopology();
  if (event.target.closest('[data-float-consoles]')) setAllConsolesFloating(true);
  if (event.target.closest('[data-dock-consoles]')) setAllConsolesFloating(false);
  if (event.target.closest('.console-menu-list button')) event.target.closest('details').open = false;
});
function positionConsoleMenu(menu) {
  const list = menu.querySelector('.console-menu-list'), anchor = menu.querySelector('summary').getBoundingClientRect();
  const bounds = consoleViewport();
  list.style.maxHeight = bounds.height+'px';
  const box = list.getBoundingClientRect();
  const below = anchor.bottom+4;
  const top = below+box.height <= bounds.y+bounds.height ? below : anchor.top-box.height-4;
  list.style.left = clampConsole(anchor.right-box.width,bounds.x,bounds.x+bounds.width-box.width)+'px';
  list.style.top = clampConsole(top,bounds.y,bounds.y+bounds.height-box.height)+'px';
}
document.addEventListener('scroll',event => {
  if (!event.target.matches?.('.console-toolbar,.toolbar-scroll')) return;
  event.target.querySelectorAll('.console-menu[open]').forEach(positionConsoleMenu);
},true);
document.addEventListener('wheel',event => {
  if (event.ctrlKey || event.metaKey || event.target.closest('.console-menu-list') || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
  const toolbar = event.target.closest('.console-toolbar,.canvas-toolbar');
  if (!toolbar) return;
  const strip = toolbar.closest('.toolbar-scroll') || toolbar.querySelector('.toolbar-scroll') || toolbar;
  if (strip.scrollWidth <= strip.clientWidth) return;
  const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? strip.clientWidth : 1;
  strip.scrollLeft += event.deltaY * scale;
  event.preventDefault();
}, {passive:false});
document.addEventListener('toggle',event => {
  const menu = event.target;
  if (!menu.matches('.console-menu') || !menu.open) return;
  document.querySelectorAll('.console-menu[open]').forEach(other => { if (other !== menu) other.open = false; });
  positionConsoleMenu(menu);
},true);
document.addEventListener('pointerdown',event => {
  document.querySelectorAll('.console-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  touchConsoleControl = event.pointerType === 'touch' ? event.target.closest(consoleTouchControlSelector) : null;
  // A touch action must not focus a summary/button and dismiss the keyboard
  // before Arrange measures the visual viewport. Mouse/keyboard focus is native.
  if (touchConsoleControl || event.target.closest('[data-console-key]')) event.preventDefault();
},true);
document.addEventListener('pointercancel',()=>{ touchConsoleControl = null; },true);
document.addEventListener('keydown',event => {
  if (event.key !== 'Escape') return;
  const menu = document.querySelector('.console-menu[open]');
  if (menu) {
    event.preventDefault(); event.stopImmediatePropagation();
    menu.open = false; menu.querySelector('summary').focus();
  }
},true);
for (const target of [window,window.visualViewport].filter(Boolean)) target.addEventListener('resize',()=>{
  document.querySelectorAll('.console-menu[open]').forEach(positionConsoleMenu);
});
for (const event of ['pointerdown','focusin']) $("console-panel").addEventListener(event,()=>{
  if (consoleFloating) bringConsoleForward($("console-panel"));
});

window.addEventListener('resize', resizeConsoleLayouts);
window.visualViewport?.addEventListener('resize', resizeConsoleLayouts);
window.visualViewport?.addEventListener('scroll', resizeConsoleLayouts);
applyConsoleLayout();
new ResizeObserver(fitConsole).observe($("terminal"));
window.addEventListener("beforeunload", () => { for (const session of consoleSessions.values()) closeSocket(session); });
document.addEventListener("keydown", e => {
  if (e.target.closest("input,textarea,select,.xterm,.instructions-panel") || document.querySelector("dialog[open]") || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key.toLowerCase() === "v") setMode("select");
  if (e.key.toLowerCase() === "l") setMode("link");
  if (e.key === "Escape") { setMode("select"); selected = null; renderInspector(); render(); }
});
async function refresh() {
  if (busy || dragging) return;
  try { const result = await api("/api/state"); if (busy || dragging) return; accept(result, !loaded); $("save-state").textContent = "All changes saved"; }
  catch (e) { $("save-state").textContent = "Server unavailable"; if (!loaded) toast("Cannot reach the lab server. Start lab_server.py to use this page.", true); }
}
applyPanelLayout(); updateControls(); refresh(); setInterval(refresh, 2500);
