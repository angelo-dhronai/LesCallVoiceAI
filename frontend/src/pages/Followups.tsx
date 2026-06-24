import { useState } from 'react'
import { Eye, Zap, Trash2, Plus } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { post, patch, del } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, cx } from '../components/ui'
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

const ROW_STATUSES: RowStatus[] = ['pending', 'completed', 'missed', 'dismissed']

function formatDate(dt: string | null | undefined): string {
  if (!dt) return '—'
  const d = new Date(dt)
  if (isNaN(d.getTime())) return dt
  return d.toLocaleString()
}

function sourceLabel(s: string | null | undefined): string {
  return (s ?? '').replace(/_/g, ' ') || '—'
}

export default function Followups() {
  const toast = useToast()
  const qc = useQueryClient()
  const [status, setStatus] = useState<StatusFilter>('all')
  const [addOpen, setAddOpen] = useState(false)
  const [detail, setDetail] = useState<Followup | null>(null)

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
    try {
      await patch(`/followups/${row.id}`, { status: newVal })
      toast('Status updated', 'success')
      invalidate()
    } catch {
      toast('Failed to update status', 'error')
    }
  }

  async function automate(row: Followup) {
    try {
      await post('/followup-automation', { followup_id: row.id })
      toast('Scheduled', 'success')
      invalidate()
    } catch {
      toast('Already scheduled or failed', 'error')
    }
  }

  async function remove(row: Followup) {
    if (row.source === 'manual' && row._manual_id) {
      if (!window.confirm('Delete this manual follow-up?')) return
      try {
        await del(`/followups/manual/${row._manual_id}`)
        toast('Follow-up deleted', 'success')
        invalidate()
      } catch {
        toast('Failed to delete', 'error')
      }
      return
    }
    try {
      await patch(`/followups/${row.id}`, { status: 'dismissed' })
      toast('Follow-up dismissed', 'success')
      invalidate()
    } catch {
      toast('Failed to dismiss', 'error')
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
        actions={<Button onClick={openAdd}><Plus size={14} /> Add follow-up</Button>}
      />

      <div>
        <Segmented options={STATUS_OPTIONS} value={status} onChange={setStatus} />
      </div>

      <Card className="p-5">
        <AsyncState
          loading={isLoading}
          error={error}
          empty={followups.length === 0}
          emptyLabel="No follow-ups yet"
        >
          <Table>
            <THead>
              <TH>Contact</TH>
              <TH>Required</TH>
              <TH>Date</TH>
              <TH>Priority</TH>
              <TH>Source</TH>
              <TH>Status</TH>
              <TH className="text-right">Actions</TH>
            </THead>
            <tbody>
              {followups.map((r) => (
                <TR key={r?.id || r?._manual_id || Math.random()}>
                  <TD>
                    <div className="font-semibold text-[var(--text)]">{r?.contact_name || '—'}</div>
                    <div className="text-[12px] text-dim">{r?.contact_phone || '—'}</div>
                  </TD>
                  <TD>{r?.follow_up_required ? 'Yes' : 'No'}</TD>
                  <TD className="whitespace-nowrap text-dim">{formatDate(r?.follow_up_date)}</TD>
                  <TD><StatusBadge value={r?.priority} /></TD>
                  <TD><Badge tone="neutral">{sourceLabel(r?.source)}</Badge></TD>
                  <TD>
                    <Select
                      value={r?.status ?? 'pending'}
                      onChange={(e) => changeStatus(r, e.target.value as RowStatus)}
                    >
                      {ROW_STATUSES.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </Select>
                  </TD>
                  <TD>
                    <div className="flex items-center justify-end gap-1.5">
                      <Button variant="ghost" size="sm" title="View" onClick={() => setDetail(r)}>
                        <Eye size={14} />
                      </Button>
                      <Button variant="soft" size="sm" title="Automate" onClick={() => automate(r)}>
                        <Zap size={14} />
                      </Button>
                      <Button variant="danger" size="sm" title="Delete / Dismiss" onClick={() => remove(r)}>
                        <Trash2 size={14} />
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
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
            <Button onClick={submitAdd} disabled={submitting || !fPhone.trim()}>Add</Button>
          </>
        }
      >
        <div className="space-y-3">
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
            <Input type="date" value={fDate} onChange={(e) => setFDate(e.target.value)} />
          </Field>
          <Field label="Priority">
            <Select
              className="w-full"
              value={fPriority}
              onChange={(e) => setFPriority(e.target.value as Priority)}
            >
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </Select>
          </Field>
        </div>
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
            <DetailRow label="Reason" value={detail.reason || '—'} />
            <DetailRow label="Follow-up date" value={formatDate(detail.follow_up_date)} />
            <DetailRow label="Priority"><StatusBadge value={detail.priority} /></DetailRow>
            <DetailRow label="Status"><StatusBadge value={detail.status} /></DetailRow>
            <DetailRow label="Source"><Badge tone="neutral">{sourceLabel(detail.source)}</Badge></DetailRow>
            <DetailRow label="Updated" value={formatDate(detail.updated_at)} />
            {detail.call_id && <DetailRow label="Call ID" value={detail.call_id} mono />}
            {detail.folder && <DetailRow label="Folder" value={detail.folder} mono />}
          </dl>
        )}
      </Modal>
    </div>
  )
}

function DetailRow({
  label, value, children, mono,
}: { label: string; value?: string; children?: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-[var(--border)] pb-2">
      <dt className="shrink-0 text-[12px] font-semibold text-dim">{label}</dt>
      <dd className={cx('text-right text-soft', mono && 'font-mono text-[12px]')}>
        {children ?? value}
      </dd>
    </div>
  )
}
