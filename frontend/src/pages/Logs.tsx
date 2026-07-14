import { useState, useRef, useEffect } from 'react'
import { Trash2, ArrowDownToLine, Terminal } from 'lucide-react'
import { api } from '../api/client'
import { useSSE } from '../api/useSSE'
import { Card, Button, EmptyState, cx } from '../components/ui'
import { PageHeader, Segmented } from '../components/kit'

type LogLine = {
  line: string
  category: string
  color: string
  ts?: number | string
}

type TailResp = {
  lines?: LogLine[]
}

type StreamEvent = {
  type?: string
  category?: string
  color?: string
  line?: string
  ts?: number | string
}

const CATEGORIES = [
  { value: 'all', label: 'All' },
  { value: 'error', label: 'Errors' },
  { value: 'stt', label: 'STT' },
  { value: 'ai', label: 'AI' },
  { value: 'credits', label: 'Credits' },
  { value: 'call', label: 'Calls' },
  { value: 'system', label: 'System' },
] as const

type Category = (typeof CATEGORIES)[number]['value']

const MAX_LINES = 1000
const FALLBACK_COLOR = '#9aa4bd'
const BOTTOM_THRESHOLD = 40

// Category tag tones, aligned with the Badge/StatusBadge palette.
const TAG_TONES: Record<string, string> = {
  error: 'bg-rose-500/15 text-rose-300',
  credits: 'bg-amber-400/15 text-amber-300',
  ai: 'bg-indigo-500/15 text-indigo-300',
  stt: 'bg-mint-400/15 text-mint-300',
  call: 'bg-mint-400/15 text-mint-300',
  system: 'bg-white/5 text-[#9aa4bd]',
}

function formatTs(ts?: number | string): string {
  if (ts === undefined || ts === null || ts === '') return ''
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('en-IN', { hour12: false })
}

const EMPTY_COPY: Record<string, { title: string; hint: string }> = {
  all: {
    title: 'No log lines yet',
    hint: 'The stream is live — lines will appear here as the runtime produces them.',
  },
  error: {
    title: 'No error lines yet',
    hint: 'The stream is live — errors will appear here the moment they occur.',
  },
  stt: {
    title: 'No STT lines yet',
    hint: 'Speech-to-text activity will stream in here during live calls.',
  },
  ai: {
    title: 'No AI lines yet',
    hint: 'LLM turns and agent decisions will stream in here during live calls.',
  },
  credits: {
    title: 'No credit lines yet',
    hint: 'Credit usage events will appear here as calls consume STT/TTS credits.',
  },
  call: {
    title: 'No call lines yet',
    hint: 'Call lifecycle events will appear here when a call connects.',
  },
  system: {
    title: 'No system lines yet',
    hint: 'Runtime and service events will stream in here as they happen.',
  },
}

