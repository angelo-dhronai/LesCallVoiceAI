import { useGet, useRefresh } from '../api/hooks/useApi'
import { Card, Button, SectionHead, cx } from '../components/ui'
import { PageHeader, BarList, Table, THead, TH, TR, TD, AsyncState } from '../components/kit'
import { StatCard } from '../components/StatCard'
import { SemiDonut } from '../components/charts'
import { num } from '../lib/format'
import { BrainCircuit, Flame, Frown, RefreshCw, Sparkles, Check } from 'lucide-react'

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

function Legend({ items }: { items: { label: string; value: number; color: string }[] }) {
  return (
    <div className="mt-3 flex flex-wrap justify-center gap-x-5 gap-y-2">
      {items.map((it, i) => (
        <div key={i} className="flex items-center gap-2 text-[12px]">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: it.color }} />
          <span className="text-soft">{it.label}</span>
          <span className="font-semibold tabular-nums text-dim">{num(it.value)}</span>
        </div>
      ))}
    </div>
  )
}

export default function ConvAnalytics() {
  const { data, isLoading, error } = useGet<ConvData>(['conv-analytics'], '/analytics/conversation')
  const doRefresh = useRefresh()
  const refresh = () => doRefresh(['conv-analytics'])

  const count = n(data?.analyzed_call_count)
  const sentiment = data?.sentiment ?? { positive: 0, neutral: 0, negative: 0 }
  const leads = data?.lead_intelligence ?? { hot: 0, warm: 0, cold: 0 }
  const recommendations = Array.isArray(data?.ai_recommendations) ? (data?.ai_recommendations ?? []) : []
  const trends = Array.isArray(data?.trends) ? (data?.trends ?? []) : []

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
          <Button variant="ghost" onClick={refresh}>
            <RefreshCw size={15} /> Refresh
          </Button>
        }
      />

      <AsyncState
        loading={isLoading}
        error={error}
        empty={!isLoading && !error && (!data || count === 0)}
        emptyLabel="No calls analyzed yet — run AI analysis first"
      >
        {/* Top stats */}
        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard label="Analyzed Calls" value={num(count)} icon={<BrainCircuit size={18} />} iconTone="indigo" />
          <StatCard label="Hot Leads" value={num(n(leads.hot))} icon={<Flame size={18} />} iconTone="amber" />
          <StatCard label="Negative Sentiment" value={num(n(sentiment.negative))} icon={<Frown size={18} />} iconTone="rose" />
        </div>

        {/* AI recommendations */}
        <Card className="mt-6 p-5">
          <SectionHead title="AI Recommendations" sub="Suggested actions based on call analysis" />
          {recommendations.length === 0 ? (
            <div className="py-6 text-center text-[13px] text-dim">No recommendations yet</div>
          ) : (
            <div className="mt-3 space-y-2.5">
              {recommendations.map((rec, i) => (
                <div key={i} className="flex items-start gap-3 rounded-xl bg-[var(--surface-2)] px-4 py-3">
                  <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-mint-400/15 text-mint-600 dark:text-mint-300">
                    <Sparkles size={13} />
                  </span>
                  <span className="text-[13px] text-soft">{String(rec)}</span>
                  <Check size={14} className="ml-auto mt-1 shrink-0 text-mint-500/60" />
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Grid of insights */}
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <Card className="p-5">
            <SectionHead title="Lead Intelligence" />
            <div className="mt-2">
              <SemiDonut segments={leadSegments} size={200} />
              <Legend items={leadSegments} />
            </div>
          </Card>

          <Card className="p-5">
            <SectionHead title="Sentiment" />
            <div className="mt-2">
              <SemiDonut segments={sentimentSegments} size={200} />
              <Legend items={sentimentSegments} />
            </div>
          </Card>

          <Card className="p-5">
            <SectionHead title="Top Customer Questions" />
            <div className="mt-3">
              <BarList items={toItems(data?.top_customer_questions)} color="var(--accent-2)" empty="No questions yet" />
            </div>
          </Card>

          <Card className="p-5">
            <SectionHead title="Common Objections" />
            <div className="mt-3">
              <BarList items={toItems(data?.common_objections)} color="#f59e0b" empty="No objections yet" />
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
            <Table>
              <THead>
                <TH>Date</TH>
                <TH>Positive</TH>
                <TH>Neutral</TH>
                <TH>Negative</TH>
                <TH>Hot</TH>
                <TH>Warm</TH>
                <TH>Cold</TH>
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
                      <TD className="tabular-nums">{num(n(t?.positive))}</TD>
                      <TD className="tabular-nums">{num(n(t?.neutral))}</TD>
                      <TD className="tabular-nums">{num(n(t?.negative))}</TD>
                      <TD className="tabular-nums">{num(n(t?.hot))}</TD>
                      <TD className="tabular-nums">{num(n(t?.warm))}</TD>
                      <TD className="tabular-nums">{num(n(t?.cold))}</TD>
                    </TR>
                  ))
                )}
              </tbody>
            </Table>
          </div>
        </Card>
      </AsyncState>
    </div>
  )
}
