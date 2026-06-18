import { MessagesSquare } from 'lucide-react'
import { formatDuration, highlightKeywords } from '../../data/mockData.js'

const sentimentBadge = {
  positive: 'badge-green',
  neutral: 'badge-gray',
  negative: 'badge-red',
}

// Split text and rewrap any highlightKeywords occurrences (case-insensitive) as styled spans.
function highlight(text) {
  if (!highlightKeywords.length) return text
  const escaped = highlightKeywords.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const re = new RegExp(`(${escaped.join('|')})`, 'gi')
  const parts = text.split(re)
  return parts.map((part, i) => {
    const isMatch = highlightKeywords.some((k) => k.toLowerCase() === part.toLowerCase())
    if (isMatch) {
      return (
        <span
          key={i}
          className="rounded px-1 bg-amber-100 text-amber-800 dark:bg-amber-400/15 dark:text-amber-300 font-medium"
        >
          {part}
        </span>
      )
    }
    return <span key={i}>{part}</span>
  })
}

function Bubble({ msg }) {
  const isAgent = msg.speaker === 'agent'
  return (
    <div className={`flex items-start gap-2 ${isAgent ? 'justify-start' : 'justify-end'}`}>
      {isAgent && (
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-white">
          A
        </div>
      )}
      <div className={`max-w-[78%] ${isAgent ? '' : 'order-1'}`}>
        <div
          className={`rounded-2xl px-3.5 py-2 text-sm ${
            isAgent
              ? 'bg-accent-soft text-gray-800 dark:bg-accent/15 dark:text-gray-100 rounded-tl-sm'
              : 'bg-gray-100 text-gray-800 dark:bg-white/5 dark:text-gray-100 rounded-tr-sm'
          }`}
        >
          {highlight(msg.text)}
        </div>
        <div className={`mt-1 flex items-center gap-2 ${isAgent ? '' : 'justify-end'}`}>
          <span className="font-mono text-[11px] tabular-nums text-gray-400 dark:text-gray-500">
            {formatDuration(msg.timestampSeconds)}
          </span>
          <span className={sentimentBadge[msg.sentiment] || 'badge-gray'}>{msg.sentiment}</span>
        </div>
      </div>
      {!isAgent && (
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-300 text-xs font-semibold text-gray-700 dark:bg-white/10 dark:text-gray-200">
          C
        </div>
      )}
    </div>
  )
}

function TranscriptPanel({ transcript }) {
  const hasTranscript = Array.isArray(transcript) && transcript.length > 0

  return (
    <div className="card flex h-full flex-col p-4">
      <span className="section-label">Transcript</span>

      {hasTranscript ? (
        <div className="mt-4 flex max-h-[520px] flex-col gap-4 overflow-auto pr-1">
          {transcript.map((msg, i) => (
            <Bubble key={i} msg={msg} />
          ))}
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center py-16 text-center">
          <MessagesSquare size={28} className="text-gray-300 dark:text-gray-600" />
          <p className="mt-3 text-sm text-gray-500 dark:text-gray-400">
            No transcript available for this call
          </p>
        </div>
      )}
    </div>
  )
}

export default TranscriptPanel
