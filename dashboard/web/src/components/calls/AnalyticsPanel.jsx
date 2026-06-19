import { Area, AreaChart, ResponsiveContainer } from 'recharts'
import { ArrowDownRight, ArrowUpRight } from 'lucide-react'

const spark = (base) => Array.from({ length: 12 }, (_, i) => ({
  i, v: +(base + Math.sin(i / 1.7) * base * 0.18 + (i % 3) * base * 0.04).toFixed(3),
}))

function KpiCard({ kpi }) {
  const up = kpi.dir === 'up'
  const good = kpi.key === 'cost' ? !up : up
  return (
    <div className="card-flat p-4">
      <div className="text-[11px] text-slate-400 uppercase tracking-wider">{kpi.label}</div>
      <div className="mt-1 flex items-end justify-between gap-2">
        <div className="text-2xl font-extrabold text-slate-900 dark:text-white">{kpi.value}</div>
        {kpi.delta != null && (
          <span className={`inline-flex items-center gap-0.5 text-xs font-semibold ${good ? 'text-emerald-600' : 'text-rose-500'}`}>
            {up ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}{kpi.delta}%
          </span>
        )}
      </div>
      <div className="h-9 mt-2 -mx-1">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={kpi.spark} margin={{ top: 2, bottom: 0, left: 0, right: 0 }}>
            <defs>
              <linearGradient id={`g-${kpi.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#3B5BFF" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#3B5BFF" stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area type="monotone" dataKey="v" stroke="#3B5BFF" strokeWidth={1.8} fill={`url(#g-${kpi.key})`} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// KPIs derived from the real call (duration, sentiment, turns); sparklines are
// illustrative. Trend deltas are omitted when there's no historical series.
export default function AnalyticsPanel({ call, turnCount }) {
  const mins = Math.floor((call.duration || 0) / 60)
  const secs = (call.duration || 0) % 60
  const sentMap = { positive: '0.85', neutral: '0.55', negative: '0.25' }
  const kpis = [
    { key: 'duration', label: 'Call Duration', value: `${mins}:${String(secs).padStart(2, '0')}`, dir: 'up', spark: spark(call.duration || 60) },
    { key: 'sentiment', label: 'Sentiment', value: sentMap[call.sentiment] || '—', dir: call.sentiment === 'negative' ? 'down' : 'up', spark: spark(0.7) },
    { key: 'turns', label: 'Conversation Turns', value: String(turnCount || 0), dir: 'up', spark: spark(turnCount || 6) },
    { key: 'lang', label: 'Language', value: (call.language || 'auto').toUpperCase(), dir: 'up', spark: spark(0.9) },
  ]
  return (
    <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
      {kpis.map((k) => <KpiCard key={k.key} kpi={k} />)}
    </div>
  )
}
