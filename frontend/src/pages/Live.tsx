import { memo, useEffect, useMemo, useRef, useState } from 'react'
import {
  Phone, Radio, Bot, User, Mic, Languages, MessageSquare, Clock, ArrowDown,
} from 'lucide-react'
import { useGet } from '../api/hooks/useApi'
import { useSSE } from '../api/useSSE'
import { Card, Badge, Button, EmptyState, SkeletonBlock, Spinner, cx } from '../components/ui'
import { PageHeader } from '../components/kit'
import { FullscreenModal } from '../components/Modal'
import { dur, clockTime } from '../lib/format'

/* ------------------------------------------------------------------ types */

type AnyCall = Record<string, any>

interface LiveState {
  updated_at: string | null
  calls: AnyCall[]
}

interface TranscriptLine {
  speaker: string
  text: string
}

const STAGES = ['Greeting', 'Discovery', 'Match', 'Schedule', 'Confirm', 'Close'] as const

/* ------------------------------------------------------------------ field helpers */

function pick(obj: AnyCall, keys: string[]): any {
  if (!obj || typeof obj !== 'object') return undefined
  for (const k of keys) {
    const v = obj[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return undefined
}

function callId(c: AnyCall): string {
  const v = pick(c, ['call_id', 'conn_id', 'id', 'uuid', 'session_id'])
  return v != null ? String(v) : ''
}
function callPhone(c: AnyCall): string {
  const v = pick(c, ['phone', 'caller', 'caller_number', 'from', 'caller_id', 'number'])
  return v != null ? String(v) : 'Unknown caller'
}
function callDuration(c: AnyCall): number {
  const v = pick(c, ['duration_seconds', 'duration', 'elapsed', 'seconds'])
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}
function callStartMs(c: AnyCall): number | null {
  const v = pick(c, ['started_at', 'start_time', 'start_ts', 'startedAt'])
  if (v == null) return null
  if (typeof v === 'number') return v < 1e12 ? v * 1000 : v
  const t = Date.parse(String(v))
  return Number.isFinite(t) ? t : null
}
function callLanguage(c: AnyCall): string {
  const v = pick(c, ['language', 'lang', 'detected_language'])
  return v != null ? String(v) : ''
}
function callStage(c: AnyCall): string {
  const v = pick(c, ['stage', 'conversation_stage', 'current_stage'])
  return v != null ? String(v) : ''
}
function callSpeaking(c: AnyCall): string {
  const v = pick(c, ['speaking', 'speaker', 'turn'])
  return v != null ? String(v).toLowerCase() : ''
}

/** Returns transcript as an array of {speaker,text} lines, or [] if only a snippet exists. */
function callMessages(c: AnyCall): TranscriptLine[] {
  const raw = pick(c, ['messages', 'transcript_tail', 'transcript_lines', 'history'])
  const arr = Array.isArray(raw) ? raw : []
  return arr
    .map((m: any): TranscriptLine | null => {
      if (m == null) return null
      if (typeof m === 'string') return { speaker: '', text: m }
      const text = pick(m, ['text', 'message', 'content', 'transcript'])
      if (text == null) return null
      const speaker = pick(m, ['speaker', 'role', 'who', 'from'])
      return { speaker: speaker != null ? String(speaker) : '', text: String(text) }
    })
    .filter((x): x is TranscriptLine => x != null)
}

/** Single-string transcript snippet (when no message array). */
function callSnippet(c: AnyCall): string {
  const v = pick(c, ['transcript', 'last_transcript', 'snippet', 'last_text'])
  return v != null ? String(v) : ''
}

function isAgent(speaker: string): boolean {
  const s = speaker.toLowerCase()
  return s.includes('ai') || s.includes('agent') || s.includes('assistant') || s.includes('bot')
}

/* ------------------------------------------------------------------ live duration ticker */

function useTick(activeMs: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!activeMs) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [activeMs])
  return now
}

/* ------------------------------------------------------------------ pieces */

/** Tiny 3-bar equalizer shown only while someone is actively speaking. */
function EqBars() {
  return (
    <span aria-hidden="true" className="inline-flex h-2.5 items-end gap-[2px]">
      <span className="h-1.5 w-[2px] animate-pulse-soft rounded-full bg-current" />
      <span className="h-2.5 w-[2px] animate-pulse-soft rounded-full bg-current [animation-delay:150ms]" />
      <span className="h-1.5 w-[2px] animate-pulse-soft rounded-full bg-current [animation-delay:300ms]" />
    </span>
  )
}

