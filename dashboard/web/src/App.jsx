import { useState, useEffect, createContext, useContext } from 'react'
import AppShell from './components/layout/AppShell.jsx'
import Overview from './components/dashboard/Overview.jsx'
import AgentConfigView from './components/agent/AgentConfigView.jsx'
import FlowCanvas from './components/flow/FlowCanvas.jsx'
import CallHistoryTable from './components/tables/CallHistoryTable.jsx'
import KnowledgeBaseGrid from './components/tables/KnowledgeBaseGrid.jsx'
import CallDetailView from './components/calls/CallDetailView.jsx'
import { Construction } from 'lucide-react'

// Dark-mode context — toggled from the header, applied to <html class="dark">.
const ThemeCtx = createContext({ dark: false, toggle: () => {} })
export const useTheme = () => useContext(ThemeCtx)

// Crumbs per view drive the header breadcrumb trail.
export const VIEWS = {
  dashboard: { crumbs: ['Dashboard'] },
  agents: { crumbs: ['Agent Setup'] },
  flow: { crumbs: ['Graph Agent', 'Inbound Support Agent'] },
  calls: { crumbs: ['Call History'] },
  numbers: { crumbs: ['My Numbers'] },
  knowledge: { crumbs: ['Knowledge Base'] },
  batches: { crumbs: ['Batches'] },
  reports: { crumbs: ['Reports'] },
  analytics: { crumbs: ['Analytics'] },
}

function ComingSoon({ title }) {
  return (
    <div className="h-full grid place-items-center p-10">
      <div className="text-center max-w-sm">
        <div className="mx-auto mb-4 h-14 w-14 rounded-2xl bg-accent-soft dark:bg-accent/10 grid place-items-center">
          <Construction className="h-7 w-7 text-accent" />
        </div>
        <h2 className="text-lg font-semibold text-slate-800 dark:text-slate-100">{title}</h2>
        <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
          This area is coming soon. The full {title.toLowerCase()} experience is on the roadmap.
        </p>
      </div>
    </div>
  )
}

export default function App() {
  const [dark, setDark] = useState(false)
  const [view, setView] = useState('dashboard')
  // When set, the Call Detail cockpit takes over the main area.
  const [openCall, setOpenCall] = useState(null)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])

  const go = (next) => { setOpenCall(null); setView(next) }

  let body
  if (openCall) {
    body = <CallDetailView call={openCall} onBack={() => setOpenCall(null)} />
  } else if (view === 'dashboard') {
    body = <Overview onNavigate={go} onOpenCall={setOpenCall} />
  } else if (view === 'agents') {
    body = <AgentConfigView />
  } else if (view === 'flow') {
    body = <FlowCanvas />
  } else if (view === 'calls') {
    body = <CallHistoryTable onOpenCall={setOpenCall} />
  } else if (view === 'knowledge') {
    body = <KnowledgeBaseGrid />
  } else {
    body = <ComingSoon title={VIEWS[view]?.crumbs.at(-1) || 'Section'} />
  }

  const crumbs = openCall
    ? ['Call History', openCall.id]
    : VIEWS[view]?.crumbs || ['Lescall']

  return (
    <ThemeCtx.Provider value={{ dark, toggle: () => setDark((d) => !d) }}>
      <AppShell view={view} onNavigate={go} crumbs={crumbs}>
        {body}
      </AppShell>
    </ThemeCtx.Provider>
  )
}
