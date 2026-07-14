import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useGet, useRefresh } from '../../api/hooks/useApi'
import {
  getAgent, updateAgent, agentToWizardData, mergeWizardIntoDefinition,
  getRawYaml, putRawYaml, validateRaw, getRouting, putRouting,
  uploadDoc, deleteDoc, extractYaml, applyYaml, deleteBooking,
  activateAgent, archiveAgent, ttsPreviewUrl,
  listRevisions, rollbackAgent, patchAgentOps,
  type Routing, type AgentDoc, type WizardData, type MissingField, type Booking,
  type AgentRevision, type AgentOp,
} from '../../api/agents'
import { Card, Button, Badge, Spinner, cx, EmptyState } from '../../components/ui'
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
  CalendarDays, History, ListOrdered, ArrowUp, ArrowDown, Plus, RotateCcw,
} from 'lucide-react'

type Tab = 'settings' | 'stages' | 'yaml' | 'routing' | 'documents' | 'voice' | 'history'
const BCP47: Record<string, string> = { en: 'en-IN', hi: 'hi-IN', gu: 'gu-IN', mr: 'hi-IN' }

export default function EditAgent() {
  const { name = '' } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const refresh = useRefresh()
  const [tab, setTab] = useState<Tab>('settings')
  // Unsaved-edit guard: tabs unmount on switch, so a dirty Settings/YAML tab
  // would silently lose its edits without this.
  const dirtyRef = useRef(false)
  const setDirty = (v: boolean) => { dirtyRef.current = v }
  const switchTab = (t: Tab) => {
    if (t === tab) return
    if (dirtyRef.current && !confirm('You have unsaved changes in this tab. Discard them?')) return
    dirtyRef.current = false
    setTab(t)
  }
  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [])

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
    { id: 'stages', label: 'Stages', icon: ListOrdered },
    { id: 'yaml', label: 'Advanced (YAML)', icon: Code2 },
    { id: 'routing', label: 'Routing', icon: Network },
    { id: 'documents', label: 'Documents · RAG', icon: Files },
    { id: 'voice', label: 'Voice', icon: Volume2 },
    { id: 'history', label: 'History', icon: History },
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
            <Button variant="danger" title="Archive agent" onClick={onArchive}>
              <Archive size={14} /><span className="hidden sm:inline">Archive</span>
            </Button>
          </div>
        }
      />

      <AsyncState loading={isLoading} error={error}>
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Agent editor sections">
          {tabs.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => switchTab(t.id)}
              className={cx('flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold ring-1 transition',
                tab === t.id ? 'bg-mint-400/15 text-mint-700 ring-mint-400/30 dark:text-mint-300' : 'bg-[var(--surface-2)] text-soft ring-[var(--border)] hover:text-[var(--text)]')}>
              <t.icon size={14} /> {t.label}
            </button>
          ))}
        </div>

        {tab === 'settings' && <SettingsTab name={name} onSaved={() => refresh(['agent', name], ['agents'])} onDirtyChange={setDirty} />}
        {tab === 'stages' && <StagesTab name={name} onSaved={() => refresh(['agent', name], ['agents'])} />}
        {tab === 'yaml' && <YamlTab name={name} onSaved={() => refresh(['agent', name], ['agents'])} onDirtyChange={setDirty} />}
        {tab === 'routing' && <RoutingTab name={name} />}
        {tab === 'documents' && <DocumentsTab name={name} />}
        {tab === 'voice' && <VoiceTab agent={agent} />}
        {tab === 'history' && <HistoryTab name={name} onChanged={() => refresh(['agent', name], ['agents'])} />}
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
const SETTINGS_SECTIONS = [
  { id: 'sec-basics', label: 'Basics' },
  { id: 'sec-call-type', label: 'Call Type' },
  { id: 'sec-languages', label: 'Languages & Voice' },
  { id: 'sec-personality', label: 'Personality' },
  { id: 'sec-script', label: 'Call Script' },
  { id: 'sec-knowledge', label: 'Knowledge' },
  { id: 'sec-calendar', label: 'Calendar' },
  { id: 'sec-rules', label: 'Objections & Rules' },
]

