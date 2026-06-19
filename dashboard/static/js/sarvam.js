// ── API usage / credits / key management ────────────────────────────────────
let _svDailyRows=[], _svSortKey=null, _svSortDir='asc';
let _creditsLowNotified=false, _creditsExhaustedNotified=false;

// Polled both when the tab is active AND globally (see app.js) so low/zero
// credit warnings fire even if the user isn't looking at this tab.
async function checkApiCredits(){
  let d;
  try{ d=await api('/api/sarvam/credits'); }catch(e){ console.error(e); return; }
  renderCreditsUI(d);
  if(d.auto_rotated){
    pushNotification(`🔄 API credits hit 0 — auto-rotated to backup key (${d.auto_rotated.rotated_to}). Restart the agent to apply it.`,'warn');
    _creditsLowNotified=false; _creditsExhaustedNotified=false;
  }else if(d.exhausted){
    if(!_creditsExhaustedNotified){
      pushNotification(d.needs_new_key
        ?'🔴 API credits exhausted (0 remaining) — add a new key now, no backups left in the pool.'
        :'🔴 API credits exhausted (0 remaining) — replace the key now.','error');
      _creditsExhaustedNotified=true;
    }
  }else if(d.low){
    if(!_creditsLowNotified){
      pushNotification(`⚠ API credits running low (${R(d.remaining_credits,0)} remaining) — consider replacing the key soon.`,'warn');
      _creditsLowNotified=true;
    }
  }else{
    _creditsLowNotified=false; _creditsExhaustedNotified=false;
  }
  renderRestartBadge(d.restart_required);
  return d;
}

function renderCreditsUI(d){
  if(!document.getElementById('cr-remaining'))return; // tab not in DOM context yet — safe no-op
  document.getElementById('cr-remaining').textContent=R(d.remaining_credits,0);
  document.getElementById('cr-total').textContent=R(d.total_credits,0);
  document.getElementById('cr-used-lbl').textContent=`${R(d.used_credits,0)} used (${R(d.percent_used,1)}%)`;
  document.getElementById('cr-synced-lbl').textContent=d.updated_at?'Last synced '+fmtTs(d.updated_at):'Never synced';
  const fillEl=document.getElementById('cr-fill');
  fillEl.style.width=Math.min(d.percent_used,100)+'%';
  fillEl.className='credit-fill'+(d.percent_used>=90?' crit':d.percent_used>=70?' warn':'');
  document.getElementById('cr-input').value=d.remaining_credits;
}

function renderRestartBadge(restartRequired){
  const badge=document.getElementById('ak-restart-badge');
  const action=document.getElementById('ak-restart-action');
  if(!badge||!action)return;
  badge.innerHTML=restartRequired?'<span class="badge ba">⚠ Restart required to apply key change</span>':'';
  action.style.display=restartRequired?'block':'none';
}

async function updateSarvamCredits(){
  const val=document.getElementById('cr-input').value;
  if(val===''||isNaN(val)){pushNotification('Enter a valid remaining-credits number','warn');return}
  try{
    const d=await api('/api/sarvam/credits',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({remaining_credits:parseFloat(val)})});
    if(d.ok){
      pushNotification('API credit balance synced: '+d.remaining_credits+' remaining','info');
      _creditsLowNotified=false;_creditsExhaustedNotified=false;
      checkApiCredits();
    }
  }catch(e){pushNotification('Could not sync credits: '+e,'error')}
}

// ── API key management ──────────────────────────────────────────────────────
function showAkMsg(text,color){
  const el=document.getElementById('ak-msg');
  if(!el)return;
  el.textContent=text;el.style.color=color||'var(--text)';
  el.style.background=color==='var(--success)'?'var(--success-bg)':color==='var(--error)'?'var(--error-bg)':color==='var(--warn)'?'var(--warn-bg)':'var(--surface2)';
  el.style.display='block';
  clearTimeout(el._t);
  el._t=setTimeout(()=>{el.style.display='none'},5000);
}

async function loadApiKeyInfo(){
  if(!document.getElementById('ak-active'))return;
  try{
    const d=await api('/api/sarvam/keys');
    document.getElementById('ak-active').textContent=d.active_key_masked||'Not set';
    document.getElementById('ak-pool-count').textContent=d.pool_count+' key'+(d.pool_count!==1?'s':'')+' in pool'+
      (d.pool_count?' ('+d.pool.join(', ')+')':'');
    renderRestartBadge(d.restart_required);
  }catch(e){console.error(e)}
}

