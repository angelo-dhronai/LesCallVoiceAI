import { useState, useEffect } from 'react'
import Sidebar from './Sidebar.jsx'
import TopHeader from './TopHeader.jsx'

// Persistent frame: collapsible sidebar rail + sticky header + scrollable body.
export default function AppShell({ view, onNavigate, crumbs, children }) {
  const [collapsed, setCollapsed] = useState(false)

  // Auto-collapse to an icon rail at tablet width.
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1024px)')
    const apply = () => setCollapsed(mq.matches)
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [])

  return (
    <div className="flex min-h-screen bg-slate-50 dark:bg-slate-950">
      <Sidebar view={view} onNavigate={onNavigate} collapsed={collapsed} />
      <div className="flex-1 min-w-0 flex flex-col">
        <TopHeader crumbs={crumbs} collapsed={collapsed} onToggleSidebar={() => setCollapsed((c) => !c)} />
        <main className="flex-1 min-h-0 overflow-y-auto scroll-thin">{children}</main>
      </div>
    </div>
  )
}
