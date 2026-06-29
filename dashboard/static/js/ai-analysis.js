// ── AI call analysis (completed calls auto-analyze in the background; this
//    page reads results and can re-run on demand) ──────────────────────────
let aaPage=1,aaPages=1,_aaSelected=new Set(),_aaRows=[],_aaCurrentFolder=null;

function renderAaRows(recs){
  const tb=document.getElementById('aa-table');
  const sum=document.getElementById('aa-summary');
  if(sum){const done=recs.filter(r=>r.analyzed).length;sum.textContent=recs.length?`${done}/${recs.length} analyzed on this page`:'';}
  if(!recs.length){tb.innerHTML='<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--text-d)">No completed calls found</td></tr>';return}
  tb.innerHTML=recs.map(r=>{
    const ph=r.caller&&r.caller.length>4?'+'+r.caller.replace(/\D/g,''):r.caller||'Unknown';
    const checked=_aaSelected.has(r.folder)?'checked':'';
    const status=r.analyzed
      ?'<span class="badge bg">✓ Analyzed</span>'
      :'<span class="badge bz">⏳ Pending…</span>';
    return`<tr>
      <td><input type="checkbox" data-folder="${esc(r.folder)}" ${checked} onchange="toggleAnalysisSelect(this)"></td>
      <td><a href="#" onclick="viewAnalysis('${esc(r.folder)}');return false">${esc(ph)}</a></td>
      <td style="font-size:12px">${fmtTs(r.folder_dt)}</td>
      <td>${fmtDur(r.duration_seconds)}</td>
      <td>${status}</td>
      <td style="text-align:right"><button class="btn btn-gh btn-sm" onclick="viewAnalysis('${esc(r.folder)}')">${r.analyzed?'View →':'Analyze'}</button></td>
    </tr>`;
  }).join('');
}

async function loadAiAnalysisList(page){
  page=page||1;aaPage=page;
  const search=document.getElementById('aa-search')?.value||'';
  const tb=document.getElementById('aa-table');
  tb.innerHTML='<tr><td colspan="6" style="text-align:center;padding:24px"><div class="spin"></div></td></tr>';
  try{
    const d=await api(`/api/analysis/eligible?page=${page}&limit=50&search=${encodeURIComponent(search)}`);
    aaPages=d.pages||1;
    _aaRows=d.recordings||[];
    renderAaRows(_aaRows);
    document.getElementById('aa-pages').innerHTML=
      `<button class="pgbtn" onclick="loadAiAnalysisList(${page-1})" ${page<=1?'disabled':''}>← Prev</button>
       <span class="pginf">Page ${page} of ${aaPages}</span>
       <button class="pgbtn" onclick="loadAiAnalysisList(${page+1})" ${page>=aaPages?'disabled':''}>Next →</button>`;
  }catch(e){tb.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--error);padding:24px">Error</td></tr>'}
}

function toggleAnalysisSelect(cb){
  if(cb.checked)_aaSelected.add(cb.dataset.folder);else _aaSelected.delete(cb.dataset.folder);
  updateAaSelectedCount();
}
function toggleSelectAllAnalysis(master){
  document.querySelectorAll('#aa-table input[type=checkbox]').forEach(cb=>{
    cb.checked=master.checked;
    if(master.checked)_aaSelected.add(cb.dataset.folder);else _aaSelected.delete(cb.dataset.folder);
  });
  updateAaSelectedCount();
}
function updateAaSelectedCount(){
  // btn.textContent='Analyzing…' removes the span from the DOM while the
  // analysis is in-flight; guard against null rather than throwing.
  const el=document.getElementById('aa-selected-count');
  if(el)el.textContent=_aaSelected.size;
  const btn=document.getElementById('aa-analyze-btn');
  if(btn)btn.disabled=_aaSelected.size===0;
}

