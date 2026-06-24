import { useState, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Phone, Upload, Plus, Trash2, Play, Square, ChevronDown } from 'lucide-react'
import { post, patch, del, postForm } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, cx } from '../components/ui'
import {
  PageHeader, Table, THead, TH, TR, TD, StatusBadge, SearchInput,
  Input, Select, Field, Segmented, AsyncState,
} from '../components/kit'
import { StatCard } from '../components/StatCard'
import { num } from '../lib/format'

type Stats = {
  total: number; not_called: number; answered: number; no_answer: number
  busy: number; declined: number; failed: number
}
type Dispositions = {
  not_classified: number; interested: number; site_visit_booked: number
  inquiry: number; in_doubt: number; to_contact_later: number; rejected: number
}
type StatsResp = { stats?: Partial<Stats>; dispositions?: Partial<Dispositions> }

type Contact = {
  id: number | string
  phone: string
  name?: string | null
  notes?: string | null
  added_at?: string | null
  call_status?: string | null
  disposition?: string | null
  call_count?: number | null
  last_called_at?: string | null
  call_notes?: string | null
  call_history?: { ts?: string; call_status?: string }[]
}
type ContactsResp = { contacts?: Contact[]; total?: number }

type CampaignStatus = {
  running?: boolean; total?: number; completed?: number; answered?: number
  no_answer?: number; busy?: number; declined?: number; failed?: number
  skipped?: number; current_phone?: string | null; current_name?: string | null
  started_at?: string | null; stopped?: boolean; error?: string | null
}

const DISPOSITION_OPTS = [
  { value: 'not_classified', label: 'Not classified' },
  { value: 'interested', label: 'Interested' },
  { value: 'site_visit_booked', label: 'Site visit booked' },
  { value: 'inquiry', label: 'Inquiry' },
  { value: 'in_doubt', label: 'In doubt' },
  { value: 'to_contact_later', label: 'To contact later' },
  { value: 'rejected', label: 'Rejected' },
]

const CALL_STATUS_FILTER = [
  { value: 'all', label: 'All' },
  { value: 'not_called', label: 'Not called' },
  { value: 'answered', label: 'Answered' },
  { value: 'no_answer', label: 'No answer' },
  { value: 'declined', label: 'Declined' },
  { value: 'busy', label: 'Busy' },
] as const

const DISPOSITION_FILTER = [
  { value: 'all', label: 'All' },
  { value: 'interested', label: 'Interested' },
  { value: 'site_visit_booked', label: 'Site visit' },
  { value: 'inquiry', label: 'Inquiry' },
  { value: 'in_doubt', label: 'In doubt' },
  { value: 'to_contact_later', label: 'Later' },
  { value: 'rejected', label: 'Rejected' },
] as const

const CAMPAIGN_WHO = [
  { value: 'not_called', label: 'Not called' },
  { value: 'no_answer', label: 'No answer' },
  { value: 'to_contact_later', label: 'To contact later' },
  { value: 'all', label: 'All' },
]

