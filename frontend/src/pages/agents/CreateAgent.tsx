import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  buildAgentPayload, createAgent, putRouting, activateAgent,
  extractFromDocument, extractedFieldsToWizardPatch,
  type WizardData, type Routing, type MissingField,
} from '../../api/agents'
import { Card, Button, Badge, Spinner, cx } from '../../components/ui'
import { PageHeader } from '../../components/kit'
import { useToast } from '../../components/Toast'
import {
  BasicsSection, CallDirectionSection, CalendarSection, LanguagesSection,
  PersonalitySection, KnowledgeSection, CallFlowSection, ResponsesSection,
  RoutingFields, SLUG_RE, type TimeWindowDraft,
} from './AgentFormSections'
import { ChevronLeft, ChevronRight, FileUp, Wand2, Check, AlertTriangle } from 'lucide-react'

const STEPS = ['Basics', 'Languages', 'Personality', 'Knowledge', 'Call Script', 'Calendar', 'Objections & Rules', 'Routing', 'Review']

const DEFAULT_STAGES = [
  { id: 'GREETING', goal: 'Greet the caller warmly, introduce yourself, and ask how you can help.', turns: '2' },
  { id: 'DISCOVERY', goal: "Understand the caller's needs and qualify their interest.", turns: '4' },
  { id: 'PITCH', goal: 'Present the most relevant option and explain its value clearly.', turns: '4' },
  { id: 'OBJECTION', goal: 'Address concerns honestly and build confidence.', turns: '3' },
  { id: 'CLOSING', goal: 'Confirm the next step (visit/callback) and close politely.', turns: '99' },
]

// Outbound calls need a pitch-shaped flow, not the inbound welcome→discovery
// skeleton — mirrors the copilot backend's _DEFAULT_OUTBOUND_STAGES.
const DEFAULT_OUTBOUND_STAGES = [
  { id: 'OPENING', goal: "Confirm you're speaking with the right person, introduce yourself and who you represent in one short sentence, and ask if it's a good time to talk.", turns: '2' },
  { id: 'PITCH', goal: "State plainly why you're calling, then make the case using the strongest specific talking points — check they're following before adding more.", turns: '4' },
  { id: 'HANDLE_QUESTIONS', goal: 'Answer questions and push-back directly with concrete facts; acknowledge each concern first, stay warm and never defensive.', turns: '3' },
  { id: 'ASK', goal: 'Make the specific ask this call exists for, clearly and directly, and give them the chance to agree.', turns: '4' },
  { id: 'CLOSING', goal: 'Thank them by name, restate any agreed next step in one sentence, and close warmly.', turns: '99' },
]

const initial: WizardData = {
  name: '',
  agent_name: '',
  company_name: '',
  company_tagline: '',
  role_description: '', // blank → buildAgentPayload picks a direction-aware default
  contact: '',
  call_direction: 'inbound',
  model: 'gpt-4o-mini',
  supported: ['en'],
  default_lang: 'en',
  tts_model: 'bulbul:v3',
  tts_speaker: 'shubh',
  pronouns: 'she',
  personality_energy: 'medium',
  personality_tone: 'conversational',
  personality_style: 'natural',
  personality_lingo: '',
  personality_custom: '',
  locations: [],
  products: [{ name: '', price: '', description: '', features: '' }],
  portfolio_label: 'KNOWLEDGE BASE',
  objection_patterns: ['too expensive', 'not interested', 'call me later', 'thinking about it'],
  stages: DEFAULT_STAGES,
  opening_greeting: '',
  objection_response_en: '',
  objection_response_hi: '',
  always_rules: [],
  never_rules: [],
  // Calendar is optional but recommended — default ON with two sample slots
  // so every new agent can offer and book visit times unless deliberately
  // switched off.
  calendar_enabled: true,
  calendar_slots: [
    { day: 'Tomorrow', time: '10:00 AM' },
    { day: 'This Saturday', time: '11:00 AM' },
  ],
}

