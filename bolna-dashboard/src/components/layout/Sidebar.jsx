import { useState, useRef, useEffect } from 'react'
import {
  Bot,
  GitBranch,
  Phone,
  Hash,
  Network,
  BookOpen,
  Layers,
  FileText,
  BarChart3,
  Code2,
  Plug,
  LifeBuoy,
  PanelLeftClose,
  PanelLeftOpen,
  ChevronDown,
  Check,
  Settings,
  LogOut,
} from 'lucide-react'
import { workspaces, currentUser } from '../../data/mockData.js'

const NAV_ITEMS = [
  { key: 'agent', label: 'Agent Setup', Icon: Bot },
  { key: 'flow', label: 'Graph Agent', Icon: GitBranch },
  { key: 'calls', label: 'Call History', Icon: Phone },
  { key: 'numbers', label: 'My Numbers', Icon: Hash },
  { key: 'sip', label: 'SIP Trunks', Icon: Network },
  { key: 'knowledge', label: 'Knowledge Base', Icon: BookOpen },
  { key: 'batches', label: 'Batches', Icon: Layers },
  { key: 'reports', label: 'Reports', Icon: FileText },
  { key: 'analytics', label: 'Analytics', Icon: BarChart3 },
  { key: 'developers', label: 'Developers', Icon: Code2 },
  { key: 'providers', label: 'Providers', Icon: Plug },
  { key: 'docs', label: 'Documentation', Icon: LifeBuoy },
]

