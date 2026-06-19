import { useState, useEffect, useRef } from 'react'
import { Play, Pause, Download, Volume2 } from 'lucide-react'

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

// Simulated playback over a fabricated waveform (no real audio decode):
// the playhead advances on a timer to convey play/seek state.
export default function AudioPlayer({ durationSeconds = 60, waveform = [], hasAudio = true }) {
  const [playing, setPlaying] = useState(false)
  const [pos, setPos] = useState(0)
  const [volume, setVolume] = useState(0.8)
  const barRef = useRef(null)

  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => {
      setPos((p) => {
        if (p >= durationSeconds) { setPlaying(false); return durationSeconds }
        return p + 0.25
      })
    }, 250)
    return () => clearInterval(id)
  }, [playing, durationSeconds])

  const progress = durationSeconds ? pos / durationSeconds : 0
  const playedBars = Math.round(progress * waveform.length)

  const seek = (e) => {
    const rect = barRef.current.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    setPos(ratio * durationSeconds)
  }

  return (
    <div className="card-flat p-5">
      <div className="section-label mb-3">Recording</div>
      <div ref={barRef} onClick={seek} className="relative flex items-center gap-[2px] h-20 cursor-pointer select-none">
        {waveform.map((amp, i) => (
          <div key={i} style={{ height: `${20 + amp * 80}%` }}
            className={`flex-1 rounded-full transition-colors ${i <= playedBars ? 'bg-accent' : 'bg-slate-200 dark:bg-slate-700'}`} />
        ))}
        <div className="absolute top-0 bottom-0 w-0.5 bg-accent-dark" style={{ left: `${progress * 100}%` }} />
      </div>
      <div className="mt-4 flex items-center gap-4">
        <button onClick={() => setPlaying((p) => !p)} disabled={!hasAudio}
          className="h-11 w-11 shrink-0 rounded-full bg-accent text-white grid place-items-center hover:bg-accent-dark transition disabled:opacity-40">
          {playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5 ml-0.5" />}
        </button>
        <span className="text-sm font-medium text-slate-600 dark:text-slate-300 tabular-nums">
          {fmt(pos)} <span className="text-slate-400">/ {fmt(durationSeconds)}</span>
        </span>
        <div className="flex items-center gap-2 ml-auto">
          <Volume2 className="h-4 w-4 text-slate-400" />
          <input type="range" min="0" max="1" step="0.05" value={volume} onChange={(e) => setVolume(+e.target.value)} className="w-24 accent-accent" />
        </div>
        <button className="btn btn-ghost !px-2" title="Download recording"><Download className="h-[18px] w-[18px]" /></button>
      </div>
    </div>
  )
}
