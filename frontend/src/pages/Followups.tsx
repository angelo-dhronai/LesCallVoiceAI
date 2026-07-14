import { useState } from 'react'
import { BellOff, CalendarClock, Copy, Eye, Plus, RefreshCw, Trash2, Zap } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { post, patch, del } from '../api/mutate'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, EmptyState, Spinner, cx } from '../components/ui'
import {
  PageHeader, Table, THead, TH, TR, TD, StatusBadge, Select, Segmented, Input, Textarea, Field, AsyncState,
} from '../components/kit'
import { Modal } from '../components/Modal'

type Followup = {
  id: string
  source: 'outbound' | 'ai_analysis' | 'manual'
  contact_name?: string | null
  contact_phone?: string | null
  follow_up_required: boolean
  reason?: string | null
  follow_up_date: string | null
  priority: 'high' | 'medium' | 'low'
  status: 'pending' | 'completed' | 'missed' | 'dismissed'
  updated_at?: string | null
  folder?: string | null
  call_id?: string | null
  _manual_id?: string
}

type FollowupsResponse = { followups: Followup[]; total: number }

type StatusFilter = 'all' | 'pending' | 'completed' | 'missed' | 'dismissed'
type RowStatus = 'pending' | 'completed' | 'missed' | 'dismissed'
type Priority = 'high' | 'medium' | 'low'

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'completed', label: 'Completed' },
  { value: 'missed', label: 'Missed' },
  { value: 'dismissed', label: 'Dismissed' },
]

const PRIORITY_OPTIONS: { value: Priority; label: string }[] = [
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]

const ROW_STATUSES: RowStatus[] = ['pending', 'completed', 'missed', 'dismissed']

const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/

function parseWhen(dt: string | null | undefined): Date | null {
  if (!dt) return null
  const d = DATE_ONLY_RE.test(dt) ? new Date(`${dt}T00:00:00`) : new Date(dt)
  return isNaN(d.getTime()) ? null : d
}

