import { useCallback, useEffect, useRef, useState } from 'react'
import { useRefresh } from '../api/hooks/useApi'
import {
  copilotChat, copilotSession, copilotSessions, copilotDeleteSession,
  type CopilotAction, type TranscriptMsg, type SessionSummary,
} from '../api/copilot'
import { cx, Button } from '../components/ui'
import {
  Sparkles, SendHorizontal, RotateCcw, Check, X,
  Plus, Pencil, Radio, Bot, Trash2, MessagesSquare, SquarePen,
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

/* ─────────────────────────── history sidebar ─────────────────────────── */

function HistorySidebar({
  sessions, activeSid, onSelect, onNew, onDelete,
}: {
  sessions: SessionSummary[]
  activeSid: string
  onSelect: (sid: string) => void
  onNew: () => void
  onDelete: (sid: string) => void
}) {
  return (
    <aside className="hidden w-60 shrink-0 flex-col gap-2 md:flex">
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
            <button onClick={() => onSelect(s.session_id)} className="w-full px-2.5 py-2 text-left">
              <div className="flex items-center gap-1.5">
                <span className={cx(
                  'truncate text-[12.5px] font-semibold',
                  s.session_id === activeSid ? 'text-mint-700 dark:text-mint-300' : 'text-[var(--text)]',
                )}>
                  {s.title || 'New chat'}
                </span>
                <span className="ml-auto shrink-0 pr-5 text-[10.5px] text-dim">{relTime(s.updated_at)}</span>
              </div>
              {s.agent && (
                <div className="mt-0.5 flex items-center gap-1 text-[11px] text-dim">
                  <Bot size={11} /> <span className="truncate">{s.agent}</span>
                </div>
              )}
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(s.session_id) }}
              title="Delete chat"
              className="absolute right-1.5 top-2 hidden rounded-md p-1 text-dim transition hover:bg-rose-500/10 hover:text-rose-500 group-hover:block"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>
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
    <div className="mt-2 flex flex-wrap gap-1.5">
      {actions.map((a, i) => (
        <span
          key={i}
          title={a.tool}
          className={cx(
            'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold ring-1',
            a.ok
              ? 'bg-mint-400/12 text-mint-700 ring-mint-400/25 dark:text-mint-300'
              : 'bg-rose-500/10 text-rose-600 ring-rose-500/25 dark:text-rose-300',
          )}
        >
          {a.ok ? <Check size={11} /> : <X size={11} />}
          {a.summary}
        </span>
      ))}
    </div>
  )
}

function Bubble({ msg }: { msg: TranscriptMsg }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[82%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-gradient-to-b from-mint-300 to-mint-500 px-4 py-2.5 text-[13.5px] font-medium leading-relaxed text-[#04150d] shadow-glow-mint">
          {msg.content}
        </div>
      </div>
    )
  }
  return (
    <div className="flex items-start gap-2.5">
      <CopilotAvatar />
      <div className="max-w-[82%]">
        <div className="whitespace-pre-wrap rounded-2xl rounded-tl-md bg-[var(--surface-2)] px-4 py-2.5 text-[13.5px] leading-relaxed text-[var(--text)] ring-1 ring-[var(--border)]">
          {msg.content}
        </div>
        <ActionChips actions={msg.actions} />
      </div>
    </div>
  )
}

function TypingBubble() {
  return (
    <div className="flex items-start gap-2.5">
      <CopilotAvatar />
      <div className="flex items-center gap-1.5 rounded-2xl rounded-tl-md bg-[var(--surface-2)] px-4 py-3.5 ring-1 ring-[var(--border)]">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text)] opacity-40"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </div>
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
    </div>
  )
}

/* ─────────────────────────── page ─────────────────────────── */

