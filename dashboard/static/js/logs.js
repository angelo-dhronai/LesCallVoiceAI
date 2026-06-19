// ── logs ───────────────────────────────────────────────────────────────────
let logESS=null,logFilter='all',logAS=true,logLines=[];
const MAXLOG=1000;

function initLogs(){if(logESS)return;connectLogs()}
function connectLogs(){
  logESS=new EventSource('/api/logs/stream?categories='+(logFilter==='all'?'all':logFilter));
  const dot=document.getElementById('log-dot'),st=document.getElementById('log-status');
  logESS.onopen=()=>{dot.classList.remove('off');st.textContent='Live'};
  logESS.onerror=()=>{dot.classList.add('off');st.textContent='Reconnecting…'};
  logESS.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.type==='log')appendLog(m.line,m.color,m.category)}catch{}};
}
function appendLog(line,color,cat){
  if(logFilter!=='all'&&cat!==logFilter)return;
  const el=document.getElementById('log-body'),span=document.createElement('span');
  span.className='log-ln';span.style.color=color||'#7ab8cc';span.textContent=line;
  el.appendChild(span);logLines.push(span);
  if(logLines.length>MAXLOG){const o=logLines.shift();o?.parentNode?.removeChild(o)}
  if(logAS)el.scrollTop=el.scrollHeight;
}
function toggleLogFilter(btn){
  const cat=btn.dataset.cat;
  document.querySelectorAll('.fchip[data-cat]').forEach(b=>b.className='fchip');
  btn.classList.add('on-'+cat);logFilter=cat;
  if(logESS){logESS.close();logESS=null}clearLogs();connectLogs();
}
function clearLogs(){document.getElementById('log-body').innerHTML='';logLines=[]}
function toggleAS(){logAS=!logAS;document.getElementById('as-btn').textContent='↓ Auto: '+(logAS?'ON':'OFF')}
