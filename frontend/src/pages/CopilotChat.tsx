import { useCallback, useEffect, useRef, useState } from 'react'
import { useRefresh } from '../api/hooks/useApi'
import {
  copilotChat, copilotSession, copilotSessions, copilotDeleteSession,
  copilotUpload, applyPendingChange, discardPendingChange,
  type CopilotAction, type TranscriptMsg, type SessionSummary,
  type InterviewProgress, type PendingChange,
} from '../api/copilot'
import { cx, Button, Spinner } from '../components/ui'
import {
  Sparkles, SendHorizontal, RotateCcw, Check, X, ArrowDown,
  Plus, Pencil, Radio, Bot, Trash2, MessagesSquare, SquarePen, Lightbulb,
  Paperclip, FileText, ClipboardList, Eye, EyeOff,
} from 'lucide-react'

/* ─────────────────────────── session id ─────────────────────────── */

const SID_KEY = 'copilot_session_id'

function newSessionId() {
  return `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
}

function loadSessionId() {
  let sid = localStorage.getItem(SID_KEY)
  if (!sid || !/^[A-Za-z0-9_-]{1,80}$/.test(sid)) {
    sid = newSessionId()
    localStorage.setItem(SID_KEY, sid)
  }
  return sid
}

function relTime(iso?: string | null): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000))
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.round(hours / 24)
  return days < 7 ? `${days}d` : new Date(iso).toLocaleDateString()
}

/* Local message shape: TranscriptMsg plus a frontend-only error flag so
   failed turns render distinctly and offer a Retry affordance. */
type ChatMsg = TranscriptMsg & { isError?: boolean }

/* ─────────────────────────── history sidebar ─────────────────────────── */

type SessionListProps = {
  sessions: SessionSummary[]
  activeSid: string
  onSelect: (sid: string) => void
  onNew: () => void
  onDelete: (sid: string) => void
}

function SessionList({ sessions, activeSid, onSelect, onNew, onDelete }: SessionListProps) {
  // Inline two-step delete confirm (replaces the native confirm() dialog).
  const [confirmingId, setConfirmingId] = useState<string | null>(null)

  useEffect(() => {
    if (!confirmingId) return
    const t = setTimeout(() => setConfirmingId(null), 3000)
    return () => clearTimeout(t)
  }, [confirmingId])

  return (
    <>
      <button
        onClick={onNew}
        className="flex items-center gap-2 rounded-xl bg-[var(--surface-2)] px-3 py-2.5 text-[13px] font-semibold ring-1 ring-[var(--border)] transition hover:ring-mint-400/40"
      >
        <SquarePen size={15} className="text-mint-600 dark:text-mint-300" /> New chat
      </button>
      <div className="eyebrow mt-2 px-1">Previous chats</div>
      <div className="flex-1 space-y-1 overflow-y-auto pr-0.5">
        {sessions.length === 0 && (
          <div className="flex items-center gap-2 px-1 py-2 text-[12px] text-dim">
            <MessagesSquare size={13} /> No conversations yet
          </div>
        )}
        {sessions.map((s) => (
          <div
            key={s.session_id}
            className={cx(
              'group relative rounded-lg ring-1 transition',
              s.session_id === activeSid
                ? 'bg-mint-400/10 ring-mint-400/30'
                : 'bg-transparent ring-transparent hover:bg-[var(--surface-2)] hover:ring-[var(--border)]',
            )}
          >
            <button
              onClick={() => onSelect(s.session_id)}
              aria-current={s.session_id === activeSid ? 'true' : undefined}
              className="w-full px-2.5 py-2 text-left"
            >
              <div className="flex items-center gap-1.5">
                <span className={cx(
                  'truncate text-[12.5px] font-semibold',
                  s.session_id === activeSid ? 'text-mint-700 dark:text-mint-300' : 'text-[var(--text)]',
                )}>
                  {s.title || 'New chat'}
                </span>
                <span className="ml-auto shrink-0 text-[10.5px] text-dim">{relTime(s.updated_at)}</span>
                {s.message_count != null && (
                  <span className="shrink-0 pr-7 text-[10.5px] tabular-nums text-dim">· {s.message_count}</span>
                )}
              </div>
              {s.agent && (
                <div className="mt-0.5 flex items-center gap-1 text-[11px] text-dim">
                  <Bot size={11} /> <span className="truncate">{s.agent_name || s.agent}</span>
                </div>
              )}
            </button>
            {confirmingId === s.session_id ? (
              <div className="absolute right-1 top-1 flex items-center gap-2">
                <button
                  onClick={(e) => { e.stopPropagation(); setConfirmingId(null); onDelete(s.session_id) }}
                  title="Confirm delete"
                  aria-label="Confirm delete"
                  className="rounded-md bg-rose-500/15 p-2 text-rose-500 transition hover:bg-rose-500/25"
                >
                  <Check size={14} />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); setConfirmingId(null) }}
                  title="Cancel delete"
                  aria-label="Cancel delete"
                  className="rounded-md p-2 text-dim transition hover:bg-[var(--surface-2)] hover:text-soft"
                >
                  <X size={14} />
                </button>
              </div>
            ) : (
              <button
                onClick={(e) => { e.stopPropagation(); setConfirmingId(s.session_id) }}
                title="Delete chat"
                aria-label="Delete chat"
                className="absolute right-1 top-1 rounded-md p-2 text-dim opacity-60 transition hover:bg-rose-500/10 hover:text-rose-500 focus-visible:opacity-100 md:opacity-0 md:group-focus-within:opacity-100 md:group-hover:opacity-100"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        ))}
      </div>
    </>
  )
}

function HistorySidebar(props: SessionListProps) {
  return (
    <aside className="hidden w-60 shrink-0 flex-col gap-2 md:flex">
      <SessionList {...props} />
    </aside>
  )
}

/* ─────────────────────────── pieces ─────────────────────────── */

function CopilotAvatar() {
  return (
    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-mint-300 to-indigo-500 text-[#04150d] shadow-glow-mint">
      <Sparkles size={15} />
    </div>
  )
}

function ActionChips({ actions }: { actions?: CopilotAction[] }) {
  if (!actions?.length) return null
  return (
    <div className="mt-2">
      <span className="eyebrow text-[10px]">Actions taken</span>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {actions.map((a, i) => (
          <span
            key={i}
            title={a.ok ? a.tool : `${a.tool} failed — ask the copilot to retry this step`}
            className={cx(
              'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold ring-1',
              a.ok
                ? 'bg-mint-400/12 text-mint-700 ring-mint-400/25 dark:text-mint-300'
                : 'bg-rose-500/10 text-rose-600 ring-rose-500/25 dark:text-rose-300',
            )}
          >
            {a.ok ? <Check size={11} /> : <X size={11} />}
            <code className="font-mono text-[10px] opacity-70">{a.tool}</code>
            {a.summary}
          </span>
        ))}
      </div>
    </div>
  )
}

/* Split assistant text on ``` fences so config/YAML/scripts render as code
   blocks instead of a flat text wall. All original content is preserved. */
function AssistantText({ content }: { content: string }) {
  const parts = content.split('```')
  if (parts.length < 3) return <>{content}</>
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <pre
            key={i}
            className="mt-2 overflow-x-auto rounded-xl bg-[var(--bg-2)] px-3 py-2 font-mono text-[12px] leading-relaxed ring-1 ring-[var(--border)]"
          >
            {part.replace(/^[A-Za-z0-9_-]*\n/, '')}
          </pre>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  )
}