export default function Logs() {
  const [category, setCategory] = useState<Category>('all')
  const [lines, setLines] = useState<LogLine[]>([])
  const [autoScroll, setAutoScroll] = useState(true)
  const [seeding, setSeeding] = useState(true)
  const [cleared, setCleared] = useState(false)
  const [newSinceScroll, setNewSinceScroll] = useState(0)

  const scrollRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(autoScroll)
  autoScrollRef.current = autoScroll
  const prevLenRef = useRef(0)

  // Seed buffer (and re-seed) whenever the category filter changes.
  useEffect(() => {
    let cancelled = false
    setLines([])
    setSeeding(true)
    setCleared(false)
    setNewSinceScroll(0)
    api<TailResp>(`/logs/tail?lines=200`)
      .then((res) => {
        if (cancelled) return
        const seed = res?.lines ?? []
        const filtered =
          category === 'all' ? seed : seed.filter((l) => l?.category === category)
        setLines(filtered.slice(-MAX_LINES))
        setSeeding(false)
      })
      .catch(() => {
        /* seed failure is non-fatal; stream will populate */
        if (!cancelled) setSeeding(false)
      })
    return () => {
      cancelled = true
    }
  }, [category])

  // Live stream; reconnects when category changes (deps).
  const status = useSSE(
    `/logs/stream?categories=${category}`,
    (data: StreamEvent) => {
      if (!data || data.type === 'keepalive') return
      if (data.type !== 'log') return
      setCleared(false)
      setLines((prev) => {
        const next = [
          ...prev,
          {
            line: data.line ?? '',
            category: data.category ?? '',
            color: data.color ?? FALLBACK_COLOR,
            ts: data.ts,
          },
        ]
        return next.length > MAX_LINES ? next.slice(-MAX_LINES) : next
      })
    },
    [category],
  )

  // Auto-scroll to bottom when new lines arrive; count arrivals while paused.
  useEffect(() => {
    const delta = lines.length - prevLenRef.current
    prevLenRef.current = lines.length
    if (autoScrollRef.current) {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
      setNewSinceScroll(0)
    } else if (delta > 0) {
      setNewSinceScroll((n) => n + delta)
    }
  }, [lines])

  // Scrolling up suspends auto-scroll; returning near the bottom resumes it.
  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD
    if (nearBottom) {
      if (!autoScrollRef.current) {
        setAutoScroll(true)
        setNewSinceScroll(0)
      }
    } else if (autoScrollRef.current) {
      setAutoScroll(false)
    }
  }

  const jumpToLatest = () => {
    setAutoScroll(true)
    setNewSinceScroll(0)
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }

  const emptyCopy = cleared
    ? { title: 'Buffer cleared', hint: 'New lines will stream in below — the connection is still live.' }
    : EMPTY_COPY[category] ?? EMPTY_COPY.all

  return (
    <div className="space-y-6">
      <PageHeader
        title="Live Logs"
        sub="Streaming runtime logs"
        actions={
          <>
            <div className="inline-flex items-center gap-1.5 px-1 text-[12px] font-semibold">
              <span
                className={cx(
                  'inline-block h-2 w-2 rounded-full',
                  status === 'open' && 'bg-mint-400 shadow-glow-mint',
                  status === 'connecting' && 'bg-[var(--text-dim)]',
                  status === 'error' && 'bg-amber-400 animate-pulse',
                )}
              />
              <span className="text-dim" aria-live="polite">
                {status === 'open' ? 'Live' : status === 'connecting' ? 'Connecting…' : 'Reconnecting…'}
              </span>
              <span
                className="tabular-nums text-dim font-normal"
                title={
                  lines.length === MAX_LINES
                    ? 'Buffer keeps the last 1,000 lines; oldest are dropped'
                    : undefined
                }
              >
                · {lines.length.toLocaleString('en-IN')} lines
              </span>
            </div>
            <Button
              variant={autoScroll ? 'soft' : 'ghost'}
              size="sm"
              onClick={() => setAutoScroll((v) => !v)}
              title="Toggle auto-scroll"
            >
              <ArrowDownToLine size={14} />
              Auto-scroll
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setLines([])
                setCleared(true)
                setNewSinceScroll(0)
              }}
              title="Clear log buffer"
            >
              <Trash2 size={14} />
              Clear
            </Button>
          </>
        }
      />

      <Card className="p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="max-w-full overflow-x-auto">
            <Segmented<Category>
              options={CATEGORIES.map((c) => ({ value: c.value, label: c.label }))}
              value={category}
              onChange={(v) => setCategory(v)}
            />
          </div>
          <span className="text-[11px] uppercase tracking-wide text-dim">
            last 200 seeded · streaming
          </span>
        </div>
        <div className="relative">
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            tabIndex={0}
            role="log"
            aria-label="Live logs"
            className="h-[calc(100dvh-240px)] min-h-[320px] overflow-y-auto rounded-xl bg-[#0b0f1a] p-4 font-mono text-[12px] leading-relaxed ring-1 ring-white/10 dark:bg-[#060910]"
          >
            {seeding ? (
              <div className="space-y-2.5 py-2" aria-hidden="true">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div
                    key={i}
                    className="skeleton h-3.5 opacity-40"
                    style={{ width: `${55 + ((i * 17) % 40)}%` }}
                  />
                ))}
              </div>
            ) : lines.length === 0 ? (
              <div className="[&_.text-soft]:text-[#e7ecf7] [&_.text-dim]:text-[#9aa4bd] [&_.surface-2]:bg-white/5">
                <EmptyState
                  icon={<Terminal size={20} />}
                  title={emptyCopy.title}
                  hint={emptyCopy.hint}
                />
              </div>
            ) : (
              lines.map((l, i) => (
                <div
                  key={i}
                  className={cx(
                    '-mx-1 rounded px-1 py-px pl-6 -indent-6 whitespace-pre-wrap [overflow-wrap:anywhere] hover:bg-white/5',
                    l?.category === 'error' && 'border-l-2 border-rose-400 bg-rose-500/10',
                  )}
                >
                  <span className="mr-2 hidden w-16 text-[10px] tabular-nums text-[#5c6784] sm:inline-block">
                    {formatTs(l?.ts)}
                  </span>
                  <span
                    className={cx(
                      'mr-2 inline-block w-12 rounded px-1 text-center text-[10px] font-semibold uppercase tracking-wide sm:w-14',
                      TAG_TONES[l?.category ?? ''] ?? 'bg-white/5 text-[#9aa4bd]',
                    )}
                  >
                    {l?.category || '—'}
                  </span>
                  <span style={{ color: l?.color || FALLBACK_COLOR }}>{l?.line ?? ''}</span>
                </div>
              ))
            )}
          </div>
          {!autoScroll && newSinceScroll > 0 && (
            <button
              onClick={jumpToLatest}
              className="absolute bottom-3 left-1/2 inline-flex min-h-9 -translate-x-1/2 items-center gap-1.5 rounded-full bg-gradient-to-b from-mint-300 to-mint-500 px-3.5 py-2 text-[12px] font-semibold text-[#04150d] shadow-glow-mint sm:min-h-0 sm:px-3 sm:py-1.5"
            >
              <ArrowDownToLine size={13} />
              {newSinceScroll} new {newSinceScroll === 1 ? 'line' : 'lines'}
            </button>
          )}
        </div>
      </Card>
    </div>
  )
}
