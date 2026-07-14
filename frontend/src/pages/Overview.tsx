import { useEffect, useState } from 'react'
import {
  Bot, Megaphone, Users, Phone, CalendarClock, Clock, PhoneIncoming, Timer,
  IndianRupee, Mic, Volume2, Activity, PhoneOutgoing, PhoneMissed, PhoneCall,
  CalendarCheck, BellRing, TrendingUp,
} from 'lucide-react'
import { StatCard } from '../components/StatCard'
import { Card, cx, SectionHead, SkeletonBlock } from '../components/ui'
import { PageHeader, Segmented } from '../components/kit'
import { Sparkline } from '../components/charts'
import { useOverview, useHealth } from '../api/hooks/useDashboard'
import { inr, num, dur, compact, pct } from '../lib/format'

const periods = ['Today', 'Last 7 Days', 'Last 30 Days'] as const
type Period = (typeof periods)[number]

/** Ticks every 5s so the freshness pill stays current. */
function useNowTick(intervalMs = 5000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return now
}

function OverviewSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading overview">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="col-span-1 lg:col-span-5"><SkeletonBlock h={260} className="rounded-2xl" /></div>
        <div className="col-span-1 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:col-span-7">
          {[0, 1, 2, 3].map((i) => <SkeletonBlock key={i} h={124} className="rounded-2xl" />)}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        {[0, 1, 2, 3, 4, 5].map((i) => <SkeletonBlock key={i} h={110} className="rounded-2xl" />)}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {[0, 1, 2].map((i) => <SkeletonBlock key={i} h={140} className="rounded-2xl" />)}
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => <SkeletonBlock key={i} h={96} className="rounded-2xl" />)}
      </div>
    </div>
  )
}

