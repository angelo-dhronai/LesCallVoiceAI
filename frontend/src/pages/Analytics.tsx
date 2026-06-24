import { useGet, useRefresh } from '../api/hooks/useApi'
import { Card, Button, SectionHead, cx } from '../components/ui'
import { PageHeader, BarList, AsyncState } from '../components/kit'
import { StatCard } from '../components/StatCard'
import { num, pct, dur } from '../lib/format'
import { Phone, TrendingUp, CalendarCheck, RefreshCw, Languages } from 'lucide-react'

type HourEntry = { hour: number; count: number }
type Overview = {
  today?: { call_count?: number }
  hourly_calls?: HourEntry[]
  kpis?: { total_calls?: number }
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

export default function Analytics() {
  const refresh = useRefresh()

  const overview = useGet<Overview>(['overview'], '/overview')
  const outbound = useGet<OutboundStats>(['outbound-stats'], '/outbound/stats')

  const ov = overview.data
  const ob = outbound.data

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

  const statusKeys: { key: keyof Stats; label: string }[] = [
    { key: 'answered', label: 'answered' },
    { key: 'no_answer', label: 'no answer' },
    { key: 'busy', label: 'busy' },
    { key: 'declined', label: 'declined' },
    { key: 'failed', label: 'failed' },
    { key: 'not_called', label: 'not called' },
  ]

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        sub="Operational call analytics"
        actions={
          <Button
            variant="ghost"
            onClick={() => refresh(['overview'], ['outbound-stats'])}
          >
            <RefreshCw size={14} /> Refresh
          </Button>
        }
      />

      <AsyncState loading={overview.isLoading} error={overview.error}>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Calls Today"
            value={num(n(ov?.today?.call_count))}
            icon={<Phone size={18} />}
            iconTone="mint"
          />
          <StatCard
            label="Total Calls"
            value={num(n(ov?.kpis?.total_calls))}
            icon={<Phone size={18} />}
            iconTone="indigo"
          />
          <StatCard
            label="Conversion Rate"
            value={pct(conv, 1)}
            icon={<TrendingUp size={18} />}
            iconTone="mint"
          />
          <StatCard
            label="Visit Booking Rate"
            value={pct(bookRate, 1)}
            icon={<CalendarCheck size={18} />}
            iconTone="amber"
          />
        </div>

        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <Card className="p-5">
            <SectionHead title="Calls by Hour" />
            <BarList
              items={hourly.map((h) => ({
                label: String(n(h?.hour)).padStart(2, '0') + ':00',
                value: n(h?.count),
              }))}
              color="var(--accent)"
              empty="No call volume yet"
            />
          </Card>

          <Card className="p-5">
            <SectionHead title="Outbound Call Outcomes" />
            <BarList
              items={dispKeys.map((k) => ({
                label: k.replace(/_/g, ' '),
                value: n(dispositions[k]),
              }))}
              color="var(--accent-2)"
              empty="No outcomes yet"
            />
          </Card>

          <Card className="p-5 lg:col-span-2">
            <SectionHead title="Call Status Breakdown" />
            <BarList
              items={statusKeys.map((s) => ({
                label: s.label,
                value: n(stats[s.key]),
              }))}
              color="#f59e0b"
              empty="No call status data yet"
            />
          </Card>
        </div>
      </AsyncState>
    </div>
  )
}
