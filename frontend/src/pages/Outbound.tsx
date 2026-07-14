import { Fragment, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Phone, Upload, Plus, Trash2, Play, Square, ChevronDown, RefreshCw } from 'lucide-react'
import { post, patch, del, postForm } from '../api/mutate'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, EmptyState, SectionHead, SkeletonBlock, Spinner, cx } from '../components/ui'
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

/** Local until a shared lib/format helper exists. '12 Jul, 06:50 pm' style. */
const fmtDateTime = (s?: string | null): string => {
  if (!s) return '—'
  const d = new Date(s)
  if (isNaN(d.getTime())) return String(s)
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** Debounce a fast-changing value (used for the search box → query key). */
function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

/** Green ping dot shown while a campaign is dialing. */
function LivePulse({ className }: { className?: string }) {
  return (
    <span className={cx('relative flex h-2 w-2 shrink-0', className)}>
      <span className="absolute h-full w-full animate-ping rounded-full bg-mint-400 opacity-75" />
      <span className="relative h-2 w-2 rounded-full bg-mint-500" />
    </span>
  )
}

function CampaignTallies({ camp }: { camp: CampaignStatus }) {
  return (
    <div className="flex flex-wrap gap-2 text-[12px]">
      <Badge tone="mint">Answered {num(camp.answered)}</Badge>
      <Badge tone="amber">No answer {num(camp.no_answer)}</Badge>
      <Badge tone="amber">Busy {num(camp.busy)}</Badge>
      <Badge tone="rose">Declined {num(camp.declined)}</Badge>
      <Badge tone="rose">Failed {num(camp.failed)}</Badge>
      <Badge tone="neutral">Skipped {num(camp.skipped)}</Badge>
    </div>
  )
}

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]">
      <div className="h-full rounded-full bg-gradient-to-r from-mint-300 to-mint-500 transition-all" style={{ width: `${pct}%` }} />
    </div>
  )
}

