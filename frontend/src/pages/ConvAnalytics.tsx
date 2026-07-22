/**
 * Conversation Analytics — the deep-dive companion to Intelligence.
 *
 * Intelligence answers "which call?"; this page answers "what is happening
 * across all of them, and is it getting better or worse?". So nothing here
 * repeats a per-call row: every panel is either a period-over-period movement,
 * a distribution, or a comparison between agents/segments.
 *
 * Reads /intel/analytics/conversation — SQL-side aggregation over
 * call_intelligence, windowed on WHEN THE CALL HAPPENED. The page it replaced
 * read the legacy calls.analysis blob with no date range at all, which is why
 * it could only ever show one all-time number per metric.
 */
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { Card, Button, SectionHead, EmptyState, SkeletonBlock, cx } from '../components/ui'
import { PageHeader, Table, THead, TH, TR, TD, AsyncState, Segmented, Select } from '../components/kit'
import {
  StackedColumns, TrendLine, ProportionBand, ActivityHeatmap, Legend,
  type Series, type ColumnDatum,
} from '../components/analyticsCharts'
import { CHART, TONE } from '../lib/chartColors'
import { num } from '../lib/format'
import {
  ArrowDownRight, ArrowUpRight, BrainCircuit, Clock, Flame, Minus,
  MessageSquareQuote, RefreshCw, Table2, TrendingUp, Users,
} from 'lucide-react'

type Counted = { label: string; count: number }
type Kpi = {
  analyzed: number; avg_lead: number | null; avg_risk: number | null
  avg_opportunity: number | null; avg_quality: number | null
  avg_satisfaction: number | null; avg_conversion: number | null
  avg_duration: number | null; total_duration: number | null
  hot: number; positive: number; neutral: number; negative: number
  low_confidence: number
  hot_rate: number | null; negative_rate: number | null; positive_rate: number | null
}
type Row = {
  day: string; calls: number; positive: number; neutral: number
  negative: number; hot: number; avg_lead: number | null; avg_duration: number | null
}
type AgentRow = {
  agent: string; calls: number; avg_lead: number | null; avg_quality: number | null
  avg_duration: number | null; hot: number; positive: number; negative: number
}
type Data = {
  days: number; agent: string | null
  current: Kpi; previous: Kpi
  series: Row[]
  lead_bands: { hot: number; warm: number; cold: number; unscored: number }
  agents: AgentRow[]
  heatmap: { dow: number; hour: number; calls: number; avg_lead: number | null }[]
  mix: { direction: string; language: string; calls: number; avg_lead: number | null }[]
  followups: { total: number; yes: number; unsure: number; completed: number; missed: number }
  funnel: {
    analyzed: number; reached: number; qualified: number
    committed: number; scheduled: number; won: number
  }
  commitments: { kind: string; n: number; closed: number }[]
  agent_options: string[]
  topics: Counted[]; objections: Counted[]; product_interest: Counted[]
  pain_points: Counted[]; buying_signals: Counted[]; competitor_mentions: Counted[]
}

const n = (v: unknown): number => (typeof v === 'number' && !isNaN(v) ? v : 0)

type LensKey = 'topics' | 'objections' | 'buying_signals' | 'product_interest'
  | 'pain_points' | 'competitor_mentions'
const LENSES: { key: LensKey; label: string; color: string; empty: string }[] = [
  { key: 'topics', label: 'Topics', color: CHART.mint, empty: 'No topics extracted yet' },
  { key: 'objections', label: 'Objections', color: CHART.rose, empty: 'No objections recorded yet' },
  { key: 'buying_signals', label: 'Buying signals', color: CHART.amber, empty: 'No buying signals captured yet' },
  { key: 'product_interest', label: 'Products', color: CHART.indigo, empty: 'No product interest captured yet' },
  { key: 'pain_points', label: 'Pain points', color: CHART.rose, empty: 'No pain points captured yet' },
  { key: 'competitor_mentions', label: 'Competitors', color: CHART.neutral, empty: 'No competitors mentioned' },
]

const SENTIMENT: Series[] = [
  { key: 'positive', label: 'Positive', color: TONE.positive },
  { key: 'neutral', label: 'Neutral', color: TONE.neutral },
  { key: 'negative', label: 'Negative', color: TONE.negative },
]

