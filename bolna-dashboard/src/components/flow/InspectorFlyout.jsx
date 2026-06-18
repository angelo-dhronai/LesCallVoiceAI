import { useState } from 'react'
import { X, Plus } from 'lucide-react'
import { functionCallOptions, flowNodes } from '../../data/mockData.js'

function InspectorFlyout({ node, onClose, onChange }) {
  const [chipDraft, setChipDraft] = useState('')

  if (!node) return null

  const isLLM = node.type === 'LLM'

  const update = (patch) => onChange({ ...node, ...patch })

  const toggleType = () => {
    // LLM <-> Static. We map "Static" onto CLOSING for non-START nodes.
    if (node.type === 'START') return
    update({ type: isLLM ? 'CLOSING' : 'LLM' })
  }

  const removeChip = (idx) =>
    update({ exampleResponses: node.exampleResponses.filter((_, i) => i !== idx) })

  const addChip = () => {
    const v = chipDraft.trim()
    if (!v) return
    update({ exampleResponses: [...node.exampleResponses, v] })
    setChipDraft('')
  }

  const reassignTransition = (idx, newTarget) =>
    update({ transitions: node.transitions.map((t, i) => (i === idx ? newTarget : t)) })

  // All other nodes available as transition targets.
  const otherNodes = flowNodes.filter((n) => n.id !== node.id)

  return (
    <>
      {/* Backdrop — clicking outside closes. */}
      <div
        className="fixed inset-0 z-40 bg-black/10 dark:bg-black/40 animate-fade-in"
        onClick={onClose}
      />

      {/* Flyout panel */}
      <aside
        className="fixed right-0 top-0 z-50 h-full w-[360px] overflow-y-auto shadow-2xl
          bg-white dark:bg-[#13161d] border-l border-gray-200 dark:border-white/10 animate-slide-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-gray-200
          dark:border-white/10 bg-white dark:bg-[#13161d] px-5 py-4">
          <h2 className="text-base font-bold text-gray-900 dark:text-white">Inspector</h2>
          <button type="button" onClick={onClose} className="btn-ghost px-2 py-2" aria-label="Close inspector">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-6 px-5 py-5">
          {/* Node ID */}
          <div>
            <div className="section-label mb-1.5">Node ID</div>
            <div className="font-mono text-xs text-gray-400 dark:text-gray-500 break-all">{node.id}</div>
          </div>

          {/* Type toggle */}
          <div>
            <div className="section-label mb-2">Node Type</div>
            <div className="flex items-center justify-between rounded-lg border border-gray-200
              dark:border-white/10 px-3 py-2.5">
              <span className={`text-sm font-medium ${!isLLM ? 'text-gray-900 dark:text-white' : 'text-gray-400 dark:text-gray-500'}`}>
                Static
              </span>
              <button
                type="button"
                onClick={toggleType}
                disabled={node.type === 'START'}
                role="switch"
                aria-checked={isLLM}
                className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-40
                  ${isLLM ? 'bg-accent' : 'bg-gray-300 dark:bg-white/15'}`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform
                    ${isLLM ? 'translate-x-5' : 'translate-x-0'}`}
                />
              </button>
              <span className={`text-sm font-medium ${isLLM ? 'text-gray-900 dark:text-white' : 'text-gray-400 dark:text-gray-500'}`}>
                LLM
              </span>
            </div>
          </div>

          {/* Prompt */}
          <div>
            <div className="section-label mb-2">Prompt</div>
            <textarea
              className="input min-h-[110px] resize-y"
              value={node.prompt}
              onChange={(e) => update({ prompt: e.target.value })}
            />
          </div>

          {/* Example responses */}
          <div>
            <div className="section-label mb-2">Example Responses</div>
            <div className="flex flex-wrap gap-2">
              {node.exampleResponses.map((resp, idx) => (
                <span
                  key={idx}
                  className="inline-flex items-center gap-1 rounded-full bg-gray-100 dark:bg-white/5
                    px-2.5 py-1 text-xs text-gray-700 dark:text-gray-300"
                >
                  <span className="max-w-[180px] truncate">{resp}</span>
                  <button
                    type="button"
                    onClick={() => removeChip(idx)}
                    className="text-gray-400 hover:text-red-500 dark:hover:text-red-400"
                    aria-label="Remove response"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
              {node.exampleResponses.length === 0 && (
                <span className="text-xs text-gray-400 dark:text-gray-500">No example responses yet.</span>
              )}
            </div>
            <div className="mt-2 flex gap-2">
              <input
                className="input"
                placeholder="Add a response…"
                value={chipDraft}
                onChange={(e) => setChipDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    addChip()
                  }
                }}
              />
              <button type="button" onClick={addChip} className="btn-outline shrink-0 px-2.5 py-2" aria-label="Add response">
                <Plus className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* Transitions */}
          <div>
            <div className="section-label mb-2">Transitions</div>
            {node.transitions.length === 0 ? (
              <span className="text-xs text-gray-400 dark:text-gray-500">No outgoing transitions (terminal node).</span>
            ) : (
              <div className="space-y-2">
                {node.transitions.map((targetId, idx) => (
                  <select
                    key={idx}
                    className="input"
                    value={targetId}
                    onChange={(e) => reassignTransition(idx, e.target.value)}
                  >
                    {otherNodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.label} ({n.id})
                      </option>
                    ))}
                  </select>
                ))}
              </div>
            )}
          </div>

          {/* Function call */}
          <div>
            <div className="section-label mb-2">Function Call</div>
            <select
              className="input"
              value={node.connectedFunctionCall || 'none'}
              onChange={(e) =>
                update({ connectedFunctionCall: e.target.value === 'none' ? null : e.target.value })
              }
            >
              {functionCallOptions.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </div>
        </div>
      </aside>
    </>
  )
}

export default InspectorFlyout
