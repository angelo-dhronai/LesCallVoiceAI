/**
 * Try-agent tester — one text turn at a time against a built agent, using the
 * LIVE engine (`POST /api/copilot/try/{name}`), not a simulation.
 *
 * This surfaces a backend capability that shipped months ago and was never
 * reachable from the UI: until now, the first execution of a freshly built
 * agent was a production phone call. The stage/slot readout is the point —
 * you can watch the flow advance and the slots fill turn by turn.
 */
import { useEffect, useRef, useState } from 'react'
import { Bot, CornerDownLeft, RotateCcw, User } from 'lucide-react'
import { post } from '../api/mutate'
import { Badge, Button, Spinner, cx } from './ui'
import { Textarea } from './kit'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from './ui/dialog'

type Turn = { role: 'user' | 'assistant'; text: string }
type TryResponse = {
  reply: string
  stage?: string
  language?: string
  slots?: Record<string, unknown>
  agent_revision?: number
  model?: string
}

export function TryAgentDialog({
  slug, open, onOpenChange, direction,
}: {
  slug: string
  open: boolean
  onOpenChange: (v: boolean) => void
  /** Outbound agents get a contact name injected; inbound ones do not. */
  direction?: 'inbound' | 'outbound'
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [meta, setMeta] = useState<TryResponse | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  // Each open is a fresh call — a stale half-conversation would replay into
  // the engine and produce nonsense on the first new turn.
  useEffect(() => {
    if (open) { setTurns([]); setDraft(''); setError(''); setMeta(null) }
  }, [open, slug])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns, busy])

  async function send() {
    const text = draft.trim()
    if (!text || busy) return
    const next: Turn[] = [...turns, { role: 'user', text }]
    setTurns(next)
    setDraft('')
    setBusy(true)
    setError('')
    try {
      // The endpoint is stateless: it replays the whole history each turn.
      const res = await post<TryResponse>(`/copilot/try/${slug}`, {
        messages: next,
        direction,
      })
      setMeta(res)
      setTurns([...next, { role: 'assistant', text: res.reply || '(no reply)' }])
    } catch (e: any) {
      setError(e?.message ?? String(e))
      setTurns(next)
    } finally {
      setBusy(false)
    }
  }

  const slots = Object.entries(meta?.slots ?? {})

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="card flex max-h-[85vh] max-w-2xl flex-col gap-0 p-0">
        <DialogHeader className="border-b border-[var(--border)] p-5 text-left">
          <DialogTitle className="font-display text-lg font-bold tracking-tight">
            Try “{slug}”
          </DialogTitle>
          <DialogDescription className="text-sm text-dim">
            A real turn through the live engine — the same router a phone call gets.
            No credits are spent on speech; this is text only.
          </DialogDescription>
        </DialogHeader>

        {/* Live state readout — the reason to test in text rather than by phone. */}
        {meta && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] px-5 py-2.5">
            {meta.stage && <Badge tone="indigo">stage · {meta.stage}</Badge>}
            {meta.language && <Badge tone="neutral">{meta.language}</Badge>}
            {slots.length > 0
              ? slots.map(([k, v]) => (
                <Badge key={k} tone="mint">{k.replace(/_/g, ' ')}: {String(v)}</Badge>
              ))
              : <span className="text-xs text-dim">no slots filled yet</span>}
          </div>
        )}

        <div className="min-h-[180px] flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {turns.length === 0 && !busy && (
            <p className="py-8 text-center text-sm text-dim">
              Say something a caller would say. Watch the stage and slots move above.
            </p>
          )}
          {turns.map((t, i) => (
            <div key={i} className={cx('flex gap-2', t.role === 'user' && 'flex-row-reverse')}>
              <div className={cx('grid h-7 w-7 shrink-0 place-items-center rounded-full',
                t.role === 'user' ? 'bg-[var(--surface-2)] text-soft' : 'bg-mint-400/15 text-mint-600 dark:text-mint-300')}>
                {t.role === 'user' ? <User size={13} /> : <Bot size={13} />}
              </div>
              <div className={cx('max-w-[80%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed',
                t.role === 'user'
                  ? 'rounded-br-md bg-[var(--surface-2)] text-soft'
                  : 'rounded-bl-md bg-mint-400/10 text-[var(--text)] ring-1 ring-mint-400/20')}>
                {t.text}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex items-center gap-2 text-xs text-dim">
              <Spinner className="h-3.5 w-3.5" /> agent is thinking…
            </div>
          )}
          {error && (
            <div role="alert" className="rounded-md bg-rose-500/10 px-3 py-2 text-xs text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">
              {error}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <div className="border-t border-[var(--border)] p-4">
          <div className="flex items-end gap-2">
            <Textarea
              rows={2}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
              }}
              aria-label="What the caller says"
              placeholder="e.g. Hi, I saw your ad about the 3BHK — what's the price?"
            />
            <div className="flex shrink-0 flex-col gap-1.5">
              <Button onClick={send} disabled={busy || !draft.trim()}>
                {busy ? <Spinner className="h-3.5 w-3.5" /> : <CornerDownLeft size={14} />} Send
              </Button>
              <Button variant="ghost" size="sm" onClick={() => { setTurns([]); setMeta(null); setError('') }}
                disabled={busy || turns.length === 0}>
                <RotateCcw size={13} /> Restart
              </Button>
            </div>
          </div>
          <p className="mt-2 text-2xs text-dim">
            Enter sends · Shift+Enter for a new line
            {meta?.model ? ` · engine ${meta.model}` : ''}
            {meta?.agent_revision ? ` · rev ${meta.agent_revision}` : ''}
          </p>
        </div>
      </DialogContent>
    </Dialog>
  )
}
