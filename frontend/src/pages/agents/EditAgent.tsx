import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useGet, useRefresh } from '../../api/hooks/useApi'
import {
  getAgent, updateAgent, agentToWizardData, mergeWizardIntoDefinition,
  getRawYaml, putRawYaml, validateRaw, getRouting, putRouting,
  uploadDoc, deleteDoc, extractYaml, applyYaml, deleteBooking,
  activateAgent, archiveAgent, ttsPreviewUrl,
  type Routing, type AgentDoc, type WizardData, type MissingField, type Booking,
} from '../../api/agents'
import { Card, Button, Badge, cx, EmptyState } from '../../components/ui'
import { PageHeader, Field, Input, Select, Textarea, AsyncState } from '../../components/kit'
import { useToast } from '../../components/Toast'
import {
  BasicsSection, CallDirectionSection, CalendarSection, LanguagesSection,
  PersonalitySection, KnowledgeSection, CallFlowSection, ResponsesSection,
  RoutingFields, type TimeWindowDraft,
} from './AgentFormSections'
import {
  Radio, Archive, ArrowLeft, Save, CheckCircle2, AlertTriangle, Volume2,
  Upload, Trash2, FileText, Sparkles, Code2, Network, Files, SlidersHorizontal,
  CalendarDays,
} from 'lucide-react'

type Tab = 'settings' | 'yaml' | 'routing' | 'documents' | 'voice'
const BCP47: Record<string, string> = { en: 'en-IN', hi: 'hi-IN', gu: 'gu-IN', mr: 'hi-IN' }

export default function EditAgent() {
  const { name = '' } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const refresh = useRefresh()
  const [tab, setTab] = useState<Tab>('settings')

  const { data: agent, isLoading, error } = useGet<any>(['agent', name], `/agents/${name}`)
  const { data: activeData } = useGet<{ active: string | null }>(['agents-active'], '/agents/active')
  const isActive = activeData?.active === name

  const onActivate = async () => {
    try { await activateAgent(name); toast(`"${name}" is now answering inbound calls`, 'success'); refresh(['agents-active'], ['agents']) }
    catch (e: any) { toast(`Activate failed: ${e.message || e}`, 'error') }
  }
  const onArchive = async () => {
    if (!confirm(`Archive "${name}"?`)) return
    try { await archiveAgent(name); toast(`Archived "${name}"`, 'success'); nav('/build/agents') }
    catch (e: any) { toast(`Archive failed: ${e.message || e}`, 'error') }
  }

  const tabs: { id: Tab; label: string; icon: any }[] = [
    { id: 'settings', label: 'Settings', icon: SlidersHorizontal },
    { id: 'yaml', label: 'Advanced (YAML)', icon: Code2 },
    { id: 'routing', label: 'Routing', icon: Network },
    { id: 'documents', label: 'Documents · RAG', icon: Files },
    { id: 'voice', label: 'Voice', icon: Volume2 },
  ]

  return (
    <div className="space-y-6">
      <PageHeader
        title={agent?.identity?.agent_name || name}
        sub={agent?.identity?.company_name || 'Edit agent'}
        actions={
          <div className="flex items-center gap-2">
            <Link to="/build/agents"><Button variant="ghost"><ArrowLeft size={15} /> Agents</Button></Link>
            {isActive
              ? <Badge tone="mint" soft><CheckCircle2 size={12} /> Active</Badge>
              : <Button variant="primary" onClick={onActivate}><Radio size={14} /> Activate</Button>}
            <Button variant="ghost" onClick={onArchive}><Archive size={14} /></Button>
          </div>
        }
      />

      <AsyncState loading={isLoading} error={error}>
        <div className="flex flex-wrap gap-2">
          {tabs.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={cx('flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold ring-1 transition',
                tab === t.id ? 'bg-mint-400/15 text-mint-700 ring-mint-400/30 dark:text-mint-300' : 'bg-[var(--surface-2)] text-soft ring-[var(--border)] hover:text-[var(--text)]')}>
              <t.icon size={14} /> {t.label}
            </button>
          ))}
        </div>

        {tab === 'settings' && <SettingsTab name={name} onSaved={() => refresh(['agent', name], ['agents'])} />}
        {tab === 'yaml' && <YamlTab name={name} onSaved={() => refresh(['agent', name], ['agents'])} />}
        {tab === 'routing' && <RoutingTab name={name} />}
        {tab === 'documents' && <DocumentsTab name={name} />}
        {tab === 'voice' && <VoiceTab agent={agent} />}
      </AsyncState>
    </div>
  )
}

