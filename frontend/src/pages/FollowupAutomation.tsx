import { useState } from 'react'
import { Phone, Trash2, Bot } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { post, patch, del } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, cx } from '../components/ui'
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
  return d.toLocaleString()
}

function truncate(s: string | null | undefined, n = 80): string {
  const v = s ?? ''
  return v.length > n ? `${v.slice(0, n)}…` : v
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

  const invalidate = () => qc.invalidateQueries({ queryKey: ['followup-automation'] })

  async function doPatch(id: string, body: Record<string, unknown>) {
    try {
      await patch(`/followup-automation/${id}`, body)
      toast('Follow-up updated', 'success')
      invalidate()
    } catch (e) {
      toast(`Update failed: ${String(e)}`, 'error')
    }
  }

  async function doCallNow(id: string) {
    if (!confirm('This places a real outbound call now.')) return
    try {
      await post(`/followup-automation/${id}/call-now`)
      toast('Placing call now…', 'success')
      invalidate()
    } catch (e) {
      toast(`Call failed: ${String(e)}`, 'error')
    }
  }

  async function doDelete(id: string) {
    if (!confirm('Delete this scheduled follow-up?')) return
    try {
      await del(`/followup-automation/${id}`)
      toast('Follow-up deleted', 'success')
      invalidate()
    } catch (e) {
      toast(`Delete failed: ${String(e)}`, 'error')
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Follow-Up Automation" sub="Scheduled AI follow-up calls" />

      <Card className="p-5">
        <AsyncState
          loading={isLoading}
          error={error}
          empty={rows.length === 0}
          emptyLabel="No scheduled follow-ups"
        >
          <Table>
            <THead>
              <TH>Contact</TH>
              <TH>Past Call Context</TH>
              <TH>Call Aim</TH>
              <TH>Script Notes</TH>
              <TH>Scheduled Time</TH>
              <TH>Status</TH>
              <TH>Outcome</TH>
              <TH>Actions</TH>
            </THead>
            <tbody>
              {rows.map((a) => {
                const editable = a.status === 'scheduled'
                return (
                  <TR key={a.id}>
                    <TD>
                      <div className="font-semibold text-[var(--text)]">{a.contact_name || '—'}</div>
                      <div className="text-[12px] text-dim">{a.contact_phone || '—'}</div>
                    </TD>

                    <TD className="max-w-[220px]">
                      <span title={a.past_call_context || ''}>{truncate(a.past_call_context) || '—'}</span>
                    </TD>

                    <TD className="min-w-[160px]">
                      {editable ? (
                        <Input
                          defaultValue={a.call_aim ?? ''}
                          onBlur={(e) => {
                            const v = e.target.value
                            if (v !== (a.call_aim ?? '')) doPatch(a.id, { call_aim: v })
                          }}
                        />
                      ) : (
                        <span>{a.call_aim || '—'}</span>
                      )}
                    </TD>

                    <TD className="min-w-[200px]">
                      {editable ? (
                        <Textarea
                          rows={2}
                          defaultValue={a.script_notes ?? ''}
                          onBlur={(e) => {
                            const v = e.target.value
                            if (v !== (a.script_notes ?? '')) doPatch(a.id, { script_notes: v })
                          }}
                        />
                      ) : (
                        <span>{a.script_notes || '—'}</span>
                      )}
                    </TD>

                    <TD className="min-w-[180px]">
                      {editable ? (
                        <Input
                          type="datetime-local"
                          value={toLocalInput(a.scheduled_time)}
                          onChange={(e) => {
                            const v = e.target.value
                            if (!v) return
                            const d = new Date(v)
                            if (isNaN(d.getTime())) return
                            doPatch(a.id, { scheduled_time: d.toISOString() })
                          }}
                        />
                      ) : (
                        <span>{formatTime(a.scheduled_time)}</span>
                      )}
                    </TD>

                    <TD>
                      <div className="flex items-center gap-1.5">
                        <StatusBadge value={a.status} />
                        {a.status === 'calling' && <Bot size={14} className="text-indigo-400" />}
                      </div>
                    </TD>

                    <TD className="max-w-[180px]">
                      <span title={a.call_outcome ?? ''}>{a.call_outcome || '—'}</span>
                    </TD>

                    <TD>
                      <div className="flex items-center gap-2">
                        {a.status === 'scheduled' && (
                          <Button
                            variant="soft"
                            size="sm"
                            title="Call now"
                            onClick={() => doCallNow(a.id)}
                          >
                            <Phone size={14} />
                            Call Now
                          </Button>
                        )}
                        <Button
                          variant="danger"
                          size="sm"
                          title="Delete"
                          onClick={() => doDelete(a.id)}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                    </TD>
                  </TR>
                )
              })}
            </tbody>
          </Table>
        </AsyncState>
      </Card>
    </div>
  )
}
