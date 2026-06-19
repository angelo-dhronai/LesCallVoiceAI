// ── history / call activity ──────────────────────────────────────────────────
let histPage=1,histPages=1,_histRows=[],_histSortKey=null,_histSortDir='asc';
// Holds the currently rendered row objects so onclick handlers can reference
// by index rather than embedding raw JSON in HTML attributes (which breaks
// on fields containing double quotes, +, or other special characters).
let _renderedHistRows=[];

const STATUS_BADGE={answered:'bg',no_answer:'ba',busy:'ba',declined:'br',failed:'br',missed:'ba'};

function renderHistoryRows(rows){
  _renderedHistRows=rows;
  const tb=document.getElementById('hist-table');
  tb.innerHTML=rows.length
    ?rows.map((c,i)=>{
      const contact=c.contact_name||c.contact_phone||'—';
      const sub=c.contact_name&&c.contact_phone?c.contact_phone:'';
      const badge=STATUS_BADGE[c.status]||'bz';
      return`<tr>
      <td class="td" style="font-size:12px">${fmtTs(c.end_time)}</td>
      <td class="td">${esc(contact)}${sub?`<div class="dim" style="font-size:11px">${esc(sub)}</div>`:''}</td>
      <td><span class="badge bz">${c.call_type==='outbound'?'📞 Outbound':'📥 Inbound'}</span></td>
      <td>${fmtDur(c.duration_seconds)}</td>
      <td><span class="badge ${badge}">${esc(c.status||'—')}</span></td>
      <td class="tm trunc" title="${esc(c.call_id||'')}">${c.call_id?shortId(c.call_id):'—'}</td>
      <td class="td">${c.est_total_cost>0?R(c.est_total_cost,4)+' cr':'—'}</td>
      <td><button class="btn btn-gh btn-sm" onclick="openAddFuModal(${i})" title="Add to Follow-Up Tracker">⏰ Follow-Up</button></td>
    </tr>`;
    }).join('')
    :'<tr><td colspan="8" style="text-align:center;padding:40px;color:var(--text-d)">No calls found</td></tr>';
}

async function loadHistory(page){
  page=page||1;histPage=page;
  const search=document.getElementById('hist-search').value;
  const days=document.getElementById('hist-days').value;
  const callType=document.getElementById('hist-call-type')?.value||'all';
  const status=document.getElementById('hist-status')?.value||'all';
  const tb=document.getElementById('hist-table');
  tb.innerHTML='<tr><td colspan="8" style="text-align:center;padding:24px"><div class="spin"></div></td></tr>';
  try{
    const d=await api(`/api/calls/history?page=${page}&limit=50&search=${encodeURIComponent(search)}&days=${days}&call_type=${callType}&status=${status}`);
    histPages=d.pages||1;
    _histRows=d.calls||[];
    renderHistoryRows(_histSortKey?sortRows(_histRows,_histSortKey,_histSortDir):_histRows);
    document.getElementById('hist-pages').innerHTML=
      `<button class="pgbtn" onclick="loadHistory(${page-1})" ${page<=1?'disabled':''}>← Prev</button>
       <span class="pginf">Page ${page} of ${histPages}</span>
       <button class="pgbtn" onclick="loadHistory(${page+1})" ${page>=histPages?'disabled':''}>Next →</button>`;
  }catch(e){tb.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--error);padding:24px">Error</td></tr>'}
}

(function initHistorySort(){
  const thead=document.querySelector('#tab-history thead');
  if(!thead)return;
  bindSortableHeaders(thead,(key,dir)=>{
    _histSortKey=key;_histSortDir=dir;
    renderHistoryRows(sortRows(_histRows,key,dir));
  });
})();

// ── Add to Follow-Up modal ────────────────────────────────────────────────────
function openAddFuModal(idx){
  const call=_renderedHistRows[idx];
  if(!call)return;
  document.getElementById('addfu-name').value=call.contact_name||'';
  document.getElementById('addfu-phone').value=call.contact_phone||'';
  document.getElementById('addfu-reason').value='';
  document.getElementById('addfu-date').value='';
  document.getElementById('addfu-priority').value='medium';
  document.getElementById('addfu-call-id').value=call.call_id||'';
  const bd=document.getElementById('addfu-backdrop');
  bd.style.display='flex';
  document.getElementById('addfu-submit').disabled=false;
}

function closeAddFuModal(){
  document.getElementById('addfu-backdrop').style.display='none';
}

async function submitAddFuModal(){
  const phone=(document.getElementById('addfu-phone').value||'').trim();
  if(!phone){alert('Phone number is required');return;}
  const btn=document.getElementById('addfu-submit');
  btn.disabled=true;btn.textContent='Saving…';
  try{
    const r=await fetch('/api/followups/manual',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        contact_name:document.getElementById('addfu-name').value.trim(),
        contact_phone:phone,
        reason:document.getElementById('addfu-reason').value.trim(),
        follow_up_date:document.getElementById('addfu-date').value||null,
        priority:document.getElementById('addfu-priority').value,
        call_id:document.getElementById('addfu-call-id').value||null,
      })
    });
    const d=await r.json();
    if(!d.ok){alert(d.reason||'Failed to save');btn.disabled=false;btn.textContent='⏰ Add to Follow-Up';return;}
    closeAddFuModal();
    pushNotification('Added to Follow-Up Tracker','info');
  }catch(e){alert('Failed to save');btn.disabled=false;btn.textContent='⏰ Add to Follow-Up';}
}
