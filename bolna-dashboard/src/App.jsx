import { useState } from 'react'
import { Construction } from 'lucide-react'
import AppShell from './components/layout/AppShell.jsx'
import AgentConfigView from './components/agent/AgentConfigView.jsx'
import FlowCanvas from './components/flow/FlowCanvas.jsx'
import CallHistoryTable from './components/tables/CallHistoryTable.jsx'
import CallDetailView from './components/calls/CallDetailView.jsx'
import KnowledgeBaseGrid from './components/tables/KnowledgeBaseGrid.jsx'

// Human-readable label per nav key (used for breadcrumbs + "coming soon").
const NAV_LABELS = {
  agent: 'Agent Setup',
  flow: 'Graph Agent',
  calls: 'Call History',
  numbers: 'My Numbers',
  sip: 'SIP Trunks',
  knowledge: 'Knowledge Base',
  batches: 'Batches',
  reports: 'Reports',
  analytics: 'Analytics',
  developers: 'Developers',
  providers: 'Providers',
  docs: 'Documentation',
}

function ComingSoon({ label }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-24">
      <div className="w-14 h-14 rounded-2xl bg-accent-soft dark:bg-accent/15 grid place-items-center mb-4">
        <Construction className="w-7 h-7 text-accent" />
      </div>
      <h2 className="page-title mb-1">{label}</h2>
      <p className="text-sm text-gray-500 dark:text-gray-400 max-w-sm">
        This section is coming soon. The Agent Setup, Graph Agent, Call History, and
        Knowledge Base views are fully built — explore those from the sidebar.
      </p>
    </div>
  )
}

export default function App() {
  const [current, setCurrent] = useState('agent')
  const [dark, setDark] = useState(false)
  const [openCallId, setOpenCallId] = useState(null) // drill-in to Call Detail (View 2)

  const navigate = (key) => {
    setCurrent(key)
    setOpenCallId(null) // leaving/entering a section resets any open call
  }

  // Breadcrumb reflects the current view (and drill-in state).
  let breadcrumb
  if (current === 'calls' && openCallId) breadcrumb = ['Call History', openCallId]
  else if (current === 'flow') breadcrumb = ['Graph Agents', 'Inbound Support Agent']
  else breadcrumb = [NAV_LABELS[current] || 'Dashboard']

  let view
  if (current === 'agent') view = <AgentConfigView />
  else if (current === 'flow') view = <FlowCanvas />
  else if (current === 'knowledge') view = <KnowledgeBaseGrid />
  else if (current === 'calls') {
    view = openCallId ? (
      <CallDetailView executionId={openCallId} onBack={() => setOpenCallId(null)} />
    ) : (
      <CallHistoryTable onOpenCall={setOpenCallId} />
    )
  } else view = <ComingSoon label={NAV_LABELS[current]} />

  return (
    <div className={dark ? 'dark' : ''}>
      <AppShell
        current={current}
        onNavigate={navigate}
        breadcrumb={breadcrumb}
        dark={dark}
        onToggleDark={() => setDark((d) => !d)}
      >
        {view}
      </AppShell>
    </div>
  )
}
