import { useState } from 'react'
import { useGet } from '../../api/hooks/useApi'
import { previewIntent, type AgentsList, type IntentResult } from '../../api/agents'
import { Card, Button, Badge, EmptyState } from '../../components/ui'
import { Field, Textarea, Select } from '../../components/kit'
import { ScanSearch, Play, Target } from 'lucide-react'

/** Pattern-match intent tester — embedded as a tab inside the Agents panel.
 * `initialAgent` pre-selects an agent (used when opened from a specific card). */
export default function IntentTester({ initialAgent = '' }: { initialAgent?: string }) {
  const { data: agents } = useGet<AgentsList>(['agents'], '/agents')
  const [agent, setAgent] = useState(initialAgent)
  const [text, setText] = useState('')
  const [res, setRes] = useState<IntentResult | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const run = async () => {
    if (!text.trim()) return
    setBusy(true); setErr('')
    try { setRes(await previewIntent(text, agent || undefined)) }
    catch (e: any) { setErr(e.message || String(e)); setRes(null) }
    finally { setBusy(false) }
  }

  const list = agents?.agents ?? []
  return (
    <div className="space-y-6">
      <p className="text-sm text-dim">
        See how an agent classifies caller speech — pattern matching only, no LLM call.
      </p>

      <Card className="p-5 space-y-4">
        <div className="grid gap-4 sm:grid-cols-[1fr_220px]">
          <Field label="Caller says…">
            <Textarea
              rows={3}
              value={text}
              placeholder="This is too expensive, can you call me next week?"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') run() }}
            />
          </Field>
          <Field label="Agent">
            <Select value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">{agents?.active ? `Active (${agents.active})` : 'First available'}</option>
              {list.map((a) => <option key={a.name} value={a.name}>{a.agent_name || a.name}</option>)}
            </Select>
          </Field>
        </div>
        <div className="flex items-center justify-end gap-2">
          <span className="text-2xs text-dim">Ctrl+↵</span>
          <Button variant="primary" onClick={run} disabled={busy || !text.trim()}><Play size={14} /> {busy ? 'Matching…' : 'Run'}</Button>
        </div>
        {err && <div role="alert" className="break-words rounded-md bg-rose-500/10 px-3 py-2 text-xs text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">{err}</div>}
      </Card>

      {res && (
        <Card className="p-5 space-y-4">
          <p className="break-words text-sm italic text-soft">"{res.text}"</p>
          <div className="flex flex-wrap items-center gap-2">
            <Target size={16} className="text-mint-500" />
            <span className="text-sm text-soft">Top intent</span>
            <Badge tone={res.intent === 'none' ? 'neutral' : 'mint'} soft>{res.intent}</Badge>
            {text.trim() !== res.text && <Badge tone="amber" soft>Input changed — run again</Badge>}
            <span title={res.agent_id} className="ml-auto max-w-full truncate text-2xs text-dim">{res.agent_id}</span>
          </div>

          {res.matches.length > 0 ? (
            <div className="space-y-1.5">
              <div className="eyebrow">Matched patterns</div>
              {res.matches.map((m, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2 rounded-md bg-[var(--surface-2)] px-3 py-2 text-xs ring-1 ring-[var(--border)]">
                  <Badge tone="indigo" soft>{m.intent}</Badge>
                  <span className="text-soft">matched</span>
                  <code className="break-all font-mono text-2xs text-[var(--text)]">{m.pattern}</code>
                </div>
              ))}
            </div>
          ) : <EmptyState icon={<ScanSearch size={18} />} title="No intent patterns matched" hint="The agent would fall back to the LLM for this turn." />}

          {res.qa_signals.length > 0 && (
            <div className="space-y-1.5">
              <div className="eyebrow">QA signals detected</div>
              <div className="flex flex-wrap gap-1.5">
                {res.qa_signals.map((q, i) => (
                  <span key={i} className="rounded-md bg-[var(--surface-2)] px-2 py-1 text-2xs text-soft ring-1 ring-[var(--border)]">{q.field}: "{q.signal}"</span>
                ))}
              </div>
            </div>
          )}

          <div className="border-t border-[var(--border)] pt-3">
            <div className="eyebrow mb-1.5">Patterns checked</div>
            <div className="flex flex-wrap gap-3 text-2xs text-dim">
              {Object.entries(res.patterns_checked).map(([k, v]) => <span key={k}>{k}: <span className="font-semibold text-soft">{v}</span></span>)}
            </div>
          </div>
        </Card>
      )}
    </div>
  )
}
