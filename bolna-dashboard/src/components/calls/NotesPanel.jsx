import { useState, useRef, useEffect } from 'react'
import { Sparkles, Check, Send } from 'lucide-react'

const ACTION_TAGS = ['Escalate', 'Resolved', 'Follow-up needed']

function NotesPanel() {
  const [draft, setDraft] = useState('')
  const [notes, setNotes] = useState([])
  const [noteCounter, setNoteCounter] = useState(0)
  const [activeTags, setActiveTags] = useState({})
  const [pushed, setPushed] = useState(false)
  const toastTimer = useRef(null)

  // Clear the CRM toast timer on unmount.
  useEffect(() => () => clearTimeout(toastTimer.current), [])

  function saveNote() {
    const text = draft.trim()
    if (!text) return // ignore empty saves
    const n = noteCounter + 1
    setNotes((prev) => [...prev, { text, label: `Note #${n}`, id: n }])
    setNoteCounter(n)
    setDraft('')
  }

  function toggleTag(tag) {
    setActiveTags((prev) => ({ ...prev, [tag]: !prev[tag] }))
  }

  function pushToCrm() {
    setPushed(true)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setPushed(false), 2500)
  }

  return (
    <div className="card flex flex-col gap-4 p-4">
      {/* AI Summary — distinct light-blue treatment */}
      <div className="rounded-lg border border-accent/20 bg-accent-soft p-3.5 dark:border-accent/20 dark:bg-accent/10">
        <div className="flex items-center gap-1.5">
          <Sparkles size={15} className="text-accent dark:text-blue-300" />
          <span className="text-xs font-semibold uppercase tracking-wider text-accent dark:text-blue-300">
            AI Summary
          </span>
        </div>
        <p className="mt-2 text-sm leading-relaxed text-gray-700 dark:text-gray-300">
          Caller reached out about a billing concern and requested a refund on a recent order. The
          agent verified the account, confirmed eligibility, and processed the request. Sentiment
          recovered to positive by the end — no escalation required.
        </p>
      </div>

      {/* Editable note */}
      <div>
        <span className="section-label">Add a note</span>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          placeholder="Write an internal note about this call…"
          className="input mt-2 resize-none"
        />
        <div className="mt-2 flex justify-end">
          <button onClick={saveNote} className="btn-outline">
            Save note
          </button>
        </div>

        {notes.length > 0 && (
          <ul className="mt-3 space-y-2">
            {notes.map((note) => (
              <li
                key={note.id}
                className="rounded-lg border border-gray-200 bg-gray-50 p-2.5 text-sm text-gray-700 dark:border-white/10 dark:bg-white/5 dark:text-gray-300"
              >
                <div className="mb-0.5 text-[11px] font-medium text-gray-400 dark:text-gray-500">
                  {note.label}
                </div>
                {note.text}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Action tags */}
      <div>
        <span className="section-label">Tags</span>
        <div className="mt-2 flex flex-wrap gap-2">
          {ACTION_TAGS.map((tag) => {
            const active = !!activeTags[tag]
            return (
              <button
                key={tag}
                onClick={() => toggleTag(tag)}
                className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                  active
                    ? 'bg-accent text-white'
                    : 'border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-white/10 dark:text-gray-300 dark:hover:bg-white/5'
                }`}
              >
                {tag}
              </button>
            )
          })}
        </div>
      </div>

      {/* Push to CRM */}
      <div>
        <button onClick={pushToCrm} className="btn-primary w-full justify-center">
          <Send size={15} />
          Push to CRM
        </button>
        {pushed && (
          <div className="mt-2 flex items-center justify-center gap-1.5 rounded-lg bg-emerald-50 py-2 text-sm font-medium text-emerald-700 animate-fade-in dark:bg-emerald-500/10 dark:text-emerald-400">
            <Check size={15} />
            Pushed to CRM
          </div>
        )}
      </div>
    </div>
  )
}

export default NotesPanel
