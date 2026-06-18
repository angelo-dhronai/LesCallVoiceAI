import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function Calls() {
  const [calls, setCalls] = useState([])
  const [sel, setSel] = useState(null)
  const [turns, setTurns] = useState([])
  const [summary, setSummary] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  useEffect(() => { api.calls(100).then(setCalls).catch((e) => setMsg(e.message)) }, [])

  const open = async (c) => {
    setSel(c); setTurns([]); setSummary(c.summary ? { summary: c.summary, sentiment: c.sentiment, outcome: c.outcome } : null)
    if (c.has_transcript) {
      try { setTurns((await api.transcript(c.conn_id)).turns) } catch (e) { setMsg(e.message) }
    }
  }
  const summarize = async () => {
    setBusy(true); setMsg(null)
    try { setSummary(await api.summary(sel.conn_id)) }
    catch (e) { setMsg(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Calls, Transcripts & AI Summaries</h1>
      {msg && <div className="mb-4 text-sm text-red-600">{msg}</div>}
      <div className="grid md:grid-cols-3 gap-6">
        <div className="space-y-1 max-h-[70vh] overflow-auto">
          {calls.map((c) => (
            <div key={c.conn_id} onClick={() => open(c)}
              className={`card py-3 cursor-pointer ${sel?.conn_id === c.conn_id ? 'ring-2 ring-brand' : ''}`}>
              <div className="text-sm font-medium truncate">{c.conn_id}</div>
              <div className="text-xs text-slate-500">
                {c.has_transcript ? 'transcript' : 'no transcript'}
                {c.outcome ? ` · ${c.outcome}` : ''}
              </div>
            </div>
          ))}
        </div>

        <div className="md:col-span-2 space-y-4">
          {!sel && <div className="text-sm text-slate-400">Select a call</div>}
          {sel && (
            <>
              <div className="card">
                <div className="flex justify-between items-center mb-2">
                  <h3 className="font-semibold">AI Summary</h3>
                  <button className="btn btn-primary" disabled={busy} onClick={summarize}>
                    {busy ? 'Summarizing…' : summary ? 'Regenerate' : 'Generate summary'}
                  </button>
                </div>
                {summary ? (
                  <div className="text-sm space-y-1">
                    <p>{summary.summary}</p>
                    <div className="flex gap-2 mt-2">
                      <span className="badge bg-slate-100">sentiment: {summary.sentiment}</span>
                      <span className="badge bg-slate-100">outcome: {summary.outcome}</span>
                    </div>
                    {summary.key_points?.length > 0 && (
                      <ul className="list-disc ml-5 mt-2 text-slate-600">
                        {summary.key_points.map((k, i) => <li key={i}>{k}</li>)}
                      </ul>
                    )}
                  </div>
                ) : <div className="text-sm text-slate-400">No summary yet</div>}
              </div>

              <div className="card max-h-[50vh] overflow-auto">
                <h3 className="font-semibold mb-3">Transcript</h3>
                {turns.map((t, i) => (
                  <div key={i} className={`mb-2 ${t.role === 'agent' ? '' : 'text-right'}`}>
                    <span className={`inline-block px-3 py-2 rounded-lg text-sm ${t.role === 'agent' ? 'bg-indigo-50' : 'bg-slate-100'}`}>
                      <b className="text-xs text-slate-500 block">{t.role}</b>{t.text}
                    </span>
                  </div>
                ))}
                {turns.length === 0 && <div className="text-sm text-slate-400">No transcript</div>}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
