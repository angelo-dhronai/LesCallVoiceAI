import { useNavigate } from 'react-router-dom'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { Card, Button, Badge, SectionHead, EmptyState, SkeletonBlock, cx } from '../components/ui'
import { PageHeader, AsyncState } from '../components/kit'
import { StatCard } from '../components/StatCard'
import { num, pct, clockTime } from '../lib/format'
import { Phone, TrendingUp, CalendarCheck, RefreshCw, PhoneOff } from 'lucide-react'
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts'

type HourEntry = { hour: number; count: number }
type DayEntry = { date?: string; count?: number }
type Overview = {
  today?: { call_count?: number }
  hourly_calls?: HourEntry[]
  kpis?: { total_calls?: number }
  charts?: { calls_over_time?: DayEntry[] }
}
type Dispositions = {
  not_classified?: number
  interested?: number
  site_visit_booked?: number
  inquiry?: number
  in_doubt?: number
  to_contact_later?: number
  rejected?: number
}
type Stats = {
  total?: number
  not_called?: number
  answered?: number
  no_answer?: number
  busy?: number
  declined?: number
  failed?: number
}
type OutboundStats = { stats?: Stats; dispositions?: Dispositions }

const n = (v: number | undefined | null): number => (typeof v === 'number' && !isNaN(v) ? v : 0)
const hh = (h: number): string => String(h).padStart(2, '0')

/* Semantic dot colors mirroring kit.tsx STATUS_TONE so outcome rows speak the
   same status color language as StatusBadge everywhere else in the app. */
type Tone = 'mint' | 'indigo' | 'amber' | 'rose' | 'neutral'
const DOT_CLASS: Record<Tone, string> = {
  mint: 'bg-mint-500 dark:bg-mint-400',
  indigo: 'bg-indigo-500 dark:bg-indigo-400',
  amber: 'bg-amber-500 dark:bg-amber-400',
  rose: 'bg-rose-500 dark:bg-rose-400',
  neutral: 'bg-[var(--text-dim)]',
}

function TonedBarList({
  items, total, color, twoCol,
}: {
  items: { label: string; value: number; tone: Tone }[]
  total: number
  color: string
  twoCol?: boolean
}) {
  const max = Math.max(...items.map((i) => i.value), 1)
  return (
    <div className={cx(twoCol ? 'grid gap-x-10 gap-y-2.5 md:grid-cols-2' : 'space-y-2.5')}>
      {items.map((it) => (
        <div key={it.label}>
          <div className="mb-1 flex items-center justify-between gap-2 text-[12px]">
            <span className="flex min-w-0 items-center gap-1.5 text-soft">
              <span className={cx('h-2 w-2 shrink-0 rounded-full', DOT_CLASS[it.tone])} />
              <span className="truncate">{it.label}</span>
            </span>
            <span className="shrink-0 font-semibold tabular-nums text-dim">
              {num(it.value)}
              {total > 0 && <span className="font-normal"> · {pct((it.value / total) * 100, 0)}</span>}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]">
            <div className="h-full rounded-full transition-all" style={{ width: `${(it.value / max) * 100}%`, background: color }} />
          </div>
        </div>
      ))}
    </div>
  )
}

function HourTooltip({ active, payload, label }: { active?: boolean; payload?: { value?: number }[]; label?: number }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg bg-[var(--surface-solid)] px-2.5 py-1.5 text-[12px] shadow-glass ring-1 ring-[var(--border)]">
      <span className="font-semibold tabular-nums">{hh(n(label))}:00</span>
      <span className="text-dim"> · {num(n(payload[0]?.value))} calls</span>
    </div>
  )
}

const DISP_TONE: Record<string, Tone> = {
  interested: 'mint',
  site_visit_booked: 'mint',
  inquiry: 'indigo',
  in_doubt: 'amber',
  to_contact_later: 'amber',
  rejected: 'rose',
  not_classified: 'neutral',
}

