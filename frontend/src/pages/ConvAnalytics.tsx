import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { Card, Button, SectionHead, EmptyState, SkeletonBlock, cx } from '../components/ui'
import { PageHeader, BarList, Table, THead, TH, TR, TD, AsyncState } from '../components/kit'
import { StatCard } from '../components/StatCard'
import { SemiDonut, Sparkline } from '../components/charts'
import { num } from '../lib/format'
import { BrainCircuit, Flame, Frown, RefreshCw, Sparkles, Check, Copy } from 'lucide-react'

type Counted = { text: string; count: number }
type Trend = {
  date: string
  positive: number
  neutral: number
  negative: number
  hot: number
  warm: number
  cold: number
}
type ConvData = {
  analyzed_call_count: number
  sentiment: { positive: number; neutral: number; negative: number }
  lead_intelligence: { hot: number; warm: number; cold: number }
  common_objections: Counted[]
  topic_analysis: Counted[]
  product_interest: Counted[]
  top_customer_questions: Counted[]
  ai_recommendations: string[]
  trends: Trend[]
}

const n = (v: unknown): number => (typeof v === 'number' && !isNaN(v) ? v : 0)
const toItems = (arr: Counted[] | undefined | null) =>
  (Array.isArray(arr) ? arr : []).map((x) => ({ label: x?.text ?? '', value: n(x?.count) }))

type TrendKey = 'positive' | 'neutral' | 'negative' | 'hot' | 'warm' | 'cold'
const TREND_COLS: { key: TrendKey; label: string; color: string }[] = [
  { key: 'positive', label: 'Positive', color: '#13f4a3' },
  { key: 'neutral', label: 'Neutral', color: '#7a87a3' },
  { key: 'negative', label: 'Negative', color: '#fb7185' },
  { key: 'hot', label: 'Hot', color: '#fb7185' },
  { key: 'warm', label: 'Warm', color: '#f59e0b' },
  { key: 'cold', label: 'Cold', color: '#817fff' },
]

function Legend({ items, total }: { items: { label: string; value: number; color: string }[]; total?: number }) {
  return (
    <div className="mt-3 flex flex-wrap justify-center gap-x-5 gap-y-2">
      {items.map((it, i) => (
        <div key={i} className="flex items-center gap-2 text-[12px]">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: it.color }} />
          <span className="text-soft">{it.label}</span>
          <span className="font-semibold tabular-nums text-dim">{num(it.value)}</span>
          {total != null && total > 0 && (
            <span className="tabular-nums text-dim">({Math.round((it.value / total) * 100)}%)</span>
          )}
        </div>
      ))}
    </div>
  )
}

/** SemiDonut with a center KPI (or an explanation when the breakdown is empty). */
function DonutBlock({
  segments, total, totalLabel, emptyText,
}: {
  segments: { label: string; value: number; color: string }[]
  total: number
  totalLabel: string
  emptyText: string
}) {
  return (
    <div className="mt-2">
      <div className="relative">
        <SemiDonut segments={total > 0 ? segments : []} size={200} />
        {total > 0 ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-1 text-center">
            <div className="metric text-[26px] leading-none">{num(total)}</div>
            <div className="eyebrow mt-1">{totalLabel}</div>
          </div>
        ) : (
          <div className="pointer-events-none absolute inset-x-0 bottom-2 text-center text-[12px] text-dim">
            {emptyText}
          </div>
        )}
      </div>
      <Legend items={segments} total={total} />
    </div>
  )
}

/** BarList variant for sentence-length labels: wraps to two lines instead of truncating. */
function SentenceBarList({
  items, color, empty,
}: { items: { label: string; value: number }[]; color: string; empty: string }) {
  if (!items.length) return <div className="py-6 text-center text-[13px] text-dim">{empty}</div>
  const max = Math.max(...items.map((i) => i.value), 1)
  return (
    <div className="space-y-2.5">
      {items.map((it, i) => (
        <div key={i}>
          <div className="mb-1 flex items-start justify-between gap-2 text-[12px]">
            <span title={it.label} className="line-clamp-2 leading-snug text-soft">{it.label}</span>
            <span className="shrink-0 font-semibold tabular-nums text-dim">{it.value.toLocaleString('en-IN')}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]">
            <div className="h-full rounded-full transition-all" style={{ width: `${(it.value / max) * 100}%`, background: color }} />
          </div>
        </div>
      ))}
    </div>
  )
}

