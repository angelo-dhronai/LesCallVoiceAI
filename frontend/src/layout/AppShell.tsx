import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { NAV } from '../lib/nav'
import { useHealth } from '../api/hooks/useDashboard'
import { cx } from '../components/ui'
import type { ReactNode } from 'react'

// Longest-prefix match so sub-routes (/build/agents/new, /build/agents/:name)
// inherit their parent panel's title instead of falling back to "Overview".
function titleFor(path: string) {
  let best = ''
  let label = 'Overview'
  for (const g of NAV) for (const i of g.items) {
    if (path === i.to || (i.to !== '/' && path.startsWith(i.to + '/'))) {
      if (i.to.length > best.length) { best = i.to; label = i.label }
    }
  }
  return label
}

export function AppShell({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const { pathname } = useLocation()
  const { services, overall, liveCount } = useHealth()

  // Navigating closes the mobile drawer.
  useEffect(() => { setMobileOpen(false) }, [pathname])

  // Tab title tracks the active panel (helps users with several tabs open).
  useEffect(() => { document.title = `${titleFor(pathname)} · LesCall` }, [pathname])

  return (
    <div className="min-h-screen bg-[var(--bg)]">
      <Sidebar collapsed={collapsed} liveCount={liveCount} mobileOpen={mobileOpen} onCloseMobile={() => setMobileOpen(false)} />
      <div className={cx('transition-all duration-300', collapsed ? 'lg:pl-16' : 'lg:pl-[248px]')}>
        {/* Full-bleed: panels use the whole browser width (user request) */}
        <div className="px-4 pb-12 sm:px-6 lg:px-8">
          <Topbar
            title={titleFor(pathname)}
            onToggleSidebar={() => setCollapsed((c) => !c)}
            onOpenMobileNav={() => setMobileOpen(true)}
            services={services}
            overall={overall}
          />
          {/* key remounts main per route so the entrance animation replays */}
          <main key={pathname} className="animate-fade-up">{children}</main>
        </div>
      </div>
    </div>
  )
}