function formatDate(dt: string | null | undefined): string {
  if (!dt) return '—'
  const d = parseWhen(dt)
  if (!d) return dt
  if (DATE_ONLY_RE.test(dt)) {
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
  }
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** Whole days from today (midnight-to-midnight); negative = past. */
function dayDiff(dt: string | null | undefined): number | null {
  const d = parseWhen(dt)
  if (!d) return null
  const target = new Date(d)
  target.setHours(0, 0, 0, 0)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((target.getTime() - today.getTime()) / 86400000)
}

function isOverdue(r: Followup): boolean {
  if (r?.status !== 'pending') return false
  const days = dayDiff(r?.follow_up_date)
  return days !== null && days < 0
}

function DueBadge({ row }: { row: Followup }) {
  if (row?.status !== 'pending') return null
  const days = dayDiff(row?.follow_up_date)
  if (days === null) return null
  if (days < 0) return <Badge tone="rose">{Math.abs(days)}d overdue</Badge>
  if (days === 0) return <Badge tone="amber">today</Badge>
  if (days === 1) return <Badge tone="indigo">tomorrow</Badge>
  return null
}

function sourceLabel(s: string | null | undefined): string {
  return (s ?? '').replace(/_/g, ' ') || '—'
}

export default function Followups() {
  const toast = useToast()
  const qc = useQueryClient()
  const refresh = useRefresh()
  const [status, setStatus] = useState<StatusFilter>('all')
  const [addOpen, setAddOpen] = useState(false)
  const [detail, setDetail] = useState<Followup | null>(null)
  const [confirmRow, setConfirmRow] = useState<Followup | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  // Add-form state
  const [fPhone, setFPhone] = useState('')
  const [fName, setFName] = useState('')
  const [fReason, setFReason] = useState('')
  const [fDate, setFDate] = useState('')
  const [fPriority, setFPriority] = useState<Priority>('medium')
  const [submitting, setSubmitting] = useState(false)

  const { data, isLoading, error } = useGet<FollowupsResponse>(
    ['followups', status],
    `/followups?status=${status}`,
  )

  const followups = Array.isArray(data?.followups) ? data!.followups : []

  const pendingCount = followups.filter((r) => r?.status === 'pending').length
  const overdueCount = followups.filter(isOverdue).length
  const highCount = followups.filter((r) => r?.status === 'pending' && r?.priority === 'high').length

  const invalidate = () => qc.invalidateQueries({ queryKey: ['followups'] })

  function openAdd() {
    setFPhone('')
    setFName('')
    setFReason('')
    setFDate('')
    setFPriority('medium')
    setAddOpen(true)
  }

  async function changeStatus(row: Followup, newVal: RowStatus) {
    // Optimistic update so the Select doesn't snap back while the refetch is in flight
    qc.setQueryData<FollowupsResponse>(['followups', status], (old) =>
      old && ({ ...old, followups: old.followups.map((f) => (f.id === row.id ? { ...f, status: newVal } : f)) }))
    setBusyId(row.id)
    try {
      await patch(`/followups/${row.id}`, { status: newVal })
      toast('Status updated', 'success')
    } catch {
      toast('Failed to update status', 'error')
    } finally {
      setBusyId(null)
      invalidate()
    }
  }

  async function automate(row: Followup) {
    setBusyId(row.id)
    try {
      await post('/followup-automation', { followup_id: row.id })
      toast('Automated call scheduled', 'success')
      invalidate()
    } catch (err) {
      toast((err as Error)?.message || 'Could not schedule — it may already be queued', 'error')
    } finally {
      setBusyId(null)
    }
  }

  async function deleteManual(row: Followup) {
    if (!row._manual_id) return
    setBusyId(row.id)
    try {
      await del(`/followups/manual/${row._manual_id}`)
      toast('Follow-up deleted', 'success')
      invalidate()
    } catch {
      toast('Failed to delete', 'error')
    } finally {
      setBusyId(null)
      setConfirmRow(null)
    }
  }

  async function remove(row: Followup) {
    if (row.source === 'manual' && row._manual_id) {
      setConfirmRow(row)
      return
    }
    setBusyId(row.id)
    try {
      await patch(`/followups/${row.id}`, { status: 'dismissed' })
      toast('Follow-up dismissed', 'success')
      invalidate()
    } catch {
      toast('Failed to dismiss', 'error')
    } finally {
      setBusyId(null)
    }
  }

  async function submitAdd() {
    const phone = fPhone.trim()
    if (!phone) {
      toast('Contact phone is required', 'warn')
      return
    }
    setSubmitting(true)
    try {
      await post('/followups/manual', {
        contact_phone: phone,
        contact_name: fName.trim() || undefined,
        reason: fReason.trim() || undefined,
        follow_up_date: fDate || undefined,
        priority: fPriority,
      })
      toast('Follow-up added', 'success')
      setAddOpen(false)
      invalidate()
    } catch {
      toast('Failed to add follow-up', 'error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Follow-Up Tracker"
        sub="Pending and completed follow-ups"
        actions={
          <>
            <Button variant="ghost" size="sm" onClick={() => refresh(['followups'])}>
              <RefreshCw size={14} /> Refresh
            </Button>
            <Button onClick={openAdd}><Plus size={14} /> Add follow-up</Button>
          </>
        }
      />

      {status === 'all' && followups.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Card className="p-4">
            <div className="eyebrow">Pending</div>
            <div className="metric mt-1 text-xl tabular-nums">{pendingCount.toLocaleString('en-IN')}</div>
          </Card>
          <Card className="p-4">
            <div className="eyebrow">Overdue</div>
            <div className={cx('metric mt-1 text-xl tabular-nums', overdueCount > 0 && 'text-rose-600 dark:text-rose-300')}>
              {overdueCount.toLocaleString('en-IN')}
            </div>
          </Card>
          <Card className="p-4">
            <div className="eyebrow">High priority</div>
            <div className="metric mt-1 text-xl tabular-nums">{highCount.toLocaleString('en-IN')}</div>
          </Card>
        </div>
      )}

      <Card className="p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <Segmented options={STATUS_OPTIONS} value={status} onChange={setStatus} />
          <div className="text-[12px] tabular-nums text-dim">
            {data ? `${data.total.toLocaleString('en-IN')} ${status === 'all' ? 'follow-ups' : status}` : ''}
          </div>
        </div>
        <AsyncState loading={isLoading} error={error}>
          {followups.length === 0 ? (
            <EmptyState
              icon={<CalendarClock size={22} />}
              title={status === 'all' ? 'No follow-ups yet' : `No ${status} follow-ups`}
              hint="Follow-ups are created automatically when AI call analysis flags a callback — or add one manually."
              action={(status === 'all' || status === 'pending')
                ? <Button size="sm" onClick={openAdd}><Plus size={14} /> Add follow-up</Button>
                : undefined}
            />
          ) : (
            <Table>
              <THead>
                <TH>Contact</TH>
                <TH>Required</TH>
                <TH>Date</TH>
                <TH>Reason</TH>
                <TH>Priority</TH>
                <TH>Source</TH>
                <TH>Status</TH>
                <TH className="text-right">Actions</TH>
              </THead>
              <tbody>
                {followups.map((r, idx) => {
                  const overdue = isOverdue(r)
                  const manual = r?.source === 'manual'
                  const busy = busyId === r?.id
                  return (
                    <TR key={`${r?.id ?? r?._manual_id ?? idx}`} className={cx(overdue && 'bg-rose-500/[.04]')}>
                      <TD>
                        <div className="font-semibold text-[var(--text)]">{r?.contact_name || '—'}</div>
                        <div className="text-[12px] text-dim">{r?.contact_phone || '—'}</div>
                      </TD>
                      <TD><Badge tone={r?.follow_up_required ? 'mint' : 'neutral'}>{r?.follow_up_required ? 'Yes' : 'No'}</Badge></TD>
                      <TD className="whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <span className={cx('text-dim', overdue && 'font-medium text-rose-600 dark:text-rose-300')}>
                            {formatDate(r?.follow_up_date)}
                          </span>
                          <DueBadge row={r} />
                        </div>
                      </TD>
                      <TD className="max-w-[240px]">
                        <div className="truncate text-dim" title={r?.reason ?? ''}>{r?.reason || '—'}</div>
                      </TD>
                      <TD><StatusBadge value={r?.priority} /></TD>
                      <TD><Badge tone="neutral">{sourceLabel(r?.source)}</Badge></TD>
                      <TD>
                        <Select
                          value={r?.status ?? 'pending'}
                          disabled={busy}
                          aria-label={`Change status for ${r?.contact_name || r?.contact_phone || 'follow-up'}`}
                          onChange={(e) => changeStatus(r, e.target.value as RowStatus)}
                        >
                          {ROW_STATUSES.map((s) => (
                            <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>
                          ))}
                        </Select>
                      </TD>
                      <TD>
                        <div className="flex items-center justify-end gap-2">
                          <Button variant="ghost" size="sm" title="View details" className="min-h-9 min-w-9" onClick={() => setDetail(r)}>
                            <Eye size={14} />
                          </Button>
                          <Button variant="soft" size="sm" title="Schedule automated AI callback" className="min-h-9 min-w-9" disabled={busy} onClick={() => automate(r)}>
                            {busy ? <Spinner className="h-3.5 w-3.5" /> : <Zap size={14} />}
                          </Button>
                          <Button
                            variant="danger" size="sm" disabled={busy}
                            title={manual ? 'Delete permanently' : 'Dismiss — keeps the record'}
                            className="min-h-9 min-w-9"
                            onClick={() => remove(r)}
                          >
                            {manual ? <Trash2 size={14} /> : <BellOff size={14} />}
                          </Button>
                        </div>
                      </TD>
                    </TR>
                  )
                })}
              </tbody>
            </Table>
          )}
        </AsyncState>
      </Card>

      {/* Add modal */}
      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add follow-up"
        footer={
          <>
            <Button variant="ghost" onClick={() => setAddOpen(false)}>Cancel</Button>
            <Button onClick={submitAdd} disabled={submitting || !fPhone.trim()}>
              {submitting ? <><Spinner className="h-3.5 w-3.5" /> Adding…</> : 'Add'}
            </Button>
          </>
        }
      >
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submitAdd() }}>
          <Field label="Contact phone *">
            <Input value={fPhone} onChange={(e) => setFPhone(e.target.value)} placeholder="+91…" />
          </Field>
          <Field label="Contact name">
            <Input value={fName} onChange={(e) => setFName(e.target.value)} placeholder="Optional" />
          </Field>
          <Field label="Reason">
            <Textarea value={fReason} onChange={(e) => setFReason(e.target.value)} rows={3} placeholder="Optional" />
          </Field>
          <Field label="Follow-up date">
            <Input type="date" min={new Date().toISOString().slice(0, 10)} value={fDate} onChange={(e) => setFDate(e.target.value)} />
          </Field>
          <div>
            <span className="mb-1 block text-[12px] font-semibold text-soft">Priority</span>
            <Segmented options={PRIORITY_OPTIONS} value={fPriority} onChange={setFPriority} />
          </div>
          <button type="submit" className="hidden" aria-hidden="true" tabIndex={-1} />
        </form>
      </Modal>

      {/* Delete confirmation modal (manual follow-ups) */}
      <Modal
        open={confirmRow !== null}
        onClose={() => setConfirmRow(null)}
        title="Delete follow-up?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmRow(null)}>Cancel</Button>
            <Button variant="danger" disabled={busyId !== null} onClick={() => confirmRow && deleteManual(confirmRow)}>
              {busyId !== null ? <><Spinner className="h-3.5 w-3.5" /> Deleting…</> : 'Delete'}
            </Button>
          </>
        }
      >
        {confirmRow && (
          <p className="text-[13px] text-soft">
            This permanently deletes the manual follow-up for{' '}
            <span className="font-semibold text-[var(--text)]">{confirmRow.contact_name || confirmRow.contact_phone || 'this contact'}</span>.
            This cannot be undone.
          </p>
        )}
      </Modal>

      {/* Detail modal */}
      <Modal
        open={detail !== null}
        onClose={() => setDetail(null)}
        title="Follow-up detail"
        footer={<Button variant="ghost" onClick={() => setDetail(null)}>Close</Button>}
      >
        {detail && (
          <dl className="space-y-3 text-[13px]">
            <DetailRow label="Contact name" value={detail.contact_name || '—'} />
            <DetailRow label="Contact phone" value={detail.contact_phone || '—'} />
            <DetailRow label="Required" value={detail.follow_up_required ? 'Yes' : 'No'} />
            <DetailRow label="Reason" block value={detail.reason || '—'} />
            <DetailRow label="Follow-up date" value={formatDate(detail.follow_up_date)} />
            <DetailRow label="Priority"><StatusBadge value={detail.priority} /></DetailRow>
            <DetailRow label="Status"><StatusBadge value={detail.status} /></DetailRow>
            <DetailRow label="Source"><Badge tone="neutral">{sourceLabel(detail.source)}</Badge></DetailRow>
            <DetailRow label="Updated" value={formatDate(detail.updated_at)} />
            {detail.call_id && (
              <DetailRow label="Call ID" mono>
                <span className="inline-flex items-center gap-1.5">
                  <span className="break-all">{detail.call_id}</span>
                  <Button
                    variant="ghost" size="sm" title="Copy call ID" className="!p-1.5 min-h-9 min-w-9"
                    onClick={() => {
                      navigator.clipboard.writeText(detail.call_id!)
                      toast('Call ID copied', 'success')
                    }}
                  >
                    <Copy size={12} />
                  </Button>
                </span>
              </DetailRow>
            )}
            {detail.folder && <DetailRow label="Folder" value={detail.folder} mono />}
          </dl>
        )}
      </Modal>
    </div>
  )
}

function DetailRow({
  label, value, children, mono, block,
}: { label: string; value?: string; children?: React.ReactNode; mono?: boolean; block?: boolean }) {
  if (block) {
    return (
      <div className="border-b border-[var(--border)] pb-2">
        <dt className="text-[12px] font-semibold text-dim">{label}</dt>
        <dd className={cx('mt-1 text-left text-soft', mono && 'break-all font-mono text-[12px]')}>
          {children ?? value}
        </dd>
      </div>
    )
  }
  return (
    <div className="flex items-start justify-between gap-4 border-b border-[var(--border)] pb-2">
      <dt className="shrink-0 text-[12px] font-semibold text-dim">{label}</dt>
      <dd className={cx('min-w-0 text-right text-soft', mono && 'break-all font-mono text-[12px]')}>
        {children ?? value}
      </dd>
    </div>
  )
}
