import { useState } from 'react'
import {
  ChevronRight, Bell, HelpCircle, Sun, Moon, Wallet, PanelLeft, Search, X,
} from 'lucide-react'
import { useTheme } from '../../App.jsx'
import { wallet, notifications } from '../../data/mockData.js'

function AddFundsModal({ onClose }) {
  const [amount, setAmount] = useState(25)
  const [done, setDone] = useState(false)
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="card w-full max-w-md shadow-flyout" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">Add funds</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X className="h-5 w-5" /></button>
        </div>
        {done ? (
          <div className="py-8 text-center">
            <div className="mx-auto mb-3 h-12 w-12 rounded-full bg-emerald-100 dark:bg-emerald-500/15 grid place-items-center">
              <Wallet className="h-6 w-6 text-emerald-600" />
            </div>
            <p className="text-sm text-slate-600 dark:text-slate-300">Added <b>${amount}.00</b> to your wallet.</p>
          </div>
        ) : (
          <>
            <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">Choose an amount to top up your balance.</p>
            <div className="grid grid-cols-3 gap-2 mb-5">
              {[10, 25, 100].map((a) => (
                <button
                  key={a}
                  onClick={() => setAmount(a)}
                  className={`rounded-xl border py-3 text-sm font-semibold transition
                    ${amount === a
                      ? 'border-accent bg-accent-soft text-accent dark:bg-accent/15'
                      : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-slate-300'}`}
                >
                  ${a}
                </button>
              ))}
            </div>
            <button className="btn btn-primary w-full" onClick={() => setDone(true)}>Confirm ${amount}.00</button>
          </>
        )}
      </div>
    </div>
  )
}

function Popover({ children, onClose }) {
  return (
    <>
      <div className="fixed inset-0 z-30" onClick={onClose} />
      <div className="absolute right-0 top-full mt-2 w-80 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl shadow-flyout z-40 overflow-hidden">
        {children}
      </div>
    </>
  )
}

export default function TopHeader({ crumbs, collapsed, onToggleSidebar }) {
  const { dark, toggle } = useTheme()
  const [funds, setFunds] = useState(false)
  const [bell, setBell] = useState(false)
  const [help, setHelp] = useState(false)
  const unread = notifications.filter((n) => n.unread).length
  const low = wallet.balance < 10

  return (
    <header className="sticky top-0 z-20 h-16 flex items-center gap-3 px-4 sm:px-6 bg-white/80 dark:bg-slate-900/80 backdrop-blur border-b border-slate-200 dark:border-slate-800">
      <button onClick={onToggleSidebar} className="btn-ghost btn !px-2" title="Toggle sidebar">
        <PanelLeft className="h-[18px] w-[18px]" />
      </button>

      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-sm min-w-0">
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1.5 min-w-0">
            {i > 0 && <ChevronRight className="h-3.5 w-3.5 text-slate-300 shrink-0" />}
            <span className={`truncate ${i === crumbs.length - 1
              ? 'font-semibold text-slate-800 dark:text-slate-100'
              : 'text-slate-400'}`}>{c}</span>
          </span>
        ))}
      </nav>

      <div className="flex-1" />

      {/* Wallet */}
      <div className={`hidden sm:flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm font-semibold
        ${low ? 'border-amber-300 bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:border-amber-500/40 dark:text-amber-400'
              : 'border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-200'}`}>
        <Wallet className="h-4 w-4" />
        ${wallet.balance.toFixed(2)}
      </div>
      <button className="btn btn-primary hidden sm:inline-flex" onClick={() => setFunds(true)}>Add funds</button>

      {/* Bell */}
      <div className="relative">
        <button onClick={() => { setBell((o) => !o); setHelp(false) }} className="btn btn-ghost !px-2 relative" title="Notifications">
          <Bell className="h-[18px] w-[18px]" />
          {unread > 0 && (
            <span className="absolute -top-1 -right-1 h-4 min-w-4 px-1 rounded-full bg-accent text-white text-[10px] font-bold grid place-items-center">
              {unread}
            </span>
          )}
        </button>
        {bell && (
          <Popover onClose={() => setBell(false)}>
            <div className="px-4 py-3 border-b border-slate-100 dark:border-slate-700 text-sm font-semibold text-slate-800 dark:text-slate-100">Notifications</div>
            <div className="max-h-80 overflow-y-auto scroll-thin">
              {notifications.map((n) => (
                <div key={n.id} className="px-4 py-3 border-b border-slate-50 dark:border-slate-700/50 hover:bg-slate-50 dark:hover:bg-slate-700/40">
                  <div className="flex items-start gap-2">
                    {n.unread && <span className="mt-1.5 h-2 w-2 rounded-full bg-accent shrink-0" />}
                    <div className={n.unread ? '' : 'pl-4'}>
                      <div className="text-sm font-medium text-slate-800 dark:text-slate-100">{n.title}</div>
                      <div className="text-xs text-slate-500 dark:text-slate-400">{n.body}</div>
                      <div className="text-[10px] text-slate-400 mt-0.5">{n.time}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Popover>
        )}
      </div>

      {/* Help */}
      <div className="relative">
        <button onClick={() => { setHelp((o) => !o); setBell(false) }} className="btn btn-ghost !px-2" title="Help">
          <HelpCircle className="h-[18px] w-[18px]" />
        </button>
        {help && (
          <Popover onClose={() => setHelp(false)}>
            <div className="p-3">
              <div className="flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-700 px-3 py-2">
                <Search className="h-4 w-4 text-slate-400" />
                <input autoFocus placeholder="Search help & docs…" className="flex-1 bg-transparent text-sm outline-none text-slate-700 dark:text-slate-200" />
              </div>
              <div className="mt-2 text-xs text-slate-400 px-1">Popular</div>
              {['Connecting a SIP trunk', 'Building a graph agent', 'Reading call analytics'].map((t) => (
                <button key={t} className="w-full text-left rounded-lg px-3 py-2 text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700">{t}</button>
              ))}
            </div>
          </Popover>
        )}
      </div>

      {/* Theme */}
      <button onClick={toggle} className="btn btn-ghost !px-2" title="Toggle theme">
        {dark ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
      </button>

      {funds && <AddFundsModal onClose={() => setFunds(false)} />}
    </header>
  )
}
