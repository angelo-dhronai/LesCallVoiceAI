// ── Agent settings: multi-agent registry + per-agent knowledge base (RAG) ────
let _asAgents=[],_asCurrentId=null,_asActiveId=null,_asKbPoll=null;

async function loadAgentSettings(){ await loadAgents(); }

async function loadAgents(){
  try{
    const d=await api('/api/agents');
    _asAgents=d.agents||[];
    _asActiveId=d.active_agent_id;
    const sel=document.getElementById('as-agent-select');
    sel.innerHTML=_asAgents.map(a=>`<option value="${esc(a.id)}">${esc(a.name)}${a.id===_asActiveId?' ★ active':''}</option>`).join('');
    const active=_asAgents.find(a=>a.id===_asActiveId)||_asAgents[0];
    document.getElementById('as-active-name').textContent=active?active.name:'—';
    if(!_asCurrentId||!_asAgents.some(a=>a.id===_asCurrentId))_asCurrentId=(active||{}).id;
    if(_asCurrentId){sel.value=_asCurrentId;await selectAgent(_asCurrentId);}
  }catch(e){console.error(e)}
}

async function selectAgent(id){
  _asCurrentId=id;
  try{
    const d=await api('/api/agents/'+encodeURIComponent(id));
    const p=d.persona||{};
    document.getElementById('as-edit-name').textContent=p.name||id;
    document.getElementById('as-f-name').value=p.name||'';
    document.getElementById('as-f-lang').value=p.default_language||'hi';
    document.getElementById('as-f-agentname').value=p.agent_name||'';
    document.getElementById('as-f-role').value=p.role_line||'';
    document.getElementById('as-f-identity').value=(p.identity||[]).join('\n');
    document.getElementById('as-f-brand').value=(p.brand||[]).join('\n');
    document.getElementById('as-f-portfolio').value=(p.portfolio||[]).join('\n');
    const actBtn=document.getElementById('as-activate-btn');
    actBtn.textContent=d.active?'✓ Active':'Set Active';
    actBtn.disabled=!!d.active;
    renderKb(d.kb||{});
  }catch(e){console.error(e)}
}

function _linesToArr(id){return document.getElementById(id).value.split('\n').map(s=>s.trim()).filter(Boolean);}

