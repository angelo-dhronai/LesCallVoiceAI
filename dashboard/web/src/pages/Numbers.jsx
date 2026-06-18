import { useEffect, useState } from 'react'
import { api } from '../api.js'

// Self-service number registration:
//  1) Server SIP settings panel — what to configure on the carrier (any SIP provider).
//  2) Create a trunk (provider domain + IPs to allow + outbound caller ID).
//  3) Add DID(s) + pick the inbound agent, then Apply (generates FS gateway +
//     inbound dialplan + ACL and reloads FreeSWITCH).
export default function Numbers() {
  const [info, setInfo] = useState(null)
  const [trunks, setTrunks] = useState([])
  const [numbers, setNumbers] = useState([])
  const [agents, setAgents] = useState([])
  const [trunkForm, setTrunkForm] = useState({
    name: '', provider: '', sip_domain: '', caller_id: '', provider_ips: '', register: 0,
  })
  const [numForm, setNumForm] = useState({ number: '', trunk_id: '', inbound_agent_id: '', direction: 'both' })
  const [msg, setMsg] = useState(null)

  const load = () => {
    api.serverInfo().then(setInfo).catch(() => {})
    api.trunks().then(setTrunks); api.numbers().then(setNumbers); api.agents().then(setAgents)
  }
  useEffect(() => { load() }, [])

  const copy = (t) => navigator.clipboard?.writeText(t)
  const addTrunk = async () => {
    try { await api.createTrunk(trunkForm); setTrunkForm({ name: '', provider: '', sip_domain: '', caller_id: '', provider_ips: '', register: 0 }); load(); setMsg('Trunk added') }
    catch (e) { setMsg(e.message) }
  }
  const addNumber = async () => {
    try {
      await api.createNumber({
        ...numForm,
        trunk_id: numForm.trunk_id ? Number(numForm.trunk_id) : null,
        inbound_agent_id: numForm.inbound_agent_id ? Number(numForm.inbound_agent_id) : null,
      })
      setNumForm({ number: '', trunk_id: '', inbound_agent_id: '', direction: 'both' }); load(); setMsg('Number added — click Apply on its trunk')
    } catch (e) { setMsg(e.message) }
  }
  const apply = async (t) => {
    try { const r = await api.applyTrunk(t.id); setMsg(`Apply: ${r.status} (${(r.written || []).length} FreeSWITCH files written)`); load() }
    catch (e) { setMsg(e.message) }
  }

  const Row = ({ label, value }) => (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-xs text-slate-400">{label}</span>
      <span className="flex items-center gap-2">
        <code className="text-sm text-green-300">{value}</code>
        <button className="text-xs text-slate-400 hover:text-white" onClick={() => copy(value)}>copy</button>
      </span>
    </div>
  )

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Register a Number</h1>
      {msg && <div className="mb-4 text-sm text-slate-700 bg-slate-100 rounded-lg px-3 py-2">{msg}</div>}

      {/* Step 0: our SIP settings to configure on the provider */}
      <div className="card mb-6 bg-brand-dark text-slate-100">
        <h3 className="font-semibold mb-1">① Configure these on your SIP carrier (Plivo, Twilio, Telnyx, …)</h3>
        <p className="text-xs text-slate-400 mb-3">Use this server's details to set up your inbound + outbound SIP trunk, then whitelist our IP.</p>
        {info ? (
          <div className="grid md:grid-cols-2 gap-x-8">
            <div>
              <div className="text-xs uppercase tracking-wide text-accent mb-1">Inbound (provider → us)</div>
              <Row label="SIP URI" value={info.inbound.sip_uri} />
              <Row label="Host / IP" value={info.inbound.host} />
              <Row label="Port" value={`${info.inbound.port}/${info.inbound.transport}`} />
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-accent mb-1">Outbound / media</div>
              <Row label="Whitelist our IP" value={info.outbound.from_ip} />
              <Row label="RTP range" value={info.rtp_port_range} />
              <Row label="ACL list" value={info.acl.list_name} />
            </div>
          </div>
        ) : <div className="text-sm text-slate-400">Loading server info…</div>}
        {info && (
          <div className="text-xs text-slate-400 mt-3 border-t border-brand-light/30 pt-2">
            {info.firewall_note}
            {info.acl.allowed_cidrs?.length > 0 && <> · Allowed provider IPs: {info.acl.allowed_cidrs.join(', ')}</>}
          </div>
        )}
      </div>

      <div className="grid md:grid-cols-2 gap-6 mb-6">
        {/* Step 1: trunk */}
        <div className="card">
          <h3 className="font-semibold mb-3">② Add your SIP trunk</h3>
          <div className="space-y-2">
            <input className="input" placeholder="Trunk name (e.g. my-carrier)" value={trunkForm.name} onChange={(e) => setTrunkForm({ ...trunkForm, name: e.target.value })} />
            <input className="input" placeholder="Provider SIP domain (e.g. sip.telnyx.com, xxx.zt.plivo.com)" value={trunkForm.sip_domain} onChange={(e) => setTrunkForm({ ...trunkForm, sip_domain: e.target.value })} />
            <input className="input" placeholder="Outbound caller ID (your DID/number)" value={trunkForm.caller_id} onChange={(e) => setTrunkForm({ ...trunkForm, caller_id: e.target.value })} />
            <input className="input" placeholder="Provider IPs to allow, comma-separated CIDRs" value={trunkForm.provider_ips} onChange={(e) => setTrunkForm({ ...trunkForm, provider_ips: e.target.value })} />
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={!!trunkForm.register} onChange={(e) => setTrunkForm({ ...trunkForm, register: e.target.checked ? 1 : 0 })} />
              Use REGISTER auth (else IP auth)
            </label>
          </div>
          <button className="btn btn-primary mt-3" onClick={addTrunk}>Add trunk</button>
        </div>

        {/* Step 2: number */}
        <div className="card">
          <h3 className="font-semibold mb-3">③ Add your number (inbound DID)</h3>
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
        <h3 className="font-semibold mb-3">④ Your trunks — Apply to activate</h3>
        {trunks.map((t) => (
          <div key={t.id} className="flex items-center justify-between py-2 border-b border-slate-100 text-sm">
            <span>{t.name} · {t.sip_domain} · <span className="badge bg-slate-100">{t.config_status}</span></span>
            <button className="btn btn-primary" onClick={() => apply(t)}>Apply to FreeSWITCH</button>
          </div>
        ))}
        {trunks.length === 0 && <div className="text-sm text-slate-400">No trunks yet</div>}
      </div>

      <div className="card">
        <h3 className="font-semibold mb-3">Your numbers</h3>
        {numbers.map((n) => (
          <div key={n.id} className="flex items-center justify-between py-2 border-b border-slate-100 text-sm">
            <span>{n.number} · {n.direction} · <span className="badge bg-slate-100">{n.config_status}</span></span>
            <button className="btn btn-ghost text-red-600" onClick={() => api.deleteNumber(n.id).then(load)}>Delete</button>
          </div>
        ))}
        {numbers.length === 0 && <div className="text-sm text-slate-400">No numbers yet</div>}
      </div>
    </div>
  )
}
