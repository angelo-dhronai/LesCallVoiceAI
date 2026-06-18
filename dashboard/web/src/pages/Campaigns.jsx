import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function Campaigns() {
  const [campaigns, setCampaigns] = useState([])
  const [agents, setAgents] = useState([])
  const [open, setOpen] = useState(null)
  const [leads, setLeads] = useState([])
  const [form, setForm] = useState({ name: '', agent_id: '', caller_id: '', gateway: '' })
  const [bulk, setBulk] = useState('')
  const [msg, setMsg] = useState(null)

  const load = () => api.campaigns().then(setCampaigns).catch((e) => setMsg(e.message))
  useEffect(() => { load(); api.agents().then(setAgents) }, [])

  const create = async () => {
    try {
      const body = { ...form, agent_id: form.agent_id ? Number(form.agent_id) : null }
      await api.createCampaign(body); setForm({ name: '', agent_id: '', caller_id: '', gateway: '' }); load()
    } catch (e) { setMsg(e.message) }
  }
  const openLeads = async (c) => { setOpen(c); setLeads(await api.leads(c.id)) }
  const setStatus = async (c, s) => { await api.campaignStatus(c.id, s); load() }
  const importLeads = async () => {
    // one "Name,phone" per line (phone required)
    const rows = bulk.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
      const [a, b] = l.split(',').map((x) => x && x.trim())
      return b ? { name: a, phone: b } : { phone: a }
    })
    if (rows.length) { await api.addLeads(open.id, rows); setBulk(''); setLeads(await api.leads(open.id)); load() }
  }

  const statusColor = { running: 'bg-green-100 text-green-700', paused: 'bg-amber-100 text-amber-700', draft: 'bg-slate-100 text-slate-600', done: 'bg-slate-200 text-slate-500' }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Outbound Campaign Dialer</h1>
      {msg && <div className="mb-4 text-sm text-red-600">{msg}</div>}

      <div className="card mb-6">
        <h3 className="font-semibold mb-3">New campaign</h3>
        <div className="grid grid-cols-4 gap-3">
          <input className="input" placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <select className="input" value={form.agent_id} onChange={(e) => setForm({ ...form, agent_id: e.target.value })}>
            <option value="">Agent…</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <input className="input" placeholder="Caller ID" value={form.caller_id} onChange={(e) => setForm({ ...form, caller_id: e.target.value })} />
          <input className="input" placeholder="Gateway" value={form.gateway} onChange={(e) => setForm({ ...form, gateway: e.target.value })} />
        </div>
        <button className="btn btn-primary mt-3" onClick={create}>Create</button>
      </div>

      <div className="space-y-3">
        {campaigns.map((c) => (
          <div key={c.id} className="card">
            <div className="flex items-center justify-between">
              <div>
                <span className="font-semibold mr-2">{c.name}</span>
                <span className={`badge ${statusColor[c.status] || ''}`}>{c.status}</span>
                <span className="text-xs text-slate-500 ml-2">
                  {Object.entries(c.lead_counts || {}).map(([k, v]) => `${v} ${k}`).join(' · ') || 'no leads'}
                </span>
              </div>
              <div className="flex gap-2">
                <button className="btn btn-ghost" onClick={() => setStatus(c, 'running')}>Start</button>
                <button className="btn btn-ghost" onClick={() => setStatus(c, 'paused')}>Pause</button>
                <button className="btn btn-ghost" onClick={() => openLeads(c)}>Leads</button>
              </div>
            </div>

            {open?.id === c.id && (
              <div className="mt-4 border-t pt-4">
                <div className="grid md:grid-cols-2 gap-4">
                  <div>
                    <label className="label">Import leads (one per line: Name,+phone)</label>
                    <textarea className="input h-24" value={bulk} onChange={(e) => setBulk(e.target.value)} />
                    <button className="btn btn-primary mt-2" onClick={importLeads}>Add leads</button>
                  </div>
                  <div className="max-h-48 overflow-auto">
                    {leads.map((l) => (
                      <div key={l.id} className="flex justify-between text-sm py-1 border-b border-slate-100">
                        <span>{l.name || '—'} · {l.phone}</span>
                        <span className="text-xs text-slate-500">{l.status}{l.disposition ? ` / ${l.disposition}` : ''}</span>
                      </div>
                    ))}
                    {leads.length === 0 && <div className="text-sm text-slate-400">No leads</div>}
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}
        {campaigns.length === 0 && <div className="text-sm text-slate-400">No campaigns yet</div>}
      </div>
    </div>
  )
}