/* ───────────────────────── Settings tab ─────────────────────────
 * Structured form built from the same shared sections as the Create wizard.
 * DATA SAFETY — merge-on-save: on Save we re-fetch the CURRENT full agent
 * definition and deep-merge only the sections this form models onto it via
 * mergeWizardIntoDefinition(), then PUT the merged FULL object. Fields the
 * form does not model (calendar, qa_field_signals, stt_corrections, slots,
 * register_notes wording, stt_forced_language, allow_code_mixing, pace, …)
 * therefore always survive an edit round-trip. */
function SettingsTab({ name, onSaved }: { name: string; onSaved: () => void }) {
  const toast = useToast()
  const [d, setD] = useState<WizardData | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const initRef = useRef<WizardData | null>(null)

  useEffect(() => {
    let on = true
    getAgent(name)
      .then((def) => {
        if (!on) return
        const w = agentToWizardData(def, name)
        initRef.current = JSON.parse(JSON.stringify(w))
        setD(w)
      })
      .catch((e) => { if (on) setLoadError(e.message || String(e)) })
    return () => { on = false }
  }, [name])

  const set = (patch: Partial<WizardData>) => setD((p) => (p ? { ...p, ...patch } : p))

  const onSave = async () => {
    if (!d || !initRef.current) return
    if (!d.agent_name || !d.company_name || !d.opening_greeting || d.supported.length === 0 || d.stages.length === 0) {
      toast('Agent name, company, opening greeting, at least one language and one stage are required', 'error')
      return
    }
    setSaving(true)
    try {
      const fresh = await getAgent(name) // merge onto the LATEST definition, not a stale copy
      const merged = mergeWizardIntoDefinition(fresh, d, initRef.current)
      await updateAgent(name, merged)
      initRef.current = JSON.parse(JSON.stringify(d))
      toast('Settings saved — effective on the next call', 'success')
      onSaved()
    } catch (e: any) {
      toast(`Save failed: ${e.message || e}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  const saveBtn = (
    <Button variant="primary" onClick={onSave} disabled={!d || saving}>
      <Save size={14} /> {saving ? 'Saving…' : 'Save settings'}
    </Button>
  )

  return (
    <AsyncState loading={!d && !loadError} error={loadError}>
      {d && (
        <div className="space-y-4">
          <Card className="flex flex-wrap items-center gap-3 p-4">
            <p className="min-w-[240px] flex-1 text-[12px] text-dim">
              Structured editor for the common agent fields. Anything not shown here (STT corrections,
              QA signals, per-language register notes, …) is preserved untouched —
              use the <span className="font-semibold text-soft">Advanced (YAML)</span> tab for those.
            </p>
            {saveBtn}
          </Card>
          <Card className="p-6"><div className="eyebrow mb-4">Basics</div><BasicsSection d={d} set={set} showSlug={false} /></Card>
          <Card className="p-6"><div className="eyebrow mb-4">Call Type</div><CallDirectionSection d={d} set={set} /></Card>
          <Card className="p-6"><div className="eyebrow mb-4">Languages &amp; Voice</div><LanguagesSection d={d} set={set} /></Card>
          <Card className="p-6"><div className="eyebrow mb-4">Personality</div><PersonalitySection d={d} set={set} /></Card>
          <Card className="p-6"><div className="eyebrow mb-4">Call Script — Opening &amp; Stages</div><CallFlowSection d={d} set={set} /></Card>
          <Card className="p-6"><div className="eyebrow mb-4">Knowledge</div><KnowledgeSection d={d} set={set} /></Card>
          <Card className="p-6">
            <div className="eyebrow mb-4">Calendar &amp; Appointments</div>
            <CalendarSection d={d} set={set} />
            <BookingsList name={name} />
          </Card>
          <Card className="p-6"><div className="eyebrow mb-4">Objections &amp; Rules</div><ResponsesSection d={d} set={set} /></Card>
          <div className="flex justify-end">{saveBtn}</div>
        </div>
      )}
    </AsyncState>
  )
}

/* ───────────────────────── Bookings (calendar) ─────────────────────────
 * Appointments booked by live calls (agents/<slug>/bookings.json). Deleting
 * one frees its slot so the agent offers it again. */
function BookingsList({ name }: { name: string }) {
  const toast = useToast()
  const refresh = useRefresh()
  const { data } = useGet<{ bookings: Booking[] }>(['bookings', name], `/agents/${name}/bookings`, { refetchInterval: 30000 })
  const bookings = data?.bookings ?? []

  const onFree = async (i: number) => {
    if (!confirm('Free this slot? The agent will start offering it again.')) return
    try { await deleteBooking(name, i); toast('Slot freed', 'success'); refresh(['bookings', name]) }
    catch (e: any) { toast(`Failed: ${e.message || e}`, 'error') }
  }

  if (bookings.length === 0) return null
  return (
    <div className="mt-5">
      <div className="eyebrow mb-2 flex items-center gap-1.5"><CalendarDays size={13} /> Booked slots ({bookings.length})</div>
      <div className="space-y-2">
        {bookings.map((b, i) => (
          <div key={`${b.conn_id}-${i}`} className="flex flex-wrap items-center gap-3 rounded-lg bg-[var(--surface-2)] px-3 py-2 ring-1 ring-[var(--border)]">
            <span className="text-[13px] font-semibold">{b.appointment_time}</span>
            <span className="text-[12px] text-soft">{b.caller_name || 'Unknown caller'}{b.caller_phone ? ` · ${b.caller_phone}` : ''}</span>
            <span className="ml-auto text-[11px] text-dim">{b.booked_at?.slice(0, 16).replace('T', ' ') || ''}</span>
            <Button variant="ghost" size="sm" onClick={() => onFree(i)} title="Free this slot"><Trash2 size={13} /></Button>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ───────────────────────── YAML tab ───────────────────────── */
function YamlTab({ name, onSaved }: { name: string; onSaved: () => void }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [result, setResult] = useState<{ valid: boolean; error?: string; agent_id?: string } | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let on = true
    getRawYaml(name).then((t) => { if (on) { setText(t); setLoaded(true) } }).catch((e) => toast(`Load failed: ${e.message || e}`, 'error'))
    return () => { on = false }
  }, [name])

  const onValidate = async () => {
    try { setResult(await validateRaw(name, text)) } catch (e: any) { setResult({ valid: false, error: e.message || String(e) }) }
  }
  const onSave = async () => {
    setSaving(true)
    try {
      const v = await validateRaw(name, text)
      setResult(v)
      if (!v.valid) { toast('Fix validation errors before saving', 'error'); return }
      await putRawYaml(name, text)
      toast('Saved — effective on the next call', 'success')
      onSaved()
    } catch (e: any) { toast(`Save failed: ${e.message || e}`, 'error') }
    finally { setSaving(false) }
  }

  return (
    <Card className="p-5 space-y-3">
      <div className="flex items-center gap-2">
        <span className="eyebrow">agents/{name}/agent.yaml</span>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" onClick={onValidate} disabled={!loaded}><CheckCircle2 size={14} /> Validate</Button>
          <Button variant="primary" onClick={onSave} disabled={!loaded || saving}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
      {result && (
        <div className={cx('flex items-start gap-2 rounded-lg px-3 py-2 text-[12px] ring-1',
          result.valid ? 'bg-mint-400/10 text-mint-700 ring-mint-400/20 dark:text-mint-300' : 'bg-rose-500/10 text-rose-600 ring-rose-500/20 dark:text-rose-300')}>
          {result.valid ? <CheckCircle2 size={14} className="mt-0.5" /> : <AlertTriangle size={14} className="mt-0.5" />}
          <span>{result.valid ? `Valid — ${result.agent_id}` : result.error}</span>
        </div>
      )}
      <Textarea
        rows={26}
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="font-mono text-[12px] leading-relaxed"
      />
    </Card>
  )
}

/* ───────────────────────── Routing tab ───────────────────────── */
function RoutingTab({ name }: { name: string }) {
  const toast = useToast()
  const [r, setR] = useState<Routing>({ did: '', caller_id_prefixes: [], fallback_agent: '', time_windows: [] })
  const [win, setWin] = useState<TimeWindowDraft>({ start: '', end: '' })
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let on = true
    getRouting(name).then((data) => {
      if (!on) return
      setR({ did: data.did || '', caller_id_prefixes: data.caller_id_prefixes || [], fallback_agent: data.fallback_agent || '', time_windows: data.time_windows || [] })
      const w = (data.time_windows || [])[0]
      if (w) setWin({ start: String(w.start), end: String(w.end) })
      setLoaded(true)
    })
    return () => { on = false }
  }, [name])

  const onSave = async () => {
    const time_windows = win.start !== '' && win.end !== '' ? [{ start: Number(win.start), end: Number(win.end) }] : []
    const body: Routing = {
      did: r.did || '',
      caller_id_prefixes: r.caller_id_prefixes || [],
      fallback_agent: r.fallback_agent || '',
      time_windows,
    }
    try { await putRouting(name, body); toast('Routing saved', 'success') }
    catch (e: any) { toast(`Save failed: ${e.message || e}`, 'error') }
  }

  return (
    <Card className="p-5 space-y-4">
      <p className="text-[12px] text-dim">
        Routing decides which agent answers a given inbound number. The platform currently runs a single <span className="font-semibold text-soft">active</span> agent;
        these rules apply once per-DID routing is enabled.
      </p>
      <RoutingFields r={r} setR={setR} win={win} setWin={setWin} disabled={!loaded} />
      <div className="flex justify-end"><Button variant="primary" onClick={onSave} disabled={!loaded}><Save size={14} /> Save routing</Button></div>
    </Card>
  )
}

/* ───────────────────────── Documents tab ───────────────────────── */
function DocumentsTab({ name }: { name: string }) {
  const toast = useToast()
  const { data, isLoading, error } = useGet<{ documents: AgentDoc[] }>(['docs', name], `/agents/${name}/documents`)
  const refresh = useRefresh()
  const [busy, setBusy] = useState(false)
  const [diff, setDiff] = useState<{ docId: string; fields: any; missing: MissingField[] } | null>(null)

  const onUpload = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const formEl = e.currentTarget
    const form = new FormData(formEl)
    if (!(form.get('file') as File)?.size) { toast('Choose a file first', 'error'); return }
    setBusy(true)
    try { await uploadDoc(name, form); toast('Uploaded & embedded', 'success'); refresh(['docs', name]); formEl.reset() }
    catch (e: any) { toast(`Upload failed: ${e.message || e}`, 'error') }
    finally { setBusy(false) }
  }
  const onDelete = async (docId: string) => {
    try { await deleteDoc(name, docId); toast('Deleted', 'success'); refresh(['docs', name]) }
    catch (e: any) { toast(`Delete failed: ${e.message || e}`, 'error') }
  }
  const onExtract = async (docId: string) => {
    setBusy(true)
    try {
      const res = await extractYaml(name, docId)
      setDiff({ docId, fields: res.fields ?? (res as any).extracted ?? res, missing: res.missing ?? [] })
    }
    catch (e: any) { toast(`Extract failed: ${e.message || e}`, 'error') }
    finally { setBusy(false) }
  }
  const onApply = async (fields: any) => {
    if (!diff) return
    setBusy(true)
    try { await applyYaml(name, diff.docId, fields); toast('Applied to agent.yaml', 'success'); setDiff(null); refresh(['agent', name]) }
    catch (e: any) { toast(`Apply failed: ${e.message || e}`, 'error') }
    finally { setBusy(false) }
  }

  const docs = data?.documents ?? []
  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="eyebrow mb-3">Upload knowledge document (PDF / DOCX / TXT)</div>
        <form onSubmit={onUpload} className="grid gap-3 sm:grid-cols-2">
          <input name="file" type="file" accept=".pdf,.docx,.txt" className="text-[13px] text-soft file:mr-3 file:rounded-lg file:border-0 file:bg-[var(--surface-2)] file:px-3 file:py-1.5 file:text-[12px] file:font-semibold" />
          <input name="org_name" placeholder="Organisation name" className="rounded-lg bg-[var(--surface-2)] px-3 py-2 text-[13px] ring-1 ring-[var(--border)]" />
          <input name="doc_type" placeholder="Type (e.g. Brochure)" defaultValue="General" className="rounded-lg bg-[var(--surface-2)] px-3 py-2 text-[13px] ring-1 ring-[var(--border)]" />
          <input name="description" placeholder="Short description" className="rounded-lg bg-[var(--surface-2)] px-3 py-2 text-[13px] ring-1 ring-[var(--border)]" />
          <div className="sm:col-span-2"><Button variant="primary" disabled={busy}><Upload size={14} /> {busy ? 'Working…' : 'Upload'}</Button></div>
        </form>
        <p className="mt-2 text-[11px] text-dim">Text is extracted, chunked and embedded for in-call RAG. "Extract YAML" then proposes agent fields from a document.</p>
      </Card>

      <AsyncState loading={isLoading} error={error}>
        {docs.length === 0 ? (
          <EmptyState icon={<FileText size={20} />} title="No documents yet" hint="Upload a brochure or fact sheet to power RAG and field extraction." />
        ) : (
          <div className="space-y-2">
            {docs.map((doc) => (
              <Card key={doc.doc_id} className="flex flex-wrap items-center gap-3 p-4">
                <FileText size={16} className="text-indigo-400" />
                <div className="min-w-0">
                  <div className="truncate text-[13px] font-semibold text-[var(--text)]">{doc.filename || doc.doc_id}</div>
                  <div className="text-[11px] text-dim">{doc.doc_type || 'General'}{doc.org_name ? ` · ${doc.org_name}` : ''}{doc.chunks ? ` · ${doc.chunks} chunks` : ''}</div>
                </div>
                <div className="ml-auto flex items-center gap-2">
                  <Button variant="ghost" onClick={() => onExtract(doc.doc_id)} disabled={busy}><Sparkles size={14} /> Extract YAML</Button>
                  <Button variant="ghost" onClick={() => onDelete(doc.doc_id)}><Trash2 size={14} /></Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </AsyncState>

      {diff && (
        <ExtractReview
          fields={diff.fields}
          missing={diff.missing}
          busy={busy}
          onCancel={() => setDiff(null)}
          onApply={onApply}
        />
      )}
    </div>
  )
}

/* ─────────────────── extraction review modal ───────────────────
 * Editable per-field review of what the document yielded. Fields the
 * document did NOT contain are shown highlighted with an explicit ask, so
 * the user fills the gaps before applying. Empty values are dropped from
 * the applied payload (they never blank out existing YAML values). */
const EXTRACT_LABELS: Record<string, string> = {
  agent_name: 'Agent name', company_name: 'Company name', company_tagline: 'Company tagline',
  role_description: 'Agent role', contact: 'Contact number', call_direction: 'Call type',
  opening_greeting: 'Opening greeting', portfolio_prompt_text: 'Products / services',
  locations: 'Locations served', objection_patterns: 'Objection phrases',
  appointment_slots: 'Calendar slots', known_projects: 'Known products (JSON)',
  extra_always_rules: 'Always rules', extra_never_rules: 'Never rules',
}
const COMMA_KEYS = new Set(['locations', 'objection_patterns'])
const LINES_KEYS = new Set(['extra_always_rules', 'extra_never_rules'])
const LONG_KEYS = new Set(['opening_greeting', 'portfolio_prompt_text'])

function toDraft(key: string, val: any): string {
  if (val == null) return ''
  if (COMMA_KEYS.has(key)) return (val as any[]).join(', ')
  if (LINES_KEYS.has(key)) return (val as any[]).join('\n')
  if (key === 'appointment_slots')
    return (val as any[]).map((s) => `${s?.day ?? ''} at ${s?.time ?? ''}`).join('\n')
  if (typeof val === 'object') return JSON.stringify(val, null, 2)
  return String(val)
}

function fromDraft(key: string, text: string): any | undefined {
  const t = text.trim()
  if (!t) return undefined
  if (COMMA_KEYS.has(key)) return t.split(',').map((s) => s.trim()).filter(Boolean)
  if (LINES_KEYS.has(key)) return t.split('\n').map((s) => s.trim()).filter(Boolean)
  if (key === 'appointment_slots') {
    const slots = t.split('\n').map((line) => {
      const [day, ...rest] = line.split(' at ')
      return { day: (day || '').trim(), time: rest.join(' at ').trim() }
    }).filter((s) => s.day && s.time)
    return slots.length ? slots : undefined
  }
  if (key === 'known_projects') { try { return JSON.parse(t) } catch { return undefined } }
  return t
}

function ExtractReview({ fields, missing, busy, onCancel, onApply }: {
  fields: Record<string, any>
  missing: MissingField[]
  busy: boolean
  onCancel: () => void
  onApply: (fields: Record<string, any>) => void
}) {
  const missingKeys = new Set(missing.map((m) => m.key))
  const orderedKeys = [
    ...Object.keys(EXTRACT_LABELS).filter((k) => k in fields || missingKeys.has(k)),
    ...Object.keys(fields).filter((k) => !(k in EXTRACT_LABELS)),
  ]
  const [draft, setDraft] = useState<Record<string, string>>(() => {
    const d: Record<string, string> = {}
    orderedKeys.forEach((k) => { d[k] = toDraft(k, fields[k]) })
    return d
  })

  const apply = () => {
    const out: Record<string, any> = {}
    orderedKeys.forEach((k) => {
      const v = fromDraft(k, draft[k] ?? '')
      if (v !== undefined) out[k] = v
    })
    onApply(out)
  }

  const stillEmpty = missing.filter((m) => !(draft[m.key] ?? '').trim())

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" onClick={onCancel}>
      <Card className="max-h-[85vh] w-full max-w-2xl overflow-auto p-6" >
        <div onClick={(e) => e.stopPropagation()}>
          <div className="mb-3 flex items-center gap-2">
            <Sparkles size={16} className="text-mint-500" />
            <h3 className="font-display text-[15px] font-bold">Review extracted fields</h3>
            <div className="ml-auto flex gap-2">
              <Button variant="ghost" onClick={onCancel}>Cancel</Button>
              <Button variant="primary" onClick={apply} disabled={busy}>Apply to agent.yaml</Button>
            </div>
          </div>
          {stillEmpty.length > 0 && (
            <div className="mb-3 rounded-lg bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 ring-1 ring-amber-500/20 dark:text-amber-300">
              <span className="font-semibold">The document didn't contain:</span>{' '}
              {stillEmpty.map((m) => m.label).join(', ')} — please fill them in below (or apply without them and
              set them later in Settings).
            </div>
          )}
          <div className="space-y-3">
            {orderedKeys.map((k) => {
              const isMissing = missingKeys.has(k) && !(draft[k] ?? '').trim()
              const hint = missing.find((m) => m.key === k)?.hint
              const label = EXTRACT_LABELS[k] ?? k
              return (
                <div key={k} className={cx(isMissing && 'rounded-lg bg-amber-500/5 p-2 ring-1 ring-amber-500/20')}>
                  <Field label={isMissing ? `${label} — needed` : label}>
                    {k === 'call_direction' ? (
                      <Select value={draft[k] ?? ''} onChange={(e) => setDraft((p) => ({ ...p, [k]: e.target.value }))}>
                        <option value="">(not set)</option>
                        <option value="inbound">inbound — answers calls</option>
                        <option value="outbound">outbound — places calls</option>
                      </Select>
                    ) : LONG_KEYS.has(k) || LINES_KEYS.has(k) || k === 'appointment_slots' || k === 'known_projects' || (draft[k] ?? '').includes('\n') ? (
                      <Textarea
                        rows={k === 'portfolio_prompt_text' ? 6 : 3}
                        value={draft[k] ?? ''}
                        placeholder={hint}
                        onChange={(e) => setDraft((p) => ({ ...p, [k]: e.target.value }))}
                      />
                    ) : (
                      <Input
                        value={draft[k] ?? ''}
                        placeholder={hint}
                        onChange={(e) => setDraft((p) => ({ ...p, [k]: e.target.value }))}
                      />
                    )}
                  </Field>
                  {k === 'appointment_slots' && <span className="mt-1 block text-[11px] text-dim">One slot per line, e.g. "Tomorrow at 10:00 AM"</span>}
                </div>
              )
            })}
          </div>
        </div>
      </Card>
    </div>
  )
}

/* ───────────────────────── Voice tab ───────────────────────── */
function VoiceTab({ agent }: { agent: any }) {
  const toast = useToast()
  const voices: Record<string, { model: string; speaker: string }> = agent?.languages?.tts_voices ?? {}
  const entries = Object.entries(voices)

  const play = (lang: string, v: { model: string; speaker: string }) => {
    try { new Audio(ttsPreviewUrl(v.speaker, v.model, BCP47[lang] || 'en-IN')).play() }
    catch (e: any) { toast(`Preview failed: ${e.message || e}`, 'error') }
  }

  if (entries.length === 0) return <EmptyState icon={<Volume2 size={20} />} title="No voices configured" hint="Add tts_voices in the YAML definition." />
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {entries.map(([lang, v]) => (
        <Card key={lang} className="flex items-center gap-3 p-4">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--surface-2)] text-indigo-400 ring-1 ring-[var(--border)]"><Volume2 size={18} /></span>
          <div className="min-w-0">
            <div className="text-[13px] font-semibold text-[var(--text)]">{lang.toUpperCase()}</div>
            <div className="truncate text-[11px] text-dim">{v.model} · {v.speaker}</div>
          </div>
          <Button variant="ghost" className="ml-auto" onClick={() => play(lang, v)}><Volume2 size={14} /> Preview</Button>
        </Card>
      ))}
    </div>
  )
}
