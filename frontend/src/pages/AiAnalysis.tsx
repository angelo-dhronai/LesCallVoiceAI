import { useState } from 'react'
import { BrainCircuit, CheckCircle2, Sparkles, AlertTriangle } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { post } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, Spinner } from '../components/ui'
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

type RunResp = {
  ok?: boolean
  results?: Record<string, { ok?: boolean; error?: string; analysis?: AnalysisObj; cached?: boolean }>
}

type AnalysisResp = { analyzed?: boolean; analysis?: AnalysisObj | null }

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

function AnalysisView({ a }: { a: AnalysisObj }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge value={a?.sentiment} />
        <StatusBadge value={a?.lead_quality} />
      </div>

      {a?.summary && (
        <div>
          <div className="mb-1 text-[12px] font-semibold uppercase tracking-[0.08em] text-dim">Summary</div>
          <p className="text-[13px] text-soft">{a.summary}</p>
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
      <List title="Topics" items={a?.topics} />
      <List title="Products Mentioned" items={a?.products_mentioned} />
      <List title="Customer Questions" items={a?.customer_questions} />

      {a?.suggested_next_action && (
        <div>
          <div className="mb-1 text-[12px] font-semibold uppercase tracking-[0.08em] text-dim">Suggested Next Action</div>
          <p className="text-[13px] text-soft">{a.suggested_next_action}</p>
        </div>
      )}

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
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [analyzing, setAnalyzing] = useState(false)
  const [modalAnalysis, setModalAnalysis] = useState<AnalysisObj | null>(null)
  const [modalOpen, setModalOpen] = useState(false)

  const path = `/analysis/eligible?page=${page}&limit=50&search=${encodeURIComponent(search)}`
  const q = useGet<EligibleResp>(['analysis-eligible', page, search], path)

  const recordings = Array.isArray(q.data?.recordings) ? q.data!.recordings! : []
  const pages = q.data?.pages ?? 1
  const total = q.data?.total

  const visibleFolders = recordings.map((r) => r?.folder).filter(Boolean) as string[]
  const allSelected = visibleFolders.length > 0 && visibleFolders.every((f) => selected.has(f))

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
    setSearch(v)
    setPage(1)
  }

  function firstSuccess(res?: RunResp): AnalysisObj | null {
    const results = res?.results ?? {}
    for (const k of Object.keys(results)) {
      const r = results[k]
      if (r?.ok && r?.analysis) return r.analysis
    }
    return null
  }

  async function analyzeSelected() {
    if (selected.size === 0 || analyzing) return
    setAnalyzing(true)
    try {
      const res = await post<RunResp>('/analysis/run', { folders: [...selected] })
      const a = firstSuccess(res)
      toast('Analysis complete', 'success')
      qc.invalidateQueries({ queryKey: ['analysis-eligible'] })
      if (a) {
        setModalAnalysis(a)
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
    try {
      const existing = await api<AnalysisResp>(`/calls/recordings/${encodeURIComponent(folder)}/analysis`)
      if (existing?.analysis) {
        setModalAnalysis(existing.analysis)
        setModalOpen(true)
        return
      }
      if (!window.confirm('No analysis yet. Run AI analysis for this call now?')) return
      setAnalyzing(true)
      const res = await post<RunResp>('/analysis/run', { folders: [folder] })
      const a = firstSuccess(res)
      qc.invalidateQueries({ queryKey: ['analysis-eligible'] })
      if (a) {
        setModalAnalysis(a)
        setModalOpen(true)
        toast('Analysis complete', 'success')
      } else {
        const err = res?.results?.[folder]?.error
        toast(`Analysis failed${err ? `: ${err}` : ''}`, 'error')
      }
    } catch (e) {
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    } finally {
      setAnalyzing(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Call Analysis"
        sub="Run and review per-call AI analysis"
        actions={
          <Button onClick={analyzeSelected} disabled={selected.size === 0 || analyzing}>
            {analyzing ? <Spinner /> : <Sparkles size={15} />}
            {analyzing ? 'Analyzing…' : `Analyze Selected (${selected.size})`}
          </Button>
        }
      />

      <Card className="p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-soft">
            <BrainCircuit size={16} className="text-mint-500" /> Eligible recordings
          </div>
          <SearchInput value={search} onChange={onSearch} placeholder="Search contact / number" className="w-64 max-w-full" />
        </div>

        <AsyncState
          loading={q.isLoading}
          error={q.error}
          empty={!q.isLoading && recordings.length === 0}
          emptyLabel="No recordings found"
        >
          <Table>
            <THead>
              <TH className="w-8">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  className="h-4 w-4 cursor-pointer accent-mint-500"
                />
              </TH>
              <TH>Contact</TH>
              <TH>Date</TH>
              <TH>Call ID</TH>
              <TH>Status</TH>
            </THead>
            <tbody>
              {recordings.map((r) => {
                const folder = r?.folder ?? ''
                return (
                  <TR key={folder} onClick={() => openRow(r)}>
                    <TD>
                      <input
                        type="checkbox"
                        checked={selected.has(folder)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggle(folder)}
                        className="h-4 w-4 cursor-pointer accent-mint-500"
                      />
                    </TD>
                    <TD className="font-medium text-[var(--text)]">{r?.caller || '—'}</TD>
                    <TD className="whitespace-nowrap text-dim">{fmtDate(r?.folder_dt)}</TD>
                    <TD className="font-mono text-[12px] text-dim">{r?.call_id || '—'}</TD>
                    <TD>
                      {r?.analyzed
                        ? <Badge tone="mint"><CheckCircle2 size={12} /> Analyzed</Badge>
                        : <Badge tone="neutral">Not analyzed</Badge>}
                    </TD>
                  </TR>
                )
              })}
            </tbody>
          </Table>

          <Pagination page={page} pages={pages} total={total} onPage={setPage} />
        </AsyncState>
      </Card>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="Call Analysis" wide>
        {modalAnalysis
          ? <AnalysisView a={modalAnalysis} />
          : <div className="py-6 text-center text-[13px] text-dim">No analysis available.</div>}
      </Modal>
    </div>
  )
}
