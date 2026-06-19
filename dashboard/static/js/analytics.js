// ── analytics ──────────────────────────────────────────────────────────────
// Everything here is derived client-side from data the dashboard already
// fetches elsewhere (/api/overview, /api/calls/recordings, /api/outbound/stats)
// — no new backend endpoints, no new tracking.

const LANG_NAMES={en:'English',hi:'Hindi',gu:'Gujarati'};

function renderHbarList(elId, rows /* [{label,value,colorClass?}] */){
  const el=document.getElementById(elId);
  if(!rows.length){el.innerHTML='<div class="dim" style="text-align:center;padding:24px">No data yet</div>';return}
  const max=Math.max(...rows.map(r=>r.value),1);
  el.innerHTML=rows.map(r=>`
    <div class="hbar-row">
      <div class="hbar-lbl" title="${esc(r.label)}">${esc(r.label)}</div>
      <div class="hbar-track"><div class="hbar-fill" style="width:${Math.max((r.value/max)*100,2)}%"></div></div>
      <div class="hbar-val">${r.value}</div>
    </div>`).join('');
}

async function loadAnalytics(){
  try{
    const [ov, recs, obStats]=await Promise.all([
      api('/api/overview'),
      api('/api/calls/recordings?limit=200'),
      api('/api/outbound/stats'),
    ]);

    document.getElementById('an-calls-today').textContent=ov.today.call_count??'0';
    renderHourly(ov.hourly_calls||[], 'an-hourly-chart');

    // Avg call duration + calls-by-language, from recent recordings.
    const recordings=recs.recordings||[];
    const durs=recordings.map(r=>r.duration_seconds).filter(d=>d>0);
    const avgDur=durs.length?durs.reduce((a,b)=>a+b,0)/durs.length:null;
    document.getElementById('an-avg-dur').textContent=avgDur?fmtDur(avgDur):'—';
    document.getElementById('an-avg-dur-sub').textContent=durs.length?`Across last ${durs.length} recordings`:'No recordings yet';

    const langCounts={};
    recordings.forEach(r=>{
      const l=r.language_detected;
      if(!l)return;
      langCounts[l]=(langCounts[l]||0)+1;
    });
    const langRows=Object.entries(langCounts)
      .sort((a,b)=>b[1]-a[1])
      .map(([k,v])=>({label:LANG_NAMES[k]||k.toUpperCase(),value:v}));
    renderHbarList('an-lang-list', langRows);

    // Conversion / visit-booking rate + outcome breakdown, from outbound stats
    // (the same disposition counters already shown on the Outbound tab).
    const s=obStats.stats||{}, d=obStats.dispositions||{};
    const total=s.total||0;
    const interested=d.interested||0, siteVisit=d.site_visit_booked||0;
    const conversion=total?Math.round(((interested+siteVisit)/total)*100):null;
    const visitRate=total?Math.round((siteVisit/total)*100):null;
    document.getElementById('an-conversion').textContent=conversion!=null?conversion+'%':'—';
    document.getElementById('an-visit-rate').textContent=visitRate!=null?visitRate+'%':'—';

    const outcomeRows=Object.entries(DP_LABELS)
      .filter(([k])=>k!=='not_classified')
      .map(([k,label])=>({label,value:d[k]||0}))
      .filter(r=>r.value>0)
      .sort((a,b)=>b.value-a.value);
    renderHbarList('an-outcome-list', outcomeRows);
  }catch(e){console.error(e)}
}
