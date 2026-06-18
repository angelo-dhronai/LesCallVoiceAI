import { useState, useRef, useEffect } from 'react'
import {
  ChevronRight,
  Plus,
  Bell,
  HelpCircle,
  Sun,
  Moon,
  Search,
  X,
  BookOpen,
  MessageCircle,
} from 'lucide-react'
import { notifications, walletBalance } from '../../data/mockData.js'

const PRESET_AMOUNTS = [10, 25, 100]

function TopHeader({ breadcrumb = [], dark, onToggleDark }) {
  const [fundsOpen, setFundsOpen] = useState(false)
  const [selectedAmount, setSelectedAmount] = useState(25)
  const [notifOpen, setNotifOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)

  const notifRef = useRef(null)
  const helpRef = useRef(null)

  const unreadCount = notifications.filter((n) => n.unread).length
  const lowBalance = walletBalance < 10

  useEffect(() => {
    function handleClick(e) {
      if (notifRef.current && !notifRef.current.contains(e.target)) setNotifOpen(false)
      if (helpRef.current && !helpRef.current.contains(e.target)) setHelpOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-gray-200 bg-white/80 px-4 backdrop-blur-md dark:border-white/10 dark:bg-[#0b0d12]/80 sm:px-6">
      {/* Breadcrumb */}
      <nav className="flex min-w-0 items-center gap-1.5 text-sm" aria-label="Breadcrumb">
        {breadcrumb.map((crumb, i) => {
          const last = i === breadcrumb.length - 1
          return (
            <span key={`${crumb}-${i}`} className="flex min-w-0 items-center gap-1.5">
              {i > 0 && <ChevronRight className="h-4 w-4 shrink-0 text-gray-300 dark:text-gray-600" />}
              <span
                className={`truncate ${
                  last ? 'font-semibold text-gray-900 dark:text-white' : 'text-gray-500 dark:text-gray-400'
                }`}
              >
                {crumb}
              </span>
            </span>
          )
        })}
      </nav>

      {/* Right cluster */}
      <div className="flex items-center gap-2 sm:gap-3">
        {/* (1) Wallet pill */}
        <div
          className={`hidden items-center rounded-full border px-3 py-1.5 text-sm font-semibold sm:inline-flex ${
            lowBalance
              ? 'border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-400'
              : 'border-gray-200 bg-gray-50 text-gray-700 dark:border-white/10 dark:bg-white/5 dark:text-gray-200'
          }`}
          title={lowBalance ? 'Low balance — add funds' : 'Wallet balance'}
        >
          ${walletBalance.toFixed(2)}
        </div>

        {/* (2) Add funds */}
        <button type="button" className="btn-primary" onClick={() => setFundsOpen(true)}>
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">Add funds</span>
        </button>

        {/* (3) Notifications */}
        <div ref={notifRef} className="relative">
          <button
            type="button"
            onClick={() => setNotifOpen((v) => !v)}
            aria-label="Notifications"
            className="relative rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-gray-200"
          >
            <Bell className="h-5 w-5" />
            {unreadCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">
                {unreadCount}
              </span>
            )}
          </button>

          {notifOpen && (
            <div className="absolute right-0 mt-2 w-80 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-lg dark:border-white/10 dark:bg-[#13161d]">
              <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-white/10">
                <span className="text-sm font-semibold text-gray-900 dark:text-white">Notifications</span>
                {unreadCount > 0 && <span className="badge-blue">{unreadCount} new</span>}
              </div>
              <ul className="max-h-80 overflow-y-auto">
                {notifications.map((n) => (
                  <li
                    key={n.id}
                    className="flex gap-3 border-b border-gray-100 px-4 py-3 last:border-0 dark:border-white/5"
                  >
                    <span className="mt-1.5 flex h-2 w-2 shrink-0">
                      {n.unread ? (
                        <span className="h-2 w-2 rounded-full bg-accent" />
                      ) : (
                        <span className="h-2 w-2 rounded-full bg-transparent" />
                      )}
                    </span>
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-900 dark:text-white">{n.title}</div>
                      <div className="text-xs text-gray-500 dark:text-gray-400">{n.body}</div>
                      <div className="mt-0.5 text-[11px] text-gray-400 dark:text-gray-500">{n.time}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* (4) Help */}
        <div ref={helpRef} className="relative">
          <button
            type="button"
            onClick={() => setHelpOpen((v) => !v)}
            aria-label="Help"
            className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-gray-200"
          >
            <HelpCircle className="h-5 w-5" />
          </button>

          {helpOpen && (
            <div className="absolute right-0 mt-2 w-72 rounded-xl border border-gray-200 bg-white p-3 shadow-lg dark:border-white/10 dark:bg-[#13161d]">
              <div className="relative mb-3">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                <input type="text" placeholder="Search help…" className="input pl-9" />
              </div>
              <div className="space-y-0.5">
                <a
                  href="#"
                  className="flex items-center gap-2 rounded-lg px-2 py-2 text-sm text-gray-700 transition-colors hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-white/5"
                >
                  <BookOpen className="h-4 w-4 text-gray-400" />
                  Getting started guide
                </a>
                <a
                  href="#"
                  className="flex items-center gap-2 rounded-lg px-2 py-2 text-sm text-gray-700 transition-colors hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-white/5"
                >
                  <MessageCircle className="h-4 w-4 text-gray-400" />
                  Contact support
                </a>
              </div>
            </div>
          )}
        </div>

        {/* (5) Dark mode toggle */}
        <button
          type="button"
          onClick={onToggleDark}
          aria-label="Toggle dark mode"
          className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-gray-200"
        >
          {dark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
        </button>
      </div>

      {/* Add funds modal */}
      {fundsOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            aria-label="Close modal"
            onClick={() => setFundsOpen(false)}
            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
          />
          <div className="relative z-10 w-full max-w-md rounded-2xl border border-gray-200 bg-white p-6 shadow-lg dark:border-white/10 dark:bg-[#13161d]">
            <div className="mb-4 flex items-start justify-between">
              <div>
                <h2 className="page-title">Add funds</h2>
                <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                  Top up your wallet to keep your agents running.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setFundsOpen(false)}
                aria-label="Close"
                className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/5 dark:hover:text-gray-200"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="section-label mb-2">Select amount</div>
            <div className="grid grid-cols-3 gap-2">
              {PRESET_AMOUNTS.map((amt) => {
                const active = selectedAmount === amt
                return (
                  <button
                    key={amt}
                    type="button"
                    onClick={() => setSelectedAmount(amt)}
                    className={`rounded-xl border px-3 py-4 text-lg font-bold transition-colors ${
                      active
                        ? 'border-accent bg-accent-soft text-accent dark:bg-accent/15 dark:text-blue-300'
                        : 'border-gray-200 text-gray-700 hover:border-gray-300 hover:bg-gray-50 dark:border-white/10 dark:text-gray-200 dark:hover:bg-white/5'
                    }`}
                  >
                    ${amt}
                  </button>
                )
              })}
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button type="button" className="btn-outline" onClick={() => setFundsOpen(false)}>
                Cancel
              </button>
              <button type="button" className="btn-primary" onClick={() => setFundsOpen(false)}>
                Confirm ${selectedAmount}
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  )
}

export default TopHeader
