import { useMemo, useRef, useState, useEffect } from 'react'
import { Play, Pause, Volume2, Download } from 'lucide-react'
import { formatDuration } from '../../data/mockData.js'

const BAR_COUNT = 60

function AudioPlayer({ durationSeconds = 0 }) {
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [volume, setVolume] = useState(70)
  const trackRef = useRef(null)

  // Deterministic waveform amplitudes (NO Math.random) — stable across renders.
  const bars = useMemo(() => {
    return Array.from({ length: BAR_COUNT }, (_, i) => {
      const a = Math.abs(Math.sin(i * 0.45))
      const b = Math.abs(Math.sin(i * 0.17 + 1.3))
      const amp = 0.25 + 0.75 * (0.55 * a + 0.45 * b)
      return Math.min(1, amp)
    })
  }, [])

  // Playback timer: advances currentTime while playing; cleared on pause/unmount.
  useEffect(() => {
    if (!playing) return
    const timer = setInterval(() => {
      setCurrentTime((t) => {
        const next = t + 0.1
        if (next >= durationSeconds) {
          setPlaying(false)
          return durationSeconds
        }
        return next
      })
    }, 100)
    return () => clearInterval(timer) // cleanup playback timer
  }, [playing, durationSeconds])

  const progress = durationSeconds > 0 ? currentTime / durationSeconds : 0

  function togglePlay() {
    if (!playing && currentTime >= durationSeconds) setCurrentTime(0)
    setPlaying((p) => !p)
  }

  function handleSeek(e) {
    const el = trackRef.current
    if (!el || durationSeconds <= 0) return
    const rect = el.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    setCurrentTime(ratio * durationSeconds)
  }

  return (
    <div className="card p-4">
      <span className="section-label">Recording</span>

      {/* Waveform track (clickable / seekable) */}
      <div
        ref={trackRef}
        onClick={handleSeek}
        className="mt-3 flex h-20 cursor-pointer items-center gap-[2px]"
      >
        {bars.map((amp, i) => {
          const played = i / BAR_COUNT <= progress
          return (
            <div
              key={i}
              className={`flex-1 rounded-full transition-colors ${
                played
                  ? 'bg-accent'
                  : 'bg-gray-200 dark:bg-white/10'
              }`}
              style={{ height: `${Math.round(amp * 100)}%` }}
            />
          )
        })}
      </div>

      {/* Controls */}
      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={togglePlay}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent text-white hover:bg-accent-hover transition-colors"
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing ? <Pause size={18} /> : <Play size={18} className="ml-0.5" />}
        </button>

        <span className="font-mono text-xs tabular-nums text-gray-600 dark:text-gray-400">
          {formatDuration(Math.floor(currentTime))} / {formatDuration(durationSeconds)}
        </span>

        <div className="ml-auto flex items-center gap-2">
          <Volume2 size={16} className="text-gray-400 dark:text-gray-500" />
          <input
            type="range"
            min="0"
            max="100"
            value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
            className="h-1 w-20 cursor-pointer accent-accent"
            aria-label="Volume"
          />
          <button
            className="btn-ghost px-2 py-2"
            aria-label="Download recording"
          >
            <Download size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}

export default AudioPlayer
