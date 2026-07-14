import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { LogOut, Search } from 'lucide-react'
import { NAV } from '../lib/nav'
import { cx } from '../components/ui'
import { useMe } from '../api/hooks/useMe'

export function Sidebar({ collapsed, liveCount, mobileOpen, onCloseMobile }: {
  collapsed: boolean; liveCount: number; mobileOpen?: boolean; onCloseMobile?: () => void
}) {
  const { data: me } = useMe()
  const [filter, setFilter] = useState('')   // mobile-drawer quick filter
  const isAdmin = me?.is_admin !== false   // default-show until identity resolves
  const needle = filter.trim().toLowerCase()
  const groups = NAV
    .filter((g) => !g.admin || isAdmin)
    .map((g) => ({
      ...g,
      items: g.items.filter((it) => (!it.admin || isAdmin) && (!needle || it.label.toLowerCase().includes(needle))),
    }))
    .filter((g) => g.items.length > 0)
  const email = me?.email || ''
  const displayName = me?.name || (email ? email.split('@')[0] : 'Account')
  const initial = (me?.name || email || 'A').trim().charAt(0).toUpperCase() || 'A'

  // Escape closes the mobile drawer.
  useEffect(() => {
    if (!mobileOpen) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onCloseMobile?.() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [mobileOpen, onCloseMobile])

  return (
    <>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-zinc-950/30 lg:hidden" onClick={onCloseMobile} aria-hidden="true" />
      )}
      <aside
        aria-label="Main navigation"
        className={cx(
          'fixed inset-y-0 left-0 z-50 flex flex-col border-r border-[var(--border)] bg-[var(--surface-solid)] transition-all duration-300',
          collapsed ? 'lg:w-16' : 'lg:w-[248px]',
          // Mobile: off-canvas drawer; desktop: docked full-height rail.
          'w-[264px]',
          mobileOpen ? 'translate-x-0 shadow-glass-lg' : '-translate-x-full',
          'lg:translate-x-0 lg:shadow-none',
        )}>
        {/* Brand */}
        <div className={cx('flex h-14 shrink-0 items-center border-b border-[var(--border)] px-4', collapsed && 'lg:justify-center lg:px-0')}>
          <img
            src="/logo.png"
            alt="LesCall — Call Intelligence"
            className={cx('w-auto object-contain', collapsed ? 'h-7' : 'h-8')}
          />
        </div>

        {/* Touch users have no "/" shortcut — give the drawer its own quick filter. */}
        <div className="relative mt-3 px-3 lg:hidden">
          <Search size={14} className="pointer-events-none absolute left-6 top-1/2 -translate-y-1/2 text-dim" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Find a panel…"
            aria-label="Find a panel"
            className="w-full rounded-lg bg-[var(--surface-2)] py-2 pl-9 pr-3 text-[13px] text-[var(--text)] outline-none ring-1 ring-[var(--border)] transition placeholder:text-dim focus:ring-mint-500/40"
          />
        </div>

        <nav className="mt-3 flex-1 space-y-4 overflow-y-auto px-3 pb-2">
          {groups.map((group, gi) => (
            <div key={group.title}>
              <div className={cx('eyebrow px-2 pb-1.5', collapsed && 'lg:hidden')}>{group.title}</div>
              {collapsed && gi > 0 && <div className="mx-2 mb-2 hidden border-t border-[var(--border)] lg:block" />}
              <div className="space-y-px">
                {group.items.map((item) => (
                  <NavLink key={item.to} to={item.to} end={item.to === '/'}
                    title={collapsed ? item.label : undefined}
                    className={({ isActive }) => cx(
                      'group relative flex items-center gap-2.5 rounded-lg px-2 py-[7px] text-[13px] font-medium transition-colors',
                      collapsed && 'lg:justify-center lg:px-0',
                      isActive
                        ? 'bg-[var(--surface-2)] text-[var(--text)]'
                        : 'text-soft hover:bg-[var(--surface-2)] hover:text-[var(--text)]',
                    )}>
                    {({ isActive }) => (
                      <>
                        {isActive && <span className="absolute left-0 h-4 w-0.5 rounded-full bg-mint-500" />}
                        <span className="relative shrink-0">
                          <item.icon size={16} strokeWidth={2} className={cx('shrink-0', isActive ? 'text-mint-600 dark:text-mint-400' : 'text-dim group-hover:text-soft')} />
                          {/* Collapsed rail still shows the live-call count as a dot badge */}
                          {collapsed && item.badge && liveCount > 0 && (
                            <span className="absolute -right-1.5 -top-1.5 hidden h-3.5 min-w-3.5 items-center justify-center rounded-full bg-rose-500 px-0.5 text-[8px] font-bold text-white lg:inline-flex">{liveCount}</span>
                          )}
                        </span>
                        <span className={cx('truncate', collapsed && 'lg:hidden')}>{item.label}</span>
                        {item.badge && liveCount > 0 && (
                          <span className={cx('relative ml-auto inline-flex', collapsed && 'lg:hidden')}>
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-40" />
                            <span className="relative inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white">{liveCount}</span>
                          </span>
                        )}
                      </>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
          {/* Scroll cue: hints that more nav items exist below on short viewports */}
          <div aria-hidden="true" className="pointer-events-none sticky bottom-0 -mt-6 h-6 bg-gradient-to-t from-[var(--surface-solid)] to-transparent" />
        </nav>

        {/* Identity */}
        <div className={cx('flex shrink-0 items-center gap-2.5 border-t border-[var(--border)] px-3 py-3', collapsed && 'lg:flex-col lg:gap-1.5 lg:px-1.5')}
          title={collapsed ? `${displayName}${email ? ` — ${email}` : ''}` : undefined}>
          {me?.picture ? (
            <img src={me.picture} alt="" referrerPolicy="no-referrer"
              className="h-8 w-8 shrink-0 rounded-full object-cover ring-1 ring-[var(--border)]" />
          ) : (
            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-indigo-600 text-[12px] font-semibold text-white">{initial}</div>
          )}
          <div className={cx('min-w-0 flex-1 leading-tight', collapsed && 'lg:hidden')}>
            <div className="truncate text-[12.5px] font-medium">
              {displayName}{isAdmin && me?.mode === 'multi-tenant' && <span className="ml-1.5 rounded bg-mint-500/10 px-1 py-px text-[9px] font-semibold uppercase tracking-wide text-mint-700 dark:text-mint-400">admin</span>}
            </div>
            <div className="truncate text-[11px] text-dim">{email || 'signed in'}</div>
          </div>
          {me?.mode === 'multi-tenant' && (
            // Stays visible in the collapsed rail — signing out must never require expanding the sidebar.
            <a href="/auth/logout" title="Sign out" aria-label="Sign out"
              className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-dim transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]">
              <LogOut size={14} />
            </a>
          )}
        </div>
      </aside>
    </>
  )
}
