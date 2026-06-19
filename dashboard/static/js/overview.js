// ── overview ───────────────────────────────────────────────────────────────
function trendBadge(curVal, baseline){
  if(curVal==null||baseline==null||isNaN(curVal)||isNaN(baseline)||baseline===0)return'';
  const pct=Math.round(((curVal-baseline)/baseline)*100);
  if(Math.abs(pct)<1)return'<span class="trend flat">→ 0%</span>';
  return pct>0?`<span class="trend up">↑ ${pct}%</span>`:`<span class="trend down">↓ ${Math.abs(pct)}%</span>`;
}

function sparklineSvg(values){
  if(!values||values.length<2)return'';
  const w=160,h=32,mx=Math.max(...values,1),mn=Math.min(...values,0);
  const range=(mx-mn)||1;
  const step=w/(values.length-1);
  const pts=values.map((v,i)=>[i*step,h-((v-mn)/range)*h]);
  const line=pts.map((p,i)=>(i===0?'M':'L')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
  const area=line+` L${w},${h} L0,${h} Z`;
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <path class="area" d="${area}"></path><path d="${line}"></path></svg>`;
}

async function loadOverview(period,btnEl){
  period=period||'today';
  if(btnEl){
    btnEl.parentElement.querySelectorAll('.ptab').forEach(b=>b.classList.remove('active'));
    btnEl.classList.add('active');
  }
  try{
    const d=await api('/api/overview?period='+encodeURIComponent(period));
    document.getElementById('s-today-calls').textContent=d.today.call_count??'0';
    document.getElementById('s-today-active').textContent=(d.active_calls??0)+' active now';
    document.getElementById('s-today-cost').textContent=RP(d.today.est_total_cost);
    document.getElementById('s-today-cost-sub').textContent='STT '+RP(d.today.est_stt_cost)+' · TTS '+RP(d.today.est_tts_cost);
    document.getElementById('s-month-cost').textContent=RP(d.month.est_total_cost);
    document.getElementById('s-month-calls').textContent=(d.month.call_count??0)+' calls in 30 days';
    document.getElementById('s-week-calls').textContent=d.week.call_count??'0';
    document.getElementById('s-week-cost').textContent=RP(d.week.est_total_cost)+' est. total';
    document.getElementById('s-stt-sec').textContent=R(d.today.stt_sarvam_billed_seconds,1)+'s billed';
    document.getElementById('s-stt-cost').textContent=RP(d.today.est_stt_cost)+' (STT)';
    document.getElementById('s-tts-chars').textContent=(d.today.tts_chars??0).toLocaleString()+' chars';
    document.getElementById('s-tts-cost').textContent=RP(d.today.est_tts_cost)+' (TTS)';
    const rt=document.getElementById('s-runtime'),ok=d.runtime_status==='healthy';
    rt.textContent=ok?'✓ Healthy':'⚠ Degraded';rt.className='stat-val '+(ok?'g':'a');
    document.getElementById('nav-active').textContent=d.active_calls??0;

    // Trend: today's call volume vs the trailing 7-day daily average (both
    // already present in /api/overview — no new backend fields invented).
    const weekAvg=d.week.call_count?d.week.call_count/7:null;
    const trendEl=document.getElementById('s-today-trend');
    if(trendEl)trendEl.innerHTML=trendBadge(d.today.call_count, weekAvg);

    renderHourly(d.hourly_calls||[]);
    const sp=document.getElementById('s-today-spark');
    if(sp)sp.innerHTML=sparklineSvg((d.hourly_calls||[]).map(h=>h.count));

    const k=d.kpis||{};
    document.getElementById('kpi-total').textContent=k.total_calls??'0';
    document.getElementById('kpi-answered').textContent=k.answered_calls??'0';
    document.getElementById('kpi-missed').textContent=k.missed_calls??'0';
    document.getElementById('kpi-inbound').textContent=k.inbound_calls??'0';
    document.getElementById('kpi-outbound').textContent=k.outbound_calls??'0';
    document.getElementById('kpi-appts').textContent=k.appointments_booked??'0';
    document.getElementById('kpi-followups').textContent=k.follow_ups_required??'0';
    document.getElementById('kpi-minutes-used').textContent=R(k.minutes_used,1);
    const mq=d.minutes_quota||{};
    document.getElementById('kpi-minutes-remaining').textContent=mq.remaining_minutes!=null?R(mq.remaining_minutes,1):'Not set';
    document.getElementById('kpi-notes').textContent=k.missed_calls_note||'';

    renderDailyBars((d.charts?.calls_over_time)||[],'calls-over-time-chart');
    const td=d.charts?.call_type_distribution||{};
    renderHbarList('call-type-dist-list',[
      {label:'Inbound',value:td.inbound||0},{label:'Outbound',value:td.outbound||0},
    ]);
    const sd=d.charts?.call_status_distribution||{};
    renderHbarList('call-status-dist-list',[
      {label:'Answered',value:sd.answered||0},{label:'Missed',value:sd.missed||0},{label:'Failed',value:sd.failed||0},
    ]);

    stamp();
  }catch(e){console.error(e)}
}

async function updateMinutesQuota(){
  const v=parseFloat(document.getElementById('minutes-quota-input').value);
  if(isNaN(v)){alert('Enter a number');return}
  try{
    await fetch('/api/minutes/quota',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({total_minutes:v})});
    document.getElementById('minutes-quota-input').value='';
    refreshAll();
  }catch(e){alert('Failed to set quota')}
}

function renderDailyBars(data,elId){
  const el=document.getElementById(elId);
  if(!el)return;
  const mx=Math.max(...data.map(h=>h.count),1);
  el.innerHTML='';
  data.forEach(h=>{
    const pct=(h.count/mx)*100,col=document.createElement('div');
    col.className='bar-col';
    const d=new Date(h.date);
    col.innerHTML=`<div class="bar" style="height:${Math.max(pct,2)}%"><div class="bar-tip">${h.count} calls on ${h.date}</div></div><div class="bar-lbl">${d.getDate()}/${d.getMonth()+1}</div>`;
    el.appendChild(col);
  });
}
function renderHourly(data,elId){
  const el=document.getElementById(elId||'hourly-chart');
  if(!el)return;
  const mx=Math.max(...data.map(h=>h.count),1);
  el.innerHTML='';
  data.forEach(h=>{
    const pct=(h.count/mx)*100,col=document.createElement('div');
    col.className='bar-col';
    col.innerHTML=`<div class="bar" style="height:${Math.max(pct,2)}%"><div class="bar-tip">${h.count} calls at ${h.hour}:00</div></div><div class="bar-lbl">${h.hour%6===0?h.hour+'h':''}</div>`;
    el.appendChild(col);
  });
}
