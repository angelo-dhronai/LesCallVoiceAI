import { useState } from 'react'
import { useGet } from '../../api/hooks/useApi'
import { previewIntent, type AgentsList, type IntentResult } from '../../api/agents'
import { Card, Button, Badge, EmptyState } from '../../components/ui'
import { PageHeader, Field, Textarea, Select } from '../../components/kit'
import { ScanSearch, Play, Target } from 'lucide-react'

export default function IntentPreview() {
  const { data: agents } = useGet<AgentsList>(['agents'], '/agents')
  const [agent, setAgent] = useState('')
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
      <PageHeader title="Intent Preview" sub="See how an agent classifies caller speech — pattern matching only, no LLM call" />

      <Card className="p-5 space-y-4">
        <div className="grid gap-4 sm:grid-cols-[1fr_220px]">
          <Field label="Caller says…">
            <Textarea rows={3} value={text} placeholder="This is too expensive, can you call me next week?" onChange={(e) => setText(e.target.value)} />
          </Field>
          <Field label="Agent">
            <Select value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">{agents?.active ? `Active (${agents.active})` : 'First available'}</option>
              {list.map((a) => <option key={a.name} value={a.name}>{a.agent_name || a.name}</option>)}
            </Select>
          </Field>
        </div>
        <div className="flex justify-end"><Button variant="primary" onClick={run} disabled={busy || !text.trim()}><Play size={14} /> {busy ? 'Matching…' : 'Run'}</Button></div>
        {err && <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-[12px] text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">{err}</div>}
      </Card>

      {res && (
        <Card className="p-5 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Target size={16} className="text-mint-500" />
            <span className="text-[13px] text-soft">Top intent</span>
            <Badge tone={res.intent === 'none' ? 'neutral' : 'mint'} soft>{res.intent}</Badge>
            <span className="ml-auto text-[11px] text-dim">{res.agent_id}</span>
          </div>

          {res.matches.length > 0 ? (
            <div className="space-y-1.5">
              {res.matches.map((m, i) => (
                <div key={i} className="flex items-center gap-2 rounded-lg bg-[var(--surface-2)] px-3 py-2 text-[12px] ring-1 ring-[var(--border)]">
                  <Badge tone="indigo" soft>{m.intent}</Badge>
                  <span className="text-soft">matched</span>
                  <code className="font-mono text-[11px] text-[var(--text)]">{m.pattern}</code>
                </div>
              ))}
            </div>
          ) : <EmptyState icon={<ScanSearch size={18} />} title="No intent patterns matched" hint="The agent would fall back to the LLM for this turn." />}

          {res.qa_signals.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {res.qa_signals.map((q, i) => (
                <span key={i} className="rounded-md bg-[var(--surface-2)] px-2 py-1 text-[11px] text-soft ring-1 ring-[var(--border)]">{q.field}: "{q.signal}"</span>
              ))}
            </div>
          )}

          <div className="flex flex-wrap gap-3 border-t border-[var(--border)] pt-3 text-[11px] text-dim">
            {Object.entries(res.patterns_checked).map(([k, v]) => <span key={k}>{k}: <span className="font-semibold text-soft">{v}</span></span>)}
          </div>
        </Card>
      )}
    </div>
  )
}