function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        {[0, 1, 2].map((i) => <SkeletonBlock key={i} h={96} className="rounded-2xl" />)}
      </div>
      <SkeletonBlock h={140} className="rounded-2xl" />
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1, 2, 3].map((i) => <SkeletonBlock key={i} h={220} className="rounded-2xl" />)}
      </div>
    </div>
  )
}

export default function ConvAnalytics() {
  const { data, isLoading, isFetching, dataUpdatedAt, error } = useGet<ConvData>(['conv-analytics'], '/analytics/conversation')
  const doRefresh = useRefresh()
  const refresh = () => doRefresh(['conv-analytics'])
  const navigate = useNavigate()
  const [copiedRec, setCopiedRec] = useState<number | null>(null)

  const copyRec = (i: number, text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {})
    setCopiedRec(i)
    window.setTimeout(() => setCopiedRec((c) => (c === i ? null : c)), 1500)
  }

  const count = n(data?.analyzed_call_count)
  const sentiment = data?.sentiment ?? { positive: 0, neutral: 0, negative: 0 }
  const leads = data?.lead_intelligence ?? { hot: 0, warm: 0, cold: 0 }
  const recommendations = Array.isArray(data?.ai_recommendations) ? (data?.ai_recommendations ?? []) : []
  const trends = Array.isArray(data?.trends) ? (data?.trends ?? []) : []

  const leadsTotal = n(leads.hot) + n(leads.warm) + n(leads.cold)
  const sentTotal = n(sentiment.positive) + n(sentiment.neutral) + n(sentiment.negative)

  const leadSegments = [
    { label: 'Hot', value: n(leads.hot), color: '#fb7185' },
    { label: 'Warm', value: n(leads.warm), color: '#f59e0b' },
    { label: 'Cold', value: n(leads.cold), color: '#817fff' },
  ]
  const sentimentSegments = [
    { label: 'Positive', value: n(sentiment.positive), color: '#13f4a3' },
    { label: 'Neutral', value: n(sentiment.neutral), color: '#7a87a3' },
    { label: 'Negative', value: n(sentiment.negative), color: '#fb7185' },
  ]

  return (
    <div className="space-y-6">
      <PageHeader
        title="Conversation Analytics"
        sub="Aggregated insights from analyzed calls"
        actions={
          <>
            {dataUpdatedAt > 0 && (
              <span className="text-[12px] tabular-nums text-dim">
                Updated {new Date(dataUpdatedAt).toLocaleTimeString()}
              </span>
            )}
            <Button variant="ghost" onClick={refresh} disabled={isFetching}>
              <RefreshCw size={15} className={cx(isFetching && 'animate-spin')} /> Refresh
            </Button>
          </>
        }
      />

      {isLoading ? (
        <PageSkeleton />
      ) : (
        <AsyncState error={error} empty={false}>
          {!data || count === 0 ? (
            <Card className="p-5">
              <EmptyState
                icon={<BrainCircuit size={22} />}
                title="No calls analyzed yet"
                hint="Open a completed call in AI Call Analysis and run an analysis — sentiment, lead temperature, objections and topics will aggregate here."
                action={
                  <Button variant="soft" onClick={() => navigate('/ai-analysis')}>
                    Go to AI Call Analysis
                  </Button>
                }
              />
            </Card>
          ) : (
            <>
              {/* Top stats */}
              <div className="grid gap-4 sm:grid-cols-3">
                <StatCard label="Analyzed Calls" value={num(count)} icon={<BrainCircuit size={18} />} iconTone="indigo" />
                <StatCard
                  label="Hot Leads"
                  value={num(n(leads.hot))}
                  sub={leadsTotal ? `${Math.round((n(leads.hot) / leadsTotal) * 100)}% of classified leads` : undefined}
                  icon={<Flame size={18} />}
                  iconTone="amber"
                />
                <StatCard
                  label="Negative Sentiment"
                  value={num(n(sentiment.negative))}
                  sub={count ? `${Math.round((n(sentiment.negative) / count) * 100)}% of analyzed calls` : undefined}
                  icon={<Frown size={18} />}
                  iconTone="rose"
                />
              </div>

              {/* AI recommendations */}
              <Card className="mt-6 p-5">
                <SectionHead title="AI Recommendations" sub="Suggested actions based on call analysis" />
                {recommendations.length === 0 ? (
                  <div className="py-6 text-center text-[13px] text-dim">No recommendations yet</div>
                ) : (
                  <div className="mt-3 space-y-2.5">
                    {recommendations.map((rec, i) => (
                      <div key={i} className="group flex items-start gap-3 rounded-xl bg-[var(--surface-2)] px-4 py-3 transition-colors hover:bg-[var(--border)]">
                        <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-mint-400/15 text-mint-600 dark:text-mint-300">
                          <Sparkles size={13} />
                        </span>
                        <span className="min-w-0 flex-1 break-words text-[13px] text-soft">{String(rec)}</span>
                        <span className="ml-auto flex shrink-0 items-center gap-1">
                          <button
                            type="button"
                            title="Copy suggestion"
                            aria-label="Copy suggestion"
                            onClick={() => copyRec(i, String(rec))}
                            className="-my-1.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg text-dim opacity-0 transition hover:bg-[var(--surface-2)] hover:text-soft focus-visible:opacity-100 group-hover:opacity-100 max-lg:opacity-100"
                          >
                            {copiedRec === i ? <Check size={15} className="text-mint-500" /> : <Copy size={15} />}
                          </button>
                          <span title="Suggested action" className="mt-1 shrink-0 text-mint-500/60 opacity-50">
                            <Check size={14} />
                          </span>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              {/* Grid of insights */}
              <div className="mt-6 grid gap-4 lg:grid-cols-2">
                <Card className="p-5">
                  <SectionHead title="Lead Intelligence" />
                  <DonutBlock
                    segments={leadSegments}
                    total={leadsTotal}
                    totalLabel="leads"
                    emptyText="No leads classified yet"
                  />
                </Card>

                <Card className="p-5">
                  <SectionHead title="Sentiment" />
                  <DonutBlock
                    segments={sentimentSegments}
                    total={sentTotal}
                    totalLabel="calls"
                    emptyText="No sentiment scored yet"
                  />
                </Card>

                <Card className="p-5">
                  <SectionHead title="Top Customer Questions" />
                  <div className="mt-3">
                    <SentenceBarList items={toItems(data?.top_customer_questions)} color="var(--accent-2)" empty="No questions yet" />
                  </div>
                </Card>

                <Card className="p-5">
                  <SectionHead title="Common Objections" />
                  <div className="mt-3">
                    <SentenceBarList items={toItems(data?.common_objections)} color="#f59e0b" empty="No objections yet" />
                  </div>
                </Card>

                <Card className="p-5">
                  <SectionHead title="Topic Analysis" />
                  <div className="mt-3">
                    <BarList items={toItems(data?.topic_analysis)} color="var(--accent)" empty="No topics yet" />
                  </div>
                </Card>

                <Card className="p-5">
                  <SectionHead title="Product Interest" />
                  <div className="mt-3">
                    <BarList items={toItems(data?.product_interest)} color="var(--accent-2)" empty="No product interest yet" />
                  </div>
                </Card>
              </div>

              {/* Trends */}
              <Card className={cx('mt-6 p-5')}>
                <SectionHead title="Trends" sub="Sentiment and lead breakdown over time" />
                <div className="mt-3">
                  {trends.length > 1 && (
                    <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
                      {TREND_COLS.map((c) => (
                        <div key={c.key} className="rounded-xl bg-[var(--surface-2)] p-3">
                          <div className="flex items-center gap-1.5">
                            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: c.color }} />
                            <span className="eyebrow">{c.label}</span>
                          </div>
                          <div className="mt-2">
                            <Sparkline data={trends.map((t) => n(t?.[c.key]))} color={c.color} height={36} />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  <Table>
                    <THead>
                      <TH>Date</TH>
                      {TREND_COLS.map((c) => (
                        <TH key={c.key} className="text-right">
                          <span className="inline-flex items-center gap-1.5">
                            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: c.color }} />
                            {c.label}
                          </span>
                        </TH>
                      ))}
                    </THead>
                    <tbody>
                      {trends.length === 0 ? (
                        <TR>
                          <TD colSpan={7} className="py-6 text-center text-dim">
                            No trend data yet
                          </TD>
                        </TR>
                      ) : (
                        trends.map((t, i) => (
                          <TR key={i}>
                            <TD className="font-medium text-[var(--text)]">{t?.date ?? '—'}</TD>
                            {TREND_COLS.map((c) => (
                              <TD key={c.key} className="text-right tabular-nums">{num(n(t?.[c.key]))}</TD>
                            ))}
                          </TR>
                        ))
                      )}
                    </tbody>
                  </Table>
                </div>
              </Card>
            </>
          )}
        </AsyncState>
      )}
    </div>
  )
}
