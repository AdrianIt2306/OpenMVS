// Simple client for the OpenMVS Bridge API
const qs = (s) => document.querySelector(s);
const apiUrlInput = qs('#apiUrl');

function base() { return apiUrlInput.value.trim().replace(/\/$/, ''); }

async function fetchJson(path){
  const res = await fetch(base() + path);
  if(!res.ok) throw new Error(await res.text());
  return res.json();
}

function formatSize(n){
  if(n < 1024) return n + ' B';
  if(n < 1024*1024) return (n/1024).toFixed(1) + ' KB';
  return (n/(1024*1024)).toFixed(2) + ' MB';
}

async function listSpools(){
  const name = qs('#spoolJobName').value.trim();
  const id = qs('#spoolJobId').value.trim();
  const params = new URLSearchParams();
  if(name) params.set('job_name', name);
  if(id) params.set('job_id', id);
  const items = await fetchJson('/spools' + (params.toString() ? ('?' + params.toString()) : ''))
    .catch(e=>{alert('Error: '+e);return []});
  const list = qs('#spoolsList'); list.innerHTML = '';
  items.forEach(it=>{
    const div = document.createElement('div'); div.className='item';
    const left = document.createElement('div');
    left.innerHTML = `<strong>${it['job-name']}</strong><div class="meta">${it['job-id']} • ${formatSize(it.size)}</div>`;
    const right = document.createElement('div');
    const viewBtn = document.createElement('button'); viewBtn.textContent = 'View'; viewBtn.className='btn'; viewBtn.style.marginLeft='8px';
    viewBtn.onclick = () => showSpool(it['file-name']);
    right.appendChild(viewBtn);
    div.appendChild(left); div.appendChild(right);
    list.appendChild(div);
  });
}

async function showSpool(name){
  const url = base() + '/spools/' + encodeURIComponent(name);
  // attempt to fetch as text first
  try{
    const res = await fetch(url);
    if(!res.ok) throw new Error('status '+res.status);
    const blob = await res.blob();
    // if it's small, try to decode as text
    const maxPreview = 200000; // 200 KB
    const d = qs('#spoolDetail');
    d.innerHTML = `<h3>${name}</h3><div class="meta">${formatSize(blob.size)}</div>`;
    if(blob.size > maxPreview){
      d.innerHTML += `<div class="meta">File too large to preview (${formatSize(blob.size)}). <a href="${url}">Download</a></div>`;
      return;
    }
    const text = await blob.text();
    // show in a preformatted box
    d.innerHTML += `<pre class="logbox">${escapeHtml(text)}</pre>`;
  }catch(err){
    const d = qs('#spoolDetail');
    d.innerHTML = `<h3>${name}</h3><div class="meta">Failed to load spool: ${err}</div><div><a href="${base() + '/spools/' + encodeURIComponent(name)}">Download raw</a></div>`;
  }
}

async function listJoblogs(){
  const name = qs('#joblogJobName').value.trim();
  const id = qs('#joblogJobId').value.trim();
  const params = new URLSearchParams();
  if(name) params.set('job_name', name);
  if(id) params.set('job_id', id);
  const items = await fetchJson('/joblogs' + (params.toString() ? ('?' + params.toString()) : ''))
    .catch(e=>{alert('Error: '+e);return []});
  const list = qs('#joblogsList'); list.innerHTML = '';
  items.forEach(it=>{
    const div = document.createElement('div'); div.className='item';
    const left = document.createElement('div');
    left.innerHTML = `<strong>${it['file-name']}</strong><div class="meta">${it['job-id']||''} ${it['job-name']?('- '+it['job-name']):''} • ${formatSize(it.size)}</div>`;
    const right = document.createElement('div');
    const view = document.createElement('button'); view.textContent='Ver'; view.onclick=()=>showJoblog(it['file-name']);
    right.appendChild(view);
    div.appendChild(left); div.appendChild(right);
    list.appendChild(div);
  });
}

