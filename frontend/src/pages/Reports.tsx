/**
 * Reports — every generated report lives here. Reports are created from the
 * Intelligence page (select calls → Generate report) and synthesized in the
 * background; this page lists them and renders the finished document.
 */
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Download, FileText, RefreshCw, Trash2 } from 'lucide-react'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { api } from '../api/client'
import { del } from '../api/mutate'
import type { Report } from '../api/intel'
import { Button, IconButton, Spinner, Card } from '../components/ui'
import {
  AsyncState, PageHeader, Pagination, StatusBadge, Table, TD, TH, THead, TR,
} from '../components/kit'
import { FullscreenModal } from '../components/Modal'
import { useToast } from '../components/Toast'
import { useConfirm } from '../components/confirm'
import { useUrlNumber } from '../lib/hooks'

const LIMIT = 50

/* Minimal, dependency-free markdown renderer for report documents:
   headings, bold/italic, lists, tables, hr, paragraphs. */
function mdToHtml(md: string): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const inline = (s: string) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\W)\*([^*\n]+)\*(?=\W|$)/g, '$1<em>$2</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
  const lines = md.split('\n')
  const out: string[] = []
  let list: string[] = []
  let table: string[][] = []
  const flushList = () => {
    if (list.length) { out.push('<ul>' + list.map((l) => `<li>${l}</li>`).join('') + '</ul>'); list = [] }
  }
  const flushTable = () => {
    if (table.length) {
      const [head, ...body] = table
      out.push('<div class="md-table"><table><thead><tr>'
        + head.map((c) => `<th>${c}</th>`).join('') + '</tr></thead><tbody>'
        + body.map((r) => '<tr>' + r.map((c) => `<td>${c}</td>`).join('') + '</tr>').join('')
        + '</tbody></table></div>')
      table = []
    }
  }
  for (const raw of lines) {
    const line = raw.trimEnd()
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    const li = /^\s*[-*]\s+(.*)$/.exec(line)
    const num = /^\s*\d+\.\s+(.*)$/.exec(line)
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line.trim().slice(1, -1).split('|').map((c) => inline(c.trim()))
      if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) table.push(cells)
      continue
    }
    flushTable()
    if (h) { flushList(); out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); continue }
    if (li) { list.push(inline(li[1])); continue }
    if (num) { list.push(inline(num[1])); continue }
    flushList()
    if (/^---+$/.test(line)) { out.push('<hr/>'); continue }
    if (line.trim()) out.push(`<p>${inline(line)}</p>`)
  }
  flushList(); flushTable()
  return out.join('\n')
}

