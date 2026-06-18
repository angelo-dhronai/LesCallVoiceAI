import { useEffect, useRef, useState } from 'react'
import { liveSocket } from '../api.js'

export default function Live() {
  const [lines, setLines] = useState([])
  const [connected, setConnected] = useState(false)
  const boxRef = useRef(null)

  useEffect(() => {
    const ws = liveSocket((m) => {
      if (m.type === 'status') setConnected(true)
      if (m.type === 'log') setLines((prev) => [...prev.slice(-400), m.line])
    })
    ws.onclose = () => setConnected(false)
    return () => ws.close()
  }, [])

  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [lines])

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Live Call Monitor</h1>
        <span className={`badge ${connected ? 'bg-green-100 text-green-700' : 'bg-slate-200 text-slate-500'}`}>
          {connected ? 'connected' : 'disconnected'}
        </span>
      </div>
      <p className="text-sm text-slate-500 mb-3">
        Live transcript &amp; stage events stream from the running pipeline. Inbound and
        outbound calls with the active agent appear here in real time.
      </p>
      <div ref={boxRef} className="card font-mono text-xs h-[65vh] overflow-auto bg-slate-900 text-green-300">
        {lines.length === 0 && <div className="text-slate-500">Waiting for call activity…</div>}
        {lines.map((l, i) => <div key={i} className="whitespace-pre-wrap">{l}</div>)}
      </div>
    </div>
  )
}
