import { NavLink, Outlet } from 'react-router-dom'

const nav = [
  { to: '/', label: 'Overview', end: true },
  { to: '/agents', label: 'Agents & Pitch' },
  { to: '/campaigns', label: 'Outbound Dialer' },
  { to: '/calls', label: 'Calls & Transcripts' },
  { to: '/numbers', label: 'Numbers & Trunks' },
  { to: '/live', label: 'Live' },
]

export default function Layout() {
  return (
    <div className="min-h-screen flex">
      <aside className="w-60 bg-slate-900 text-slate-200 flex flex-col">
        <div className="px-5 py-5 text-xl font-bold text-white">lescall</div>
        <nav className="flex-1 px-3 space-y-1">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `block px-3 py-2 rounded-lg text-sm ${
                  isActive ? 'bg-brand text-white' : 'hover:bg-slate-800'
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="px-5 py-4 text-xs text-slate-500">Voice AI · Pramukh Group</div>
      </aside>
      <main className="flex-1 p-8 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
