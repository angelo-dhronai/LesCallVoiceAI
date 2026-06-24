import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { NAV } from '../lib/nav'
import { useHealth } from '../api/hooks/useDashboard'
import { cx } from '../components/ui'
import type { ReactNode } from 'react'

function titleFor(path: string) {
  for (const g of NAV) for (const i of g.items) if (i.to === path) return i.label
  return 'Overview'
}

export function AppShell({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const { pathname } = useLocation()
  const { services, overall, liveCount } = useHealth()

  return (
    <div className="min-h-screen bg-[var(--bg)] bg-mesh-light dark:bg-mesh-dark">
      <Sidebar collapsed={collapsed} liveCount={liveCount} />
      <div className={cx('transition-all duration-300', collapsed ? 'lg:pl-[92px]' : 'lg:pl-[260px]')}>
        <div className="mx-auto max-w-[1600px] px-4 pb-12 lg:px-6">
          <Topbar title={titleFor(pathname)} onToggleSidebar={() => setCollapsed((c) => !c)} services={services} overall={overall} />
          <main className="animate-fade-up">{children}</main>
        </div>
      </div>
    </div>
  )
}
