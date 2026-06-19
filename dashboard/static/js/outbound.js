// ── outbound campaign ──────────────────────────────────────────────────────
let _obCsFilter='all', _obDpFilter='all', _obAllContacts=[], _campPollTimer=null;
let _obSortKey=null, _obSortDir='asc';

const CS_LABELS={not_called:'Not Called',answered:'Answered',no_answer:'No Answer',busy:'Busy',declined:'Declined',failed:'Failed'};
const CS_COLORS={not_called:'bz',answered:'bg',no_answer:'ba',busy:'ba',declined:'br',failed:'br'};
const DP_LABELS={not_classified:'Unclassified',interested:'Interested',site_visit_booked:'Site Visit Booked',
  inquiry:'Inquiry',in_doubt:'In Doubt',to_contact_later:'Contact Later',rejected:'Rejected'};
const DP_COLORS={not_classified:'bz',interested:'bb',site_visit_booked:'bg',
  inquiry:'bb',in_doubt:'ba',to_contact_later:'ba',rejected:'br'};

function toggleSection(id){
  const el=document.getElementById(id);
  const arrow=document.getElementById(id+'-arrow');
  const hidden=el.style.display==='none';
  el.style.display=hidden?'block':'none';
  if(arrow)arrow.textContent=hidden?'▲':'▼';
}

async function loadOutbound(){
  try{
    const [dc, ds]=await Promise.all([
      api('/api/outbound/contacts?call_status='+_obCsFilter+'&disposition='+_obDpFilter),
      api('/api/outbound/stats')
    ]);
    _obAllContacts=dc.contacts||[];
    renderOutbound(_obAllContacts);
    renderOBStats(ds);
  }catch(e){console.error(e)}
}

function renderOBStats(ds){
  const s=ds.stats||{}, d=ds.dispositions||{};
  const ids=['total','not_called','answered','no_answer','declined','busy','failed'];
  ids.forEach(k=>{const el=document.getElementById('os-'+k);if(el)el.textContent=s[k]??0});
  ['interested','site_visit_booked','inquiry','in_doubt','to_contact_later','rejected'].forEach(k=>{
    const el=document.getElementById('od-'+k);if(el)el.textContent=d[k]??0;
  });
}

function setOBFilter(type, val, btn){
  if(type==='cs'){
    _obCsFilter=val;
    document.querySelectorAll('[onclick*="setOBFilter(\'cs\'"]').forEach(b=>b.classList.remove('active'));
  } else {
    _obDpFilter=val;
    document.querySelectorAll('[onclick*="setOBFilter(\'dp\'"]').forEach(b=>b.classList.remove('active'));
  }
  btn.classList.add('active');
  loadOutbound();
}

function filterContacts(){
  const q=document.getElementById('ob-search').value.toLowerCase().trim();
  renderOutbound(q?_obAllContacts.filter(c=>(c.phone||'').includes(q)||(c.name||'').toLowerCase().includes(q)):_obAllContacts);
}

function renderOutbound(contacts){
  if(_obSortKey)contacts=sortRows(contacts,_obSortKey,_obSortDir);
  const cnt=document.getElementById('ob-count');
  if(cnt)cnt.textContent=contacts.length+' contact'+(contacts.length!==1?'s':'');
  const tb=document.getElementById('ob-table');
  if(!contacts.length){
    tb.innerHTML='<tr><td colspan="9" style="text-align:center;padding:32px;color:var(--text-d)">No contacts match — try changing the filters or add contacts above.</td></tr>';
    return;
  }
  tb.innerHTML=contacts.map((c,i)=>{
    const cs=c.call_status||'not_called';
    const dp=c.disposition||'not_classified';
    return`<tr id="ob-row-${c.id}">
      <td class="td" style="font-size:12px">${i+1}</td>
      <td class="tm">${esc(c.phone)}</td>
      <td style="max-width:120px">${esc(c.name||'—')}</td>
      <td><span class="badge ${CS_COLORS[cs]||'bz'}">${CS_LABELS[cs]||cs}</span></td>
      <td>
        <select class="inp inp-sm" style="width:130px" onchange="setDisposition('${c.id}',this.value)">
          ${Object.entries(DP_LABELS).map(([v,l])=>`<option value="${v}" ${dp===v?'selected':''}>${l}</option>`).join('')}
        </select>
      </td>
      <td class="td" style="font-size:11px">${c.last_called_at?fmtTs(c.last_called_at):'—'}</td>
      <td class="td" style="font-size:12px;text-align:center">${c.call_count||0}</td>
      <td style="max-width:140px;color:var(--text-s);font-size:11px">${esc(c.notes||'')}</td>
      <td style="white-space:nowrap">
        <button class="btn btn-p btn-sm" onclick="dialContact('${c.id}',this)">📞 Call</button>
        <button class="btn btn-d btn-sm" style="margin-left:4px" onclick="deleteOBContact('${c.id}')">✕</button>
      </td>
    </tr>`;
  }).join('');
}

async function setDisposition(id, disposition){
  try{
    await fetch('/api/outbound/contacts/'+id,{method:'PATCH',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({disposition})});
    // reload stats silently
    const ds=await api('/api/outbound/stats');renderOBStats(ds);
  }catch(e){console.error(e)}
}

async function dialContact(id, btn){
  const orig=btn.textContent;
  btn.disabled=true; btn.textContent='⏳';
  showOBMsg('Dialing — AI will connect when answered…','var(--accent)');
  try{
    const d=await api('/api/outbound/dial/'+id,{method:'POST'});
    if(d.ok){
      showOBMsg('Call initiated to '+d.phone,'var(--success)');
      btn.textContent='✓';
      setTimeout(()=>{btn.textContent=orig;btn.disabled=false;loadOutbound()},5000);
    }else{
      showOBMsg('Call failed: '+(d.reason||'error'),'var(--error)');
      btn.textContent=orig;btn.disabled=false;
    }
  }catch(e){showOBMsg(String(e),'var(--error)');btn.textContent=orig;btn.disabled=false;}
}

