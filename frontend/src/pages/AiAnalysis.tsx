import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, BrainCircuit, CheckCircle2, ChevronRight, SearchX, Sparkles, XCircle,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { post } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, Spinner, EmptyState, SkeletonBlock, cx } from '../components/ui'
import {
  PageHeader, Table, THead, TH, TR, TD, Pagination, StatusBadge, SearchInput, AsyncState,
} from '../components/kit'
import { Modal } from '../components/Modal'

type AnalysisObj = {
  summary?: string
  customer_intent?: string
  key_points?: string[]
  customer_questions?: string[]
  suggested_next_action?: string
  sentiment?: 'positive' | 'neutral' | 'negative'
  lead_quality?: 'hot' | 'warm' | 'cold'
  objections?: string[]
  topics?: string[]
  products_mentioned?: string[]
  follow_up_needed?: boolean
  follow_up_reason?: string
  follow_up_priority?: 'high' | 'medium' | 'low'
  recommended_follow_up_date?: string
}

type Rec = {
  folder: string
  call_id?: string
  caller?: string
  folder_dt?: string
  analyzed?: boolean
}

type EligibleResp = {
  recordings?: Rec[]
  total?: number
  page?: number
  limit?: number
  pages?: number
}

type RunEntry = { ok?: boolean; error?: string; analysis?: AnalysisObj; cached?: boolean }

type RunResp = {
  ok?: boolean
  results?: Record<string, RunEntry>
}

type AnalysisResp = { analyzed?: boolean; analysis?: AnalysisObj | null }

type ModalMode = 'loading' | 'analysis' | 'pending' | 'running' | 'batch'