export default function Reports() {
  const toast = useToast()
  const refresh = useRefresh()
  const confirm = useConfirm()
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useUrlNumber('page', 1)
  const [open, setOpen] = useState<Report | null>(null)
  // Which row is being fetched — a single global flag spun every row at once.
  const [openingId, setOpeningId] = useState<number | null>(null)

  const anyRunning = (items: Report[] | undefined) =>
    (items ?? []).some((r) => r.status === 'queued' || r.status === 'running')

  const q = useGet<{ items: Report[]; total: number }>(
    ['reports', page], `/intel/reports?page=${page}&limit=${LIMIT}`,
    { refetchInterval: 10_000 })

  async function openReport(id: number) {
    setOpeningId(id)
    try {
      const rep = await api<Report>(`/intel/reports/${id}`)
      setOpen(rep)
    } catch (e: any) {
      toast(`Failed to open: ${e?.message ?? e}`, 'error')
    } finally {
      setOpeningId(null)
    }
  }

  // deep link: /reports?open=<id> (used by the "report ready" notification)
  useEffect(() => {
    const id = params.get('open')
    if (id) {
      openReport(Number(id))
      params.delete('open')
      setParams(params, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function download(rep: Report) {
    const blob = new Blob([rep.document ?? ''], { type: 'text/markdown' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${rep.title.replace(/\W+/g, '-').toLowerCase()}.md`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  async function remove(rep: Report) {
    const ok = await confirm({
      title: 'Delete report?',
      description: `“${rep.title}” will be removed permanently. The calls it was built from are not affected.`,
      confirmLabel: 'Delete report',
      destructive: true,
    })
    if (!ok) return
    try {
      await del(`/intel/reports/${rep.id}`)
      toast('Report deleted', 'success')
      refresh(['reports'])
    } catch (e: any) {
      toast(`Failed: ${e?.message ?? e}`, 'error')
    }
  }

  const html = useMemo(() => (open?.document ? mdToHtml(open.document) : ''), [open])
  const items = q.data?.items ?? []
  const pages = Math.max(1, Math.ceil((q.data?.total ?? 0) / LIMIT))

  return (
    <div className="space-y-6">
      <PageHeader title="Reports"
        sub="AI-synthesized documents from selected call sets. Create one from the Intelligence page."
        actions={
          <Button variant="ghost" onClick={() => refresh(['reports'])}>
            <RefreshCw size={14} /> Refresh
          </Button>
        } />

      <Card className="p-5">
        <AsyncState loading={q.isLoading} error={q.error} empty={items.length === 0}
          emptyLabel="No reports yet — select calls on the Intelligence page and click Generate report.">
          <Table>
            <THead>
              <TH>Title</TH>
              <TH>Filter</TH>
              <TH>Calls</TH>
              <TH>Status</TH>
              <TH>Created</TH>
              <TH className="text-right">Actions</TH>
            </THead>
            {/* Only finished reports open — a queued row must not offer a
                pointer cursor and a focus stop that lead nowhere. */}
            <tbody>
              {items.map((r) => (
                <TR key={r.id}
                  onClick={r.status === 'done' ? () => openReport(r.id) : undefined}>
                  <TD className="font-medium text-[var(--text)]">{r.title}</TD>
                  <TD className="max-w-[240px] truncate text-dim"
                    title={r.query_prompt ?? ''}>{r.query_prompt || '—'}</TD>
                  <TD className="tabular-nums">{r.call_count}</TD>
                  <TD>
                    <span className="inline-flex items-center gap-1.5">
                      <StatusBadge value={r.status} />
                      {(r.status === 'queued' || r.status === 'running') && <Spinner className="h-3 w-3" />}
                    </span>
                    {r.error && <div className="mt-0.5 max-w-[220px] truncate text-2xs text-rose-500"
                      title={r.error}>{r.error}</div>}
                  </TD>
                  <TD className="whitespace-nowrap tabular-nums">
                    {r.created_at?.slice(0, 16).replace('T', ' ')}
                  </TD>
                  <TD className="text-right">
                    {/* The row itself opens the report; actions must not double-fire. */}
                    <div className="inline-flex gap-1" onClick={(e) => e.stopPropagation()}>
                      {r.status === 'done' && (
                        <IconButton label={`Open “${r.title}”`}
                          onClick={() => openReport(r.id)}>
                          {openingId === r.id ? <Spinner className="h-3.5 w-3.5" /> : <FileText size={14} />}
                        </IconButton>
                      )}
                      <IconButton label={`Delete “${r.title}”`} onClick={() => remove(r)}>
                        <Trash2 size={14} />
                      </IconButton>
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
          <Pagination page={page} pages={pages} total={q.data?.total} onPage={setPage} />
        </AsyncState>
        {anyRunning(items) && (
          <div className="mt-3 text-xs text-dim">
            Reports generate in the background — this list refreshes automatically.
          </div>
        )}
      </Card>

      <FullscreenModal open={!!open} onClose={() => setOpen(null)}
        title={open?.title}
        headerExtra={open ? (
          <Button variant="ghost" size="sm" onClick={() => download(open)}>
            <Download size={13} /> Download .md
          </Button>
        ) : undefined}>
        <div className="report-doc mx-auto max-w-3xl px-1 py-2"
          dangerouslySetInnerHTML={{ __html: html }} />
      </FullscreenModal>
    </div>
  )
}