function Bubble({ msg, onRetry }: { msg: ChatMsg; onRetry?: () => void }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[82%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-gradient-to-b from-mint-300 to-mint-500 px-4 py-2.5 text-[13.5px] font-medium leading-relaxed text-[#04150d] shadow-glow-mint">
          {msg.content}
        </div>
      </div>
    )
  }
  return (
    <div className="flex items-start gap-2.5">
      <CopilotAvatar />
      <div className="max-w-[82%]">
        <div className={cx(
          'whitespace-pre-wrap break-words rounded-2xl rounded-tl-md px-4 py-2.5 text-[13.5px] leading-relaxed ring-1',
          msg.isError
            ? 'bg-rose-500/10 text-rose-600 ring-rose-500/25 dark:text-rose-300'
            : 'bg-[var(--surface-2)] text-[var(--text)] ring-[var(--border)]',
        )}>
          <AssistantText content={msg.content} />
        </div>
        {msg.isError && onRetry && (
          <div className="mt-2">
            <Button variant="ghost" size="sm" onClick={onRetry} title="Re-send the last message">
              <RotateCcw size={13} /> Retry
            </Button>
          </div>
        )}
        <ActionChips actions={msg.actions} />
      </div>
    </div>
  )
}

function TypingBubble() {
  // Phase caption keeps long tool-running turns (10-30s) from reading as a hang.
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [])
  const caption = elapsed < 4
    ? 'Thinking…'
    : elapsed < 12
      ? 'Working on it — running actions…'
      : 'Still working. Building or editing an agent can take up to a minute.'
  return (
    <div className="flex items-start gap-2.5">
      <CopilotAvatar />
      <div className="flex flex-col rounded-2xl rounded-tl-md bg-[var(--surface-2)] px-4 py-3 ring-1 ring-[var(--border)]">
        <div className="flex items-center gap-1.5 py-1">
          {[0, 150, 300].map((delay) => (
            <span
              key={delay}
              className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text)] opacity-40"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </div>
        <div className="mt-1 text-[11px] text-dim">{caption}</div>
      </div>
    </div>
  )
}

