import { useState, useMemo, useEffect } from 'react'
import { Search, FileText, RotateCcw, ChevronLeft, ChevronRight } from 'lucide-react'
import { callHistory, formatDuration } from '../../data/mockData.js'

const PAGE_SIZE = 10

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// "Jun 17, 09:12" — local-time, no external lib.
function formatTimestamp(iso) {
  const d = new Date(iso)
  const month = MONTHS[d.getMonth()]
  const day = d.getDate()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${month} ${day}, ${hh}:${mm}`
}

const TRIGGER_BADGE = {
  inbound: 'badge-blue',
  outbound: 'badge-amber',
  batch: 'badge-gray',
}

const RANGE_DAYS = {
  '7': 7,
  '30': 30,
  'all': Infinity,
}

function TriggerBadge({ source }) {
  return <span className={TRIGGER_BADGE[source] || 'badge-gray'}>{source}</span>
}

function StatusBadge({ status }) {
  if (status === 'success') return <span className="badge-green">Success</span>
  if (status === 'failed') return <span className="badge-red">Failed</span>
  // live
  return (
    <span className="badge-blue">
      <span className="h-1.5 w-1.5 rounded-full bg-accent dark:bg-blue-400 animate-pulse-dot" />
      Live
    </span>
  )
}

export default function CallHistoryTable({ onOpenCall }) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [range, setRange] = useState('all')
  const [page, setPage] = useState(1)

  // Reset to first page whenever any filter changes.
  useEffect(() => {
    setPage(1)
  }, [query, status, range])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const days = RANGE_DAYS[range]
    const cutoff = days === Infinity ? -Infinity : Date.now() - days * 86400000
    return callHistory.filter((c) => {
      if (status !== 'all' && c.status !== status) return false
      if (cutoff !== -Infinity && new Date(c.timestamp).getTime() < cutoff) return false
      if (q) {
        const hay = `${c.executionId} ${c.agentName}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [query, status, range])

  const total = filtered.length
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const safePage = Math.min(page, pageCount)
  const start = (safePage - 1) * PAGE_SIZE
  const rows = filtered.slice(start, start + PAGE_SIZE)
  const showingFrom = total === 0 ? 0 : start + 1
  const showingTo = Math.min(start + PAGE_SIZE, total)

  return (
    <div className="space-y-4">
      {/* Title row */}
      <div className="flex items-center gap-3">
        <h1 className="page-title">Call History</h1>
        <span className="text-sm text-gray-400 dark:text-gray-500">{callHistory.length} calls</span>
      </div>

      {/* Toolbar */}
      <div className="card p-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by execution ID or agent…"
              className="input pl-9"
            />
          </div>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="input sm:w-40"
            aria-label="Filter by status"
          >
            <option value="all">All statuses</option>
            <option value="success">Success</option>
            <option value="failed">Failed</option>
            <option value="live">Live</option>
          </select>
          <select
            value={range}
            onChange={(e) => setRange(e.target.value)}
            className="input sm:w-40"
            aria-label="Filter by date range"
          >
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="all">All time</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[840px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-white/[0.02]">
                <th className="section-label px-4 py-3 text-left">Execution ID</th>
                <th className="section-label px-4 py-3 text-left">Agent Name</th>
                <th className="section-label px-4 py-3 text-left">Trigger</th>
                <th className="section-label px-4 py-3 text-left">Duration</th>
                <th className="section-label px-4 py-3 text-left">Status</th>
                <th className="section-label px-4 py-3 text-left">Timestamp</th>
                <th className="section-label px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-gray-400 dark:text-gray-500">
                    No calls match your filters.
                  </td>
                </tr>
              )}
              {rows.map((c) => (
                <tr
                  key={c.executionId}
                  className="border-b border-gray-100 dark:border-white/5 last:border-0 hover:bg-gray-50 dark:hover:bg-white/[0.03] transition-colors"
                >
                  <td className="px-4 py-3">
                    <span
                      className="block max-w-[140px] truncate font-mono text-xs text-gray-700 dark:text-gray-300"
                      title={c.executionId}
                    >
                      {c.executionId}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-900 dark:text-gray-100">{c.agentName}</td>
                  <td className="px-4 py-3">
                    <TriggerBadge source={c.triggerSource} />
                  </td>
                  <td className="px-4 py-3 text-gray-700 dark:text-gray-300">
                    {c.status === 'live' || c.durationSeconds === 0 ? '—' : formatDuration(c.durationSeconds)}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-gray-600 dark:text-gray-400">
                    {formatTimestamp(c.timestamp)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => onOpenCall(c.executionId)}
                        className="btn-ghost"
                      >
                        <FileText className="h-4 w-4" />
                        <span className="hidden lg:inline">View Transcript</span>
                      </button>
                      <button
                        type="button"
                        disabled
                        title="Replay is disabled in demo"
                        className="btn-outline cursor-not-allowed opacity-50"
                      >
                        <RotateCcw className="h-4 w-4" />
                        <span className="hidden lg:inline">Replay</span>
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination footer */}
        <div className="flex flex-col gap-3 border-t border-gray-200 dark:border-white/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Showing {showingFrom}–{showingTo} of {total}
          </p>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={safePage <= 1}
              className="btn-outline disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft className="h-4 w-4" />
              <span className="hidden sm:inline">Prev</span>
            </button>
            {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPage(p)}
                className={
                  p === safePage
                    ? 'btn px-3 py-2 bg-accent text-white hover:bg-accent-hover'
                    : 'btn-ghost'
                }
                aria-current={p === safePage ? 'page' : undefined}
              >
                {p}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
              disabled={safePage >= pageCount}
              className="btn-outline disabled:cursor-not-allowed disabled:opacity-40"
            >
              <span className="hidden sm:inline">Next</span>
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
