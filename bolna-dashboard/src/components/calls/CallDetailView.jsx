import { ArrowLeft, PhoneIncoming, PhoneOutgoing, Layers } from 'lucide-react'
import { callHistory, analyticsForCall, formatDuration } from '../../data/mockData.js'
import AnalyticsPanel from './AnalyticsPanel.jsx'
import AudioPlayer from './AudioPlayer.jsx'
import TranscriptPanel from './TranscriptPanel.jsx'
import NotesPanel from './NotesPanel.jsx'

const triggerMeta = {
  inbound: { label: 'Inbound', icon: PhoneIncoming, badge: 'badge-blue' },
  outbound: { label: 'Outbound', icon: PhoneOutgoing, badge: 'badge-green' },
  batch: { label: 'Batch', icon: Layers, badge: 'badge-amber' },
}

const statusBadge = {
  success: 'badge-green',
  failed: 'badge-red',
  live: 'badge-blue',
}

function CallDetailView({ executionId, onBack }) {
  const call = callHistory.find((c) => c.executionId === executionId)
  const analytics = analyticsForCall[executionId] || analyticsForCall.default

  if (!call) {
    return (
      <div className="p-6">
        <button onClick={onBack} className="btn-ghost">
          <ArrowLeft size={16} />
          Back
        </button>
        <p className="mt-6 text-sm text-gray-500 dark:text-gray-400">Call not found.</p>
      </div>
    )
  }

  const trigger = triggerMeta[call.triggerSource] || triggerMeta.inbound
  const TriggerIcon = trigger.icon

  return (
    <div className="flex flex-col gap-4 p-4 sm:p-6">
      {/* Top row: back + call meta */}
      <div className="flex flex-wrap items-center gap-3">
        <button onClick={onBack} className="btn-ghost">
          <ArrowLeft size={16} />
          Back
        </button>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="page-title">{call.agentName}</h1>
          <span className="font-mono text-xs text-gray-400 dark:text-gray-500">
            {call.executionId}
          </span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className={trigger.badge}>
            <TriggerIcon size={12} />
            {trigger.label}
          </span>
          <span className="badge-gray font-mono tabular-nums">
            {formatDuration(call.durationSeconds)}
          </span>
          <span className={statusBadge[call.status] || 'badge-gray'}>
            {call.status === 'live' && (
              <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse-dot" />
            )}
            {call.status}
          </span>
        </div>
      </div>

      {/* Analytics across the top */}
      <AnalyticsPanel analytics={analytics} />

      {/* Cockpit body: left column (player + notes) / right column (transcript) */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <div className="flex flex-col gap-4 lg:col-span-2">
          <AudioPlayer durationSeconds={call.durationSeconds} />
          <NotesPanel />
        </div>
        <div className="lg:col-span-3">
          <TranscriptPanel transcript={call.transcript} />
        </div>
      </div>
    </div>
  )
}

export default CallDetailView