/* ─────────────── interview progress rail + spec preview (v2) ─────────────── */

function InterviewRail({ progress }: { progress: InterviewProgress }) {
  const done = progress.layers.filter((l) => l.done || l.skipped).length
  return (
    <div className="mb-2 rounded-xl bg-[var(--surface-2)] px-3 py-2 ring-1 ring-[var(--border)]">
      <div className="flex items-center gap-2">
        <ClipboardList size={13} className="shrink-0 text-mint-600 dark:text-mint-300" />
        <span className="text-[11.5px] font-semibold text-soft">
          Building your agent — {done}/{progress.layers.length} topics covered
        </span>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {progress.layers.map((l) => (
          <span
            key={l.id}
            title={l.skipped ? 'Skipped at your request' : l.done ? 'Covered' : 'Still to cover'}
            className={cx(
              'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold ring-1',
              l.done
                ? 'bg-mint-400/12 text-mint-700 ring-mint-400/25 dark:text-mint-300'
                : l.skipped
                  ? 'bg-[var(--bg-2)] text-dim ring-[var(--border)] line-through'
                  : l.id === progress.next_layer_id
                    ? 'bg-amber-400/15 text-amber-600 ring-amber-400/30 dark:text-amber-300'
                    : 'bg-transparent text-dim ring-[var(--border)]',
            )}
          >
            {l.done ? <Check size={10} /> : null}
            {l.title}
          </span>
        ))}
      </div>
    </div>
  )
}

