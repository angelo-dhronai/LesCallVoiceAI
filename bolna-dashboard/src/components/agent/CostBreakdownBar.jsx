import { useMemo } from 'react'

// Harmonious palette — one entry per cost segment. Dynamic values, so inline
// style backgroundColor is the right call here.
const SEGMENTS = [
  { key: 'transcriber', label: 'Transcriber', color: '#3B5BFF' },
  { key: 'llm', label: 'LLM', color: '#22B8A0' },
  { key: 'voice', label: 'Voice', color: '#F59E0B' },
  { key: 'telephony', label: 'Telephony', color: '#A855F7' },
  { key: 'platform', label: 'Platform', color: '#94A3B8' },
]

function CostBreakdownBar({ breakdown }) {
  const total = useMemo(
    () => SEGMENTS.reduce((sum, s) => sum + (breakdown?.[s.key] ?? 0), 0),
    [breakdown],
  )

  const rows = SEGMENTS.map((s) => {
    const value = breakdown?.[s.key] ?? 0
    const pct = total > 0 ? (value / total) * 100 : 0
    return { ...s, value, pct }
  })

  return (
    <div className="card p-5">
      <div className="flex items-end justify-between mb-3">
        <span className="section-label">Cost per minute</span>
        <span className="text-2xl font-bold tracking-tight text-gray-900 dark:text-white">
          ~${total.toFixed(3)}
          <span className="text-sm font-medium text-gray-400 dark:text-gray-500">/min</span>
        </span>
      </div>

      {/* Single stacked bar */}
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-white/5">
        {rows.map((r) => (
          <div
            key={r.key}
            className="h-full first:rounded-l-full last:rounded-r-full transition-all"
            style={{ width: `${r.pct}%`, backgroundColor: r.color }}
            title={`${r.label}: $${r.value.toFixed(3)} (${r.pct.toFixed(0)}%)`}
          />
        ))}
      </div>

      {/* Legend */}
      <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-5">
        {rows.map((r) => (
          <div key={r.key} className="flex items-center gap-2">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: r.color }}
            />
            <div className="min-w-0 leading-tight">
              <div className="truncate text-xs font-medium text-gray-700 dark:text-gray-200">
                {r.label}
              </div>
              <div className="text-[11px] text-gray-400 dark:text-gray-500">
                ${r.value.toFixed(3)} · {r.pct.toFixed(0)}%
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default CostBreakdownBar
