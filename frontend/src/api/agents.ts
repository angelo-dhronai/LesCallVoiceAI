import { api } from './client'
import { post, del, postForm } from './mutate'

/* ───────────────────────── types ───────────────────────── */
export type AgentSummary = {
  name: string
  agent_id?: string
  version?: string
  agent_name?: string
  company?: string
  languages?: string[]
  locations?: string[]
  stages?: string[]
  active?: boolean
  error?: string
  call_direction?: 'inbound' | 'outbound'
  /** Twin grouping key — `acme-inbound` and `acme-outbound` share family `acme`. */
  family?: string
  calendar_slots?: number
}
export type AgentsList = { agents: AgentSummary[]; active: string | null }

export type Routing = {
  exists?: boolean
  did?: string
  caller_id_prefixes?: string[]
  time_windows?: { start: number; end: number }[]
  fallback_agent?: string
}

export type AgentDoc = {
  doc_id: string
  filename?: string
  org_name?: string
  doc_type?: string
  description?: string
  chunks?: number
  uploaded_at?: string
}

export type MissingField = { key: string; label: string; hint: string }
export type ExtractResult = {
  status: string
  fields: Record<string, any>
  missing: MissingField[]
  filename?: string
  error?: string
}

export type Booking = {
  slot_day: string
  slot_time: string
  appointment_time: string
  caller_name?: string
  caller_phone?: string
  conn_id?: string
  booked_at?: string
}

export type CalendarSlot = { day: string; time: string }

export type IntentResult = {
  text: string
  intent: string
  matches: { intent: string; pattern: string }[]
  qa_signals: { field: string; signal: string }[]
  agent_id: string
  patterns_checked: Record<string, number>
}

/* ───────────────────────── reads ───────────────────────── */
export const listAgents = () => api<AgentsList>('/agents')
export const getAgent = (name: string) => api<any>(`/agents/${name}`)
export const getRawYaml = (name: string) =>
  api<string>(`/agents/${name}/raw`, { headers: { Accept: 'text/plain' } })
export const getRouting = (name: string) => api<Routing>(`/agents/${name}/routing`)
export const listDocs = (name: string) =>
  api<{ documents: AgentDoc[] }>(`/agents/${name}/documents`)

/* ───────────────────────── writes ───────────────────────── */
/** Creating an agent creates a PAIR — `<base>-inbound` and `<base>-outbound`.
 *  `name` is the primary slug to open in the editor; it is NOT the base name
 *  the user typed, so always navigate/route with the returned value. */
export type CreateAgentResult = {
  status: string
  name: string
  agent_id?: string
  family?: string
  twins?: { inbound: string; outbound: string }
}
export const createAgent = (payload: any) => post<CreateAgentResult>('/agents', payload)

/** rev = the `_rev` from getAgent(). When sent, a concurrent edit (e.g. by
 *  the Copilot) 409s instead of being silently clobbered. */