function previewSections(draft: Record<string, any>): { title: string; rows: string[] }[] {
  const out: { title: string; rows: string[] }[] = []
  const id = draft.identity || {}
  const who: string[] = []
  if (id.agent_name) who.push(`Name: ${id.agent_name}`)
  if (id.company_name) who.push(`Company: ${id.company_name}`)
  if (id.role_description) who.push(`Role: ${id.role_description}`)
  if (draft.call_direction) who.push(`Direction: ${draft.call_direction}`)
  if (who.length) out.push({ title: 'Who it is', rows: who })
  const langs = draft.languages || {}
  const speak: string[] = []
  if (langs.supported?.length) speak.push(`Languages: ${langs.supported.join(', ')}`)
  if (langs.default) speak.push(`Opens in: ${langs.default}`)
  const voices = langs.tts_voices ? Object.values(langs.tts_voices) as any[] : []
  if (voices[0]?.speaker) speak.push(`Voice: ${voices[0].speaker}`)
  const pers = draft.personality || {}
  if (pers.tone || pers.energy) speak.push(`Style: ${[pers.tone, pers.energy].filter(Boolean).join(', ')}`)
  if (speak.length) out.push({ title: 'How it speaks', rows: speak })
  if (draft.call_flow?.call_goal) {
    out.push({ title: 'Mission', rows: [draft.call_flow.call_goal] })
  }
  const stages = draft.call_flow?.stages
  if (stages?.length) {
    out.push({ title: 'Call flow', rows: stages.map((s: any) => s.id).filter(Boolean) })
  }
  if (draft.slots?.length) {
    out.push({
      title: 'What it collects',
      rows: draft.slots.map((s: any) => s.name).filter(Boolean),
    })
  }
  const kbText = draft.knowledge_base?.portfolio_prompt_text
  const catalog = draft.knowledge_base?.catalog
  if (kbText || catalog?.length) {
    const rows: string[] = []
    if (catalog?.length) rows.push(`${catalog.length} catalog item(s): ${catalog.map((c: any) => c.name).filter(Boolean).join(', ')}`)
    if (kbText) rows.push(String(kbText).slice(0, 160) + (String(kbText).length > 160 ? '…' : ''))
    out.push({ title: 'What it knows', rows })
  }
  const gr = draft.guardrails || {}
  const rules = [...(gr.never_rules || []).map((r: string) => `Never: ${r}`),
                 ...(gr.always_rules || []).map((r: string) => `Always: ${r}`)]
  if (rules.length) out.push({ title: 'Rules', rows: rules.slice(0, 6) })
  const greeting = draft.response_bank?.opening_greeting
  if (greeting) out.push({ title: 'Opening line', rows: [String(greeting)] })
  return out
}