async function addOutboundContact(){
  const phone=document.getElementById('ob-phone').value.trim();
  const name=document.getElementById('ob-name').value.trim();
  const notes=document.getElementById('ob-notes').value.trim();
  if(!phone){showOBMsg('Phone number is required','var(--error)');return}
  try{
    const d=await api('/api/outbound/contacts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone,name,notes})});
    if(d.ok){
      document.getElementById('ob-phone').value='';
      document.getElementById('ob-name').value='';
      document.getElementById('ob-notes').value='';
      showOBMsg('Contact added ✓','var(--success)');
      loadOutbound();
    }else{showOBMsg(d.reason||'Error','var(--error)')}
  }catch(e){showOBMsg(String(e),'var(--error)')}
}

async function uploadContacts(){
  const fileEl=document.getElementById('ob-file');
  if(!fileEl.files?.length){showOBMsg('Please select a file first','var(--warn)');return}
  const file=fileEl.files[0];
  const form=new FormData();
  form.append('file',file);
  showOBMsg('Uploading and parsing '+file.name+'…','var(--accent)');
  try{
    const r=await fetch('/api/outbound/contacts/upload',{method:'POST',body:form});
    const d=await r.json();
    if(d.ok){
      showOBMsg(`✓ Added ${d.added} numbers. Duplicates skipped: ${d.duplicates}. Parse errors: ${d.parse_errors||0}.`,'var(--success)');
      fileEl.value='';
      loadOutbound();
    }else{showOBMsg('Upload failed: '+(d.reason||'error'),'var(--error)')}
  }catch(e){showOBMsg(String(e),'var(--error)')}
}

async function deleteOBContact(id){
  if(!confirm('Remove this contact?'))return;
  try{await fetch('/api/outbound/contacts/'+id,{method:'DELETE'});loadOutbound()}catch(e){console.error(e)}
}

async function clearAllContacts(){
  if(!confirm('Delete ALL contacts? This cannot be undone.'))return;
  try{await fetch('/api/outbound/contacts/all',{method:'DELETE'});loadOutbound()}catch(e){console.error(e)}
}

function showOBMsg(text,color){
  const el=document.getElementById('ob-msg');
  if(!el)return;
  el.textContent=text;el.style.color=color||'var(--text)';
  el.style.background=color==='var(--success)'?'var(--success-bg)':color==='var(--error)'?'var(--error-bg)':color==='var(--warn)'?'var(--warn-bg)':'var(--surface2)';
  el.style.display='block';
  clearTimeout(el._t);
  el._t=setTimeout(()=>{el.style.display='none'},5000);
}

// ── Campaign ───────────────────────────────────────────────────────────────
async function startCampaign(){
  const filter=document.getElementById('camp-filter').value;
  const delay=parseFloat(document.getElementById('camp-delay').value)||4;
  const maxCalls=parseInt(document.getElementById('camp-max').value)||0;
  try{
    const d=await api('/api/outbound/campaign/start',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({call_status_filter:filter,delay_seconds:delay,max_calls:maxCalls})});
    if(d.ok){
      document.getElementById('camp-start-btn').disabled=true;
      document.getElementById('camp-stop-btn').disabled=false;
      document.getElementById('camp-progress-wrap').style.display='block';
      document.getElementById('campaign-status-label').textContent='Running…';
      startCampPoll();
    }else{showOBMsg('Could not start: '+(d.reason||'error'),'var(--error)')}
  }catch(e){showOBMsg(String(e),'var(--error)')}
}

async function stopCampaign(){
  await fetch('/api/outbound/campaign/stop',{method:'POST'});
  document.getElementById('campaign-status-label').textContent='Stopping…';
}

function startCampPoll(){
  if(_campPollTimer)clearInterval(_campPollTimer);
  _campPollTimer=setInterval(pollCampaign,2000);
}

async function pollCampaign(){
  try{
    const d=await api('/api/outbound/campaign/status');
    const pct=d.total>0?Math.round((d.completed/d.total)*100):0;
    document.getElementById('camp-prog-bar').style.width=pct+'%';
    document.getElementById('camp-prog-pct').textContent=pct+'%';
    document.getElementById('camp-cur-phone').textContent=d.current_phone||'';
    document.getElementById('camp-prog-label').textContent=d.running?
      `Calling ${d.current_phone||'…'} (${d.completed}/${d.total})`:`Campaign done — ${d.completed}/${d.total} processed`;
    ['answered','no_answer','declined','busy','failed','skipped'].forEach(k=>{
      const el=document.getElementById('cp-'+k);if(el)el.textContent=d[k]||0;
    });
    if(!d.running){
      document.getElementById('camp-start-btn').disabled=false;
      document.getElementById('camp-stop-btn').disabled=true;
      document.getElementById('campaign-status-label').textContent=
        d.error?'Error: '+d.error:`Done — ${d.answered} answered of ${d.total}`;
      clearInterval(_campPollTimer);_campPollTimer=null;
      pushNotification(d.error?'Campaign error: '+d.error:`Campaign finished — ${d.answered} answered of ${d.total}`, d.error?'error':'info');
      loadOutbound(); // refresh table after campaign
    }
  }catch(e){console.error(e)}
}

(function initOutboundSort(){
  const thead=document.querySelector('#tab-outbound table thead');
  if(!thead)return;
  bindSortableHeaders(thead,(key,dir)=>{
    _obSortKey=key;_obSortDir=dir;
    filterContacts(); // re-renders respecting the active search filter, now sorted
  });
})();
