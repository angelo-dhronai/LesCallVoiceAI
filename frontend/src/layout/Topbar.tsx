import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, Bell, Timer, HelpCircle, Sun, Moon, PanelLeft, Menu, CornerDownLeft, CheckCircle2, XCircle, Keyboard, MessageCircle } from 'lucide-react'
import { useTheme } from '../theme/ThemeProvider'
import { HealthPill, type Service } from '../components/HealthPill'
import { cx } from '../components/ui'
import { clockTime, num } from '../lib/format'
import { NAV } from '../lib/nav'
import { useMe } from '../api/hooks/useMe'
import { useGet } from '../api/hooks/useApi'

/** Flattened, admin-filtered list of navigable pages for the quick-jump search. */
function usePages() {
  const { data: me } = useMe()
  const isAdmin = me?.is_admin !== false
  return useMemo(() => {
    const out: { to: string; label: string; group: string; icon: any }[] = []
    for (const g of NAV) {
      if (g.admin && !isAdmin) continue
      for (const i of g.items) {
        if (i.admin && !isAdmin) continue
        out.push({ to: i.to, label: i.label, group: g.title, icon: i.icon })
      }
    }
    return out
  }, [isAdmin])
}

/** Close a popover on Escape. */
function useEscClose(open: boolean, close: () => void) {
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, close])
}

const IS_MAC = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform)

/** Quiet icon-only utility button (bell, help, theme…). */
function IconBtn({ onClick, label, className, expanded, children }: {
  onClick?: () => void; label: string; className?: string; expanded?: boolean; children: React.ReactNode
}) {
  return (
    <button onClick={onClick} aria-label={label} title={label}
      aria-haspopup={expanded !== undefined ? 'true' : undefined}
      aria-expanded={expanded}
      className={cx('grid h-8 w-8 place-items-center rounded-lg text-dim transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]', className)}>
      {children}
    </button>
  )
}