function SpecPreview({ progress }: { progress: InterviewProgress }) {
  const sections = previewSections(progress.draft || {})
  if (!sections.length) return null
  return (
    <div className="space-y-3">
      <div className="eyebrow px-1">Agent so far</div>
      {sections.map((s) => (
        <div key={s.title} className="rounded-xl bg-[var(--surface-2)] p-2.5 ring-1 ring-[var(--border)]">
          <div className="text-[11px] font-semibold text-mint-700 dark:text-mint-300">{s.title}</div>
          <ul className="mt-1 space-y-0.5">
            {s.rows.map((r, i) => (
              <li key={i} className="break-words text-[11.5px] leading-snug text-soft">{r}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

/* ───────────────────── pending-change diff cards (v2) ───────────────────── */

function DiffCards({ changes, busy, onApply, onDiscard }: {
  changes: PendingChange[]
  busy: boolean
  onApply: (id: string) => void
  onDiscard: (id: string) => void
}) {
  if (!changes.length) return null
  return (
    <div className="space-y-2">
      {changes.map((c) => (
        <div key={c.change_id} className="rounded-xl bg-amber-400/8 p-3 ring-1 ring-amber-400/25">
          <div className="flex items-center gap-2 text-[12px] font-semibold text-soft">
            <Pencil size={13} className="text-amber-500" />
            Proposed change{c.slug ? ` to ${c.slug}` : ''}
            {c.reason ? <span className="font-normal text-dim">— {c.reason}</span> : null}
          </div>
          <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-[var(--bg-2)] px-3 py-2 font-mono text-[11px] leading-relaxed ring-1 ring-[var(--border)]">
            {c.diff}
          </pre>
          <div className="mt-2 flex gap-2">
            <Button size="sm" disabled={busy} onClick={() => onApply(c.change_id)} title="Apply this change">
              <Check size={13} /> Apply
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDiscard(c.change_id)} title="Discard this change">
              <X size={13} /> Discard
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}

const SUGGESTIONS = [
  {
    icon: Plus,
    title: 'Create a new agent',
    sub: 'I will interview you and build it step by step',
    prompt: 'I want to create a new voice agent.',
  },
  {
    icon: Pencil,
    title: 'Edit an existing agent',
    sub: 'Change the greeting, voice, flow, or knowledge',
    prompt: 'I want to edit one of my existing agents.',
  },
  {
    icon: Radio,
    title: 'Which agent is live right now?',
    sub: 'See what answers your inbound calls',
    prompt: 'Which agent is live right now?',
  },
]

/* Placeholder tip — the two live templates the call engine substitutes in the
   opening greeting. Shown in the empty state and (dismissibly) above the
   composer so agent builders discover them mid-conversation too. */
const TIP_KEY = 'copilot_placeholder_tip_dismissed'

function PlaceholderTip({ onDismiss }: { onDismiss?: () => void }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl bg-amber-400/10 px-3 py-2.5 text-left ring-1 ring-amber-400/25">
      <Lightbulb size={15} className="mt-0.5 shrink-0 text-amber-500" />
      <div className="min-w-0 flex-1 text-[12px] leading-relaxed text-soft">
        <span className="font-semibold text-[var(--text)]">Tip — smart greeting placeholders:</span>{' '}
        use <code className="rounded bg-[var(--bg-2)] px-1 py-0.5 font-mono text-[11px]">{'{contact_name}'}</code>{' '}
        in your opening line and the agent says the dialed contact's name from your contact list, and{' '}
        <code className="rounded bg-[var(--bg-2)] px-1 py-0.5 font-mono text-[11px]">{'{current_time}'}</code>{' '}
        becomes morning, afternoon or evening at call time — e.g.{' '}
        <span className="italic">"Good {'{current_time}'}, am I speaking with {'{contact_name}'}?"</span>
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          title="Dismiss tip"
          aria-label="Dismiss tip"
          className="shrink-0 -m-1 rounded-md p-2 text-dim transition hover:bg-[var(--surface-2)] hover:text-soft"
        >
          <X size={14} />
        </button>
      )}
    </div>
  )
}

function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 py-10 text-center">
      <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-mint-300 to-indigo-500 text-[#04150d] shadow-glow-mint">
        <Sparkles size={26} />
      </div>
      <div>
        <h1 className="font-display text-[24px] font-bold tracking-tight">Agent Copilot</h1>
        <p className="mx-auto mt-1.5 max-w-md text-[13px] text-dim">
          Describe the voice agent you need — I'll ask a few questions, then build it,
          wire it up, and put it on your phone line when you're ready.
        </p>
      </div>
      <div className="grid w-full max-w-2xl gap-3 sm:grid-cols-3">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.title}
            onClick={() => onPick(s.prompt)}
            className="card card-hover flex flex-col items-start gap-2 p-4 text-left transition-all"
          >
            <div className="grid h-8 w-8 place-items-center rounded-xl bg-[var(--surface-2)] text-mint-600 ring-1 ring-[var(--border)] dark:text-mint-300">
              <s.icon size={15} />
            </div>
            <div className="text-[13px] font-semibold leading-tight">{s.title}</div>
            <div className="text-[11.5px] leading-snug text-dim">{s.sub}</div>
          </button>
        ))}
      </div>
      <div className="w-full max-w-2xl">
        <PlaceholderTip />
      </div>
    </div>
  )
}

/* ─────────────────────────── page ─────────────────────────── */

export default function CopilotChat() {
  const [sid, setSid] = useState<string>(() => loadSessionId())
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadingTranscript, setLoadingTranscript] = useState(false)
  const [mobileChatsOpen, setMobileChatsOpen] = useState(false)
  const [showJump, setShowJump] = useState(false)
  const [tipDismissed, setTipDismissed] = useState(() => localStorage.getItem(TIP_KEY) === '1')
  // v2: interview progress rail + live spec preview + pending diff cards
  const [interview, setInterview] = useState<InterviewProgress | null>(null)
  const [pendingChanges, setPendingChanges] = useState<PendingChange[]>([])
  const [showPreview, setShowPreview] = useState(true)
  const [attachments, setAttachments] = useState<{ doc_id: string; name: string }[]>([])
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const nearBottomRef = useRef(true)
  const lastUserRef = useRef('')
  const refresh = useRefresh()

  const loadHistory = useCallback(() => {
    copilotSessions()
      .then((res) => setSessions(res.sessions ?? []))
      .catch(() => { /* history is best-effort */ })
  }, [])

  // Restore transcript on mount / session switch. Only replace when the
  // server actually has history — switching to a brand-new/empty session must
  // not wipe messages we just appended locally (see the fresh-session retry
  // in send()). Explicit "New chat" clears via newChat().
  useEffect(() => {
    let cancelled = false
    copilotSession(sid)
      .then((res) => {
        if (!cancelled && (res.transcript?.length ?? 0) > 0) setMessages(res.transcript)
      })
      .catch(() => { /* fresh session, backend restart, or not ours — leave as-is */ })
      .finally(() => { if (!cancelled) setLoadingTranscript(false) })
    return () => { cancelled = true }
  }, [sid])

  // Chat history for the sidebar
  useEffect(() => { loadHistory() }, [loadHistory])

  // Auto-scroll to the newest message — but only when the user is already
  // near the bottom; otherwise offer a "Latest" pill instead of yanking them.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (nearBottomRef.current) {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    } else if (messages.length > 0) {
      setShowJump(true)
    }
  }, [messages, busy])

  // Close the mobile chats sheet on Escape
  useEffect(() => {
    if (!mobileChatsOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMobileChatsOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [mobileChatsOpen])

  const onMessagesScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    nearBottomRef.current = near
    if (near) setShowJump(false)
  }

  const jumpToLatest = () => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    nearBottomRef.current = true
    setShowJump(false)
  }

  const send = async (text?: string, opts?: { skipEcho?: boolean }) => {
    const content = (text ?? input).trim()
    if (!content || busy) return
    lastUserRef.current = content
    if (!opts?.skipEcho) {
      setInput('')
      if (inputRef.current) inputRef.current.style.height = 'auto'
      setMessages((m) => [...m, { role: 'user', content }])
    }
    setBusy(true)
    const docIds = attachments.map((a) => a.doc_id)
    setAttachments([])
    try {
      let res
      try {
        res = await copilotChat(sid, content, docIds)
      } catch (e: any) {
        // A stored session id can point at a chat we don't own (a legacy chat,
        // or one from another account on a shared browser); the backend 404s
        // to protect it. Silently start a fresh session and retry once.
        const msg = String(e?.message || e).toLowerCase()
        if (msg.includes('session not found') || msg.includes('404')) {
          const fresh = newSessionId()
          localStorage.setItem(SID_KEY, fresh)
          setSid(fresh)
          res = await copilotChat(fresh, content, docIds)
        } else {
          throw e
        }
      }
      // Adopt the server's real session id (it may have remapped a stale one)
      // so localStorage and future turns stay aligned.
      const realSid = res.session?.session_id
      if (realSid && realSid !== sid) {
        localStorage.setItem(SID_KEY, realSid)
        setSid(realSid)
      }
      setMessages((m) => [...m, { role: 'assistant', content: res.reply, actions: res.actions }])
      setInterview(res.interview ?? null)
      setPendingChanges(res.pending_changes ?? [])
      if (res.agents_changed) refresh(['agents'])
      loadHistory() // title/agent may have changed — keep the sidebar in sync
    } catch (e: any) {
      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          content: `Sorry — I hit an error talking to the copilot backend (${e?.message || e}). Please try again.`,
          actions: [],
          isError: true,
        },
      ])
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  // Re-send the last user message after a failed turn without echoing it again.
  const retryLast = () => {
    if (!lastUserRef.current || busy) return
    void send(lastUserRef.current, { skipEcho: true })
  }

  const newChat = () => {
    const fresh = newSessionId()
    localStorage.setItem(SID_KEY, fresh)
    setSid(fresh)
    setMessages([])
    setInput('')
    setInterview(null)
    setPendingChanges([])
    setAttachments([])
    setLoadingTranscript(false)
    inputRef.current?.focus()
  }

  const attachFile = async (file: File) => {
    setUploading(true)
    try {
      const res = await copilotUpload(sid, file)
      // The upload may have remapped a stale session id — adopt it.
      if (res.session_id && res.session_id !== sid) {
        localStorage.setItem(SID_KEY, res.session_id)
        setSid(res.session_id)
      }
      setAttachments((a) => [...a, { doc_id: res.doc_id, name: file.name }])
    } catch (e: any) {
      setMessages((m) => [...m, {
        role: 'assistant', isError: true, actions: [],
        content: `Couldn't upload "${file.name}" (${e?.message || e}).`,
      }])
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const applyChange = async (changeId: string) => {
    setBusy(true)
    try {
      const res = await applyPendingChange(sid, changeId)
      setPendingChanges((p) => p.filter((c) => c.change_id !== changeId))
      setMessages((m) => [...m, {
        role: 'assistant', actions: [],
        content: `Applied — revision ${res.revision}. It takes effect from the next call.`,
      }])
      refresh(['agents'])
    } catch (e: any) {
      setMessages((m) => [...m, {
        role: 'assistant', isError: true, actions: [],
        content: `Couldn't apply the change (${e?.message || e}).`,
      }])
    } finally {
      setBusy(false)
    }
  }

  const discardChange = async (changeId: string) => {
    try {
      await discardPendingChange(sid, changeId)
    } catch { /* best-effort */ }
    setPendingChanges((p) => p.filter((c) => c.change_id !== changeId))
  }

  const selectChat = (id: string) => {
    if (id === sid || busy) return
    setLoadingTranscript(true)
    nearBottomRef.current = true
    setShowJump(false)
    localStorage.setItem(SID_KEY, id)
    setSid(id)
    setInput('')
  }

  const deleteChat = async (id: string) => {
    try {
      await copilotDeleteSession(id)
      setSessions((s) => s.filter((x) => x.session_id !== id))
      if (id === sid) newChat()
    } catch { /* best-effort */ }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const autoGrow = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }

  return (
    <div className="mx-auto flex h-[calc(100dvh-140px)] min-h-[420px] w-full max-w-6xl gap-5">
      <HistorySidebar
        sessions={sessions}
        activeSid={sid}
        onSelect={selectChat}
        onNew={newChat}
        onDelete={deleteChat}
      />

      {/* mobile chat-history sheet (the sidebar is hidden below md) */}
      {mobileChatsOpen && (
        <>
          <div
            className="fixed inset-0 z-30 bg-black/40 backdrop-blur-sm md:hidden"
            aria-hidden="true"
            onClick={() => setMobileChatsOpen(false)}
          />
          <div
            role="dialog"
            aria-label="Chat history"
            className="card fixed inset-x-4 top-16 z-40 flex max-h-[60dvh] flex-col gap-2 overflow-y-auto p-3 md:hidden"
          >
            <SessionList
              sessions={sessions}
              activeSid={sid}
              onSelect={(id) => { setMobileChatsOpen(false); selectChat(id) }}
              onNew={() => { setMobileChatsOpen(false); newChat() }}
              onDelete={deleteChat}
            />
          </div>
        </>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
      {/* header row */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[12px] text-dim">
          {messages.length > 0 && (
            <>
              <Sparkles size={13} className="text-mint-600 dark:text-mint-300" />
              <span className="font-semibold text-soft">Agent Copilot</span>
              <span className="hidden sm:inline">· builds and manages your voice agents</span>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            className="md:hidden"
            onClick={() => setMobileChatsOpen(true)}
            title="Previous chats"
          >
            <MessagesSquare size={13} /> Chats
          </Button>
          {messages.length > 0 && (
            <Button variant="ghost" size="sm" onClick={newChat} title="Start a fresh conversation">
              <RotateCcw size={13} /> New chat
            </Button>
          )}
        </div>
      </div>

      {/* interview progress rail (v2) */}
      {interview && interview.phase !== 'idle' && messages.length > 0 && (
        <InterviewRail progress={interview} />
      )}

      {/* messages */}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div ref={scrollRef} onScroll={onMessagesScroll} className="flex flex-1 flex-col overflow-y-auto pr-1">
          {loadingTranscript ? (
            <div className="flex flex-col gap-4 py-2">
              <div className="skeleton h-16 w-2/3 rounded-2xl rounded-tl-md" />
              <div className="skeleton ml-auto h-10 w-1/2 rounded-2xl rounded-br-md" />
              <div className="skeleton h-20 w-3/4 rounded-2xl rounded-tl-md" />
            </div>
          ) : messages.length === 0 && !busy ? (
            <EmptyState onPick={(p) => send(p)} />
          ) : (
            <div className="flex flex-col gap-4 py-2">
              {messages.map((m, i) => (
                <Bubble
                  key={i}
                  msg={m}
                  onRetry={m.isError && i === messages.length - 1 && !busy ? retryLast : undefined}
                />
              ))}
              {pendingChanges.length > 0 && !busy && (
                <DiffCards
                  changes={pendingChanges}
                  busy={busy}
                  onApply={applyChange}
                  onDiscard={discardChange}
                />
              )}
              {busy && <TypingBubble />}
            </div>
          )}
        </div>
        {showJump && (
          <button
            onClick={jumpToLatest}
            className="glass absolute bottom-2 left-1/2 z-10 inline-flex -translate-x-1/2 items-center gap-1 rounded-full px-3 py-1.5 text-[12px] font-semibold text-soft"
          >
            <ArrowDown size={13} /> Latest
          </button>
        )}
      </div>

      {/* composer */}
      <div className="mt-3">
        {messages.length > 0 && !tipDismissed && (
          <div className="mb-2">
            <PlaceholderTip
              onDismiss={() => { localStorage.setItem(TIP_KEY, '1'); setTipDismissed(true) }}
            />
          </div>
        )}
        {attachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attachments.map((a) => (
              <span
                key={a.doc_id}
                className="inline-flex items-center gap-1.5 rounded-md bg-indigo-400/10 px-2 py-1 text-[11px] font-semibold text-indigo-600 ring-1 ring-indigo-400/25 dark:text-indigo-300"
              >
                <FileText size={12} /> {a.name}
                <button
                  onClick={() => setAttachments((x) => x.filter((y) => y.doc_id !== a.doc_id))}
                  title="Remove attachment"
                  aria-label={`Remove ${a.name}`}
                  className="rounded p-0.5 hover:bg-indigo-400/20"
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="card flex items-end gap-2 p-2.5 transition-shadow focus-within:shadow-glow-mint focus-within:ring-1 focus-within:ring-mint-400/40">
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            accept=".pdf,.txt,.md,.csv,.docx,.doc"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void attachFile(f)
            }}
          />
          <Button
            variant="ghost"
            size="sm"
            disabled={busy || uploading}
            onClick={() => fileRef.current?.click()}
            title="Attach a brochure / price sheet — the copilot reads it into the agent"
          >
            {uploading ? <Spinner /> : <Paperclip size={15} />}
          </Button>
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            disabled={busy}
            aria-label="Message the copilot"
            placeholder={busy ? 'Copilot is working…' : 'Message the copilot — e.g. "Create a salon receptionist agent"'}
            onChange={(e) => { setInput(e.target.value); autoGrow(e.target) }}
            onKeyDown={onKeyDown}
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-[13.5px] leading-relaxed text-[var(--text)] outline-none placeholder:text-dim disabled:opacity-50"
          />
          <Button onClick={() => send()} disabled={busy || !input.trim()} title="Send (Enter)">
            {busy ? <Spinner /> : <SendHorizontal size={15} />}
          </Button>
        </div>
        <p className="mt-1.5 px-1 text-center text-[11px] text-dim">
          Enter to send · Shift+Enter for a new line · the copilot writes real agents under{' '}
          <code className="font-mono text-[10px]">agents/</code>
        </p>
      </div>
      </div>

      {/* live spec preview pane (v2) — xl screens, while an interview is live */}
      {interview && interview.phase !== 'idle' &&
        Object.keys(interview.draft || {}).length > 0 && (
        <aside className="hidden w-64 shrink-0 flex-col gap-2 xl:flex">
          <button
            onClick={() => setShowPreview((v) => !v)}
            className="flex items-center gap-2 rounded-xl bg-[var(--surface-2)] px-3 py-2 text-[12px] font-semibold ring-1 ring-[var(--border)] transition hover:ring-mint-400/40"
            title={showPreview ? 'Hide the live preview' : 'Show the live preview'}
          >
            {showPreview ? <EyeOff size={13} /> : <Eye size={13} />}
            {showPreview ? 'Hide preview' : 'Show preview'}
          </button>
          {showPreview && (
            <div className="flex-1 overflow-y-auto pr-0.5">
              <SpecPreview progress={interview} />
            </div>
          )}
        </aside>
      )}
    </div>
  )
}
