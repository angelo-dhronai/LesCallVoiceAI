import { Bot, Flag, PhoneOff, FunctionSquare } from 'lucide-react'

// Per-type visual treatment. START=green, LLM=blue/accent, CLOSING=gray-red.
const TYPE_STYLES = {
  START: {
    border: 'border-l-emerald-500',
    dot: 'bg-emerald-500',
    tag: 'text-emerald-700 dark:text-emerald-400',
    Icon: Flag,
  },
  LLM: {
    border: 'border-l-accent',
    dot: 'bg-accent',
    tag: 'text-accent dark:text-blue-300',
    Icon: Bot,
  },
  CLOSING: {
    border: 'border-l-red-400 dark:border-l-red-500',
    dot: 'bg-gray-400 dark:bg-red-400',
    tag: 'text-gray-500 dark:text-gray-400',
    Icon: PhoneOff,
  },
}

function FlowNode({ node, selected, onClick }) {
  const style = TYPE_STYLES[node.type] || TYPE_STYLES.LLM
  const { Icon } = style

  return (
    <button
      type="button"
      onClick={() => onClick(node.id)}
      className={`card w-52 text-left border-l-4 ${style.border} p-3 cursor-pointer
        transition-all duration-150 hover:-translate-y-0.5 hover:shadow-lg
        hover:border-gray-300 dark:hover:border-white/20
        ${selected ? 'ring-2 ring-accent shadow-lg' : 'shadow-sm'}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className={`flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider ${style.tag}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
          {node.type}
        </div>
        <Icon className={`h-3.5 w-3.5 ${style.tag}`} />
      </div>

      <div className="mt-1.5 text-sm font-bold text-gray-900 dark:text-white truncate">
        {node.label}
      </div>

      <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400 truncate">
        {node.prompt}
      </p>

      {node.connectedFunctionCall && (
        <div className="mt-2 inline-flex items-center gap-1 rounded-md bg-gray-100 dark:bg-white/5
          px-1.5 py-0.5 text-[10px] font-mono text-gray-600 dark:text-gray-300">
          <FunctionSquare className="h-3 w-3" />
          {node.connectedFunctionCall}
        </div>
      )}
    </button>
  )
}

export default FlowNode
