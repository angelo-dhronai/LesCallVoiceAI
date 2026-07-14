import { Fragment, useEffect, useRef, useState } from 'react'
import {
  Check, ChevronRight, Copy, PhoneIncoming, PhoneOutgoing, RefreshCw, SearchX,
} from 'lucide-react'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { Badge, Button, Card, EmptyState, Spinner, cx } from '../components/ui'
import {
  PageHeader, Table, THead, TH, TR, TD, Pagination,
  StatusBadge, SearchInput, Select, Segmented,
} from '../components/kit'
import { inr, dur, num } from '../lib/format'

type Call = {
  call_id?: string
  start_time?: string
  end_time?: string
  duration_seconds?: number
  est_stt_cost?: number
  est_tts_cost?: number
  est_total_cost?: number
  call_type?: 'inbound' | 'outbound'
  contact_name?: string
  contact_phone?: string
  status?: 'answered' | 'no_answer' | 'busy' | 'declined' | 'failed'
}

type HistoryResp = {
  calls?: Call[]
  total?: number
  page?: number
  limit?: number
  pages?: number
}

const fmtWhen = (s?: string): string => {
  if (!s) return '—'
  const d = new Date(s)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

const fmtTime = (s?: string): string => {
  if (!s) return '—'
  const d = new Date(s)
  return isNaN(d.getTime()) ? '—' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

const dayLabel = (s?: string): string => {
  if (!s) return 'Unknown date'
  const d = new Date(s)
  if (isNaN(d.getTime())) return 'Unknown date'
  const today = new Date()
  const yesterday = new Date()
  yesterday.setDate(today.getDate() - 1)
  if (d.toDateString() === today.toDateString()) return 'Today'
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

export default function Activity() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [days, setDays] = useState(7)
  const [callType, setCallType] = useState('all')
  const [status, setStatus] = useState('all')
  const [open, setOpen] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const refresh = useRefresh()

  // Debounce search so we fire one request per pause, not one per keystroke.
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search), 300)
    return () => window.clearTimeout(t)
  }, [search])

  const reset = (fn: () => void) => { fn(); setPage(1) }

  const path =
    `/calls/history?page=${page}&limit=50` +
    `&search=${encodeURIComponent(debouncedSearch)}` +
    `&days=${days}&call_type=${callType}&status=${status}`

  const { data, isLoading, isFetching, error } = useGet<HistoryResp>(
    ['history', page, debouncedSearch, days, callType, status],
    path,
  )

  // Keep the previous rows on screen while a new page/filter loads so a
  // refetch reads as a soft dim instead of a table teardown.
  const lastDataRef = useRef<HistoryResp | undefined>(undefined)
  if (data) lastDataRef.current = data
  const view = data ?? lastDataRef.current

  const calls = view?.calls ?? []
  const showSkeleton = isLoading && !view
  const refreshing = isFetching && !showSkeleton

  const filtersActive = !!search || callType !== 'all' || status !== 'all' || days !== 7
  const clearFilters = () => {
    setSearch(''); setDebouncedSearch(''); setCallType('all'); setStatus('all'); setDays(7); setPage(1)
  }

  const copyId = (id: string) => {
    navigator.clipboard.writeText(id).then(() => {
      setCopied(id)
      window.setTimeout(() => setCopied((cur) => (cur === id ? null : cur)), 1500)
    }).catch(() => { /* clipboard unavailable */ })
  }

  // Group consecutive rows by calendar day for scannable date dividers.
  const groups: { key: string; label: string; items: Call[] }[] = []
  for (const c of calls) {
    const d = c?.start_time ? new Date(c.start_time) : null
    const key = d && !isNaN(d.getTime()) ? d.toDateString() : 'unknown'
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.items.push(c)
    else groups.push({ key, label: dayLabel(c?.start_time), items: [c] })
  }

  const head = (
    <THead>
      <TH className="w-8"><span className="sr-only">Expand row</span></TH>
      <TH>Date &amp; Time</TH>
      <TH>Contact</TH>
      <TH>Type</TH>
      <TH>Duration</TH>
      <TH>Status</TH>
      <TH>Call ID</TH>
      <TH className="text-right">
        <span
          title="Estimated STT + TTS spend for this call"
          className="cursor-help underline decoration-dotted decoration-[var(--border-strong)] underline-offset-2"
        >
          Est. Cost
        </span>
      </TH>
    </THead>
  )

  return (
    <div className="space-y-6">
      <PageHeader
        title="Call Activity"
        sub="Every call your agents placed and answered"
        actions={
          <Button variant="ghost" size="sm" onClick={() => refresh(['history'])}>
            {refreshing ? <Spinner className="h-3.5 w-3.5" /> : <RefreshCw size={14} />}
            Refresh
          </Button>
        }
      />

      <Card className="p-5">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <SearchInput
            value={search}
            onChange={(v) => reset(() => setSearch(v))}
            placeholder="Search call id or contact"
            className="min-w-[220px] flex-1"
          />
          <div className="max-w-full overflow-x-auto [scrollbar-width:none]">
            <Segmented
              options={[
                { value: '1', label: 'Today' },
                { value: '7', label: '7 days' },
                { value: '30', label: '30 days' },
                { value: '90', label: '90 days' },
              ]}
              value={String(days)}
              onChange={(v) => reset(() => setDays(Number(v)))}
            />
          </div>
          <label className="flex items-center gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-dim">Type</span>
            <Select aria-label="Filter by call type" value={callType} onChange={(e) => reset(() => setCallType(e.target.value))}>
              <option value="all">All types</option>
              <option value="inbound">Inbound</option>
              <option value="outbound">Outbound</option>
            </Select>
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-dim">Status</span>
            <Select aria-label="Filter by status" value={status} onChange={(e) => reset(() => setStatus(e.target.value))}>
              <option value="all">All status</option>
              <option value="answered">Answered</option>
              <option value="no_answer">No answer</option>
              <option value="busy">Busy</option>
              <option value="declined">Declined</option>
              <option value="failed">Failed</option>
            </Select>
          </label>
        </div>

        {!showSkeleton && !error && (
          <div aria-live="polite" className="mb-2 flex items-center gap-2 text-[12px] text-dim">
            <span>{num(view?.total ?? 0)} calls · {days === 1 ? 'today' : `last ${days} days`}</span>
            {refreshing && <Spinner className="h-3 w-3" />}
          </div>
        )}

        {showSkeleton ? (
          <Table>
            {head}
            <tbody>
              {Array.from({ length: 8 }).map((_, i) => (
                <TR key={i}>
                  <TD className="w-8"><div className="skeleton h-4 w-4" /></TD>
                  <TD><div className="skeleton h-4 w-24" /></TD>
                  <TD>
                    <div className="skeleton h-4 w-32" />
                    <div className="skeleton mt-1 h-3 w-20" />
                  </TD>
                  <TD><div className="skeleton h-4 w-16 rounded-full" /></TD>
                  <TD><div className="skeleton h-4 w-10" /></TD>
                  <TD><div className="skeleton h-4 w-16 rounded-full" /></TD>
                  <TD><div className="skeleton h-4 w-24" /></TD>
                  <TD><div className="skeleton ml-auto h-4 w-12" /></TD>
                </TR>
              ))}
            </tbody>
          </Table>
        ) : error ? (
          <div role="alert" className="rounded-xl bg-rose-500/10 px-4 py-3 text-[13px] font-medium text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">
            Failed to load: {String((error as Error)?.message ?? error)}
          </div>
        ) : calls.length === 0 ? (
          filtersActive ? (
            <EmptyState
              icon={<SearchX size={22} />}
              title="No calls match these filters"
              hint="Try a wider date range or a different status."
              action={<Button variant="ghost" size="sm" onClick={clearFilters}>Reset filters</Button>}
            />
          ) : (
            <EmptyState
              icon={<PhoneIncoming size={22} />}
              title="No calls yet"
              hint="Calls will appear here the moment your agent answers an inbound call or a campaign starts dialing."
            />
          )
        ) : (
          <div className={cx('transition-opacity', refreshing && 'opacity-60')}>
            <Table>
              {head}
              <tbody>
                {groups.map((g, gi) => (
                  <Fragment key={`${g.key}-${gi}`}>
                    <tr className="border-b border-[var(--border)]">
                      <td colSpan={8} className="bg-[var(--surface-2)] px-3 py-1.5">
                        <span className="eyebrow sticky left-3 inline-block">{g.label}</span>
                      </td>
                    </tr>
                    {g.items.map((c, i) => {
                      const rid = c?.call_id ?? `row-${gi}-${i}`
                      const isOpen = open === rid
                      return (
                        <Fragment key={rid}>
                          <TR className="group" onClick={() => setOpen(isOpen ? null : rid)}>
                            <TD className="w-8 pr-0">
                              <button
                                type="button"
                                aria-label={isOpen ? 'Collapse call details' : 'Expand call details'}
                                aria-expanded={isOpen}
                                onClick={(e) => { e.stopPropagation(); setOpen(isOpen ? null : rid) }}
                                className="grid h-6 w-6 place-items-center rounded-md text-dim transition-colors hover:bg-[var(--surface-2)] hover:text-soft"
                              >
                                <ChevronRight size={14} className={cx('transition-transform', isOpen && 'rotate-90')} />
                              </button>
                            </TD>
                            <TD className="whitespace-nowrap text-[var(--text)]" title={fmtWhen(c?.start_time)}>
                              {fmtTime(c?.start_time)}
                            </TD>
                            <TD>
                              <div className="font-semibold text-[var(--text)]">{c?.contact_name ?? 'Unknown'}</div>
                              {c?.contact_phone && <div className="text-[12px] text-dim">{c.contact_phone}</div>}
                            </TD>
                            <TD>
                              {c?.call_type
                                ? (
                                  <Badge tone={c.call_type === 'inbound' ? 'indigo' : 'mint'}>
                                    {c.call_type === 'inbound' ? <PhoneIncoming size={11} /> : <PhoneOutgoing size={11} />}
                                    {c.call_type}
                                  </Badge>
                                )
                                : <span className="text-dim">—</span>}
                            </TD>
                            <TD className="tabular-nums">{dur(c?.duration_seconds)}</TD>
                            <TD><StatusBadge value={c?.status} /></TD>
                            <TD>
                              <div className="flex items-center gap-1.5">
                                <span title={c?.call_id} className="block max-w-[140px] truncate font-mono text-[11px] text-dim">
                                  {c?.call_id ?? '—'}
                                </span>
                                {c?.call_id && (
                                  <button
                                    type="button"
                                    aria-label="Copy call ID"
                                    onClick={(e) => { e.stopPropagation(); copyId(c.call_id!) }}
                                    className="-my-2.5 grid h-9 w-9 shrink-0 place-items-center rounded-md text-dim transition-opacity hover:text-soft focus-visible:opacity-100 md:opacity-0 md:group-hover:opacity-100"
                                  >
                                    {copied === c.call_id ? <Check size={12} className="text-mint-500" /> : <Copy size={12} />}
                                  </button>
                                )}
                              </div>
                            </TD>
                            <TD
                              className="text-right tabular-nums text-[var(--text)]"
                              title={`STT ${c?.est_stt_cost != null ? inr(c.est_stt_cost, 2) : '—'} · TTS ${c?.est_tts_cost != null ? inr(c.est_tts_cost, 2) : '—'}`}
                            >
                              {c?.est_total_cost != null ? inr(c.est_total_cost, 2) : <span className="text-dim">—</span>}
                            </TD>
                          </TR>
                          {isOpen && (
                            <tr className="border-b border-[var(--border)]">
                              <TD colSpan={8} className="bg-[var(--surface-2)]">
                                <div className="sticky left-0 grid max-w-[calc(100vw-5rem)] grid-cols-2 gap-4 py-1 sm:max-w-none sm:grid-cols-4">
                                  <div>
                                    <div className="eyebrow">Started</div>
                                    <div className="mt-0.5 text-[13px] text-[var(--text)]">{fmtWhen(c?.start_time)}</div>
                                  </div>
                                  <div>
                                    <div className="eyebrow">Ended</div>
                                    <div className="mt-0.5 text-[13px] text-[var(--text)]">{fmtWhen(c?.end_time)}</div>
                                  </div>
                                  <div>
                                    <div className="eyebrow">STT cost</div>
                                    <div className="mt-0.5 text-[13px] tabular-nums text-[var(--text)]">
                                      {c?.est_stt_cost != null ? inr(c.est_stt_cost, 2) : '—'}
                                    </div>
                                  </div>
                                  <div>
                                    <div className="eyebrow">TTS cost</div>
                                    <div className="mt-0.5 text-[13px] tabular-nums text-[var(--text)]">
                                      {c?.est_tts_cost != null ? inr(c.est_tts_cost, 2) : '—'}
                                    </div>
                                  </div>
                                  <div className="col-span-2 sm:col-span-4">
                                    <div className="eyebrow">Call ID</div>
                                    <div className="mt-0.5 break-all font-mono text-[12px] text-soft">{c?.call_id ?? '—'}</div>
                                  </div>
                                </div>
                              </TD>
                            </tr>
                          )}
                        </Fragment>
                      )
                    })}
                  </Fragment>
                ))}
              </tbody>
            </Table>
          </div>
        )}

        <Pagination
          page={view?.page ?? page}
          pages={view?.pages ?? 1}
          total={view?.total}
          onPage={setPage}
        />
      </Card>
    </div>
  )
}
