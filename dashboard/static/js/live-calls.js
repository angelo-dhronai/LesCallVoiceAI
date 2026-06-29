// ── live calls (Phase 2: card grid fed by the new live-state snapshot,
//    sourced from real per-call pipeline state — stage, language, who's
//    speaking, rolling transcript tail. See ws_audio_receiver.py's
//    _live_state_writer_loop() and dashboard/server.py's /api/calls/live-state*) ──
let _liveSSE=null;
let _liveCardSig=new Map();   // conn_id -> last-rendered HTML signature (skip unchanged DOM writes)
let _liveCallsByConnId=new Map();

const SPEAK_MAP={
  ai:{cls:'ai',label:'AI Speaking'},
  user:{cls:'user',label:'Caller Speaking'},
  listening:{cls:'listening',label:'Listening'},
};

function liveInitial(caller){
  const d=String(caller||'').replace(/\D/g,'');
  return d?d.slice(-2):'?';
}

function buildLiveCardHtml(call){
  const caller=call.caller_number?'+'+String(call.caller_number).replace(/\D/g,''):'Unknown';
  const sp=SPEAK_MAP[call.speaking]||SPEAK_MAP.listening;
  const idx=stageIndex(call.stage);
  const total=STAGE_STEPS.length;
  const pct=idx<0?0:Math.round(((idx+1)/total)*100);
  const stageHtml=STAGE_STEPS.map((s,i)=>{
    const cls=i<idx?'done':(i===idx?'current':'');
    return `<div class="stage-step ${cls}"><div class="dotline"></div><div class="stage-lbl">${s.label}</div></div>`;
  }).join('');
  const curStage=idx>=0?STAGE_STEPS[idx].label:'—';
  const tail=call.transcript_tail||[];
  const last=tail[tail.length-1];
  const previewHtml=last
    ?`<span class="pv-spk">${esc(last.speaker)}:</span>${esc((last.text||'').slice(0,140))}`
    :'<span class="pv-wait">Waiting for first transcript…</span>';
  const language=call.language?String(call.language).toUpperCase():null;
  // Duration lives in its own node updated by the 1s ticker (data-start) so it
  // ticks smoothly regardless of how often the live-state stream pushes — and
  // it's excluded from the render signature so the card doesn't churn each tick.
  return `
    <div class="lc-head">
      <div class="lc-id">
        <div class="lc-avatar">${esc(liveInitial(call.caller_number))}</div>
        <div><div class="lc-caller">${esc(caller)}</div><div class="lc-num mono">${esc(shortId(call.conn_id))}</div></div>
      </div>
      <div class="lc-live"><span class="lc-live-dot"></span><span class="lc-dur" data-start="${esc(call.start_time||'')}">${call.start_time?elapsed(call.start_time):'—'}</span></div>
    </div>
    <div class="lc-meta">
      <span class="speak-ind ${sp.cls}"><span class="sdot2"></span>${sp.label}</span>
      ${language?`<span class="badge bz">${esc(language)}</span>`:''}
      <span class="lc-stagepill">${esc(curStage)} · ${pct}%</span>
    </div>
    <div class="stage-strip">${stageHtml}</div>
    <div class="lc-preview">${previewHtml}</div>
    <div class="lc-foot"><button class="btn btn-gh btn-sm" onclick="openConvPanelFromLive('${esc(call.conn_id)}')">💬 View Conversation</button></div>
  `;
}

// Independent 1-second clock that advances every visible call timer without
// re-rendering the card (the live stream may not push every second).
let _liveTicker=null;
function _tickLiveDurations(){
  document.querySelectorAll('#live-grid .lc-dur[data-start]').forEach(el=>{
    const st=el.getAttribute('data-start');
    if(st)el.textContent=elapsed(st);
  });
}
function startLiveTicker(){if(!_liveTicker)_liveTicker=setInterval(_tickLiveDurations,1000)}

function openConvPanelFromLive(connId){
  const call=_liveCallsByConnId.get(connId);
  if(!call)return;
  const caller=call.caller_number?'+'+String(call.caller_number).replace(/\D/g,''):'Unknown';
  openConvPanelActive({connId,caller,startTime:call.start_time,language:call.language,stage:call.stage});
}

function renderLiveCalls(calls){
  const grid=document.getElementById('live-grid');
  _liveCallsByConnId=new Map(calls.map(c=>[c.conn_id,c]));
  document.getElementById('nav-active').textContent=calls.length;
  if(!calls.length){
    grid.innerHTML='<div class="live-empty" style="grid-column:1/-1"><div style="font-size:34px;opacity:.5">📞</div><div style="font-weight:600;color:var(--text-s);margin-top:8px">No active calls right now</div><div style="font-size:12px;margin-top:4px">Live calls will appear here the moment one connects.</div></div>';
    _liveCardSig.clear();
    return;
  }
  const seen=new Set();
  calls.forEach(call=>{
    const id=call.conn_id;
    seen.add(id);
    const html=buildLiveCardHtml(call);
    if(_liveCardSig.get(id)===html)return; // unchanged — skip DOM write entirely
    _liveCardSig.set(id,html);
    let el=Array.from(grid.children).find(c=>c.dataset&&c.dataset.connId===id);
    if(!el){
      el=document.createElement('div');
      el.className='live-card';
      el.dataset.connId=id;
      grid.appendChild(el);
    }
    el.innerHTML=html;
  });
  Array.from(grid.children).forEach(el=>{
    const id=el.dataset&&el.dataset.connId;
    if(id&&!seen.has(id)){el.remove();_liveCardSig.delete(id)}
  });
}

function connectLiveSSE(){
  if(_liveSSE)return;
  _liveSSE=new EventSource('/api/calls/live-state/stream');
  _liveSSE.onopen=()=>{document.getElementById('live-updated').textContent='Live · updates streaming'};
  _liveSSE.onerror=()=>{document.getElementById('live-updated').textContent='Reconnecting… (falling back to periodic refresh)'};
  _liveSSE.onmessage=e=>{
    try{
      const m=JSON.parse(e.data);
      if(m.type!=='live_state')return;
      renderLiveCalls(m.calls||[]);
      document.getElementById('live-updated').textContent='Live · updated '+new Date().toLocaleTimeString();
    }catch{}
  };
}

async function loadLive(){
  try{
    const d=await api('/api/calls/live-state');
    renderLiveCalls(d.calls||[]);
  }catch(e){console.error(e)}
  connectLiveSSE();
  startLiveTicker();
}
