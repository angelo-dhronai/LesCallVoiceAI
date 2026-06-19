import { useState, useEffect, useCallback } from 'react'
import {
  Megaphone, Plus, Loader2, RefreshCw, Phone, ChevronLeft, Play, Pause, Upload, X, AlertTriangle,
} from 'lucide-react'
import { api } from '../../api.js'

const STATUS_BADGE = {
  draft: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
  running: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400',
  paused: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400',
  done: 'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400',
}
const LEAD_BADGE = {
  pending: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
  dialing: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400',
  called: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400',
  failed: 'bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400',
}

function NewCampaignModal({ agents, onClose, onSaved }) {
  const [f, setF] = useState({ name: '', agent_id: '', caller_id: '', gateway: '', max_concurrency: 1, retry_limit: 2 })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const set = (p) => setF((s) => ({ ...s, ...p }))
  const save = async () => {
    if (!f.name.trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr('')
    try {
      await api.createCampaign({
        name: f.name.trim(), agent_id: f.agent_id ? +f.agent_id : null,
        caller_id: f.caller_id || null, gateway: f.gateway || null,
        max_concurrency: +f.max_concurrency, retry_limit: +f.retry_limit,
      })
      onSaved(); onClose()
    } catch (e) { setErr(e.message) } finally { setSaving(false) }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="card w-full max-w-md shadow-flyout" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">New campaign</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X className="h-5 w-5" /></button>
        </div>
        <div className="space-y-3">
          <div><div className="label">Name</div><input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="Pramukh Q3 outreach" /></div>
          <div><div className="label">Agent</div>
            <select className="input" value={f.agent_id} onChange={(e) => set({ agent_id: e.target.value })}>
              <option value="">— default —</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><div className="label">Caller ID</div><input className="input" value={f.caller_id} onChange={(e) => set({ caller_id: e.target.value })} placeholder="+9180..." /></div>
            <div><div className="label">Gateway / trunk</div><input className="input" value={f.gateway} onChange={(e) => set({ gateway: e.target.value })} /></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><div className="label">Max concurrency</div><input className="input" type="number" min="1" value={f.max_concurrency} onChange={(e) => set({ max_concurrency: e.target.value })} /></div>
            <div><div className="label">Retry limit</div><input className="input" type="number" min="0" value={f.retry_limit} onChange={(e) => set({ retry_limit: e.target.value })} /></div>
          </div>
        </div>
        {err && <div className="mt-3 text-sm text-rose-500">{err}</div>}
        <button className="btn btn-primary w-full mt-4" onClick={save} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Create campaign
        </button>
      </div>
    </div>
  )
}

// Parse pasted "name, phone" or "phone" lines into lead objects.
function parseLeads(text) {
  return text.split('\n').map((line) => {
    const parts = line.split(/[,\t]/).map((s) => s.trim()).filter(Boolean)
    if (!parts.length) return null
    const phoneIdx = parts.findIndex((p) => /\+?\d[\d\s-]{6,}/.test(p))
    const phone = (phoneIdx >= 0 ? parts[phoneIdx] : parts[0]).replace(/[^\d+]/g, '')
    const name = parts.filter((_, i) => i !== phoneIdx).join(' ') || null
    return phone.replace(/\D/g, '').length >= 7 ? { name, phone } : null
  }).filter(Boolean)
}

function AddLeadsModal({ campaignId, onClose, onSaved }) {
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const parsed = parseLeads(text)
  const save = async () => {
    if (!parsed.length) { setErr('No valid phone numbers found.'); return }
    setSaving(true); setErr('')
    try { await api.addLeads(campaignId, parsed); onSaved(); onClose() }
    catch (e) { setErr(e.message) } finally { setSaving(false) }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="card w-full max-w-md shadow-flyout" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">Add leads</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X className="h-5 w-5" /></button>
        </div>
        <p className="text-xs text-slate-400 mb-2">One per line: <code>Name, +9180...</code> or just a phone number.</p>
        <textarea className="input h-40 resize-none font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)}
          placeholder={'Rahul, +919812345678\n+918031339140'} />
        <div className="mt-2 text-xs text-slate-400">{parsed.length} valid lead{parsed.length === 1 ? '' : 's'} detected.</div>
        {err && <div className="mt-2 text-sm text-rose-500">{err}</div>}
        <button className="btn btn-primary w-full mt-4" onClick={save} disabled={saving || !parsed.length}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} Add {parsed.length || ''} leads
        </button>
      </div>
    </div>
  )
}

