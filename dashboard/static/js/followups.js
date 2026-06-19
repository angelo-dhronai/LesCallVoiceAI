// ── follow-up tracker ──────────────────────────────────────────────────────
const FU_SOURCE_LABEL={outbound:'Outbound disposition',ai_analysis:'AI Call Analysis',manual:'Manual (Call History)'};

function renderFollowupRows(entries){
  const tb=document.getElementById('fu-table');
  if(!entries.length){tb.innerHTML='<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--text-d)">No follow-ups found</td></tr>';return}
  tb.innerHTML=entries.map((e,i)=>{
    const contact=e.contact_name||e.contact_phone||'Unknown';
    const statusBadge={pending:'ba',completed:'bg',missed:'br'}[e.status]||'bz';
    return`<tr>
      <td>${esc(contact)}<div class="dim" style="font-size:11px">${esc((e.reason||'').slice(0,60))}${(e.reason||'').length>60?'…':''}</div></td>
      <td>${e.follow_up_required?'Yes':'No'}</td>
      <td>${esc(e.follow_up_date||'—')}</td>
      <td><span class="badge bz">${esc(e.priority||'medium')}</span></td>
      <td class="dim" style="font-size:11px">${esc(FU_SOURCE_LABEL[e.source]||e.source)}</td>
      <td><span class="badge ${statusBadge}">${esc(e.status)}</span></td>
      <td style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <button class="btn btn-gh btn-sm" onclick="openFuDetail(${i})" title="View full details">👁</button>
        <select class="inp inp-sm" onchange="updateFollowupStatus('${esc(e.id)}',this.value)" style="font-size:11px">
          <option value="pending" ${e.status==='pending'?'selected':''}>Pending</option>
          <option value="completed" ${e.status==='completed'?'selected':''}>Completed</option>
          <option value="missed" ${e.status==='missed'?'selected':''}>Missed</option>
        </select>
        <button class="btn btn-gh btn-sm" onclick="sendToAutomation('${esc(e.id)}',this)" title="Schedule outbound follow-up call">⚡ Automate</button>
        <button class="btn btn-d btn-sm" onclick="dismissFollowup('${esc(e.id)}','${esc(e._manual_id||'')}',this)" title="Dismiss / remove">🗑</button>
      </td>
    </tr>`;
  }).join('');
}

let _fuEntries=[];

async function loadFollowups(){
  const status=document.getElementById('fu-status')?.value||'all';
  const tb=document.getElementById('fu-table');
  tb.innerHTML='<tr><td colspan="7" style="text-align:center;padding:24px"><div class="spin"></div></td></tr>';
  try{
    const d=await api('/api/followups?status='+encodeURIComponent(status));
    _fuEntries=d.followups||[];
    renderFollowupRows(_fuEntries);
  }catch(e){tb.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--error);padding:24px">Error</td></tr>'}
}

function openFuDetail(idx){
  const e=_fuEntries[idx];
  if(!e)return;
  const bd=document.getElementById('fudetail-backdrop');
  document.getElementById('fudetail-contact').textContent=(e.contact_name||'')+(e.contact_phone?'\n'+e.contact_phone:'');
  document.getElementById('fudetail-source').textContent=FU_SOURCE_LABEL[e.source]||e.source;
  document.getElementById('fudetail-status').textContent=e.status;
  document.getElementById('fudetail-priority').textContent=e.priority||'medium';
  document.getElementById('fudetail-date').textContent=e.follow_up_date||'—';
  document.getElementById('fudetail-reason').textContent=e.reason||'—';
  document.getElementById('fudetail-updated').textContent=e.updated_at?fmtTs(e.updated_at):'—';
  if(e.call_id||e.folder){document.getElementById('fudetail-callid').textContent=e.call_id||e.folder;document.getElementById('fudetail-callid-row').style.display='';}
  else{document.getElementById('fudetail-callid-row').style.display='none';}
  bd.style.display='flex';
}

function closeFuDetail(){document.getElementById('fudetail-backdrop').style.display='none';}

async function updateFollowupStatus(id,status){
  try{
    await fetch('/api/followups/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});
    loadFollowups();
  }catch(e){alert('Failed to update status')}
}

async function dismissFollowup(entryId,manualId,btnEl){
  if(!confirm('Remove this follow-up from the list?'))return;
  btnEl.disabled=true;
  try{
    if(manualId){
      await fetch('/api/followups/manual/'+encodeURIComponent(manualId),{method:'DELETE'});
    } else {
      await fetch('/api/followups/'+encodeURIComponent(entryId),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'dismissed'})});
    }
    loadFollowups();
  }catch(e){alert('Failed to dismiss');btnEl.disabled=false}
}

async function sendToAutomation(followupId,btnEl){
  btnEl.disabled=true;
  try{
    const r=await fetch('/api/followup-automation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({followup_id:followupId})});
    if(r.status===409){pushNotification('Already scheduled — view in Follow-Up Automation','info');switchTab('followup-automation');return;}
    const d=await r.json();
    if(!d.ok){alert(d.reason||'Could not schedule automation');btnEl.disabled=false;return}
    pushNotification('Sent to Follow-Up Automation — set a time on that tab','info');
    switchTab('followup-automation');
  }catch(e){alert('Failed to send to automation');btnEl.disabled=false}
}
