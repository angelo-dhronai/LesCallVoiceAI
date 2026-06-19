// ── runtime control ────────────────────────────────────────────────────────
let cmdESS=null, _lastRuntimeAction=null;

function initControl(){loadCtrlStatus();connectCmdStream()}
async function loadCtrlStatus(){try{const d=await api('/api/runtime/status');updateCtrlBtns(d)}catch(e){}}
function connectCmdStream(){
  if(cmdESS)return;
  cmdESS=new EventSource('/api/runtime/output/stream');
  const dot=document.getElementById('term-dot');
  cmdESS.onopen=()=>{if(dot)dot.classList.remove('off')};
  cmdESS.onerror=()=>{if(dot)dot.classList.add('off')};
  cmdESS.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.type!=='keepalive')appendTerm(m)}catch{}};
}
function appendTerm(m){
  const el=document.getElementById('term-body');if(!el)return;
  el.querySelector('.tcur')?.remove();
  if(m.type==='cmd_start'){
    const s=document.createElement('span');s.className='tl cmd';
    s.textContent='$ '+m.cmd+' ['+new Date(m.ts).toLocaleTimeString()+']';
    el.appendChild(s);document.getElementById('term-label').textContent='Running: '+m.cmd;
    document.getElementById('term-dot')?.classList.remove('off');loadCtrlStatus();
  }else if(m.type==='cmd_end'){
    const s=document.createElement('span');s.className='tl '+(m.ok?'ok':'err');
    s.textContent=m.ok?'✓ Done (exit 0)':'✗ Failed (exit '+m.exit_code+')';
    el.appendChild(s);document.getElementById('term-label').textContent=m.ok?'Last: success':'Last: FAILED';
    document.getElementById('term-dot')?.classList.add('off');setTimeout(loadCtrlStatus,1500);
    if(!m.ok)pushNotification('Runtime command failed (exit '+m.exit_code+')','error');
    else if(_lastRuntimeAction==='start'||_lastRuntimeAction==='restart'){
      // A fresh process load picked up any pending .env API-key change.
      fetch('/api/sarvam/keys/ack-restart',{method:'POST'}).then(()=>{loadApiKeyInfo?.();checkApiCredits?.()});
    }
  }else if(m.type==='line'){
    const s=document.createElement('span');s.className='tl';const t=m.text||'';
    if(t.includes('[OK]')||t.toLowerCase().includes('started'))s.style.color='#18b068';
    else if(t.includes('[FAIL]')||t.toLowerCase().includes('error'))s.style.color='#ef4444';
    else if(t.includes('Stopping')||t.includes('Killing'))s.style.color='#f59e0b';
    else s.style.color='#7ab8cc';
    s.textContent=t;el.appendChild(s);
  }else if(m.type==='error'){
    const s=document.createElement('span');s.className='tl err';s.textContent='✗ '+m.text;el.appendChild(s);
    pushNotification('Runtime error: '+m.text,'error');
  }
  const cur=document.createElement('span');cur.className='tcur';el.appendChild(cur);
  el.scrollTop=el.scrollHeight;
}
async function runtimeAction(action){
  _lastRuntimeAction=action;
  const el=document.getElementById('ctrl-status');
  if(el){el.textContent={start:'Starting…',stop:'Stopping…',restart:'Restarting…'}[action]||'Working…';el.style.color='var(--warn)'}
  ['btn-start','btn-stop','btn-restart'].forEach(id=>{const b=document.getElementById(id);if(b)b.disabled=true});
  try{
    const r=await fetch('/api/runtime/'+action,{method:'POST'});const d=await r.json();
    if(!d.ok){appendTerm({type:'error',text:d.reason||'rejected'});await loadCtrlStatus()}
  }catch(e){appendTerm({type:'error',text:String(e)});await loadCtrlStatus()}
}
function clearTerm(){const el=document.getElementById('term-body');el.innerHTML='<span class="tcur"></span>'}