async function replaceApiKey(){
  const input=document.getElementById('ak-replace-input');
  const key=input.value.trim();
  if(!key){showAkMsg('Enter an API key first','var(--error)');return}
  try{
    const d=await api('/api/sarvam/keys/replace',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({api_key:key})});
    if(d.ok){
      showAkMsg('Key replaced ('+d.masked_key+'). Credits reset to total. Restart the agent to apply it.','var(--success)');
      pushNotification('API key replaced — restart the agent to apply it.','info');
      input.value='';
      _creditsLowNotified=false;_creditsExhaustedNotified=false;
      loadApiKeyInfo();checkApiCredits();
    }else{showAkMsg(d.reason||'Error','var(--error)')}
  }catch(e){showAkMsg(String(e),'var(--error)')}
}

async function addApiKeysBulk(){
  const ta=document.getElementById('ak-bulk-input');
  const keys=ta.value.split(/[\n,]+/).map(k=>k.trim()).filter(Boolean);
  if(!keys.length){showAkMsg('Paste at least one key first','var(--error)');return}
  try{
    const d=await api('/api/sarvam/keys/bulk',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keys})});
    if(d.ok){
      showAkMsg(`✓ Added ${d.added} key(s) to the pool. Skipped ${d.skipped} duplicate(s).`,'var(--success)');
      ta.value='';
      loadApiKeyInfo();checkApiCredits();
    }else{showAkMsg(d.reason||'Error','var(--error)')}
  }catch(e){showAkMsg(String(e),'var(--error)')}
}

async function clearApiKeyPool(){
  if(!confirm('Remove all backup keys from the pool?'))return;
  try{
    await fetch('/api/sarvam/keys/pool',{method:'DELETE'});
    showAkMsg('Backup key pool cleared','var(--success)');
    loadApiKeyInfo();
  }catch(e){showAkMsg(String(e),'var(--error)')}
}

// ── usage tables ─────────────────────────────────────────────────────────────
function renderSvDaily(rows){
  const tb=document.getElementById('sv-daily');
  tb.innerHTML=rows.length
    ?rows.map(r=>`<tr>
      <td class="td" style="font-size:12px">${esc(r.date)}</td><td>${r.call_count??0}</td>
      <td>${R(r.stt_sarvam_billed_seconds,1)}</td><td>${(r.tts_chars||0).toLocaleString()}</td>
      <td>${RP(r.est_stt_cost)}</td><td>${RP(r.est_tts_cost)}</td>
      <td><strong>${RP(r.est_total_cost)}</strong></td>
      <td class="td">${R(r.est_total_cost,2)} cr</td></tr>`).join('')
    :'<tr><td colspan="8" style="text-align:center;padding:24px;color:var(--text-d)">No data</td></tr>';
}

async function loadSarvam(period,btn){
  if(btn){document.querySelectorAll('.ptab').forEach(b=>b.classList.remove('active'));btn.classList.add('active')}
  try{
    const d=await api('/api/sarvam/usage?period='+period);const s=d.summary||{};
    document.getElementById('sv-calls').textContent=s.call_count??'0';
    document.getElementById('sv-stt').textContent=RP(s.est_stt_cost);
    document.getElementById('sv-stt-s').textContent=R(s.stt_sarvam_billed_seconds,1)+' billed s';
    document.getElementById('sv-tts').textContent=RP(s.est_tts_cost);
    document.getElementById('sv-tts-c').textContent=(s.tts_chars||0).toLocaleString()+' chars';
    document.getElementById('sv-total').textContent=RP(s.est_total_cost);
    document.getElementById('sv-total-credits').textContent=R(s.est_total_cost,2)+' credits (est.)';
    const daily=d.daily||{};
    _svDailyRows=Object.keys(daily).sort().map(day=>({date:day,...(daily[day]||{})}));
    renderSvDaily(_svSortKey?sortRows(_svDailyRows,_svSortKey,_svSortDir):_svDailyRows);
    const top=d.top_calls||[];
    document.getElementById('sv-top').innerHTML=top.length
      ?top.map(c=>`<tr><td class="tm" style="font-size:11px">${shortId(c.call_id)}</td><td>${RP(c.est_tts_cost)}</td><td><strong>${RP(c.est_total_cost)}</strong></td><td class="td">${R(c.est_total_cost,2)} cr</td></tr>`).join('')
      :'<tr><td colspan="4" style="text-align:center;padding:24px;color:var(--text-d)">No data</td></tr>';
  }catch(e){console.error(e)}
}

(function initSarvamSort(){
  const thead=document.querySelector('#tab-sarvam table thead');
  if(!thead)return;
  bindSortableHeaders(thead,(key,dir)=>{
    _svSortKey=key;_svSortDir=dir;
    renderSvDaily(sortRows(_svDailyRows,key,dir));
  });
})();
