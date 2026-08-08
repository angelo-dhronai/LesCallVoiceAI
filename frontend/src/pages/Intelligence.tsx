/**
 * Intelligence — the unified post-call dashboard. Every call is auto-analyzed
 * by the pipeline; this page is the source of truth: KPI band, a natural-
 * language semantic filter ("calls with complaints about product quality"),
 * structured filters, the master table, row selection → Generate Report.
 * Structured filters + page live in the URL; the NL filter is in-memory.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  BrainCircuit, FileText, RefreshCw, Sparkles, X,
} from 'lucide-react'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { searchIntel, createReport, type IntelCallRow } from '../api/intel'
import { Button, Card, EmptyState, Spinner } from '../components/ui'
import {
  AsyncState, ContactLabel, PageHeader, Pagination, SearchInput, Segmented, Select,
  StatusBadge, Table, TD, TH, THead, TR, Field, Input,
} from '../components/kit'
import { ScoreCell } from '../components/insight'
import { Checkbox } from '@/components/ui/checkbox'
import { Modal } from '../components/Modal'
import { StatCard } from '../components/StatCard'
import { useToast } from '../components/Toast'
import { useDebounced, useUrlNumber, useUrlParam } from '../lib/hooks'
import { fmtDateTime } from '../lib/format'

const LIMIT = 50
const DAYS_VALUES = ['7', '30', '90', '0'] as const
type Days = (typeof DAYS_VALUES)[number]

/* Slicing the raw string used to print the stored UTC clock verbatim; every
   call time on this page read hours off. fmtDateTime renders the instant in the
   viewer's zone — see lib/format.ts. */
const fmtWhen = (ts?: string | null) => fmtDateTime(ts)

