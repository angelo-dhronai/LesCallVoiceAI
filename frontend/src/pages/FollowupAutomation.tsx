import { Fragment, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Phone, Trash2, Bot, ChevronRight, RefreshCw, PhoneForwarded } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { post, patch, del } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, Spinner, EmptyState, cx } from '../components/ui'
import {
  PageHeader, Table, THead, TH, TR, TD, StatusBadge, Input, Textarea, AsyncState,
} from '../components/kit'

type Status = 'scheduled' | 'calling' | 'called' | 'cancelled' | 'failed'

type Automation = {
  id: string
  source_id: string
  contact_name: string
  contact_phone: string
  past_call_context: string
  call_aim: string
  script_notes: string
  scheduled_time: string | null
  status: Status
  created_at: string
  called_at: string | null
  call_outcome: string | null
}

type Response = {
  automations: Automation[]
  total: number
}

/** ISO → 'YYYY-MM-DDTHH:mm' for <input type="datetime-local">, guarded. */
function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** Relative delta like 'in 2 hours' / '3 days ago'. */
function relTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const diffMs = d.getTime() - Date.now()
  const abs = Math.abs(diffMs)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  if (abs < 60_000) return rtf.format(Math.round(diffMs / 1000), 'second')
  if (abs < 3_600_000) return rtf.format(Math.round(diffMs / 60_000), 'minute')
  if (abs < 86_400_000) return rtf.format(Math.round(diffMs / 3_600_000), 'hour')
  return rtf.format(Math.round(diffMs / 86_400_000), 'day')
}

function isOverdue(a: Automation): boolean {
  if (a.status !== 'scheduled' || !a.scheduled_time) return false
  const d = new Date(a.scheduled_time)
  return !isNaN(d.getTime()) && d.getTime() < Date.now()
}

function truncate(s: string | null | undefined, n = 80): string {
  const v = s ?? ''
  return v.length > n ? `${v.slice(0, n)}…` : v
}

const STATUS_ORDER: Status[] = ['scheduled', 'calling', 'called', 'failed', 'cancelled']

/** Editable input wrapper that shows a saving spinner overlay. */
function SavingWrap({ saving, children }: { saving: boolean; children: ReactNode }) {
  return (
    <div className="relative">
      {children}
      {saving && <Spinner className="absolute right-2 top-2.5 h-3 w-3" />}
    </div>
  )
}

