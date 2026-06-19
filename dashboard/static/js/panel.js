// ── conversation slide-over panel ───────────────────────────────────────────
// Shared component opened from Live Calls ("View Conversation"). For an
// active call it streams transcript_tail updates from the new live-state SSE
// endpoint (added alongside the Phase 2 backend instrumentation); for a
// completed call it reuses the existing recordings/transcript endpoint
// exactly as the Conversations tab already does — zero backend change there.

let _spMode=null;        // 'active' | 'past'
let _spConnId=null;
let _spFolder=null;
let _spMessages=[];      // [{speaker, text, timestamp}]
let _spLastTail=[];      // raw transcript_tail from the last live-state update
let _spSSE=null;
let _spDurTimer=null;
let _spStartTime=null;
let _spAutoscroll=true;
let _spQuery='';
let _spCallerLabel='';
let _spTriggerEl=null;

function _spFocusableEls(){
  return Array.from(document.getElementById('sp-panel').querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  )).filter(el=>!el.disabled && el.offsetParent!==null);
}
function _spTrapKeydown(e){
  if(e.key!=='Tab')return;
  const items=_spFocusableEls();
  if(!items.length)return;
  const first=items[0], last=items[items.length-1];
  if(e.shiftKey && document.activeElement===first){e.preventDefault();last.focus()}
  else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first.focus()}
}

function _spDiffTail(oldTail,newTail){
  for(let k=0;k<=oldTail.length;k++){
    const oldSuffix=oldTail.slice(k);
    const newPrefix=newTail.slice(0,oldSuffix.length);
    if(JSON.stringify(oldSuffix)===JSON.stringify(newPrefix)){
      return newTail.slice(oldSuffix.length);
    }
  }
  return newTail;
}

function openConvPanelActive(opts){
  _spTriggerEl=document.activeElement;
  _resetConvPanelState();
  _spMode='active';_spConnId=opts.connId;_spCallerLabel=opts.caller||opts.connId;
  _spStartTime=opts.startTime||null;
  document.getElementById('sp-title').textContent=_spCallerLabel;
  _renderSpMeta({connId:opts.connId,language:opts.language,stage:opts.stage,live:true});
  document.getElementById('sp-foot-status').textContent='● Live';
  _openConvBackdrop();
  if(_spStartTime){
    _spDurTimer=setInterval(()=>{
      const chip=document.getElementById('sp-meta-dur');
      if(chip)chip.textContent='⏱ '+elapsed(_spStartTime);
    },1000);
  }
  _spSSE=new EventSource('/api/calls/live-state/stream');
  _spSSE.onmessage=e=>{
    try{
      const m=JSON.parse(e.data);
      if(m.type!=='live_state')return;
      const call=(m.calls||[]).find(c=>c.conn_id===_spConnId);
      if(!call){
        // Call no longer active — it ended.
        document.getElementById('sp-foot-status').textContent='Call ended';
        if(_spSSE){_spSSE.close();_spSSE=null}
        if(_spDurTimer){clearInterval(_spDurTimer);_spDurTimer=null}
        return;
      }
      _renderSpMeta({connId:_spConnId,language:call.language,stage:call.stage,live:true});
      const tail=call.transcript_tail||[];
      const fresh=_spDiffTail(_spLastTail,tail);
      _spLastTail=tail;
      if(fresh.length){
        fresh.forEach(t=>_spMessages.push({speaker:t.speaker,text:t.text,timestamp:null}));
        _renderSpMessages();
      }
    }catch{}
  };
}

async function openConvPanelPast(opts){
  _spTriggerEl=document.activeElement;
  _resetConvPanelState();
  _spMode='past';_spFolder=opts.folder;_spCallerLabel=opts.caller||opts.folder;
  document.getElementById('sp-title').textContent=_spCallerLabel;
  _renderSpMeta({startedAt:opts.dt,dur:opts.dur,language:opts.lang,live:false});
  document.getElementById('sp-foot-status').textContent='Completed call';
  _openConvBackdrop();
  document.getElementById('sp-body').innerHTML='<div style="text-align:center;padding:60px"><div class="spin"></div></div>';
  try{
    const d=await api('/api/calls/recordings/'+encodeURIComponent(opts.folder)+'/transcript');
    _spMessages=(d.messages||[]).map(m=>({speaker:m.speaker,text:m.text,timestamp:m.timestamp}));
    _renderSpMessages();
  }catch(e){
    document.getElementById('sp-body').innerHTML='<div class="conv-empty" style="color:var(--error)">⚠ Error loading transcript</div>';
  }
}

