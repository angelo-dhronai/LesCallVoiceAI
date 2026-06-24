import { useEffect, useMemo, useState } from 'react'
import {
  Phone, Radio, Bot, User, Mic, Languages, MessageSquare, Clock,
} from 'lucide-react'
import { useGet } from '../api/hooks/useApi'
import { useSSE } from '../api/useSSE'
import { Card, Badge, Button, EmptyState, cx } from '../components/ui'
import { PageHeader } from '../components/kit'
import { SlideOver } from '../components/Modal'
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
  const v = pick(c, ['phone', 'caller', 'from', 'caller_id', 'number'])
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

function SpeakingBadge({ speaking }: { speaking: string }) {
  if (speaking === 'ai') {
    return <Badge tone="indigo"><Bot size={11} className="mr-1 inline" />AI Speaking</Badge>
  }
  if (speaking === 'caller') {
    return <Badge tone="mint"><User size={11} className="mr-1 inline" />Caller Speaking</Badge>
  }
  return <Badge tone="neutral"><Mic size={11} className="mr-1 inline" />Listening</Badge>
}

function StageStrip({ stage }: { stage: string }) {
  const s = stage.toLowerCase()
  const activeIdx = STAGES.findIndex((st) => st.toLowerCase() === s || s.startsWith(st.toLowerCase()))
  return (
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
    <Card hover glow="mint" className="flex flex-col gap-3 p-5">
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

      {tail && (
        <div className="line-clamp-2 rounded-xl surface-2 px-3 py-2 text-[12px] text-soft">
          {tail}
        </div>
      )}

      <div className="mt-auto pt-1">
        <Button variant="soft" size="sm" onClick={onView}>
          <MessageSquare size={13} className="mr-1 inline" />View Conversation
        </Button>
      </div>
    </Card>
  )
}

function Transcript({ call }: { call: AnyCall }) {
  const msgs = callMessages(call)
  const snippet = callSnippet(call)

  if (!msgs.length && !snippet) {
    return <div className="py-8 text-center text-[13px] text-dim">No transcript yet.</div>
  }

  if (!msgs.length) {
    return (
      <div className="rounded-xl surface-2 px-3 py-2.5 text-[13px] text-soft">{snippet}</div>
    )
  }

  return (
    <div className="space-y-2.5">
      {msgs.map((m, i) => {
        const agent = isAgent(m.speaker)
        return (
          <div key={i} className={cx('flex', agent ? 'justify-start' : 'justify-end')}>
            <div
              className={cx(
                'max-w-[80%] rounded-2xl px-3 py-2 text-[13px]',
                agent
                  ? 'bg-[var(--surface-2)] text-soft'
                  : 'bg-mint-400/15 text-[var(--text)] ring-1 ring-mint-400/25',
              )}
            >
              <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-dim">
                {agent ? 'Agent' : 'Caller'}
              </div>
              {m.text}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ page */

export default function Live() {
  const { data } = useGet<LiveState>(['calls', 'live-state'], '/calls/live-state')

  const [calls, setCalls] = useState<AnyCall[]>([])
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)

  // Seed from initial fetch.
  useEffect(() => {
    if (!data) return
    setCalls(Array.isArray(data.calls) ? data.calls : [])
    setUpdatedAt(data.updated_at ?? null)
  }, [data])

  const status = useSSE('/calls/live-state/stream', (evt: any) => {
    if (!evt || typeof evt !== 'object') return
    if (evt.type === 'keepalive') return
    if (evt.type === 'live_state') {
      setCalls(Array.isArray(evt.calls) ? evt.calls : [])
      setUpdatedAt(evt.updated_at ?? null)
    }
  }, [])

  const live = status === 'open'

  // Tick only while there are active calls (cheap when idle).
  const now = useTick(calls.length)

  const openCall = useMemo(
    () => calls.find((c) => callId(c) === openId) ?? null,
    [calls, openId],
  )

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

      {calls.length === 0 ? (
        <EmptyState icon={<Radio size={22} />} title="No active calls right now" hint="Calls appear here the moment they connect." />
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

      <SlideOver
        open={openCall != null}
        onClose={() => setOpenId(null)}
        title={openCall ? `Conversation — ${callPhone(openCall)}` : 'Conversation'}
        headerExtra={openCall ? <SpeakingBadge speaking={callSpeaking(openCall)} /> : undefined}
      >
        {openCall ? <Transcript call={openCall} /> : null}
      </SlideOver>
    </div>
  )
}