async function savePersona(){
  if(!_asCurrentId)return;
  const body={
    name:document.getElementById('as-f-name').value.trim(),
    default_language:document.getElementById('as-f-lang').value,
    agent_name:document.getElementById('as-f-agentname').value.trim(),
    role_line:document.getElementById('as-f-role').value.trim(),
    identity:_linesToArr('as-f-identity'),
    brand:_linesToArr('as-f-brand'),
    portfolio:_linesToArr('as-f-portfolio'),
  };
  try{
    const r=await (await fetch('/api/agents/'+encodeURIComponent(_asCurrentId),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    if(r.ok){pushNotification('Persona saved','info');showRestartNote();loadAgents();}
    else alert('Save failed: '+(r.reason||'unknown'));
  }catch(e){alert('Save failed: '+e.message)}
}

async function createAgentPrompt(){
  const name=prompt('Name for the new agent:');
  if(!name)return;
  try{
    const r=await (await fetch('/api/agents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})).json();
    if(r.ok){_asCurrentId=r.agent.id;await loadAgents();}
    else alert('Create failed: '+(r.reason||'unknown'));
  }catch(e){alert('Create failed: '+e.message)}
}

async function activateAgent(){
  if(!_asCurrentId)return;
  try{
    const r=await (await fetch('/api/agents/'+encodeURIComponent(_asCurrentId)+'/activate',{method:'POST'})).json();
    if(r.ok){pushNotification('Agent activated','info');showRestartNote();await loadAgents();}
    else alert('Activate failed: '+(r.reason||'unknown'));
  }catch(e){alert('Activate failed: '+e.message)}
}

async function deleteAgentCur(){
  if(!_asCurrentId)return;
  if(!confirm('Delete this agent and its knowledge base? This cannot be undone.'))return;
  try{
    const r=await (await fetch('/api/agents/'+encodeURIComponent(_asCurrentId),{method:'DELETE'})).json();
    if(r.ok){_asCurrentId=null;await loadAgents();}
    else alert('Delete failed: '+(r.reason||'unknown'));
  }catch(e){alert('Delete failed: '+e.message)}
}

// ── Knowledge base ──────────────────────────────────────────────────────────
function renderKb(kb){
  document.getElementById('as-kb-docs').textContent=kb.n_docs??0;
  document.getElementById('as-kb-chunks').textContent=kb.n_chunks??0;
  const tb=document.getElementById('as-kb-table');
  const docs=kb.docs||[];
  if(!docs.length){tb.innerHTML='<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--text-d)">No documents yet</td></tr>';stopKbPoll();return}
  const badge=s=>s==='ready'?'<span class="badge bg">✓ Ready</span>':s==='error'?'<span class="badge br">✗ Error</span>':'<span class="badge bz">⏳ Indexing…</span>';
  tb.innerHTML=docs.map(d=>`<tr>
    <td>${esc(d.filename)}</td>
    <td>${d.size?(d.size/1024).toFixed(1)+' KB':'—'}</td>
    <td>${d.chunk_count||0}</td>
    <td>${badge(d.status)}</td>
    <td style="text-align:right"><button class="btn btn-gh btn-sm" onclick="deleteKbDoc('${esc(d.id)}')" style="color:var(--error)">Remove</button></td>
  </tr>`).join('');
  if(docs.some(d=>d.status==='indexing'))startKbPoll();else stopKbPoll();
}

function startKbPoll(){if(_asKbPoll)return;_asKbPoll=setInterval(refreshKb,2500);}
function stopKbPoll(){if(_asKbPoll){clearInterval(_asKbPoll);_asKbPoll=null;}}
async function refreshKb(){
  if(!_asCurrentId)return stopKbPoll();
  try{const kb=await api('/api/agents/'+encodeURIComponent(_asCurrentId)+'/kb');renderKb(kb);}catch(e){}
}

async function uploadKbFile(){
  const inp=document.getElementById('as-kb-file');
  const f=inp.files&&inp.files[0];
  if(!f||!_asCurrentId)return;
  const fd=new FormData();fd.append('file',f);
  try{
    const r=await (await fetch('/api/agents/'+encodeURIComponent(_asCurrentId)+'/kb/upload',{method:'POST',body:fd})).json();
    if(r.ok){pushNotification('Uploaded — indexing…','info');showRestartNote();refreshKb();startKbPoll();}
    else alert('Upload failed: '+(r.reason||'unknown'));
  }catch(e){alert('Upload failed: '+e.message)}
  inp.value='';
}

async function deleteKbDoc(docId){
  if(!_asCurrentId||!confirm('Remove this document from the knowledge base?'))return;
  try{
    await fetch('/api/agents/'+encodeURIComponent(_asCurrentId)+'/kb/'+encodeURIComponent(docId),{method:'DELETE'});
    showRestartNote();refreshKb();startKbPoll();
  }catch(e){alert('Remove failed: '+e.message)}
}

async function reindexKb(){
  if(!_asCurrentId)return;
  try{await fetch('/api/agents/'+encodeURIComponent(_asCurrentId)+'/kb/reindex',{method:'POST'});pushNotification('Re-indexing…','info');refreshKb();startKbPoll();}
  catch(e){alert('Re-index failed: '+e.message)}
}

function showRestartNote(){const n=document.getElementById('as-restart-note');if(n)n.style.display='block';}
async function restartFromAgents(){
  try{await fetch('/api/runtime/restart',{method:'POST'});pushNotification('Pipeline restarting…','info');document.getElementById('as-restart-note').style.display='none';}
  catch(e){alert('Restart failed: '+e.message)}
}
