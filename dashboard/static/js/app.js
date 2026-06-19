// ── dark mode (persisted) ──────────────────────────────────────────────────
(function(){
  const t=localStorage.getItem('lc-theme')||'light';
  document.documentElement.setAttribute('data-theme',t);
  const b=document.getElementById('theme-btn');
  if(b) b.textContent=t==='dark'?'☀️':'🌙';
})();
function toggleTheme(){
  const cur=document.documentElement.getAttribute('data-theme');
  const nxt=cur==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme',nxt);
  localStorage.setItem('lc-theme',nxt);
  document.getElementById('theme-btn').textContent=nxt==='dark'?'☀️':'🌙';
}

// ── shared: conversation stage steps (mirrors conversation.py's STAGE_*
//    constants — see CLAUDE.md's GREETING→DISCOVERY→PROJECT_MATCH→
//    INVITE_VISIT→CONFIRMATION→CLOSING flow) ────────────────────────────────
const STAGE_STEPS=[
  {key:'GREETING',label:'Greeting'},
  {key:'DISCOVERY',label:'Discovery'},
  {key:'PROJECT_MATCH',label:'Match'},
  {key:'INVITE_VISIT',label:'Schedule'},
  {key:'CONFIRMATION',label:'Confirm'},
  {key:'CLOSING',label:'Close'},
];
function stageIndex(stage){return STAGE_STEPS.findIndex(s=>s.key===stage)}

// ── state ──────────────────────────────────────────────────────────────────
const TABS={overview:'Overview','live-calls':'Live Calls',conversations:'Call Transcripts',
  history:'Call Activity',recordings:'Recordings',followups:'Follow-Up Tracker',
  analytics:'Analytics',logs:'Live Logs',outbound:'Outbound Calls',
  'ai-analysis':'AI Call Analysis','conv-analytics':'Conversation Analytics',
  'followup-automation':'Follow-Up Automation',
  sarvam:'API Usage',control:'Runtime Control',health:'Health','agent-settings':'Agent Settings'};
let curTab='overview';

// ── nav ────────────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-item[data-tab]').forEach(el=>{
  el.addEventListener('click',()=>{switchTab(el.dataset.tab);toggleMobileNav(false)});
});

// Mobile sidebar — below 768px the sidebar is hidden off-canvas by default
// (see layout.css); this is the only way back in on a phone-width screen.
function toggleMobileNav(force){
  const sidebar=document.getElementById('sidebar');
  const backdrop=document.getElementById('mobile-nav-backdrop');
  const open=force!==undefined?force:!sidebar.classList.contains('mobile-open');
  sidebar.classList.toggle('mobile-open',open);
  backdrop.classList.toggle('open',open);
}
function switchTab(n){
  document.querySelectorAll('.nav-item').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(e=>e.classList.remove('active'));
  document.querySelector(`.nav-item[data-tab="${n}"]`)?.classList.add('active');
  document.getElementById('tab-'+n)?.classList.add('active');
  document.getElementById('topbar-title').textContent=TABS[n]||n;
  curTab=n; onActivate(n);
}
function onActivate(t){
  if(t==='overview')loadOverview();
  else if(t==='live-calls')loadLive();
  else if(t==='conversations')loadConversations();
  else if(t==='history')loadHistory();
  else if(t==='recordings')loadRecordings();
  else if(t==='followups')loadFollowups();
  else if(t==='ai-analysis')loadAiAnalysisList();
  else if(t==='conv-analytics')loadConvAnalytics();
  else if(t==='followup-automation')loadAutomationList();
  else if(t==='analytics')loadAnalytics();
  else if(t==='logs')initLogs();
  else if(t==='outbound')loadOutbound();
  else if(t==='sarvam'){loadSarvam('today',document.querySelector('.ptab.active'));checkApiCredits();loadApiKeyInfo()}
  else if(t==='control')initControl();
  else if(t==='health')loadHealth();
  else if(t==='agent-settings')loadAgentSettings();
}