export default function Outbound() {
  const toast = useToast()
  const qc = useQueryClient()
  const refresh = useRefresh()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['outbound-contacts'] })
    qc.invalidateQueries({ queryKey: ['outbound-stats'] })
  }

  // filters
  const [cs, setCs] = useState<string>('all')
  const [dp, setDp] = useState<string>('all')
  const [q, setQ] = useState('')
  const dq = useDebounced(q, 300)

  // add contacts form
  const [showAdd, setShowAdd] = useState(false)
  const [phone, setPhone] = useState('')
  const [name, setName] = useState('')
  const [notes, setNotes] = useState('')
  const [adding, setAdding] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const addCardRef = useRef<HTMLDivElement>(null)
  const [fileName, setFileName] = useState('')
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)

  // campaign form
  const [whoFilter, setWhoFilter] = useState('not_called')
  const [delay, setDelay] = useState('4')
  const [maxCalls, setMaxCalls] = useState('0')
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)

  // row-level busy / expansion
  const [dialingId, setDialingId] = useState<Contact['id'] | null>(null)
  const [deletingId, setDeletingId] = useState<Contact['id'] | null>(null)
  const [expandedId, setExpandedId] = useState<Contact['id'] | null>(null)

  const campaignQ = useGet<CampaignStatus>(['campaign'], '/outbound/campaign/status', { refetchInterval: 2000 })
  const camp = campaignQ.data ?? {}
  const campRunning = !!camp.running

  // While a campaign dials, keep statuses and stats ticking without a manual refresh.
  const statsQ = useGet<StatsResp>(['outbound-stats'], '/outbound/stats',
    { refetchInterval: campRunning ? 5000 : undefined })
  const contactsQ = useGet<ContactsResp>(
    ['outbound-contacts', cs, dp, dq],
    `/outbound/contacts?call_status=${cs}&disposition=${dp}&q=${encodeURIComponent(dq)}`,
    { refetchInterval: campRunning ? 5000 : undefined },
  )

  const stats = statsQ.data?.stats ?? {}
  const disp = statsQ.data?.dispositions ?? {}
  const contacts = Array.isArray(contactsQ.data?.contacts) ? contactsQ.data!.contacts! : []

  const hasFilters = cs !== 'all' || dp !== 'all' || dq.trim() !== ''

  const audienceCounts: Record<string, number | undefined> = {
    not_called: stats.not_called,
    no_answer: stats.no_answer,
    to_contact_later: disp.to_contact_later,
    all: stats.total,
  }
  const queued = statsQ.data ? Number(audienceCounts[whoFilter] ?? 0) : null

  async function addContact() {
    if (!phone.trim()) { toast('Phone is required', 'warn'); return }
    setAdding(true)
    try {
      await post('/outbound/contacts', { phone: phone.trim(), name: name.trim(), notes: notes.trim() })
      toast('Contact added', 'success')
      setPhone(''); setName(''); setNotes('')
      invalidate()
    } catch (e) {
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    } finally {
      setAdding(false)
    }
  }

  async function uploadFile() {
    const file = fileRef.current?.files?.[0]
    if (!file) { toast('Choose a file first', 'warn'); return }
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await postForm<{ added?: number; duplicates?: number }>('/outbound/contacts/upload', fd)
      toast(`Added ${r?.added ?? 0} contacts · ${r?.duplicates ?? 0} duplicates skipped`, 'success')
      if (fileRef.current) fileRef.current.value = ''
      setFileName('')
      invalidate()
    } catch (e) {
      toast(`Upload failed: ${String((e as Error)?.message ?? e)}`, 'error')
    } finally {
      setUploading(false)
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

  async function callContact(c: Contact) {
    const label = c.name ? `${c.phone} (${c.name})` : c.phone
    if (!confirm(`Place a real call to ${label} now?`)) return
    setDialingId(c.id)
    try {
      const r = await post<{ status?: string }>(`/outbound/dial/${c.id}`, {})
      toast(`Dialing — ${r?.status ?? 'ok'}`, 'success')
      invalidate()
    } catch (e) {
      toast(`Dial failed: ${String((e as Error)?.message ?? e)}`, 'error')
    } finally {
      setDialingId(null)
    }
  }

  async function deleteContact(c: Contact) {
    const label = c.name ? `${c.phone} (${c.name})` : c.phone
    if (!confirm(`Delete ${label}? This cannot be undone.`)) return
    setDeletingId(c.id)
    try {
      await del(`/outbound/contacts/${c.id}`)
      toast('Deleted', 'success')
      invalidate()
    } catch (e) {
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    } finally {
      setDeletingId(null)
    }
  }

  async function clearAll() {
    const countLabel = stats.total != null ? `ALL ${num(stats.total)}` : 'ALL'
    if (!confirm(`Delete ${countLabel} contacts? This cannot be undone.`)) return
    try {
      await del('/outbound/contacts/all')
      toast('All contacts cleared', 'success')
      invalidate()
    } catch (e) {
      toast(`Failed: ${String((e as Error)?.message ?? e)}`, 'error')
    }
  }

  async function startCampaign() {
    setStarting(true)
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
    } finally {
      setStarting(false)
    }
  }

  async function stopCampaign() {
    setStopping(true)
    try {
      await post('/outbound/campaign/stop', {})
      toast('Campaign stopped', 'info')
      qc.invalidateQueries({ queryKey: ['campaign'] })
    } catch (e) {
      toast(`Stop failed: ${String((e as Error)?.message ?? e)}`, 'error')
    } finally {
      setStopping(false)
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

  const listTotal = contactsQ.data?.total ?? contacts.length
  const searching = contactsQ.isFetching && !contactsQ.isLoading

  return (
    <div className="space-y-6">
      <PageHeader
        title="Outbound Calls"
        sub="Contacts & calling campaigns"
        actions={
          <>
            {campRunning && (
              <Badge tone="mint"><LivePulse /> Campaign running</Badge>
            )}
            <Button variant="ghost" onClick={() => refresh(['outbound-contacts'], ['outbound-stats'], ['campaign'])}>
              <RefreshCw size={14} /> Refresh
            </Button>
          </>
        }
      />

      {/* 1. Stat grid — call statuses */}
      <div>
        <div className="eyebrow mb-2">Call status</div>
        {statsQ.isLoading ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
            {[0, 1, 2, 3, 4, 5, 6].map((i) => <SkeletonBlock key={i} h={92} />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
            <StatCard label="Total" value={num(stats.total)} />
            <StatCard label="Not called" value={num(stats.not_called)} />
            <StatCard label="Answered" value={num(stats.answered)} iconTone="mint" />
            <StatCard label="No answer" value={num(stats.no_answer)} iconTone="amber" />
            <StatCard label="Declined" value={num(stats.declined)} iconTone="rose" />
            <StatCard label="Busy" value={num(stats.busy)} iconTone="amber" />
            <StatCard label="Failed" value={num(stats.failed)} iconTone="rose" />
          </div>
        )}
      </div>

      {/* Lead dispositions */}
      <div>
        <div className="eyebrow mb-2">Lead dispositions</div>
        {statsQ.isLoading ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
            {[0, 1, 2, 3, 4, 5].map((i) => <SkeletonBlock key={i} h={92} />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
            {dispBoxes.map((b) => (
              <StatCard
                key={b.key}
                label={b.label}
                value={num((disp as Record<string, number | undefined>)[b.key])}
              />
            ))}
          </div>
        )}
      </div>

      {/* 2. Add Contacts (collapsible) */}
      <div ref={addCardRef}>
        <Card className="p-5">
          <button
            onClick={() => setShowAdd((v) => !v)}
            aria-expanded={showAdd}
            aria-controls="add-contacts-panel"
            className="flex w-full items-center justify-between gap-2 text-left">
            <span className="font-display text-lg font-bold tracking-tight">Add Contacts</span>
            <ChevronDown size={18} className={cx('text-dim transition-transform', showAdd && 'rotate-180')} />
          </button>

          {showAdd && (
            <div id="add-contacts-panel" className="mt-5 grid gap-6 animate-fade-up lg:grid-cols-2">
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
                <Button onClick={addContact} disabled={adding}>
                  {adding ? <Spinner /> : <Plus size={15} />} {adding ? 'Adding…' : 'Add'}
                </Button>
              </div>

              {/* bulk */}
              <div className="space-y-3">
                <div className="text-[12px] font-semibold text-dim">Bulk upload (.csv, .xlsx, .txt)</div>
                <label
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={(e) => {
                    e.preventDefault()
                    setDragOver(false)
                    if (e.dataTransfer.files.length && fileRef.current) {
                      fileRef.current.files = e.dataTransfer.files
                      setFileName(e.dataTransfer.files[0]?.name ?? '')
                    }
                  }}
                  className={cx(
                    'flex cursor-pointer flex-col items-center gap-2 rounded-xl border border-dashed border-[var(--border-strong)] bg-[var(--surface-2)] px-4 py-8 text-center transition hover:border-mint-400/50 hover:bg-mint-400/5',
                    dragOver && 'border-mint-400/60 bg-mint-400/5',
                  )}>
                  <Upload size={18} className="text-dim" />
                  <span className="text-[13px] font-medium text-soft">Click to browse or drop a file</span>
                  <span className="text-[12px] text-dim">CSV, XLSX or TXT — must include a phone column</span>
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".csv,.xlsx,.txt"
                    className="sr-only"
                    onChange={(e) => setFileName(e.target.files?.[0]?.name ?? '')}
                  />
                </label>
                {fileName && (
                  <div><Badge tone="neutral"><span className="max-w-[70vw] truncate">{fileName}</span></Badge></div>
                )}
                <Button variant="soft" onClick={uploadFile} disabled={uploading}>
                  {uploading ? <Spinner /> : <Upload size={15} />} {uploading ? 'Uploading…' : 'Upload'}
                </Button>
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* 3. Campaign Runner */}
      <Card className="p-5">
        <SectionHead title="Campaign Runner" sub="Dials contacts sequentially with a delay between calls" />
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Who to call">
            <div>
              <Select value={whoFilter} onChange={(e) => setWhoFilter(e.target.value)} className="w-full">
                {CAMPAIGN_WHO.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </Select>
              {queued != null && (
                queued > 0 ? (
                  <div className="mt-1 text-[12px] text-dim tabular-nums">{num(queued)} contacts will be queued</div>
                ) : (
                  <div className="mt-1 text-[12px] font-medium text-amber-600 dark:text-amber-300">No contacts match — nothing to dial</div>
                )
              )}
            </div>
          </Field>
          <Field label="Delay (seconds)">
            <Input type="number" min={0} value={delay} onChange={(e) => setDelay(e.target.value)} />
          </Field>
          <Field label="Max calls (0 = no limit)">
            <Input type="number" min={0} value={maxCalls} onChange={(e) => setMaxCalls(e.target.value)} />
          </Field>
        </div>
        <div className="mt-4 flex gap-2">
          <Button onClick={startCampaign} disabled={campRunning || starting || queued === 0}
            title={queued === 0 ? 'No contacts match the selected audience' : undefined}>
            {starting ? <Spinner /> : <Play size={15} />} {starting ? 'Starting…' : 'Start'}
          </Button>
          <Button variant="danger" onClick={stopCampaign} disabled={!campRunning || stopping}>
            {stopping ? <Spinner /> : <Square size={15} />} {stopping ? 'Stopping…' : 'Stop'}
          </Button>
        </div>

        {campRunning && (
          <div className="mt-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[13px]">
              <div className="flex min-w-0 items-center gap-2 text-soft">
                <LivePulse />
                <span className="truncate font-semibold text-[var(--text)]">{camp.current_name || '—'}</span>
                {camp.current_phone && <span className="text-dim tabular-nums">{camp.current_phone}</span>}
              </div>
              <div className="shrink-0 tabular-nums text-dim">{completed} / {total} · {progress}%</div>
            </div>
            <ProgressBar pct={progress} />
            <CampaignTallies camp={camp} />
          </div>
        )}

        {/* Last-run summary stays visible once the campaign ends */}
        {!campRunning && total > 0 && (
          <div className="mt-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3 text-[13px]">
              <div className="flex items-center gap-2">
                <span className="eyebrow">Last run</span>
                {camp.started_at && <span className="text-[12px] text-dim">started {fmtDateTime(camp.started_at)}</span>}
              </div>
              <div className="tabular-nums text-dim">{completed} / {total} completed</div>
            </div>
            <ProgressBar pct={progress} />
            <CampaignTallies camp={camp} />
          </div>
        )}

        {camp.error && (
          <div role="alert" className="mt-3 rounded-xl bg-rose-500/10 px-3 py-2 text-[12px] font-medium text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">
            {camp.error}
          </div>
        )}
      </Card>

      {/* 4. Contacts table (filters live inside the card) */}
      <Card className="p-5">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Segmented options={CALL_STATUS_FILTER.map((o) => ({ value: o.value, label: o.label }))} value={cs} onChange={setCs} />
          <Segmented options={DISPOSITION_FILTER.map((o) => ({ value: o.value, label: o.label }))} value={dp} onChange={setDp} />
          <div className="relative">
            <SearchInput value={q} onChange={setQ} placeholder="Search phone or name…" className="w-56" />
            {searching && <Spinner className="absolute right-3 top-1/2 -translate-y-1/2" />}
          </div>
          <span className="text-[12px] text-dim tabular-nums">{num(listTotal)} contacts</span>
          <div className="ml-auto border-l border-[var(--border)] pl-3">
            <Button variant="danger" size="sm" onClick={clearAll}>
              <Trash2 size={14} /> Clear all{stats.total != null ? ` (${num(stats.total)})` : ''}
            </Button>
          </div>
        </div>

        <AsyncState loading={contactsQ.isLoading} error={contactsQ.error}>
          {contacts.length === 0 ? (
            hasFilters ? (
              <EmptyState
                icon={<Phone size={20} />}
                title="No contacts match these filters"
                hint="Try a different status, disposition or search term."
                action={
                  <Button variant="ghost" onClick={() => { setCs('all'); setDp('all'); setQ('') }}>
                    Reset filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={<Phone size={20} />}
                title="No contacts yet"
                hint="Add a contact manually or upload a CSV above — then run a campaign to dial through them."
                action={
                  <Button onClick={() => { setShowAdd(true); addCardRef.current?.scrollIntoView({ behavior: 'smooth' }) }}>
                    <Plus size={15} /> Add contacts
                  </Button>
                }
              />
            )
          ) : (
            <Table className={cx(searching && 'opacity-60 transition-opacity')}>
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
                {contacts.map((c, i) => {
                  const isExpanded = expandedId === c.id
                  const isDialingNow = campRunning && !!c.phone && c.phone === camp.current_phone
                  return (
                    <Fragment key={c.id ?? i}>
                      <TR
                        onClick={() => setExpandedId(isExpanded ? null : c.id)}
                        className={isDialingNow ? 'bg-mint-400/10' : undefined}>
                        <TD className="text-dim tabular-nums">
                          <span className="flex items-center gap-1.5">
                            <ChevronDown size={14} className={cx('shrink-0 text-dim transition-transform', isExpanded && 'rotate-180')} />
                            {i + 1}
                          </span>
                        </TD>
                        <TD className="font-medium text-[var(--text)] tabular-nums">
                          <span className="flex items-center gap-2">
                            {c.phone ?? '—'}
                            {isDialingNow && <StatusBadge value="dialing" />}
                          </span>
                        </TD>
                        <TD>{c.name || '—'}</TD>
                        <TD><StatusBadge value={c.call_status} /></TD>
                        <TD>
                          <Select
                            value={c.disposition ?? 'not_classified'}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => changeDisposition(c.id, e.target.value)}>
                            {DISPOSITION_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                          </Select>
                        </TD>
                        <TD className="text-dim" title={c.last_called_at ?? undefined}>{fmtDateTime(c.last_called_at)}</TD>
                        <TD className="tabular-nums">{num(c.call_count)}</TD>
                        <TD>
                          <div className="flex items-center justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
                            <Button size="sm" variant="ghost" title={`Call ${c.name || c.phone || 'contact'} now`} className="min-h-[34px] min-w-[34px]"
                              disabled={dialingId === c.id} onClick={() => callContact(c)}>
                              {dialingId === c.id ? <Spinner /> : <Phone size={14} />}
                            </Button>
                            <Button size="sm" variant="danger" title={`Delete ${c.name || c.phone || 'contact'}`} className="min-h-[34px] min-w-[34px]"
                              disabled={deletingId === c.id} onClick={() => deleteContact(c)}>
                              {deletingId === c.id ? <Spinner /> : <Trash2 size={14} />}
                            </Button>
                          </div>
                        </TD>
                      </TR>
                      {isExpanded && (
                        <tr className="border-b border-[var(--border)]">
                          <TD colSpan={8} className="bg-[var(--surface-2)]">
                            <div className="sticky left-0 max-w-[calc(100vw-5rem)]">
                            <div className="grid gap-4 py-1 sm:grid-cols-2 lg:grid-cols-4">
                              <div>
                                <div className="eyebrow mb-1">Notes</div>
                                <div className="text-[13px] text-soft">{c.notes || '—'}</div>
                              </div>
                              <div>
                                <div className="eyebrow mb-1">Call notes</div>
                                <div className="text-[13px] text-soft">{c.call_notes || '—'}</div>
                              </div>
                              <div>
                                <div className="eyebrow mb-1">Added</div>
                                <div className="text-[13px] text-soft" title={c.added_at ?? undefined}>{fmtDateTime(c.added_at)}</div>
                              </div>
                              <div>
                                <div className="eyebrow mb-1">Call history</div>
                                {c.call_history?.length ? (
                                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                                    {c.call_history.map((h, hi) => (
                                      <span key={hi} className="inline-flex items-center gap-1.5">
                                        <StatusBadge value={h.call_status} />
                                        <span className="text-[11px] text-dim">{fmtDateTime(h.ts)}</span>
                                      </span>
                                    ))}
                                  </div>
                                ) : (
                                  <div className="text-[13px] text-dim">—</div>
                                )}
                              </div>
                            </div>
                            </div>
                          </TD>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </Table>
          )}
        </AsyncState>
      </Card>
    </div>
  )
}
