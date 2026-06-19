// Map backend (per-tenant) API shapes <-> the rich UI shapes the views render.
// Only a subset of UI fields are persisted by the backend `agents` table; the
// rest (llm/engine/cost telemetry) are presentational defaults shown per agent.

export const DEFAULT_COST = { transcriber: 0.012, llm: 0.026, voice: 0.039, telephony: 0.012, platform: 0.005 }

const LANG_LABEL = { hi: 'Hindi', en: 'English', gu: 'Gujarati', auto: 'Auto-detect' }
const langLabel = (code) => LANG_LABEL[(code || 'auto').split('-')[0]] || code

// ---- Agents ---------------------------------------------------------------
export function agentFromApi(row) {
  const primary = langLabel(row.language)
  return {
    id: row.id,
    name: row.name || 'Untitled agent',
    status: row.is_active ? 'Active' : 'Draft',
    language: `${primary} (Primary)`,
    languages: Array.from(new Set([`${primary} (Primary)`, 'English', 'Gujarati'])),
    welcomeMessage: row.greeting || '',
    welcomeDelay: 0.8,
    prompt: row.system_prompt || '',
    company: row.company || '',
    pitch: row.pitch || '',
    rawLanguage: row.language || 'auto',
    llm: { provider: 'OpenAI', model: 'gpt-4o-mini', temperature: 0.6, maxTokens: 220 },
    audio: { transcriber: 'Sarvam saaras:v3', voice: `Bulbul · ${row.tts_speaker || 'shubh'}`, speed: 1.0, speaker: row.tts_speaker || 'shubh' },
    engine: { vad: 'Energy (self-calibrating)', interruptions: true, endpointing: 700 },
    call: { maxDuration: 600, recording: true, voicemailDetection: true },
    inbound: { number: '—', greetingOnFirstFrame: true },
    cost: DEFAULT_COST,
  }
}

// Build the AgentIn payload the backend expects from the editable UI draft.
export function agentToApi(d) {
  return {
    name: d.name,
    company: d.company || null,
    system_prompt: d.prompt || '',
    pitch: d.pitch || null,
    greeting: d.welcomeMessage || null,
    language: d.rawLanguage || 'auto',
    tts_speaker: d.audio?.speaker || 'shubh',
    is_active: d.status === 'Active' ? 1 : 0,
  }
}

// ---- Calls ----------------------------------------------------------------
export function callFromApi(c) {
  const meta = c.metadata || {}
  const start = meta.start_time ? new Date(meta.start_time) : null
  return {
    id: c.conn_id,
    agent: meta.owner ? meta.owner.split('@')[0] : (meta.language_detected ? `Agent · ${langLabel(meta.language_detected)}` : 'Agent'),
    source: (meta.direction || 'inbound') === 'outbound' ? 'Outbound' : 'Inbound',
    duration: Math.round(meta.duration_seconds || 0),
    status: c.has_transcript ? 'Success' : (c.has_audio ? 'Success' : 'Failed'),
    date: start ? start.toISOString().slice(0, 10) : '—',
    sentiment: c.sentiment || 'neutral',
    cost: null,
    summary: c.summary || null,
    language: meta.language_detected || null,
  }
}

// Backend transcript turns ({ts, role, text}) -> chat bubbles for the UI.
export function turnsToBubbles(turns) {
  return (turns || []).map((t, i) => ({
    id: i,
    role: t.role === 'agent' ? 'agent' : 'customer',
    t: t.ts != null ? `${Math.floor(t.ts / 60)}:${String(Math.floor(t.ts % 60)).padStart(2, '0')}` : '',
    sentiment: 'neutral',
    text: t.text || '',
  }))
}

// Fabricate a stable waveform envelope from a duration (no real audio decode).
export function fakeWaveform(seconds = 60) {
  const n = 96
  return Array.from({ length: n }, (_, i) =>
    Math.min(1, 0.18 + Math.abs(Math.sin((i + (seconds % 7)) / 3.1) * 0.55) + (i % 5 === 0 ? 0.2 : 0))
  )
}