function Sidebar({ current, onNavigate, collapsed, onToggleCollapse }) {
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [activeWorkspace, setActiveWorkspace] = useState(workspaces[0])

  const workspaceRef = useRef(null)
  const profileRef = useRef(null)

  useEffect(() => {
    function handleClick(e) {
      if (workspaceRef.current && !workspaceRef.current.contains(e.target)) setWorkspaceOpen(false)
      if (profileRef.current && !profileRef.current.contains(e.target)) setProfileOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  return (
    <aside
      className={`flex h-screen flex-col border-r border-gray-200 bg-white dark:border-white/10 dark:bg-[#0f1218] ${
        collapsed ? 'w-16' : 'w-64'
      } shrink-0 transition-[width] duration-200`}
    >
      {/* Brand */}
      <div className={`flex h-16 items-center border-b border-gray-200 dark:border-white/10 ${collapsed ? 'justify-center px-0' : 'justify-between px-4'}`}>
        {collapsed ? (
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-sm font-bold text-white">
            B
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold tracking-tight text-gray-900 dark:text-white">Bolna.ai</span>
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-500 animate-pulse-dot" />
              <span className="inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
            <span className="text-[10px] font-semibold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">live</span>
          </div>
        )}
        {!collapsed && (
          <button
            type="button"
            onClick={onToggleCollapse}
            title="Collapse sidebar"
            aria-label="Collapse sidebar"
            className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/5 dark:hover:text-gray-200"
          >
            <PanelLeftClose className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Collapsed expand button */}
      {collapsed && (
        <div className="flex justify-center py-2">
          <button
            type="button"
            onClick={onToggleCollapse}
            title="Expand sidebar"
            aria-label="Expand sidebar"
            className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/5 dark:hover:text-gray-200"
          >
            <PanelLeftOpen className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        <ul className="space-y-0.5">
          {NAV_ITEMS.map(({ key, label, Icon }) => {
            const active = current === key
            return (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => onNavigate(key)}
                  title={collapsed ? label : undefined}
                  aria-current={active ? 'page' : undefined}
                  className={`flex w-full items-center gap-3 rounded-lg py-2 text-sm font-medium transition-colors ${
                    collapsed ? 'justify-center px-0' : 'px-3'
                  } ${
                    active
                      ? 'border-l-2 border-accent bg-accent-soft text-accent dark:bg-accent/15 dark:text-blue-300'
                      : 'border-l-2 border-transparent text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-white'
                  }`}
                >
                  <Icon className="h-[18px] w-[18px] shrink-0" />
                  {!collapsed && <span className="truncate">{label}</span>}
                </button>
              </li>
            )
          })}
        </ul>
      </nav>

      {/* Bottom: workspace + profile */}
      <div className="border-t border-gray-200 p-2 dark:border-white/10">
        {/* Workspace switcher */}
        <div ref={workspaceRef} className="relative">
          <button
            type="button"
            onClick={() => setWorkspaceOpen((v) => !v)}
            title={collapsed ? activeWorkspace.name : undefined}
            className={`flex w-full items-center gap-2 rounded-lg py-2 text-sm transition-colors hover:bg-gray-100 dark:hover:bg-white/5 ${
              collapsed ? 'justify-center px-0' : 'px-2'
            }`}
          >
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-gray-900 text-[11px] font-bold text-white dark:bg-white/10">
              {activeWorkspace.name.charAt(0)}
            </div>
            {!collapsed && (
              <>
                <div className="min-w-0 flex-1 text-left">
                  <div className="truncate font-medium text-gray-900 dark:text-white">{activeWorkspace.name}</div>
                  <div className="truncate text-xs text-gray-400">{activeWorkspace.plan}</div>
                </div>
                <ChevronDown className={`h-4 w-4 shrink-0 text-gray-400 transition-transform ${workspaceOpen ? 'rotate-180' : ''}`} />
              </>
            )}
          </button>

          {workspaceOpen && (
            <div className="absolute bottom-full left-0 mb-2 w-56 rounded-xl border border-gray-200 bg-white p-1 shadow-lg dark:border-white/10 dark:bg-[#13161d]">
              <div className="px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-gray-400">Switch workspace</div>
              {workspaces.map((ws) => {
                const isActive = ws.id === activeWorkspace.id
                return (
                  <button
                    key={ws.id}
                    type="button"
                    onClick={() => {
                      setActiveWorkspace(ws)
                      setWorkspaceOpen(false)
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors hover:bg-gray-100 dark:hover:bg-white/5"
                  >
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-gray-900 text-[11px] font-bold text-white dark:bg-white/10">
                      {ws.name.charAt(0)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium text-gray-900 dark:text-white">{ws.name}</div>
                      <div className="truncate text-xs text-gray-400">{ws.plan}</div>
                    </div>
                    {isActive && <Check className="h-4 w-4 shrink-0 text-accent" />}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Profile */}
        <div ref={profileRef} className="relative mt-1">
          <button
            type="button"
            onClick={() => setProfileOpen((v) => !v)}
            title={collapsed ? currentUser.email : undefined}
            className={`flex w-full items-center gap-2 rounded-lg py-2 text-sm transition-colors hover:bg-gray-100 dark:hover:bg-white/5 ${
              collapsed ? 'justify-center px-0' : 'px-2'
            }`}
          >
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] font-bold text-white">
              {currentUser.initials}
            </div>
            {!collapsed && (
              <>
                <div className="min-w-0 flex-1 text-left">
                  <div className="truncate text-xs text-gray-500 dark:text-gray-400">{currentUser.email}</div>
                </div>
                <ChevronDown className={`h-4 w-4 shrink-0 text-gray-400 transition-transform ${profileOpen ? 'rotate-180' : ''}`} />
              </>
            )}
          </button>

          {profileOpen && (
            <div className="absolute bottom-full left-0 mb-2 w-56 rounded-xl border border-gray-200 bg-white p-1 shadow-lg dark:border-white/10 dark:bg-[#13161d]">
              <button
                type="button"
                onClick={() => setProfileOpen(false)}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-gray-700 transition-colors hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-white/5"
              >
                <Settings className="h-4 w-4 text-gray-400" />
                Settings
              </button>
              <button
                type="button"
                onClick={() => setProfileOpen(false)}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10"
              >
                <LogOut className="h-4 w-4" />
                Logout
              </button>
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}

export default Sidebar
