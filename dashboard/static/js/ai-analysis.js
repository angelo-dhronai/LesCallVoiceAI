// ── AI call analysis (on-demand only — nothing here runs automatically) ──────
let aaPage=1,aaPages=1,_aaSelected=new Set(),_aaRows=[];

function renderAaRows(recs){
  const tb=document.getElementById('aa-table');
  if(!recs.length){tb.innerHTML='<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--text-d)">No completed calls found</td></tr>';return}
  tb.innerHTML=recs.map(r=>{
    const ph=r.caller&&r.caller.length>4?'+'+r.caller.replace(/\D/g,''):r.caller||'Unknown';
    const checked=_aaSelected.has(r.folder)?'checked':'';
    return`<tr>
      <td><input type="checkbox" data-folder="${esc(r.folder)}" ${checked} onchange="toggleAnalysisSelect(this)"></td>
      <td><a href="#" onclick="viewAnalysis('${esc(r.folder)}');return false">${esc(ph)}</a></td>
      <td style="font-size:12px">${fmtTs(r.folder_dt)}</td>
      <td>${fmtDur(r.duration_seconds)}</td>
      <td>${r.analyzed?'<span class="badge bg">✓ Analyzed</span>':'<span class="badge bz">Not analyzed</span>'}</td>
    </tr>`;
  }).join('');
}

async function loadAiAnalysisList(page){
  page=page||1;aaPage=page;
  const search=document.getElementById('aa-search')?.value||'';
  const tb=document.getElementById('aa-table');
  tb.innerHTML='<tr><td colspan="5" style="text-align:center;padding:24px"><div class="spin"></div></td></tr>';
  try{
    const d=await api(`/api/analysis/eligible?page=${page}&limit=50&search=${encodeURIComponent(search)}`);
    aaPages=d.pages||1;
    _aaRows=d.recordings||[];
    renderAaRows(_aaRows);
    document.getElementById('aa-pages').innerHTML=
      `<button class="pgbtn" onclick="loadAiAnalysisList(${page-1})" ${page<=1?'disabled':''}>← Prev</button>
       <span class="pginf">Page ${page} of ${aaPages}</span>
       <button class="pgbtn" onclick="loadAiAnalysisList(${page+1})" ${page>=aaPages?'disabled':''}>Next →</button>`;
  }catch(e){tb.innerHTML='<tr><td colspan="5" style="text-align:center;color:var(--error);padding:24px">Error</td></tr>'}
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

function renderAnalysisResult(folder,a){
  const card=document.getElementById('aa-result-card'),body=document.getElementById('aa-result-body');
  card.style.display='block';
  const listHtml=(arr)=>(arr&&arr.length)?'<ul style="margin:4px 0 0 18px">'+arr.map(x=>`<li>${esc(x)}</li>`).join('')+'</ul>':'<span class="dim">None</span>';
  body.innerHTML=`
    <div class="dim" style="font-size:11px;margin-bottom:10px">Call: ${esc(folder)} · Analyzed ${fmtTs(a._analyzed_at)} · Model ${esc(a._model||'')}</div>
    <div class="g2">
      <div><strong>Summary</strong><div style="margin-top:4px">${esc(a.summary||'—')}</div></div>
      <div><strong>Customer Intent</strong><div style="margin-top:4px">${esc(a.customer_intent||'—')}</div></div>
    </div>
    <div class="g3" style="margin-top:14px">
      <div><strong>Sentiment</strong><div style="margin-top:4px"><span class="badge ${a.sentiment==='positive'?'bg':a.sentiment==='negative'?'br':'bz'}">${esc(a.sentiment||'—')}</span></div></div>
      <div><strong>Lead Quality</strong><div style="margin-top:4px"><span class="badge ${a.lead_quality==='hot'?'bg':a.lead_quality==='cold'?'bz':'ba'}">${esc(a.lead_quality||'—')}</span></div></div>
      <div><strong>Follow-Up Needed</strong><div style="margin-top:4px">${a.follow_up_needed?'<span class="badge ba">Yes — '+esc(a.follow_up_priority||'')+' priority</span>':'<span class="badge bz">No</span>'}</div></div>
    </div>
    <div class="g2" style="margin-top:14px">
      <div><strong>Key Points</strong>${listHtml(a.key_points)}</div>
      <div><strong>Suggested Next Action</strong><div style="margin-top:4px">${esc(a.suggested_next_action||'—')}</div></div>
    </div>
    <div class="g3" style="margin-top:14px">
      <div><strong>Objections</strong>${listHtml(a.objections)}</div>
      <div><strong>Topics</strong>${listHtml(a.topics)}</div>
      <div><strong>Products Mentioned</strong>${listHtml(a.products_mentioned)}</div>
    </div>
    ${a.follow_up_needed?`<div class="notice mt14"><strong>Follow-up:</strong> ${esc(a.follow_up_reason||'')} — recommended ${esc(a.recommended_follow_up_date||'no date suggested')}</div>`:''}
  `;
  card.scrollIntoView({behavior:'smooth',block:'nearest'});
}