function SpeakingBadge({ speaking }: { speaking: string }) {
  if (speaking === 'ai') {
    return <Badge tone="indigo"><EqBars /><Bot size={11} className="mr-1 inline" />AI Speaking</Badge>
  }
  // The live snapshot reports the human turn as "user"; older payloads said "caller".
  if (speaking === 'caller' || speaking === 'user') {
    return <Badge tone="mint"><EqBars /><User size={11} className="mr-1 inline" />Caller Speaking</Badge>
  }
  return <Badge tone="neutral"><Mic size={11} className="mr-1 inline" />Listening</Badge>
}

function StageStrip({ stage }: { stage: string }) {
  const s = stage.toLowerCase()
  const activeIdx = STAGES.findIndex((st) => st.toLowerCase() === s || s.startsWith(st.toLowerCase()))
  return (
    <div>
      {activeIdx < 0 && stage !== '' && (
        <div className="mb-1.5 flex items-center gap-1.5">
          <span className="eyebrow">Stage</span>
          <Badge tone="indigo">{stage.replace(/_/g, ' ')}</Badge>
        </div>
      )}
      <div className="flex items-center gap-1.5">
        {STAGES.map((st, i) => {
          const done = activeIdx >= 0 && i < activeIdx
          const active = i === activeIdx
          return (
            <div key={st} className="flex-1" title={st}>
              <div
                className={cx(
                  'h-1.5 rounded-full transition-colors',
                  active ? 'bg-[var(--accent)]' : done ? 'bg-[var(--accent-2)]' : 'bg-[var(--surface-2)]',
                )}
              />
              <div
                className={cx(
                  'mt-1 truncate text-center text-[9px] font-semibold uppercase tracking-wide',
                  active ? 'text-[var(--text)]' : 'text-dim',
                )}
              >
                {st}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function CallCard({ call, now, onView }: { call: AnyCall; now: number; onView: () => void }) {
  const id = callId(call)
  const startMs = callStartMs(call)
  const seconds = startMs != null ? Math.max(0, Math.floor((now - startMs) / 1000)) : callDuration(call)
  const lang = callLanguage(call)
  const snippet = callSnippet(call)
  const msgs = callMessages(call)
  const tail = msgs.length ? msgs[msgs.length - 1].text : snippet

  return (
    <Card hover glow="mint" className="flex animate-fade-up">
      <div
        role="button"
        tabIndex={0}
        onClick={onView}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onView() } }}
        aria-label={`View conversation with ${callPhone(call)}`}
        className="flex flex-1 cursor-pointer flex-col gap-3 rounded-2xl p-5 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-mint-400/50"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 font-semibold">
              <Phone size={14} className="shrink-0 text-mint-500" />
              <span className="truncate">{callPhone(call)}</span>
            </div>
            {id && <div className="mt-0.5 truncate font-mono text-[10px] text-dim">{id}</div>}
          </div>
          <div className="flex shrink-0 items-center gap-1 tabular-nums text-[13px] text-soft">
            <Clock size={12} className="text-dim" />
            {dur(seconds)}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <SpeakingBadge speaking={callSpeaking(call)} />
          {lang && (
            <Badge tone="amber"><Languages size={11} className="mr-1 inline" />{lang}</Badge>
          )}
        </div>

        <StageStrip stage={callStage(call)} />

        <div>
          <div className="eyebrow mb-1">Latest</div>
          {tail ? (
            <div key={tail} className="line-clamp-2 animate-fade-up rounded-xl surface-2 px-3 py-2 text-[12px] text-soft">
              {tail}
            </div>
          ) : (
            <div className="rounded-xl surface-2 px-3 py-2 text-[12px] italic text-dim">
              Waiting for first words…
            </div>
          )}
        </div>

        <div className="mt-auto pt-1" onClick={(e) => e.stopPropagation()}>
          <Button variant="soft" size="sm" onClick={onView}>
            <MessageSquare size={13} className="mr-1 inline" />View Conversation
          </Button>
        </div>
      </div>
    </Card>
  )
}

/**
 * One transcript bubble. Memoized so the once-per-second live-state pushes
 * only re-render the line that actually changed — with minute-long calls the
 * transcript can hold hundreds of bubbles, and re-painting all of them every
 * second is exactly the lag this page used to have.
 */
const Bubble = memo(function Bubble({ speaker, text, animate }: {
  speaker: string; text: string; animate: boolean
}) {
  const agent = isAgent(speaker)
  return (
    <div className={cx('flex', agent ? 'justify-start' : 'justify-end', animate && 'animate-fade-up')}>
      <div
        className={cx(
          'max-w-[88%] rounded-2xl px-3.5 py-2.5 text-[13.5px] leading-relaxed sm:max-w-[42rem] sm:text-[15px]',
          agent
            ? 'bg-[var(--surface-2)] text-soft'
            : 'bg-mint-400/15 text-[var(--text)] ring-1 ring-mint-400/25',
        )}
      >
        <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-dim">
          {agent ? 'Agent' : 'Caller'}
        </div>
        {text}
      </div>
    </div>
  )
})

/**
 * The scrollable live transcript. Owns its scroll container: new turns pin
 * the view to the bottom with an INSTANT scroll (never scrollIntoView, which
 * smooth-scrolls every scrollable ancestor — including the page behind the
 * dialog — once per SSE push and made the whole UI feel like it lagged).
 * Scrolling up unpins; a "jump to latest" chip brings you back.
 */
function TranscriptPanel({ call }: { call: AnyCall }) {
  const msgs = callMessages(call)
  const snippet = callSnippet(call)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [pinned, setPinned] = useState(true)
  const pinnedRef = useRef(true)
  // Lines that existed when the dialog opened render statically; only turns
  // that arrive while you watch play the entrance animation.
  const animateFromRef = useRef<number | null>(null)
  if (animateFromRef.current === null) animateFromRef.current = msgs.length

  useEffect(() => {
    const el = scrollRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [msgs.length])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    pinnedRef.current = nearBottom
    setPinned(nearBottom)
  }

  const jumpToLatest = () => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
    pinnedRef.current = true
    setPinned(true)
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="min-h-0 flex-1 space-y-2.5 overflow-y-auto overscroll-contain px-4 py-4 sm:px-8 sm:py-5"
      >
        {msgs.length === 0 && !snippet && (
          <div className="grid h-full place-items-center text-[13.5px] text-dim">
            Listening — the conversation will appear here as it happens.
          </div>
        )}
        {msgs.length === 0 && snippet && (
          <div className="rounded-xl surface-2 px-3.5 py-2.5 text-[13.5px] text-soft sm:text-[15px]">{snippet}</div>
        )}
        {msgs.map((m, i) => (
          <Bubble
            key={i}
            speaker={m.speaker}
            text={m.text}
            animate={i >= (animateFromRef.current ?? 0)}
          />
        ))}
      </div>
      {!pinned && (
        <button
          onClick={jumpToLatest}
          className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-[var(--surface-solid)] px-3.5 py-2 text-[12px] font-semibold text-soft shadow-glass-lg ring-1 ring-[var(--border)] transition hover:text-[var(--text)]"
        >
          <ArrowDown size={13} /> Jump to latest
        </button>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ page */

export default function Live() {
  const { data, isLoading } = useGet<LiveState>(['calls', 'live-state'], '/calls/live-state')

  const [calls, setCalls] = useState<AnyCall[]>([])
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [seeded, setSeeded] = useState(false)

  // Seed from initial fetch.
  useEffect(() => {
    if (!data) return
    setCalls(Array.isArray(data.calls) ? data.calls : [])
    setUpdatedAt(data.updated_at ?? null)
    setSeeded(true)
  }, [data])

  const status = useSSE('/calls/live-state/stream', (evt: any) => {
    if (!evt || typeof evt !== 'object') return
    if (evt.type === 'keepalive') return
    if (evt.type === 'live_state') {
      setCalls(Array.isArray(evt.calls) ? evt.calls : [])
      setUpdatedAt(evt.updated_at ?? null)
      setSeeded(true)
    }
  }, [])

  const live = status === 'open'
  const booting = isLoading && !seeded

  // Tick only while there are active calls (cheap when idle).
  const now = useTick(calls.length)

  const openCall = useMemo(
    () => calls.find((c) => callId(c) === openId) ?? null,
    [calls, openId],
  )

  const openStartMs = openCall ? callStartMs(openCall) : null
  const openSeconds = openCall
    ? (openStartMs != null ? Math.max(0, Math.floor((now - openStartMs) / 1000)) : callDuration(openCall))
    : 0

  const updatedLabel = useMemo(() => {
    if (!updatedAt) return null
    const t = Date.parse(updatedAt)
    return Number.isFinite(t) ? clockTime(new Date(t)) : null
  }, [updatedAt])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Live Calls"
        sub="Real-time monitoring of in-progress phone calls"
        actions={
          <div className="flex items-center gap-3 text-[13px]">
            <span className="flex items-baseline gap-1.5">
              <span className="metric text-xl tabular-nums">{calls.length}</span>
              <span className="text-[12px] text-dim">active</span>
            </span>
            <span className="inline-flex items-center gap-2 font-semibold">
              <span className="relative flex h-2.5 w-2.5">
                {live && (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-mint-400 opacity-60" />
                )}
                <span className={cx('relative inline-flex h-2.5 w-2.5 rounded-full', live ? 'bg-mint-400' : 'bg-amber-400')} />
              </span>
              {live ? 'Live' : 'Reconnecting…'}
            </span>
            {updatedLabel && <span className="text-dim">Updated {updatedLabel}</span>}
          </div>
        }
      />

      {status === 'error' && calls.length > 0 && (
        <div role="alert" className="flex items-center gap-2 rounded-xl bg-amber-400/10 px-4 py-2.5 text-[13px] font-medium text-amber-600 ring-1 ring-amber-400/30 dark:text-amber-300">
          <Spinner className="!h-3.5 !w-3.5 shrink-0" />
          <span>
            Live stream interrupted — showing last update{updatedLabel ? ` from ${updatedLabel}` : ''}. Reconnecting…
          </span>
        </div>
      )}

      {booting ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <SkeletonBlock key={i} h={230} className="rounded-2xl" />
          ))}
        </div>
      ) : calls.length === 0 ? (
        // Fill the viewport so the panel doesn't look like a stub floating in dead space.
        <Card className="grid min-h-[calc(100dvh-240px)] place-items-center p-2">
          <EmptyState
            icon={
              <span className="relative grid place-items-center">
                {live && <span aria-hidden="true" className="absolute inset-0 animate-ping rounded-full bg-mint-400/30" />}
                <Radio size={22} className={cx('relative', live && 'animate-pulse-soft')} />
              </span>
            }
            title="No active calls right now"
            hint="Calls appear here the moment they connect. Finished calls move to Recordings."
            action={
              live
                ? <Badge tone="mint">Stream connected — listening for calls</Badge>
                : <Badge tone="amber">{status === 'error' ? 'Reconnecting to stream…' : 'Connecting to stream…'}</Badge>
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {calls.map((c, i) => {
            const id = callId(c)
            return (
              <CallCard
                key={id || i}
                call={c}
                now={now}
                onView={() => setOpenId(id || String(i))}
              />
            )
          })}
        </div>
      )}

      {/* The live conversation gets the whole browser: near-fullscreen popup
          on desktop, edge-to-edge on phones. */}
      <FullscreenModal
        open={openCall != null}
        onClose={() => setOpenId(null)}
        title={openCall ? (
          <span className="flex min-w-0 items-center gap-2">
            <Phone size={15} className="shrink-0 text-mint-500" />
            <span className="truncate">{callPhone(openCall)}</span>
          </span>
        ) : 'Conversation'}
        headerExtra={openCall ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <span className="flex items-center gap-1 tabular-nums text-[13px] text-soft">
              <Clock size={12} className="text-dim" />{dur(openSeconds)}
            </span>
            {callLanguage(openCall) && (
              <Badge tone="amber"><Languages size={11} className="mr-1 inline" />{callLanguage(openCall)}</Badge>
            )}
            <SpeakingBadge speaking={callSpeaking(openCall)} />
          </div>
        ) : undefined}
      >
        {openCall && (
          <>
            {callStage(openCall) && (
              <div className="shrink-0 border-b border-[var(--border)] px-4 py-3 sm:px-8">
                <StageStrip stage={callStage(openCall)} />
              </div>
            )}
            <TranscriptPanel call={openCall} />
          </>
        )}
      </FullscreenModal>
    </div>
  )
}
