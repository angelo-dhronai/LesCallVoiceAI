import { Play, Cpu, PhoneOff } from 'lucide-react'

const NODE_W = 168
const NODE_H = 72

const STYLES = {
  START: { ring: 'border-emerald-400 dark:border-emerald-500/60', dot: 'bg-emerald-500', icon: Play, tag: 'Entry' },
  LLM: { ring: 'border-accent/50', dot: 'bg-accent', icon: Cpu, tag: 'LLM node' },
  CLOSING: { ring: 'border-rose-300 dark:border-rose-500/50', dot: 'bg-rose-400', icon: PhoneOff, tag: 'Exit' },
}

export { NODE_W, NODE_H }

export default function FlowNode({ node, selected, onSelect }) {
  const s = STYLES[node.type]
  const Icon = s.icon
  return (
    <button
      onClick={() => onSelect(node.id)}
      style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
      className={`absolute rounded-2xl border-2 bg-white dark:bg-slate-900 px-3.5 text-left
        flex items-center gap-3 transition-all hover:shadow-pop
        ${s.ring} ${selected ? 'ring-2 ring-accent ring-offset-2 ring-offset-slate-50 dark:ring-offset-slate-950 shadow-pop' : ''}`}
    >
      <span className={`h-8 w-8 shrink-0 rounded-lg grid place-items-center text-white ${s.dot}`}>
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-slate-800 dark:text-slate-100 truncate">{node.label}</span>
        <span className="block text-[10px] uppercase tracking-wider text-slate-400">{s.tag}</span>
      </span>
    </button>
  )
}
