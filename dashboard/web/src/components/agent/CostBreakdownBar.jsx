// Single horizontal stacked bar of per-minute cost, with a legend below.
const SEGMENTS = [
  { key: 'transcriber', label: 'Transcriber', color: '#3B5BFF' },
  { key: 'llm', label: 'LLM', color: '#22c55e' },
  { key: 'voice', label: 'Voice', color: '#a855f7' },
  { key: 'telephony', label: 'Telephony', color: '#f59e0b' },
  { key: 'platform', label: 'Platform', color: '#64748b' },
]

export default function CostBreakdownBar({ cost }) {
  const total = SEGMENTS.reduce((s, seg) => s + (cost[seg.key] || 0), 0)

  return (
    <div className="card-flat p-5">
      <div className="flex items-end justify-between mb-3">
        <div>
          <div className="section-label">Estimated cost</div>
          <div className="text-2xl font-extrabold text-slate-900 dark:text-white">
            ~${total.toFixed(3)}<span className="text-base font-semibold text-slate-400">/min</span>
          </div>
        </div>
      </div>

      <div className="flex h-3 w-full rounded-full overflow-hidden bg-slate-100 dark:bg-slate-800">
        {SEGMENTS.map((seg) => {
          const pct = total ? ((cost[seg.key] || 0) / total) * 100 : 0
          return (
            <div
              key={seg.key}
              title={`${seg.label}: $${(cost[seg.key] || 0).toFixed(3)}`}
              style={{ width: `${pct}%`, backgroundColor: seg.color }}
              className="h-full first:rounded-l-full last:rounded-r-full transition-[width]"
            />
          )
        })}
      </div>

      <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {SEGMENTS.map((seg) => {
          const v = cost[seg.key] || 0
          const pct = total ? (v / total) * 100 : 0
          return (
            <div key={seg.key} className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-sm shrink-0" style={{ backgroundColor: seg.color }} />
              <div className="leading-tight">
                <div className="text-[11px] text-slate-400">{seg.label}</div>
                <div className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                  ${v.toFixed(3)} <span className="text-xs text-slate-400">· {pct.toFixed(0)}%</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
