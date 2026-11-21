'use strict';
const $ = id => document.getElementById(id);
const state = {
  info: null, token: '', metricsSocket: null, terminalSocket: null, terminal: null, fitAddon: null,
  terminalConnected: false, terminalInitPromise: null, terminalFontLoaded: false, currentPath: null, parentPath: null, files: [], selectedPaths: new Set(), lastSelectedPath: null,
  showHidden: false, view: 'grid', sortBy: 'name', sortDirection: 1, fileHistory: [], volumes: [], services: [],
  metricHistory: [], previousNet: null, mediaPath: null, mediaParent: null, contextItem: null, resizeObserver: null,

};

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options, headers: { ...(options.headers || {}) } });
  let body = {};
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) throw new Error(body.detail || response.statusText || 'Request failed');
  return body;
}
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const bytes = value => {
  if (value === null || value === undefined) return '—';
  const units = ['B','KB','MB','GB','TB','PB']; let n = Number(value) || 0, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
};
const duration = seconds => {
  const s = Math.max(0, Math.floor(seconds || 0)), d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
};
const fmtDate = seconds => new Date(seconds * 1000).toLocaleString([], {month:'short', day:'numeric', year:'numeric', hour:'2-digit', minute:'2-digit'});
const joinUrl = (base, params) => `${base}?${new URLSearchParams(params).toString()}`;

$('loginForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter || $('loginForm').querySelector('button');
  button.disabled = true; $('loginError').textContent = '';
  try {
    await api('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password:$('password').value,remember:$('rememberLogin').checked}) });
    $('password').value = '';
    await enterWorkspace();
  } catch (error) {
    $('loginError').textContent = error.message;
  } finally { button.disabled = false; }
});

async function enterWorkspace() {
  try {
    const [info, metrics] = await Promise.all([api('/api/info'), api('/api/metrics')]);
    state.info = info; state.volumes = info.volumes || [];
    $('login').hidden = true; $('app').hidden = false;
    initWorkspace(metrics);
  } catch (error) {
    $('login').hidden = false; $('app').hidden = true;
    $('loginError').textContent = error.message;
  }
}

function initWorkspace(initialMetrics) {
  const i = state.info;
  $('host').textContent = `${i.host} · ${i.os} ${i.release}`;
  $('serverAddress').textContent = location.host;
  $('miniUser').textContent = i.user;
  $('miniOS').textContent = `${i.os} · ${i.arch}`;
  $('hostNameLarge').textContent = i.host;
  $('hostSub').textContent = `${i.user}@${i.host} · ${i.os} ${i.release}`;
  $('terminalUser').textContent = i.user;
  $('terminalAccount').textContent = i.user;
  $('terminalCaption').textContent = `Interactive ${i.os} PTY · running as ${i.user}`;
  $('fileScopeText').textContent = `Starting in ${i.home}. Mounted volumes appear as separate devices.`;
  $('filePermissions').textContent = `OS permissions · ${i.user}`;
  $('settingsUser').textContent = i.user;
  $('settingsHost').textContent = i.host;
  $('settingsOS').textContent = `${i.os} ${i.release}`;
  $('shell').innerHTML = (i.shells || []).map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join('');
  $('shell').disabled = !(i.shells || []).length;
  $('info').innerHTML = [
    ['Operating system',`${i.os} ${i.release}`],['Architecture',i.arch],['Processors',`${i.cpu || '—'} logical / ${i.cpu_physical || '—'} physical`],
    ['System uptime',duration(i.uptime)],['Python runtime',i.python],['Home directory',i.home]
  ].map(([k,v]) => `<div class="detail-item"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
  renderMetrics(initialMetrics);
  renderVolumes(); renderLocations();
  loadFiles(i.home); loadMedia(i.home); loadServices();
  connectMetrics();
  activateTab('overview');
}

function renderMetrics(m) {
  $('cpu').textContent = `${Number(m.cpu).toFixed(1)}%`;
  $('ram').textContent = `${Number(m.ram).toFixed(1)}%`;
  $('disk').textContent = `${Number(m.disk).toFixed(1)}%`;
  [['cpuBar',m.cpu],['ramBar',m.ram],['diskBar',m.disk]].forEach(([id,value]) => $(id).style.width = `${Math.max(0,Math.min(100,Number(value)||0))}%`);
  $('ramMeta').textContent = `${bytes(m.ram_used)} / ${bytes(m.ram_total)}`;
  $('diskMeta').textContent = `${bytes(m.disk_used)} / ${bytes(m.disk_total)}`;
  $('memoryUsed').textContent = bytes(m.ram_used);
  $('swapUsed').textContent = `${Number(m.swap || 0).toFixed(1)}%`;
  if (state.previousNet) {
    const elapsed = Math.max(0.25, (m.time - state.previousNet.time) || 1);
    $('netDown').textContent = `↓ ${bytes(Math.max(0,m.net_recv-state.previousNet.recv)/elapsed)}/s`;
    $('netUp').textContent = `↑ ${bytes(Math.max(0,m.net_sent-state.previousNet.sent)/elapsed)}/s`;
  } else { $('netDown').textContent = `↓ ${bytes(0)}/s`; $('netUp').textContent = `↑ ${bytes(0)}/s`; }
  state.previousNet = {time:m.time, recv:m.net_recv, sent:m.net_sent};
  state.metricHistory.push(Number(m.cpu)||0); if (state.metricHistory.length > 50) state.metricHistory.shift();
  $('uptimeSummary').textContent = `Uptime ${duration(state.info?.uptime)}`;
  drawChart();
}
function connectMetrics() {
  if (state.metricsSocket) state.metricsSocket.close();
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws/metrics`);
  state.metricsSocket = socket;
  socket.onmessage = event => { try { renderMetrics(JSON.parse(event.data)); } catch {} };
  socket.onclose = () => { if (!$('app').hidden) setTimeout(() => { if (!$('app').hidden) connectMetrics(); }, 2600); };
}
function drawChart() {
  const canvas = $('chart'); if (!canvas || !canvas.getContext) return;
  const rect = canvas.getBoundingClientRect(); if (!rect.width) return;
  const ratio = window.devicePixelRatio || 1; const width = Math.round(rect.width * ratio); const height = Math.round(130 * ratio);
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  const ctx = canvas.getContext('2d'); ctx.clearRect(0,0,width,height);
  const styles = getComputedStyle(document.documentElement);
  const accent = styles.getPropertyValue('--accent').trim() || '#9a7cff';
  const faint = styles.getPropertyValue('--line').trim() || '#333';
  ctx.strokeStyle = faint; ctx.lineWidth = ratio; ctx.setLineDash([3*ratio,5*ratio]);
  for (let n=1;n<=3;n++){const y=height*n/4;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}
  ctx.setLineDash([]); const values = state.metricHistory; if (!values.length) return;
  const grad = ctx.createLinearGradient(0,0,width,0); grad.addColorStop(0,accent); grad.addColorStop(1,styles.getPropertyValue('--accent-2').trim() || '#55d6be');
  ctx.strokeStyle = grad; ctx.lineWidth = 2.5*ratio; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.beginPath();
  values.forEach((value,index) => { const x = values.length === 1 ? 0 : index/(Math.max(49,values.length-1))*width; const y = height - (value/100*height*.88) - height*.04; index ? ctx.lineTo(x,y) : ctx.moveTo(x,y); }); ctx.stroke();
  const last = values[values.length-1], x = values.length === 1 ? 0 : (values.length-1)/49*width, y = height-(last/100*height*.88)-height*.04;
  ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(x,y,3.5*ratio,0,Math.PI*2); ctx.fill();
}

