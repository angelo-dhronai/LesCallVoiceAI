import { ResponsiveContainer, AreaChart, Area } from 'recharts'
import { ArrowUp, ArrowDown } from 'lucide-react'

// Format a KPI value depending on its semantic key/unit.
function formatKpiValue(kpi) {
  if (kpi.key === 'duration') return `${Math.round(kpi.value)}s`
  if (kpi.key === 'cost') return `$${Number(kpi.value).toFixed(2)}`
  // sentiment + intent are percentages
  return `${Math.round(kpi.value)}%`
}

// For 'cost' and 'duration' a DOWN trend is good; for 'sentiment'/'intent' UP is good.
function isPositiveTrend(kpi) {
  const downIsGood = kpi.key === 'cost' || kpi.key === 'duration'
  return downIsGood ? kpi.direction === 'down' : kpi.direction === 'up'
}

function KpiCard({ kpi }) {
  const good = isPositiveTrend(kpi)
  const TrendIcon = kpi.direction === 'up' ? ArrowUp : ArrowDown
  const trendColor = good
    ? 'text-emerald-600 dark:text-emerald-400'
    : 'text-red-600 dark:text-red-400'
  const stroke = good ? '#10b981' : '#ef4444'
  const gradId = `spark-${kpi.key}`
  const data = (kpi.spark || []).map((v, i) => ({ i, value: v }))

  return (
    <div className="card p-4 flex flex-col">
      <span className="section-label">{kpi.label}</span>
      <div className="mt-1 flex items-end justify-between gap-2">
        <span className="metric-number">{formatKpiValue(kpi)}</span>
        <span className={`inline-flex items-center gap-0.5 text-xs font-semibold ${trendColor}`}>
          <TrendIcon size={13} strokeWidth={2.5} />
          {Math.abs(kpi.delta)}%
        </span>
      </div>
      <div className="mt-3 h-10 -mx-1">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity={0.35} />
                <stop offset="100%" stopColor={stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="value"
              stroke={stroke}
              strokeWidth={2}
              fill={`url(#${gradId})`}
              isAnimationActive={false}
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function AnalyticsPanel({ analytics }) {
  const kpis = analytics?.kpis || []
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {kpis.map((kpi) => (
        <KpiCard key={kpi.key} kpi={kpi} />
      ))}
    </div>
  )
}

export default AnalyticsPanel