export function Topbar({ title, onToggleSidebar, onOpenMobileNav, services, overall }: {
  title: string; onToggleSidebar: () => void; onOpenMobileNav?: () => void
  services: Service[]; overall: 'healthy' | 'degraded' | 'down'
}) {
  const { theme, toggle } = useTheme()
  const navigate = useNavigate()
  const pages = usePages()
  const minutes = useGet<{ minutes_month?: number }>(['usage-minutes'], '/usage/minutes', { refetchInterval: 60000 })
  const [now, setNow] = useState(clockTime())
  const [q, setQ] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const [bellOpen, setBellOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { const t = setInterval(() => setNow(clockTime()), 1000); return () => clearInterval(t) }, [])
  useEscClose(bellOpen, () => setBellOpen(false))
  useEscClose(helpOpen, () => setHelpOpen(false))

  // "/" or Cmd/Ctrl+K focuses the quick-jump search from anywhere.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target as HTMLElement)?.isContentEditable
      if ((e.key === '/' && !typing) || (e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey))) {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return []
    return pages.filter((p) => p.label.toLowerCase().includes(needle) || p.group.toLowerCase().includes(needle)).slice(0, 7)
  }, [q, pages])

  // Empty query browses the full page list; typing narrows it.
  const visible = q.trim() ? matches : pages

  const go = (to: string) => {
    navigate(to)
    setQ(''); setSearchOpen(false); inputRef.current?.blur()
  }

  const downServices = services.filter((s) => !s.ok)

  const resultRow = (m: { to: string; label: string; group: string; icon: any }, i: number) => (
    <button key={m.to}
      onMouseDown={(e) => { e.preventDefault(); go(m.to) }}
      onMouseEnter={() => setHi(i)}
      className={cx('flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] transition-colors',
        i === hi ? 'bg-[var(--surface-2)] text-[var(--text)]' : 'text-soft')}>
      <m.icon size={15} className="shrink-0 text-dim" />
      <span className="font-medium">{m.label}</span>
      <span className="text-[11px] text-dim">{m.group}</span>
      {i === hi && <CornerDownLeft size={13} className="ml-auto shrink-0 text-dim" />}
    </button>
  )

  return (
    <header className="sticky top-0 z-20 -mx-4 mb-6 border-b border-[var(--border)] bg-[var(--surface-solid)] px-4 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
      <div className="flex h-14 items-center gap-3">
        {/* Mobile: open the nav drawer. Desktop: collapse the rail. */}
        <IconBtn onClick={onOpenMobileNav} label="Open navigation" className="lg:hidden"><Menu size={17} /></IconBtn>
        <IconBtn onClick={onToggleSidebar} label="Toggle sidebar" className="hidden lg:grid"><PanelLeft size={16} /></IconBtn>
        <h1 className="truncate text-[13.5px] font-semibold tracking-tight">{title}</h1>
        <HealthPill services={services} overall={overall} />

        {/* Quick-jump */}
        <div className="relative ml-2 hidden w-full max-w-sm md:block">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => { setQ(e.target.value); setSearchOpen(true); setHi(0) }}
            onFocus={() => { setSearchOpen(true); setHi(0) }}
            onBlur={() => setTimeout(() => setSearchOpen(false), 120)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { setQ(''); setSearchOpen(false); inputRef.current?.blur() }
              if (e.key === 'ArrowDown') { e.preventDefault(); setHi((i) => Math.min(i + 1, visible.length - 1)) }
              if (e.key === 'ArrowUp') { e.preventDefault(); setHi((i) => Math.max(i - 1, 0)) }
              if (e.key === 'Enter' && visible[hi]) go(visible[hi].to)
            }}
            placeholder="Jump to a panel…"
            aria-label="Jump to a panel"
            className="h-8 w-full rounded-lg bg-[var(--surface-2)] pl-9 pr-14 text-[13px] text-[var(--text)] outline-none ring-1 ring-transparent transition placeholder:text-dim focus:bg-[var(--surface-solid)] focus:ring-[var(--border-strong)]"
          />
          <kbd className="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 rounded border border-[var(--border)] bg-[var(--surface-solid)] px-1.5 py-0.5 text-[10px] font-medium text-dim lg:inline-flex">
            {IS_MAC ? '⌘K' : 'Ctrl K'}
          </kbd>
          {searchOpen && (
            <div className="card absolute left-0 top-10 z-40 max-h-72 w-full overflow-y-auto p-1.5 shadow-glass-lg animate-fade-up">
              {q.trim() ? (
                <>
                  {matches.length === 0 && (
                    <div className="px-3 py-2.5 text-[13px] text-dim">No panel matches “{q}”</div>
                  )}
                  {matches.map((m, i) => resultRow(m, i))}
                </>
              ) : (
                // Browse mode: the whole IA, grouped — a teaching moment on first focus.
                Array.from(new Set(pages.map((p) => p.group))).map((g) => (
                  <div key={g}>
                    <div className="eyebrow px-3 pb-1 pt-2">{g}</div>
                    {pages.filter((p) => p.group === g).map((m) => resultRow(m, pages.indexOf(m)))}
                  </div>
                ))
              )}
            </div>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          {/* Live call-minutes usage → Usage page */}
          <button
            onClick={() => navigate('/usage')}
            title="Call minutes used this month — open Usage"
            aria-label="Call minutes used this month — open Usage"
            className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-[12px] font-medium text-soft ring-1 ring-[var(--border)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
          >
            <Timer size={13} className="text-dim" />
            <span className="tabular-nums">
              {minutes.data?.minutes_month != null ? `${num(minutes.data.minutes_month)} min` : '— min'}
            </span>
            <span className="hidden text-dim sm:inline">this month</span>
          </button>
          <span className="hidden px-1.5 text-[12px] font-medium tabular-nums text-dim md:block" title="Live clock">{now}</span>

          <div className="relative">
            <IconBtn onClick={() => setBellOpen((o) => !o)} label="Notifications" expanded={bellOpen} className="relative">
              <Bell size={16} />
              {/* Dot only when something actually needs attention */}
              {downServices.length > 0 && <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-rose-500" />}
            </IconBtn>
            {bellOpen && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setBellOpen(false)} />
                <div className="card absolute right-0 z-40 mt-2 w-72 p-2 shadow-glass-lg animate-fade-up">
                  <div className="eyebrow px-2 pb-2 pt-1">Notifications</div>
                  {downServices.length === 0 ? (
                    <div className="flex items-center gap-2 rounded-lg px-2 py-2 text-[13px] text-soft">
                      <CheckCircle2 size={15} className="shrink-0 text-mint-600 dark:text-mint-400" />
                      All clear — every service is online.
                    </div>
                  ) : (
                    downServices.map((s) => (
                      <div key={s.name} className="flex items-center gap-2 rounded-lg px-2 py-2 text-[13px] text-soft">
                        <XCircle size={15} className="shrink-0 text-rose-500 dark:text-rose-300" />
                        <span><span className="font-medium">{s.name}</span> is down</span>
                      </div>
                    ))
                  )}
                </div>
              </>
            )}
          </div>

          <div className="relative">
            <IconBtn onClick={() => setHelpOpen((o) => !o)} label="Help" expanded={helpOpen} className="hidden sm:grid">
              <HelpCircle size={16} />
            </IconBtn>
            {helpOpen && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setHelpOpen(false)} />
                <div className="card absolute right-0 z-40 mt-2 w-72 p-2 shadow-glass-lg animate-fade-up">
                  <div className="eyebrow px-2 pb-2 pt-1">Help</div>
                  <div className="flex items-start gap-2 rounded-lg px-2 py-2 text-[13px] text-soft">
                    <MessageCircle size={15} className="mt-0.5 shrink-0 text-indigo-500 dark:text-indigo-300" />
                    <span>Ask the <button onMouseDown={() => { setHelpOpen(false); navigate('/') }} className="font-medium text-indigo-600 hover:underline dark:text-indigo-300">Copilot</button> anything — it can create and edit agents for you.</span>
                  </div>
                  <div className="flex items-start gap-2 rounded-lg px-2 py-2 text-[13px] text-soft">
                    <Keyboard size={15} className="mt-0.5 shrink-0 text-dim" />
                    <span>Press <kbd className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-1.5 py-0.5 text-[11px] font-medium">/</kbd> or <kbd className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-1.5 py-0.5 text-[11px] font-medium">{IS_MAC ? '⌘K' : 'Ctrl K'}</kbd> to jump to any panel.</span>
                  </div>
                  <div className="flex items-start gap-2 rounded-lg px-2 py-2 text-[13px] text-soft">
                    <HelpCircle size={15} className="mt-0.5 shrink-0 text-dim" />
                    <span>Need a human? Write to <a href="mailto:tech@dhronai.com" className="font-medium text-indigo-600 hover:underline dark:text-indigo-300">tech@dhronai.com</a>.</span>
                  </div>
                </div>
              </>
            )}
          </div>

          <IconBtn onClick={toggle} label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </IconBtn>
        </div>
      </div>
    </header>
  )
}