function renderVolumes() {
  const volumes = state.volumes || [];
  $('driveCount').textContent = volumes.length;
  $('driveList').innerHTML = volumes.map(v => {
    const locked = !v.browseable;
    const driveType = v.mountpoint === state.info?.home ? 'HOME FOLDER' : locked ? 'SYSTEM · RESTRICTED' : v.removable ? 'REMOVABLE DRIVE' : 'MOUNTED VOLUME';
    const details = [v.filesystem || 'Storage', v.mountpoint].filter(Boolean).join(' · ');
    return `<button class="drive-item ${locked?'restricted':''} ${v.mountpoint===state.info?.home?'home-drive':''}" data-volume="${esc(v.mountpoint)}" data-browseable="${v.browseable}" title="${locked?'System volume is intentionally restricted':esc(v.mountpoint)}">
      <span class="drive-icon">${v.removable?'▣':v.mountpoint===state.info?.home?'⌂':'▤'}</span><span class="drive-meta"><b>${esc(v.label || v.mountpoint)}</b><small>${esc(driveType)} · ${esc(details)}</small><i class="drive-meter"><em style="width:${Math.max(0,Math.min(100,Number(v.percent)||0))}%"></em></i><small class="drive-capacity">${bytes(v.free)} free of ${bytes(v.total)}</small></span><span class="drive-lock">${locked?'⌑':'›'}</span></button>`;
  }).join('') || '<div class="fm-loading">No additional data drives are mounted</div>';
  $('driveList').querySelectorAll('[data-volume]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.browseable === 'true') loadFiles(button.dataset.volume, true);
    else toast('System root is restricted. Browse your home folder or another mounted data volume.', 'info');
  }));
  $('volumeSummary').innerHTML = volumes.slice(0,4).map(v => `<div class="volume-summary-item"><span class="volume-disc">▤</span><div class="volume-summary-info"><div><b>${esc(v.label)}</b><small>${esc(v.mountpoint)}</small></div><strong>${bytes(v.free)} free</strong></div><div class="volume-track"><i style="width:${Math.max(0,Math.min(100,Number(v.percent)||0))}%"></i></div><small class="volume-capacity">${bytes(v.used)} used of ${bytes(v.total)}</small></div>`).join('') || '<div class="fm-loading">No storage volumes found</div>';
}
function renderLocations() {
  const locations = (state.info.locations || []).filter(item => item.exists);
  const icons = {home:'⌂',desktop:'▧',documents:'▤',downloads:'⇩',pictures:'▧',music:'♫',videos:'▣'};
  $('locationList').innerHTML = locations.map(item => `<button class="fm-place" data-location="${esc(item.path)}"><span>${icons[item.icon] || '▤'}</span>${esc(item.name)}${item.name==='Home'?'<i>⌂</i>':''}</button>`).join('');
  $('locationList').querySelectorAll('[data-location]').forEach(button => button.addEventListener('click', () => loadFiles(button.dataset.location, true)));
}