// Which wizard step collects each extraction field the document may lack —
// shown next to missing items so the user knows where they'll be asked.
const MISSING_STEP: Record<string, string> = {
  agent_name: 'Basics',
  company_name: 'Basics',
  company_tagline: 'Basics',
  role_description: 'Basics',
  contact: 'Basics',
  call_direction: 'Basics',
  portfolio_prompt_text: 'Knowledge',
  locations: 'Knowledge',
  appointment_slots: 'Calendar',
  opening_greeting: 'Call Script',
}

export default function CreateAgent() {
  const nav = useNavigate()
  const toast = useToast()
  const [step, setStep] = useState(0)
  const [d, setD] = useState<WizardData>(initial)
  const [r, setR] = useState<Routing>({ did: '', caller_id_prefixes: [], fallback_agent: '', time_windows: [] })
  const [win, setWin] = useState<TimeWindowDraft>({ start: '', end: '' })
  const [activateNow, setActivateNow] = useState(false)
  const [saving, setSaving] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [missing, setMissing] = useState<MissingField[] | null>(null)
  const [extractedFrom, setExtractedFrom] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const set = (patch: Partial<WizardData>) => setD((p) => {
    const next = { ...p, ...patch }
    // Direction toggled while the stages are still an untouched default
    // skeleton → swap to the matching skeleton so outbound agents don't
    // ship an inbound "ask how you can help" flow (and vice versa).
    if (patch.call_direction && patch.call_direction !== p.call_direction) {
      const s = JSON.stringify(p.stages)
      if (s === JSON.stringify(DEFAULT_STAGES) || s === JSON.stringify(DEFAULT_OUTBOUND_STAGES)) {
        next.stages = patch.call_direction === 'outbound' ? DEFAULT_OUTBOUND_STAGES : DEFAULT_STAGES
      }
    }
    return next
  })

  const onDocument = async (file: File) => {
    setExtracting(true)
    try {
      const res = await extractFromDocument(file)
      const patch = extractedFieldsToWizardPatch(res.fields || {})
      set(patch)
      setMissing(res.missing || [])
      setExtractedFrom(res.filename || file.name)
      const got = Object.keys(patch).length
      toast(
        got
          ? `Prefilled ${got} field${got === 1 ? '' : 's'} from ${file.name}`
          : `No agent fields could be extracted from ${file.name}`,
        got ? 'success' : 'warn',
      )
    } catch (e: any) {
      toast(`Document extraction failed: ${e.message || e}`, 'error')
    } finally {
      setExtracting(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const slugOk = SLUG_RE.test(d.name)
  const canCreate =
    slugOk && d.name && d.agent_name && d.company_name && d.opening_greeting &&
    d.supported.length > 0 && d.stages.length > 0

  const calendarOk =
    !d.calendar_enabled || d.calendar_slots.some((s) => s.day.trim() && s.time.trim())

  const stepValid = (s: number): boolean => {
    if (s === 0) return !!(d.name && slugOk && d.agent_name && d.company_name)
    if (s === 1) return d.supported.length > 0
    if (s === 4) return !!d.opening_greeting && d.stages.length > 0 // Call Script: opening line + at least one stage
    if (s === 5) return calendarOk
    return true
  }

  const submit = async () => {
    setSaving(true)
    try {
      const payload = buildAgentPayload({
        ...d,
        products: d.products.filter((p) => p.name.trim()),
      })
      await createAgent(payload)

      // Routing — applied after creation; skipped silently when all fields empty.
      const time_windows = win.start !== '' && win.end !== ''
        ? [{ start: Number(win.start), end: Number(win.end) }]
        : []
      const hasRouting = !!(r.did || (r.caller_id_prefixes || []).length || r.fallback_agent || time_windows.length)
      if (hasRouting) {
        try {
          await putRouting(d.name, {
            did: r.did || '',
            caller_id_prefixes: r.caller_id_prefixes || [],
            fallback_agent: r.fallback_agent || '',
            time_windows,
          })
        } catch (e: any) {
          toast(`Agent created, but routing save failed: ${e.message || e}`, 'error')
        }
      }

      if (activateNow) {
        try {
          await activateAgent(d.name)
          toast(`Agent "${d.name}" created and activated`, 'success')
        } catch (e: any) {
          toast(`Agent created, but activation failed: ${e.message || e}`, 'error')
        }
      } else {
        toast(`Agent "${d.name}" created`, 'success')
      }
      nav(`/build/agents/${d.name}`)
    } catch (e: any) {
      toast(`Create failed: ${e.message || e}`, 'error')
      setStep(STEPS.length - 1)
    } finally {
      setSaving(false)
    }
  }

  // Missing fields the user has since filled in drop off the list live.
  const stillMissing = (missing ?? []).filter((m) => {
    switch (m.key) {
      case 'agent_name': return !d.agent_name
      case 'company_name': return !d.company_name
      case 'company_tagline': return !d.company_tagline
      case 'role_description': return !d.role_description
      case 'contact': return !d.contact
      case 'call_direction': return false // always has a value; selector is on Basics
      case 'opening_greeting': return !d.opening_greeting
      case 'portfolio_prompt_text': return !d.products.some((p) => p.name.trim())
      case 'locations': return d.locations.length === 0
      case 'appointment_slots': return d.calendar_enabled && !d.calendar_slots.some((s) => s.day.trim() && s.time.trim())
      default: return true
    }
  })

  return (
    <div className="space-y-6">
      <PageHeader title="Create Agent" sub="Define a new voice agent step by step" />

      {/* Start from a document */}
      <Card className="flex flex-wrap items-center gap-3 p-4">
        <div className="flex items-center gap-2 text-[13px] font-semibold">
          <FileUp size={16} className="text-mint-500" /> Start from a document
        </div>
        <div className="text-[12px] text-dim">
          Upload a company profile / brochure (PDF, DOCX or TXT) — every field it contains is extracted
          and prefilled; anything it lacks, you'll be asked for below.
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && onDocument(e.target.files[0])}
        />
        <Button variant="soft" size="sm" onClick={() => fileRef.current?.click()} disabled={extracting}>
          {extracting ? <><Spinner /> Extracting…</> : 'Upload document'}
        </Button>
      </Card>

      {/* Missing-info panel — the "ask the user for the rest" half of extraction */}
      {missing !== null && stillMissing.length > 0 && (
        <div className="rounded-xl bg-amber-500/10 p-4 ring-1 ring-amber-500/20">
          <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-amber-700 dark:text-amber-300">
            <AlertTriangle size={15} /> {extractedFrom} didn't contain everything — please provide:
          </div>
          <ul className="space-y-1">
            {stillMissing.map((m) => (
              <li key={m.key} className="text-[12px] text-amber-700/90 dark:text-amber-300/90">
                <span className="font-semibold">{m.label}</span>
                {MISSING_STEP[m.key] ? <span className="opacity-70"> · {MISSING_STEP[m.key]} step</span> : null}
                <span className="opacity-70"> — {m.hint}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {missing !== null && stillMissing.length === 0 && (
        <div className="rounded-xl bg-mint-400/10 px-4 py-3 text-[12px] font-medium text-mint-700 ring-1 ring-mint-400/20 dark:text-mint-300">
          All essential fields are covered — review each step and create the agent.
        </div>
      )}

      {/* Step rail */}
      <div className="flex flex-wrap items-center gap-2">
        {STEPS.map((s, i) => (
          <button
            key={s}
            onClick={() => i < step && setStep(i)}
            className={cx(
              'flex items-center gap-1.5 rounded-full px-3 py-1 text-[12px] font-semibold ring-1 transition',
              i === step
                ? 'bg-mint-400/15 text-mint-700 ring-mint-400/30 dark:text-mint-300'
                : i < step
                ? 'bg-[var(--surface-2)] text-soft ring-[var(--border)] hover:text-[var(--text)]'
                : 'bg-[var(--surface-2)] text-dim ring-[var(--border)] cursor-default',
            )}
          >
            <span className={cx('grid h-4 w-4 place-items-center rounded-full text-[10px]', i < step ? 'bg-mint-500 text-white' : 'bg-[var(--surface-solid)] ring-1 ring-[var(--border)]')}>
              {i < step ? <Check size={10} /> : i + 1}
            </span>
            {s}
          </button>
        ))}
      </div>

      <Card className="p-6">
        {step === 0 && (
          <div className="space-y-6">
            <BasicsSection d={d} set={set} />
            <div>
              <div className="eyebrow mb-2">Call type *</div>
              <CallDirectionSection d={d} set={set} />
            </div>
          </div>
        )}
        {step === 1 && <LanguagesSection d={d} set={set} />}
        {step === 2 && <PersonalitySection d={d} set={set} />}
        {step === 3 && <KnowledgeSection d={d} set={set} />}
        {step === 4 && <CallFlowSection d={d} set={set} />}
        {step === 5 && <CalendarSection d={d} set={set} />}
        {step === 6 && <ResponsesSection d={d} set={set} />}

        {step === 7 && (
          <div className="space-y-4">
            <p className="text-[12px] text-dim">
              Optional — decide which inbound number this agent answers. Leave everything blank to skip;
              you can configure routing later from the agent's Routing tab.
            </p>
            <RoutingFields r={r} setR={setR} win={win} setWin={setWin} />
          </div>
        )}

        {step === 8 && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="indigo" soft>{d.name}</Badge>
              <Badge tone="mint" soft>{d.agent_name}</Badge>
              <Badge tone={d.call_direction === 'outbound' ? 'amber' : 'mint'} soft>{d.call_direction}</Badge>
              <span className="text-[13px] text-soft">{d.company_name}</span>
              <span className="ml-auto text-[12px] text-dim">
                {d.supported.join(' · ')} · {d.stages.length} stages · {d.products.filter((p) => p.name).length} products
                {d.calendar_enabled ? ` · ${d.calendar_slots.filter((s) => s.day && s.time).length} slots` : ' · no calendar'}
              </span>
            </div>
            {!canCreate && <div className="rounded-lg bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 ring-1 ring-amber-500/20 dark:text-amber-300">Fill in slug, agent name, company, opening line and at least one call stage before creating.</div>}
            {!d.calendar_enabled && (
              <div className="rounded-lg bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 ring-1 ring-amber-500/20 dark:text-amber-300">
                No calendar configured — the agent won't be able to offer or book visit slots. Recommended:
                go back to the Calendar step and add availability.
              </div>
            )}
            <label className="flex items-center gap-2 rounded-lg bg-[var(--surface-2)] px-3 py-2.5 text-[13px] font-medium text-soft ring-1 ring-[var(--border)]">
              <input
                type="checkbox"
                checked={activateNow}
                onChange={(e) => setActivateNow(e.target.checked)}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              Activate immediately — this agent starts answering inbound calls right after creation
            </label>
            <details className="rounded-xl bg-[var(--surface-2)] p-4 ring-1 ring-[var(--border)]">
              <summary className="cursor-pointer text-[12px] font-semibold text-soft">Preview generated definition (JSON)</summary>
              <pre className="mt-3 max-h-80 overflow-auto text-[11px] leading-relaxed text-dim">{JSON.stringify(buildAgentPayload({ ...d, products: d.products.filter((p) => p.name.trim()) }), null, 2)}</pre>
            </details>
          </div>
        )}

        {/* Footer nav */}
        <div className="mt-6 flex items-center justify-between border-t border-[var(--border)] pt-4">
          <Button variant="ghost" onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0}>
            <ChevronLeft size={15} /> Back
          </Button>
          {step < STEPS.length - 1 ? (
            <Button variant="primary" onClick={() => setStep((s) => s + 1)} disabled={!stepValid(step)}>
              Next <ChevronRight size={15} />
            </Button>
          ) : (
            <Button variant="primary" onClick={submit} disabled={!canCreate || saving}>
              <Wand2 size={15} /> {saving ? 'Creating…' : 'Create Agent'}
            </Button>
          )}
        </div>
      </Card>
    </div>
  )
}