function SettingsTab({ name, onSaved, onDirtyChange }: { name: string; onSaved: () => void; onDirtyChange?: (dirty: boolean) => void }) {
  const toast = useToast()
  const [d, setD] = useState<WizardData | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [showErrors, setShowErrors] = useState(false)
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null)
  const [justSaved, setJustSaved] = useState(false)
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

  const dirty = !!d && !!initRef.current && JSON.stringify(d) !== JSON.stringify(initRef.current)
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])

  // Per-section validity — powers the inline "needs attention" markers.
  const invalid = {
    'sec-basics': !!d && (!d.agent_name || !d.company_name),
    'sec-languages': !!d && d.supported.length === 0,
    'sec-script': !!d && (!d.opening_greeting || d.stages.length === 0),
  } as Record<string, boolean>

  const onSave = async () => {
    if (!d || !initRef.current) return
    if (!d.agent_name || !d.company_name || !d.opening_greeting || d.supported.length === 0 || d.stages.length === 0) {
      setShowErrors(true)
      toast('Agent name, company, opening greeting, at least one language and one stage are required', 'error')
      const firstInvalid = SETTINGS_SECTIONS.find((s) => invalid[s.id])?.id
      if (firstInvalid) document.getElementById(firstInvalid)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      return
    }
    setSaving(true)
    try {
      const fresh = await getAgent(name) // merge onto the LATEST definition, not a stale copy
      const rev: string | undefined = fresh?._rev
      delete fresh._rev
      const merged = mergeWizardIntoDefinition(fresh, d, initRef.current)
      delete merged._rev
      await updateAgent(name, merged, rev)
      initRef.current = JSON.parse(JSON.stringify(d))
      setD((p) => (p ? { ...p } : p)) // re-render so dirty recomputes against the new snapshot
      setShowErrors(false)
      setLastSavedAt(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }))
      setJustSaved(true)
      setTimeout(() => setJustSaved(false), 1500)
      toast('Settings saved — effective on the next call', 'success')
      onSaved()
    } catch (e: any) {
      if (String(e?.message || e).startsWith('409')) {
        toast('This agent changed while you were editing (e.g. by the Copilot). '
          + 'Your form is untouched — press Save again to merge onto the latest version.', 'error')
      } else {
        toast(`Save failed: ${e.message || e}`, 'error')
      }
    } finally {
      setSaving(false)
    }
  }

  const saveBtn = (
    <Button variant="primary" onClick={onSave} disabled={!d || saving}>
      {justSaved ? <><CheckCircle2 size={14} /> Saved</> : <><Save size={14} /> {saving ? 'Saving…' : 'Save settings'}</>}
    </Button>
  )

  const sectionHead = (id: string, label: string) => (
    <div className="eyebrow mb-4 flex items-center gap-2">
      {label}
      {showErrors && invalid[id] && <Badge tone="rose" soft>needs attention</Badge>}
    </div>
  )
  const cardCls = (id: string) => cx('card p-4 sm:p-6 scroll-mt-24', showErrors && invalid[id] && 'ring-1 ring-rose-500/40')

  return (
    <AsyncState loading={!d && !loadError} error={loadError}>
      {d && (
        <div className="space-y-4 scroll-smooth">
          <Card className="flex flex-wrap items-center gap-3 p-4">
            <p className="min-w-[240px] flex-1 text-[12px] text-dim">
              Structured editor for the common agent fields. Anything not shown here (STT corrections,
              QA signals, per-language register notes, …) is preserved untouched —
              use the <span className="font-semibold text-soft">Advanced (YAML)</span> tab for those.
            </p>
            {saveBtn}
          </Card>

          {/* Section jump nav — 8 stacked cards need a map */}
          <nav aria-label="Settings sections" className="sticky top-2 z-10 flex gap-1.5 overflow-x-auto rounded-xl glass px-3 py-2 sm:flex-wrap sm:overflow-visible">
            {SETTINGS_SECTIONS.map((s) => (
              <a key={s.id} href={`#${s.id}`}
                className="shrink-0 whitespace-nowrap rounded-lg bg-[var(--surface-2)] px-3 py-2 text-[12px] font-semibold text-soft ring-1 ring-[var(--border)] transition hover:text-[var(--text)] sm:px-2.5 sm:py-1">
                {s.label}
              </a>
            ))}
          </nav>

          <div id="sec-basics" className={cardCls('sec-basics')}>{sectionHead('sec-basics', 'Basics')}<BasicsSection d={d} set={set} showSlug={false} showErrors={showErrors} /></div>
          <div id="sec-call-type" className="card p-4 sm:p-6 scroll-mt-24"><div className="eyebrow mb-4">Call Type</div><CallDirectionSection d={d} set={set} /></div>
          <div id="sec-languages" className={cardCls('sec-languages')}>
            {sectionHead('sec-languages', 'Languages & Voice')}
            <LanguagesSection d={d} set={set} />
            {showErrors && invalid['sec-languages'] && <span className="mt-2 block text-[11px] text-rose-500">Select at least one language.</span>}
          </div>
          <div id="sec-personality" className="card p-4 sm:p-6 scroll-mt-24"><div className="eyebrow mb-4">Personality</div><PersonalitySection d={d} set={set} /></div>
          <div id="sec-script" className={cardCls('sec-script')}>{sectionHead('sec-script', 'Call Script — Opening & Stages')}<CallFlowSection d={d} set={set} showErrors={showErrors} /></div>
          <div id="sec-knowledge" className="card p-4 sm:p-6 scroll-mt-24"><div className="eyebrow mb-4">Knowledge</div><KnowledgeSection d={d} set={set} /></div>
          <div id="sec-calendar" className="card p-4 sm:p-6 scroll-mt-24">
            <div className="eyebrow mb-4">Calendar &amp; Appointments</div>
            <CalendarSection d={d} set={set} />
            <BookingsList name={name} />
          </div>
          <div id="sec-rules" className="card p-4 sm:p-6 scroll-mt-24"><div className="eyebrow mb-4">Objections &amp; Rules</div><ResponsesSection d={d} set={set} /></div>

          {/* Sticky save bar — reachable from anywhere in the long form */}
          <div className="sticky bottom-4 z-10 flex flex-wrap items-center justify-end gap-3 rounded-2xl glass px-4 py-3">
            {dirty && <Badge tone="amber" soft>Unsaved changes</Badge>}
            {lastSavedAt && <span className="hidden sm:inline text-[12px] text-dim">Saved {lastSavedAt} — effective on the next call</span>}
            {saveBtn}
          </div>
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

  if (!data) return null
  return (
    <div className="mt-5">
      <div className="eyebrow mb-2 flex items-center gap-1.5"><CalendarDays size={13} /> Booked slots ({bookings.length})</div>
      {bookings.length === 0 && (
        <div className="rounded-lg bg-[var(--surface-2)] px-3 py-2 text-[12px] text-dim ring-1 ring-[var(--border)]">
          No appointments yet — when a caller books a slot on a live call it appears here, and deleting
          it frees the slot for future calls.
        </div>
      )}
      <div className="space-y-2">
        {bookings.map((b, i) => (
          <div key={`${b.conn_id}-${i}`} className="flex flex-wrap items-center gap-3 rounded-lg bg-[var(--surface-2)] px-3 py-2 ring-1 ring-[var(--border)]">
            <span className="text-[13px] font-semibold">{b.appointment_time}</span>
            <span className="text-[12px] text-soft">{b.caller_name || 'Unknown caller'}{b.caller_phone ? ` · ${b.caller_phone}` : ''}</span>
            <span className="ml-auto text-[11px] text-dim">{b.booked_at?.slice(0, 16).replace('T', ' ') || ''}</span>
            <Button variant="ghost" size="sm" onClick={() => onFree(i)} title="Free this slot — the agent will offer it again"><Trash2 size={13} /></Button>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ───────────────────────── YAML tab ───────────────────────── */
function YamlTab({ name, onSaved, onDirtyChange }: { name: string; onSaved: () => void; onDirtyChange?: (dirty: boolean) => void }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [result, setResult] = useState<{ valid: boolean; error?: string; agent_id?: string } | null>(null)
  const [validatedText, setValidatedText] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const loadedTextRef = useRef('')

  useEffect(() => {
    let on = true
    getRawYaml(name).then((t) => { if (on) { setText(t); loadedTextRef.current = t; setLoaded(true) } }).catch((e) => toast(`Load failed: ${e.message || e}`, 'error'))
    return () => { on = false }
  }, [name])

  const edited = loaded && text !== loadedTextRef.current
  useEffect(() => { onDirtyChange?.(edited) }, [edited, onDirtyChange])

  const onValidate = async () => {
    try { setResult(await validateRaw(name, text)) } catch (e: any) { setResult({ valid: false, error: e.message || String(e) }) }
    setValidatedText(text)
  }
  const onSave = async () => {
    setSaving(true)
    try {
      const v = await validateRaw(name, text)
      setResult(v)
      setValidatedText(text)
      if (!v.valid) { toast('Fix validation errors before saving', 'error'); return }
      // If-Match: the current on-disk rev, so a concurrent Copilot edit 409s
      // instead of being silently clobbered.
      let rev: string | undefined
      try { rev = (await getAgent(name))?._rev } catch { /* legacy last-write-wins */ }
      await putRawYaml(name, text, rev)
      loadedTextRef.current = text
      toast('Saved — effective on the next call', 'success')
      onSaved()
    } catch (e: any) {
      if (String(e?.message || e).startsWith('409')) {
        toast('This agent changed since you loaded it (edited by the Copilot?). '
          + 'Reload the YAML tab and reapply your edit.', 'error')
      } else {
        toast(`Save failed: ${e.message || e}`, 'error')
      }
    }
    finally { setSaving(false) }
  }

  const stale = result !== null && text !== validatedText

  return (
    <Card className="p-5 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="eyebrow min-w-0 max-w-full truncate">agents/{name}/agent.yaml</span>
        {edited && <Badge tone="amber" soft>edited</Badge>}
        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" onClick={onValidate} disabled={!loaded}><CheckCircle2 size={14} /> Validate</Button>
          <Button variant="primary" onClick={onSave} disabled={!loaded || saving}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
      {result && (
        <div
          role={result.valid && !stale ? 'status' : 'alert'}
          className={cx('flex items-start gap-2 rounded-lg px-3 py-2 text-[12px] ring-1',
            stale ? 'bg-[var(--surface-2)] text-dim ring-[var(--border)]'
              : result.valid ? 'bg-mint-400/10 text-mint-700 ring-mint-400/20 dark:text-mint-300' : 'bg-rose-500/10 text-rose-600 ring-rose-500/20 dark:text-rose-300')}>
          {stale ? <AlertTriangle size={14} className="mt-0.5" /> : result.valid ? <CheckCircle2 size={14} className="mt-0.5" /> : <AlertTriangle size={14} className="mt-0.5" />}
          <span>{stale ? 'YAML changed since this check — validate again' : result.valid ? `Valid — ${result.agent_id}` : result.error}</span>
        </div>
      )}
      <Textarea
        rows={26}
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); if (loaded && !saving) onSave() } }}
        className="font-mono text-[12px] leading-relaxed"
      />
      <p className="text-[11px] text-dim">Tip: Ctrl/Cmd+S validates and saves.</p>
    </Card>
  )
}

/* ───────────────────────── Routing tab ───────────────────────── */
function RoutingTab({ name }: { name: string }) {
  const toast = useToast()
  const [r, setR] = useState<Routing>({ did: '', caller_id_prefixes: [], fallback_agent: '', time_windows: [] })
  const [win, setWin] = useState<TimeWindowDraft>({ start: '', end: '' })
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)

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
    setSaving(true)
    try { await putRouting(name, body); toast('Routing saved', 'success') }
    catch (e: any) { toast(`Save failed: ${e.message || e}`, 'error') }
    finally { setSaving(false) }
  }

  return (
    <Card className="p-5 space-y-4">
      <p className="text-[12px] text-dim">
        Routing decides which agent answers a given inbound number. The platform currently runs a single <span className="font-semibold text-soft">active</span> agent;
        these rules apply once per-DID routing is enabled.
      </p>
      <RoutingFields r={r} setR={setR} win={win} setWin={setWin} disabled={!loaded} />
      <div className="flex justify-end"><Button variant="primary" onClick={onSave} disabled={!loaded || saving}><Save size={14} /> {saving ? 'Saving…' : 'Save routing'}</Button></div>
    </Card>
  )
}

/* ───────────────────────── Documents tab ───────────────────────── */
function DocumentsTab({ name }: { name: string }) {
  const toast = useToast()
  const { data, isLoading, error } = useGet<{ documents: AgentDoc[] }>(['docs', name], `/agents/${name}/documents`)
  const refresh = useRefresh()
  const [busy, setBusy] = useState(false)
  const [extractingId, setExtractingId] = useState<string | null>(null)
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
  const onDelete = async (docId: string, filename?: string) => {
    if (!confirm(`Delete "${filename || docId}" and its embeddings?`)) return
    try { await deleteDoc(name, docId); toast('Deleted', 'success'); refresh(['docs', name]) }
    catch (e: any) { toast(`Delete failed: ${e.message || e}`, 'error') }
  }
  const onExtract = async (docId: string) => {
    setBusy(true)
    setExtractingId(docId)
    try {
      const res = await extractYaml(name, docId)
      setDiff({ docId, fields: res.fields ?? (res as any).extracted ?? res, missing: res.missing ?? [] })
    }
    catch (e: any) { toast(`Extract failed: ${e.message || e}`, 'error') }
    finally { setBusy(false); setExtractingId(null) }
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
          <Field label="Document file">
            <input name="file" type="file" accept=".pdf,.docx,.txt" className="w-full text-[13px] text-soft file:mr-3 file:rounded-lg file:border-0 file:bg-[var(--surface-2)] file:px-3 file:py-1.5 file:text-[12px] file:font-semibold" />
          </Field>
          <Field label="Organisation name">
            <input name="org_name" placeholder="Organisation name" className="w-full rounded-lg bg-[var(--surface-2)] px-3 py-2 text-[13px] ring-1 ring-[var(--border)]" />
          </Field>
          <Field label="Document type">
            <input name="doc_type" placeholder="Type (e.g. Brochure)" defaultValue="General" className="w-full rounded-lg bg-[var(--surface-2)] px-3 py-2 text-[13px] ring-1 ring-[var(--border)]" />
          </Field>
          <Field label="Short description">
            <input name="description" placeholder="Short description" className="w-full rounded-lg bg-[var(--surface-2)] px-3 py-2 text-[13px] ring-1 ring-[var(--border)]" />
          </Field>
          <div className="sm:col-span-2"><Button variant="primary" disabled={busy}><Upload size={14} /> {busy ? 'Uploading & embedding…' : 'Upload'}</Button></div>
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
                  <Button variant="ghost" onClick={() => onExtract(doc.doc_id)} disabled={busy}>
                    {extractingId === doc.doc_id
                      ? <><Spinner className="h-3.5 w-3.5" /> Extracting…</>
                      : <><Sparkles size={14} /> Extract YAML</>}
                  </Button>
                  <Button variant="ghost" title={`Delete ${doc.filename || 'document'}`} onClick={() => onDelete(doc.doc_id, doc.filename)}><Trash2 size={14} /></Button>
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
  // Snapshot of the initial draft so dismissal can warn about typed-in edits.
  const initialRef = useRef<Record<string, string> | null>(null)
  if (initialRef.current === null) initialRef.current = { ...draft }
  const changed = orderedKeys.some((k) => (draft[k] ?? '') !== (initialRef.current?.[k] ?? ''))
  const firstMissingKey = missing.find((m) => !(draft[m.key] ?? '').trim())?.key

  const requestCancel = () => {
    if (changed && !confirm('Discard the extracted fields and your edits?')) return
    onCancel()
  }

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') requestCancel() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changed])

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
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="extract-review-title"
      onClick={requestCancel}
    >
      <Card className="max-h-[85vh] w-full max-w-2xl overflow-auto p-4 sm:p-6" >
        <div onClick={(e) => e.stopPropagation()}>
          <div className="sticky top-0 z-10 -mx-4 -mt-4 mb-3 flex flex-wrap items-center gap-2 border-b border-[var(--border)] bg-[var(--surface-solid)] px-4 py-4 sm:-mx-6 sm:-mt-6 sm:px-6">
            <Sparkles size={16} className="text-mint-500" />
            <h3 id="extract-review-title" className="font-display text-[15px] font-bold">Review extracted fields</h3>
            <div className="flex w-full justify-end gap-2 sm:ml-auto sm:w-auto">
              <Button variant="ghost" onClick={requestCancel}>Cancel</Button>
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
                        autoFocus={k === firstMissingKey}
                        onChange={(e) => setDraft((p) => ({ ...p, [k]: e.target.value }))}
                      />
                    ) : (
                      <Input
                        value={draft[k] ?? ''}
                        placeholder={hint}
                        autoFocus={k === firstMissingKey}
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
  const [playingLang, setPlayingLang] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const play = (lang: string, v: { model: string; speaker: string }) => {
    try {
      audioRef.current?.pause()
      const audio = new Audio(ttsPreviewUrl(v.speaker, v.model, BCP47[lang] || 'en-IN'))
      audioRef.current = audio
      setPlayingLang(lang)
      audio.onended = () => setPlayingLang(null)
      audio.onerror = () => { setPlayingLang(null); toast('Preview failed to load', 'error') }
      audio.play().catch((e: any) => { setPlayingLang(null); toast(`Preview failed: ${e.message || e}`, 'error') })
    } catch (e: any) { setPlayingLang(null); toast(`Preview failed: ${e.message || e}`, 'error') }
  }

  if (entries.length === 0) return <EmptyState icon={<Volume2 size={20} />} title="No voices configured" hint="Add tts_voices in the YAML definition." />
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {entries.map(([lang, v]) => (
        <Card key={lang} className="flex items-center gap-3 p-4">
          <span className={cx('grid h-10 w-10 place-items-center rounded-xl bg-[var(--surface-2)] ring-1 ring-[var(--border)]',
            playingLang === lang ? 'animate-pulse text-mint-500' : 'text-indigo-400')}><Volume2 size={18} /></span>
          <div className="min-w-0">
            <div className="text-[13px] font-semibold text-[var(--text)]">{lang.toUpperCase()}</div>
            <div className="truncate text-[11px] text-dim">{v.model} · {v.speaker}</div>
          </div>
          <Button variant="ghost" className="ml-auto" onClick={() => play(lang, v)}>
            {playingLang === lang ? <><Spinner className="h-3.5 w-3.5" /> Playing…</> : <><Volume2 size={14} /> Preview</>}
          </Button>
        </Card>
      ))}
    </div>
  )
}

/* ───────────────────────── Stages tab (v2 surgical ops) ─────────────────────
 * Visual stage list wired to PATCH /api/agents/{name}/ops — the SAME surgical
 * machinery the Copilot uses, so both paths converge on the server store
 * (revisioned, concurrency-safe, scope-proven). */
type StageRow = {
  id: string
  goal: string
  turns_until_advance?: number
  role?: string
  [k: string]: any
}

function StagesTab({ name, onSaved }: { name: string; onSaved: () => void }) {
  const toast = useToast()
  const [stages, setStages] = useState<StageRow[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)
  const [goalDraft, setGoalDraft] = useState('')

  const load = () => {
    getAgent(name)
      .then((def) => setStages(def?.call_flow?.stages ?? []))
      .catch((e) => toast(`Load failed: ${e.message || e}`, 'error'))
  }
  useEffect(load, [name]) // eslint-disable-line react-hooks/exhaustive-deps

  const runOps = async (ops: AgentOp[], what: string) => {
    setBusy(true)
    try {
      const res = await patchAgentOps(name, ops)
      toast(`${what} — revision ${res.revision}, effective on the next call`, 'success')
      onSaved()
      load()
    } catch (e: any) {
      if (String(e?.message || e).startsWith('409')) {
        toast('The agent changed underneath this view — reloading.', 'error')
        load()
      } else {
        toast(`${what} failed: ${e.message || e}`, 'error')
      }
    } finally {
      setBusy(false)
      setEditing(null)
    }
  }

  const move = (i: number, dir: -1 | 1) => {
    const to = i + dir
    if (!stages || to < 0 || to >= stages.length) return
    void runOps(
      [{ op: 'move', path: `call_flow.stages[${i}]`, to },
       // renumber order ints server-side too (set both swapped orders)
       { op: 'set', path: `call_flow.stages[${to}].order`, value: to },
       { op: 'set', path: `call_flow.stages[${i}].order`, value: i }],
      `Moved ${stages[i].id}`)
  }

  const remove = (i: number) => {
    if (!stages || stages.length <= 1) return
    if (!confirm(`Remove stage ${stages[i].id}?`)) return
    const renumber: AgentOp[] = stages
      .filter((_, j) => j !== i)
      .map((_, j) => ({ op: 'set' as const, path: `call_flow.stages[${j}].order`, value: j }))
    void runOps(
      [{ op: 'remove_at', path: `call_flow.stages[${i}]` }, ...renumber],
      `Removed ${stages[i].id}`)
  }

  const saveGoal = (i: number) => {
    if (!stages) return
    void runOps(
      [{ op: 'set', path: `call_flow.stages[${i}].goal`, value: goalDraft }],
      `Updated ${stages[i].id}`)
  }

  const addStage = () => {
    if (!stages) return
    const id = prompt('New stage id (e.g. HANDLE_REJECTION):')?.trim().toUpperCase().replace(/\s+/g, '_')
    if (!id) return
    if (stages.some((s) => s.id === id)) { toast(`Stage ${id} already exists`, 'error'); return }
    void runOps(
      [{ op: 'append', path: 'call_flow.stages',
         value: { id, order: stages.length, goal: 'Describe what this stage must accomplish.', turns_until_advance: 8 } }],
      `Added ${id}`)
  }

  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center gap-2">
        <span className="eyebrow">Call stages — surgical edits, versioned</span>
        <div className="ml-auto">
          <Button variant="ghost" size="sm" disabled={busy || !stages} onClick={addStage}>
            <Plus size={13} /> Add stage
          </Button>
        </div>
      </div>
      {!stages ? <Spinner /> : stages.length === 0 ? (
        <EmptyState title="No stages" hint="Add the first stage of the call flow." />
      ) : (
        <div className="space-y-2">
          {stages.map((s, i) => (
            <div key={`${s.id}-${i}`} className="rounded-xl bg-[var(--surface-2)] p-3 ring-1 ring-[var(--border)]">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="mint" soft>{i + 1}</Badge>
                <span className="font-mono text-[12.5px] font-semibold">{s.id}</span>
                {s.role && <Badge soft>{s.role}</Badge>}
                {s.turns_until_advance != null && (
                  <span className="text-[11px] text-dim">advance after {s.turns_until_advance} turns</span>
                )}
                <div className="ml-auto flex items-center gap-1">
                  <Button variant="ghost" size="sm" disabled={busy || i === 0} onClick={() => move(i, -1)} title="Move up"><ArrowUp size={13} /></Button>
                  <Button variant="ghost" size="sm" disabled={busy || i === stages.length - 1} onClick={() => move(i, 1)} title="Move down"><ArrowDown size={13} /></Button>
                  <Button variant="ghost" size="sm" disabled={busy}
                    onClick={() => { setEditing(editing === i ? null : i); setGoalDraft(s.goal || '') }}
                    title="Edit the stage goal">
                    <Code2 size={13} />
                  </Button>
                  <Button variant="ghost" size="sm" disabled={busy || stages.length <= 1} onClick={() => remove(i)} title="Remove stage"><Trash2 size={13} /></Button>
                </div>
              </div>
              {editing === i ? (
                <div className="mt-2 space-y-2">
                  <Textarea rows={3} value={goalDraft} onChange={(e) => setGoalDraft(e.target.value)} className="text-[12.5px]" />
                  <div className="flex gap-2">
                    <Button size="sm" disabled={busy || !goalDraft.trim()} onClick={() => saveGoal(i)}><Save size={13} /> Save goal</Button>
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(null)}>Cancel</Button>
                  </div>
                </div>
              ) : (
                <p className="mt-1.5 text-[12px] leading-relaxed text-soft">{s.goal}</p>
              )}
            </div>
          ))}
        </div>
      )}
      <p className="text-[11px] text-dim">
        Each edit lands as a single surgical operation — versioned in History and provably touching nothing else.
      </p>
    </Card>
  )
}