async function loadFiles(path, remember = false) {
  if (!path) path = state.info?.home || '~';
  if (remember && state.currentPath && state.currentPath !== path) state.fileHistory.push(state.currentPath);
  $('fileRows').innerHTML = '<div class="fm-loading">Opening location…</div>';
  closeContext(); state.selectedPaths.clear(); state.lastSelectedPath=null; $('fileActions').disabled = true;
  try {
    const data = await api(joinUrl('/api/files',{path, hidden:state.showHidden}));
    state.currentPath = data.path; state.parentPath = data.parent; state.files = data.items;
    $('filePath').value = data.path; $('fileBreadcrumbs').textContent = data.path;
    $('fileStatus').textContent = `${data.items.length} items`; $('fileStatusFoot').textContent = `${data.items.length} items · ${data.path}`;
    renderFiles();
    document.querySelectorAll('.fm-place').forEach(button => button.classList.toggle('active', button.dataset.location === data.path));
  } catch (error) {
    $('fileRows').innerHTML = `<div class="file-error">${esc(error.message)}</div>`;
    $('fileStatus').textContent = 'Access unavailable'; $('fileStatusFoot').textContent = error.message;
  }
}
function fileKind(file) {
  if (file.directory) return 'folder';
  const ext = file.extension || '';
  if (['.png','.jpg','.jpeg','.webp','.gif','.avif','.svg','.heic'].includes(ext)) return 'image';
  if (['.mp3','.wav','.flac','.m4a','.ogg','.aac'].includes(ext)) return 'audio';
  if (['.mp4','.mkv','.mov','.webm','.avi','.m4v'].includes(ext)) return 'video';
  if (['.pdf'].includes(ext)) return 'pdf';
  if (['.zip','.7z','.tar','.gz','.rar'].includes(ext)) return 'archive';
  if (['.txt','.md','.log','.json','.yaml','.yml','.toml','.ini'].includes(ext)) return 'text';
  return 'file';
}
const fileIcon = kind => ({folder:'📁',image:'▧',audio:'♫',video:'▶',pdf:'▣',archive:'▧',text:'≡',file:'▤'}[kind] || '▤');
function selectedFiles() {
  return state.files.filter(file => state.selectedPaths.has(file.path));
}
function updateSelectionUI() {
  const selected = selectedFiles();
  document.querySelectorAll('#fileRows [data-path]').forEach(node => node.classList.toggle('selected', state.selectedPaths.has(node.dataset.path)));
  $('fileActions').disabled = selected.length === 0;
  $('fileStatus').textContent = selected.length ? `${selected.length} selected · ${state.files.length} items` : `${state.files.length} items`;
  $('fileStatusFoot').textContent = selected.length === 1
    ? `${selected[0].name} · ${selected[0].directory ? 'Folder' : bytes(selected[0].size)} · ${fmtDate(selected[0].modified)}`
    : selected.length ? `${selected.length} items selected` : `${state.files.length} items · ${state.currentPath || ''}`;
}
function sortedFiles() {
  const query = $('fileSearch').value.trim().toLowerCase();
  const visible = state.files.filter(file => file.name.toLowerCase().includes(query));
  const key = state.sortBy;
  visible.sort((a,b) => {
    if (a.directory !== b.directory) return a.directory ? -1 : 1;
    let cmp = 0;
    if (key === 'size') cmp = (a.size || 0) - (b.size || 0);
    else if (key === 'modified') cmp = (a.modified || 0) - (b.modified || 0);
    else if (key === 'kind') cmp = String(a.extension || (a.directory ? 'folder' : '')).localeCompare(String(b.extension || (b.directory ? 'folder' : '')));
    else cmp = a.name.localeCompare(b.name, undefined, {numeric:true,sensitivity:'base'});
    return cmp * state.sortDirection;
  });
  return visible;
}
function renderFiles() {
  const visible = sortedFiles();
  $('fileRows').className = `fm-file-grid ${state.view === 'list'?'list-view':'grid-view'}`;
  if (!visible.length) { $('fileRows').innerHTML = `<div class="file-empty"><span>⌕</span><b>${$('fileSearch').value.trim()?'No matching items':'This folder is empty'}</b><small>${$('fileSearch').value.trim()?'Try another search term':'Right-click here, create a folder or upload files to get started.'}</small></div>`; updateSelectionUI(); return; }
  if (state.view === 'list') {
    $('fileRows').innerHTML = `<div class="file-list-head"><span>Name</span><span>Kind</span><span>Size</span><span>Modified</span></div>` + visible.map(file => {
      const kind = fileKind(file);
      const thumb = !file.directory && ['image','video'].includes(kind)
        ? `<img class="file-thumbnail" loading="lazy" decoding="async" src="${joinUrl('/api/thumbnail',{path:file.path,v:`${file.modified}-${file.size}`})}" alt="" aria-hidden="true"><span class="list-thumb-fallback">${fileIcon(kind)}</span>`
        : '';
      return `<button class="file-list-row ${state.selectedPaths.has(file.path)?'selected':''}" data-path="${esc(file.path)}" data-dir="${file.directory}"><span class="file-cell-name"><i class="file-glyph ${kind} ${thumb?'has-list-thumbnail':''}">${thumb||fileIcon(kind)}</i><b>${esc(file.name)}</b></span><span>${kind==='folder'?'Folder':kind[0].toUpperCase()+kind.slice(1)}</span><span>${file.directory?'—':bytes(file.size)}</span><span>${fmtDate(file.modified)}</span></button>`;
    }).join('');
  } else {
    $('fileRows').innerHTML = visible.map(file => {
      const kind = fileKind(file);
      const thumb = !file.directory && ['image','video'].includes(kind)
        ? `<img class="file-thumbnail" loading="lazy" decoding="async" src="${joinUrl('/api/thumbnail',{path:file.path,v:`${file.modified}-${file.size}`})}" alt="" aria-hidden="true"><span class="thumbnail-fallback">${fileIcon(kind)}</span>`
        : `<span class="thumbnail-fallback">${fileIcon(kind)}</span>`;
      return `<button class="file-tile ${state.selectedPaths.has(file.path)?'selected':''}" data-path="${esc(file.path)}" data-dir="${file.directory}"><span class="tile-glyph ${kind} ${!file.directory&&['image','video'].includes(kind)?'has-thumbnail':''}">${thumb}</span><b title="${esc(file.name)}">${esc(file.name)}</b><small>${file.directory?'Folder':bytes(file.size)}</small></button>`;
    }).join('');
  }
  $('fileRows').querySelectorAll('.file-thumbnail').forEach(img => img.addEventListener('error', () => {
    img.closest('.tile-glyph, .file-glyph')?.classList.add('thumbnail-unavailable'); img.remove();
  }, {once:true}));
  updateSelectionUI();
}
function selectFile(path, {toggle=false, range=false}={}) {
  const visible = sortedFiles();
  if (range && state.lastSelectedPath) {
    const from = visible.findIndex(file => file.path === state.lastSelectedPath);
    const to = visible.findIndex(file => file.path === path);
    if (from >= 0 && to >= 0) {
      if (!toggle) state.selectedPaths.clear();
      visible.slice(Math.min(from,to),Math.max(from,to)+1).forEach(file => state.selectedPaths.add(file.path));
    } else state.selectedPaths = new Set([path]);
  } else if (toggle) {
    if (state.selectedPaths.has(path)) state.selectedPaths.delete(path); else state.selectedPaths.add(path);
    state.lastSelectedPath = path;
  } else {
    state.selectedPaths = new Set([path]); state.lastSelectedPath = path;
  }
  updateSelectionUI();
}
function clearSelection() { state.selectedPaths.clear(); state.lastSelectedPath=null; updateSelectionUI(); }
function openFile(path, isDirectory) {
  const item = state.files.find(file => file.path === path);
  if (isDirectory) { loadFiles(path, true); return; }
  if (item && ['audio','video','image'].includes(fileKind(item))) {
    document.querySelector('[data-tab="media"]').click(); loadMedia(state.currentPath).then(() => playMedia(path, fileKind(item)));
  } else window.open(joinUrl('/api/file',{path,preview:'true'}), '_blank', 'noopener');
}
let touchPressTimer=null, touchStartPoint=null, suppressTouchClick=false;
$('fileRows').addEventListener('pointerdown',event=>{
  if(event.pointerType!=='touch')return;
  const target=event.target.closest('[data-path]');if(!target)return;
  touchStartPoint={x:event.clientX,y:event.clientY,path:target.dataset.path};
  clearTimeout(touchPressTimer);
  touchPressTimer=setTimeout(()=>{
    const item=state.files.find(file=>file.path===touchStartPoint?.path);if(!item)return;
    if(!state.selectedPaths.has(item.path))selectFile(item.path);
    showContext(touchStartPoint.x,touchStartPoint.y,item);suppressTouchClick=true;
  },560);
});
['pointerup','pointercancel','pointerleave'].forEach(type=>$('fileRows').addEventListener(type,()=>{clearTimeout(touchPressTimer);touchPressTimer=null;touchStartPoint=null;}));
$('fileRows').addEventListener('pointermove',event=>{if(touchStartPoint&&Math.hypot(event.clientX-touchStartPoint.x,event.clientY-touchStartPoint.y)>12){clearTimeout(touchPressTimer);touchPressTimer=null;touchStartPoint=null;}});
$('fileRows').addEventListener('click', event => {
  const target = event.target.closest('[data-path]');
  if (!target) return;
  if(suppressTouchClick){suppressTouchClick=false;event.preventDefault();return;}
  selectFile(target.dataset.path,{toggle:event.ctrlKey||event.metaKey,range:event.shiftKey});
});
$('fileRows').addEventListener('dblclick', event => {
  const target = event.target.closest('[data-path]');
  if (target) openFile(target.dataset.path,target.dataset.dir==='true');
});
$('toggleHidden').addEventListener('click', () => { state.showHidden = !state.showHidden; $('toggleHidden').textContent = state.showHidden ? '· Hide hidden' : '· Hidden'; loadFiles(state.currentPath); });
$('fileActions').addEventListener('click', event => { const rect = event.currentTarget.getBoundingClientRect(); showContext(rect.left,rect.bottom+5,selectedFiles()[0]||null); });
$('fileSearch').addEventListener('input', renderFiles);
$('fileSort').addEventListener('change',()=>{state.sortBy=$('fileSort').value;renderFiles();});
$('sortDirection').addEventListener('click',()=>{state.sortDirection*=-1;$('sortDirection').textContent=state.sortDirection===1?'↑':'↓';renderFiles();});
$('gridView').addEventListener('click', () => { state.view='grid';$('gridView').classList.add('selected');$('listView').classList.remove('selected');renderFiles(); });
$('listView').addEventListener('click', () => { state.view='list';$('listView').classList.add('selected');$('gridView').classList.remove('selected');renderFiles(); });
$('upFiles').addEventListener('click', () => { if (state.parentPath) loadFiles(state.parentPath,true); });
$('fileBack').addEventListener('click', () => { const previous=state.fileHistory.pop(); if(previous)loadFiles(previous); else toast('No previous folder in this session.','info'); });
$('refreshFiles').addEventListener('click', () => loadFiles(state.currentPath));
$('refreshVolumes').addEventListener('click', refreshVolumes);
$('editPath').addEventListener('click', () => { const value=prompt('Enter a folder path. Only your home folder and mounted data volumes are accessible.',state.currentPath); if(value)loadFiles(value,true); });
$('newFolder').addEventListener('click', createFolderPrompt);
$('newFile').addEventListener('click', createFilePrompt);
$('uploadButton').addEventListener('click', () => $('uploadInput').click());
$('uploadInput').addEventListener('change', async event => { const files=[...event.target.files]; await uploadFiles(files); event.target.value=''; });
async function createFolderPrompt(){const name=prompt('New folder name:');if(!name)return;try{await api('/api/files/folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:state.currentPath,name})});toast('Folder created.');loadFiles(state.currentPath);}catch(e){toast(e.message,'error');}}
async function createFilePrompt(){const name=prompt('New file name (for example notes.txt):');if(!name)return;try{await api('/api/files/file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:state.currentPath,name,content:''})});toast('File created.');loadFiles(state.currentPath);}catch(e){toast(e.message,'error');}}
async function uploadFiles(files) {
  for (const file of files) {
    const data=new FormData(); data.append('file',file,file.name);
    try{await api(`/api/files/upload?path=${encodeURIComponent(state.currentPath)}`,{method:'POST',body:data});toast(`Uploaded ${file.name}`);}catch(e){toast(`${file.name}: ${e.message}`,'error');}
  }
  loadFiles(state.currentPath);
}
const dropZone=$('dropZone');
['dragenter','dragover'].forEach(type=>dropZone.addEventListener(type,event=>{event.preventDefault();$('dropHint').hidden=false;}));
['dragleave','drop'].forEach(type=>dropZone.addEventListener(type,event=>{event.preventDefault();$('dropHint').hidden=true;}));
dropZone.addEventListener('drop',event=>{const files=[...event.dataTransfer.files];if(files.length)uploadFiles(files);});

// Keep the menu in the top document layer and capture context-menu events before
// nested file tiles, previews, or drag/drop handlers can swallow them.
const contextMenu=$('contextMenu');
document.body.append(contextMenu);
function contextActions(item) {
  const count=state.selectedPaths.size;
  if(!item)return [['new-folder','New folder',''],['new-file','New file',''],['upload','Upload files',''],['refresh','Refresh',''],['select-all','Select all','']];
  if(count>1)return [['copy-path','Copy paths',''],['delete','Delete selected','danger-action']];
  const actions=[['open','Open','']];
  if(!item.directory)actions.push(['download','Download','']);
  actions.push(['copy-path','Copy path',''],['rename','Rename',''],['delete','Delete','danger-action']);
  return actions;
}
function showContext(x,y,item=null) {
  state.contextItem=item;
  contextMenu.innerHTML=contextActions(item).map(([action,label,cls])=>`<button type="button" role="menuitem" data-action="${action}" class="${cls}">${label}</button>`).join('');
  contextMenu.hidden=false;
  contextMenu.style.visibility='hidden';
  contextMenu.style.position='fixed';
  contextMenu.style.left='0px'; contextMenu.style.top='0px';
  // Measure after un-hiding, then flip toward the pointer if an edge is near.
  const rect=contextMenu.getBoundingClientRect();
  const margin=8;
  const width=document.documentElement.clientWidth || window.innerWidth;
  const height=window.innerHeight || document.documentElement.clientHeight;
  const px=Number.isFinite(Number(x)) ? Number(x) : margin;
  const py=Number.isFinite(Number(y)) ? Number(y) : margin;
  let left=px, top=py;
  if(left+rect.width>width-margin) left=px-rect.width;
  if(top+rect.height>height-margin) top=py-rect.height;
  left=Math.max(margin,Math.min(left,width-rect.width-margin));
  top=Math.max(margin,Math.min(top,height-rect.height-margin));
  contextMenu.style.left=`${left}px`; contextMenu.style.top=`${top}px`;
  contextMenu.style.visibility='visible';
  requestAnimationFrame(()=>contextMenu.querySelector('[role="menuitem"]')?.focus({preventScroll:true}));
}
// Capture phase makes right-click work on both file rows and the empty canvas.
document.addEventListener('contextmenu',event=>{
  const node=event.target instanceof Element ? event.target : event.target?.parentElement;
  const zone=node?.closest('#dropZone');
  if(!zone) return;
  event.preventDefault();
  event.stopPropagation();
  const target=node.closest('[data-path]');
  const item=target ? state.files.find(file=>file.path===target.dataset.path) : null;
  if(item && !state.selectedPaths.has(item.path)) selectFile(item.path);
  if(!target) clearSelection();
  showContext(event.clientX,event.clientY,item);
},true);
function closeContext(){contextMenu.hidden=true;state.contextItem=null;}
document.addEventListener('click',event=>{if(!event.target.closest('#contextMenu'))closeContext();});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&!contextMenu.hidden){closeContext();return;}
  if($('files').hidden)return;
  const tag=event.target?.tagName?.toLowerCase();
  if(['input','textarea','select'].includes(tag)||event.target?.isContentEditable)return;
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='a'){event.preventDefault();state.selectedPaths=new Set(sortedFiles().map(file=>file.path));updateSelectionUI();return;}
  if(event.key==='F2'&&state.selectedPaths.size===1){event.preventDefault();runContextAction('rename');return;}
  if(event.key==='Delete'&&state.selectedPaths.size){event.preventDefault();runContextAction('delete');return;}
  if(event.key==='Enter'&&state.selectedPaths.size===1){event.preventDefault();const item=selectedFiles()[0];openFile(item.path,item.directory);return;}
  if(event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10')){event.preventDefault();const node=document.querySelector('#fileRows [data-path].selected');const rect=node?.getBoundingClientRect();showContext(rect?rect.left:24,rect?rect.bottom:90,selectedFiles()[0]||null);}
  if((event.ctrlKey||event.metaKey)&&event.shiftKey&&event.key.toLowerCase()==='n'){event.preventDefault();createFolderPrompt();}
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='n'){event.preventDefault();createFilePrompt();}
});
contextMenu.addEventListener('click',event=>{const button=event.target.closest('[data-action]');if(button)runContextAction(button.dataset.action);});
async function runContextAction(action){
  const items=selectedFiles();const item=items[0];closeContext();
  if(action==='new-folder'){createFolderPrompt();return;}
  if(action==='new-file'){createFilePrompt();return;}
  if(action==='upload'){$('uploadInput').click();return;}
  if(action==='refresh'){loadFiles(state.currentPath);return;}
  if(action==='select-all'){state.selectedPaths=new Set(sortedFiles().map(file=>file.path));updateSelectionUI();return;}
  if(action==='open'&&item&&items.length===1){openFile(item.path,item.directory);return;}
  if(action==='download'&&item&&items.length===1&&!item.directory){window.location.href=joinUrl('/api/file',{path:item.path});return;}
  if(action==='copy-path'&&items.length){const text=items.map(entry=>entry.path).join('\n');try{await navigator.clipboard.writeText(text);toast(items.length>1?'Paths copied.':'Path copied.');}catch{prompt('Copy path:',text);}return;}
  if(action==='rename'&&item&&items.length===1){const name=prompt('Rename item:',item.name);if(!name||name===item.name)return;try{await api('/api/files/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:item.path,name})});toast('Item renamed.');loadFiles(state.currentPath);}catch(e){toast(e.message,'error');}return;}
  if(action==='delete'&&items.length){const message=items.length===1?`Delete “${items[0].name}”? Empty folders only; this cannot be undone.`:`Delete ${items.length} selected items? Empty folders only; this cannot be undone.`;if(!confirm(message))return;let removed=0;for(const entry of items){try{await api('/api/files/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:entry.path})});removed++;}catch(e){toast(`${entry.name}: ${e.message}`,'error');}}if(removed)toast(`Deleted ${removed} item${removed===1?'':'s'}.`);loadFiles(state.currentPath);}
}
async function refreshVolumes(){try{state.volumes=await api('/api/volumes');renderVolumes();toast('Mounted volumes refreshed.');}catch(e){toast(e.message,'error');}}

async function loadServices(){
  try{state.services=await api('/api/services');renderServices();}
  catch(error){$('serviceRows').innerHTML=`<tr><td colspan="3" class="empty-table">${esc(error.message)}</td></tr>`;}
}
function renderServices(){
  const query=$('serviceSearch').value.toLowerCase();const rows=state.services.filter(service=>`${service.name} ${service.display||''} ${service.status||''}`.toLowerCase().includes(query));
  $('serviceCount').textContent=`${rows.length} services`; $('serviceRows').innerHTML=rows.map(service=>`<tr><td><b>${esc(service.name)}</b></td><td><span class="service-state ${String(service.status).toLowerCase().includes('running')||String(service.status).toLowerCase()==='active'?'running':''}"><i></i>${esc(service.status||'unknown')}</span></td><td>${esc(service.display||'—')}</td></tr>`).join('')||'<tr><td colspan="3" class="empty-table">No matching services</td></tr>';
}
$('serviceSearch').addEventListener('input',renderServices);$('refreshServices').addEventListener('click',loadServices);

async function ensureTerminal() {
  if(state.terminal) return;
  if(state.terminalInitPromise) return state.terminalInitPromise;
  state.terminalInitPromise=(async()=>{
    if(!window.Terminal||!window.FitAddon){$('terminalMissing').hidden=false;throw new Error('Terminal emulator assets did not load.');}
    $('terminalMissing').hidden=true;
    const glyphSample='';
    let fontPromise=Promise.resolve([]);
    try {
      fontPromise=Promise.all([
        document.fonts.load('14px "MesloLGS NF"',glyphSample),
        document.fonts.load('bold 14px "MesloLGS NF"',glyphSample)
      ]).then(groups=>groups.flat());
    } catch {}
    // Wait for the webfont before xterm measures cells. A timeout still allows
    // fallback fonts to work, while a late font load triggers a full relayout.
    let timer;
    const loaded=await Promise.race([fontPromise.then(faces=>({faces})).catch(()=>({faces:[]})),new Promise(resolve=>{timer=setTimeout(()=>resolve({faces:[]}),2800);})]);
    clearTimeout(timer);
    state.terminalFontLoaded=loaded.faces.length>0;
    $('terminalFontStatus').textContent=state.terminalFontLoaded?'MESLO NF LOADED':'FONT FALLBACK';
    if(!state.terminalFontLoaded){
      fontPromise.then(faces=>{
        if(!faces.length) return;
        state.terminalFontLoaded=true;
        $('terminalFontStatus').textContent='MESLO NF LOADED';
        if(state.terminal){state.terminal.options.fontFamily='"MesloLGS NF", "CaskaydiaCove Nerd Font", "FiraCode Nerd Font", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';state.terminal.refresh(0,Math.max(0,state.terminal.rows-1));try{state.fitAddon.fit();}catch{}}
      }).catch(()=>{});
    }
    state.terminal=new window.Terminal({
      cursorBlink:true, cursorStyle:'block', convertEol:false, scrollback:8000,
      allowTransparency:true, macOptionIsMeta:true, scrollOnUserInput:true,
      fontFamily:'"MesloLGS NF", "CaskaydiaCove Nerd Font", "FiraCode Nerd Font", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize:14, lineHeight:1.22, letterSpacing:0,
      theme:{background:'#10131d',foreground:'#e5e7ef',cursor:'#91e5ce',selectionBackground:'#5a547a88',black:'#242735',red:'#ff7189',green:'#91e5ce',yellow:'#efc67d',blue:'#8ca8ff',magenta:'#c4a3ff',cyan:'#7edbd2',white:'#e5e7ef'}
    });
    state.fitAddon=new window.FitAddon.FitAddon();
    state.terminal.loadAddon(state.fitAddon);
    state.terminal.open($('terminalHost'));
    state.terminal.onData(data=>{if(state.terminalSocket?.readyState===WebSocket.OPEN)state.terminalSocket.send(data);});
    state.terminal.onResize(size=>{if(state.terminalSocket?.readyState===WebSocket.OPEN)state.terminalSocket.send(JSON.stringify({type:'resize',cols:size.cols,rows:size.rows}));});
    requestAnimationFrame(()=>{try{state.fitAddon.fit();}catch{}});
  })();
  return state.terminalInitPromise;
}
async function connectTerminal(){
  try { await ensureTerminal(); }
  catch(error){ $('terminalMissing').hidden=false; $('terminalMissing').textContent=error.message; return; }
  if(state.terminalSocket&&state.terminalSocket.readyState<=WebSocket.OPEN)return;
  const shell=$('shell').value;
  if(!shell){state.terminal.write('\r\nNo supported shell was detected for this OS account.\r\n');return;}
  const protocol=location.protocol==='https:'?'wss:':'ws:';
  const socket=new WebSocket(`${protocol}//${location.host}/ws/terminal?shell=${encodeURIComponent(shell)}`);socket.binaryType='arraybuffer';state.terminalSocket=socket;
  $('terminalState').textContent='CONNECTING';$('terminalState').classList.remove('connected');$('terminalShellName').textContent=shell;
  socket.onopen=()=>{state.terminalConnected=true;$('terminalState').textContent='CONNECTED';$('terminalState').classList.add('connected');state.terminal.write(`\r\n\x1b[38;2;143;226;204m● Connected as ${state.info.user} · ${state.info.os}\x1b[0m\r\n`);try{state.fitAddon.fit();}catch{};};
  socket.onmessage=event=>{if(event.data instanceof ArrayBuffer)state.terminal.write(new Uint8Array(event.data));else state.terminal.write(event.data);};
  socket.onerror=()=>{state.terminal.write('\r\n\x1b[31mTerminal connection error. Check server logs and your session.\x1b[0m\r\n');};
  socket.onclose=()=>{state.terminalConnected=false;$('terminalState').textContent='DISCONNECTED';$('terminalState').classList.remove('connected');};
}
function stopTerminal(){if(state.terminalSocket){state.terminalSocket.close();state.terminalSocket=null;}state.terminalConnected=false;}
$('shell').addEventListener('change',()=>{stopTerminal();connectTerminal();});
$('restartTerminal').addEventListener('click',()=>{stopTerminal();if(state.terminal)state.terminal.write('\r\n\x1b[90m--- Starting a new shell session ---\x1b[0m\r\n');connectTerminal();});
$('clearTerminal').addEventListener('click',()=>state.terminal?.clear());
window.addEventListener('resize',()=>{if(state.terminal&&$('terminal').hidden===false){try{state.fitAddon.fit();}catch{}}drawChart();});
if('ResizeObserver' in window){
  const terminalResizeObserver=new ResizeObserver(()=>{if(state.terminal&&$('terminal').hidden===false){try{state.fitAddon.fit();}catch{}}});
  terminalResizeObserver.observe($('terminalHost'));
  const chartResizeObserver=new ResizeObserver(()=>drawChart());
  chartResizeObserver.observe($('chart').parentElement);
}

async function loadMedia(path){
  state.mediaPath=path||state.info.home;
  try{const data=await api(joinUrl('/api/media',{path:state.mediaPath}));state.mediaPath=data.path;state.mediaParent=data.parent;$('mediaPath').value=data.path;
    $('mediaRows').innerHTML=data.items.map(item=>`<button class="media-row" data-path="${esc(item.path)}" data-dir="${item.directory}" data-kind="${item.kind}"><span class="media-row-icon">${item.directory?'📁':item.kind==='audio'?'♫':item.kind==='video'?'▶':'▧'}</span><span class="media-row-name"><b>${esc(item.name)}</b><small>${item.directory?'Folder':item.kind}</small></span><small>${item.directory?'':bytes(item.size)}</small></button>`).join('')||'<div class="file-empty"><span>♫</span><b>No media files in this folder</b><small>Try another folder or a mounted volume.</small></div>';
    $('mediaRows').querySelectorAll('[data-path]').forEach(button=>button.addEventListener('dblclick',()=>button.dataset.dir==='true'?loadMedia(button.dataset.path):playMedia(button.dataset.path,button.dataset.kind)));
    $('mediaRows').querySelectorAll('[data-path]').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('.media-row').forEach(row=>row.classList.toggle('selected',row===button));if(button.dataset.dir==='true')loadMedia(button.dataset.path);}));
  }catch(error){$('mediaRows').innerHTML=`<div class="file-error">${esc(error.message)}</div>`;}
}
function playMedia(path,kind){
  const url=joinUrl('/api/media/stream',{path});$('playerTitle').textContent=path.split(/[\\/]/).pop();$('audioPlayer').hidden=kind!=='audio';$('videoPlayer').hidden=kind!=='video';$('mediaStage').innerHTML='';
  $('audioPlayer').pause();$('videoPlayer').pause();$('audioPlayer').removeAttribute('src');$('videoPlayer').removeAttribute('src');
  if(kind==='audio'){$('audioPlayer').src=url;$('audioPlayer').load();$('audioPlayer').play().catch(()=>{});$('mediaStage').innerHTML='<div class="media-placeholder"><span>♫</span><b>Audio is ready to play</b><small>Use the player controls below.</small></div>';}
  else if(kind==='video'){$('videoPlayer').src=url;$('videoPlayer').load();$('videoPlayer').play().catch(()=>{});}
  else if(kind==='image'){const img=document.createElement('img');img.src=url;img.alt='Image preview';img.className='media-preview-image';$('mediaStage').append(img);}
}
$('mediaGo').addEventListener('click',()=>loadMedia($('mediaPath').value));
$('mediaPath').addEventListener('keydown',event=>{if(event.key==='Enter')loadMedia($('mediaPath').value);});
$('mediaUp').addEventListener('click',()=>{if(state.mediaParent)loadMedia(state.mediaParent);});

function activateTab(name){
  closeContext();
  document.querySelectorAll('.tab-page').forEach(page=>page.hidden=page.id!==name);
  document.querySelectorAll('.nav-item').forEach(button=>button.classList.toggle('active',button.dataset.tab===name));
  if(name==='terminal')connectTerminal(); if(name==='files'&& !state.currentPath)loadFiles(state.info.home); if(name==='media'&& !state.mediaPath)loadMedia(state.info.home);
  if(name==='overview')requestAnimationFrame(drawChart);
}
document.querySelectorAll('.nav-item').forEach(button=>button.addEventListener('click',()=>activateTab(button.dataset.tab)));
document.querySelectorAll('[data-goto]').forEach(button=>button.addEventListener('click',()=>activateTab(button.dataset.goto)));
$('logout').addEventListener('click',async()=>{
  stopTerminal();if(state.metricsSocket)state.metricsSocket.close();
  try{await api('/api/auth/logout',{method:'POST'});}catch{}
  $('app').hidden=true;$('login').hidden=false;$('password').value='';$('loginError').textContent='';state.info=null;state.currentPath=null;
});

$('passwordChangeForm').addEventListener('submit',async event=>{
  event.preventDefault();
  const current=$('currentPassword').value;
  const next=$('newPassword').value;
  const message=$('passwordChangeMessage');
  message.textContent='';
  if(next.length<12){message.textContent='Use at least 12 characters.';return;}
  const button=event.submitter || $('passwordChangeForm').querySelector('button');
  button.disabled=true;
  try{
    await api('/api/auth/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({current_password:current,new_password:next})});
    $('currentPassword').value='';$('newPassword').value='';
    message.textContent='Password updated. Other signed-in sessions have been invalidated.';
    toast('Administrator password updated.');
  }catch(error){message.textContent=error.message;}
  finally{button.disabled=false;}
});
$('settingsOpen').addEventListener('click',()=>$('settings').hidden=false);
function closeSettings(){$('settings').hidden=true;}
$('settingsClose').addEventListener('click',closeSettings);$('settingsDone').addEventListener('click',closeSettings);
$('settings').addEventListener('click',event=>{if(event.target===$('settings'))closeSettings();});
function applyTheme(value){document.documentElement.dataset.theme=value;localStorage.setItem('hostpanel-theme',value);drawChart();}
let savedTheme='system';try{savedTheme=localStorage.getItem('hostpanel-theme')||'system';}catch{}
$('themeSelect').value=savedTheme;applyTheme(savedTheme);
$('themeSelect').addEventListener('change',()=>applyTheme($('themeSelect').value));
let savedDensity='comfortable';try{savedDensity=localStorage.getItem('hostpanel-density')||'comfortable';}catch{}
if($('densitySelect'))$('densitySelect').value=savedDensity;
function applyDensity(value){document.documentElement.dataset.density=value;try{localStorage.setItem('hostpanel-density',value);}catch{}}
applyDensity(savedDensity);
$('densitySelect')?.addEventListener('change',()=>applyDensity($('densitySelect').value));
$('themeQuick').addEventListener('click',()=>{const current=document.documentElement.dataset.theme;const next=current==='light'?'dark':current==='dark'?'light':(matchMedia('(prefers-color-scheme: dark)').matches?'light':'dark');$('themeSelect').value=next;applyTheme(next);});
let motion=true;try{motion=localStorage.getItem('hostpanel-motion')!=='off';}catch{}
$('motionToggle').checked=motion;document.documentElement.classList.toggle('reduce-motion',!motion);
$('motionToggle').addEventListener('change',()=>{motion=$('motionToggle').checked;document.documentElement.classList.toggle('reduce-motion',!motion);try{localStorage.setItem('hostpanel-motion',motion?'on':'off');}catch{}});
function toast(message,type='success'){const node=document.createElement('div');node.className=`toast ${type}`;node.textContent=message;$('toastStack').append(node);setTimeout(()=>node.classList.add('visible'),10);setTimeout(()=>{node.classList.remove('visible');setTimeout(()=>node.remove(),250);},3200);}

// A signed HttpOnly cookie keeps credentials out of localStorage, JS and WebSocket URLs.
(async()=>{
  try{await api('/api/auth/session');await enterWorkspace();}catch{ /* normal signed-out state */ }
})();
