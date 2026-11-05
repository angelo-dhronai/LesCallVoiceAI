/**
 * The blank LesCall Studio: one question and one quiet line of tips.
 *
 * The question is chosen once per page load, so it changes at every sign-in
 * (Google OAuth is a full page load) and stays put while someone moves around
 * the app. It uses the account's first name only: an email prefix ("tech") is
 * not a name, so without a real one the question simply goes unaddressed.
 */
import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { useMe } from '../api/hooks/useMe'
import { cx } from './ui'

const OPENING_LINES = [
  'What are we making today',
  'What should we build today',
  'What are we building next',
  'Who should your next agent call',
  'Which call can we make better today',
  'What can we improve today',
  "What's on your mind today",
  'What shall we create today',
  'Where do we begin today',
  'Ready to build something',
]

const LAST_LINE_KEY = 'studio_last_opening_line'

/** Never the same question twice in a row. Storage can throw in a private
 *  window, and then a repeat is the worst that happens. */
function pickOpeningLine(): string {
  let last = -1
  try { last = Number(localStorage.getItem(LAST_LINE_KEY) ?? -1) } catch { /* no storage */ }
  const others = OPENING_LINES.map((_, i) => i).filter((i) => i !== last)
  const next = others[Math.floor(Math.random() * others.length)]
  try { localStorage.setItem(LAST_LINE_KEY, String(next)) } catch { /* no storage */ }
  return OPENING_LINES[next]
}

// Module scope = once per page load; opening a chat and returning keeps it.
let chosenLine: string | null = null

export function StudioGreeting() {
  const { data: me, isLoading } = useMe()
  const [line] = useState(() => (chosenLine ??= pickOpeningLine()))
  const first = me?.name?.trim().split(/\s+/)[0]
  return (
    <h1
      className={cx(
        'text-center font-display text-3xl font-bold tracking-tight [text-wrap:balance] transition-opacity duration-300',
        // Held back until /auth/me answers, so the name never pops in late.
        isLoading ? 'opacity-0' : 'opacity-100',
      )}
    >
      {line}{first ? `, ${first}` : ''}?
    </h1>
  )
}

const TIPS = [
  { text: 'Create a new agent', prompt: 'I want to create a new voice agent.' },
  { text: 'Edit an existing agent', prompt: 'I want to edit one of my existing agents.' },
  { text: 'Which agent is live right now?', prompt: 'Which agent is live right now?' },
  { text: 'Teach an agent from a brochure', prompt: 'I want to teach one of my agents from a brochure.' },
  { text: 'Put an agent on your phone number', prompt: 'I want to put an agent on my phone number.' },
  { text: 'Undo a change to an agent', prompt: 'I want to undo a recent change to one of my agents.' },
]

const TIP_MS = 3800

/** Clicking a tip fills the composer instead of sending: this text moves, and
 *  a mis-click must not spend LLM quota shared with live calls. Hover and
 *  keyboard focus hold it still so it can actually be read and pressed. */
export function StudioTips({ onPick, paused = false }: {
  onPick: (prompt: string) => void
  paused?: boolean
}) {
  const [index, setIndex] = useState(0)
  const [held, setHeld] = useState(false)

  useEffect(() => {
    if (paused || held) return
    const t = window.setInterval(() => setIndex((i) => (i + 1) % TIPS.length), TIP_MS)
    return () => window.clearInterval(t)
  }, [paused, held])

  const tip = TIPS[index]
  return (
    <div
      className="mt-4 flex h-6 items-center justify-center overflow-hidden opacity-75 transition-opacity duration-200 hover:opacity-100 focus-within:opacity-100"
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={() => setHeld(false)}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.button
          key={tip.text}
          type="button"
          onClick={() => onPick(tip.prompt)}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
          className="rounded-md px-2 text-sm text-soft"
        >
          {tip.text}
        </motion.button>
      </AnimatePresence>
    </div>
  )
}
