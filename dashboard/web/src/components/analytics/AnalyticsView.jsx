import { useState, useEffect } from 'react'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, Tooltip, Cell, PieChart, Pie,
} from 'recharts'
import { PhoneCall, FileText, Languages, ArrowRightLeft, Loader2, RefreshCw, AlertTriangle } from 'lucide-react'
import { api } from '../../api.js'
import { callFromApi } from '../../data/adapters.js'

const SENTIMENT_COLOR = { positive: '#22c55e', neutral: '#94a3b8', negative: '#f43f5e', unknown: '#cbd5e1' }
const PIE_COLORS = ['#3B5BFF', '#22c55e', '#a855f7', '#f59e0b', '#64748b']
const langName = (c) => ({ hi: 'Hindi', en: 'English', gu: 'Gujarati' }[(c || '').split('-')[0]] || (c || 'unknown'))

function Kpi({ icon: Icon, label, value, tint }) {
  return (
    <div className="card-flat p-5">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-[11px] uppercase tracking-wider text-slate-400">{label}</div>
          <div className="mt-1 text-3xl font-extrabold text-slate-900 dark:text-white">{value}</div>
        </div>
        <span className={`h-10 w-10 rounded-xl grid place-items-center ${tint}`}><Icon className="h-5 w-5" /></span>
      </div>
    </div>
  )
}

function ChartCard({ title, empty, data, children }) {
  return (
    <div className="card-flat p-5">
      <div className="section-label mb-4">{title}</div>
      {(!data || data.length === 0)
        ? <div className="h-48 grid place-items-center text-sm text-slate-400">{empty}</div>
        : <div className="h-48">{children}</div>}
    </div>
  )
}

export default function AnalyticsView() {
  const [stats, setStats] = useState(null)
  const [calls, setCalls] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true); setError('')
    try {
      const [a, c] = await Promise.all([api.analytics(), api.calls(200)])
      setStats(a); setCalls(c.map(callFromApi))
    } catch (e) { setError(e.message); setStats(null); setCalls([]) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const sentiment = Object.entries(stats?.summaries_by_sentiment || {}).map(([name, value]) => ({ name, value }))
  const bySource = Object.entries(calls.reduce((m, c) => { m[c.source] = (m[c.source] || 0) + 1; return m }, {})).map(([name, value]) => ({ name, value }))
  const byLang = Object.entries(calls.reduce((m, c) => { const k = langName(c.language); m[k] = (m[k] || 0) + 1; return m }, {})).map(([name, value]) => ({ name, value }))
  const coverage = stats?.total_recordings ? Math.round((stats.with_transcript / stats.total_recordings) * 100) : 0

  if (loading) return <div className="h-full grid place-items-center text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-slate-900 dark:text-white">Analytics</h1>
          <p className="text-xs text-slate-400">Aggregated across your workspace's calls.</p>
        </div>
        <button className="btn btn-outline" onClick={load}><RefreshCw className="h-4 w-4" /> Refresh</button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 dark:border-rose-500/30 dark:bg-rose-500/10 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
          <AlertTriangle className="h-4 w-4" /> Couldn't load analytics: {error}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Kpi icon={PhoneCall} label="Total calls" value={stats?.total_recordings ?? 0} tint="bg-accent-soft text-accent dark:bg-accent/15" />
        <Kpi icon={FileText} label="Transcript coverage" value={`${coverage}%`} tint="bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400" />
        <Kpi icon={ArrowRightLeft} label="Active campaigns" value={stats?.campaigns ?? 0} tint="bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400" />
        <Kpi icon={Languages} label="Languages seen" value={byLang.length} tint="bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ChartCard title="Calls by direction" data={bySource} empty="No calls yet.">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bySource}>
              <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fill: '#94a3b8', fontSize: 12 }} />
              <Tooltip cursor={{ fill: 'rgba(148,163,184,0.1)' }} contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }} />
              <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                {bySource.map((e, i) => <Cell key={e.name} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Calls by language" data={byLang} empty="No calls yet.">
          <div className="flex items-center h-full">
            <ResponsiveContainer width="55%" height="100%">
              <PieChart>
                <Pie data={byLang} dataKey="value" nameKey="name" innerRadius={42} outerRadius={70} paddingAngle={2}>
                  {byLang.map((e, i) => <Cell key={e.name} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
            <div className="flex-1 space-y-2">
              {byLang.map((e, i) => (
                <div key={e.name} className="flex items-center gap-2 text-sm">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                  <span className="text-slate-600 dark:text-slate-300">{e.name}</span>
                  <span className="ml-auto font-semibold text-slate-800 dark:text-slate-100">{e.value}</span>
                </div>
              ))}
            </div>
          </div>
        </ChartCard>

        <ChartCard title="Sentiment (summarized calls)" data={sentiment} empty="No summarized calls yet.">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={sentiment}>
              <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fill: '#94a3b8', fontSize: 12 }} />
              <Tooltip cursor={{ fill: 'rgba(148,163,184,0.1)' }} contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }} />
              <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                {sentiment.map((e) => <Cell key={e.name} fill={SENTIMENT_COLOR[e.name] || '#94a3b8'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <div className="card-flat p-5">
          <div className="section-label mb-4">Workspace totals</div>
          <div className="grid grid-cols-2 gap-4">
            {[['Agents', stats?.agents ?? 0], ['Campaigns', stats?.campaigns ?? 0], ['Leads', stats?.leads ?? 0], ['With transcript', stats?.with_transcript ?? 0]].map(([k, v]) => (
              <div key={k} className="rounded-xl border border-slate-200 dark:border-slate-700 p-4">
                <div className="text-[11px] text-slate-400 uppercase tracking-wider">{k}</div>
                <div className="mt-1 text-xl font-bold text-slate-800 dark:text-slate-100">{v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
