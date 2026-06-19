import { Bot, User, MessageSquareOff } from 'lucide-react'

const TRANSCRIPT_KEYWORDS = ['refund', 'cancel', 'upgrade', 'complaint', 'budget', 'booking', 'visit', 'price']

const SENTIMENT = {
  positive: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400',
  neutral: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
  negative: 'bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400',
}

// Wrap any matched keyword in a subtly highlighted span.
function highlight(text) {
  const re = new RegExp(`\\b(${TRANSCRIPT_KEYWORDS.join('|')})\\b`, 'gi')
  return text.split(re).map((part, i) =>
    TRANSCRIPT_KEYWORDS.some((k) => k.toLowerCase() === part.toLowerCase())
      ? <span key={i} className="rounded px-1 bg-accent-soft text-accent font-medium dark:bg-accent/20 dark:text-accent-light">{part}</span>
      : <span key={i}>{part}</span>
  )
}

export default function TranscriptPanel({ bubbles = [] }) {
  return (
    <div className="card-flat p-5 flex flex-col h-full min-h-0">
      <div className="section-label mb-3">Transcript</div>
      {bubbles.length === 0 ? (
        <div className="flex-1 grid place-items-center text-center text-slate-400">
          <div><MessageSquareOff className="h-7 w-7 mx-auto mb-2 text-slate-300" />No transcript available for this call.</div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto scroll-thin space-y-3 pr-1">
          {bubbles.map((m) => {
            const agent = m.role === 'agent'
            return (
              <div key={m.id} className={`flex gap-2.5 ${agent ? '' : 'flex-row-reverse'}`}>
                <div className={`h-8 w-8 shrink-0 rounded-full grid place-items-center
                  ${agent ? 'bg-accent/15 text-accent' : 'bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-300'}`}>
                  {agent ? <Bot className="h-4 w-4" /> : <User className="h-4 w-4" />}
                </div>
                <div className={`max-w-[78%] ${agent ? '' : 'items-end'}`}>
                  <div className={`rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed
                    ${agent
                      ? 'bg-slate-50 dark:bg-slate-800 text-slate-700 dark:text-slate-200 rounded-tl-sm'
                      : 'bg-accent text-white rounded-tr-sm'}`}>
                    {agent ? highlight(m.text) : m.text}
                  </div>
                  <div className={`mt-1 flex items-center gap-2 text-[10px] text-slate-400 ${agent ? '' : 'justify-end'}`}>
                    {m.t && <span>{m.t}</span>}
                    <span className={`badge ${SENTIMENT[m.sentiment]}`}>{m.sentiment}</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
