import { useState, useEffect, useCallback } from 'react'
import {
  Hash, Server, Plus, Trash2, Loader2, RefreshCw, Copy, Check, Wifi, AlertTriangle, Zap, X,
} from 'lucide-react'
import { api } from '../../api.js'

const STATUS_BADGE = {
  applied: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400',
  pending: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400',
  error: 'bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400',
}

function CopyField({ label, value }) {
  const [done, setDone] = useState(false)
  const copy = () => { navigator.clipboard?.writeText(value); setDone(true); setTimeout(() => setDone(false), 1400) }
  return (
    <div>
      <div className="label">{label}</div>
      <div className="flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-700 px-3 py-2">
        <code className="flex-1 text-xs text-slate-700 dark:text-slate-200 truncate">{value || '—'}</code>
        <button onClick={copy} className="text-slate-400 hover:text-accent" title="Copy">
          {done ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
        </button>
      </div>
    </div>
  )
}

function AddTrunkModal({ onClose, onSaved }) {
  const [f, setF] = useState({ name: '', provider: 'plivo', sip_domain: '', proxy: '', username: '', password: '', caller_id: '', provider_ips: '', register: 0, transport: 'udp' })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const set = (patch) => setF((s) => ({ ...s, ...patch }))
  const save = async () => {
    if (!f.name.trim() || !f.sip_domain.trim()) { setErr('Name and SIP domain are required.'); return }
    setSaving(true); setErr('')
    try { await api.createTrunk(f); onSaved(); onClose() }
    catch (e) { setErr(e.message) } finally { setSaving(false) }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="card w-full max-w-lg shadow-flyout max-h-[90vh] overflow-y-auto scroll-thin" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">Add SIP trunk</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X className="h-5 w-5" /></button>
        </div>
        <div className="grid sm:grid-cols-2 gap-3">
          <div><div className="label">Name</div><input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="my-plivo" /></div>
          <div><div className="label">Provider</div><input className="input" value={f.provider} onChange={(e) => set({ provider: e.target.value })} placeholder="plivo" /></div>
          <div><div className="label">SIP domain</div><input className="input" value={f.sip_domain} onChange={(e) => set({ sip_domain: e.target.value })} placeholder="sip.plivo.com" /></div>
          <div><div className="label">Proxy (optional)</div><input className="input" value={f.proxy} onChange={(e) => set({ proxy: e.target.value })} /></div>
          <div><div className="label">Username (optional)</div><input className="input" value={f.username} onChange={(e) => set({ username: e.target.value })} /></div>
          <div><div className="label">Password (optional)</div><input className="input" type="password" value={f.password} onChange={(e) => set({ password: e.target.value })} /></div>
          <div><div className="label">Caller ID</div><input className="input" value={f.caller_id} onChange={(e) => set({ caller_id: e.target.value })} placeholder="+9180..." /></div>
          <div><div className="label">Transport</div>
            <select className="input" value={f.transport} onChange={(e) => set({ transport: e.target.value })}>
              <option value="udp">UDP</option><option value="tcp">TCP</option><option value="tls">TLS</option>
            </select>
          </div>
          <div className="sm:col-span-2"><div className="label">Provider signaling IPs (comma-separated CIDRs)</div>
            <input className="input" value={f.provider_ips} onChange={(e) => set({ provider_ips: e.target.value })} placeholder="18.214.109.0/24, 13.52.9.0/24" /></div>
          <label className="sm:col-span-2 flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            <input type="checkbox" checked={!!f.register} onChange={(e) => set({ register: e.target.checked ? 1 : 0 })} className="accent-accent" />
            Register with the carrier (username/password auth)
          </label>
        </div>
        {err && <div className="mt-3 text-sm text-rose-500">{err}</div>}
        <button className="btn btn-primary w-full mt-4" onClick={save} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Add trunk
        </button>
      </div>
    </div>
  )
}

function AddNumberModal({ trunks, agents, onClose, onSaved }) {
  const [f, setF] = useState({ number: '', trunk_id: '', inbound_agent_id: '', direction: 'both', label: '' })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const set = (patch) => setF((s) => ({ ...s, ...patch }))
  const save = async () => {
    if (!f.number.trim()) { setErr('Number is required.'); return }
    setSaving(true); setErr('')
    try {
      await api.createNumber({
        number: f.number.trim(),
        trunk_id: f.trunk_id ? +f.trunk_id : null,
        inbound_agent_id: f.inbound_agent_id ? +f.inbound_agent_id : null,
        direction: f.direction, label: f.label || null,
      })
      onSaved(); onClose()
    } catch (e) { setErr(e.message) } finally { setSaving(false) }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="card w-full max-w-md shadow-flyout" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">Add phone number</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X className="h-5 w-5" /></button>
        </div>
        <div className="space-y-3">
          <div><div className="label">Number (DID)</div><input className="input" value={f.number} onChange={(e) => set({ number: e.target.value })} placeholder="+918031339140" /></div>
          <div><div className="label">Trunk</div>
            <select className="input" value={f.trunk_id} onChange={(e) => set({ trunk_id: e.target.value })}>
              <option value="">— none —</option>
              {trunks.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
          <div><div className="label">Inbound agent</div>
            <select className="input" value={f.inbound_agent_id} onChange={(e) => set({ inbound_agent_id: e.target.value })}>
              <option value="">— none —</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><div className="label">Direction</div>
              <select className="input" value={f.direction} onChange={(e) => set({ direction: e.target.value })}>
                <option value="both">Both</option><option value="inbound">Inbound</option><option value="outbound">Outbound</option>
              </select>
            </div>
            <div><div className="label">Label</div><input className="input" value={f.label} onChange={(e) => set({ label: e.target.value })} /></div>
          </div>
        </div>
        {err && <div className="mt-3 text-sm text-rose-500">{err}</div>}
        <button className="btn btn-primary w-full mt-4" onClick={save} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Add number
        </button>
      </div>
    </div>
  )
}

export default function NumbersView() {
  const [server, setServer] = useState(null)
  const [trunks, setTrunks] = useState([])
  const [numbers, setNumbers] = useState([])
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [addTrunk, setAddTrunk] = useState(false)
  const [addNumber, setAddNumber] = useState(false)
  const [applying, setApplying] = useState(null)
  const [toast, setToast] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [s, t, n, a] = await Promise.all([
        api.serverInfo().catch(() => null), api.trunks(), api.numbers(), api.agents().catch(() => []),
      ])
      setServer(s); setTrunks(t); setNumbers(n); setAgents(a)
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const apply = async (id) => {
    setApplying(id)
    try { const r = await api.applyTrunk(id); setToast(r.status === 'applied' ? 'Trunk applied to FreeSWITCH.' : `Apply: ${r.error || r.status}`); await load() }
    catch (e) { setToast(`Apply failed: ${e.message}`) } finally { setApplying(null); setTimeout(() => setToast(''), 3500) }
  }
  const removeNumber = async (id) => {
    if (!confirm('Delete this number?')) return
    try { await api.deleteNumber(id); await load() } catch (e) { setToast(`Delete failed: ${e.message}`) }
  }

  if (loading) return <div className="h-full grid place-items-center text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-slate-900 dark:text-white">My Numbers</h1>
          <p className="text-xs text-slate-400">Connect a SIP trunk and route DIDs to your agents.</p>
        </div>
        <button className="btn btn-outline" onClick={load}><RefreshCw className="h-4 w-4" /> Refresh</button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 dark:border-rose-500/30 dark:bg-rose-500/10 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
          <AlertTriangle className="h-4 w-4" /> Couldn't load telephony config: {error}
        </div>
      )}

      {/* Server SIP endpoint */}
      <div className="card-flat p-5">
        <div className="flex items-center gap-2 mb-4"><Server className="h-4 w-4 text-accent" /><div className="section-label">Your SIP endpoint</div></div>
        {server ? (
          <div className="grid sm:grid-cols-2 gap-3">
            <CopyField label="Public IP" value={server.public_ip} />
            <CopyField label="Inbound SIP URI" value={server.inbound?.sip_uri} />
            <CopyField label="RTP port range" value={server.rtp_port_range} />
            <CopyField label="Allowed provider CIDRs" value={(server.acl?.allowed_cidrs || []).join(', ')} />
            <p className="sm:col-span-2 text-xs text-slate-400">{server.firewall_note}</p>
          </div>
        ) : <p className="text-sm text-slate-400">Server info unavailable.</p>}
      </div>

      {/* Trunks */}
      <div className="card-flat overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2"><Wifi className="h-4 w-4 text-accent" /><div className="section-label">SIP trunks</div></div>
          <button className="btn btn-primary !py-1.5" onClick={() => setAddTrunk(true)}><Plus className="h-4 w-4" /> Add trunk</button>
        </div>
        {trunks.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-slate-400">No trunks yet. Add one to connect your carrier.</div>
        ) : (
          <div className="divide-y divide-slate-50 dark:divide-slate-800/60">
            {trunks.map((t) => (
              <div key={t.id} className="flex items-center gap-3 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">{t.name}</div>
                  <div className="text-[11px] text-slate-400 truncate">{t.provider} · {t.sip_domain}</div>
                </div>
                <span className={`badge ${STATUS_BADGE[t.config_status] || STATUS_BADGE.pending}`}>{t.config_status}</span>
                <button className="btn btn-outline !py-1.5 !px-2.5 text-xs" onClick={() => apply(t.id)} disabled={applying === t.id}>
                  {applying === t.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />} Apply to FreeSWITCH
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Numbers */}
      <div className="card-flat overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2"><Hash className="h-4 w-4 text-accent" /><div className="section-label">Phone numbers</div></div>
          <button className="btn btn-primary !py-1.5" onClick={() => setAddNumber(true)}><Plus className="h-4 w-4" /> Add number</button>
        </div>
        {numbers.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-slate-400">No numbers yet.</div>
        ) : (
          <div className="divide-y divide-slate-50 dark:divide-slate-800/60">
            {numbers.map((n) => {
              const agent = agents.find((a) => a.id === n.inbound_agent_id)
              const trunk = trunks.find((t) => t.id === n.trunk_id)
              return (
                <div key={n.id} className="flex items-center gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-mono font-medium text-slate-800 dark:text-slate-100 truncate">{n.number}</div>
                    <div className="text-[11px] text-slate-400 truncate">
                      {n.label ? `${n.label} · ` : ''}{trunk ? trunk.name : 'no trunk'} · {agent ? `→ ${agent.name}` : 'no agent'} · {n.direction}
                    </div>
                  </div>
                  <span className={`badge ${STATUS_BADGE[n.config_status] || STATUS_BADGE.pending}`}>{n.config_status}</span>
                  <button className="btn btn-ghost !px-2" onClick={() => removeNumber(n.id)} title="Delete number"><Trash2 className="h-4 w-4" /></button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {addTrunk && <AddTrunkModal onClose={() => setAddTrunk(false)} onSaved={load} />}
      {addNumber && <AddNumberModal trunks={trunks} agents={agents} onClose={() => setAddNumber(false)} onSaved={load} />}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-xl bg-slate-800 text-white px-4 py-3 shadow-flyout text-sm">{toast}</div>
      )}
    </div>
  )
}
