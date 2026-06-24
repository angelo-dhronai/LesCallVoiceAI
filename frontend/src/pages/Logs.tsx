import { useState, useRef, useEffect } from 'react'
import { Trash2, ArrowDownToLine } from 'lucide-react'
import { api } from '../api/client'
import { useSSE } from '../api/useSSE'
import { Card, Button, cx } from '../components/ui'
import { PageHeader, Segmented } from '../components/kit'

type LogLine = {
  line: string
  category: string
  color: string
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

export default function Logs() {
  const [category, setCategory] = useState<Category>('all')
  const [lines, setLines] = useState<LogLine[]>([])
  const [autoScroll, setAutoScroll] = useState(true)

  const scrollRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(autoScroll)
  autoScrollRef.current = autoScroll

  // Seed buffer (and re-seed) whenever the category filter changes.
  useEffect(() => {
    let cancelled = false
    setLines([])
    api<TailResp>(`/logs/tail?lines=200`)
      .then((res) => {
        if (cancelled) return
        const seed = res?.lines ?? []
        const filtered =
          category === 'all' ? seed : seed.filter((l) => l?.category === category)
        setLines(filtered.slice(-MAX_LINES))
      })
      .catch(() => {
        /* seed failure is non-fatal; stream will populate */
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
      setLines((prev) => {
        const next = [
          ...prev,
          {
            line: data.line ?? '',
            category: data.category ?? '',
            color: data.color ?? FALLBACK_COLOR,
          },
        ]
        return next.length > MAX_LINES ? next.slice(-MAX_LINES) : next
      })
    },
    [category],
  )

  // Auto-scroll to bottom when new lines arrive.
  useEffect(() => {
    if (!autoScrollRef.current) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines])

  const live = status === 'open'

  return (
    <div className="space-y-6">
      <PageHeader
        title="Live Logs"
        sub="Streaming runtime logs"
        actions={
          <>
            <Segmented<Category>
              options={CATEGORIES.map((c) => ({ value: c.value, label: c.label }))}
              value={category}
              onChange={(v) => setCategory(v)}
            />
            <div className="inline-flex items-center gap-1.5 px-1 text-[12px] font-semibold">
              <span
                className={cx(
                  'inline-block h-2 w-2 rounded-full',
                  live ? 'bg-mint-400 shadow-glow-mint' : 'bg-amber-400 animate-pulse',
                )}
              />
              <span className="text-dim">{live ? 'Live' : 'Reconnecting…'}</span>
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
              onClick={() => setLines([])}
              title="Clear log buffer"
            >
              <Trash2 size={14} />
              Clear
            </Button>
          </>
        }
      />

      <Card className="p-4">
        <div
          ref={scrollRef}
          className="h-[60vh] overflow-y-auto rounded-xl bg-[#0b0f1a] p-4 font-mono text-[12px] leading-relaxed dark:bg-black/40"
        >
          {lines.length === 0 ? (
            <div className="py-8 text-center text-[12px]" style={{ color: FALLBACK_COLOR }}>
              Waiting for logs…
            </div>
          ) : (
            lines.map((l, i) => (
              <div
                key={i}
                className="whitespace-pre-wrap break-all"
                style={{ color: l?.color || FALLBACK_COLOR }}
              >
                {l?.line ?? ''}
              </div>
            ))
          )}
        </div>
      </Card>
    </div>
  )
}