async function showJoblog(name){
  const p = base() + '/joblogs/' + encodeURIComponent(name);
  const meta = await fetchJson('/joblogs/' + encodeURIComponent(name) + '/meta').catch(e=>({error:e}));
  const content = await fetch(p).then(r=>r.text()).catch(e=>"(error loading)");
  const d = qs('#joblogDetail');
  d.innerHTML = `<h3>${name}</h3><div class="meta">${meta.job_id||''} ${meta.job_name?('- '+meta.job_name):''}</div><pre class="logbox">${escapeHtml(content)}</pre>`;
}

function escapeHtml(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function loadLog(name, lines=200){
  const res = await fetchJson('/logs/' + encodeURIComponent(name) + '?lines=' + lines).catch(e=>({content:'(error)'}));
  qs('#logContent').textContent = res.content || '(no content)';
}

async function listPids(){
  const arr = await fetchJson('/pids').catch(e=>[]);
  alert(JSON.stringify(arr, null, 2));
}

async function checkReady(){
  const r = await fetchJson('/ready').catch(e=>({ready:false}));
  alert(JSON.stringify(r, null, 2));
}

async function rawSearch(){
  const q = qs('#rawQuery').value.trim();
  if(!q) return alert('empty query');
  const res = await fetchJson('/raw/search?q=' + encodeURIComponent(q)).catch(e=>({error:e}));
  qs('#rawResult').textContent = JSON.stringify(res);
}

let evtSource = null;
function startSSE(){
  if(evtSource) return;
  evtSource = new EventSource(base() + '/stream/watch');
  const box = qs('#streamBox');
  evtSource.onmessage = (e)=>{
    const line = e.data || '';
    const el = document.createElement('div'); el.textContent = line; box.appendChild(el); box.scrollTop = box.scrollHeight;
  };
  evtSource.onerror = (err)=>{
    const el = document.createElement('div'); el.textContent = '(SSE error)'; box.appendChild(el);
  };
}
function stopSSE(){ if(evtSource){ evtSource.close(); evtSource=null; } }

// Auto-refresh support
let autoRefreshInterval = null;
function setAutoRefresh(enabled){
  clearInterval(autoRefreshInterval);
  if(enabled){
    autoRefreshInterval = setInterval(()=>{
      listSpools().catch(()=>{});
      listJoblogs().catch(()=>{});
      const el = document.getElementById('lastUpdated');
      if(el) el.textContent = new Date().toLocaleTimeString();
    }, 5000);
  }
}

// wire up auto-refresh toggle (if present)
const autoToggle = document.getElementById('autoRefreshToggle');
if(autoToggle){
  autoToggle.addEventListener('change', ()=> setAutoRefresh(autoToggle.checked));
  // start enabled by default when checkbox checked
  setAutoRefresh(autoToggle.checked);
}

// Tab switching
document.querySelectorAll('.tab-button').forEach(btn=>{
  btn.addEventListener('click', ()=>{
    document.querySelectorAll('.tab-button').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    const target = btn.dataset.tab;
    document.querySelectorAll('.tab-content').forEach(tc=>tc.style.display='none');
    const el = document.getElementById('tab-'+target);
    if(el) el.style.display='block';
  });
});

// wire up buttons
qs('#btnRefresh').onclick = ()=>{ listSpools(); listJoblogs(); };
qs('#btnListSpools').onclick = listSpools;
qs('#btnListJoblogs').onclick = listJoblogs;
qs('#btnListSpools').addEventListener('click', ()=>qs('#spoolJobName').focus());
qs('#btnListJoblogs').addEventListener('click', ()=>qs('#joblogJobName').focus());
document.querySelectorAll('.btnLog').forEach(b=>b.addEventListener('click', ()=>loadLog(b.dataset.log)) );
qs('#btnPids').onclick = listPids;
qs('#btnReady').onclick = checkReady;
qs('#btnRawSearch').onclick = rawSearch;
qs('#btnStartStream').onclick = startSSE;
qs('#btnStopStream').onclick = stopSSE;

// initial load
listSpools().catch(()=>{});
listJoblogs().catch(()=>{});

// ensure default tab visible
const defaultTab = document.querySelector('.tab-button.active');
if(defaultTab){ defaultTab.click(); }
