import { useMemo, useState } from 'react'
import { Search, Bot } from 'lucide-react'

function AgentList({ agents, selectedId, onSelect }) {
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return agents
    return agents.filter((a) => a.name.toLowerCase().includes(q))
  }, [agents, query])

  return (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search agents…"
            className="input pl-9"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {filtered.length === 0 ? (
          <p className="px-2 py-6 text-center text-sm text-gray-400 dark:text-gray-500">
            No agents match “{query}”.
          </p>
        ) : (
          filtered.map((agent) => {
            const isSelected = agent.id === selectedId
            return (
              <button
                key={agent.id}
                type="button"
                onClick={() => onSelect(agent.id)}
                className={[
                  'group flex w-full items-center gap-3 rounded-lg border-l-2 px-3 py-2.5 text-left transition-colors',
                  isSelected
                    ? 'border-accent bg-accent-soft dark:bg-accent/15'
                    : 'border-transparent hover:bg-gray-100 dark:hover:bg-white/5',
                ].join(' ')}
              >
                <span
                  className={[
                    'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
                    isSelected
                      ? 'bg-accent/15 text-accent dark:text-blue-300'
                      : 'bg-gray-100 text-gray-400 dark:bg-white/5 dark:text-gray-500',
                  ].join(' ')}
                >
                  <Bot className="h-4 w-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div
                    className={[
                      'truncate text-sm font-medium',
                      isSelected
                        ? 'text-accent dark:text-blue-300'
                        : 'text-gray-800 dark:text-gray-100',
                    ].join(' ')}
                  >
                    {agent.name}
                  </div>
                  <div className="truncate text-xs text-gray-400 dark:text-gray-500">
                    {agent.language}
                  </div>
                </div>
                <span className={agent.status === 'active' ? 'badge-green' : 'badge-gray'}>
                  {agent.status === 'active' ? 'Active' : 'Draft'}
                </span>
              </button>
            )
          })
        )}
      </div>
    </div>
  )
}

export default AgentList
