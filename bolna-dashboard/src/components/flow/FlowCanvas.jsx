import { useState } from 'react'
import { Plus } from 'lucide-react'
import { flowNodes } from '../../data/mockData.js'
import FlowNode from './FlowNode.jsx'
import InspectorFlyout from './InspectorFlyout.jsx'

// Estimated node box dimensions (FlowNode is w-52 ≈ 208px; height ≈ 92px).
const NODE_W = 200
const NODE_H = 92

function FlowCanvas() {
  const [nodes, setNodes] = useState(flowNodes)
  const [selectedNodeId, setSelectedNodeId] = useState(null)
  const [counter, setCounter] = useState(1)

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) || null

  const handleNodeChange = (updated) =>
    setNodes((prev) => prev.map((n) => (n.id === updated.id ? updated : n)))

  const addNode = () => {
    const id = `node_custom_${counter}`
    setCounter((c) => c + 1)
    setNodes((prev) => [
      ...prev,
      {
        id,
        type: 'LLM',
        label: `New Node ${counter}`,
        position: { x: 320, y: 320 + counter * 20 },
        prompt: 'Describe what this node should do…',
        exampleResponses: [],
        transitions: [],
        connectedFunctionCall: null,
      },
    ])
  }

  // Build connector segments: source right-edge-center → target left-edge-center.
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]))
  const lines = []
  nodes.forEach((src) => {
    src.transitions.forEach((targetId) => {
      const dst = byId[targetId]
      if (!dst) return
      lines.push({
        key: `${src.id}->${targetId}`,
        x1: src.position.x + NODE_W,
        y1: src.position.y + NODE_H / 2,
        x2: dst.position.x,
        y2: dst.position.y + NODE_H / 2,
      })
    })
  })

  return (
    <div>
      {/* Header row */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-4">
          <h1 className="page-title">Graph Agent</h1>
          {/* Legend */}
          <div className="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-emerald-500" /> START
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-accent" /> LLM
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-gray-400 dark:bg-red-400" /> CLOSING
            </span>
          </div>
        </div>
        <button type="button" onClick={addNode} className="btn-outline">
          <Plus className="h-4 w-4" />
          Add node
        </button>
      </div>

      {/* Canvas */}
      <div
        className="card relative min-h-[560px] overflow-auto
          bg-[radial-gradient(circle,rgba(0,0,0,0.10)_1px,transparent_1px)]
          dark:bg-[radial-gradient(circle,rgba(255,255,255,0.08)_1px,transparent_1px)]
          [background-size:20px_20px]"
      >
        <div className="relative h-[560px] min-w-[1120px]">
          {/* Connector layer */}
          <svg className="pointer-events-none absolute inset-0 h-full w-full text-gray-300 dark:text-white/20">
            <defs>
              <marker
                id="flow-arrowhead"
                markerWidth="8"
                markerHeight="8"
                refX="7"
                refY="3.5"
                orient="auto"
                markerUnits="userSpaceOnUse"
              >
                <path d="M0,0 L7,3.5 L0,7 Z" fill="currentColor" />
              </marker>
            </defs>
            {lines.map((l) => (
              <line
                key={l.key}
                x1={l.x1}
                y1={l.y1}
                x2={l.x2}
                y2={l.y2}
                stroke="currentColor"
                strokeWidth="2"
                markerEnd="url(#flow-arrowhead)"
              />
            ))}
          </svg>

          {/* Nodes */}
          {nodes.map((node) => (
            <div
              key={node.id}
              className="absolute"
              style={{ left: node.position.x, top: node.position.y }}
            >
              <FlowNode
                node={node}
                selected={node.id === selectedNodeId}
                onClick={setSelectedNodeId}
              />
            </div>
          ))}
        </div>
      </div>

      {/* Inspector */}
      <InspectorFlyout
        node={selectedNode}
        onClose={() => setSelectedNodeId(null)}
        onChange={handleNodeChange}
      />
    </div>
  )
}

export default FlowCanvas