function dayLabel(iso: string) {
  const d = new Date(`${iso}T00:00:00`)
  return `${d.getDate()}/${d.getMonth() + 1}`
}
function dayFull(iso: string) {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
}
function mins(sec: number | null | undefined) {
  const s = n(sec)
  if (!s) return '—'
  return s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`
}

/** Signed change vs the previous equal-length period. `goodDown` inverts the
 *  color so "negative sentiment fell" reads as the win it is. */
function Delta({ now, prev, unit = '', goodDown = false }:
  { now: number | null; prev: number | null; unit?: string; goodDown?: boolean }) {
  if (now == null || prev == null || prev === 0) {
    return <span className="text-dim">no prior period</span>
  }
  const diff = now - prev
  const pct = Math.round((diff / Math.abs(prev)) * 100)
  if (pct === 0) {
    return <span className="inline-flex items-center gap-0.5 text-dim"><Minus size={11} /> flat</span>
  }
  const good = goodDown ? diff < 0 : diff > 0
  return (
    <span className={cx('inline-flex items-center gap-0.5 font-semibold',
      good ? 'text-mint-600 dark:text-mint-300' : 'text-rose-500 dark:text-rose-300')}>
      {diff > 0 ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
      {Math.abs(pct)}%{unit}
    </span>
  )
}

/** Metric tile: value, movement vs prior period, and the raw prior value. */
function Tile({ label, value, sub, delta, icon, tone = 'indigo' }: {
  label: string; value: string; sub?: string
  delta?: React.ReactNode; icon?: React.ReactNode
  tone?: 'mint' | 'indigo' | 'amber' | 'rose'
}) {
  const toneBg: Record<string, string> = {
    mint: 'bg-mint-400/15 text-mint-600 dark:text-mint-300',
    indigo: 'bg-indigo-500/12 text-indigo-500 dark:text-indigo-300',
    amber: 'bg-amber-400/15 text-amber-600 dark:text-amber-300',
    rose: 'bg-rose-500/12 text-rose-500 dark:text-rose-300',
  }
  return (
    <Card hover className="flex flex-col p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="eyebrow truncate">{label}</div>
          {/* proportional figures — tabular-nums only in table columns */}
          <div className="metric mt-2 text-3xl leading-none sm:text-4xl">{value}</div>
        </div>
        {icon && (
          <div className={cx('grid h-9 w-9 shrink-0 place-items-center rounded-xl', toneBg[tone])}>
            {icon}
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-2 text-xs">
        {delta}
        {sub && <span className="text-dim">{sub}</span>}
      </div>
    </Card>
  )
}

function BarRows({ items, color, empty, onPick }: {
  items: Counted[]; color: string; empty: string; onPick?: (label: string) => void
}) {
  if (!items?.length) return <div className="py-6 text-center text-sm text-dim">{empty}</div>
  const max = Math.max(...items.map((i) => i.count), 1)
  return (
    <div className="space-y-2.5">
      {items.map((it, i) => {
        const Row = onPick ? 'button' : 'div'
        return (
          <Row
            key={i}
            {...(onPick ? { type: 'button' as const, onClick: () => onPick(it.label) } : {})}
            className={cx('block w-full text-left', onPick && 'group rounded-lg')}
          >
            <div className="mb-1 flex items-start justify-between gap-2 text-xs">
              <span title={it.label}
                className={cx('line-clamp-2 leading-snug text-soft', onPick && 'group-hover:text-[var(--text)]')}>
                {it.label}
              </span>
              <span className="shrink-0 font-semibold tabular-nums text-dim">{num(it.count)}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]">
              <div className="h-full rounded-full transition-all"
                style={{ width: `${(it.count / max) * 100}%`, background: color }} />
            </div>
          </Row>
        )
      })}
    </div>
  )
}

/**
 * Conversion funnel. The stages are counted independently rather than as
 * strict subsets — a customer can commit without ever scoring 40 — so each bar
 * is drawn as a share of `reached` and the drop between bars is deliberately
 * NOT labelled as loss. Anything else would be a chart asserting causation the
 * data doesn't carry.
 */
function Funnel({ f }: { f: Data['funnel'] }) {
  const base = Math.max(1, n(f?.reached))
  const STAGES: { key: keyof Data['funnel']; label: string; hint: string; color: string }[] = [
    { key: 'reached', label: 'Conversations', hint: 'connected and spoke', color: CHART.indigo },
    { key: 'qualified', label: 'Qualified', hint: 'lead score ≥ 40', color: CHART.mint },
    { key: 'committed', label: 'Committed', hint: 'agreed to something concrete', color: CHART.amber },
    { key: 'scheduled', label: 'Follow-up booked', hint: 'AI decided yes', color: CHART.mint },
    { key: 'won', label: 'Won', hint: "outcome marked 'success'", color: CHART.mint },
  ]
  return (
    <div className="space-y-3">
      {STAGES.map((s) => {
        const v = n(f?.[s.key])
        const pct = (v / base) * 100
        return (
          <div key={s.key}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-xs">
              <span className="text-soft">
                {s.label} <span className="text-dim">· {s.hint}</span>
              </span>
              <span className="shrink-0 tabular-nums text-dim">
                <span className="font-semibold text-[var(--text)]">{num(v)}</span>
                {' '}· {Math.round(pct)}%
              </span>
            </div>
            <div className="h-3 overflow-hidden rounded-full bg-[var(--surface-2)]">
              <div className="h-full rounded-full transition-all"
                style={{ width: `${Math.min(100, pct)}%`, background: s.color }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <SkeletonBlock key={i} h={110} className="rounded-2xl" />)}
      </div>
      <SkeletonBlock h={300} className="rounded-2xl" />
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1, 2, 3].map((i) => <SkeletonBlock key={i} h={230} className="rounded-2xl" />)}
      </div>
    </div>
  )
}

export default function ConvAnalytics() {
  const nav = useNavigate()
  const doRefresh = useRefresh()
  const [days, setDays] = useState<'7' | '30' | '90'>('30')
  const [agent, setAgent] = useState('')
  const [showTable, setShowTable] = useState(false)
  const [lens, setLens] = useState<LensKey>('topics')
  const activeLens = LENSES.find((l) => l.key === lens) ?? LENSES[0]

  const qs = `days=${days}${agent ? `&agent=${encodeURIComponent(agent)}` : ''}`
  const { data, isLoading, isFetching, error } =
    useGet<Data>(['conv-analytics', days, agent], `/intel/analytics/conversation?${qs}`)

  const cur = data?.current
  const prev = data?.previous
  const series = useMemo(() => data?.series ?? [], [data])

  const columns: ColumnDatum[] = useMemo(
    () => series.map((r) => ({
      label: dayLabel(r.day),
      caption: dayFull(r.day),
      values: { positive: n(r.positive), neutral: n(r.neutral), negative: n(r.negative) },
    })),
    [series],
  )
  const leadTrend = useMemo(
    () => series.map((r) => ({ label: dayFull(r.day), value: r.avg_lead })),
    [series],
  )

  const bands = data?.lead_bands
  const analyzed = n(cur?.analyzed)

  // Drill-through: a topic/objection is only useful if you can get to the calls
  // behind it. Intelligence persists its free-text filter as ?search=, and the
  // window is carried across so a 90-day topic doesn't land on a 30-day list.
  const drill = (q: string) =>
    nav(`/intelligence?search=${encodeURIComponent(q)}&days=${days}`)

  return (
    <div className="space-y-6">
      <PageHeader
        title="Conversation Analytics"
        sub="What every conversation adds up to — movement, distribution, and which agent is actually converting."
        actions={
          <Button variant="ghost" onClick={() => doRefresh(['conv-analytics'])} disabled={isFetching}>
            <RefreshCw size={15} className={cx(isFetching && 'animate-spin')} /> Refresh
          </Button>
        }
      />

      {/* One filter row above everything it scopes — never per-card filters. */}
      <Card className="flex flex-wrap items-center gap-2 p-3">
        <Segmented
          options={[{ value: '7', label: '7 days' }, { value: '30', label: '30 days' },
            { value: '90', label: '90 days' }]}
          value={days} onChange={setDays} />
        <Select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Filter by agent">
          <option value="">All agents</option>
          {(data?.agent_options ?? []).map((a) => <option key={a} value={a}>{a}</option>)}
        </Select>
        <span className="ml-auto text-xs text-dim">
          vs the preceding {days} days
        </span>
        <Button variant="ghost" onClick={() => setShowTable((s) => !s)}
          aria-pressed={showTable}>
          <Table2 size={14} /> {showTable ? 'Hide' : 'Table'} view
        </Button>
      </Card>

      {isLoading ? (
        <PageSkeleton />
      ) : (
        <AsyncState error={error} empty={false}>
          {analyzed === 0 ? (
            <Card className="p-5">
              <EmptyState
                icon={<BrainCircuit size={22} />}
                title="No analyzed calls in this range"
                hint="Every completed call is analyzed automatically. Widen the date range, or clear the agent filter."
                action={<Button variant="soft" onClick={() => { setDays('90'); setAgent('') }}>
                  Show last 90 days
                </Button>}
              />
            </Card>
          ) : (
            /* Hold the previous render at reduced opacity on refetch — no
               skeleton flash, no layout jump. */
            <div className={cx('space-y-6 transition-opacity', isFetching && 'opacity-60')}>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Tile label="Calls analyzed" value={num(analyzed)}
                  icon={<BrainCircuit size={17} />} tone="indigo"
                  delta={<Delta now={analyzed} prev={n(prev?.analyzed)} />}
                  sub={`${num(n(prev?.analyzed))} prior`} />
                <Tile label="Hot lead rate" value={cur?.hot_rate != null ? `${cur.hot_rate}%` : '—'}
                  icon={<Flame size={17} />} tone="amber"
                  delta={<Delta now={cur?.hot_rate ?? null} prev={prev?.hot_rate ?? null} />}
                  sub={`${num(n(cur?.hot))} of ${num(analyzed)} calls`} />
                <Tile label="Avg lead score" value={cur?.avg_lead != null ? String(cur.avg_lead) : '—'}
                  icon={<TrendingUp size={17} />} tone="mint"
                  delta={<Delta now={cur?.avg_lead ?? null} prev={prev?.avg_lead ?? null} />}
                  sub="out of 100" />
                <Tile label="Negative sentiment"
                  value={cur?.negative_rate != null ? `${cur.negative_rate}%` : '—'}
                  icon={<MessageSquareQuote size={17} />} tone="rose"
                  delta={<Delta now={cur?.negative_rate ?? null} prev={prev?.negative_rate ?? null} goodDown />}
                  sub={`${num(n(cur?.negative))} calls`} />
              </div>

              {/* Conversion + what was actually agreed to. */}
              <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
                <Card className="p-5">
                  <SectionHead title="Conversion funnel"
                    sub="Each stage is a recorded fact, shown as a share of conversations" />
                  <div className="mt-4"><Funnel f={data!.funnel} /></div>
                </Card>
                <Card className="p-5">
                  <SectionHead title="What customers agreed to"
                    sub="Commitments the agent captured on the call" />
                  <div className="mt-4">
                    {(data?.commitments ?? []).length === 0 ? (
                      <div className="py-8 text-center text-sm text-dim">
                        No commitments captured in this range
                      </div>
                    ) : (
                      <BarRows
                        items={(data?.commitments ?? []).map((c) => ({
                          label: c.kind.replace(/_/g, ' '), count: c.n,
                        }))}
                        color={CHART.amber} empty="" />
                    )}
                  </div>
                </Card>
              </div>

              {/* Volume + sentiment over time — the page's lead chart. */}
              <Card className="p-5">
                <SectionHead title="Call volume by sentiment"
                  sub={`Daily, last ${days} days — dated by when the call happened`} />
                <div className="mt-4">
                  <StackedColumns data={columns} series={SENTIMENT} height={220} unit="calls" />
                </div>
              </Card>

              <div className="grid gap-4 lg:grid-cols-2">
                <Card className="p-5">
                  <SectionHead title="Average lead score" sub="Daily mean, 0–100" />
                  <div className="mt-4">
                    <TrendLine data={leadTrend} color={CHART.mint} height={140} unit="avg lead" />
                  </div>
                </Card>

                <Card className="p-5">
                  <SectionHead title="Lead temperature mix"
                    sub="Hot ≥ 70 · Warm 40–69 · Cold < 40" />
                  <div className="mt-4">
                    <ProportionBand
                      total={analyzed}
                      segments={[
                        { key: 'hot', label: 'Hot', value: n(bands?.hot), color: TONE.hot },
                        { key: 'warm', label: 'Warm', value: n(bands?.warm), color: CHART.indigo },
                        { key: 'cold', label: 'Cold', value: n(bands?.cold), color: CHART.neutral },
                      ]}
                    />
                  </div>
                  <div className="mt-5 grid grid-cols-3 gap-3 border-t border-[var(--border)] pt-4">
                    <div>
                      <div className="eyebrow">Avg talk time</div>
                      <div className="mt-1 text-lg font-semibold text-[var(--text)]">
                        {mins(cur?.avg_duration)}
                      </div>
                    </div>
                    <div>
                      <div className="eyebrow">Conversion prob.</div>
                      <div className="mt-1 text-lg font-semibold text-[var(--text)]">
                        {cur?.avg_conversion != null
                          ? `${Math.round(Number(cur.avg_conversion) * 100)}%` : '—'}
                      </div>
                    </div>
                    <div>
                      <div className="eyebrow">Follow-ups set</div>
                      <div className="mt-1 text-lg font-semibold text-[var(--text)]">
                        {num(n(data?.followups?.yes))}
                      </div>
                    </div>
                  </div>
                </Card>
              </div>

              {/* Agent comparison — the panel that says which script works. */}
              <Card className="p-5">
                <SectionHead title="Agent performance"
                  sub="Same window, side by side. Sorted by call volume."
                  right={<span className="text-xs text-dim">
                    {data?.agents?.length ?? 0} active
                  </span>} />
                <div className="mt-3 overflow-x-auto">
                  <Table>
                    <THead>
                      <TH>Agent</TH>
                      <TH className="text-right">Calls</TH>
                      <TH className="text-right">Avg lead</TH>
                      <TH className="text-right">Hot</TH>
                      <TH className="text-right">Avg talk</TH>
                      <TH className="w-[180px]">Sentiment</TH>
                    </THead>
                    <tbody>
                      {(data?.agents ?? []).map((a) => {
                        const other = Math.max(0, a.calls - a.positive - a.negative)
                        return (
                          <TR key={a.agent} onClick={() => setAgent(a.agent)}>
                            <TD className="max-w-[220px] truncate font-medium text-[var(--text)]"
                              title={a.agent}>{a.agent}</TD>
                            <TD className="text-right tabular-nums">{num(a.calls)}</TD>
                            <TD className="text-right tabular-nums">{a.avg_lead ?? '—'}</TD>
                            <TD className="text-right tabular-nums">{num(a.hot)}</TD>
                            <TD className="text-right tabular-nums">{mins(a.avg_duration)}</TD>
                            <TD>
                              <div className="flex h-2 gap-[2px] overflow-hidden rounded-full">
                                {[['positive', a.positive], ['neutral', other], ['negative', a.negative]]
                                  .map(([k, v]) => (v as number) > 0 && (
                                    <div key={k as string} title={`${k}: ${v}`}
                                      style={{ width: `${((v as number) / a.calls) * 100}%`,
                                        background: TONE[k as string] }} />
                                  ))}
                              </div>
                            </TD>
                          </TR>
                        )
                      })}
                    </tbody>
                  </Table>
                </div>
                <div className="mt-3"><Legend series={SENTIMENT} /></div>
              </Card>

              <Card className="p-5">
                <SectionHead title="When calls happen"
                  sub="Volume by weekday and hour — where to point capacity" />
                <div className="mt-4">
                  <ActivityHeatmap cells={data?.heatmap ?? []} color={CHART.indigo} />
                </div>
              </Card>

              {/* One card, six lenses. These were six separate cards; at that
                  size the page was mostly bar lists and the signal drowned. */}
              <Card className="p-5">
                <SectionHead title="Voice of the customer"
                  sub="Extracted from every conversation — pick a lens; click any row for the calls behind it"
                  right={
                    <div className="flex flex-wrap gap-1">
                      {LENSES.map((l) => (
                        <button key={l.key} type="button" onClick={() => setLens(l.key)}
                          aria-pressed={lens === l.key}
                          className={cx('rounded-lg px-2.5 py-1 text-xs transition-colors',
                            lens === l.key
                              ? 'bg-[var(--surface-2)] font-semibold text-[var(--text)]'
                              : 'text-dim hover:text-soft')}>
                          {l.label}
                        </button>
                      ))}
                    </div>
                  } />
                <div className="mt-4">
                  <BarRows items={data?.[activeLens.key] ?? []} color={activeLens.color}
                    empty={activeLens.empty} onPick={drill} />
                </div>
              </Card>

              {/* Table-view twin: every plotted value in WCAG-clean text form. */}
              {showTable && (
                <>
                  <Card className="p-5">
                    <SectionHead title="Daily figures"
                      sub="The same numbers the charts above plot" />
                    <div className="mt-3 overflow-x-auto">
                      <Table>
                        <THead>
                          <TH>Date</TH>
                          <TH className="text-right">Calls</TH>
                          <TH className="text-right">Positive</TH>
                          <TH className="text-right">Neutral</TH>
                          <TH className="text-right">Negative</TH>
                          <TH className="text-right">Hot</TH>
                          <TH className="text-right">Avg lead</TH>
                          <TH className="text-right">Avg talk</TH>
                        </THead>
                        <tbody>
                          {series.filter((r) => r.calls > 0).map((r) => (
                            <TR key={r.day}>
                              <TD className="whitespace-nowrap font-medium text-[var(--text)]">{r.day}</TD>
                              <TD className="text-right tabular-nums">{num(r.calls)}</TD>
                              <TD className="text-right tabular-nums">{num(r.positive)}</TD>
                              <TD className="text-right tabular-nums">{num(r.neutral)}</TD>
                              <TD className="text-right tabular-nums">{num(r.negative)}</TD>
                              <TD className="text-right tabular-nums">{num(r.hot)}</TD>
                              <TD className="text-right tabular-nums">{r.avg_lead ?? '—'}</TD>
                              <TD className="text-right tabular-nums">{mins(r.avg_duration)}</TD>
                            </TR>
                          ))}
                        </tbody>
                      </Table>
                    </div>
                  </Card>

                  <Card className="p-5">
                    <SectionHead title="Channel & language mix"
                      sub="Lead quality is rarely uniform across these" />
                    <div className="mt-3 overflow-x-auto">
                      <Table>
                        <THead>
                          <TH>Direction</TH>
                          <TH>Language</TH>
                          <TH className="text-right">Calls</TH>
                          <TH className="text-right">Share</TH>
                          <TH className="text-right">Avg lead</TH>
                        </THead>
                        <tbody>
                          {(data?.mix ?? []).map((m, i) => (
                            <TR key={i}>
                              <TD className="capitalize">{m.direction}</TD>
                              <TD className="uppercase">{m.language}</TD>
                              <TD className="text-right tabular-nums">{num(m.calls)}</TD>
                              <TD className="text-right tabular-nums">
                                {Math.round((m.calls / analyzed) * 100)}%
                              </TD>
                              <TD className="text-right tabular-nums">{m.avg_lead ?? '—'}</TD>
                            </TR>
                          ))}
                        </tbody>
                      </Table>
                    </div>
                  </Card>
                </>
              )}

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-dim">
                <span className="inline-flex items-center gap-1">
                  <Clock size={12} /> {mins(cur?.total_duration)} of conversation analyzed
                </span>
                <span className="inline-flex items-center gap-1">
                  <Users size={12} /> {num(n(data?.followups?.total))} follow-up decisions
                  · {num(n(data?.followups?.missed))} missed
                </span>
                {n(cur?.low_confidence) > 0 && (
                  <span>{num(n(cur?.low_confidence))} call(s) analyzed with low confidence</span>
                )}
              </div>
            </div>
          )}
        </AsyncState>
      )}
    </div>
  )
}
