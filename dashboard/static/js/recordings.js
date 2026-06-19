// ── recordings (play / download only — no transcript here) ───────────────────
let recPage=1,recPages=1;

function renderRecRows(recs){
  const tb=document.getElementById('rec-table');
  if(!recs.length){tb.innerHTML='<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--text-d)">No recordings found</td></tr>';return}
  tb.innerHTML=recs.map(r=>{
    const ph=r.caller&&r.caller.length>4?'+'+r.caller.replace(/\D/g,''):r.caller||'Unknown';
    const audioUrl='/api/calls/recordings/'+encodeURIComponent(r.folder)+'/audio';
    return`<tr>
      <td>${esc(ph)}</td>
      <td style="font-size:12px">${fmtTs(r.folder_dt)}</td>
      <td>${fmtDur(r.duration_seconds)}</td>
      <td><audio controls preload="none" style="height:30px;max-width:220px"><source src="${audioUrl}" type="audio/wav"></audio></td>
      <td><a class="btn btn-gh btn-sm" href="${audioUrl}?download=1" download>⬇ Download</a></td>
    </tr>`;
  }).join('');
}

async function loadRecordings(page){
  page=page||1;recPage=page;
  const search=document.getElementById('rec-search')?.value||'';
  const tb=document.getElementById('rec-table');
  tb.innerHTML='<tr><td colspan="5" style="text-align:center;padding:24px"><div class="spin"></div></td></tr>';
  try{
    const d=await api(`/api/calls/recordings?page=${page}&limit=50&search=${encodeURIComponent(search)}`);
    recPages=d.pages||1;
    renderRecRows(d.recordings||[]);
    document.getElementById('rec-pages').innerHTML=
      `<button class="pgbtn" onclick="loadRecordings(${page-1})" ${page<=1?'disabled':''}>← Prev</button>
       <span class="pginf">Page ${page} of ${recPages}</span>
       <button class="pgbtn" onclick="loadRecordings(${page+1})" ${page>=recPages?'disabled':''}>Next →</button>`;
  }catch(e){tb.innerHTML='<tr><td colspan="5" style="text-align:center;color:var(--error);padding:24px">Error</td></tr>'}
}
