import { useEffect, useRef, useState } from 'react'
import { Check, Copy, Download, FileAudio, Link2, RefreshCw, SearchX } from 'lucide-react'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { Badge, Button, Card, EmptyState, Spinner, cx } from '../components/ui'
import { PageHeader, Table, THead, TH, TR, TD, Pagination, SearchInput, AsyncState } from '../components/kit'
import { AudioPlayer } from '../components/AudioPlayer'

type Recording = {
  folder: string
  call_id: string | null
  caller: string
  folder_dt: string
  /** Public tokenized download link (unguessable, shareable without login). */
  download_url?: string | null
  gcs_uploaded?: boolean
}

type RecordingsResponse = {
  recordings: Recording[]
  total: number
  page: number
  limit: number
  pages: number
}

function parseDate(dt: string | null | undefined): Date | null {
  if (!dt) return null
  const d = new Date(dt)
  return isNaN(d.getTime()) ? null : d
}

/** 'Today' / 'Yesterday' / '12 Jul' (with year when it isn't the current one). */
function formatDay(d: Date): string {
  const now = new Date()
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diffDays = Math.round((startOf(now) - startOf(d)) / 86400000)
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  return d.toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' as const } : {}),
  })
}

export default function Recordings() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const copyTimer = useRef<number | null>(null)
  const refresh = useRefresh()

  // Debounce keystrokes so the query (and the table) doesn't churn per character.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 300)
    return () => clearTimeout(t)
  }, [search])

  // Reset to first page whenever the search term changes.
  useEffect(() => {
    setPage(1)
  }, [search])

  useEffect(() => () => {
    if (copyTimer.current) window.clearTimeout(copyTimer.current)
  }, [])

  const { data, isLoading, isFetching, error } = useGet<RecordingsResponse>(
    ['recordings', page, debounced],
    `/calls/recordings?page=${page}&limit=50&search=${encodeURIComponent(debounced)}`,
  )

  // Keep the previous page's rows on screen (dimmed) while the next fetch runs,
  // instead of tearing the table down into skeletons on every page/search change.
  const [prev, setPrev] = useState<RecordingsResponse | null>(null)
  useEffect(() => {
    if (data) setPrev(data)
  }, [data])
  const view = data ?? prev ?? undefined
  const refreshing = isLoading && view != null

  const recordings = Array.isArray(view?.recordings) ? view!.recordings : []
  const total = view?.total ?? 0
  const pages = view?.pages ?? 1

  const copyCallId = (id: string) => {
    navigator.clipboard?.writeText(id).then(() => {
      setCopiedId(id)
      if (copyTimer.current) window.clearTimeout(copyTimer.current)
      copyTimer.current = window.setTimeout(() => setCopiedId(null), 1200)
    }).catch(() => {})
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Recordings"
        sub="Call audio & metadata"
        actions={
          <>
            {total > 0 && <Badge tone="neutral">{total.toLocaleString('en-IN')} recordings</Badge>}
            <Button variant="ghost" onClick={() => refresh(['recordings'])}>
              <RefreshCw size={14} /> Refresh
            </Button>
          </>
        }
      />

      <Card className="p-5">
        <div className="mb-4 flex items-center gap-3">
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search by contact / number"
            className="w-full max-w-sm"
          />
          {isFetching && view != null && <Spinner className="h-4 w-4 shrink-0" />}
        </div>

        <AsyncState loading={isLoading && view == null} error={error}>
          {recordings.length === 0 ? (
            debounced ? (
              <EmptyState
                icon={<SearchX size={20} />}
                title={`No recordings match "${debounced}"`}
                hint="Try a shorter name or the last digits of the number."
                action={
                  <Button variant="ghost" size="sm" onClick={() => setSearch('')}>
                    Clear search
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={<FileAudio size={20} />}
                title="No recordings yet"
                hint="Audio is captured automatically for every completed call — recordings will appear here after your agent takes its first call."
              />
            )
          ) : (
            <div className={cx('transition-opacity', refreshing && 'pointer-events-none opacity-60')}>
              <Table>
                <THead>
                  <TH>Contact</TH>
                  <TH>Date</TH>
                  <TH>Call ID</TH>
                  <TH>Play</TH>
                  <TH>Download</TH>
                </THead>
                <tbody>
                  {recordings.map((r) => {
                    const folder = r?.folder ?? ''
                    const enc = encodeURIComponent(folder)
                    const audioUrl = `/api/calls/recordings/${enc}/audio`
                    // Prefer the public tokenized link (works without a
                    // session and survives local-file cleanup after GCS sync).
                    const downloadUrl = r?.download_url || `${audioUrl}?download=1`
                    const d = parseDate(r?.folder_dt)
                    return (
                      <TR key={folder || r?.call_id || Math.random()} className="group">
                        <TD className="font-medium text-[var(--text)]">{r?.caller || '—'}</TD>
                        <TD className="whitespace-nowrap tabular-nums" title={d ? d.toLocaleString() : undefined}>
                          {d ? (
                            <>
                              <span className="text-[var(--text)]">{formatDay(d)}</span>{' '}
                              <span className="text-dim">
                                {d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                              </span>
                            </>
                          ) : (
                            <span className="text-dim">{r?.folder_dt || '—'}</span>
                          )}
                        </TD>
                        <TD>
                          <div className="flex items-center gap-1.5">
                            <span
                              className="max-w-[160px] truncate font-mono text-[12px] text-dim"
                              title={r?.call_id ?? ''}
                            >
                              {r?.call_id || '—'}
                            </span>
                            {r?.call_id && (
                              <button
                                onClick={() => copyCallId(r.call_id!)}
                                aria-label="Copy call ID"
                                title="Copy call ID"
                                className={cx(
                                  '-m-2.5 shrink-0 p-2.5 text-dim opacity-100 transition-opacity hover:text-soft md:opacity-0 md:focus-visible:opacity-100 md:group-hover:opacity-100',
                                  copiedId === r.call_id && 'md:opacity-100',
                                )}
                              >
                                {copiedId === r.call_id ? (
                                  <Check size={12} className="text-mint-500" />
                                ) : (
                                  <Copy size={12} />
                                )}
                              </button>
                            )}
                          </div>
                        </TD>
                        <TD>
                          <AudioPlayer src={audioUrl} caller={r?.caller || undefined} />
                        </TD>
                        <TD>
                          <div className="flex items-center gap-1.5">
                            <a
                              href={downloadUrl}
                              download
                              className={cx(
                                'inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-1.5 text-[12px] font-semibold transition-all active:scale-[.97]',
                                'text-soft bg-[var(--surface-2)] hover:bg-[var(--border)] ring-1 ring-[var(--border)]',
                              )}
                            >
                              <Download size={14} /> Download
                            </a>
                            {r?.download_url && (
                              <button
                                onClick={() => copyCallId(r.download_url!)}
                                aria-label="Copy public download link"
                                title="Copy public download link"
                                className={cx(
                                  'inline-flex items-center justify-center rounded-xl p-2.5 text-dim transition-all hover:text-soft md:p-1.5',
                                  'bg-[var(--surface-2)] hover:bg-[var(--border)] ring-1 ring-[var(--border)] active:scale-[.97]',
                                )}
                              >
                                {copiedId === r.download_url ? (
                                  <Check size={14} className="text-mint-500" />
                                ) : (
                                  <Link2 size={14} />
                                )}
                              </button>
                            )}
                          </div>
                        </TD>
                      </TR>
                    )
                  })}
                </tbody>
              </Table>
            </div>
          )}
        </AsyncState>

        <Pagination page={page} pages={pages} total={total} onPage={setPage} />
      </Card>
    </div>
  )
}
