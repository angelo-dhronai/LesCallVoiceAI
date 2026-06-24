import { useState, useEffect } from 'react'
import { MessageSquare, Search, User, Bot } from 'lucide-react'
import { api } from '../api/client'
import { useGet } from '../api/hooks/useApi'
import { Card, Badge, Spinner, EmptyState, cx } from '../components/ui'
import { PageHeader, SearchInput, AsyncState } from '../components/kit'
import { dur } from '../lib/format'

/* ------------------------------------------------------------------ Types */

interface Recording {
  folder: string
  call_id?: string
  caller?: string
  folder_dt?: string
}
interface RecordingsResp {
  recordings?: Recording[]
  total?: number
}
interface TranscriptMessage {
  speaker?: string
  text?: string
  timestamp?: string
}
interface TranscriptResp {
  folder?: string
  metadata?: Record<string, unknown>
  messages?: TranscriptMessage[]
}

/* ------------------------------------------------------------------ Helpers */

function fmtDate(s: string | undefined | null): string {
  if (!s) return '—'
  const d = new Date(s)
  if (isNaN(d.getTime())) return String(s)
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function isAgent(speaker: string | undefined): boolean {
  const s = (speaker || '').toLowerCase()
  return s.includes('agent') || s.includes('bot') || s.includes('assistant') || s.includes('ai')
}

/* ------------------------------------------------------------------ Transcript panel */

function TranscriptPanel({ folder }: { folder: string | null }) {
  const [data, setData] = useState<TranscriptResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (!folder) {
      setData(null)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    api<TranscriptResp>(`/calls/recordings/${encodeURIComponent(folder)}/transcript`)
      .then((res) => { if (!cancelled) setData(res) })
      .catch((e) => { if (!cancelled) setError(e) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [folder])

  if (!folder) {
    return (
      <EmptyState
        icon={<MessageSquare size={20} />}
        title="Select a call to view its transcript"
        hint="Pick a recording from the list on the left."
      />
    )
  }

  const messages = Array.isArray(data?.messages) ? (data?.messages ?? []) : []

  return (
    <AsyncState loading={loading} error={error}>
      {messages.length === 0 ? (
        <EmptyState
          icon={<MessageSquare size={20} />}
          title="No transcript for this call"
        />
      ) : (
        <div className="space-y-3">
          {messages.map((m, i) => {
            const agent = isAgent(m?.speaker)
            return (
              <div key={i} className="flex items-start gap-2.5">
                <div className={cx(
                  'grid h-8 w-8 shrink-0 place-items-center rounded-xl ring-1',
                  agent
                    ? 'bg-mint-400/15 text-mint-700 ring-mint-400/30 dark:text-mint-300'
                    : 'bg-indigo-500/12 text-indigo-600 ring-indigo-500/30 dark:text-indigo-300',
                )}>
                  {agent ? <Bot size={15} /> : <User size={15} />}
                </div>
                <div className={cx(
                  'min-w-0 flex-1 rounded-2xl px-3.5 py-2.5 ring-1',
                  agent
                    ? 'bg-mint-400/10 ring-mint-400/20'
                    : 'bg-[var(--surface-2)] ring-[var(--border)]',
                )}>
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <Badge tone={agent ? 'mint' : 'indigo'}>{agent ? 'Agent' : 'Caller'}</Badge>
                    {m?.timestamp && (
                      <span className="text-[11px] tabular-nums text-dim">{m.timestamp}</span>
                    )}
                  </div>
                  <div className="whitespace-pre-wrap break-words text-[13px] text-soft">
                    {m?.text ?? ''}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </AsyncState>
  )
}

/* ------------------------------------------------------------------ Page */

export default function Transcripts() {
  const [callerFilter, setCallerFilter] = useState('')
  const [keyword, setKeyword] = useState('')
  const [debouncedKeyword, setDebouncedKeyword] = useState('')
  const [selected, setSelected] = useState<string | null>(null)

  // Debounce keyword for search endpoint
  useEffect(() => {
    const t = setTimeout(() => setDebouncedKeyword(keyword.trim()), 300)
    return () => clearTimeout(t)
  }, [keyword])

  const searchActive = debouncedKeyword.length >= 2

  const defaultQ = useGet<RecordingsResp>(
    ['recordings', 'list'],
    '/calls/recordings?limit=100&page=1',
    { enabled: !searchActive },
  )
  const searchQ = useGet<RecordingsResp>(
    ['transcripts', 'search', debouncedKeyword],
    `/calls/transcripts/search?q=${encodeURIComponent(debouncedKeyword)}`,
    { enabled: searchActive },
  )

  const activeQ = searchActive ? searchQ : defaultQ
  const allRecordings: Recording[] = Array.isArray(activeQ.data?.recordings)
    ? (activeQ.data?.recordings ?? [])
    : []

  // Client-side caller filter
  const filter = callerFilter.trim().toLowerCase()
  const recordings = filter
    ? allRecordings.filter((r) => (r?.caller || '').toLowerCase().includes(filter))
    : allRecordings

  // Default-select first item when list loads / changes
  useEffect(() => {
    if (recordings.length === 0) return
    const stillPresent = selected != null && recordings.some((r) => r?.folder === selected)
    if (!stillPresent) {
      setSelected(recordings[0]?.folder ?? null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordings])

  return (
    <div className="space-y-6">
      <PageHeader title="Call Transcripts" sub="Browse & search conversation transcripts" />

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        {/* LEFT — list + filters */}
        <Card className="flex flex-col gap-3 p-4">
          <SearchInput
            value={callerFilter}
            onChange={setCallerFilter}
            placeholder="Filter by phone / name…"
          />
          <SearchInput
            value={keyword}
            onChange={setKeyword}
            placeholder="Search words in transcripts"
          />

          <div className="flex items-center justify-between text-[11px] text-dim">
            <span>{searchActive ? `Results for "${debouncedKeyword}"` : 'Recent recordings'}</span>
            {activeQ.isFetching && <Spinner className="h-3.5 w-3.5" />}
          </div>

          <div className="-mr-1 max-h-[60vh] space-y-1 overflow-y-auto pr-1">
            <AsyncState
              loading={activeQ.isLoading}
              error={activeQ.error}
              empty={recordings.length === 0}
              emptyLabel={searchActive ? 'No matching transcripts' : 'No recordings'}
            >
              {recordings.map((r, i) => {
                const isSel = selected === r?.folder
                return (
                  <button
                    key={r?.folder ?? i}
                    onClick={() => setSelected(r?.folder ?? null)}
                    className={cx(
                      'w-full rounded-xl px-3 py-2.5 text-left transition ring-1',
                      isSel
                        ? 'bg-mint-400/12 ring-mint-400/40'
                        : 'bg-[var(--surface-2)] ring-[var(--border)] hover:bg-[var(--border)]',
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Search size={13} className="shrink-0 text-dim" />
                      <span className="truncate text-[13px] font-semibold text-soft">
                        {r?.caller || r?.call_id || r?.folder || 'Unknown'}
                      </span>
                    </div>
                    <div className="mt-0.5 pl-5 text-[11px] tabular-nums text-dim">
                      {fmtDate(r?.folder_dt)}
                    </div>
                  </button>
                )
              })}
            </AsyncState>
          </div>
        </Card>

        {/* RIGHT — transcript thread */}
        <Card className="p-4">
          <TranscriptPanel folder={selected} />
        </Card>
      </div>
    </div>
  )
}
