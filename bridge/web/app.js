// Lightweight client script matching the current HTML structure in index.html
(function(){
  const API = window.API_BASE || '';
  const qs = (s,root=document)=>root.querySelector(s);
  const qsa = (s,root=document)=>Array.from(root.querySelectorAll(s));

  // Top tabs behavior
  qsa('.top-tabs .tab').forEach(t=>t.addEventListener('click', ()=>{
    qsa('.top-tabs .tab').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
  }));

  // Sidebar selection
  qsa('.sidebar .item').forEach(it=>it.addEventListener('click', ()=>{
    qsa('.sidebar .item').forEach(x=>x.classList.remove('selected'));
    it.classList.add('selected');
  }));

  // +Add
  const addBtn = qs('.btn.add');
  if(addBtn) addBtn.addEventListener('click', ()=>{
    // placeholder - hook into API or open a modal later
    alert('Action: + Add (placeholder)');
  });

  // Populate data table from /spools
  async function loadSpools(){
    const tbody = qs('.data-table tbody');
    if(!tbody) return;
    try{
      const res = await fetch((API?API:'') + '/spools');
      if(!res.ok) throw new Error('status ' + res.status);
      const files = await res.json();
      if(!Array.isArray(files) || files.length===0){
        tbody.innerHTML = `<tr><td colspan="5" style="color:#777;padding:16px">No spools</td></tr>`;
        return;
      }
  const rows = files.map(f=>{
        // f can be a string or an object with keys like 'file-name', 'job-id', size
        let name = '', size = '--', jobname = '', jobid = '';
        if(typeof f === 'string'){
          name = f;
        }else if(f && typeof f === 'object'){
          name = f['file-name'] || f.fileName || f.name || f['file_name'] || JSON.stringify(f);
          size = (typeof f.size === 'number') ? niceSize(f.size) : (f.size || '--');
          jobname = f['job-name'] || f.job_name || f.jobName || '';
          jobid = f['job-id'] || f.job_id || f.jobId || '';
          jobrc = f['job-rc'] || f.job_rc || f.jobRc || '';
        }else{
          name = String(f);
        }
        return `<tr><td>${escapeHtml(jobname)}</td><td>${escapeHtml(jobid)}</td><td>${escapeHtml(jobrc)}</td><td>${escapeHtml(size)}</td><td><button class="btn small view-btn" data-file="${escapeHtml(name)}" data-jobname="${escapeHtml(jobname)}" data-jobid="${escapeHtml(jobid)}">View</button></td></tr>`;
      }).join('');
      tbody.innerHTML = rows;
      // wire view buttons
      Array.from(tbody.querySelectorAll('.view-btn')).forEach(b=>b.addEventListener('click', ()=>showSpool(b.dataset.file, b.dataset.jobname, b.dataset.jobid)));
    }catch(err){
      console.warn('loadSpools error', err);
      // keep the example row already in the HTML or render fallback
      // (no-op)
    }
  }

  function niceSize(n){
    if(typeof n !== 'number' || Number.isNaN(n)) return '--';
    if(n < 1024) return n + ' B';
    if(n < 1024*1024) return (n/1024).toFixed(1) + ' KB';
    return (n/(1024*1024)).toFixed(2) + ' MB';
  }

  function escapeHtml(s){ return String(s).replace(/[&<>"'`]/g,ch=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','`':'&#96;'
  }[ch])); }

  // Minimal SSE starter (call from console or add UI later)
  let es=null;
  window.startConsoleStream = function(){
    if(es) return;
    try{
      es = new EventSource((API?API:'') + '/stream/watch');
      es.onmessage = e=>{
        const box = qs('.main');
        const pre = document.createElement('pre'); pre.textContent = e.data; pre.style.background='#071226'; pre.style.color='#bfe7ff'; pre.style.padding='6px'; pre.style.margin='6px 0';
        box.appendChild(pre);
        box.scrollTop = box.scrollHeight;
      };
      es.onerror = ()=>{ es.close(); es=null; };
    }catch(e){ console.warn('SSE not available', e); }
  };
  window.stopConsoleStream = function(){ if(es){ es.close(); es=null; } };

  // Auto-refresh table every 10s
  loadSpools();
  setInterval(loadSpools, 10000);
  
  /* Modal logic */
  const modal = document.getElementById('modal');
  const modalTitle = document.getElementById('modal-title');
  const modalBody = document.getElementById('modal-body');
  const modalDownload = document.getElementById('modal-download');
  function openModal(){ modal.setAttribute('aria-hidden','false'); }
  function closeModal(){ modal.setAttribute('aria-hidden','true'); modalBody.textContent = ''; modalTitle.textContent = ''; modalDownload.href = '#'; }
  modal.querySelectorAll('[data-close]').forEach(el=>el.addEventListener('click', closeModal));
  const mclose = modal.querySelector('.modal-close'); if(mclose) mclose.addEventListener('click', closeModal);

  async function showSpool(name, jobname, jobid){
    // Title prefers job-name and job-id when available
    if(jobname){
      modalTitle.textContent = jobname + (jobid ? ` (${jobid})` : '');
    }else{
      modalTitle.textContent = name;
    }
    modalBody.textContent = 'Loading...';
    modalDownload.href = '#';
    openModal();
    try{
      const url = (API?API:'') + '/spools/' + encodeURIComponent(name);
      const res = await fetch(url);
      if(!res.ok) throw new Error('status '+res.status);
      const blob = await res.blob();
      const maxPreview = 200000; // 200 KB
      if(blob.size > maxPreview){
        modalBody.textContent = `File too large to preview (${blob.size} bytes)`;
        const objectUrl = URL.createObjectURL(blob);
        modalDownload.href = objectUrl; modalDownload.download = name;
        return;
      }
      const text = await blob.text();
      modalBody.textContent = text;
      const objectUrl = URL.createObjectURL(blob);
      modalDownload.href = objectUrl; modalDownload.download = name;
    }catch(err){
      modalBody.textContent = 'Failed to load: ' + err;
    }
  }

})();
