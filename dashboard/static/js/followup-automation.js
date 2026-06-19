// ── follow-up automation ──────────────────────────────────────────────────
// Schedules/places the outbound call only (same FreeSWITCH path as manual
// Outbound Calls). Past-call context / script notes / call aim are shown
// here for the operator's reference — they are NOT injected into the live
// agent's conversation, by design (see Agent Settings tab).

const FA_STATUS_BADGE={scheduled:'ba',calling:'bz',called:'bg',cancelled:'br',failed:'br'};

function renderAutomationRows(entries){
  const tb=document.getElementById('fa-table');
  if(!entries.length){tb.innerHTML='<tr><td colspan="8" style="text-align:center;padding:32px;color:var(--text-d)">Nothing scheduled yet — use ⚡ Automate on the Follow-Up Tracker tab</td></tr>';return}
  tb.innerHTML=entries.map(e=>{
    const contact=e.contact_name||e.contact_phone||'Unknown';
    const editable=e.status==='scheduled';
    const badge=FA_STATUS_BADGE[e.status]||'bz';
    const localTime=e.scheduled_time?new Date(e.scheduled_time).toISOString().slice(0,16):'';
    return`<tr>
      <td>${esc(contact)}${e.contact_phone&&e.contact_name?`<div class="dim" style="font-size:11px">${esc(e.contact_phone)}</div>`:''}</td>
      <td style="max-width:220px"><div class="dim" style="font-size:11px" title="${esc(e.past_call_context||'')}">${esc((e.past_call_context||'').slice(0,90))}${(e.past_call_context||'').length>90?'…':''}</div></td>
      <td style="min-width:160px">${editable
          ?`<input class="inp inp-sm" style="width:100%;font-size:11px" value="${esc(e.call_aim||'')}" onblur="updateAutomationField('${e.id}','call_aim',this.value)">`
          :esc(e.call_aim||'—')}</td>
      <td style="min-width:160px">${editable
          ?`<textarea class="inp inp-sm" style="width:100%;font-size:11px;min-height:36px" onblur="updateAutomationField('${e.id}','script_notes',this.value)">${esc(e.script_notes||'')}</textarea>`
          :esc(e.script_notes||'—')}</td>
      <td>${editable
          ?`<input class="inp inp-sm" type="datetime-local" style="font-size:11px" value="${localTime}" onchange="updateAutomationField('${e.id}','scheduled_time',new Date(this.value).toISOString())">`
          :(e.scheduled_time?fmtTs(e.scheduled_time):'—')}</td>
      <td><span class="badge ${badge}">${esc(e.status)}</span></td>
      <td>${esc(e.call_outcome||'—')}</td>
      <td style="display:flex;gap:6px">
        ${editable?`<button class="btn btn-p btn-sm" onclick="callAutomationNow('${e.id}',this)">📞 Call Now</button>
        <button class="btn btn-d btn-sm" onclick="cancelAutomation('${e.id}')">✕</button>`:`<button class="btn btn-gh btn-sm" onclick="deleteAutomation('${e.id}')">🗑</button>`}
      </td>
    </tr>`;
  }).join('');
}

async function loadAutomationList(){
  const tb=document.getElementById('fa-table');
  tb.innerHTML='<tr><td colspan="8" style="text-align:center;padding:24px"><div class="spin"></div></td></tr>';
  try{
    const d=await api('/api/followup-automation');
    renderAutomationRows(d.automations||[]);
  }catch(e){tb.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--error);padding:24px">Error</td></tr>'}
}

async function updateAutomationField(id,field,value){
  try{
    await fetch('/api/followup-automation/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({[field]:value})});
    loadAutomationList();
  }catch(e){alert('Failed to save')}
}

async function callAutomationNow(id,btnEl){
  if(!confirm('This will place a real outbound call right now. Continue?'))return;
  btnEl.disabled=true;btnEl.textContent='Calling…';
  try{
    await fetch('/api/followup-automation/'+encodeURIComponent(id)+'/call-now',{method:'POST'});
    pushNotification('Follow-up call placed','info');
    setTimeout(loadAutomationList,2000);
  }catch(e){alert('Failed to place call');btnEl.disabled=false}
}

async function cancelAutomation(id){
  if(!confirm('Cancel this scheduled follow-up call?'))return;
  try{
    await fetch('/api/followup-automation/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'cancelled'})});
    loadAutomationList();
  }catch(e){alert('Failed to cancel')}
}

async function deleteAutomation(id){
  if(!confirm('Remove this entry from the automation list?'))return;
  try{
    await fetch('/api/followup-automation/'+encodeURIComponent(id),{method:'DELETE'});
    loadAutomationList();
  }catch(e){alert('Failed to delete')}
}
