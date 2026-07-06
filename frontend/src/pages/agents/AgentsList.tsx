import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useGet, useRefresh } from '../../api/hooks/useApi'
import { activateAgent, archiveAgent, type AgentsList as AgentsListT } from '../../api/agents'
import { Card, Button, Badge, EmptyState } from '../../components/ui'
import { PageHeader, AsyncState } from '../../components/kit'
import { useToast } from '../../components/Toast'
import { Plus, Radio, Pencil, Archive, Bot, Languages, GitBranch, CheckCircle2, PhoneIncoming, PhoneOutgoing, CalendarDays } from 'lucide-react'

export default function AgentsList() {
  const { data, isLoading, error } = useGet<AgentsListT>(['agents'], '/agents', { refetchInterval: 15000 })
  const refresh = useRefresh()
  const toast = useToast()
  const [busy, setBusy] = useState<string>('')

  const onActivate = async (name: string) => {
    setBusy(name)
    try {
      await activateAgent(name)
      toast(`"${name}" is now answering inbound calls`, 'success')
      refresh(['agents'])
    } catch (e: any) {
      toast(`Activate failed: ${e.message || e}`, 'error')
    } finally {
      setBusy('')
    }
  }

  const onArchive = async (name: string) => {
    if (!confirm(`Archive agent "${name}"? It will be moved to agents/_archived and stop being selectable.`)) return
    setBusy(name)
    try {
      await archiveAgent(name)
      toast(`Archived "${name}"`, 'success')
      refresh(['agents'])
    } catch (e: any) {
      toast(`Archive failed: ${e.message || e}`, 'error')
    } finally {
      setBusy('')
    }
  }

  const agents = data?.agents ?? []

  return (
    <div className="space-y-7">
      <PageHeader
        title="Agents"
        sub="Build voice agents and connect one to the live calling system"
        actions={
          <Link to="/build/agents/new">
            <Button variant="primary"><Plus size={15} /> New Agent</Button>
          </Link>
        }
      />

      <AsyncState loading={isLoading} error={error}>
        {agents.length === 0 ? (
          <EmptyState icon={<Bot size={22} />} title="No agents yet" hint="Create your first agent to get started." />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {agents.map((a) => (
              <Card key={a.name} className="flex flex-col gap-3 p-5" hover>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="font-display text-[16px] font-bold tracking-tight truncate">
                        {a.agent_name || a.name}
                      </h3>
                      {a.active && (
                        <Badge tone="mint" soft><CheckCircle2 size={12} /> Active</Badge>
                      )}
                    </div>
                    <div className="truncate text-[12px] text-dim">{a.company || '—'}</div>
                  </div>
                  <code className="shrink-0 rounded-md bg-[var(--surface-2)] px-2 py-0.5 font-mono text-[11px] text-soft ring-1 ring-[var(--border)]">
                    {a.name}
                  </code>
                </div>

                {a.error ? (
                  <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-[12px] text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">
                    Invalid YAML: {a.error}
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-1.5">
                    {(a.languages ?? []).map((l) => (
                      <span key={l} className="inline-flex items-center gap-1 rounded-md bg-[var(--surface-2)] px-2 py-0.5 text-[11px] font-semibold text-soft ring-1 ring-[var(--border)]">
                        <Languages size={11} /> {l}
                      </span>
                    ))}
                    <span className="inline-flex items-center gap-1 rounded-md bg-[var(--surface-2)] px-2 py-0.5 text-[11px] font-semibold text-soft ring-1 ring-[var(--border)]">
                      <GitBranch size={11} /> {(a.stages ?? []).length} stages
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-md bg-[var(--surface-2)] px-2 py-0.5 text-[11px] font-semibold text-soft ring-1 ring-[var(--border)]">
                      {a.call_direction === 'outbound' ? <PhoneOutgoing size={11} /> : <PhoneIncoming size={11} />}
                      {a.call_direction === 'outbound' ? 'outbound' : 'inbound'}
                    </span>
                    {(a.calendar_slots ?? 0) > 0 && (
                      <span className="inline-flex items-center gap-1 rounded-md bg-[var(--surface-2)] px-2 py-0.5 text-[11px] font-semibold text-soft ring-1 ring-[var(--border)]">
                        <CalendarDays size={11} /> {a.calendar_slots} slots
                      </span>
                    )}
                  </div>
                )}

                <div className="mt-1 flex items-center gap-2 border-t border-[var(--border)] pt-3">
                  {a.active ? (
                    <Button variant="ghost" disabled><Radio size={14} /> Live</Button>
                  ) : (
                    <Button variant="primary" onClick={() => onActivate(a.name)} disabled={busy === a.name || !!a.error}>
                      <Radio size={14} /> Activate
                    </Button>
                  )}
                  <Link to={`/build/agents/${a.name}`} className="ml-auto">
                    <Button variant="ghost"><Pencil size={14} /> Edit</Button>
                  </Link>
                  <Button variant="ghost" onClick={() => onArchive(a.name)} disabled={busy === a.name}>
                    <Archive size={14} />
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </AsyncState>

      <p className="text-[12px] text-dim">
        The <span className="font-semibold text-soft">Active</span> agent answers every inbound call. Activating writes
        <code className="mx-1 font-mono text-[11px]">agents/active_agent.json</code>, which the telephony bridge reads when each call starts.
      </p>
    </div>
  )
}