export default function Analytics() {
  const refresh = useRefresh()
  const navigate = useNavigate()

  const overview = useGet<Overview>(['overview'], '/overview')
  const outbound = useGet<OutboundStats>(['outbound-stats'], '/outbound/stats')

  const ov = overview.data
  const ob = outbound.data

  const loading = overview.isLoading || outbound.isLoading
  const fetching = overview.isFetching || outbound.isFetching
  const updatedAt = Math.max(overview.dataUpdatedAt ?? 0, outbound.dataUpdatedAt ?? 0)

  const dispositions = ob?.dispositions ?? {}
  const stats = ob?.stats ?? {}

  const dispKeys = [
    'interested',
    'site_visit_booked',
    'inquiry',
    'in_doubt',
    'to_contact_later',
    'rejected',
    'not_classified',
  ] as const

  const total = dispKeys.reduce((acc, k) => acc + n(dispositions[k]), 0)
  const interested = n(dispositions.interested)
  const booked = n(dispositions.site_visit_booked)

  const conv = total ? ((interested + booked) / total) * 100 : 0
  const bookRate = total ? (booked / total) * 100 : 0

  const hourly = ov?.hourly_calls ?? []
  const hourData = hourly.map((h) => ({ hour: n(h?.hour), count: n(h?.count) }))
  const peak = hourData.reduce((a, b) => (b.count > a.count ? b : a), { hour: 0, count: 0 })
  const hasHourCalls = hourData.some((h) => h.count > 0)
  const callsTodaySpark = hourData.map((h) => h.count)
  const totalCallsSpark = (ov?.charts?.calls_over_time ?? []).map((d) => n(d?.count))

  const statusKeys: { key: keyof Stats; label: string }[] = [
    { key: 'answered', label: 'answered' },
    { key: 'no_answer', label: 'no answer' },
    { key: 'busy', label: 'busy' },
    { key: 'declined', label: 'declined' },
    { key: 'failed', label: 'failed' },
    { key: 'not_called', label: 'not called' },
  ]
  const STATUS_ROW_TONE: Record<string, Tone> = {
    answered: 'mint',
    no_answer: 'amber',
    busy: 'amber',
    declined: 'rose',
    failed: 'rose',
    not_called: 'neutral',
  }
  const statusTotal = n(stats.total)
  const statusSum = statusKeys.reduce((acc, s) => acc + n(stats[s.key]), 0)
  const hasStatusData = statusSum > 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        sub="Operational call analytics"
        actions={
          <>
            {updatedAt > 0 && (
              <span className="text-[11px] text-dim">Updated {clockTime(new Date(updatedAt))}</span>
            )}
            <Button
              variant="ghost"
              disabled={fetching}
              onClick={() => refresh(['overview'], ['outbound-stats'])}
            >
              <RefreshCw size={14} className={cx(fetching && 'animate-spin')} /> Refresh
            </Button>
          </>
        }
      />

      {loading ? (
        <div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <SkeletonBlock key={i} h={110} className="rounded-2xl" />
            ))}
          </div>
          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <SkeletonBlock h={280} className="rounded-2xl" />
            <SkeletonBlock h={280} className="rounded-2xl" />
          </div>
        </div>
      ) : (
        <AsyncState error={overview.error ?? outbound.error}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Calls Today"
              value={num(n(ov?.today?.call_count))}
              sub={hasHourCalls ? `peak ${hh(peak.hour)}:00` : 'no calls yet today'}
              icon={<Phone size={18} />}
              iconTone="mint"
              spark={hasHourCalls ? callsTodaySpark : undefined}
              sparkColor="var(--accent)"
            />
            <StatCard
              label="Total Calls"
              value={num(n(ov?.kpis?.total_calls))}
              sub="all time"
              icon={<Phone size={18} />}
              iconTone="indigo"
              spark={totalCallsSpark.length ? totalCallsSpark : undefined}
              sparkColor="var(--accent-2)"
            />
            <StatCard
              label="Conversion Rate"
              value={total ? pct(conv, 1) : '—'}
              sub={total ? `${num(interested + booked)} of ${num(total)} classified calls` : 'No classified outcomes yet'}
              icon={<TrendingUp size={18} />}
              iconTone="mint"
            />
            <StatCard
              label="Visit Booking Rate"
              value={total ? pct(bookRate, 1) : '—'}
              sub={total ? `${num(booked)} visits booked` : 'No classified outcomes yet'}
              icon={<CalendarCheck size={18} />}
              iconTone="amber"
            />
          </div>

          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <Card className="p-5">
              <SectionHead
                title="Calls by Hour"
                right={hasHourCalls ? <Badge tone="mint">Peak {hh(peak.hour)}:00</Badge> : undefined}
              />
              {hasHourCalls ? (
                <div className="h-[180px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={hourData} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
                      <CartesianGrid vertical={false} stroke="var(--border)" />
                      <XAxis
                        dataKey="hour"
                        ticks={[0, 3, 6, 9, 12, 15, 18, 21]}
                        tickFormatter={(h: number) => hh(h)}
                        tickLine={false}
                        axisLine={false}
                        tick={{ fontSize: 11, fill: 'var(--text-dim)' }}
                      />
                      <YAxis
                        allowDecimals={false}
                        width={32}
                        tickLine={false}
                        axisLine={false}
                        tick={{ fontSize: 11, fill: 'var(--text-dim)' }}
                      />
                      <Tooltip content={<HourTooltip />} cursor={{ fill: 'var(--surface-2)' }} />
                      <Bar dataKey="count" fill="var(--accent)" radius={[3, 3, 0, 0]} maxBarSize={14} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <EmptyState
                  icon={<Phone size={20} />}
                  title="No calls today yet"
                  hint="Hourly volume appears here as calls come in through the day."
                />
              )}
            </Card>

            <Card className="p-5">
              <SectionHead title="Outbound Call Outcomes" />
              {total > 0 ? (
                <TonedBarList
                  items={dispKeys.map((k) => ({
                    label: k.replace(/_/g, ' '),
                    value: n(dispositions[k]),
                    tone: DISP_TONE[k] ?? 'neutral',
                  }))}
                  total={total}
                  color="var(--accent-2)"
                />
              ) : (
                <EmptyState
                  icon={<PhoneOff size={20} />}
                  title="No outbound outcomes yet"
                  hint="Outcomes are classified automatically after your agent completes outbound calls — start a campaign from Outbound Calls."
                  action={
                    <Button variant="soft" onClick={() => navigate('/outbound')}>
                      Go to Outbound
                    </Button>
                  }
                />
              )}
            </Card>

            <Card className="p-5 lg:col-span-2">
              <SectionHead
                title="Call Status Breakdown"
                right={
                  statusTotal > 0 ? (
                    <span className="text-[12px] text-dim">{num(statusTotal)} contacts total</span>
                  ) : undefined
                }
              />
              {hasStatusData ? (
                <TonedBarList
                  items={statusKeys.map((s) => ({
                    label: s.label,
                    value: n(stats[s.key]),
                    tone: STATUS_ROW_TONE[s.key as string] ?? 'neutral',
                  }))}
                  total={statusTotal || statusSum}
                  color="var(--accent-2)"
                  twoCol
                />
              ) : (
                <div className="py-6 text-center text-[13px] text-dim">No call status data yet</div>
              )}
            </Card>
          </div>
        </AsyncState>
      )}
    </div>
  )
}