function fmtDate(s?: string): string {
  if (!s) return '—'
  const d = new Date(s)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function List({ title, items }: { title: string; items?: string[] }) {
  const arr = (items ?? []).filter(Boolean)
  if (!arr.length) return null
  return (
    <div>
      <div className="mb-1 text-[12px] font-semibold uppercase tracking-[0.08em] text-dim">{title}</div>
      <ul className="list-disc space-y-1 pl-5 text-[13px] text-soft">
        {arr.map((it, i) => <li key={i}>{it}</li>)}
      </ul>
    </div>
  )
}

function Chips({ title, items }: { title: string; items?: string[] }) {
  const arr = (items ?? []).filter(Boolean)
  if (!arr.length) return null
  return (
    <div>
      <div className="mb-1.5 text-[12px] font-semibold uppercase tracking-[0.08em] text-dim">{title}</div>
      <div className="flex flex-wrap gap-1.5">
        {arr.map((it, i) => <Badge key={i} tone="neutral">{it}</Badge>)}
      </div>
    </div>
  )
}

function AnalysisView({ a }: { a: AnalysisObj }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex items-center gap-1.5">
          <span className="eyebrow">Sentiment</span>
          <StatusBadge value={a?.sentiment} />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="eyebrow">Lead quality</span>
          <StatusBadge value={a?.lead_quality} />
        </div>
      </div>

      {a?.summary && (
        <div>
          <div className="mb-1 text-[12px] font-semibold uppercase tracking-[0.08em] text-dim">Summary</div>
          <p className="text-[13px] text-soft">{a.summary}</p>
        </div>
      )}

      {a?.suggested_next_action && (
        <div className="rounded-xl bg-indigo-500/10 px-4 py-3 ring-1 ring-indigo-500/25">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-semibold text-indigo-600 dark:text-indigo-300">
            <Sparkles size={15} /> Suggested next action
          </div>
          <p className="text-[13px] text-soft">{a.suggested_next_action}</p>
        </div>
      )}

      {a?.customer_intent && (
        <div>
          <div className="mb-1 text-[12px] font-semibold uppercase tracking-[0.08em] text-dim">Customer Intent</div>
          <p className="text-[13px] text-soft">{a.customer_intent}</p>
        </div>
      )}

      <List title="Key Points" items={a?.key_points} />
      <List title="Objections" items={a?.objections} />
      <Chips title="Topics" items={a?.topics} />
      <Chips title="Products Mentioned" items={a?.products_mentioned} />
      <List title="Customer Questions" items={a?.customer_questions} />

      {a?.follow_up_needed && (
        <div className="rounded-xl bg-amber-400/10 px-4 py-3 ring-1 ring-amber-400/25">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-semibold text-amber-600 dark:text-amber-300">
            <AlertTriangle size={15} /> Follow-up needed
          </div>
          {a?.follow_up_reason && <p className="text-[13px] text-soft">{a.follow_up_reason}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-dim">
            {a?.follow_up_priority && <StatusBadge value={a.follow_up_priority} />}
            {a?.recommended_follow_up_date && <span>by {a.recommended_follow_up_date}</span>}
          </div>
        </div>
      )}
    </div>
  )
}

export default function AiAnalysis() {
  const toast = useToast()
  const qc = useQueryClient()

  const [page, setPage] = useState(1)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [analyzing, setAnalyzing] = useState(false)
  const [busyFolder, setBusyFolder] = useState<string | null>(null)
  const [modalAnalysis, setModalAnalysis] = useState<AnalysisObj | null>(null)
  const [modalRec, setModalRec] = useState<Rec | null>(null)
  const [modalMode, setModalMode] = useState<ModalMode>('analysis')
  const [modalOpen, setModalOpen] = useState(false)
  const [batchResults, setBatchResults] = useState<Record<string, RunEntry>>({})
  const [batchFolders, setBatchFolders] = useState<string[]>([])
  const [cameFromBatch, setCameFromBatch] = useState(false)
  const modalFolderRef = useRef<string | null>(null)

  // Debounce the search box so the table doesn't refetch on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput)
      setPage(1)
    }, 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const path = `/analysis/eligible?page=${page}&limit=50&search=${encodeURIComponent(search)}`
  const q = useGet<EligibleResp>(['analysis-eligible', page, search], path)

  // Keep the last good payload on screen while a new page/search loads,
  // so pagination and typing don't strobe the table into skeletons.
  const lastDataRef = useRef<EligibleResp | undefined>(undefined)
  if (q.data) lastDataRef.current = q.data
  const data = q.data ?? lastDataRef.current
  const showingStale = !q.data && lastDataRef.current !== undefined

  const recordings = Array.isArray(data?.recordings) ? data!.recordings! : []
  const pages = data?.pages ?? 1
  const total = data?.total

  const visibleFolders = recordings.map((r) => r?.folder).filter(Boolean) as string[]
  const visibleSet = new Set(visibleFolders)
  const allSelected = visibleFolders.length > 0 && visibleFolders.every((f) => selected.has(f))
  const someVisibleSelected = visibleFolders.some((f) => selected.has(f))
  const hasOffPage = [...selected].some((f) => !visibleSet.has(f))

  function toggle(folder: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(folder)) next.delete(folder)
      else next.add(folder)
      return next
    })
  }

  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev)
      if (allSelected) visibleFolders.forEach((f) => next.delete(f))
      else visibleFolders.forEach((f) => next.add(f))
      return next
    })
  }

  function onSearch(v: string) {
    setSearchInput(v)
  }

  async function analyzeSelected() {
    if (selected.size === 0 || analyzing || busyFolder) return
    const folders = [...selected]
    setAnalyzing(true)
    try {
      const res = await post<RunResp>('/analysis/run', { folders })
      const results = res?.results ?? {}
      const okCount = folders.filter((f) => results[f]?.ok).length
      const failCount = folders.length - okCount
      toast(`Analyzed ${okCount} of ${folders.length}`, failCount > 0 ? 'error' : 'success')
      qc.invalidateQueries({ queryKey: ['analysis-eligible'] })
      setBatchResults(results)
      setBatchFolders(folders)
      if (folders.length === 1) {
        const entry = results[folders[0]]
        if (entry?.ok && entry?.analysis) {
          modalFolderRef.current = folders[0]
          setModalRec(recordings.find((r) => r?.folder === folders[0]) ?? { folder: folders[0] })
          setModalAnalysis(entry.analysis)
          setCameFromBatch(false)
          setModalMode('analysis')
          setModalOpen(true)
        }
      } else {
        modalFolderRef.current = null
        setModalRec(null)
        setCameFromBatch(false)
        setModalMode('batch')
        setModalOpen(true)
      }
    } catch (e) {
      toast(`Analysis failed: ${String((e as Error)?.message ?? e)}`, 'error')
    } finally {
      setAnalyzing(false)
    }
  }

  async function openRow(r: Rec) {
    const folder = r?.folder
    if (!folder) return
    modalFolderRef.current = folder
    setModalRec(r)
    setCameFromBatch(false)
    setModalOpen(true)
    if (busyFolder === folder) {
      // This call is already being analyzed — show its progress.
      setModalMode('running')
      return
    }
    setModalMode('loading')
    try {
      const existing = await api<AnalysisResp>(`/calls/recordings/${encodeURIComponent(folder)}/analysis`)
      if (modalFolderRef.current !== folder) return
      if (existing?.analysis) {
        setModalAnalysis(existing.analysis)
        setModalMode('analysis')
        return
      }
      setModalAnalysis(null)
      setModalMode('pending')
    } catch (e) {
      if (modalFolderRef.current === folder) setModalOpen(false)
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  async function runFolder(r: Rec | null) {
    const folder = r?.folder
    if (!folder || busyFolder || analyzing) return
    setBusyFolder(folder)
    if (modalFolderRef.current === folder) setModalMode('running')
    try {
      const res = await post<RunResp>('/analysis/run', { folders: [folder] })
      const entry = res?.results?.[folder]
      qc.invalidateQueries({ queryKey: ['analysis-eligible'] })
      if (entry?.ok && entry?.analysis) {
        if (modalFolderRef.current === folder) {
          setModalAnalysis(entry.analysis)
          setModalMode('analysis')
        }
        toast('Analysis complete', 'success')
      } else {
        const err = entry?.error
        if (modalFolderRef.current === folder) setModalMode('pending')
        toast(`Analysis failed${err ? `: ${err}` : ''}`, 'error')
      }
    } catch (e) {
      if (modalFolderRef.current === folder) setModalMode('pending')
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    } finally {
      setBusyFolder(null)
    }
  }

  function viewFromBatch(folder: string) {
    const entry = batchResults[folder]
    if (!entry?.analysis) return
    modalFolderRef.current = folder
    setModalRec(recordings.find((r) => r?.folder === folder) ?? { folder })
    setModalAnalysis(entry.analysis)
    setCameFromBatch(true)
    setModalMode('analysis')
  }

  const modalTitle = modalMode === 'batch'
    ? `Batch Analysis — ${batchFolders.length} calls`
    : `Call Analysis — ${modalRec?.caller || 'Unknown caller'}`

  const modalFooter = modalMode === 'pending'
    ? (
      <Button onClick={() => runFolder(modalRec)}>
        <Sparkles size={15} /> Run analysis
      </Button>
    )
    : modalMode === 'analysis' && cameFromBatch
      ? <Button variant="ghost" onClick={() => setModalMode('batch')}>Back to results</Button>
      : undefined

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Call Analysis"
        sub="Run and review per-call AI analysis"
        actions={
          <Button onClick={analyzeSelected} disabled={selected.size === 0 || analyzing || busyFolder !== null}>
            {analyzing ? <Spinner /> : <Sparkles size={15} />}
            {analyzing ? 'Analyzing…' : `Analyze Selected (${selected.size})`}
          </Button>
        }
      />

      <Card className="p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-soft">
            <BrainCircuit size={16} className="text-mint-500" /> Eligible recordings
          </div>
          <SearchInput value={searchInput} onChange={onSearch} placeholder="Search contact / number" className="w-64 max-w-full" />
        </div>

        {selected.size > 0 && (
          <div className="mb-3 flex items-center justify-between gap-3 rounded-xl bg-mint-400/10 px-3 py-2 text-[12px] ring-1 ring-mint-400/25">
            <span className="font-medium text-soft">
              {selected.size} selected{hasOffPage ? ' (includes other pages)' : ''}
            </span>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
          </div>
        )}

        <AsyncState
          loading={q.isLoading && !data}
          error={q.error}
        >
          {recordings.length === 0 ? (
            search ? (
              <EmptyState
                icon={<SearchX size={22} />}
                title={`No matches for "${search}"`}
                hint="Try a different contact name or number."
              />
            ) : (
              <EmptyState
                icon={<BrainCircuit size={22} />}
                title="No call recordings yet"
                hint="Finished calls land here automatically. Select one or more and hit Analyze Selected to get summaries, sentiment, lead quality and follow-up suggestions."
              />
            )
          ) : (
            <div className={cx('transition-opacity', showingStale && 'pointer-events-none opacity-60')}>
              <Table>
                <THead>
                  <TH className="w-8">
                    <label className="-m-2.5 grid cursor-pointer place-items-center p-2.5">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        ref={(el) => { if (el) el.indeterminate = !allSelected && someVisibleSelected }}
                        onChange={toggleAll}
                        aria-label="Select all recordings on this page"
                        className="h-4 w-4 cursor-pointer accent-mint-500"
                      />
                    </label>
                  </TH>
                  <TH>Contact</TH>
                  <TH>Date</TH>
                  <TH>Call ID</TH>
                  <TH>Status</TH>
                  <TH><span className="sr-only">Actions</span></TH>
                </THead>
                <tbody>
                  {recordings.map((r) => {
                    const folder = r?.folder ?? ''
                    const isBusy = busyFolder === folder || (analyzing && selected.has(folder))
                    return (
                      <TR
                        key={folder}
                        onClick={() => openRow(r)}
                        className={selected.has(folder) ? 'bg-mint-400/[0.07]' : undefined}
                      >
                        <TD>
                          <label
                            className="-m-2 grid cursor-pointer place-items-center p-2"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <input
                              type="checkbox"
                              checked={selected.has(folder)}
                              onChange={() => toggle(folder)}
                              aria-label={`Select ${r?.caller || 'call'} ${fmtDate(r?.folder_dt)}`}
                              className="h-4 w-4 cursor-pointer accent-mint-500"
                            />
                          </label>
                        </TD>
                        <TD className="font-medium text-[var(--text)]">{r?.caller || '—'}</TD>
                        <TD className="whitespace-nowrap text-dim">{fmtDate(r?.folder_dt)}</TD>
                        <TD className="font-mono text-[12px] text-dim" title={r?.call_id}>
                          <span className="block max-w-[180px] truncate">{r?.call_id || '—'}</span>
                        </TD>
                        <TD>
                          {isBusy
                            ? (
                              <span className="inline-flex items-center gap-1.5 text-[12px] text-dim">
                                <Spinner /> Analyzing…
                              </span>
                            )
                            : r?.analyzed
                              ? <Badge tone="mint"><CheckCircle2 size={12} /> Analyzed</Badge>
                              : <Badge tone="neutral">Not analyzed</Badge>}
                        </TD>
                        <TD className="text-right">
                          <span onClick={(e) => e.stopPropagation()}>
                            <Button size="sm" variant="ghost" onClick={() => openRow(r)}>
                              View <ChevronRight size={14} />
                            </Button>
                          </span>
                        </TD>
                      </TR>
                    )
                  })}
                </tbody>
              </Table>

              <Pagination page={page} pages={pages} total={total} onPage={setPage} />
            </div>
          )}
        </AsyncState>
      </Card>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title={modalTitle} wide footer={modalFooter}>
        {modalMode === 'batch' ? (
          <div className="divide-y divide-[var(--border)]">
            {batchFolders.map((f) => {
              const entry = batchResults[f]
              const rec = recordings.find((r) => r?.folder === f)
              const ok = !!(entry?.ok && entry?.analysis)
              return (
                <div key={f} className="flex flex-wrap items-center gap-2 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium text-[var(--text)]">
                      {rec?.caller || rec?.call_id || f}
                    </div>
                    <div className="text-[12px] text-dim">{fmtDate(rec?.folder_dt)}</div>
                  </div>
                  {entry?.cached && <span className="text-[11px] text-dim">cached</span>}
                  {ok
                    ? <Badge tone="mint"><CheckCircle2 size={12} /> Done</Badge>
                    : <Badge tone="rose"><XCircle size={12} /> Failed</Badge>}
                  {ok && (
                    <Button size="sm" variant="ghost" onClick={() => viewFromBatch(f)}>View</Button>
                  )}
                  {!ok && entry?.error && (
                    <div className="w-full text-[12px] text-dim">{entry.error}</div>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <div>
            {modalRec && (
              <div className="mb-3 text-[12px] text-dim">
                {fmtDate(modalRec.folder_dt)}
                {modalRec.call_id && <> · <span className="break-all font-mono">{modalRec.call_id}</span></>}
              </div>
            )}
            {modalMode === 'loading' && (
              <div className="space-y-3">
                <SkeletonBlock h={16} />
                <SkeletonBlock h={56} />
              </div>
            )}
            {modalMode === 'running' && (
              <div className="space-y-3">
                <SkeletonBlock h={16} />
                <SkeletonBlock h={56} />
                <SkeletonBlock h={90} />
                <div className="text-[12px] text-dim">Analyzing transcript — this takes a few seconds…</div>
              </div>
            )}
            {modalMode === 'pending' && (
              <EmptyState
                icon={<Sparkles size={22} />}
                title="Not analyzed yet"
                hint="Run AI analysis to get a summary, sentiment and next-step suggestion for this call."
              />
            )}
            {modalMode === 'analysis' && (
              modalAnalysis
                ? <AnalysisView a={modalAnalysis} />
                : <div className="py-6 text-center text-[13px] text-dim">No analysis available.</div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
