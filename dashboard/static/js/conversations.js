// ── conversations ──────────────────────────────────────────────────────────
let convData=[];

async function loadConversations(){
  const li=document.getElementById('conv-list-items');
  li.innerHTML='<div style="padding:32px;text-align:center;color:var(--text-d)"><div class="spin" style="margin:0 auto 8px"></div>Loading…</div>';
  try{
    // Sorted newest-first by date (server-side fix). Short TTL (15s) means new calls appear fast.
    const d=await api('/api/calls/recordings?limit=100&page=1');
    convData=d.recordings||[];
    const lc=document.getElementById('conv-list-count');
    if(lc) lc.textContent=convData.length+' recordings';
    const hadActive=!!document.querySelector('.conv-item.active');
    renderConvList(convData);
    // Auto-load newest call on first tab open so the right panel is never blank.
    if(!hadActive&&convData.length){
      const firstItem=document.querySelector('.conv-item');
      if(firstItem)firstItem.click();
    }
  }catch(e){
    document.getElementById('conv-list-items').innerHTML='<div style="padding:24px;color:var(--error);text-align:center">Error loading</div>';
  }
}

function renderConvList(recs){
  const el=document.getElementById('conv-list-items');
  if(!recs.length){el.innerHTML='<div style="padding:32px;text-align:center;color:var(--text-d)">No recordings found</div>';return}
  el.innerHTML='';
  recs.forEach(r=>{
    const ph=r.caller&&r.caller!=='unknown'&&r.caller.length>4?'+'+r.caller.replace(/\D/g,''):r.caller||'Unknown';
    const dur=fmtDur(r.duration_seconds);
    const item=document.createElement('div');
    item.className='conv-item';
    item.innerHTML=`
      <div class="conv-caller">${esc(ph)}</div>
      <div class="conv-meta">
        <span>${fmtTs(r.folder_dt)}</span>
        ${dur!=='—'?`<span class="conv-tag">⏱ ${dur}</span>`:''}
        ${r.language_detected?`<span class="conv-tag">${r.language_detected.toUpperCase()}</span>`:''}
        ${r.transcript_count?`<span class="conv-tag">${r.transcript_count} turns</span>`:''}
      </div>`;
    item.addEventListener('click',()=>loadTranscript(item,r.folder,ph,r.folder_dt||'',r.duration_seconds||0,r.language_detected||''));
    el.appendChild(item);
  });
}

function filterConvList(){
  const q=document.getElementById('conv-search').value.toLowerCase().trim();
  renderConvList(q?convData.filter(r=>(r.caller||'').toLowerCase().includes(q)||(r.folder_dt||'').includes(q)):convData);
}

// Server-side full-text search across transcripts — covers product, pricing,
// budget, competitor, location, or any custom keyword the user types, since
// none of those map to a structured field today (only the raw transcript
// text exists), unlike the AI Call Analysis tab's structured topic/objection
// extraction.
let _convKeywordTimer=null;
async function filterConvByKeyword(){
  const q=document.getElementById('conv-keyword-search').value.trim();
  clearTimeout(_convKeywordTimer);
  if(!q){renderConvList(convData);return}
  _convKeywordTimer=setTimeout(async()=>{
    const li=document.getElementById('conv-list-items');
    li.innerHTML='<div style="padding:32px;text-align:center;color:var(--text-d)"><div class="spin" style="margin:0 auto 8px"></div>Searching…</div>';
    try{
      const d=await api('/api/calls/transcripts/search?q='+encodeURIComponent(q));
      renderConvList(d.recordings||[]);
    }catch(e){li.innerHTML='<div style="padding:24px;color:var(--error);text-align:center">Search error</div>'}
  },300);
}

async function loadTranscript(itemEl,folder,caller,dt,dur,lang){
  document.querySelectorAll('.conv-item').forEach(e=>e.classList.remove('active'));
  itemEl.classList.add('active');
  document.getElementById('conv-title').textContent=caller;
  document.getElementById('conv-sub').textContent=fmtTs(dt)+(dur?' · '+fmtDur(Number(dur)):'')+(lang?' · '+lang.toUpperCase():'');
  const msgs=document.getElementById('conv-msgs');
  msgs.innerHTML='<div style="text-align:center;padding:60px"><div class="spin"></div></div>';
  try{
    const d=await api('/api/calls/recordings/'+encodeURIComponent(folder)+'/transcript');
    if(!d.messages?.length){msgs.innerHTML='<div class="conv-empty"><span style="font-size:24px">📭</span><span>No transcript for this call</span></div>';return}
    msgs.innerHTML=d.messages.map(m=>{
      const cls=m.speaker==='Agent'?'ag':'ca';
      return`<div class="msg ${cls}">
        <div class="msg-lbl">${esc(m.speaker)}</div>
        <div class="msg-bub">${esc(m.text)}</div>
        ${m.timestamp?`<div class="msg-ts">${esc(m.timestamp)}</div>`:''}
      </div>`;
    }).join('');
    msgs.scrollTop=0;
  }catch(e){msgs.innerHTML='<div class="conv-empty" style="color:var(--error)"><span>⚠</span><span>Error loading transcript</span></div>';}
}