export default function Outbound() {
  const toast = useToast()
  const qc = useQueryClient()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['outbound-contacts'] })
    qc.invalidateQueries({ queryKey: ['outbound-stats'] })
  }

  // filters
  const [cs, setCs] = useState<string>('all')
  const [dp, setDp] = useState<string>('all')
  const [q, setQ] = useState('')

  // add contacts form
  const [showAdd, setShowAdd] = useState(false)
  const [phone, setPhone] = useState('')
  const [name, setName] = useState('')
  const [notes, setNotes] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  // campaign form
  const [whoFilter, setWhoFilter] = useState('not_called')
  const [delay, setDelay] = useState('4')
  const [maxCalls, setMaxCalls] = useState('0')

  const statsQ = useGet<StatsResp>(['outbound-stats'], '/outbound/stats')
  const contactsQ = useGet<ContactsResp>(
    ['outbound-contacts', cs, dp, q],
    `/outbound/contacts?call_status=${cs}&disposition=${dp}&q=${encodeURIComponent(q)}`,
  )
  const campaignQ = useGet<CampaignStatus>(['campaign'], '/outbound/campaign/status', { refetchInterval: 2000 })

  const stats = statsQ.data?.stats ?? {}
  const disp = statsQ.data?.dispositions ?? {}
  const contacts = Array.isArray(contactsQ.data?.contacts) ? contactsQ.data!.contacts! : []
  const camp = campaignQ.data ?? {}

  async function addContact() {
    if (!phone.trim()) { toast('Phone is required', 'warn'); return }
    try {
      await post('/outbound/contacts', { phone: phone.trim(), name: name.trim(), notes: notes.trim() })
      toast('Contact added', 'success')
      setPhone(''); setName(''); setNotes('')
      invalidate()
    } catch (e) {
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  async function uploadFile() {
    const file = fileRef.current?.files?.[0]
    if (!file) { toast('Choose a file first', 'warn'); return }
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await postForm<{ added?: number; duplicates?: number }>('/outbound/contacts/upload', fd)
      toast(`Added ${r?.added ?? 0}, ${r?.duplicates ?? 0} dup`, 'success')
      if (fileRef.current) fileRef.current.value = ''
      invalidate()
    } catch (e) {
      toast(`Upload failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  async function changeDisposition(id: Contact['id'], disposition: string) {
    try {
      await patch(`/outbound/contacts/${id}`, { disposition })
      toast('Disposition updated', 'success')
      invalidate()
    } catch (e) {
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  async function callContact(id: Contact['id']) {
    if (!confirm('Place a real call now?')) return
    try {
      const r = await post<{ status?: string }>(`/outbound/dial/${id}`, {})
      toast(`Dialing — ${r?.status ?? 'ok'}`, 'success')
      invalidate()
    } catch (e) {
      toast(`Dial failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  async function deleteContact(id: Contact['id']) {
    if (!confirm('Delete this contact?')) return
    try {
      await del(`/outbound/contacts/${id}`)
      toast('Deleted', 'success')
      invalidate()
    } catch (e) {
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  async function clearAll() {
    if (!confirm('Delete ALL contacts? This cannot be undone.')) return
    try {
      await del('/outbound/contacts/all')
      toast('All contacts cleared', 'success')
      invalidate()
    } catch (e) {
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  async function startCampaign() {
    try {
      await post('/outbound/campaign/start', {
        call_status_filter: whoFilter,
        delay_seconds: Number(delay) || 0,
        max_calls: Number(maxCalls) || 0,
      })
      toast('Campaign started', 'success')
      qc.invalidateQueries({ queryKey: ['campaign'] })
    } catch (e) {
      toast(`Start failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  async function stopCampaign() {
    try {
      await post('/outbound/campaign/stop', {})
      toast('Campaign stopped', 'info')
      qc.invalidateQueries({ queryKey: ['campaign'] })
    } catch (e) {
      toast(`Stop failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  const dispBoxes = [
    { key: 'interested', label: 'Interested' },
    { key: 'site_visit_booked', label: 'Site visit booked' },
    { key: 'inquiry', label: 'Inquiry' },
    { key: 'in_doubt', label: 'In doubt' },
    { key: 'to_contact_later', label: 'To contact later' },
    { key: 'rejected', label: 'Rejected' },
  ] as const

  const total = Number(camp.total ?? 0)
  const completed = Number(camp.completed ?? 0)
  const progress = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0

  return (
    <div className="space-y-6">
      <PageHeader title="Outbound Calls" sub="Contacts & calling campaigns" />

      {/* 1. Stat grid */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        <StatCard label="Total" value={num(stats.total)} />
        <StatCard label="Not called" value={num(stats.not_called)} />
        <StatCard label="Answered" value={num(stats.answered)} iconTone="mint" />
        <StatCard label="No answer" value={num(stats.no_answer)} iconTone="amber" />
        <StatCard label="Declined" value={num(stats.declined)} iconTone="rose" />
        <StatCard label="Busy" value={num(stats.busy)} iconTone="amber" />
        <StatCard label="Failed" value={num(stats.failed)} iconTone="rose" />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {dispBoxes.map((b) => (
          <Card key={b.key} className="p-4">
            <div className="eyebrow truncate">{b.label}</div>
            <div className="mt-1.5 text-[22px] font-bold tabular-nums">
              {num((disp as Record<string, number | undefined>)[b.key])}
            </div>
          </Card>
        ))}
      </div>

      {/* 2. Add Contacts (collapsible) */}
      <Card className="p-5">
        <button
          onClick={() => setShowAdd((v) => !v)}
          className="flex w-full items-center justify-between gap-2 text-left">
          <span className="font-display text-lg font-bold tracking-tight">Add Contacts</span>
          <ChevronDown size={18} className={cx('text-dim transition-transform', showAdd && 'rotate-180')} />
        </button>

        {showAdd && (
          <div className="mt-5 grid gap-6 lg:grid-cols-2">
            {/* manual */}
            <div className="space-y-3">
              <div className="text-[12px] font-semibold text-dim">Add manually</div>
              <Field label="Phone *">
                <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91…" />
              </Field>
              <Field label="Name">
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Optional" />
              </Field>
              <Field label="Notes">
                <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Optional" />
              </Field>
              <Button onClick={addContact}><Plus size={15} /> Add</Button>
            </div>

            {/* bulk */}
            <div className="space-y-3">
              <div className="text-[12px] font-semibold text-dim">Bulk upload (.csv, .xlsx, .txt)</div>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,.xlsx,.txt"
                className="block w-full text-[13px] text-soft file:mr-3 file:rounded-lg file:border-0 file:bg-[var(--surface-2)] file:px-3 file:py-2 file:text-[12px] file:font-semibold file:text-[var(--text)]"
              />
              <Button variant="soft" onClick={uploadFile}><Upload size={15} /> Upload</Button>
            </div>
          </div>
        )}
      </Card>

      {/* 3. Campaign Runner */}
      <Card className="p-5">
        <div className="mb-4 font-display text-lg font-bold tracking-tight">Campaign Runner</div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Who to call">
            <Select value={whoFilter} onChange={(e) => setWhoFilter(e.target.value)} className="w-full">
              {CAMPAIGN_WHO.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </Field>
          <Field label="Delay (seconds)">
            <Input type="number" value={delay} onChange={(e) => setDelay(e.target.value)} />
          </Field>
          <Field label="Max calls (0 = no limit)">
            <Input type="number" value={maxCalls} onChange={(e) => setMaxCalls(e.target.value)} />
          </Field>
        </div>
        <div className="mt-4 flex gap-2">
          <Button onClick={startCampaign} disabled={!!camp.running}><Play size={15} /> Start</Button>
          <Button variant="danger" onClick={stopCampaign} disabled={!camp.running}><Square size={15} /> Stop</Button>
        </div>

        {camp.running && (
          <div className="mt-5 space-y-3">
            <div className="flex items-center justify-between gap-3 text-[13px]">
              <div className="text-soft">
                <span className="font-semibold text-[var(--text)]">{camp.current_name || '—'}</span>
                {camp.current_phone && <span className="ml-2 text-dim tabular-nums">{camp.current_phone}</span>}
              </div>
              <div className="tabular-nums text-dim">{completed} / {total}</div>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]">
              <div className="h-full rounded-full bg-gradient-to-r from-mint-300 to-mint-500 transition-all" style={{ width: `${progress}%` }} />
            </div>
            <div className="flex flex-wrap gap-2 text-[12px]">
              <Badge tone="mint">Answered {num(camp.answered)}</Badge>
              <Badge tone="amber">No answer {num(camp.no_answer)}</Badge>
              <Badge tone="amber">Busy {num(camp.busy)}</Badge>
              <Badge tone="rose">Declined {num(camp.declined)}</Badge>
              <Badge tone="rose">Failed {num(camp.failed)}</Badge>
              <Badge tone="neutral">Skipped {num(camp.skipped)}</Badge>
            </div>
          </div>
        )}
        {camp.error && (
          <div className="mt-3 rounded-xl bg-rose-500/10 px-3 py-2 text-[12px] font-medium text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">
            {camp.error}
          </div>
        )}
      </Card>

      {/* 4. Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <Segmented options={CALL_STATUS_FILTER.map((o) => ({ value: o.value, label: o.label }))} value={cs} onChange={setCs} />
        <Segmented options={DISPOSITION_FILTER.map((o) => ({ value: o.value, label: o.label }))} value={dp} onChange={setDp} />
        <SearchInput value={q} onChange={setQ} placeholder="Search phone or name…" className="w-56" />
        <div className="ml-auto">
          <Button variant="danger" size="sm" onClick={clearAll}><Trash2 size={14} /> Clear all</Button>
        </div>
      </div>

      {/* 5. Contacts table */}
      <Card className="p-5">
        <AsyncState
          loading={contactsQ.isLoading}
          error={contactsQ.error}
          empty={!contactsQ.isLoading && contacts.length === 0}
          emptyLabel="No contacts yet — add some above">
          <Table>
            <THead>
              <TH>#</TH>
              <TH>Phone</TH>
              <TH>Name</TH>
              <TH>Call Status</TH>
              <TH>Disposition</TH>
              <TH>Last Called</TH>
              <TH>Calls</TH>
              <TH className="text-right">Actions</TH>
            </THead>
            <tbody>
              {contacts.map((c, i) => (
                <TR key={c.id ?? i}>
                  <TD className="text-dim tabular-nums">{i + 1}</TD>
                  <TD className="font-medium text-[var(--text)] tabular-nums">{c.phone ?? '—'}</TD>
                  <TD>{c.name || '—'}</TD>
                  <TD><StatusBadge value={c.call_status} /></TD>
                  <TD>
                    <Select
                      value={c.disposition ?? 'not_classified'}
                      onChange={(e) => changeDisposition(c.id, e.target.value)}>
                      {DISPOSITION_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </Select>
                  </TD>
                  <TD className="text-dim">{c.last_called_at ? String(c.last_called_at) : '—'}</TD>
                  <TD className="tabular-nums">{num(c.call_count)}</TD>
                  <TD>
                    <div className="flex items-center justify-end gap-1.5">
                      <Button size="sm" variant="ghost" title="Call now" onClick={() => callContact(c.id)}>
                        <Phone size={14} />
                      </Button>
                      <Button size="sm" variant="danger" title="Delete" onClick={() => deleteContact(c.id)}>
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
    </div>
  )
}