/* ───────────────────────── History tab (v2 revisions) ───────────────────── */
function HistoryTab({ name, onChanged }: { name: string; onChanged: () => void }) {
  const toast = useToast()
  const [revs, setRevs] = useState<AgentRevision[] | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    listRevisions(name)
      .then((r) => setRevs(r.revisions ?? []))
      .catch((e) => toast(`Load failed: ${e.message || e}`, 'error'))
  }
  useEffect(load, [name]) // eslint-disable-line react-hooks/exhaustive-deps

  const onRollback = async (rev: AgentRevision) => {
    if (!confirm(`Restore the agent to how it was BEFORE this change?\n\n${rev.reason || rev.actor || `revision ${rev.rev_id}`}`)) return
    setBusy(true)
    try {
      const res = await rollbackAgent(name, rev.rev_id)
      toast(`Rolled back — now revision ${res.revision}, effective on the next call`, 'success')
      onChanged()
      load()
    } catch (e: any) {
      toast(`Rollback failed: ${e.message || e}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="space-y-3 p-5">
      <span className="eyebrow">Edit history — every change is reversible</span>
      {!revs ? <Spinner /> : revs.length === 0 ? (
        <EmptyState
          title="No revisions yet"
          hint="Every save (Copilot, Settings, YAML, Stages) snapshots the agent here first."
        />
      ) : (
        <div className="space-y-2">
          {revs.map((r) => (
            <div key={r.rev_id} className="flex flex-wrap items-center gap-3 rounded-lg bg-[var(--surface-2)] px-3 py-2 ring-1 ring-[var(--border)]">
              <Badge soft>#{r.rev_id}</Badge>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[12.5px] font-semibold">{r.reason || 'edit'}</div>
                <div className="text-[11px] text-dim">
                  {r.actor || 'unknown'} · {String(r.created_at).slice(0, 16).replace('T', ' ')}
                </div>
              </div>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => onRollback(r)}
                title="Restore the agent to the state saved in this snapshot">
                <RotateCcw size={13} /> Restore
              </Button>
            </div>
          ))}
        </div>
      )}
      <p className="text-[11px] text-dim">
        Snapshots are taken BEFORE each write (last 50 kept). Restoring is itself snapshotted, so a rollback can be undone.
      </p>
    </Card>
  )
}
