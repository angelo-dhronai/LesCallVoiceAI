import { useState } from 'react'
import { Search, Plus } from 'lucide-react'

export default function AgentList({ agents, selectedId, onSelect, onNew }) {
  const [q, setQ] = useState('')
  const filtered = agents.filter((a) => a.name.toLowerCase().includes(q.toLowerCase()))

  return (
    <div className="w-64 shrink-0 border-r border-slate-200 dark:border-slate-800 flex flex-col bg-white dark:bg-slate-900">
      <div className="p-3 border-b border-slate-100 dark:border-slate-800">
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-700 px-2.5 py-1.5">
          <Search className="h-4 w-4 text-slate-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search agents…"
            className="flex-1 bg-transparent text-sm outline-none text-slate-700 dark:text-slate-200"
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto scroll-thin p-2 space-y-1">
        {filtered.map((a) => {
          const active = a.id === selectedId
          return (
            <button
              key={a.id}
              onClick={() => onSelect(a.id)}
              className={`w-full text-left rounded-xl px-3 py-2.5 transition-colors border
                ${active
                  ? 'bg-accent-soft border-accent/30 dark:bg-accent/15 dark:border-accent/40'
                  : 'border-transparent hover:bg-slate-100 dark:hover:bg-slate-800'}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">{a.name}</span>
              </div>
              <span className={`badge mt-1.5 ${a.status === 'Active'
                ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400'
                : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${a.status === 'Active' ? 'bg-emerald-500' : 'bg-slate-400'}`} />
                {a.status}
              </span>
            </button>
          )
        })}
        {filtered.length === 0 && (
          <div className="text-center text-sm text-slate-400 py-8">No agents match.</div>
        )}
      </div>
      <div className="p-3 border-t border-slate-100 dark:border-slate-800">
        <button className="btn btn-outline w-full" onClick={onNew}><Plus className="h-4 w-4" /> New agent</button>
      </div>
    </div>
  )
}
