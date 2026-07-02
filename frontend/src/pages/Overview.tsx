import { useState } from 'react'
import {
  Bot, Megaphone, Users, Phone, CalendarClock, Clock, PhoneIncoming, Timer,
  IndianRupee, Mic, Volume2, Activity, PhoneOutgoing, PhoneMissed, PhoneCall,
  CalendarCheck, BellRing, TrendingUp,
} from 'lucide-react'
import { StatCard } from '../components/StatCard'
import { Card, cx, SectionHead } from '../components/ui'
import { Sparkline } from '../components/charts'
import { useOverview } from '../api/hooks/useDashboard'
import { inr, num } from '../lib/format'

const periods = ['Today', 'Last 7 Days', 'Last 30 Days'] as const

export default function Overview() {
  const { data: d, isDemo } = useOverview()
  const [period, setPeriod] = useState<(typeof periods)[number]>('Today')

  return (
    <div className="space-y-6">
      {isDemo && (
        <div className="flex items-center gap-2 rounded-xl bg-amber-400/10 px-3 py-2 text-[12px] font-medium text-amber-600 ring-1 ring-amber-400/25 dark:text-amber-300">
          <Activity size={14} /> Preview mode — showing representative data (backend at <code className="font-mono">:8080</code> not reachable from this machine).
        </div>
      )}

      {/* Bento hero row */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <Card hover glow="indigo" className="relative col-span-1 overflow-hidden p-6 lg:col-span-5">
          <div className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-indigo-500/20 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-12 -left-6 h-40 w-40 rounded-full bg-mint-400/20 blur-3xl" />
          <div className="relative">
            <div className="flex items-center justify-between">
              <span className="eyebrow">This Month — Estimated Billing</span>
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-indigo-500/15 text-indigo-500 dark:text-indigo-300"><IndianRupee size={18} /></span>
            </div>
            <div className="mt-3 flex items-end gap-3">
              <span className="metric text-[52px] leading-none">{inr(d.month.cost, 2)}</span>
              <span className="mb-2 inline-flex items-center gap-1 rounded-full bg-mint-400/15 px-2 py-0.5 text-[12px] font-bold text-mint-600 dark:text-mint-300"><TrendingUp size={13} /> {num(d.month.calls)} calls</span>
            </div>
            <div className="mt-1 text-[13px] text-dim">STT + TTS usage · 30-day estimate</div>
            <div className="mt-5"><Sparkline data={d.spark.calls} color="var(--accent-2)" height={70} /></div>
            <div className="mt-4 grid grid-cols-3 gap-3">
              {[['Today', inr(d.today.cost)], ['Last 7 Days', inr(d.week.cost)], ['Inbound', num(d.totals.inbound)]].map(([k, v]) => (
                <div key={k} className="rounded-xl surface-2 px-3 py-2.5">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-dim">{k}</div>
                  <div className="metric mt-1 text-[18px]">{v}</div>
                </div>
              ))}
            </div>
          </div>
        </Card>

        <div className="col-span-1 grid grid-cols-2 gap-4 lg:col-span-7">
          <StatCard label="Total Calls" value={num(d.totals.calls)} icon={<Phone size={18} />} iconTone="mint" spark={d.spark.calls} sparkColor="var(--accent)" />
          <StatCard label="Inbound" value={num(d.totals.inbound)} icon={<PhoneIncoming size={18} />} iconTone="indigo" spark={d.spark.inbound} sparkColor="var(--accent-2)" />
          <StatCard label="Minutes Used" value={num(d.totals.minutes_used)} icon={<Timer size={18} />} iconTone="amber" spark={d.spark.minutes} sparkColor="#f59e0b" />
          <StatCard label="Calls Today" value={num(d.today.calls)} trend={d.today.trend} icon={<CalendarClock size={18} />} iconTone="mint" spark={d.spark.today} sparkColor="var(--accent)" />
        </div>
      </div>

      {/* Secondary metric strip */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Agents" value={num(d.totals.agents)} icon={<Bot size={16} />} iconTone="indigo" />
        <StatCard label="Campaigns" value={num(d.totals.campaigns)} icon={<Megaphone size={16} />} iconTone="mint" />
        <StatCard label="Leads" value={num(d.totals.leads)} icon={<Users size={16} />} iconTone="amber" />
        <StatCard label="Calls (7d)" value={num(d.week.calls)} icon={<Clock size={16} />} iconTone="indigo" />
        <Card hover className="flex flex-col justify-between p-5">
          <div className="eyebrow">STT Today</div>
          <div className="metric mt-2 text-[26px]">{num(d.today.stt_seconds)}s</div>
          <div className="mt-1 inline-flex items-center gap-1 text-[12px] text-dim"><Mic size={13} /> {inr(d.today.stt_cost)} cost</div>
        </Card>
        <Card hover className="flex flex-col justify-between p-5">
          <div className="eyebrow">TTS Today</div>
          <div className="metric mt-2 text-[26px]">{num(d.today.tts_chars)}</div>
          <div className="mt-1 inline-flex items-center gap-1 text-[12px] text-dim"><Volume2 size={13} /> {inr(d.today.tts_cost)} cost</div>
        </Card>
      </div>

      {/* Runtime health + cost cards */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="flex items-center gap-4 p-5 lg:col-span-1" glow={d.runtime === 'degraded' ? 'indigo' : undefined}>
          <div className={cx('grid h-12 w-12 place-items-center rounded-2xl', d.runtime === 'degraded' ? 'bg-amber-400/15 text-amber-500' : 'bg-mint-400/15 text-mint-500')}>
            <Activity size={22} />
          </div>
          <div>
            <div className="eyebrow">Runtime Health</div>
            <div className="metric mt-1 text-[22px] capitalize">{d.runtime}</div>
            <div className="text-[12px] text-dim">No active calls · FreeSWITCH → AI</div>
          </div>
        </Card>
        <Card hover className="p-5">
          <div className="eyebrow">Last 7 Days — Est.</div>
          <div className="metric mt-2 text-[28px]">{inr(d.week.cost)}</div>
          <div className="mt-1 text-[12px] text-dim">{num(d.week.calls)} calls</div>
          <div className="mt-3"><Sparkline data={d.calls_7d} color="var(--accent-2)" height={42} /></div>
        </Card>
        <Card hover className="p-5">
          <div className="eyebrow">Last 30 Days — Est.</div>
          <div className="metric mt-2 text-[28px]">{inr(d.month.cost, 2)}</div>
          <div className="mt-1 text-[12px] text-dim">{num(d.month.calls)} calls</div>
          <div className="mt-3"><Sparkline data={d.spark.calls} color="var(--accent)" height={42} /></div>
        </Card>
      </div>

      {/* Activity KPIs */}
      <div>
        <SectionHead title="Activity KPIs" sub="Calling activity for the selected period"
          right={
            <div className="flex rounded-xl bg-[var(--surface-2)] p-1 ring-1 ring-[var(--border)]">
              {periods.map((p) => (
                <button key={p} onClick={() => setPeriod(p)}
                  className={cx('rounded-lg px-3 py-1.5 text-[12px] font-semibold transition', period === p ? 'bg-[var(--surface-solid)] text-[var(--text)] shadow-sm' : 'text-dim hover:text-soft')}>
                  {p}
                </button>
              ))}
            </div>
          } />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard label="Total Calls" value={num(d.kpis.total)} icon={<PhoneCall size={16} />} iconTone="indigo" />
          <StatCard label="Answered Calls" value={num(d.kpis.answered)} icon={<PhoneIncoming size={16} />} iconTone="mint" />
          <StatCard label="Missed Calls" value={num(d.kpis.missed)} icon={<PhoneMissed size={16} />} iconTone="rose" />
          <StatCard label="Inbound Calls" value={num(d.kpis.inbound)} icon={<PhoneIncoming size={16} />} iconTone="indigo" />
          <StatCard label="Outbound Calls" value={num(d.kpis.outbound)} icon={<PhoneOutgoing size={16} />} iconTone="indigo" />
          <StatCard label="Appointments Booked" value={num(d.kpis.appointments)} icon={<CalendarCheck size={16} />} iconTone="mint" />
          <StatCard label="Follow-Ups Required" value={num(d.kpis.followups)} icon={<BellRing size={16} />} iconTone="amber" />
          <StatCard label="Minutes Used" value={num(d.kpis.minutes_used)} icon={<Timer size={16} />} iconTone="indigo" />
        </div>
      </div>
    </div>
  )
}
