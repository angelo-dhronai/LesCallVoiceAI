// ── agent settings (read-only — see /api/agent/config for why) ───────────────
async function loadAgentSettings(){
  try{
    const d=await api('/api/agent/config');
    if(!d.available){
      document.getElementById('as-notice').textContent='Could not read agent configuration: '+(d.reason||'unknown error');
      return;
    }
    document.getElementById('as-name').textContent=d.agent_name||'—';
    document.getElementById('as-tone').textContent=d.agent_tone||'—';
    document.getElementById('as-greeting').textContent=d.greeting||'—';
    document.getElementById('as-identity').textContent=(d.identity||[]).join('\n');
    document.getElementById('as-guidelines').textContent=(d.prompt_guidelines||[]).join('\n');
    document.getElementById('as-rules').textContent=(d.firm_rules||[]).join('\n');
    document.getElementById('as-portfolio').textContent=(d.pitch?.project_portfolio||[]).join('\n');
    document.getElementById('as-objections').textContent=(d.pitch?.objection_handling||[]).join('\n');
  }catch(e){console.error(e)}
}