function CampaignDetail({ campaign, agents, onBack, onChanged }) {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [addLeads, setAddLeads] = useState(false)
  const [busy, setBusy] = useState('')
  const [toast, setToast] = useState('')
  const agent = agents.find((a) => a.id === campaign.agent_id)

  const load = useCallback(async () => {
    setLoading(true)
    try { setLeads(await api.leads(campaign.id)) } catch { setLeads([]) } finally { setLoading(false) }
  }, [campaign.id])
  useEffect(() => { load() }, [load])

  const setStatus = async (status) => {
    try { await api.campaignStatus(campaign.id, status); onChanged() } catch (e) { setToast(e.message) }
  }
  const dialAll = async () => {
    setBusy('all')
    try { const r = await api.dialCampaign(campaign.id); setToast(`Dialed ${r.dialed} lead(s).`); await load() }
    catch (e) { setToast(`Dial failed: ${e.message}`) } finally { setBusy(''); setTimeout(() => setToast(''), 3500) }
  }
  const dialOne = async (id) => {
    setBusy(id)
    try { const r = await api.dialLead(id); setToast(`Lead: ${r.status}`); await load() }
    catch (e) { setToast(`Dial failed: ${e.message}`) } finally { setBusy(''); setTimeout(() => setToast(''), 3500) }
  }

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="btn btn-ghost !px-2"><ChevronLeft className="h-[18px] w-[18px]" /></button>
        <div className="min-w-0">
          <h1 className="text-lg font-bold text-slate-900 dark:text-white truncate">{campaign.name}</h1>
          <div className="text-xs text-slate-400">{agent ? agent.name : 'default agent'} · {campaign.gateway || 'no gateway'}</div>
        </div>
        <span className={`badge ml-auto ${STATUS_BADGE[campaign.status] || STATUS_BADGE.draft}`}>{campaign.status}</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-outline" onClick={() => setAddLeads(true)}><Plus className="h-4 w-4" /> Add leads</button>
        <button className="btn btn-primary" onClick={dialAll} disabled={busy === 'all'}>
          {busy === 'all' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Phone className="h-4 w-4" />} Dial pending
        </button>
        {campaign.status !== 'running'
          ? <button className="btn btn-outline" onClick={() => setStatus('running')}><Play className="h-4 w-4" /> Mark running</button>
          : <button className="btn btn-outline" onClick={() => setStatus('paused')}><Pause className="h-4 w-4" /> Pause</button>}
      </div>

      <div className="card-flat overflow-hidden">
        {loading ? (
          <div className="grid place-items-center py-12 text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>
        ) : leads.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-slate-400">No leads yet. Add some to start dialing.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-slate-400 border-b border-slate-100 dark:border-slate-800">
                <th className="px-4 py-3 font-semibold">Name</th><th className="px-4 py-3 font-semibold">Phone</th>
                <th className="px-4 py-3 font-semibold">Status</th><th className="px-4 py-3 font-semibold">Disposition</th>
                <th className="px-4 py-3 font-semibold">Attempts</th><th className="px-4 py-3 font-semibold text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((l) => (
                <tr key={l.id} className="border-b border-slate-50 dark:border-slate-800/60">
                  <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{l.name || '—'}</td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-500 dark:text-slate-400">{l.phone}</td>
                  <td className="px-4 py-3"><span className={`badge ${LEAD_BADGE[l.status] || LEAD_BADGE.pending}`}>{l.status}</span></td>
                  <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{l.disposition || '—'}</td>
                  <td className="px-4 py-3 tabular-nums text-slate-500">{l.attempts}</td>
                  <td className="px-4 py-3 text-right">
                    <button className="btn btn-outline !py-1.5 !px-2.5 text-xs" onClick={() => dialOne(l.id)} disabled={busy === l.id}>
                      {busy === l.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Phone className="h-3.5 w-3.5" />} Call now
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {addLeads && <AddLeadsModal campaignId={campaign.id} onClose={() => setAddLeads(false)} onSaved={load} />}
      {toast && <div className="fixed bottom-6 right-6 z-50 rounded-xl bg-slate-800 text-white px-4 py-3 shadow-flyout text-sm">{toast}</div>}
    </div>
  )
}

export default function CampaignsView() {
  const [campaigns, setCampaigns] = useState([])
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [c, a] = await Promise.all([api.campaigns(), api.agents().catch(() => [])])
      setCampaigns(c); setAgents(a)
      if (open) setOpen(c.find((x) => x.id === open.id) || null)
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }, [open])
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (open) return <CampaignDetail campaign={open} agents={agents} onBack={() => { setOpen(null); load() }} onChanged={load} />

  if (loading) return <div className="h-full grid place-items-center text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>

  const totals = (c) => Object.entries(c.lead_counts || {}).reduce((acc, [k, v]) => { acc.total += v; if (k === 'called') acc.called += v; return acc }, { total: 0, called: 0 })

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-slate-900 dark:text-white">Campaigns</h1>
          <p className="text-xs text-slate-400">Outbound batch dialing — one campaign per agent.</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-outline" onClick={load}><RefreshCw className="h-4 w-4" /> Refresh</button>
          <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New campaign</button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 dark:border-rose-500/30 dark:bg-rose-500/10 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
          <AlertTriangle className="h-4 w-4" /> Couldn't load campaigns: {error}
        </div>
      )}

      {campaigns.length === 0 ? (
        <div className="card-flat grid place-items-center py-16 text-center">
          <Megaphone className="h-8 w-8 text-slate-300 mb-3" />
          <div className="text-sm font-medium text-slate-600 dark:text-slate-300">No campaigns yet</div>
          <div className="text-xs text-slate-400 mt-1">Create one to start dialing a list of leads.</div>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {campaigns.map((c) => {
            const t = totals(c)
            const agent = agents.find((a) => a.id === c.agent_id)
            return (
              <button key={c.id} onClick={() => setOpen(c)} className="card-flat p-5 text-left hover:shadow-pop transition-shadow">
                <div className="flex items-start justify-between">
                  <span className="h-10 w-10 rounded-xl bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400 grid place-items-center"><Megaphone className="h-5 w-5" /></span>
                  <span className={`badge ${STATUS_BADGE[c.status] || STATUS_BADGE.draft}`}>{c.status}</span>
                </div>
                <div className="mt-3 text-sm font-semibold text-slate-800 dark:text-slate-100 truncate">{c.name}</div>
                <div className="text-[11px] text-slate-400">{agent ? agent.name : 'default agent'}</div>
                <div className="mt-3 flex items-center gap-4 text-xs text-slate-500 dark:text-slate-400">
                  <span><b className="text-slate-700 dark:text-slate-200">{t.total}</b> leads</span>
                  <span><b className="text-slate-700 dark:text-slate-200">{t.called}</b> called</span>
                </div>
              </button>
            )
          })}
        </div>
      )}

      {creating && <NewCampaignModal agents={agents} onClose={() => setCreating(false)} onSaved={load} />}
    </div>
  )
}
