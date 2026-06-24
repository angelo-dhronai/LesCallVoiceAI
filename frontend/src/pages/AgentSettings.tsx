import type { ReactNode } from 'react'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { Card, Badge, Button, cx } from '../components/ui'
import { PageHeader, AsyncState } from '../components/kit'
import { Bot, MessageSquare, Shield, Sparkles, BookOpen, RefreshCw } from 'lucide-react'

type AgentConfig = {
  available: boolean
  reason?: string
  agent_name: string | null
  agent_tone: string | null
  greeting: string | null
  identity: string[]
  brand: string[]
  prompt_guidelines: string[]
  firm_rules: string[]
  pitch: {
    project_portfolio: string[]
    objection_handling: string[]
  }
  source_file: string
  editable: boolean
  editable_note: string
}

function StatCard({ label, value, wrap }: { label: string; value: string; wrap?: boolean }) {
  return (
    <Card>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-dim">{label}</div>
      <div className={cx('mt-1 text-[14px] font-semibold text-[var(--text)]', !wrap && 'truncate')}>{value}</div>
    </Card>
  )
}

function ListCard({ title, icon, items }: { title: string; icon: ReactNode; items?: string[] | null }) {
  const rows = Array.isArray(items) ? items : []
  return (
    <Card>
      <div className="mb-3 flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--surface-2)] text-soft">{icon}</span>
        <h3 className="font-display text-[15px] font-bold tracking-tight">{title}</h3>
      </div>
      {rows.length === 0 ? (
        <div className="text-[13px] text-dim">None</div>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((it, i) => (
            <li key={i} className="flex gap-2 text-[13px] text-soft">
              <span className="mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full bg-mint-400/60" />
              <span className="min-w-0 break-words">{String(it)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export default function AgentSettings() {
  const { data, isLoading, error } = useGet<AgentConfig>(['agent-config'], '/agent/config')
  const refresh = useRefresh()

  const cfg = data
  const note = cfg?.editable_note || 'Read-only — generated live from the calling agent’s source'

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agent Settings"
        sub="Live agent identity & prompt (read-only)"
        actions={
          <Button variant="ghost" onClick={() => refresh(['agent-config'])}>
            <RefreshCw size={15} /> Refresh
          </Button>
        }
      />

      <AsyncState loading={isLoading} error={error}>
        {cfg?.available === false ? (
          <Card>
            <div className="text-[13px] text-soft">{cfg?.reason || 'Agent config unavailable'}</div>
          </Card>
        ) : (
          <div className="space-y-6">
            <div className="flex items-center gap-2">
              <Badge tone="amber">{note}</Badge>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <StatCard label="Agent Name" value={cfg?.agent_name || '—'} />
              <StatCard label="Tone" value={cfg?.agent_tone || '—'} />
              <StatCard label="Greeting" value={cfg?.greeting || '—'} wrap />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <ListCard title="Identity" icon={<Bot size={16} />} items={cfg?.identity} />
              <ListCard title="Conversation Guidelines" icon={<MessageSquare size={16} />} items={cfg?.prompt_guidelines} />
              <ListCard title="Firm Rules" icon={<Shield size={16} />} items={cfg?.firm_rules} />
              <ListCard title="Brand" icon={<Sparkles size={16} />} items={cfg?.brand} />
              <ListCard title="Pitch — Project Portfolio" icon={<BookOpen size={16} />} items={cfg?.pitch?.project_portfolio} />
              <ListCard title="Pitch — Objection Handling" icon={<MessageSquare size={16} />} items={cfg?.pitch?.objection_handling} />
            </div>

            {cfg?.source_file && (
              <div className="font-mono text-[11px] text-dim">{cfg.source_file}</div>
            )}
          </div>
        )}
      </AsyncState>
    </div>
  )
}