export default function CopilotChat() {
  const [sid, setSid] = useState<string>(() => loadSessionId())
  const [messages, setMessages] = useState<TranscriptMsg[]>([])
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const refresh = useRefresh()

  const loadHistory = useCallback(() => {
    copilotSessions()
      .then((res) => setSessions(res.sessions ?? []))
      .catch(() => { /* history is best-effort */ })
  }, [])

  // Restore transcript on mount / session switch
  useEffect(() => {
    let cancelled = false
    copilotSession(sid)
      .then((res) => { if (!cancelled) setMessages(res.transcript ?? []) })
      .catch(() => { /* fresh session or backend restart — start empty */ })
    return () => { cancelled = true }
  }, [sid])

  // Chat history for the sidebar
  useEffect(() => { loadHistory() }, [loadHistory])

  // Auto-scroll to the newest message
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  const send = async (text?: string) => {
    const content = (text ?? input).trim()
    if (!content || busy) return
    setInput('')
    if (inputRef.current) inputRef.current.style.height = 'auto'
    setMessages((m) => [...m, { role: 'user', content }])
    setBusy(true)
    try {
      const res = await copilotChat(sid, content)
      setMessages((m) => [...m, { role: 'assistant', content: res.reply, actions: res.actions }])
      if (res.agents_changed) refresh(['agents'])
      loadHistory() // title/agent may have changed — keep the sidebar in sync
    } catch (e: any) {
      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          content: `Sorry — I hit an error talking to the copilot backend (${e?.message || e}). Please try again.`,
          actions: [],
        },
      ])
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  const newChat = () => {
    const fresh = newSessionId()
    localStorage.setItem(SID_KEY, fresh)
    setSid(fresh)
    setMessages([])
    setInput('')
    inputRef.current?.focus()
  }

  const selectChat = (id: string) => {
    if (id === sid || busy) return
    localStorage.setItem(SID_KEY, id)
    setSid(id)
    setInput('')
  }

  const deleteChat = async (id: string) => {
    if (!confirm('Delete this conversation?')) return
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
    <div className="mx-auto flex h-[calc(100dvh-140px)] min-h-[420px] w-full max-w-5xl gap-5">
      <HistorySidebar
        sessions={sessions}
        activeSid={sid}
        onSelect={selectChat}
        onNew={newChat}
        onDelete={deleteChat}
      />

      <div className="flex min-w-0 flex-1 flex-col">
      {/* header row */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[12px] text-dim">
          {messages.length > 0 && (
            <>
              <Sparkles size={13} className="text-mint-600 dark:text-mint-300" />
              <span className="font-semibold text-soft">Agent Copilot</span>
              <span>· builds and manages your voice agents</span>
            </>
          )}
        </div>
        {messages.length > 0 && (
          <Button variant="ghost" size="sm" onClick={newChat} title="Start a fresh conversation">
            <RotateCcw size={13} /> New chat
          </Button>
        )}
      </div>

      {/* messages */}
      <div ref={scrollRef} className="flex flex-1 flex-col overflow-y-auto pr-1">
        {messages.length === 0 && !busy ? (
          <EmptyState onPick={(p) => send(p)} />
        ) : (
          <div className="flex flex-col gap-4 py-2">
            {messages.map((m, i) => <Bubble key={i} msg={m} />)}
            {busy && <TypingBubble />}
          </div>
        )}
      </div>

      {/* composer */}
      <div className="mt-3">
        <div className="card flex items-end gap-2 p-2.5">
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            disabled={busy}
            placeholder={busy ? 'Copilot is working…' : 'Message the copilot — e.g. "Create a salon receptionist agent"'}
            onChange={(e) => { setInput(e.target.value); autoGrow(e.target) }}
            onKeyDown={onKeyDown}
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-[13.5px] leading-relaxed text-[var(--text)] outline-none placeholder:text-dim disabled:opacity-50"
          />
          <Button onClick={() => send()} disabled={busy || !input.trim()} title="Send (Enter)">
            <SendHorizontal size={15} />
          </Button>
        </div>
        <p className="mt-1.5 px-1 text-center text-[11px] text-dim">
          Enter to send · Shift+Enter for a new line · the copilot writes real agents under{' '}
          <code className="font-mono text-[10px]">agents/</code>
        </p>
      </div>
      </div>
    </div>
  )
}