async function runSelectedAnalysis(){
  const folders=[..._aaSelected];
  if(!folders.length)return;
  const btn=document.getElementById('aa-analyze-btn');
  btn.disabled=true;btn.textContent='Analyzing…';
  try{
    const resp=await fetch('/api/analysis/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folders})});
    if(!resp.ok){const t=await resp.text();throw new Error(t||resp.statusText);}
    const d=await resp.json();
    const okFolders=Object.entries(d.results||{}).filter(([,r])=>r.ok).map(([f])=>f);
    if(okFolders.length){
      const prev=JSON.parse(sessionStorage.getItem('caSelectedFolders')||'[]');
      const merged=[...new Set([...prev,...okFolders])];
      sessionStorage.setItem('caSelectedFolders',JSON.stringify(merged));
    }
    _aaSelected.clear();
    await loadAiAnalysisList(aaPage);
    const firstOk=Object.entries(d.results||{}).find(([,r])=>r.ok);
    if(firstOk)renderAnalysisResult(firstOk[0],firstOk[1].analysis);
    pushNotification(`Analyzed ${okFolders.length}/${folders.length} call(s)`,'info');
  }catch(e){alert('Analysis failed: '+e.message)}
  finally{
    btn.innerHTML='🧠 Analyze Selected (<span id="aa-selected-count">0</span>)';
    updateAaSelectedCount();
  }
}

async function viewAnalysis(folder){
  try{
    const d=await api('/api/calls/recordings/'+encodeURIComponent(folder)+'/analysis');
    if(!d.analyzed){
      if(confirm('This call has not been analyzed yet. Analyze it now?')){
        const r=await (await fetch('/api/analysis/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folders:[folder]})})).json();
        const res=r.results?.[folder];
        if(res?.ok){renderAnalysisResult(folder,res.analysis);loadAiAnalysisList(aaPage)}
        else alert('Analysis failed: '+(res?.error||'unknown error'));
      }
      return;
    }
    renderAnalysisResult(folder,d.analysis);
  }catch(e){alert('Failed to load analysis')}
}

function _aaListSec(title,arr){
  if(!arr||!arr.length)return `<div class="aa-sec"><h4>${esc(title)}</h4><p class="dim">None noted</p></div>`;
  return `<div class="aa-sec"><h4>${esc(title)}</h4><ul>${arr.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`;
}
function _aaChipSec(title,arr){
  if(!arr||!arr.length)return `<div class="aa-sec"><h4>${esc(title)}</h4><p class="dim">None noted</p></div>`;
  return `<div class="aa-sec"><h4>${esc(title)}</h4><div class="aa-chips">${arr.map(x=>`<span class="aa-chip">${esc(x)}</span>`).join('')}</div></div>`;
}

function renderAnalysisResult(folder,a){
  _aaCurrentFolder=folder;
  const card=document.getElementById('aa-result-card'),body=document.getElementById('aa-result-body');
  card.style.display='block';
  const reBtn=document.getElementById('aa-reanalyze-btn');if(reBtn){reBtn.disabled=false;reBtn.textContent='↻ Re-analyze';}
  const caller=(folder.split('_')[0]||'').replace(/\D/g,'');
  const ph=caller?'+'+caller:'Unknown caller';
  const initials=caller?caller.slice(-2):'?';
  const sentClass=a.sentiment==='positive'?'bg':a.sentiment==='negative'?'br':'bz';
  const leadClass=a.lead_quality==='hot'?'bg':a.lead_quality==='cold'?'bz':'ba';
  body.innerHTML=`
    <div class="aa-res-head">
      <div class="aa-res-who">
        <div class="aa-res-avatar">${esc(initials)}</div>
        <div><div class="aa-res-name">${esc(ph)}</div><div class="aa-res-meta">Analyzed ${fmtTs(a._analyzed_at)} · ${esc(a._model||'')}</div></div>
      </div>
    </div>
    <div class="aa-tiles">
      <div class="aa-tile"><div class="aa-tile-lbl">Sentiment</div><div class="aa-tile-val"><span class="badge ${sentClass}">${esc(a.sentiment||'—')}</span></div></div>
      <div class="aa-tile"><div class="aa-tile-lbl">Lead Quality</div><div class="aa-tile-val"><span class="badge ${leadClass}">${esc(a.lead_quality||'—')}</span></div></div>
      <div class="aa-tile"><div class="aa-tile-lbl">Follow-Up</div><div class="aa-tile-val">${a.follow_up_needed?'<span class="badge ba">Yes · '+esc(a.follow_up_priority||'')+'</span>':'<span class="badge bz">Not needed</span>'}</div></div>
    </div>
    <div class="aa-sec"><h4>Summary</h4><p>${esc(a.summary||'—')}</p></div>
    <div class="aa-grid2">
      <div class="aa-sec"><h4>Customer Intent</h4><p>${esc(a.customer_intent||'—')}</p></div>
      <div class="aa-sec"><h4>Suggested Next Action</h4><p>${esc(a.suggested_next_action||'—')}</p></div>
    </div>
    ${_aaListSec('Key Points',a.key_points)}
    <div class="aa-grid2">
      ${_aaChipSec('Topics',a.topics)}
      ${_aaChipSec('Products Mentioned',a.products_mentioned)}
    </div>
    ${_aaListSec('Objections',a.objections)}
    ${a.follow_up_needed?`<div class="aa-followup"><strong>📞 Follow-up:</strong> ${esc(a.follow_up_reason||'')} — recommended ${esc(a.recommended_follow_up_date||'no date suggested')}</div>`:''}
  `;
  card.scrollIntoView({behavior:'smooth',block:'nearest'});
}

async function reanalyzeCurrent(){
  if(!_aaCurrentFolder)return;
  const btn=document.getElementById('aa-reanalyze-btn');
  if(btn){btn.disabled=true;btn.textContent='Re-analyzing…';}
  try{
    const r=await (await fetch('/api/analysis/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folders:[_aaCurrentFolder],force:true})})).json();
    const res=r.results?.[_aaCurrentFolder];
    if(res?.ok){renderAnalysisResult(_aaCurrentFolder,res.analysis);loadAiAnalysisList(aaPage);}
    else alert('Re-analysis failed: '+(res?.error||'unknown error'));
  }catch(e){alert('Re-analysis failed: '+e.message)}
  finally{if(btn){btn.disabled=false;btn.textContent='↻ Re-analyze';}}
}
