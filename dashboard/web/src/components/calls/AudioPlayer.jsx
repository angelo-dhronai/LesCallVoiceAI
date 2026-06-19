import { useState, useEffect, useRef } from 'react'
import { Play, Pause, Download, Volume2 } from 'lucide-react'

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

// Real playback of the recorded WAV via a hidden <audio> element. The waveform
// strip is a visual envelope (no real decode) that doubles as a seek bar driven
// by the element's actual currentTime/duration.
export default function AudioPlayer({ src, downloadUrl, durationSeconds = 60, waveform = [], hasAudio = true }) {
  const audioRef = useRef(null)
  const barRef = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [pos, setPos] = useState(0)
  const [dur, setDur] = useState(durationSeconds)
  const [volume, setVolume] = useState(0.8)
  const [error, setError] = useState(false)

  // Reset when the source changes (navigating between calls).
  useEffect(() => {
    setPlaying(false); setPos(0); setDur(durationSeconds); setError(false)
  }, [src, durationSeconds])

  useEffect(() => { if (audioRef.current) audioRef.current.volume = volume }, [volume])

  const onLoaded = () => {
    const d = audioRef.current?.duration
    if (d && isFinite(d) && d > 0) setDur(d)
  }

  const toggle = () => {
    const el = audioRef.current
    if (!el || !hasAudio) return
    if (el.paused) { el.play().then(() => setPlaying(true)).catch(() => setError(true)) }
    else { el.pause(); setPlaying(false) }
  }

  const seek = (e) => {
    if (!audioRef.current || !hasAudio) return
    const rect = barRef.current.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    audioRef.current.currentTime = ratio * (dur || 0)
    setPos(audioRef.current.currentTime)
  }

  const progress = dur ? pos / dur : 0
  const playedBars = Math.round(progress * waveform.length)
  const disabled = !hasAudio || error

  return (
    <div className="card-flat p-5">
      <div className="section-label mb-3">Recording</div>

      <audio
        ref={audioRef}
        src={hasAudio ? src : undefined}
        preload="none"
        onLoadedMetadata={onLoaded}
        onTimeUpdate={() => setPos(audioRef.current?.currentTime || 0)}
        onEnded={() => setPlaying(false)}
        onError={() => { if (hasAudio) setError(true) }}
      />

      <div ref={barRef} onClick={seek} className="relative flex items-center gap-[2px] h-20 cursor-pointer select-none">
        {waveform.map((amp, i) => (
          <div key={i} style={{ height: `${20 + amp * 80}%` }}
            className={`flex-1 rounded-full transition-colors ${i <= playedBars ? 'bg-accent' : 'bg-slate-200 dark:bg-slate-700'}`} />
        ))}
        <div className="absolute top-0 bottom-0 w-0.5 bg-accent-dark" style={{ left: `${progress * 100}%` }} />
      </div>

      <div className="mt-4 flex items-center gap-4">
        <button onClick={toggle} disabled={disabled}
          className="h-11 w-11 shrink-0 rounded-full bg-accent text-white grid place-items-center hover:bg-accent-dark transition disabled:opacity-40">
          {playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5 ml-0.5" />}
        </button>
        <span className="text-sm font-medium text-slate-600 dark:text-slate-300 tabular-nums">
          {fmt(pos)} <span className="text-slate-400">/ {fmt(dur)}</span>
        </span>
        {error && <span className="text-xs text-rose-500">Audio unavailable</span>}
        <div className="flex items-center gap-2 ml-auto">
          <Volume2 className="h-4 w-4 text-slate-400" />
          <input type="range" min="0" max="1" step="0.05" value={volume} onChange={(e) => setVolume(+e.target.value)} className="w-24 accent-accent" />
        </div>
        <a href={hasAudio ? downloadUrl : undefined} download
          className={`btn btn-ghost !px-2 ${disabled ? 'pointer-events-none opacity-40' : ''}`} title="Download recording">
          <Download className="h-[18px] w-[18px]" />
        </a>
      </div>
    </div>
  )
}
