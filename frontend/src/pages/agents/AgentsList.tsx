import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useGet, useRefresh } from '../../api/hooks/useApi'
import { useMe } from '../../api/hooks/useMe'
import { activateAgent, archiveAgent, type AgentsList as AgentsListT } from '../../api/agents'
import { patch } from '../../api/mutate'
import { Card, Button, Badge, EmptyState, Spinner, SkeletonBlock, cx } from '../../components/ui'
import { PageHeader, AsyncState, Segmented } from '../../components/kit'
import { Modal } from '../../components/Modal'
import { useToast } from '../../components/Toast'
import {
  Plus, Radio, Pencil, Archive, Bot, Languages, GitBranch, CheckCircle2,
  PhoneIncoming, PhoneOutgoing, CalendarDays, Info, PhoneForwarded, Unplug,
  AlertTriangle, ArrowRight,
} from 'lucide-react'
import IntentTester from './IntentTester'

type Tab = 'agents' | 'intent'

type NumberRow = {
  id: number
  number: string
  label?: string | null
  inbound_agent_slug?: string | null
  config_status?: string | null
}

/* ------------------------------------------------------------------ activate modal */

function ActivateModal({
  slug, title, numbers, busyId, onConnect, onDisconnect, onClose,
}: {
  slug: string
  title: string
  numbers: NumberRow[]
  busyId: number | null
  onConnect: (n: NumberRow) => void
  onDisconnect: (n: NumberRow) => void
  onClose: () => void
}) {
  // A number already live with another agent needs an explicit detach confirm.
  const [confirmFor, setConfirmFor] = useState<NumberRow | null>(null)

  return (
    <Modal open onClose={onClose} title={`Activate “${title}”`} wide>
      <p className="mb-3 text-[13px] text-soft">
        An agent goes live by connecting it to one of your phone numbers — it then answers
        every call on that number.
      </p>

      {numbers.length === 0 ? (
        <EmptyState
          icon={<PhoneForwarded size={20} />}
          title="You have no numbers yet"
          hint="Connect your own number first — it takes about 2 minutes."
          action={
            <Link to="/telephony" onClick={onClose}>
              <Button variant="primary" size="sm">
                Go to Numbers (BYON) <ArrowRight size={13} />
              </Button>
            </Link>
          }
        />
      ) : (
        <div className="space-y-2">
          {numbers.map((n) => {
            const mine = n.inbound_agent_slug === slug
            const free = !n.inbound_agent_slug
            const applied = n.config_status === 'applied'
            const busy = busyId === n.id
            return (
              <div key={n.id}
                className={cx('flex flex-wrap items-center gap-2.5 rounded-xl px-3.5 py-3 ring-1 transition',
                  mine ? 'bg-mint-400/10 ring-mint-400/30' : 'bg-[var(--surface-2)] ring-[var(--border)]')}>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[13.5px] font-semibold tabular-nums">{n.number}</span>
                    {!applied && (
                      <Badge tone="amber" soft>
                        <AlertTriangle size={11} /> setup incomplete
                      </Badge>
                    )}
                  </div>
                  {n.label && <div className="text-[11.5px] text-dim">{n.label}</div>}
                </div>
                <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2">
                  {mine ? (
                    <>
                      <Badge tone="mint">
                        <span className="h-1.5 w-1.5 rounded-full bg-mint-400 animate-pulse-soft" />
                        Connected to this agent
                      </Badge>
                      <Button variant="ghost" size="sm" disabled={busy} onClick={() => onDisconnect(n)}>
                        {busy ? <Spinner className="h-3 w-3" /> : <Unplug size={13} />} Disconnect
                      </Button>
                    </>
                  ) : free ? (
                    <>
                      <Badge tone="neutral">Free</Badge>
                      <Button variant="primary" size="sm" disabled={busy} onClick={() => onConnect(n)}>
                        {busy ? <Spinner className="h-3 w-3" /> : <Radio size={13} />} Connect
                      </Button>
                    </>
                  ) : (
                    <>
                      <Badge tone="indigo">Live with “{n.inbound_agent_slug}”</Badge>
                      <Button variant="soft" size="sm" disabled={busy} onClick={() => setConfirmFor(n)}>
                        {busy ? <Spinner className="h-3 w-3" /> : <Radio size={13} />} Connect
                      </Button>
                    </>
                  )}
                </div>

                {confirmFor?.id === n.id && !mine && !free && (
                  <div role="alertdialog" className="mt-1 w-full rounded-xl bg-amber-400/10 px-3.5 py-3 ring-1 ring-amber-400/30">
                    <div className="flex items-start gap-2 text-[12.5px] text-soft">
                      <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-300" />
                      <span>
                        <b>{n.number}</b> is currently answered by <b>“{n.inbound_agent_slug}”</b>.
                        Detach it and connect <b>“{title}”</b> instead? The switch applies from the next call.
                      </span>
                    </div>
                    <div className="mt-2.5 flex flex-wrap items-center gap-2 pl-6">
                      <Button variant="danger" size="sm" disabled={busy}
                        onClick={() => { setConfirmFor(null); onConnect(n) }}>
                        {busy ? <Spinner className="h-3 w-3" /> : <Unplug size={13} />} Detach & connect
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setConfirmFor(null)}>Cancel</Button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          <p className="pt-1 text-[11.5px] text-dim">
            “Setup incomplete” numbers can be connected now, but won't take calls until their
            trunk is applied in <Link to="/telephony" onClick={onClose} className="font-semibold text-indigo-600 hover:underline dark:text-indigo-300">Numbers (BYON)</Link>.
          </p>
        </div>
      )}
    </Modal>
  )
}

/* ------------------------------------------------------------------ page */

export default function AgentsList() {
  const { data, isLoading, error } = useGet<AgentsListT>(['agents'], '/agents', { refetchInterval: 15000 })
  const { data: numbersData } = useGet<{ numbers: NumberRow[] }>(['numbers'], '/numbers')
  const { data: me } = useMe()
  const isAdmin = me?.is_admin === true
  const refresh = useRefresh()
  const toast = useToast()
  const [busy, setBusy] = useState<string>('')
  const [bindBusyId, setBindBusyId] = useState<number | null>(null)
  const [tab, setTab] = useState<Tab>('agents')
  const [confirmArchive, setConfirmArchive] = useState<string>('')
  const [activating, setActivating] = useState<{ slug: string; title: string } | null>(null)
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => { if (confirmTimer.current) clearTimeout(confirmTimer.current) }, [])

  const numbers = useMemo(() => numbersData?.numbers ?? [], [numbersData])
  const boundTo = (slug: string) => numbers.filter((n) => n.inbound_agent_slug === slug)

  const connectNumber = async (n: NumberRow) => {
    if (!activating) return
    setBindBusyId(n.id)
    try {
      await patch(`/numbers/${n.id}`, { inbound_agent_slug: activating.slug })
      toast(`“${activating.title}” is now live on ${n.number}`, 'success')
      refresh(['numbers'], ['agents'])
    } catch (e: any) {
      toast(`Could not connect: ${e.message || e}`, 'error')
    } finally {
      setBindBusyId(null)
    }
  }

  const disconnectNumber = async (n: NumberRow) => {
    setBindBusyId(n.id)
    try {
      await patch(`/numbers/${n.id}`, { inbound_agent_slug: '' })
      toast(`${n.number} disconnected — it has no agent now`, 'info')
      refresh(['numbers'], ['agents'])
    } catch (e: any) {
      toast(`Could not disconnect: ${e.message || e}`, 'error')
    } finally {
      setBindBusyId(null)
    }
  }

  // Admin-only: the legacy platform fallback (active_agent.json) that answers
  // calls arriving with no DID→agent binding.
  const onSetFallback = async (name: string) => {
    setBusy(name)
    try {
      await activateAgent(name)
      toast(`"${name}" is now the platform fallback agent`, 'success')
      refresh(['agents'])
    } catch (e: any) {
      toast(`Failed: ${e.message || e}`, 'error')
    } finally {
      setBusy('')
    }
  }

  const requestArchive = (name: string) => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current)
    setConfirmArchive(name)
    confirmTimer.current = setTimeout(() => setConfirmArchive(''), 4000)
  }

  const onArchive = async (name: string) => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current)
    setConfirmArchive('')
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

  const agents = [...(data?.agents ?? [])].sort((a, b) =>
    (boundTo(b.name).length - boundTo(a.name).length) || (Number(b.active) - Number(a.active)))

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agents"
        sub="Build voice agents, test their intent matching, and connect one to your phone number"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Segmented<Tab>
              value={tab}
              onChange={setTab}
              options={[{ value: 'agents', label: 'Agents' }, { value: 'intent', label: 'Intent tester' }]}
            />
            {tab === 'agents' && (
              <Link to="/build/agents/new">
                <Button variant="primary"><Plus size={15} /> New Agent</Button>
              </Link>
            )}
          </div>
        }
      />

      {tab === 'intent' ? (
        <IntentTester />
      ) : isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => <SkeletonBlock key={i} h={190} className="rounded-2xl" />)}
        </div>
      ) : (
      <AsyncState error={error}>
        {agents.length === 0 ? (
          <EmptyState
            icon={<Bot size={22} />}
            title="No agents yet"
            hint="Agents are voice personas defined in YAML. Build one, try phrases against it in the Intent tester tab, then Activate it on one of your numbers."
            action={
              <Link to="/build/agents/new">
                <Button variant="primary" size="sm"><Plus size={14} /> Create your first agent</Button>
              </Link>
            }
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {agents.map((a) => {
              const bound = boundTo(a.name)
              const activated = bound.length > 0
              return (
              <Card key={a.name} className="flex flex-col gap-3 p-5" hover glow={activated ? 'mint' : undefined}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <h3 className="font-display text-[16px] font-bold tracking-tight truncate">
                        {a.agent_name || a.name}
                      </h3>
                      {activated && (
                        <Badge tone="mint" soft>
                          <span className="h-1.5 w-1.5 rounded-full bg-mint-400 animate-pulse-soft" />
                          <CheckCircle2 size={12} /> Activated
                        </Badge>
                      )}
                      {isAdmin && a.active && (
                        <span title="Answers calls that arrive with no number→agent binding (active_agent.json)">
                          <Badge tone="indigo" soft>fallback</Badge>
                        </span>
                      )}
                    </div>
                    <div className="truncate text-[12px] text-dim">{a.company || '—'}</div>
                  </div>
                  <code title={a.name} className="max-w-[45%] truncate rounded-md bg-[var(--surface-2)] px-2 py-0.5 font-mono text-[11px] text-soft ring-1 ring-[var(--border)]">
                    {a.name}
                    {a.version && <span className="ml-1 text-[10px] text-dim">v{a.version}</span>}
                  </code>
                </div>

                {a.error ? (
                  <div role="alert" className="rounded-lg bg-rose-500/10 px-3 py-2 text-[12px] text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">
                    Invalid YAML: {a.error}
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-1.5">
                    {(a.languages ?? []).map((l) => (
                      <span key={l} className="inline-flex items-center gap-1 rounded-md bg-[var(--surface-2)] px-2 py-0.5 text-[11px] font-semibold text-soft ring-1 ring-[var(--border)]">
                        <Languages size={11} /> {l}
                      </span>
                    ))}
                    <span
                      title={(a.stages ?? []).join(' → ')}
                      className="inline-flex items-center gap-1 rounded-md bg-[var(--surface-2)] px-2 py-0.5 text-[11px] font-semibold text-soft ring-1 ring-[var(--border)]"
                    >
                      <GitBranch size={11} /> {(a.stages ?? []).length} stages
                    </span>
                    <span
                      className={
                        a.call_direction === 'outbound'
                          ? 'inline-flex items-center gap-1 rounded-md bg-indigo-500/12 px-2 py-0.5 text-[11px] font-semibold text-indigo-600 ring-1 ring-indigo-500/30 dark:text-indigo-300'
                          : 'inline-flex items-center gap-1 rounded-md bg-mint-400/10 px-2 py-0.5 text-[11px] font-semibold text-mint-700 ring-1 ring-mint-400/30 dark:text-mint-300'
                      }
                    >
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

                <div className="mt-1 flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-3">
                  {activated ? (
                    <>
                      <span
                        title={`Answering calls on ${bound.map((n) => n.number).join(', ')}`}
                        className="inline-flex max-w-full items-center gap-1.5 rounded-xl bg-mint-400/10 px-3.5 py-2 text-[13px] font-semibold text-mint-700 ring-1 ring-mint-400/30 dark:text-mint-300"
                      >
                        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-mint-400 animate-pulse-soft" />
                        <Radio size={14} className="shrink-0" />
                        <span className="truncate">Live on {bound.map((n) => n.number).join(', ')}</span>
                      </span>
                      <Button variant="ghost" size="sm"
                        onClick={() => setActivating({ slug: a.name, title: a.agent_name || a.name })}>
                        Change number
                      </Button>
                    </>
                  ) : (
                    <Button variant="primary"
                      onClick={() => setActivating({ slug: a.name, title: a.agent_name || a.name })}
                      disabled={!!a.error}>
                      <Radio size={14} /> Activate
                    </Button>
                  )}
                  {isAdmin && !a.active && (
                    <Button variant="ghost" size="sm" onClick={() => onSetFallback(a.name)}
                      disabled={busy === a.name || !!a.error}
                      title="Admin: make this the platform fallback agent (answers calls with no number binding)">
                      {busy === a.name ? <Spinner className="h-3 w-3" /> : 'Set fallback'}
                    </Button>
                  )}
                  <Link to={`/build/agents/${a.name}`} className="ml-auto">
                    <Button variant="ghost"><Pencil size={14} /> Edit</Button>
                  </Link>
                  {confirmArchive === a.name ? (
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => onArchive(a.name)}
                      disabled={busy === a.name}
                      title={`"${a.name}" will be moved to agents/_archived and stop being selectable.`}
                    >
                      Archive "{a.name}"?
                    </Button>
                  ) : (
                    <Button variant="ghost" onClick={() => requestArchive(a.name)} disabled={busy === a.name} title="Archive agent">
                      {busy === a.name ? <Spinner className="h-3.5 w-3.5" /> : <Archive size={14} />}
                    </Button>
                  )}
                </div>
              </Card>
              )
            })}
          </div>
        )}
      </AsyncState>
      )}

      {tab === 'agents' && (
        <div className="flex items-start gap-2 rounded-xl bg-indigo-500/10 px-4 py-3 ring-1 ring-indigo-500/20">
          <Info size={14} className="mt-0.5 shrink-0 text-indigo-500" />
          <p className="text-[12px] text-soft">
            <span className="font-semibold">Activating</span> connects an agent to one of your phone
            numbers — it then answers every call on that number. Manage your numbers in{' '}
            <Link to="/telephony" className="font-semibold text-indigo-600 hover:underline dark:text-indigo-300">Numbers (BYON)</Link>
            {isAdmin && <>; the <span className="font-semibold">fallback</span> agent (admin-only) answers calls that arrive with no number binding</>}.
          </p>
        </div>
      )}

      {activating && (
        <ActivateModal
          slug={activating.slug}
          title={activating.title}
          numbers={numbers}
          busyId={bindBusyId}
          onConnect={connectNumber}
          onDisconnect={disconnectNumber}
          onClose={() => setActivating(null)}
        />
      )}
    </div>
  )
}