export default function Overview() {
  const { data: d, isDemo, isLoading, dataUpdatedAt } = useOverview()
  const health = useHealth()
  const [period, setPeriod] = useState<Period>('Today')
  const now = useNowTick()

  if (isLoading) {
    return (
      <div className="space-y-6">
        <PageHeader title="Overview" sub="Calls, spend and agent activity at a glance" />
        <OverviewSkeleton />
      </div>
    )
  }

  const b = period === 'Today' ? d.today : period === 'Last 7 Days' ? d.week : d.month
  const answerRate = d.kpis.total ? (d.kpis.answered / d.kpis.total) * 100 : 0
  const missRate = d.kpis.total ? (d.kpis.missed / d.kpis.total) * 100 : 0
  const apptRate = d.kpis.answered ? (d.kpis.appointments / d.kpis.answered) * 100 : 0
  const degraded = d.runtime === 'degraded'
  const secondsAgo = dataUpdatedAt ? Math.max(0, Math.round((now - dataUpdatedAt) / 1000)) : null

  return (
    <div className="space-y-6">
      <PageHeader title="Overview" sub="Calls, spend and agent activity at a glance"
        actions={!isDemo && secondsAgo != null ? (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-dim" role="status">
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-mint-400 animate-pulse-soft" />
            Live · updated {secondsAgo}s ago
          </span>
        ) : undefined} />

      {isDemo && (
        <div className="flex items-start gap-2 rounded-xl bg-amber-400/10 px-3 py-2 text-[12px] font-medium text-amber-600 ring-1 ring-amber-400/25 dark:text-amber-300" role="status">
          <Activity size={14} className="mt-0.5 shrink-0" /> Preview mode — showing representative data (backend at <code className="font-mono">:8080</code> not reachable from this machine).
        </div>
      )}

      {/* Bento hero row */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <Card hover glow="indigo" className="relative col-span-1 overflow-hidden p-6 lg:col-span-5">
          <div className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-indigo-500/20 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-12 -left-6 h-40 w-40 rounded-full bg-mint-400/20 blur-3xl" />
          <div className="relative min-w-0">
            <div className="flex items-center justify-between">
              <span className="eyebrow">This Month — Estimated Billing</span>
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-indigo-500/15 text-indigo-500 dark:text-indigo-300"><IndianRupee size={18} /></span>
            </div>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <span className="metric text-[clamp(32px,4.5vw,52px)] leading-none tabular-nums">{inr(d.month.cost, 2)}</span>
              <span className="mb-2 inline-flex items-center gap-1 rounded-full bg-mint-400/15 px-2 py-0.5 text-[12px] font-bold text-mint-600 dark:text-mint-300"><TrendingUp size={13} /> {num(d.month.calls)} calls</span>
            </div>
            <div className="mt-1 text-[13px] text-dim">Sarvam STT + TTS usage · 30-day estimate</div>
            <div className="mt-5" role="img" aria-label="Call volume over time"><Sparkline data={d.spark.calls} color="var(--accent-2)" height={70} /></div>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {([
                ['Today', inr(d.today.cost), null],
                ['Last 7 Days', inr(d.week.cost), null],
                ['This Month', num(d.month.calls), 'calls'],
                ['Inbound', num(d.totals.inbound), 'calls'],
              ] as const).map(([k, v, unit]) => (
                <div key={k} className="rounded-xl surface-2 px-3 py-2.5">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-dim">{k}</div>
                  <div className="metric mt-1 text-[18px]">
                    {v}{unit && <span className="ml-1 text-[12px] font-medium text-dim">{unit}</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </Card>

        <div className="col-span-1 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:col-span-7">
          <StatCard label="Total Calls" value={num(d.totals.calls)} sub="all time" icon={<Phone size={18} />} iconTone="mint" spark={d.spark.calls} sparkColor="var(--accent)" />
          <StatCard label="Inbound" value={num(d.totals.inbound)} sub="all inbound" icon={<PhoneIncoming size={18} />} iconTone="indigo" spark={d.spark.inbound} sparkColor="var(--accent-2)" />
          <StatCard label="Minutes Used" value={num(d.totals.minutes_used)} sub="lifetime minutes" icon={<Timer size={18} />} iconTone="amber" spark={d.spark.minutes} sparkColor="#f59e0b" />
          <StatCard label="Calls Today" value={num(d.today.calls)} sub={d.today.calls === 0 ? 'none yet today' : undefined} trend={d.today.trend} icon={<CalendarClock size={18} />} iconTone="mint" spark={d.spark.today} sparkColor="var(--accent)" />
        </div>
      </div>

      {/* Secondary metric strip */}
      <div>
        <SectionHead title="Workspace" sub="Agents, campaigns and today's usage" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <StatCard label="Agents" value={num(d.totals.agents)} icon={<Bot size={16} />} iconTone="indigo" />
          <StatCard label="Campaigns" value={num(d.totals.campaigns)} icon={<Megaphone size={16} />} iconTone="mint" />
          <StatCard label="Leads" value={num(d.totals.leads)} icon={<Users size={16} />} iconTone="amber" />
          <StatCard label="Calls (7d)" value={num(d.week.calls)} icon={<Clock size={16} />} iconTone="indigo" />
          <StatCard label="STT Today" value={dur(d.today.stt_seconds)} sub={`${num(d.today.stt_seconds)}s · ${inr(d.today.stt_cost)} cost`} icon={<Mic size={16} />} iconTone="indigo" />
          <StatCard label="TTS Today" value={compact(d.today.tts_chars)} sub={`${num(d.today.tts_chars)} chars · ${inr(d.today.tts_cost)} cost`} icon={<Volume2 size={16} />} iconTone="mint" />
        </div>
      </div>

      {/* Runtime health + cost cards */}
      <div>
        <SectionHead title="Usage & Billing" sub="Sarvam STT + TTS estimates" />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card className={cx('flex items-center gap-4 p-5 lg:col-span-1', degraded && 'ring-1 ring-amber-400/30')}>
            <div className={cx('grid h-12 w-12 place-items-center rounded-2xl', degraded ? 'bg-amber-400/15 text-amber-500' : 'bg-mint-400/15 text-mint-500')}>
              <Activity size={22} className={degraded ? 'animate-pulse-soft' : undefined} />
            </div>
            <div className="min-w-0">
              <div className="eyebrow">Runtime Health</div>
              <div className="metric mt-1 text-[22px] capitalize">{d.runtime}</div>
              <div className="flex items-center gap-1.5 text-[12px] text-dim">
                {health.liveCount > 0 && <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-mint-400 animate-pulse-soft" />}
                {health.liveCount > 0 ? `${health.liveCount} active call${health.liveCount > 1 ? 's' : ''}` : 'No active calls'} · FreeSWITCH → AI
              </div>
              {health.services.length > 0 && (
                <div className="mt-1.5 flex items-center gap-1.5">
                  {health.services.map((s) => (
                    <span key={s.name} title={`${s.name}: ${s.ok ? 'up' : 'down'}`} aria-label={`${s.name} ${s.ok ? 'up' : 'down'}`}
                      className={cx('h-1.5 w-1.5 rounded-full', s.ok ? 'bg-mint-400' : 'bg-rose-500')} />
                  ))}
                </div>
              )}
            </div>
          </Card>
          <Card hover className="p-5">
            <div className="eyebrow">Last 7 Days — Est.</div>
            <div className="metric mt-2 text-[28px]">{inr(d.week.cost)}</div>
            <div className="mt-1 text-[12px] text-dim">{num(d.week.calls)} calls</div>
            <div className="mt-3" role="img" aria-label="Calls over the last 7 days"><Sparkline data={d.calls_7d} color="var(--accent-2)" height={42} /></div>
          </Card>
          <Card hover className="p-5">
            <div className="eyebrow">Last 30 Days — Est.</div>
            <div className="metric mt-2 text-[28px]">{inr(d.month.cost, 2)}</div>
            <div className="mt-1 text-[12px] text-dim">{num(d.month.calls)} calls</div>
            <div className="mt-3" role="img" aria-label="Calls over the last 30 days"><Sparkline data={d.spark.calls} color="var(--accent)" height={42} /></div>
          </Card>
        </div>
      </div>

      {/* Activity KPIs */}
      <div>
        <SectionHead title="Activity KPIs" sub="Calling activity for the selected period"
          right={
            <Segmented options={periods.map((p) => ({ value: p, label: p }))} value={period} onChange={setPeriod} />
          } />
        {d.kpis.total === 0 && (
          <div className="mb-3 flex items-center gap-2 rounded-xl surface-2 px-3 py-2 text-[12px] text-dim" role="status">
            <PhoneCall size={13} /> No calls in this period yet — stats update within 30 seconds of your first call.
          </div>
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Total Calls" value={num(b.calls)} sub={period === 'Today' ? 'today' : period.toLowerCase()} icon={<PhoneCall size={16} />} iconTone="indigo" />
          <StatCard label="Answered Calls" value={num(d.kpis.answered)} sub={`${pct(answerRate)} answer rate · all time`} icon={<PhoneIncoming size={16} />} iconTone="mint" />
          <StatCard label="Missed Calls" value={num(d.kpis.missed)} sub={`${pct(missRate)} of total · all time`} icon={<PhoneMissed size={16} />} iconTone="rose" />
          <StatCard label="Minutes Used" value={num(d.kpis.minutes_used)} sub="all time" icon={<Timer size={16} />} iconTone="indigo" />
          <StatCard label="Inbound Calls" value={num(d.kpis.inbound)} sub="all time" icon={<PhoneIncoming size={16} />} iconTone="indigo" />
          <StatCard label="Outbound Calls" value={num(d.kpis.outbound)} sub="all time" icon={<PhoneOutgoing size={16} />} iconTone="indigo" />
          <StatCard label="Appointments Booked" value={num(d.kpis.appointments)} sub={`${pct(apptRate)} of answered · all time`} icon={<CalendarCheck size={16} />} iconTone="mint" />
          <StatCard label="Follow-Ups Required" value={num(d.kpis.followups)} sub="all time" icon={<BellRing size={16} />} iconTone="amber" />
        </div>
      </div>
    </div>
  )
}
