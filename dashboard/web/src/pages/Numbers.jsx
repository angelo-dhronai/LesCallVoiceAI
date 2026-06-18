import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function Numbers() {
  const [trunks, setTrunks] = useState([])
  const [numbers, setNumbers] = useState([])
  const [agents, setAgents] = useState([])
  const [trunkForm, setTrunkForm] = useState({ name: '', provider: 'plivo', sip_domain: '', caller_id: '', register: 0 })
  const [numForm, setNumForm] = useState({ number: '', trunk_id: '', inbound_agent_id: '', direction: 'both' })
  const [instr, setInstr] = useState(null)
  const [msg, setMsg] = useState(null)

  const load = () => {
    api.trunks().then(setTrunks); api.numbers().then(setNumbers); api.agents().then(setAgents)
  }
  useEffect(() => { load() }, [])

  const addTrunk = async () => {
    try { await api.createTrunk(trunkForm); setTrunkForm({ name: '', provider: 'plivo', sip_domain: '', caller_id: '', register: 0 }); load() }
    catch (e) { setMsg(e.message) }
  }
  const addNumber = async () => {
    try {
      await api.createNumber({
        ...numForm,
        trunk_id: numForm.trunk_id ? Number(numForm.trunk_id) : null,
        inbound_agent_id: numForm.inbound_agent_id ? Number(numForm.inbound_agent_id) : null,
      })
      setNumForm({ number: '', trunk_id: '', inbound_agent_id: '', direction: 'both' }); load()
    } catch (e) { setMsg(e.message) }
  }
  const apply = async (t) => {
    try { const r = await api.applyTrunk(t.id); setMsg(`Apply: ${r.status} (${(r.written || []).length} files)`); load() }
    catch (e) { setMsg(e.message) }
  }
  const showInstr = async (t) => setInstr(await api.trunkInstructions(t.id))

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Plivo Numbers & SIP Trunks</h1>
      {msg && <div className="mb-4 text-sm text-slate-700">{msg}</div>}

      <div className="grid md:grid-cols-2 gap-6 mb-6">
        <div className="card">
          <h3 className="font-semibold mb-3">Add SIP trunk</h3>
          <div className="space-y-2">
            <input className="input" placeholder="Name (e.g. plivo)" value={trunkForm.name} onChange={(e) => setTrunkForm({ ...trunkForm, name: e.target.value })} />
            <input className="input" placeholder="SIP domain (xxx.zt.plivo.com)" value={trunkForm.sip_domain} onChange={(e) => setTrunkForm({ ...trunkForm, sip_domain: e.target.value })} />
            <input className="input" placeholder="Caller ID" value={trunkForm.caller_id} onChange={(e) => setTrunkForm({ ...trunkForm, caller_id: e.target.value })} />
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={!!trunkForm.register} onChange={(e) => setTrunkForm({ ...trunkForm, register: e.target.checked ? 1 : 0 })} />
              Use REGISTER auth (else IP auth)
            </label>
          </div>
          <button className="btn btn-primary mt-3" onClick={addTrunk}>Add trunk</button>
        </div>

        <div className="card">
          <h3 className="font-semibold mb-3">Add phone number (DID)</h3>
          <div className="space-y-2">
            <input className="input" placeholder="+918031339140" value={numForm.number} onChange={(e) => setNumForm({ ...numForm, number: e.target.value })} />
            <select className="input" value={numForm.trunk_id} onChange={(e) => setNumForm({ ...numForm, trunk_id: e.target.value })}>
              <option value="">Trunk…</option>{trunks.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <select className="input" value={numForm.inbound_agent_id} onChange={(e) => setNumForm({ ...numForm, inbound_agent_id: e.target.value })}>
              <option value="">Inbound agent…</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
          <button className="btn btn-primary mt-3" onClick={addNumber}>Add number</button>
        </div>
      </div>

      <div className="card mb-6">
        <h3 className="font-semibold mb-3">Trunks</h3>
        {trunks.map((t) => (
          <div key={t.id} className="flex items-center justify-between py-2 border-b border-slate-100 text-sm">
            <span>{t.name} · {t.sip_domain} · <span className="badge bg-slate-100">{t.config_status}</span></span>
            <span className="flex gap-2">
              <button className="btn btn-ghost" onClick={() => showInstr(t)}>SIP setup</button>
              <button className="btn btn-primary" onClick={() => apply(t)}>Apply to FreeSWITCH</button>
            </span>
          </div>
        ))}
        {trunks.length === 0 && <div className="text-sm text-slate-400">No trunks</div>}
      </div>

      {instr && (
        <div className="card mb-6 bg-slate-900 text-slate-100">
          <h3 className="font-semibold mb-2">Required SIP trunk configuration ({instr.provider})</h3>
          <div className="text-sm space-y-1 font-mono">
            <div>SIP domain: {instr.sip_domain}</div>
            <div>Auth: {instr.auth_mode}</div>
            <div>FS signaling port: {instr.freeswitch_signaling_port}</div>
            <div>RTP range: {instr.rtp_port_range}</div>
          </div>
          <ul className="list-disc ml-5 mt-2 text-sm text-slate-300">
            {instr.notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      )}

      <div className="card">
        <h3 className="font-semibold mb-3">Numbers</h3>
        {numbers.map((n) => (
          <div key={n.id} className="flex items-center justify-between py-2 border-b border-slate-100 text-sm">
            <span>{n.number} · {n.direction} · <span className="badge bg-slate-100">{n.config_status}</span></span>
            <button className="btn btn-ghost text-red-600" onClick={() => api.deleteNumber(n.id).then(load)}>Delete</button>
          </div>
        ))}
        {numbers.length === 0 && <div className="text-sm text-slate-400">No numbers</div>}
      </div>
    </div>
  )
}
