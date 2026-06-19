import { useState, useEffect } from 'react'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { api } from '../../api.js'
import { turnsToBubbles, fakeWaveform } from '../../data/adapters.js'
import AnalyticsPanel from './AnalyticsPanel.jsx'
import AudioPlayer from './AudioPlayer.jsx'
import TranscriptPanel from './TranscriptPanel.jsx'
import NotesPanel from './NotesPanel.jsx'

// Cockpit: KPIs across the top, then transcript (left) + audio & notes (right).
// Transcript and AI summary are loaded live for this tenant's call.
export default function CallDetailView({ call, onBack }) {
  const [bubbles, setBubbles] = useState([])
  const [summary, setSummary] = useState(call.summary || '')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    api.transcript(call.id)
      .then((r) => { if (alive) setBubbles(turnsToBubbles(r.turns)) })
      .catch(() => { if (alive) setBubbles([]) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [call.id])

  const waveform = fakeWaveform(call.duration || 60)

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="btn btn-ghost !px-2"><ArrowLeft className="h-[18px] w-[18px]" /></button>
        <div className="min-w-0">
          <h1 className="text-lg font-bold text-slate-900 dark:text-white truncate">{call.agent}</h1>
          <div className="text-xs text-slate-400 font-mono truncate">{call.id} · {call.date} · {call.source}</div>
        </div>
        <span className={`badge ml-auto ${call.status === 'Failed'
          ? 'bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400'
          : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400'}`}>{call.status}</span>
      </div>

      <AnalyticsPanel call={call} turnCount={bubbles.length} />

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-stretch">
        <div className="min-h-0 h-[640px]">
          {loading
            ? <div className="card-flat h-full grid place-items-center text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>
            : <TranscriptPanel bubbles={bubbles} />}
        </div>
        <div className="flex flex-col gap-5 h-[640px] min-h-0">
          <AudioPlayer durationSeconds={call.duration || 60} waveform={waveform} hasAudio />
          <div className="flex-1 min-h-0">
            <NotesPanel connId={call.id} summary={summary} onSummary={setSummary} />
          </div>
        </div>
      </div>
    </div>
  )
}
