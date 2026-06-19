import { useState, useMemo, useEffect } from 'react'
import { Search, ChevronLeft, ChevronRight, FileText, Repeat, Loader2, PhoneOff, RefreshCw } from 'lucide-react'
import { api } from '../../api.js'
import { callFromApi } from '../../data/adapters.js'

const PER_PAGE = 10
const fmtDur = (s) => s === 0 ? '—' : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

const SOURCE_STYLE = {
  Inbound: 'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400',
  Outbound: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-400',
}

function StatusBadge({ status }) {
  if (status === 'Live') {
    return <span className="badge bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400">
      <span className="h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" /> Live</span>
  }
  const ok = status === 'Success'
  return <span className={`badge ${ok
    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400'
    : 'bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400'}`}>
    <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-emerald-500' : 'bg-rose-500'}`} /> {status}</span>
}

export default function CallHistoryTable({ onOpenCall }) {
  const [calls, setCalls] = useState([])
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [statusFilter, setStatusFilter] = useState('All')
  const [page, setPage] = useState(0)

  const load = async () => {
    setLoading(true)
    try { setCalls((await api.calls(200)).map(callFromApi)) }
    catch { setCalls([]) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const filtered = useMemo(() => calls.filter((c) => {
    const matchQ = `${c.id} ${c.agent}`.toLowerCase().includes(q.toLowerCase())
    const matchS = statusFilter === 'All' || c.status === statusFilter
    return matchQ && matchS
  }), [calls, q, statusFilter])

  const pages = Math.max(1, Math.ceil(filtered.length / PER_PAGE))
  const safePage = Math.min(page, pages - 1)
  const rows = filtered.slice(safePage * PER_PAGE, safePage * PER_PAGE + PER_PAGE)

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-slate-900 dark:text-white">Call History</h1>
          <p className="text-xs text-slate-400">{loading ? 'Loading…' : `${filtered.length} of your calls`}</p>
        </div>
        <button className="btn btn-outline" onClick={load}><RefreshCw className="h-4 w-4" /> Refresh</button>
      </div>

      <div className="card-flat p-3 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-700 px-2.5 py-1.5 flex-1 min-w-48">
          <Search className="h-4 w-4 text-slate-400" />
          <input value={q} onChange={(e) => { setQ(e.target.value); setPage(0) }}
            placeholder="Search by ID or agent…" className="flex-1 bg-transparent text-sm outline-none text-slate-700 dark:text-slate-200" />
        </div>
        <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(0) }} className="input w-auto">
          {['All', 'Success', 'Failed'].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>

      <div className="card-flat overflow-hidden">
        {loading ? (
          <div className="grid place-items-center py-16 text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>
        ) : calls.length === 0 ? (
          <div className="grid place-items-center py-16 text-center">
            <PhoneOff className="h-8 w-8 text-slate-300 mb-3" />
            <div className="text-sm font-medium text-slate-600 dark:text-slate-300">No calls yet</div>
            <div className="text-xs text-slate-400 mt-1">Calls handled by your agents will appear here — only you can see them.</div>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto scroll-thin">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-slate-400 border-b border-slate-100 dark:border-slate-800">
                    <th className="px-4 py-3 font-semibold">Execution ID</th>
                    <th className="px-4 py-3 font-semibold">Agent</th>
                    <th className="px-4 py-3 font-semibold">Source</th>
                    <th className="px-4 py-3 font-semibold">Duration</th>
                    <th className="px-4 py-3 font-semibold">Status</th>
                    <th className="px-4 py-3 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c) => (
                    <tr key={c.id} className="border-b border-slate-50 dark:border-slate-800/60 hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors">
                      <td className="px-4 py-3 font-mono text-xs text-slate-500 dark:text-slate-400 truncate max-w-[180px]">{c.id}</td>
                      <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{c.agent}</td>
                      <td className="px-4 py-3"><span className={`badge ${SOURCE_STYLE[c.source] || SOURCE_STYLE.Inbound}`}>{c.source}</span></td>
                      <td className="px-4 py-3 tabular-nums text-slate-600 dark:text-slate-300">{fmtDur(c.duration)}</td>
                      <td className="px-4 py-3"><StatusBadge status={c.status} /></td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-2">
                          <button onClick={() => onOpenCall(c)} className="btn btn-outline !py-1.5 !px-2.5 text-xs">
                            <FileText className="h-3.5 w-3.5" /> View Transcript
                          </button>
                          <button disabled title="Replay is not available in this build"
                            className="btn !py-1.5 !px-2.5 text-xs text-slate-400 bg-slate-100 dark:bg-slate-800 cursor-not-allowed">
                            <Repeat className="h-3.5 w-3.5" /> Replay
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">No calls match your filters.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between px-4 py-3 border-t border-slate-100 dark:border-slate-800">
              <span className="text-xs text-slate-400">Page {safePage + 1} of {pages} · {filtered.length} results</span>
              <div className="flex items-center gap-1">
                <button disabled={safePage === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}
                  className="btn btn-outline !px-2 disabled:opacity-40 disabled:cursor-not-allowed"><ChevronLeft className="h-4 w-4" /></button>
                <button disabled={safePage >= pages - 1} onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                  className="btn btn-outline !px-2 disabled:opacity-40 disabled:cursor-not-allowed"><ChevronRight className="h-4 w-4" /></button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
