import { useState, useEffect } from 'react'
import {
  Bot, Megaphone, Users, PhoneCall, ArrowUpRight, FileText, Loader2, RefreshCw, Plus,
} from 'lucide-react'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, Tooltip, Cell,
  PieChart, Pie, AreaChart, Area,
} from 'recharts'
import { api } from '../../api.js'
import { useAuth, deriveIdentity } from '../../auth.jsx'
import { callFromApi } from '../../data/adapters.js'

const SENTIMENT_COLOR = { positive: '#22c55e', neutral: '#94a3b8', negative: '#f43f5e', unknown: '#cbd5e1' }
const OUTCOME_COLORS = ['#3B5BFF', '#22c55e', '#a855f7', '#f59e0b', '#64748b']

const trend = (n) => Array.from({ length: 10 }, (_, i) => ({ i, v: Math.max(0, n * (0.5 + Math.sin(i / 1.6) * 0.25 + i * 0.03)) }))

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
      <div className="h-8 mt-2 -mx-1">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={trend(value || 1)} margin={{ top: 2, bottom: 0, left: 0, right: 0 }}>
            <defs>
              <linearGradient id={`kpi-${label}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#3B5BFF" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#3B5BFF" stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area type="monotone" dataKey="v" stroke="#3B5BFF" strokeWidth={1.6} fill={`url(#kpi-${label})`} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function fmtDur(s) { return s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : '—' }

export default function Overview({ onNavigate, onOpenCall }) {
  const me = deriveIdentity(useAuth())
  const [stats, setStats] = useState(null)
  const [calls, setCalls] = useState([])
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const [a, c] = await Promise.all([api.analytics(), api.calls(8)])
      setStats(a)
      setCalls(c.map(callFromApi))
    } catch { setStats(null); setCalls([]) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const sentiment = Object.entries(stats?.summaries_by_sentiment || {}).map(([k, v]) => ({ name: k, value: v }))
  const outcome = Object.entries(stats?.summaries_by_outcome || {}).map(([k, v]) => ({ name: k, value: v }))

  return (
    <div className="p-6 space-y-6">
      {/* Greeting */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-white">Welcome back, {me.name.split(' ')[0]}</h1>
          <p className="text-sm text-slate-400">Here's what's happening in your workspace.</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-outline" onClick={load}><RefreshCw className="h-4 w-4" /> Refresh</button>
          <button className="btn btn-primary" onClick={() => onNavigate('agents')}><Plus className="h-4 w-4" /> New agent</button>
        </div>
      </div>

      {loading ? (
        <div className="grid place-items-center py-24 text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>
      ) : (
        <>
          {/* KPI row */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <Kpi icon={Bot} label="Agents" value={stats?.agents ?? 0} tint="bg-accent-soft text-accent dark:bg-accent/15" />
            <Kpi icon={Megaphone} label="Campaigns" value={stats?.campaigns ?? 0} tint="bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400" />
            <Kpi icon={Users} label="Leads" value={stats?.leads ?? 0} tint="bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400" />
            <Kpi icon={PhoneCall} label="Calls" value={stats?.total_recordings ?? 0} tint="bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400" />
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="card-flat p-5">
              <div className="section-label mb-4">Call sentiment</div>
              {sentiment.length === 0 ? (
                <div className="h-48 grid place-items-center text-sm text-slate-400">No summarized calls yet.</div>
              ) : (
                <div className="h-48">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={sentiment}>
                      <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fill: '#94a3b8', fontSize: 12 }} />
                      <Tooltip cursor={{ fill: 'rgba(148,163,184,0.1)' }}
                        contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }} />
                      <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                        {sentiment.map((e) => <Cell key={e.name} fill={SENTIMENT_COLOR[e.name] || '#94a3b8'} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            <div className="card-flat p-5">
              <div className="section-label mb-4">Call outcomes</div>
              {outcome.length === 0 ? (
                <div className="h-48 grid place-items-center text-sm text-slate-400">No summarized calls yet.</div>
              ) : (
                <div className="h-48 flex items-center">
                  <ResponsiveContainer width="60%" height="100%">
                    <PieChart>
                      <Pie data={outcome} dataKey="value" nameKey="name" innerRadius={42} outerRadius={70} paddingAngle={2}>
                        {outcome.map((e, i) => <Cell key={e.name} fill={OUTCOME_COLORS[i % OUTCOME_COLORS.length]} />)}
                      </Pie>
                      <Tooltip contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex-1 space-y-2">
                    {outcome.map((e, i) => (
                      <div key={e.name} className="flex items-center gap-2 text-sm">
                        <span className="h-2.5 w-2.5 rounded-sm" style={{ background: OUTCOME_COLORS[i % OUTCOME_COLORS.length] }} />
                        <span className="text-slate-600 dark:text-slate-300 capitalize">{e.name}</span>
                        <span className="ml-auto font-semibold text-slate-800 dark:text-slate-100">{e.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Recent calls */}
          <div className="card-flat overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-slate-800">
              <div className="section-label">Recent calls</div>
              <button className="text-sm font-semibold text-accent hover:underline inline-flex items-center gap-1"
                onClick={() => onNavigate('calls')}>View all <ArrowUpRight className="h-3.5 w-3.5" /></button>
            </div>
            {calls.length === 0 ? (
              <div className="px-5 py-10 text-center text-sm text-slate-400">No calls yet — they'll show up here once your agents take calls.</div>
            ) : (
              <div className="divide-y divide-slate-50 dark:divide-slate-800/60">
                {calls.map((c) => (
                  <button key={c.id} onClick={() => onOpenCall(c)}
                    className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors">
                    <span className="h-9 w-9 rounded-full bg-slate-100 dark:bg-slate-800 grid place-items-center text-slate-500"><PhoneCall className="h-4 w-4" /></span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">{c.agent}</div>
                      <div className="font-mono text-[11px] text-slate-400 truncate">{c.id}</div>
                    </div>
                    <span className="text-xs text-slate-400 tabular-nums hidden sm:block">{fmtDur(c.duration)}</span>
                    <span className={`badge ${c.status === 'Failed'
                      ? 'bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400'
                      : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400'}`}>{c.status}</span>
                    <FileText className="h-4 w-4 text-slate-300" />
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