export const updateAgent = (name: string, payload: any, rev?: string) =>
  api<{ status: string; revision?: number; rev?: string }>(`/agents/${name}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
    headers: rev ? { 'If-Match': rev } : undefined,
  })
export const archiveAgent = (name: string) => del(`/agents/${name}`)

export const putRawYaml = (name: string, yamlText: string, rev?: string) =>
  api<{ status: string; revision?: number; rev?: string }>(`/agents/${name}/raw`, {
    method: 'PUT',
    body: yamlText,
    headers: { 'Content-Type': 'text/plain', ...(rev ? { 'If-Match': rev } : {}) },
  })

/* ─────────────────── revisions / surgical ops (v2) ─────────────────── */
export type AgentRevision = {
  rev_id: number
  actor: string | null
  reason: string | null
  sha256?: string
  created_at: string
}

export const listRevisions = (name: string) =>
  api<{ revisions: AgentRevision[] }>(`/agents/${name}/revisions`)

export const rollbackAgent = (name: string, revId: number) =>
  post<{ status: string; revision: number }>(`/agents/${name}/rollback/${revId}`)

export type AgentOp = {
  op: 'set' | 'append' | 'remove_at' | 'move'
  path: string
  value?: any
  to?: number
}

/** Surgical path ops — the same server machinery the Copilot uses. */
export const patchAgentOps = (name: string, ops: AgentOp[], baseRev?: string) =>
  api<{ status: string; revision: number; rev: string; diff: string }>(
    `/agents/${name}/ops`,
    {
      method: 'PATCH',
      body: JSON.stringify(baseRev ? { ops, base_rev: baseRev } : { ops }),
    },
  )

export const isConflictError = (e: unknown) =>
  String((e as any)?.message || e).startsWith('409')

// validate against an existing agent endpoint slug — the server only schema-
// checks the posted YAML body and ignores {name}, so any existing agent works
// as the validation target.
export const validateRaw = (name: string, yamlText: string) =>
  api<{ valid: boolean; error?: string; agent_id?: string; stages?: string[]; languages?: string[] }>(
    `/agents/${name}/validate`,
    { method: 'POST', body: yamlText, headers: { 'Content-Type': 'text/plain' } },
  )

export const putRouting = (name: string, body: Routing) =>
  api(`/agents/${name}/routing`, { method: 'PUT', body: JSON.stringify(body) })

export const uploadDoc = (name: string, form: FormData) =>
  postForm(`/agents/${name}/documents`, form)
export const deleteDoc = (name: string, docId: string) =>
  del(`/agents/${name}/documents/${docId}`)
export const extractYaml = (name: string, docId: string) =>
  post<ExtractResult>(`/agents/${name}/documents/${docId}/extract-yaml`)
export const applyYaml = (name: string, docId: string, fields: any) =>
  post(`/agents/${name}/documents/${docId}/apply-yaml`, { fields })

// Stateless extraction for the Create wizard — no agent needed yet.
export const extractFromDocument = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return postForm(`/agent-tools/extract-document`, fd) as Promise<ExtractResult>
}

export const listBookings = (name: string) =>
  api<{ bookings: Booking[] }>(`/agents/${name}/bookings`)
export const deleteBooking = (name: string, index: number) =>
  del(`/agents/${name}/bookings/${index}`)

export const previewIntent = (text: string, agent?: string, language = 'auto') =>
  post<IntentResult>('/preview/intent', { text, agent, language })

export const ttsPreviewUrl = (speaker: string, model: string, language: string) =>
  `/api/tts/preview?speaker=${encodeURIComponent(speaker)}&model=${encodeURIComponent(
    model,
  )}&language=${encodeURIComponent(language)}`

/* ─────────────────────── wizard payload ───────────────────────
 * Ports buildAgentFromWizard() from the standalone builder so a from-scratch
 * agent created in the React UI is byte-identical (and schema-valid) to one
 * created by the original Agent Builder. */
export type WizardData = {
  name: string
  agent_name: string
  company_name: string
  company_tagline?: string
  role_description?: string
  contact?: string
  call_direction: 'inbound' | 'outbound'
  model: string
  supported: string[]
  default_lang: string
  tts_model?: string
  tts_speaker?: string
  pronouns?: string
  personality_energy?: string
  personality_tone?: string
  personality_style?: string
  personality_lingo?: string
  personality_custom?: string
  locations: string[]
  products: { name: string; price?: string; description?: string; features?: string }[]
  portfolio_label?: string
  objection_patterns: string[]
  stages: { id: string; goal: string; turns: string }[]
  opening_greeting: string
  objection_response_en?: string
  objection_response_hi?: string
  always_rules: string[]
  never_rules: string[]
  calendar_enabled: boolean
  calendar_slots: CalendarSlot[]
}

const REG: Record<string, string> = {
  en: 'Speak clearly and professionally in English.',
  hi: 'Speak in clear, polite Hindi. Keep technical terms in English.',
  gu: 'Speak in clear Gujarati. Keep technical terms in English.',
  mr: 'Speak in clear Marathi. Keep technical terms in English.',
}

/** Compile the wizard's product rows into the two knowledge_base fields
 * derived from them (portfolio prompt text + known_projects index). */
function compileProducts(products: WizardData['products']) {
  const portfolioText =
    products.length > 0
      ? products
          .map((p, i) => {
            let e = `${i + 1}. ${p.name}${p.price ? ' — ' + p.price : ''}\n`
            if (p.description) e += `   ${p.description}\n`
            if (p.features) e += `   Key features: ${p.features}\n`
            return e
          })
          .join('\n')
      : 'No specific products configured. Use general knowledge to assist the caller.'

  const knownProjects: Record<string, [string, string]> = {}
  products.forEach((p) => {
    const key = (p.name || '').toLowerCase().trim()
    if (key) knownProjects[key] = [p.name, p.price || 'Standard']
  })
  return { portfolioText, knownProjects }
}

/** Compile wizard stage rows into call_flow.stages entries. */
const compileStages = (stages: WizardData['stages']) =>
  stages.map((s, i) => ({
    id: s.id.toUpperCase().replace(/[^A-Z0-9_]/g, '_'),
    order: i,
    goal: s.goal,
    turns_until_advance: parseInt(s.turns) || 8,
  }))

/** Compile the calendar slot rows into the definition's calendar block. */
function compileCalendar(d: WizardData): any {
  const slots = d.calendar_enabled
    ? d.calendar_slots.filter((s) => s.day.trim() && s.time.trim())
    : []
  const slotStr = slots.map((s) => `${s.day} at ${s.time}`).join(', ')
  const slotsText: Record<string, string> = {}
  d.supported.forEach((l) => {
    slotsText[l] = slotStr
  })
  return { type: 'mock', mock_slots: slots.map((s) => ({ day: s.day.trim(), time: s.time.trim() })), slots_text: slotsText }
}

export function buildAgentPayload(d: WizardData): any {
  const ttsVoices: Record<string, any> = {}
  const registerNotes: Record<string, string> = {}
  d.supported.forEach((l) => {
    ttsVoices[l] = { model: d.tts_model || 'bulbul:v3', speaker: d.tts_speaker || 'shubh' }
    registerNotes[l] = REG[l] || 'Speak clearly.'
  })

  const { portfolioText, knownProjects } = compileProducts(d.products)
  const stages = compileStages(d.stages)

  const fillerResponses: Record<string, string[]> = {}
  if (d.supported.includes('hi')) fillerResponses.hi = ['Ek second...']
  if (d.supported.includes('en')) fillerResponses.en = ['One moment...']

  const objectionResponses: Record<string, string[]> = {}
  if (d.objection_response_en) objectionResponses.en = [d.objection_response_en]
  if (d.objection_response_hi) objectionResponses.hi = [d.objection_response_hi]

  return {
    name: d.name,
    agent_id: `${d.name}-v1`,
    version: '1.0.0',
    call_direction: d.call_direction || 'inbound',
    identity: {
      agent_name: d.agent_name,
      company_name: d.company_name,
      company_tagline: d.company_tagline || d.company_name,
      role_description:
        d.role_description ||
        (d.call_direction === 'outbound' ? 'outbound sales consultant' : 'inbound sales consultant'),
      contact: d.contact || '',
    },
    languages: {
      supported: d.supported,
      default: d.default_lang,
      tts_voices: ttsVoices,
      register_notes: registerNotes,
    },
    calendar: compileCalendar(d),
    call_flow: { decline_threshold: 2, stages },
    knowledge_base: {
      known_projects: knownProjects,
      locations: d.locations,
      portfolio_prompt_text: portfolioText,
      portfolio_section_label: d.portfolio_label || 'KNOWLEDGE BASE',
      location_aliases: d.locations,
      objection_patterns: d.objection_patterns,
      time_patterns: ['tomorrow', 'next week', 'kal', 'baad mein', 'weekend', 'Saturday', 'Sunday'],
      qa_field_signals: { name: ['my name is', 'mera naam', 'I am', 'main hoon', 'myself'] },
      stt_corrections: {
        city_corrections: {},
        phonetic_corrections: {},
        real_estate_corrections: {},
        project_name_corrections: {},
      },
    },
    response_bank: {
      opening_greeting: d.opening_greeting,
      response_fallback: 'One moment please.',
      filler_responses: fillerResponses,
      objection_responses: objectionResponses,
    },
    personality: {
      pronouns: d.pronouns || 'she',
      energy: d.personality_energy || 'medium',
      tone: d.personality_tone || 'conversational',
      style: d.personality_style || 'natural',
      lingo: (d.personality_lingo || '').replace(/"/g, "'"),
      custom_instructions: (d.personality_custom || '').replace(/"/g, "'"),
    },
    llm_config: {
      model: d.model,
      max_tokens: 130,
      timeout_s: 8.0,
      stream_timeout_s: 10.0,
      filler_timeout_s: 1.5,
      intent_classify_timeout_s: 1.0,
      max_response_length: 160,
      extra_always_rules: d.always_rules,
      extra_never_rules: d.never_rules,
    },
  }
}

/* ─────────────── extracted document fields → wizard patch ───────────────
 * Maps the fields returned by /agent-tools/extract-document (or a document
 * extract-yaml run) onto the Create wizard's form model. Only fields the
 * document actually produced are included, so user-entered values are never
 * clobbered by an empty extraction. */
export function extractedFieldsToWizardPatch(fields: Record<string, any>): Partial<WizardData> {
  const patch: Partial<WizardData> = {}
  if (fields.agent_name) patch.agent_name = String(fields.agent_name)
  if (fields.company_name) patch.company_name = String(fields.company_name)
  if (fields.company_tagline) patch.company_tagline = String(fields.company_tagline)
  if (fields.role_description) patch.role_description = String(fields.role_description)
  if (fields.contact) patch.contact = String(fields.contact)
  if (fields.call_direction === 'inbound' || fields.call_direction === 'outbound')
    patch.call_direction = fields.call_direction
  if (fields.opening_greeting) patch.opening_greeting = String(fields.opening_greeting)
  if (Array.isArray(fields.locations) && fields.locations.length)
    patch.locations = fields.locations.map(String)
  if (Array.isArray(fields.objection_patterns) && fields.objection_patterns.length)
    patch.objection_patterns = fields.objection_patterns.map(String)
  if (Array.isArray(fields.extra_always_rules) && fields.extra_always_rules.length)
    patch.always_rules = fields.extra_always_rules.map(String)
  if (Array.isArray(fields.extra_never_rules) && fields.extra_never_rules.length)
    patch.never_rules = fields.extra_never_rules.map(String)
  if (fields.known_projects && typeof fields.known_projects === 'object') {
    const products = Object.values(fields.known_projects as Record<string, any>)
      .map((v: any) => ({
        name: String(v?.[0] ?? ''),
        price: String(v?.[1] ?? '') === 'Standard' ? '' : String(v?.[1] ?? ''),
        description: '',
        features: '',
      }))
      .filter((p) => p.name)
    if (products.length) patch.products = products
  }
  if (Array.isArray(fields.appointment_slots)) {
    const slots = fields.appointment_slots
      .map((s: any) => ({ day: String(s?.day ?? ''), time: String(s?.time ?? '') }))
      .filter((s: CalendarSlot) => s.day && s.time)
    if (slots.length) {
      patch.calendar_enabled = true
      patch.calendar_slots = slots
    }
  }
  return patch
}

/* ─────────────────── definition → wizard (inverse) ───────────────────
 * agentToWizardData() maps a full agent-definition JSON (as returned by
 * GET /api/agents/{name}) back into the WizardData form shape. Best-effort:
 * fields the form does not model (calendar, qa_field_signals, slots, …)
 * are NOT represented here — mergeWizardIntoDefinition() keeps them intact
 * on save. */

/** Try to recover the wizard's product rows from a portfolio_prompt_text
 * that was generated by compileProducts(). Returns null when the text does
 * not follow the generated "1. Name — price / desc / Key features:" shape
 * (i.e. it was hand-written in YAML). */
function parsePortfolioProducts(text: string): WizardData['products'] | null {
  if (!text || !/^\s*1\.\s/.test(text)) return null
  const blocks = text.split(/\n(?=\d+\.\s)/)
  const out: WizardData['products'] = []
  for (const block of blocks) {
    const lines = block.split('\n').filter((l) => l.trim())
    const head = lines[0]?.match(/^\d+\.\s+(.*)$/)
    if (!head) return null
    const [namePart, ...priceParts] = head[1].split(' — ')
    const p = { name: namePart.trim(), price: priceParts.join(' — ').trim(), description: '', features: '' }
    for (const line of lines.slice(1)) {
      const t = line.trim()
      if (t.startsWith('Key features: ')) p.features = t.slice('Key features: '.length)
      else p.description = p.description ? `${p.description} ${t}` : t
    }
    if (p.name) out.push(p)
  }
  return out.length ? out : null
}

export function agentToWizardData(def: any, name = ''): WizardData {
  const identity = def?.identity ?? {}
  const languages = def?.languages ?? {}
  const personality = def?.personality ?? {}
  const kb = def?.knowledge_base ?? {}
  const cf = def?.call_flow ?? {}
  const rb = def?.response_bank ?? {}
  const llm = def?.llm_config ?? {}

  const supported: string[] = languages.supported ?? []
  const default_lang: string = languages.default ?? supported[0] ?? 'en'
  const voices: Record<string, any> = languages.tts_voices ?? {}
  const defVoice = voices[default_lang] ?? voices[supported[0]] ?? {}

  // Products: prefer parsing a wizard-generated portfolio text (recovers
  // description/features); otherwise reconstruct rows from known_projects
  // ([canonical_name, price-or-location] tuples — lossy but editable).
  const fromKnown = Object.values(kb.known_projects ?? {})
    .map((v: any) => ({
      name: String(v?.[0] ?? ''),
      price: String(v?.[1] ?? ''),
      description: '',
      features: '',
    }))
    .filter((p) => p.name)
  const products = parsePortfolioProducts(kb.portfolio_prompt_text ?? '') ?? fromKnown

  const stages = (cf.stages ?? []).map((s: any) => ({
    id: String(s?.id ?? ''),
    goal: String(s?.goal ?? ''),
    turns: String(s?.turns_until_advance ?? 8),
  }))

  const objections: Record<string, string[]> = rb.objection_responses ?? {}

  return {
    name,
    agent_name: identity.agent_name ?? '',
    company_name: identity.company_name ?? '',
    company_tagline: identity.company_tagline ?? '',
    role_description: identity.role_description ?? '',
    contact: identity.contact ?? '',
    call_direction: def?.call_direction === 'outbound' ? 'outbound' : 'inbound',
    model: llm.model ?? 'gpt-4o-mini',
    supported: [...supported],
    default_lang,
    tts_model: defVoice.model ?? 'bulbul:v3',
    tts_speaker: defVoice.speaker ?? 'shubh',
    pronouns: personality.pronouns ?? identity.pronouns ?? 'she',
    personality_energy: personality.energy ?? 'medium',
    personality_tone: personality.tone ?? 'conversational',
    personality_style: personality.style ?? 'natural',
    personality_lingo: personality.lingo ?? '',
    personality_custom: personality.custom_instructions ?? '',
    locations: [...(kb.locations ?? [])],
    products: products.length ? products : [{ name: '', price: '', description: '', features: '' }],
    portfolio_label: kb.portfolio_section_label ?? 'KNOWLEDGE BASE',
    objection_patterns: [...(kb.objection_patterns ?? [])],
    stages,
    opening_greeting: rb.opening_greeting ?? '',
    objection_response_en: objections.en?.[0] ?? '',
    objection_response_hi: objections.hi?.[0] ?? '',
    always_rules: [...(llm.extra_always_rules ?? [])],
    never_rules: [...(llm.extra_never_rules ?? [])],
    calendar_enabled: (def?.calendar?.mock_slots ?? []).length > 0,
    calendar_slots: (def?.calendar?.mock_slots ?? []).map((s: any) => ({
      day: String(s?.day ?? ''),
      time: String(s?.time ?? ''),
    })),
  }
}

/* ─────────────────── merge-on-save (edit safety) ───────────────────
 * mergeWizardIntoDefinition() deep-merges ONLY the sections the structured
 * form models onto a freshly-fetched full definition, so every field the
 * form does NOT model (calendar, qa_field_signals, stt_corrections,
 * register_notes wording, slots, stt_forced_language, allow_code_mixing,
 * per-voice pace, filler/identity/decline responses, …) survives an edit
 * round-trip untouched. NEVER send buildAgentPayload() output on edit.
 *
 * `initial` is the WizardData snapshot taken when the form loaded — used
 * to detect whether lossy sections (products, TTS voice, objection
 * responses) were actually edited before overwriting their YAML source. */
export function mergeWizardIntoDefinition(current: any, d: WizardData, initial: WizardData): any {
  const out = JSON.parse(JSON.stringify(current ?? {}))

  // identity — overwrite the five modeled fields, keep the rest (pronouns…)
  out.identity = {
    ...(out.identity ?? {}),
    agent_name: d.agent_name,
    company_name: d.company_name,
    company_tagline: d.company_tagline || d.company_name,
    role_description: d.role_description || 'inbound sales consultant',
    contact: d.contact || '',
  }

  // languages — supported/default always; tts_voices only for new languages
  // or when the form's model/speaker was actually changed (preserves pace
  // and per-language voice overrides otherwise).
  const langs = { ...(out.languages ?? {}) }
  const voices: Record<string, any> = { ...(langs.tts_voices ?? {}) }
  const notes: Record<string, string> = { ...(langs.register_notes ?? {}) }
  const ttsEdited =
    (d.tts_model ?? '') !== (initial.tts_model ?? '') ||
    (d.tts_speaker ?? '') !== (initial.tts_speaker ?? '')
  d.supported.forEach((l) => {
    const existing = voices[l]
    if (!existing) {
      voices[l] = { model: d.tts_model || 'bulbul:v3', speaker: d.tts_speaker || 'shubh' }
    } else if (ttsEdited) {
      voices[l] = { ...existing, model: d.tts_model || existing.model, speaker: d.tts_speaker || existing.speaker }
    }
    if (!notes[l]) notes[l] = REG[l] || 'Speak clearly.'
  })
  langs.supported = [...d.supported]
  langs.default = d.default_lang
  langs.tts_voices = voices
  langs.register_notes = notes
  out.languages = langs

  // calendar — recompiled ONLY when the slot rows (or the enable toggle)
  // were actually edited; otherwise the existing block (incl. hand-written
  // slots_text translations) passes through, only backfilling slots_text
  // keys for newly added languages.
  const calendarEdited =
    d.calendar_enabled !== initial.calendar_enabled ||
    JSON.stringify(d.calendar_slots) !== JSON.stringify(initial.calendar_slots)
  if (calendarEdited) {
    out.calendar = compileCalendar(d)
  } else if (out.calendar && out.calendar.slots_text) {
    const st: Record<string, string> = { ...out.calendar.slots_text }
    d.supported.forEach((l) => {
      if (!st[l]) st[l] = st[d.default_lang] ?? st.en ?? Object.values(st)[0] ?? 'Tomorrow 10 AM or this Saturday'
    })
    out.calendar = { ...out.calendar, slots_text: st }
  }

  // call direction — single scalar, always taken from the form
  out.call_direction = d.call_direction || 'inbound'

  // personality — fully modeled
  out.personality = {
    ...(out.personality ?? {}),
    pronouns: d.pronouns || 'she',
    energy: d.personality_energy || 'medium',
    tone: d.personality_tone || 'conversational',
    style: d.personality_style || 'natural',
    lingo: (d.personality_lingo || '').replace(/"/g, "'"),
    custom_instructions: (d.personality_custom || '').replace(/"/g, "'"),
  }

  // knowledge_base — locations/label/objection_patterns are directly modeled;
  // known_projects + portfolio_prompt_text are recompiled ONLY when the
  // product rows were edited (a hand-written portfolio text is otherwise
  // reconstructed lossily, so an untouched form must not clobber it).
  // location_aliases, time_patterns, qa_field_signals, stt_corrections pass
  // through untouched.
  const kb = { ...(out.knowledge_base ?? {}) }
  kb.locations = [...d.locations]
  kb.portfolio_section_label = d.portfolio_label || 'KNOWLEDGE BASE'
  kb.objection_patterns = [...d.objection_patterns]
  const productRows = d.products.filter((p) => p.name.trim())
  const initialRows = initial.products.filter((p) => p.name.trim())
  if (JSON.stringify(productRows) !== JSON.stringify(initialRows)) {
    const { portfolioText, knownProjects } = compileProducts(productRows)
    kb.known_projects = knownProjects
    kb.portfolio_prompt_text = portfolioText
  }
  out.knowledge_base = kb

  // call_flow — stages fully modeled; decline_threshold preserved
  out.call_flow = { ...(out.call_flow ?? {}), stages: compileStages(d.stages) }

  // response_bank — opening_greeting always; en/hi objection responses only
  // when changed (the form shows just the FIRST response per language, so
  // any additional rotation entries are preserved).
  const rb = { ...(out.response_bank ?? {}) }
  rb.opening_greeting = d.opening_greeting
  const objections: Record<string, string[]> = { ...(rb.objection_responses ?? {}) }
  const applyObjection = (lang: string, next: string, prev: string) => {
    if (next === prev) return
    const rest = (objections[lang] ?? []).slice(1)
    if (next.trim()) objections[lang] = [next.trim(), ...rest]
    else if (rest.length) objections[lang] = rest
    else delete objections[lang]
  }
  applyObjection('en', d.objection_response_en ?? '', initial.objection_response_en ?? '')
  applyObjection('hi', d.objection_response_hi ?? '', initial.objection_response_hi ?? '')
  rb.objection_responses = objections
  out.response_bank = rb

  // llm_config — model + extra rules modeled; timeouts/token caps preserved
  out.llm_config = {
    ...(out.llm_config ?? {}),
    model: d.model,
    extra_always_rules: [...d.always_rules],
    extra_never_rules: [...d.never_rules],
  }

  return out
}