// ── helpers ────────────────────────────────────────────────────────────────
const R=(n,d=2)=>(n==null||isNaN(n))?'—':Number(n).toFixed(d);
const RP=(n)=>(n==null||isNaN(n))?'—':'₹'+Number(n).toFixed(4);
function fmtDur(s){if(!s||s<=0)return'—';const m=Math.floor(s/60),sc=Math.floor(s%60);return m+':'+String(sc).padStart(2,'0')}
function fmtTs(ts){if(!ts)return'—';try{return new Date(ts).toLocaleString('en-IN',{dateStyle:'short',timeStyle:'short'})}catch{return ts.slice(0,16)}}
function shortId(id){return id?id.slice(0,8)+'…':'—'}
function elapsed(st){try{return fmtDur((Date.now()-new Date(st).getTime())/1000)}catch{return'—'}}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
async function api(p,opts){const r=await fetch(p,opts);if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}
function stamp(){document.getElementById('last-refresh').textContent='Updated '+new Date().toLocaleTimeString()}

/* small client-side table sort helper: rows = array of objects, colKey = field,
   dir = 'asc'|'desc'. Used by History/Outbound tables — purely a presentation
   helper, does not touch how data is fetched. */
function sortRows(rows,colKey,dir){
  const sign=dir==='desc'?-1:1;
  return [...rows].sort((a,b)=>{
    let av=a[colKey],bv=b[colKey];
    if(av==null)av='';if(bv==null)bv='';
    if(typeof av==='number'&&typeof bv==='number')return(av-bv)*sign;
    return String(av).localeCompare(String(bv))*sign;
  });
}
function bindSortableHeaders(theadEl,onSort){
  theadEl.querySelectorAll('th[data-sort]').forEach(th=>{
    th.classList.add('sortable');
    if(!th.querySelector('.sort-ic')){
      const ic=document.createElement('span');ic.className='sort-ic';ic.textContent='↕';th.appendChild(ic);
    }
    th.onclick=()=>{
      const key=th.dataset.sort;
      const wasAsc=th.classList.contains('sort-asc');
      theadEl.querySelectorAll('th').forEach(t=>t.classList.remove('sort-asc','sort-desc'));
      const dir=wasAsc?'desc':'asc';
      th.classList.add(dir==='asc'?'sort-asc':'sort-desc');
      onSort(key,dir);
    };
  });
}

// ── header: live clock ────────────────────────────────────────────────────
function tickClock(){
  const el=document.getElementById('topbar-clock');
  if(el)el.textContent=new Date().toLocaleString('en-IN',{dateStyle:'medium',timeStyle:'medium'});
}
setInterval(tickClock,1000);tickClock();

// ── header: status chips (sourced from existing /api/runtime/status — no new
//    backend fields; anything not actually probed by that endpoint is shown
//    as "Unknown" rather than invented as healthy) ─────────────────────────
function updateStatusChips(d){
  const wrap=document.getElementById('status-chips');
  if(!wrap)return;
  const overallUp=d?.overall==='healthy';
  const svcs=d?.services||{};
  const aiUp=!!svcs.ws_audio_receiver?.running;
  const telUp=!!d?.ports?.['9000'];
  const chips=[
    {label:'System',up:overallUp,known:true},
    {label:'AI Pipeline',up:aiUp,known:true},
    {label:'Telephony',up:telUp,known:true},
    {label:'STT',up:null,known:false},
    {label:'TTS',up:null,known:false},
  ];
  wrap.innerHTML=chips.map(c=>{
    const cls=!c.known?'unknown':(c.up?'up':'down');
    const txt=!c.known?c.label+' · Not monitored':c.label+(c.up?' Online':' Offline');
    return `<span class="schip ${cls}" title="${!c.known?'No independent health probe exists for this service yet':''}"><span class="sdot"></span>${esc(txt)}</span>`;
  }).join('');
}

// ── header: search (filters sidebar nav items client-side) ────────────────
function onTopbarSearch(){
  const q=(document.getElementById('topbar-search')?.value||'').toLowerCase().trim();
  document.querySelectorAll('.nav-item[data-tab]').forEach(el=>{
    const label=el.textContent.toLowerCase();
    el.classList.toggle('nav-hidden', q.length>0 && !label.includes(q));
  });
}
document.addEventListener('keydown',e=>{
  if(e.key==='/' && document.activeElement?.tagName!=='INPUT' && document.activeElement?.tagName!=='TEXTAREA'){
    e.preventDefault();document.getElementById('topbar-search')?.focus();
  }else if(e.key==='Escape' && document.activeElement?.id==='topbar-search'){
    document.activeElement.value='';onTopbarSearch();document.activeElement.blur();
  }
});

