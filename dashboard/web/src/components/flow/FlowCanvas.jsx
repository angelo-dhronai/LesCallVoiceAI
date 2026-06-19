import { useState } from 'react'
import FlowNode, { NODE_W, NODE_H } from './FlowNode.jsx'
import InspectorFlyout from './InspectorFlyout.jsx'
import { flowNodes } from '../../data/mockData.js'

export default function FlowCanvas() {
  const [selected, setSelected] = useState(null)
  const byId = Object.fromEntries(flowNodes.map((n) => [n.id, n]))

  // Edges connect the right edge of a source node to the left edge of its target.
  const edges = flowNodes.flatMap((n) =>
    n.transitions.map((t) => {
      const to = byId[t.to]
      return {
        key: `${n.id}-${t.to}`,
        x1: n.x + NODE_W, y1: n.y + NODE_H / 2,
        x2: to.x, y2: to.y + NODE_H / 2,
      }
    })
  )

  return (
    <div className="relative h-full">
      <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-800">
        <h1 className="text-lg font-bold text-slate-900 dark:text-white">Graph Agent</h1>
        <p className="text-xs text-slate-400">Click any node to edit its prompt, transitions and tools.</p>
      </div>

      <div className="relative h-[calc(100%-69px)] overflow-auto scroll-thin bg-[radial-gradient(theme(colors.slate.200)_1px,transparent_1px)] dark:bg-[radial-gradient(theme(colors.slate.800)_1px,transparent_1px)] [background-size:22px_22px]">
        <div className="relative" style={{ width: 1040, height: 460 }}>
          {/* Connector lines */}
          <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ width: 1040, height: 460 }}>
            <defs>
              <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
                <path d="M0,0 L8,3 L0,6 Z" fill="#94a3b8" />
              </marker>
            </defs>
            {edges.map((e) => {
              const midX = (e.x1 + e.x2) / 2
              return (
                <path
                  key={e.key}
                  d={`M ${e.x1} ${e.y1} C ${midX} ${e.y1}, ${midX} ${e.y2}, ${e.x2 - 6} ${e.y2}`}
                  fill="none" stroke="#94a3b8" strokeWidth="2" markerEnd="url(#arrow)"
                />
              )
            })}
          </svg>

          {flowNodes.map((n) => (
            <FlowNode key={n.id} node={n} selected={selected?.id === n.id} onSelect={(id) => setSelected(byId[id])} />
          ))}
        </div>
      </div>

      {selected && <InspectorFlyout node={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
