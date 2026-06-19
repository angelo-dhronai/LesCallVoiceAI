// ── health ─────────────────────────────────────────────────────────────────
async function loadHealth(){
  try{
    const d=await api('/api/runtime/status');
    const ok=d.overall==='healthy',svcs=d.services||{},ports=d.ports||{};
    const ov=document.getElementById('h-overall');ov.textContent=ok?'✓ Healthy':'⚠ Degraded';ov.className='stat-val '+(ok?'g':'a');
    document.getElementById('h-checked').textContent='Checked '+fmtTs(d.checked_at);
    const run=Object.values(svcs).filter(s=>s.running).length,tot=Object.keys(svcs).length;
    document.getElementById('h-svc-count').textContent=run+' / '+tot;
    document.getElementById('h-svc-count').className='stat-val '+(run===tot&&tot>0?'g':'a');
    const pu=Object.values(ports).filter(Boolean).length;
    document.getElementById('h-port-count').textContent=pu+' / '+Object.keys(ports).length;
    document.getElementById('h-port-count').className='stat-val '+(pu===Object.keys(ports).length?'g':'a');
    const SL={socket_server:{label:'Socket Server (ESL)',icon:'🔌',detail:'Port 9000 · FreeSWITCH ESL controller'},ws_audio_receiver:{label:'WS Audio Receiver',icon:'🎙',detail:'Port 9001 · WebSocket media bridge + AI pipeline'}};
    const body=document.getElementById('health-svc-body');
    body.innerHTML=Object.keys(svcs).length
      ?Object.entries(svcs).map(([n,s])=>{const i=SL[n]||{label:n,icon:'⚙',detail:'PID: '+s.pid},up=s.running;return`<div class="svc-row"><div class="svc-ic ${up?'up':'dn'}">${i.icon}</div><div><div class="svc-nm">${i.label}</div><div class="svc-dt">${i.detail}</div><div class="svc-dt" style="margin-top:3px">PID: <span class="mono">${s.pid}</span></div></div><div class="svc-st">${up?'<span class="badge bg">● Running</span>':'<span class="badge br">✕ Stopped</span>'}</div></div>`}).join('')
      :'<div style="padding:24px;color:var(--text-d);text-align:center">No service data — runtime may not be started.</div>';
    const PI={'9000':'socket_server.py (ESL)','9001':'ws_audio_receiver.py (Media WS)'};
    document.getElementById('health-ports').innerHTML=Object.entries(PI).map(([p,s])=>`<tr><td class="tm">${p}</td><td>${s}</td><td>${ports[p]?'<span class="badge bg">● Listening</span>':'<span class="badge br">✕ Not listening</span>'}</td></tr>`).join('');
    document.getElementById('h-logdir').textContent=d.logs_dir||'—';
    renderInfraGrid(d);
    stamp();
  }catch(e){console.error(e)}
}

// Infrastructure status cards. Only FreeSWITCH-bridge and AI-pipeline are
// actually probed by /api/runtime/status (via process + port checks); STT,
// TTS and LLM have no independent health probe anywhere in this stack, so
// they're shown as "Not monitored" rather than invented as healthy. The
// Dashboard card is tautologically "Online" — if you're seeing this render,
// this same process answered the request.
function renderInfraGrid(d){
  const svcs=d.services||{};
  const cards=[
    {label:'FreeSWITCH ESL Bridge',icon:'🔌',state:svcs.socket_server?.running?'up':(svcs.socket_server?'down':'unknown')},
    {label:'AI Pipeline',icon:'🎙',state:svcs.ws_audio_receiver?.running?'up':(svcs.ws_audio_receiver?'down':'unknown')},
    {label:'STT Provider',icon:'🗣',state:'unknown'},
    {label:'TTS Provider',icon:'🔊',state:'unknown'},
    {label:'LLM',icon:'🧠',state:'unknown'},
    {label:'Dashboard',icon:'🖥',state:'up'},
  ];
  const STATE_TXT={up:'Online',down:'Offline',unknown:'Not monitored'};
  document.getElementById('infra-grid').innerHTML=cards.map(c=>`
    <div class="infra-card">
      <div class="infra-ic ${c.state}">${c.icon}</div>
      <div><div class="infra-nm">${esc(c.label)}</div><div class="infra-st ${c.state}">${STATE_TXT[c.state]}</div></div>
    </div>`).join('');
}