export default function FollowupAutomation() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data, isLoading, error } = useGet<Response>(
    ['followup-automation'],
    '/followup-automation',
    { refetchInterval: 5000 },
  )

  const rows: Automation[] = Array.isArray(data?.automations) ? data!.automations : []

  const [statusFilter, setStatusFilter] = useState<'all' | Status>('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [confirmKey, setConfirmKey] = useState<string | null>(null)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const confirmTimer = useRef<number | null>(null)

  useEffect(() => () => {
    if (confirmTimer.current) window.clearTimeout(confirmTimer.current)
  }, [])

  function armConfirm(key: string) {
    setConfirmKey(key)
    if (confirmTimer.current) window.clearTimeout(confirmTimer.current)
    confirmTimer.current = window.setTimeout(() => setConfirmKey(null), 4000)
  }

  const counts = rows.reduce((m, r) => {
    m[r.status] = (m[r.status] ?? 0) + 1
    return m
  }, {} as Record<Status, number>)

  const filtered = statusFilter === 'all' ? rows : rows.filter((r) => r.status === statusFilter)

  const invalidate = () => qc.invalidateQueries({ queryKey: ['followup-automation'] })

  async function doPatch(id: string, body: Record<string, unknown>, field: string) {
    setSavingKey(`${id}:${field}`)
    try {
      await patch(`/followup-automation/${id}`, body)
      toast('Follow-up updated', 'success')
      invalidate()
    } catch (e) {
      toast(`Update failed: ${String(e)}`, 'error')
    } finally {
      setSavingKey(null)
    }
  }

  async function doCallNow(id: string) {
    setBusyId(id)
    try {
      await post(`/followup-automation/${id}/call-now`)
      toast('Placing call now…', 'success')
      invalidate()
    } catch (e) {
      toast(`Call failed: ${String(e)}`, 'error')
    } finally {
      setBusyId(null)
    }
  }

  async function doDelete(id: string) {
    setBusyId(id)
    try {
      await del(`/followup-automation/${id}`)
      toast('Follow-up deleted', 'success')
      invalidate()
    } catch (e) {
      toast(`Delete failed: ${String(e)}`, 'error')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Follow-Up Automation"
        sub={`Scheduled AI follow-up calls${data ? ` · ${data.total} total` : ''}`}
        actions={
          <>
            <Badge tone="mint">
              <span className="h-1.5 w-1.5 rounded-full bg-mint-400 animate-pulse-soft" />
              Auto-refreshing
            </Badge>
            <Button variant="ghost" size="sm" title="Refresh" onClick={invalidate}>
              <RefreshCw size={14} /> Refresh
            </Button>
          </>
        }
      />

      {rows.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setStatusFilter('all')}
            className={cx(
              'inline-flex min-h-9 items-center gap-1.5 rounded-xl px-3 py-1.5 text-[12px] font-semibold ring-1 ring-[var(--border)] bg-[var(--surface-2)] transition hover:ring-mint-400/40',
              statusFilter === 'all' && 'ring-mint-400/60 bg-mint-400/10',
            )}
          >
            All
            <span className="tabular-nums text-dim">{data?.total ?? rows.length}</span>
          </button>
          {STATUS_ORDER.filter((s) => counts[s]).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(statusFilter === s ? 'all' : s)}
              className={cx(
                'inline-flex min-h-9 items-center gap-1.5 rounded-xl px-3 py-1.5 text-[12px] font-semibold ring-1 ring-[var(--border)] bg-[var(--surface-2)] transition hover:ring-mint-400/40',
                statusFilter === s && 'ring-mint-400/60 bg-mint-400/10',
              )}
            >
              <StatusBadge value={s} />
              <span className="tabular-nums text-dim">{counts[s]}</span>
            </button>
          ))}
        </div>
      )}

      <Card className="p-5">
        <AsyncState loading={isLoading} error={error}>
          {rows.length === 0 ? (
            <EmptyState
              icon={<PhoneForwarded size={20} />}
              title="No follow-ups scheduled"
              hint="When the AI agent flags a call for a callback, it lands here with the past-call context and a suggested time — you can edit the aim and script before it dials."
            />
          ) : filtered.length === 0 ? (
            <EmptyState
              title={`No “${statusFilter}” follow-ups`}
              hint="Nothing matches the current status filter."
              action={<Button variant="ghost" size="sm" onClick={() => setStatusFilter('all')}>Show all</Button>}
            />
          ) : (
            <>
              <p className="mb-3 text-[11px] text-dim">
                Rows in “scheduled” status are editable — changes save when you click away. Click a row to see full details.
              </p>
              <Table>
                <THead>
                  <TH className="sticky left-0 z-10 bg-[var(--surface-solid)] shadow-[1px_0_0_var(--border)]">Contact</TH>
                  <TH>Past Call Context</TH>
                  <TH>Call Aim</TH>
                  <TH>Script Notes</TH>
                  <TH>Scheduled Time</TH>
                  <TH>Status</TH>
                  <TH>Outcome</TH>
                  <TH>Actions</TH>
                </THead>
                <tbody>
                  {filtered.map((a) => {
                    const editable = a.status === 'scheduled'
                    const open = expandedId === a.id
                    const busy = busyId === a.id
                    const callKey = `${a.id}:call`
                    const deleteKey = `${a.id}:delete`
                    return (
                      <Fragment key={a.id}>
                        <TR
                          onClick={() => setExpandedId(open ? null : a.id)}
                          className={cx(a.status === 'calling' && 'bg-indigo-500/5')}
                        >
                          <TD className="sticky left-0 z-10 max-w-[160px] bg-[var(--surface-solid)] shadow-[1px_0_0_var(--border)]">
                            <div className="flex items-center gap-2">
                              <ChevronRight size={14} className={cx('shrink-0 text-dim transition-transform', open && 'rotate-90')} />
                              <div className="min-w-0">
                                <div className="truncate font-semibold text-[var(--text)]">{a.contact_name || '—'}</div>
                                <div className="truncate text-[12px] text-dim">{a.contact_phone || '—'}</div>
                              </div>
                            </div>
                          </TD>

                          <TD className="max-w-[220px]">
                            <span title={a.past_call_context || ''}>{truncate(a.past_call_context) || '—'}</span>
                          </TD>

                          <TD className="min-w-[160px]">
                            {editable ? (
                              <div onClick={(e) => e.stopPropagation()}>
                                <SavingWrap saving={savingKey === `${a.id}:call_aim`}>
                                  <Input
                                    defaultValue={a.call_aim ?? ''}
                                    aria-label="Call aim"
                                    onBlur={(e) => {
                                      const v = e.target.value
                                      if (v !== (a.call_aim ?? '')) doPatch(a.id, { call_aim: v }, 'call_aim')
                                    }}
                                  />
                                </SavingWrap>
                              </div>
                            ) : (
                              <span>{a.call_aim || '—'}</span>
                            )}
                          </TD>

                          <TD className="min-w-[200px]">
                            {editable ? (
                              <div onClick={(e) => e.stopPropagation()}>
                                <SavingWrap saving={savingKey === `${a.id}:script_notes`}>
                                  <Textarea
                                    rows={2}
                                    defaultValue={a.script_notes ?? ''}
                                    aria-label="Script notes"
                                    onBlur={(e) => {
                                      const v = e.target.value
                                      if (v !== (a.script_notes ?? '')) doPatch(a.id, { script_notes: v }, 'script_notes')
                                    }}
                                  />
                                </SavingWrap>
                              </div>
                            ) : (
                              <span>{a.script_notes || '—'}</span>
                            )}
                          </TD>

                          <TD className="min-w-[180px]">
                            {editable ? (
                              <div onClick={(e) => e.stopPropagation()}>
                                <SavingWrap saving={savingKey === `${a.id}:scheduled_time`}>
                                  <Input
                                    type="datetime-local"
                                    defaultValue={toLocalInput(a.scheduled_time)}
                                    aria-label="Scheduled time"
                                    onBlur={(e) => {
                                      const v = e.target.value
                                      if (!v || v === toLocalInput(a.scheduled_time)) return
                                      const d = new Date(v)
                                      if (isNaN(d.getTime())) return
                                      doPatch(a.id, { scheduled_time: d.toISOString() }, 'scheduled_time')
                                    }}
                                  />
                                </SavingWrap>
                                <div className="mt-1 flex items-center gap-1.5 text-[11px] text-dim">
                                  {relTime(a.scheduled_time)}
                                  {isOverdue(a) && <Badge tone="amber">overdue</Badge>}
                                </div>
                              </div>
                            ) : (
                              <div>
                                <span>{formatTime(a.scheduled_time)}</span>
                                {a.scheduled_time && (
                                  <div className="text-[11px] text-dim">{relTime(a.scheduled_time)}</div>
                                )}
                              </div>
                            )}
                          </TD>

                          <TD>
                            <div className="flex items-center gap-1.5">
                              <StatusBadge value={a.status} />
                              {a.status === 'calling' && <Bot size={14} className="text-indigo-400 animate-pulse-soft" />}
                            </div>
                          </TD>

                          <TD className="max-w-[180px]">
                            <span title={a.call_outcome ?? ''}>{a.call_outcome || '—'}</span>
                          </TD>

                          <TD>
                            <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                              {a.status === 'scheduled' && (
                                <Button
                                  variant={confirmKey === callKey ? 'primary' : 'soft'}
                                  size="sm"
                                  className="min-h-9"
                                  title={confirmKey === callKey ? 'Click again to place a real outbound call now' : 'Call now'}
                                  disabled={busy}
                                  onClick={() => {
                                    if (confirmKey === callKey) {
                                      setConfirmKey(null)
                                      doCallNow(a.id)
                                    } else {
                                      armConfirm(callKey)
                                    }
                                  }}
                                >
                                  {busy ? <Spinner className="h-3.5 w-3.5" /> : <Phone size={14} />}
                                  {confirmKey === callKey ? 'Confirm call' : 'Call Now'}
                                </Button>
                              )}
                              <Button
                                variant="danger"
                                size="sm"
                                className="min-h-9 min-w-9"
                                title={confirmKey === deleteKey ? 'Click again to delete this follow-up' : 'Delete'}
                                disabled={busy}
                                onClick={() => {
                                  if (confirmKey === deleteKey) {
                                    setConfirmKey(null)
                                    doDelete(a.id)
                                  } else {
                                    armConfirm(deleteKey)
                                  }
                                }}
                              >
                                {busy ? <Spinner className="h-3.5 w-3.5" /> : <Trash2 size={14} />}
                                {confirmKey === deleteKey ? (
                                  'Confirm delete'
                                ) : (
                                  <span className="sr-only">Delete follow-up</span>
                                )}
                              </Button>
                            </div>
                          </TD>
                        </TR>

                        {open && (
                          <TR className="hover:!bg-transparent">
                            <TD colSpan={8}>
                              <div className="sticky left-0 grid max-w-[calc(100vw-5rem)] gap-4 p-2 sm:grid-cols-2">
                                <div>
                                  <div className="eyebrow mb-1">Past Call Context</div>
                                  <div className="whitespace-pre-wrap text-[13px] text-soft">{a.past_call_context || '—'}</div>
                                </div>
                                <div>
                                  <div className="eyebrow mb-1">Script Notes</div>
                                  <div className="whitespace-pre-wrap text-[13px] text-soft">{a.script_notes || '—'}</div>
                                </div>
                                <div>
                                  <div className="eyebrow mb-1">Outcome</div>
                                  <div className="whitespace-pre-wrap text-[13px] text-soft">{a.call_outcome || '—'}</div>
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                  <div>
                                    <div className="eyebrow mb-1">Created</div>
                                    <div className="text-[13px] text-soft">{formatTime(a.created_at)}</div>
                                  </div>
                                  <div>
                                    <div className="eyebrow mb-1">Called at</div>
                                    <div className="text-[13px] text-soft">{formatTime(a.called_at)}</div>
                                  </div>
                                </div>
                              </div>
                            </TD>
                          </TR>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </Table>
              <div className="mt-3 text-[12px] text-dim">
                {statusFilter === 'all'
                  ? `${(data?.total ?? rows.length).toLocaleString('en-IN')} total`
                  : `${filtered.length.toLocaleString('en-IN')} of ${(data?.total ?? rows.length).toLocaleString('en-IN')}`}
              </div>
            </>
          )}
        </AsyncState>
      </Card>
    </div>
  )
}
