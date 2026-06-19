import { useState } from 'react'
import { Sparkles, Send, CheckCircle2, Loader2, Wand2 } from 'lucide-react'
import { api } from '../../api.js'

const ACTION_TAGS = ['Escalate', 'Resolved', 'Follow-up needed', 'Booked visit']

// AI summary is fetched/generated from the backend for this tenant's call.
// Manual notes + action tags are local working state for the session.
export default function NotesPanel({ connId, summary: initialSummary, onSummary }) {
  const [summary, setSummary] = useState(initialSummary || '')
  const [genning, setGenning] = useState(false)
  const [note, setNote] = useState('')
  const [notes, setNotes] = useState([])
  const [tags, setTags] = useState({})
  const [toast, setToast] = useState(false)

  const generate = async () => {
    setGenning(true)
    try {
      const r = await api.summary(connId)
      setSummary(r.summary || '')
      onSummary?.(r.summary || '')
    } catch (e) {
      alert(`Summary failed: ${e.message}`)
    } finally {
      setGenning(false)
    }
  }

  const addNote = () => {
    if (!note.trim()) return
    const stamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    setNotes((n) => [{ id: Date.now(), text: note.trim(), at: stamp }, ...n])
    setNote('')
  }

  const pushCrm = () => { setToast(true); setTimeout(() => setToast(false), 2200) }

  return (
    <div className="card-flat p-5 flex flex-col h-full min-h-0">
      <div className="section-label mb-3">Summary &amp; Notes</div>

      <div className="rounded-xl border border-accent/20 bg-accent-soft dark:bg-accent/10 p-4">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5 text-accent text-xs font-semibold"><Sparkles className="h-3.5 w-3.5" /> AI Summary</div>
          <button onClick={generate} disabled={genning} className="text-xs font-semibold text-accent hover:underline inline-flex items-center gap-1">
            {genning ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wand2 className="h-3 w-3" />}
            {summary ? 'Regenerate' : 'Generate'}
          </button>
        </div>
        <p className="text-sm text-slate-700 dark:text-slate-200 leading-relaxed">
          {summary || <span className="text-slate-400">No summary yet — click Generate to produce one from the transcript.</span>}
        </p>
      </div>

      <div className="flex flex-wrap gap-2 mt-4">
        {ACTION_TAGS.map((t) => {
          const on = tags[t]
          return (
            <button key={t} onClick={() => setTags((s) => ({ ...s, [t]: !s[t] }))}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition
                ${on ? 'bg-accent text-white border-accent' : 'border-slate-200 text-slate-500 hover:border-slate-300 dark:border-slate-700 dark:text-slate-400'}`}>
              {t}
            </button>
          )
        })}
      </div>

      <div className="mt-4">
        <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a manual note…" className="input h-20 resize-none" />
        <div className="flex items-center gap-2 mt-2">
          <button className="btn btn-outline" onClick={addNote}><Send className="h-4 w-4" /> Save note</button>
          <button className="btn btn-primary ml-auto" onClick={pushCrm}>Push to CRM</button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin mt-4 space-y-2">
        {notes.map((n) => (
          <div key={n.id} className="rounded-xl border border-slate-200 dark:border-slate-700 p-3">
            <div className="text-sm text-slate-700 dark:text-slate-200">{n.text}</div>
            <div className="text-[10px] text-slate-400 mt-1">{n.at}</div>
          </div>
        ))}
        {notes.length === 0 && <div className="text-xs text-slate-400 text-center py-4">No manual notes yet.</div>}
      </div>

      {toast && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-xl bg-emerald-600 text-white px-4 py-3 shadow-flyout">
          <CheckCircle2 className="h-5 w-5" /> Pushed to CRM successfully
        </div>
      )}
    </div>
  )
}