// ── header: quick actions + notifications popovers ─────────────────────────
function togglePopover(id){
  const el=document.getElementById(id);
  const isOpen=el.classList.contains('open');
  document.querySelectorAll('.popover').forEach(p=>p.classList.remove('open'));
  if(!isOpen)el.classList.add('open');
}
document.addEventListener('click',e=>{
  if(!e.target.closest('.topbar-right'))document.querySelectorAll('.popover').forEach(p=>p.classList.remove('open'));
});

let _notifications=[];
function pushNotification(text,type){
  _notifications.unshift({text,type:type||'info',ts:new Date().toISOString()});
  if(_notifications.length>20)_notifications.length=20;
  renderNotifications();
  const dot=document.getElementById('bell-dot');if(dot)dot.classList.add('show');
}
function renderNotifications(){
  const el=document.getElementById('notif-list');
  if(!el)return;
  if(!_notifications.length){el.innerHTML='<div class="popover-empty">No notifications yet</div>';return}
  el.innerHTML=_notifications.map(n=>`<div class="notif-item"><div style="color:${n.type==='error'?'var(--error)':n.type==='warn'?'var(--warn)':'var(--text)'}">${esc(n.text)}</div><div class="nt-time">${new Date(n.ts).toLocaleTimeString()}</div></div>`).join('');
}
function clearNotifications(){
  document.getElementById('bell-dot')?.classList.remove('show');
}

// ── health poll ────────────────────────────────────────────────────────────
let _lastOverall=null;
async function pollHealth(){
  try{
    const d=await api('/api/runtime/status');
    const ok=d.overall==='healthy';
    const dot=document.getElementById('sidebar-dot');
    dot.className='rdot '+(ok?'healthy':'degraded');
    document.getElementById('sidebar-status').textContent=ok?'Healthy':'Degraded';
    updateCtrlBtns(d);
    updateStatusChips(d);
    if(_lastOverall!==null && _lastOverall!==d.overall){
      pushNotification('System status changed: '+(ok?'Healthy':'Degraded'), ok?'info':'warn');
    }
    _lastOverall=d.overall;
  }catch(e){}
}
setInterval(pollHealth,10000);
// Independent of which tab is active — low/zero API-credit warnings must
// fire even if the user isn't looking at the API Usage tab.
setInterval(()=>{typeof checkApiCredits==='function'&&checkApiCredits()},30000);

function updateCtrlBtns(d){
  const running=d.services&&Object.values(d.services).some(s=>s.running);
  const bs=document.getElementById('btn-start'),bst=document.getElementById('btn-stop'),br=document.getElementById('btn-restart');
  if(bs)bs.disabled=running||d.cmd_running;
  if(bst)bst.disabled=!running||d.cmd_running;
  if(br)br.disabled=d.cmd_running;
  const el=document.getElementById('ctrl-status');
  if(el){
    if(d.cmd_running){el.textContent='Running command…';el.style.color='var(--warn)'}
    else if(running){el.textContent='● Online';el.style.color='var(--success)'}
    else{el.textContent='○ Offline';el.style.color='var(--error)'}
  }
  const p9=d.ports?.['9000'],p1=d.ports?.['9001'];
  const e9=document.getElementById('qi-9000'),e1=document.getElementById('qi-9001');
  if(e9){e9.className='badge '+(p9?'bg':'br');e9.textContent=p9?'● UP':'✕ DOWN'}
  if(e1){e1.className='badge '+(p1?'bg':'br');e1.textContent=p1?'● UP':'✕ DOWN'}
  const ld=document.getElementById('qi-logdir');if(ld&&d.logs_dir)ld.textContent=d.logs_dir;
  const lc=document.getElementById('qi-lastcmd');if(lc)lc.textContent=d.cmd_last?d.cmd_last+(d.cmd_exit!=null?' (exit '+d.cmd_exit+')':''):'—';
}

// ── bootstrap ──────────────────────────────────────────────────────────────
// Note: the initial pollHealth()/loadOverview() call is fired from the inline
// bootstrap script at the end of index.html, AFTER every tab's JS file (which
// define loadOverview etc.) has loaded — this file loads first in script order.
function refreshAll(){onActivate(curTab)}
setInterval(()=>{
  pollHealth();
  if(['overview','live-calls','health','control'].includes(curTab))onActivate(curTab);
  // Auto-refresh conversations every 15s when on that tab (so new calls appear)
  if(curTab==='conversations')loadConversations();
},15000);
