import { useEffect, useState } from 'react'
import { Search, Bell, Wallet, Plus, HelpCircle, Sun, Moon, PanelLeft } from 'lucide-react'
import { useTheme } from '../theme/ThemeProvider'
import { HealthPill, type Service } from '../components/HealthPill'
import { Button, cx } from '../components/ui'
import { clockTime } from '../lib/format'

export function Topbar({ title, onToggleSidebar, services, overall }: {
  title: string; onToggleSidebar: () => void; services: Service[]; overall: 'healthy' | 'degraded' | 'down'
}) {
  const { theme, toggle } = useTheme()
  const [now, setNow] = useState(clockTime())
  useEffect(() => { const t = setInterval(() => setNow(clockTime()), 1000); return () => clearInterval(t) }, [])
  return (
    <header className="sticky top-0 z-20 -mx-1 mb-5 px-1 pt-3">
      <div className="glass flex items-center gap-3 rounded-2xl px-3 py-2.5">
        <button onClick={onToggleSidebar} className="grid h-9 w-9 place-items-center rounded-xl text-soft hover:bg-[var(--surface-2)]">
          <PanelLeft size={18} />
        </button>
        <h1 className="font-display text-[15px] font-bold tracking-tight">{title}</h1>
        <HealthPill services={services} overall={overall} />
        <div className="relative ml-2 hidden flex-1 md:block">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
          <input placeholder="Search calls, contacts, recordings…  ( / )"
            className="w-full max-w-md rounded-xl bg-[var(--surface-2)] py-2 pl-9 pr-3 text-[13px] text-[var(--text)] outline-none ring-1 ring-[var(--border)] transition placeholder:text-dim focus:ring-mint-400/40" />
        </div>
        <div className="ml-auto flex items-center gap-2">
          <div className="hidden items-center gap-1.5 rounded-xl bg-amber-400/10 px-2.5 py-1.5 text-[12px] font-bold text-amber-600 ring-1 ring-amber-400/25 sm:flex dark:text-amber-300">
            <Wallet size={14} /> $5.00
          </div>
          <Button variant="soft" size="sm" className="hidden sm:inline-flex"><Plus size={14} /> Add funds</Button>
          <div className="hidden text-right leading-tight md:block">
            <div className="text-[12px] font-semibold tabular-nums">{now}</div>
            <div className="text-[10px] text-dim">Live</div>
          </div>
          <button className="relative grid h-9 w-9 place-items-center rounded-xl text-soft hover:bg-[var(--surface-2)]">
            <Bell size={17} /><span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-rose-500" />
          </button>
          <button className="hidden h-9 w-9 place-items-center rounded-xl text-soft hover:bg-[var(--surface-2)] sm:grid"><HelpCircle size={17} /></button>
          <button onClick={toggle} className="grid h-9 w-9 place-items-center rounded-xl text-soft hover:bg-[var(--surface-2)]">
            {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </div>
      </div>
    </header>
  )
}
