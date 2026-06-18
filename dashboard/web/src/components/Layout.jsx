import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from '../auth.jsx'

const nav = [
  { to: '/', label: 'Overview', end: true },
  { to: '/agents', label: 'Agents & Pitch' },
  { to: '/campaigns', label: 'Outbound Dialer' },
  { to: '/calls', label: 'Calls & Transcripts' },
  { to: '/numbers', label: 'Numbers & Trunks' },
  { to: '/live', label: 'Live' },
]

export default function Layout() {
  const user = useAuth()
  const logout = async () => { try { await api.logout() } catch {} ; location.reload() }
  return (
    <div className="min-h-screen flex">
      <aside className="w-60 bg-brand-dark text-slate-200 flex flex-col">
        <div className="px-4 py-5">
          <div className="bg-white rounded-xl px-3 py-3 flex items-center justify-center">
            <img src="/brand/logo.png" alt="LesCall" className="h-9 w-auto" />
          </div>
        </div>
        <nav className="flex-1 px-3 space-y-1">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `block px-3 py-2 rounded-lg text-sm ${
                  isActive ? 'bg-accent text-white' : 'hover:bg-brand-light/40'
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-4 border-t border-brand-light/30">
          {user && (
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="text-sm text-slate-200 truncate">{user.name || user.email}</div>
                <div className="text-xs text-slate-500 truncate">{user.email}</div>
              </div>
              <button onClick={logout} className="text-xs text-slate-400 hover:text-white">Logout</button>
            </div>
          )}
        </div>
      </aside>
      <main className="flex-1 p-8 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
