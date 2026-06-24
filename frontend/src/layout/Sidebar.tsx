import { NavLink } from 'react-router-dom'
import { NAV } from '../lib/nav'
import { cx } from '../components/ui'

export function Sidebar({ collapsed, liveCount }: { collapsed: boolean; liveCount: number }) {
  return (
    <aside className={cx('fixed inset-y-3 left-3 z-40 hidden flex-col lg:flex transition-all duration-300', collapsed ? 'w-[76px]' : 'w-[244px]')}>
      <div className="glass flex h-full flex-col rounded-3xl p-3">
        <div className={cx('flex items-center px-1 py-1.5', collapsed ? 'justify-center' : 'px-2')}>
          <div className={cx('grid place-items-center rounded-2xl bg-white shadow-sm ring-1 ring-black/5', collapsed ? 'h-11 w-11 p-1.5' : 'w-full px-3 py-2.5')}>
            <img
              src="/logo.png"
              alt="LesCall — Call Intelligence"
              className={cx('w-auto object-contain', collapsed ? 'h-7' : 'h-12')}
            />
          </div>
        </div>
        <nav className="mt-2 flex-1 space-y-4 overflow-y-auto px-0.5 py-1">
          {NAV.map((group) => (
            <div key={group.title}>
              {!collapsed && <div className="px-3 pb-1.5 eyebrow">{group.title}</div>}
              <div className="space-y-0.5">
                {group.items.map((item) => (
                  <NavLink key={item.to} to={item.to} end={item.to === '/'}
                    className={({ isActive }) => cx(
                      'group relative flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] font-medium transition-all',
                      collapsed && 'justify-center px-0',
                      isActive
                        ? 'bg-gradient-to-r from-mint-400/20 to-indigo-500/10 text-[var(--text)] ring-1 ring-mint-400/25'
                        : 'text-soft hover:bg-[var(--surface-2)] hover:text-[var(--text)]',
                    )}>
                    {({ isActive }) => (
                      <>
                        {isActive && <span className="absolute left-0 h-5 w-1 rounded-r-full bg-gradient-to-b from-mint-400 to-indigo-500" />}
                        <item.icon size={18} className={cx('shrink-0', isActive ? 'text-mint-600 dark:text-mint-300' : '')} />
                        {!collapsed && <span className="truncate">{item.label}</span>}
                        {!collapsed && item.badge && liveCount > 0 && (
                          <span className="ml-auto inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-rose-500/90 px-1.5 text-[10px] font-bold text-white">{liveCount}</span>
                        )}
                      </>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className={cx('mt-2 flex items-center gap-2.5 rounded-2xl surface-2 p-2.5', collapsed && 'justify-center')}>
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-indigo-400 to-indigo-600 text-[12px] font-bold text-white">A</div>
          {!collapsed && (
            <div className="min-w-0 leading-tight">
              <div className="truncate text-[12px] font-semibold">Pramukh Group</div>
              <div className="truncate text-[10px] text-dim">tech@dhronai.com</div>
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}