export default function Intelligence() {
  const nav = useNavigate()
  const toast = useToast()
  const refresh = useRefresh()

  // Structured filters — URL-persisted (refresh/share keeps them).
  const [page, setPage] = useUrlNumber('page', 1)
  const [search, setSearch] = useUrlParam('search')
  const [rawDays, setDays] = useUrlParam('days', '30')
  const days: Days = (DAYS_VALUES as readonly string[]).includes(rawDays) ? (rawDays as Days) : '30'
  const [sentiment, setSentiment] = useUrlParam('sentiment')
  const [decision, setDecision] = useUrlParam('decision')
  const [minLead, setMinLead] = useUrlParam('minLead', '0')
  const [, setParams] = useSearchParams()

  const debSearch = useDebounced(search, 300)
  const hasFilters = Boolean(search) || sentiment !== '' || decision !== ''
    || minLead !== '0' || days !== '30'

  // Filter changes reset the page. Done as an effect (not a second setter call
  // in the same handler) because react-router setSearchParams calls clobber
  // each other within one tick; skip the first run so deep links keep ?page=.
  const firstRun = useRef(true)
  useEffect(() => {
    if (firstRun.current) { firstRun.current = false; return }
    if (page !== 1) setPage(1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debSearch, days, sentiment, decision, minLead])

  function resetFilters() {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      for (const k of ['search', 'days', 'sentiment', 'decision', 'minLead', 'page']) next.delete(k)
      return next
    }, { replace: true })
  }

  // Natural-language semantic filter (server-side hybrid retrieval) — in-memory.
  const [nlQuery, setNlQuery] = useState('')
  const [nlActive, setNlActive] = useState('')
  const [nlRows, setNlRows] = useState<IntelCallRow[] | null>(null)
  const [nlBusy, setNlBusy] = useState(false)
  const [nlDegraded, setNlDegraded] = useState(false)
  const nlSeq = useRef(0)

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [reportOpen, setReportOpen] = useState(false)
  const [reportTitle, setReportTitle] = useState('')
  const [reportBusy, setReportBusy] = useState(false)

  const qs = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), limit: String(LIMIT) })
    if (debSearch) p.set('search', debSearch)
    if (days !== '0') p.set('days', days)
    if (sentiment) p.set('sentiment', sentiment)
    if (decision) p.set('decision', decision)
    if (minLead !== '0') p.set('min_lead', minLead)
    return p.toString()
  }, [page, debSearch, days, sentiment, decision, minLead])

  const list = useGet<{ items: IntelCallRow[]; total: number }>(
    ['intel-calls', qs], `/intel/calls?${qs}`, { refetchInterval: 30_000 })
  const analytics = useGet<any>(['intel-analytics', days],
    `/intel/analytics?days=${days === '0' ? 365 : days}`)
  const status = useGet<any>(['intel-status'], '/intel/status',
    { refetchInterval: 30_000 })

  async function runNlSearch() {
    const q = nlQuery.trim()
    if (!q) return
    const seq = ++nlSeq.current
    setNlBusy(true)
    try {
      const res = await searchIntel(q, 50)
      if (seq !== nlSeq.current) return
      setNlRows(res.items)
      setNlActive(q)
      setNlDegraded(res.degraded)
      setSelected(new Set())
    } catch (e: any) {
      toast(`Search failed: ${e?.message ?? e}`, 'error')
    } finally {
      if (seq === nlSeq.current) setNlBusy(false)
    }
  }

  function clearNl() {
    setNlRows(null); setNlActive(''); setNlQuery(''); setSelected(new Set())
  }

  const rows: IntelCallRow[] = nlRows ?? list.data?.items ?? []
  const total = nlRows ? nlRows.length : list.data?.total ?? 0
  const pages = nlRows ? 1 : Math.max(1, Math.ceil(total / LIMIT))

  function toggle(id: number) {
    setSelected((s) => {
      const n = new Set(s)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }
  const allChecked = rows.length > 0 && rows.every((r) => selected.has(r.call_id))

  async function generateReport() {
    setReportBusy(true)
    try {
      const res = await createReport({
        title: reportTitle || undefined,
        query_prompt: nlActive || search || undefined,
        call_ids: [...selected],
      })
      toast(`Report #${res.id} queued — it will appear in Reports shortly`, 'success')
      setReportOpen(false)
      setReportTitle('')
      setSelected(new Set())
      refresh(['reports'])
    } catch (e: any) {
      toast(`Report failed: ${e?.message ?? e}`, 'error')
    } finally {
      setReportBusy(false)
    }
  }

  const sum = analytics.data?.summary
  const cov = status.data?.coverage

  return (
    <div className="space-y-6">
      <PageHeader
        title="Intelligence"
        sub="Every call is analyzed automatically — no manual step. Filter in plain language, inspect, act."
        actions={
          <>
            {selected.size > 0 && (
              <Button onClick={() => setReportOpen(true)}>
                <FileText size={14} /> Generate report ({selected.size})
              </Button>
            )}
            <Button variant="ghost" onClick={() => refresh(['intel-calls'], ['intel-analytics'], ['intel-status'])}>
              <RefreshCw size={14} /> Refresh
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Calls analyzed" value={sum?.analyzed ?? '—'}
          sub={cov ? `${cov.analyzed}/${cov.calls} lifetime coverage` : undefined}
          icon={<BrainCircuit size={18} />} iconTone="mint" />
        <StatCard label="Hot leads" value={sum?.hot_leads ?? '—'}
          sub="lead score ≥ 70" iconTone="rose"
          icon={<Sparkles size={18} />} />
        <StatCard label="Avg lead score" value={sum?.avg_lead ?? '—'}
          radial={sum?.avg_lead != null ? { value: sum.avg_lead, max: 100 } : undefined} />
        <StatCard label="Negative calls" value={sum?.negative ?? '—'}
          sub={sum ? `${sum.positive} positive · ${sum.neutral} neutral` : undefined}
          iconTone="amber" icon={<X size={18} />} />
      </div>

      <Card className="p-5">
        <div className="flex flex-col gap-2 sm:flex-row">
          <SearchInput
            value={nlQuery}
            onChange={setNlQuery}
            onEnter={runNlSearch}
            icon={<Sparkles size={15} />}
            placeholder='Describe the calls you want — e.g. "3BHK flat enquiries" or "complaints about product quality"'
            ariaLabel="Semantic call search"
            className="flex-1"
          />
          <Button onClick={runNlSearch} disabled={nlBusy || !nlQuery.trim()}>
            {nlBusy ? <Spinner /> : <Sparkles size={14} />} Smart filter
          </Button>
        </div>

        {nlRows ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-xs text-dim">
              <span className="font-semibold tabular-nums text-soft">{nlRows.length}</span>
              {' '}call(s) matching “{nlActive}”
              {nlDegraded && ' · semantic engine unavailable — keyword results only'}
            </span>
            <Button variant="ghost" size="sm" onClick={clearNl}>
              <X size={13} /> Clear AI filter
            </Button>
          </div>
        ) : (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <SearchInput value={search} className="w-56"
              onChange={setSearch}
              placeholder="Phone / summary…" ariaLabel="Filter calls" />
            <Segmented
              options={[{ value: '7', label: '7d' }, { value: '30', label: '30d' },
                { value: '90', label: '90d' }, { value: '0', label: 'All' }]}
              value={days} onChange={(v) => setDays(v)} />
            <Select value={sentiment} onChange={(e) => setSentiment(e.target.value)}
              aria-label="Sentiment filter">
              <option value="">Any sentiment</option>
              <option value="positive">Positive</option>
              <option value="neutral">Neutral</option>
              <option value="negative">Negative</option>
            </Select>
            <Select value={decision} onChange={(e) => setDecision(e.target.value)}
              aria-label="Follow-up filter">
              <option value="">Any follow-up</option>
              <option value="yes">Follow-up: yes</option>
              <option value="no">Follow-up: no</option>
              <option value="unsure">Needs review</option>
            </Select>
            <Select value={minLead} onChange={(e) => setMinLead(e.target.value)}
              aria-label="Lead score filter">
              <option value="0">Any lead score</option>
              <option value="40">Lead ≥ 40</option>
              <option value="70">Lead ≥ 70 (hot)</option>
            </Select>
            {hasFilters && (
              <Button variant="ghost" size="sm" onClick={resetFilters}>
                <X size={13} /> Reset filters
              </Button>
            )}
            {!list.isLoading && (
              <span className="ml-auto text-xs tabular-nums text-dim">
                {total.toLocaleString('en-IN')} result{total === 1 ? '' : 's'}
              </span>
            )}
          </div>
        )}

        <div className="mt-4">
          <AsyncState loading={list.isLoading && !nlRows} error={nlRows ? undefined : list.error}>
            {rows.length === 0 ? (
              <EmptyState icon={<BrainCircuit size={22} />}
                title={nlRows ? 'No calls match the AI filter'
                  : hasFilters ? 'No analyzed calls match these filters'
                    : 'No calls analyzed yet'}
                hint={nlRows
                  ? 'Try rephrasing — or clear the AI filter to browse all analyzed calls.'
                  : hasFilters
                    ? 'Widen the date range or clear a filter — new calls appear here automatically after they end.'
                    : 'Completed calls enter the intelligence pipeline automatically.'}
                action={nlRows
                  ? <Button variant="ghost" size="sm" onClick={clearNl}><X size={13} /> Clear AI filter</Button>
                  : hasFilters
                    ? <Button variant="ghost" size="sm" onClick={resetFilters}><X size={13} /> Reset filters</Button>
                    : undefined}
              />
            ) : (
              <>
                <Table>
                  <THead>
                    <TH className="w-8">
                      <Checkbox aria-label="Select all" checked={allChecked}
                        onCheckedChange={() => setSelected(allChecked ? new Set()
                          : new Set(rows.map((r) => r.call_id)))} />
                    </TH>
                    <TH>Customer</TH>
                    <TH>Date</TH>
                    <TH>Agent</TH>
                    <TH>Intent</TH>
                    <TH>Sentiment</TH>
                    <TH>Lead</TH>
                    <TH>Opp</TH>
                    <TH>Risk</TH>
                    <TH>Follow-up</TH>
                    <TH>Summary</TH>
                  </THead>
                  <tbody>
                    {rows.map((r) => (
                      <TR key={r.call_id}
                        onClick={() => r.call_key && nav(`/intelligence/${r.call_key}`)}>
                        <TD className="w-8">
                          <span onClick={(e) => e.stopPropagation()}
                            onKeyDown={(e) => e.stopPropagation()}>
                            <Checkbox aria-label={`Select call ${r.call_key}`}
                              checked={selected.has(r.call_id)}
                              onCheckedChange={() => toggle(r.call_id)} />
                          </span>
                        </TD>
                        <TD>
                          <ContactLabel name={r.contact_name} phone={r.phone} />
                          <div className="text-2xs text-dim">
                            {r.direction} · {Math.round(r.duration_s ?? 0)}s
                          </div>
                        </TD>
                        <TD className="whitespace-nowrap tabular-nums">{fmtWhen(r.started_at)}</TD>
                        <TD className="max-w-[110px] truncate" title={r.agent_slug ?? ''}>{r.agent_slug ?? '—'}</TD>
                        <TD className="max-w-[160px] truncate" title={r.customer_intent ?? ''}>
                          {r.customer_intent ?? '—'}
                        </TD>
                        <TD><StatusBadge value={r.sentiment} /></TD>
                        <TD><ScoreCell score={r.lead_score} /></TD>
                        <TD><ScoreCell score={r.opportunity_score} /></TD>
                        <TD><ScoreCell score={r.risk_score} invert /></TD>
                        <TD>
                          {r.followup_decision === 'yes'
                            ? <StatusBadge value={r.followup_status ?? 'scheduled'} />
                            : r.followup_decision === 'unsure'
                              ? <StatusBadge value="review" />
                              : <span className="text-dim">no</span>}
                        </TD>
                        <TD className="max-w-[320px]">
                          <span className="line-clamp-2 text-xs text-dim">
                            {r.snippet ?? r.summary ?? '—'}
                          </span>
                        </TD>
                      </TR>
                    ))}
                  </tbody>
                </Table>
                {!nlRows && (
                  <Pagination page={page} pages={pages} total={total} onPage={setPage} />
                )}
              </>
            )}
          </AsyncState>
        </div>
      </Card>

      <Modal open={reportOpen} onClose={() => setReportOpen(false)}
        title={`Generate report from ${selected.size} call(s)`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setReportOpen(false)}>Cancel</Button>
            <Button onClick={generateReport} disabled={reportBusy}>
              {reportBusy ? <Spinner /> : <FileText size={14} />} Generate
            </Button>
          </>
        }>
        <div className="space-y-3">
          <Field label="Report title">
            <Input value={reportTitle} onChange={(e) => setReportTitle(e.target.value)}
              placeholder={nlActive ? `Report: ${nlActive}` : 'Call intelligence report'} />
          </Field>
          <p className="text-xs text-dim">
            The AI synthesizes the selected calls&apos; stored intelligence into a
            structured report (executive summary, findings, recommended actions).
            It runs in the background — you&apos;ll get a notification and it appears
            under Reports.
          </p>
        </div>
      </Modal>
    </div>
  )
}
