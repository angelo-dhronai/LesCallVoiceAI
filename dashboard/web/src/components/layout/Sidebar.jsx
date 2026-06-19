import { useState } from 'react'
import {
  LayoutDashboard, Bot, Workflow, PhoneCall, Hash, BookOpen, Layers, FileBarChart,
  BarChart3, ChevronDown, Settings, LogOut, Check,
} from 'lucide-react'
import { useAuth, deriveIdentity } from '../../auth.jsx'

const NAV = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'agents', label: 'Agent Setup', icon: Bot },
  { id: 'flow', label: 'Graph Agent', icon: Workflow },
  { id: 'calls', label: 'Call History', icon: PhoneCall },
  { id: 'numbers', label: 'My Numbers', icon: Hash },
  { id: 'knowledge', label: 'Knowledge Base', icon: BookOpen },
  { id: 'batches', label: 'Campaigns', icon: Layers },
  { id: 'reports', label: 'Reports', icon: FileBarChart },
  { id: 'analytics', label: 'Analytics', icon: BarChart3 },
]

export default function Sidebar({ view, onNavigate, collapsed }) {
  const user = useAuth()
  const me = deriveIdentity(user)
  // Workspaces are derived from the signed-in identity (real tenant + a personal one).
  const workspaces = [
    { id: 'ws_team', name: me.workspace, plan: 'Team' },
    { id: 'ws_personal', name: 'Personal', plan: 'Free' },
  ]
  const [wsOpen, setWsOpen] = useState(false)
  const [userOpen, setUserOpen] = useState(false)
  const [activeWs, setActiveWs] = useState(workspaces[0])

  return (
    <aside
      className={`${collapsed ? 'w-[68px]' : 'w-60'} shrink-0 h-screen sticky top-0
        flex flex-col bg-white border-r border-slate-200
        dark:bg-slate-900 dark:border-slate-800 transition-[width] duration-200`}
    >
      {/* Brand logo (white chip so the wordmark reads in dark mode too) */}
      <div className="h-16 flex items-center px-2 border-b border-slate-100 dark:border-slate-800">
        <div className="bg-white rounded-lg px-2 py-1 flex items-center">
          <img src="/brand/logo.png" alt="LesCall"
            className={collapsed ? 'h-8 w-auto' : 'h-11 w-auto'} />
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto scroll-thin px-2 py-3 space-y-0.5">
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = view === id
          return (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              title={collapsed ? label : undefined}
              className={`group w-full flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium
                border-l-2 transition-colors
                ${active
                  ? 'bg-accent-soft text-accent border-accent dark:bg-accent/15 dark:text-accent-light'
                  : 'text-slate-600 border-transparent hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'}`}
            >
              <Icon className={`h-[18px] w-[18px] shrink-0 ${active ? '' : 'group-hover:text-slate-700 dark:group-hover:text-slate-200'}`} />
              {!collapsed && <span className="truncate">{label}</span>}
            </button>
          )
        })}
      </nav>

      {/* Workspace + user */}
      <div className="border-t border-slate-100 dark:border-slate-800 p-2 space-y-1">
        <div className="relative">
          <button
            onClick={() => setWsOpen((o) => !o)}
            className="w-full flex items-center gap-2.5 rounded-xl px-2.5 py-2 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            <div className="h-7 w-7 rounded-lg bg-brand/10 text-brand dark:text-brand-light grid place-items-center text-xs font-bold">
              {activeWs.name[0]}
            </div>
            {!collapsed && (
              <>
                <div className="flex-1 text-left leading-tight">
                  <div className="text-sm font-semibold text-slate-800 dark:text-slate-100 truncate">{activeWs.name}</div>
                  <div className="text-[10px] text-slate-400">{activeWs.plan} workspace</div>
                </div>
                <ChevronDown className="h-4 w-4 text-slate-400" />
              </>
            )}
          </button>
          {wsOpen && !collapsed && (
            <div className="absolute bottom-full mb-1 left-0 right-0 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-pop p-1 z-20">
              {workspaces.map((ws) => (
                <button
                  key={ws.id}
                  onClick={() => { setActiveWs(ws); setWsOpen(false) }}
                  className="w-full flex items-center justify-between rounded-lg px-2.5 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-700"
                >
                  <span className="text-slate-700 dark:text-slate-200">{ws.name}</span>
                  {ws.id === activeWs.id && <Check className="h-4 w-4 text-accent" />}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="relative">
          <button
            onClick={() => setUserOpen((o) => !o)}
            className="w-full flex items-center gap-2.5 rounded-xl px-2.5 py-2 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            {me.picture
              ? <img src={me.picture} alt="" className="h-7 w-7 rounded-full object-cover" referrerPolicy="no-referrer" />
              : <div className="h-7 w-7 rounded-full bg-accent text-white grid place-items-center text-xs font-bold">{me.initials}</div>}
            {!collapsed && (
              <div className="flex-1 text-left text-xs text-slate-500 dark:text-slate-400 truncate">
                {me.email}
              </div>
            )}
          </button>
          {userOpen && !collapsed && (
            <div className="absolute bottom-full mb-1 left-0 right-0 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-pop p-1 z-20">
              <button className="w-full flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700">
                <Settings className="h-4 w-4" /> Settings
              </button>
              <a href="/api/auth/logout" className="w-full flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-500/10">
                <LogOut className="h-4 w-4" /> Logout
              </a>
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}
