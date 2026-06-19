// ── conversation analytics (aggregated from AI Call Analysis results) ────────
let _caData={};  // full API response kept for expand popups

// Build hbar HTML directly rather than calling renderHbarList (which writes
// to an element by id) — needed for both inline and popup rendering.
function _caHbarHtml(rows,big){
  if(!rows||!rows.length)return'<div class="dim" style="text-align:center;padding:24px">No data yet</div>';
  const max=Math.max(...rows.map(r=>r.value),1);
  return rows.map(r=>`
    <div class="hbar-row" style="${big?'margin-bottom:10px':''}">
      <div class="hbar-lbl" style="${big?'font-size:13px;min-width:220px':'min-width:140px'}" title="${esc(r.label)}">${esc(r.label)}</div>
      <div class="hbar-track" style="${big?'flex:1':''}"><div class="hbar-fill" style="width:${Math.max((r.value/max)*100,2)}%"></div></div>
      <div class="hbar-val">${r.value}</div>
    </div>`).join('');
}

async function loadConvAnalytics(){
  const storedFolders=JSON.parse(sessionStorage.getItem('caSelectedFolders')||'[]');
  const allToggle=document.getElementById('ca-show-all');
  const showAll=allToggle&&allToggle.checked;
  const foldersParam=(storedFolders.length&&!showAll)?storedFolders.join(','):'';
  const url='/api/analytics/conversation'+(foldersParam?'?folders='+encodeURIComponent(foldersParam):'');
  try{
    const d=await api(url);
    _caData=d;
    document.getElementById('ca-count').textContent=d.analyzed_call_count??0;
    document.getElementById('ca-hot').textContent=(d.lead_intelligence||{}).hot||0;
    document.getElementById('ca-negative').textContent=(d.sentiment||{}).negative||0;
    const label=foldersParam
      ?`Showing analytics for ${storedFolders.length} selected call(s). <a href="#" onclick="clearCaSelection();return false" style="color:var(--primary)">Clear selection →</a>`
      :(d.analyzed_call_count?`Aggregated from all ${d.analyzed_call_count} analyzed call(s)`:'No calls analyzed yet — go to AI Call Analysis to analyze some calls');
    document.getElementById('ca-sub').innerHTML=label;

    document.getElementById('ca-recommendations').innerHTML=(d.ai_recommendations||[])
      .map(r=>`<div style="padding:8px 0;border-bottom:1px solid var(--border)">💡 ${esc(r)}</div>`).join('');

    const leadRows=Object.entries(d.lead_intelligence||{}).map(([k,v])=>({label:k[0].toUpperCase()+k.slice(1),value:v}));
    document.getElementById('ca-lead-list').innerHTML=_caHbarHtml(leadRows);
    const sentRows=Object.entries(d.sentiment||{}).map(([k,v])=>({label:k[0].toUpperCase()+k.slice(1),value:v}));
    document.getElementById('ca-sentiment-list').innerHTML=_caHbarHtml(sentRows);

    const norm=arr=>(arr||[]).map(x=>({label:x.text||x.label,value:x.count??x.value}));
    document.getElementById('ca-questions-list').innerHTML=_caHbarHtml(norm(d.top_customer_questions));
    document.getElementById('ca-objections-list').innerHTML=_caHbarHtml(norm(d.common_objections));
    document.getElementById('ca-topics-list').innerHTML=_caHbarHtml(norm(d.topic_analysis));
    document.getElementById('ca-products-list').innerHTML=_caHbarHtml(norm(d.product_interest));

    const trendsTb=document.getElementById('ca-trends-table');
    const trends=d.trends||[];
    trendsTb.innerHTML=trends.length
      ?trends.map(t=>`<tr><td>${esc(t.date)}</td><td>${t.positive}</td><td>${t.neutral}</td><td>${t.negative}</td><td>${t.hot}</td><td>${t.warm}</td><td>${t.cold}</td></tr>`).join('')
      :'<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-d)">No trend data yet</td></tr>';
  }catch(e){console.error(e)}
}

function clearCaSelection(){
  sessionStorage.removeItem('caSelectedFolders');
  const tog=document.getElementById('ca-show-all');
  if(tog)tog.checked=false;
  loadConvAnalytics();
}

// ── card expand popups ────────────────────────────────────────────────────────
function expandCaCard(key){
  const d=_caData;
  if(!d||!Object.keys(d).length){pushNotification('Open Conversation Analytics first','warn');return;}
  const bd=document.getElementById('ca-popup-backdrop');
  const title=document.getElementById('ca-popup-title');
  const body=document.getElementById('ca-popup-body');

  const showHbar=(rows,label)=>{
    title.textContent=label;
    body.innerHTML=rows&&rows.length
      ?`<div style="display:flex;flex-direction:column;gap:4px">${_caHbarHtml(rows,true)}</div>`
      :'<div class="dim" style="text-align:center;padding:40px">No data yet</div>';
    bd.style.display='flex';
  };

  const norm=arr=>(arr||[]).map(x=>({label:x.text||x.label,value:x.count??x.value}));

  if(key==='recommendations'){
    title.textContent='💡 AI Recommendations';
    const recs=d.ai_recommendations||[];
    body.innerHTML=recs.length
      ?recs.map((r,i)=>`<div style="padding:14px 0;border-bottom:1px solid var(--border);line-height:1.6"><span class="dim" style="font-size:11px;display:block;margin-bottom:4px">#${i+1}</span>💡 ${esc(r)}</div>`).join('')
      :'<div class="dim" style="text-align:center;padding:40px">No recommendations yet — analyze more calls</div>';
    bd.style.display='flex';
  }else if(key==='lead'){
    showHbar(Object.entries(d.lead_intelligence||{}).map(([k,v])=>({label:k[0].toUpperCase()+k.slice(1),value:v})),'Lead Intelligence');
  }else if(key==='sentiment'){
    showHbar(Object.entries(d.sentiment||{}).map(([k,v])=>({label:k[0].toUpperCase()+k.slice(1),value:v})),'Sentiment Analysis');
  }else if(key==='questions'){
    showHbar(norm(d.top_customer_questions),'Top Customer Questions');
  }else if(key==='objections'){
    showHbar(norm(d.common_objections),'Common Objections');
  }else if(key==='topics'){
    showHbar(norm(d.topic_analysis),'Topic Analysis');
  }else if(key==='products'){
    showHbar(norm(d.product_interest),'Product Interest Analysis');
  }else if(key==='trends'){
    title.textContent='Advanced Analytics — Trends';
    const trends=d.trends||[];
    body.innerHTML=`<div class="tw"><table style="width:100%">
      <thead><tr><th>Date</th><th>Positive</th><th>Neutral</th><th>Negative</th><th>Hot</th><th>Warm</th><th>Cold</th></tr></thead>
      <tbody>${trends.length
        ?trends.map(t=>`<tr><td>${esc(t.date)}</td><td>${t.positive}</td><td>${t.neutral}</td><td>${t.negative}</td><td>${t.hot}</td><td>${t.warm}</td><td>${t.cold}</td></tr>`).join('')
        :'<tr><td colspan="7" style="text-align:center;padding:40px;color:var(--text-d)">No trend data yet</td></tr>'
      }</tbody>
    </table></div>`;
    bd.style.display='flex';
  }
}

function closeCaPopup(){
  document.getElementById('ca-popup-backdrop').style.display='none';
}
