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

function buildLiveCardHtml(call){
  const caller=call.caller_number?'+'+String(call.caller_number).replace(/\D/g,''):'Unknown';
  const dur=call.start_time?elapsed(call.start_time):'—';
  const sp=SPEAK_MAP[call.speaking]||SPEAK_MAP.listening;
  const idx=stageIndex(call.stage);
  const stageHtml=STAGE_STEPS.map((s,i)=>{
    const cls=i<idx?'done':(i===idx?'current':'');
    return `<div class="stage-step ${cls}"><div class="dotline"></div><div class="stage-lbl">${s.label}</div></div>`;
  }).join('');
  const tail=call.transcript_tail||[];
  const last=tail[tail.length-1];
  const previewHtml=last
    ?`<span class="pv-spk">${esc(last.speaker)}:</span>${esc((last.text||'').slice(0,140))}`
    :'No transcript yet…';
  const language=call.language?String(call.language).toUpperCase():null;
  return `
    <div class="lc-head">
      <div><div class="lc-caller">${esc(caller)}</div><div class="lc-num mono">${esc(shortId(call.conn_id))}</div></div>
      <div class="lc-dur">${dur}</div>
    </div>
    <div class="lc-meta">
      <span class="speak-ind ${sp.cls}"><span class="sdot2"></span>${sp.label}</span>
      ${language?`<span class="badge bz">${esc(language)}</span>`:''}
    </div>
    <div class="stage-strip">${stageHtml}</div>
    <div class="lc-preview">${previewHtml}</div>
    <div class="lc-foot"><button class="btn btn-gh btn-sm" onclick="openConvPanelFromLive('${esc(call.conn_id)}')">💬 View Conversation</button></div>
  `;
}

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
    grid.innerHTML='<div class="live-empty" style="grid-column:1/-1">No active calls right now</div>';
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
}