function _renderSpMeta({connId,startedAt,dur,language,stage,live}){
  const el=document.getElementById('sp-meta');
  const chips=[];
  if(connId)chips.push(`<span class="sp-meta-item mono">${esc(shortId(connId))}</span>`);
  if(live&&_spStartTime)chips.push(`<span class="sp-meta-item" id="sp-meta-dur">⏱ ${elapsed(_spStartTime)}</span>`);
  if(!live&&startedAt)chips.push(`<span class="sp-meta-item">${esc(fmtTs(startedAt))}</span>`);
  if(!live&&dur)chips.push(`<span class="sp-meta-item">⏱ ${fmtDur(Number(dur))}</span>`);
  if(language)chips.push(`<span class="sp-meta-item">${esc(String(language).toUpperCase())}</span>`);
  if(live){
    const idx=stageIndex(stage);
    chips.push(`<span class="sp-meta-item">${idx>=0?esc(STAGE_STEPS[idx].label):'—'}</span>`);
  }
  el.innerHTML=chips.join('');
}

function _renderSpMessages(){
  const body=document.getElementById('sp-body');
  const q=_spQuery.trim().toLowerCase();
  const hl=(text)=>{
    if(!q)return esc(text);
    const idx=text.toLowerCase().indexOf(q);
    if(idx<0)return esc(text);
    return esc(text.slice(0,idx))+'<mark class="sp-hit">'+esc(text.slice(idx,idx+q.length))+'</mark>'+esc(text.slice(idx+q.length));
  };
  const filtered=q?_spMessages.filter(m=>(m.text||'').toLowerCase().includes(q)):_spMessages;
  if(!filtered.length){
    body.innerHTML=`<div class="conv-empty"><span style="font-size:24px">${q?'🔍':'💬'}</span><span>${q?'No matches':'Waiting for transcript…'}</span></div>`;
  }else{
    body.innerHTML=filtered.map(m=>{
      const cls=m.speaker==='Agent'?'ag':'ca';
      return `<div class="msg ${cls}">
        <div class="msg-lbl">${esc(m.speaker)}</div>
        <div class="msg-bub">${hl(m.text||'')}</div>
        ${m.timestamp?`<div class="msg-ts">${esc(m.timestamp)}</div>`:''}
      </div>`;
    }).join('');
  }
  document.getElementById('sp-foot-count').textContent=_spMessages.length+' turn'+(_spMessages.length!==1?'s':'');
  if(_spAutoscroll)body.scrollTop=body.scrollHeight;
}

function searchConvPanel(){
  _spQuery=document.getElementById('sp-search').value||'';
  _renderSpMessages();
}

function toggleConvAutoscroll(){
  _spAutoscroll=!_spAutoscroll;
  document.getElementById('sp-autoscroll-btn').textContent=_spAutoscroll?'⏸ Pause auto-scroll':'▶ Resume auto-scroll';
  if(_spAutoscroll){
    const body=document.getElementById('sp-body');body.scrollTop=body.scrollHeight;
  }
}

function copyConvPanel(){
  const text=_spMessages.map(m=>`${m.speaker}: ${m.text}`).join('\n');
  navigator.clipboard?.writeText(text).then(()=>{
    pushNotification('Transcript copied to clipboard','info');
  }).catch(()=>pushNotification('Could not copy transcript','warn'));
}

function exportConvPanel(){
  const text=_spMessages.map(m=>`${m.speaker}: ${m.text}`).join('\n');
  const blob=new Blob([text],{type:'text/plain'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=(_spCallerLabel||'transcript').replace(/[^a-z0-9_-]/gi,'_')+'.txt';
  document.body.appendChild(a);a.click();a.remove();
  URL.revokeObjectURL(url);
}

function _openConvBackdrop(){
  document.getElementById('sp-search').value='';
  const body=document.getElementById('sp-body');
  body.innerHTML='';
  // Detect manual scroll-up to auto-pause autoscroll (spec requirement).
  body.onscroll=()=>{
    const atBottom=body.scrollHeight-body.scrollTop-body.clientHeight<24;
    if(!atBottom&&_spAutoscroll){
      _spAutoscroll=false;
      document.getElementById('sp-autoscroll-btn').textContent='▶ Resume auto-scroll';
    }
  };
  document.getElementById('sp-backdrop').classList.add('open');
  document.addEventListener('keydown',_spTrapKeydown);
  // Focus the close button (first meaningful focusable element) so keyboard
  // users land inside the dialog immediately, per standard dialog pattern.
  document.getElementById('sp-panel').querySelector('.sp-close')?.focus();
}

function _resetConvPanelState(){
  if(_spSSE){_spSSE.close();_spSSE=null}
  if(_spDurTimer){clearInterval(_spDurTimer);_spDurTimer=null}
  _spMode=null;_spConnId=null;_spFolder=null;_spMessages=[];_spLastTail=[];
  _spStartTime=null;_spAutoscroll=true;_spQuery='';
}

function closeConvPanel(){
  document.getElementById('sp-backdrop').classList.remove('open');
  document.removeEventListener('keydown',_spTrapKeydown);
  _resetConvPanelState();
  _spTriggerEl?.focus?.();
  _spTriggerEl=null;
}

document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&document.getElementById('sp-backdrop').classList.contains('open'))closeConvPanel();
});
