import { useEffect, useState } from 'react'
import { api } from '../api.js'

function Stat({ label, value }) {
  return (
    <div className="card">
      <div className="text-3xl font-bold text-slate-900">{value ?? '—'}</div>
      <div className="text-sm text-slate-500 mt-1">{label}</div>
    </div>
  )
}

function Bars({ title, data }) {
  const entries = Object.entries(data || {})
  const max = Math.max(1, ...entries.map(([, v]) => v))
  return (
    <div className="card">
      <h3 className="font-semibold mb-3">{title}</h3>
      {entries.length === 0 && <div className="text-sm text-slate-400">No data yet</div>}
      {entries.map(([k, v]) => (
        <div key={k} className="mb-2">
          <div className="flex justify-between text-xs mb-0.5"><span>{k}</span><span>{v}</span></div>
          <div className="h-2 bg-slate-100 rounded">
            <div className="h-2 bg-brand rounded" style={{ width: `${(v / max) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  )
}

export default function Overview() {
  const [a, setA] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.analytics().then(setA).catch((e) => setErr(e.message)) }, [])

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Overview</h1>
      {err && <div className="text-red-600 mb-4">{err}</div>}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <Stat label="Recordings" value={a?.total_recordings} />
        <Stat label="With transcript" value={a?.with_transcript} />
        <Stat label="Leads" value={a?.leads} />
        <Stat label="Campaigns" value={a?.campaigns} />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <Bars title="Outcomes" data={a?.summaries_by_outcome} />
        <Bars title="Sentiment" data={a?.summaries_by_sentiment} />
      </div>
    </div>
  )
}
