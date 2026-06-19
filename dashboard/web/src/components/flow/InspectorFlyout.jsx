import { useState, useEffect } from 'react'
import { X, Plus } from 'lucide-react'
import { flowNodes, FUNCTION_OPTIONS } from '../../data/mockData.js'

export default function InspectorFlyout({ node, onClose }) {
  const [draft, setDraft] = useState(node)
  const [chip, setChip] = useState('')

  // Re-sync the editable copy whenever a different node is selected.
  useEffect(() => { setDraft(node); setChip('') }, [node])

  if (!node) return null
  const isLLM = draft.type === 'LLM'
  const targets = flowNodes.filter((n) => n.id !== draft.id)

  const addChip = () => {
    if (!chip.trim()) return
    setDraft((d) => ({ ...d, examples: [...d.examples, chip.trim()] }))
    setChip('')
  }

  return (
    <div className="absolute top-0 right-0 h-full w-[360px] bg-white dark:bg-slate-900 border-l border-slate-200 dark:border-slate-800 shadow-flyout z-30 flex flex-col animate-[slidein_.2s_ease-out]">
      <style>{`@keyframes slidein{from{transform:translateX(20px);opacity:.4}to{transform:translateX(0);opacity:1}}`}</style>

      <div className="flex items-center justify-between px-5 h-14 border-b border-slate-100 dark:border-slate-800">
        <div className="font-semibold text-slate-800 dark:text-slate-100">Inspector</div>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X className="h-5 w-5" /></button>
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin p-5 space-y-5">
        <div>
          <div className="label">Node ID</div>
          <input className="input font-mono" value={draft.id} readOnly />
        </div>

        {/* Node type toggle */}
        <div>
          <div className="label">Node type</div>
          <div className="flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-700 p-1 w-max">
            {['LLM', 'Static'].map((t) => {
              const on = (t === 'LLM') === isLLM
              return (
                <button
                  key={t}
                  onClick={() => setDraft((d) => ({ ...d, type: t === 'LLM' ? 'LLM' : 'CLOSING' }))}
                  className={`px-3 py-1 rounded-lg text-sm font-medium transition
                    ${on ? 'bg-accent text-white' : 'text-slate-500 hover:text-slate-700 dark:text-slate-400'}`}
                >
                  {t}
                </button>
              )
            })}
          </div>
        </div>

        <div>
          <div className="label">Prompt</div>
          <textarea
            value={draft.prompt}
            onChange={(e) => setDraft((d) => ({ ...d, prompt: e.target.value }))}
            placeholder="Describe what this node should do…"
            className="input h-28 resize-none font-mono text-[13px]"
          />
        </div>

        {/* Example response chips */}
        <div>
          <div className="label">Example responses</div>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {draft.examples.map((ex, i) => (
              <span key={i} className="badge bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300 pr-1">
                {ex}
                <button
                  onClick={() => setDraft((d) => ({ ...d, examples: d.examples.filter((_, j) => j !== i) }))}
                  className="ml-1 text-slate-400 hover:text-rose-500"
                ><X className="h-3 w-3" /></button>
              </span>
            ))}
            {draft.examples.length === 0 && <span className="text-xs text-slate-400">None yet.</span>}
          </div>
          <div className="flex gap-2">
            <input
              value={chip}
              onChange={(e) => setChip(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addChip()}
              placeholder="Add example…"
              className="input"
            />
            <button onClick={addChip} className="btn btn-outline !px-2.5"><Plus className="h-4 w-4" /></button>
          </div>
        </div>

        {/* Transitions */}
        <div>
          <div className="label">Transitions</div>
          <div className="space-y-2">
            {draft.transitions.length === 0 && <div className="text-xs text-slate-400">Terminal node — no transitions.</div>}
            {draft.transitions.map((tr, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-xs text-slate-400 w-20 truncate">{tr.label}</span>
                <select
                  value={tr.to}
                  onChange={(e) => setDraft((d) => {
                    const transitions = [...d.transitions]
                    transitions[i] = { ...transitions[i], to: e.target.value }
                    return { ...d, transitions }
                  })}
                  className="input flex-1"
                >
                  {targets.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                </select>
              </div>
            ))}
          </div>
        </div>

        {/* Function call */}
        <div>
          <div className="label">Function call</div>
          <select
            value={draft.fn}
            onChange={(e) => setDraft((d) => ({ ...d, fn: e.target.value }))}
            className="input"
          >
            {FUNCTION_OPTIONS.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </div>
      </div>
    </div>
  )
}
