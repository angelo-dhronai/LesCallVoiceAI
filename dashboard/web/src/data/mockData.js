// ─────────────────────────────────────────────────────────────────────────
// All mock data for the Lescall dashboard lives here, consistently shaped.
// (The live API wiring is layered on top of these shapes in a later pass;
//  every view reads from here so the UI is fully functional standalone.)
// ─────────────────────────────────────────────────────────────────────────

export const workspaces = [
  { id: 'ws_dhron', name: 'Dhron AI', plan: 'Scale' },
  { id: 'ws_personal', name: 'Personal', plan: 'Free' },
]

export const currentUser = {
  email: 'tech@dhronai.com',
  name: 'Dhron AI',
  initials: 'DA',
}

export const wallet = { balance: 5.0, currency: 'USD' }

export const notifications = [
  { id: 1, title: 'Campaign "Pramukh Q3" finished', body: '142 calls completed · 31 booked visits', time: '4m ago', unread: true },
  { id: 2, title: 'Low wallet balance', body: 'Balance under $10 — add funds to keep agents live', time: '1h ago', unread: true },
  { id: 3, title: 'Knowledge base indexed', body: 'pramukh-brochure.pdf finished embedding', time: '3h ago', unread: true },
  { id: 4, title: 'New inbound number verified', body: '+91 80 3133 9140 is now routing to Vishal', time: 'Yesterday', unread: false },
]

// ── Agents ────────────────────────────────────────────────────────────────
export const agents = [
  {
    id: 'agt_vishal',
    name: 'Vishal Patel — Inbound',
    status: 'Active',
    language: 'Hindi (Primary)',
    languages: ['Hindi (Primary)', 'English', 'Gujarati'],
    welcomeMessage: 'Namaste! Pramukh Group mein aapka swagat hai. Main Vishal bol raha hoon — bataaiye main aapki kaise madad kar sakta hoon?',
    welcomeDelay: 0.8,
    prompt: `# Role
You are **Vishal Patel**, a warm, professional inbound sales associate for **Pramukh Group** (premium real estate, Surat & Ahmedabad).

# Objective
Greet bilingually, detect the caller's language, qualify interest, match a project, and book a site visit.

# Style
- Keep replies to **1–2 spoken sentences**.
- Mirror the caller's language (Hindi / English / Gujarati).
- Never read phone numbers back aloud.

# Flow
GREETING → DISCOVERY → PROJECT_MATCH → INVITE_VISIT → CONFIRMATION → CLOSING`,
    llm: { provider: 'OpenAI', model: 'gpt-4o-mini', temperature: 0.6, maxTokens: 220 },
    audio: { transcriber: 'Sarvam saaras:v3', voice: 'Bulbul · shubh', speed: 1.0 },
    engine: { vad: 'Energy (self-calibrating)', interruptions: true, endpointing: 700 },
    call: { maxDuration: 600, recording: true, voicemailDetection: true },
    inbound: { number: '+91 80 3133 9140', greetingOnFirstFrame: true },
    cost: { transcriber: 0.012, llm: 0.028, voice: 0.041, telephony: 0.012, platform: 0.005 },
  },
  {
    id: 'agt_priya',
    name: 'Priya — Outbound Dialer',
    status: 'Active',
    language: 'English (Primary)',
    languages: ['English', 'Hindi'],
    welcomeMessage: 'Hi, this is Priya calling from Pramukh Group about your enquiry on our new launch — is now a good time?',
    welcomeDelay: 0.5,
    prompt: `# Role
You are **Priya**, an outbound appointment-setter for **Pramukh Group**.

# Objective
Confirm the lead's interest from their web enquiry and book a site visit slot.

# Style
- Friendly, brisk, respectful of the prospect's time.
- 1–2 sentences per turn.`,
    llm: { provider: 'OpenAI', model: 'gpt-4o-mini', temperature: 0.5, maxTokens: 200 },
    audio: { transcriber: 'Sarvam saaras:v3', voice: 'Bulbul · maya', speed: 1.05 },
    engine: { vad: 'Energy (self-calibrating)', interruptions: true, endpointing: 650 },
    call: { maxDuration: 480, recording: true, voicemailDetection: true },
    inbound: { number: '—', greetingOnFirstFrame: false },
    cost: { transcriber: 0.012, llm: 0.024, voice: 0.038, telephony: 0.012, platform: 0.005 },
  },
  {
    id: 'agt_support',
    name: 'Support Triage',
    status: 'Draft',
    language: 'English (Primary)',
    languages: ['English'],
    welcomeMessage: 'Thanks for calling support. To get you to the right place — can you tell me what this is about?',
    welcomeDelay: 1.0,
    prompt: `# Role
You are a calm support triage agent. Identify the issue category and either resolve or create a ticket.`,
    llm: { provider: 'OpenAI', model: 'gpt-4o-mini', temperature: 0.4, maxTokens: 180 },
    audio: { transcriber: 'Sarvam saaras:v3', voice: 'Bulbul · arjun', speed: 1.0 },
    engine: { vad: 'Energy (self-calibrating)', interruptions: false, endpointing: 800 },
    call: { maxDuration: 600, recording: true, voicemailDetection: false },
    inbound: { number: '—', greetingOnFirstFrame: true },
    cost: { transcriber: 0.012, llm: 0.020, voice: 0.035, telephony: 0.012, platform: 0.005 },
  },
]

// ── Call history (25+ records for working pagination) ───────────────────────
const SOURCES = ['Inbound', 'Outbound', 'API', 'Batch']
const STATUSES = ['Success', 'Success', 'Success', 'Failed', 'Live']
const AGENT_NAMES = ['Vishal Patel — Inbound', 'Priya — Outbound Dialer', 'Support Triage']

export const callHistory = Array.from({ length: 27 }, (_, i) => {
  const status = i === 0 ? 'Live' : STATUSES[(i * 3) % STATUSES.length]
  const dur = status === 'Live' ? 47 + i : 40 + ((i * 37) % 520)
  return {
    id: `exec_${(9043 - i).toString(16)}`,
    agent: AGENT_NAMES[i % AGENT_NAMES.length],
    source: i === 0 ? 'Inbound' : SOURCES[(i * 2) % SOURCES.length],
    duration: status === 'Failed' ? 0 : dur,
    status,
    date: `2026-06-${String(19 - (i % 18)).padStart(2, '0')}`,
    sentiment: ['positive', 'neutral', 'negative'][(i * 5) % 3],
    cost: +(0.05 + ((i * 13) % 90) / 1000).toFixed(3),
  }
})

// Per-call analytics + transcript shown in the Call Detail view.
const spark = (base) => Array.from({ length: 12 }, (_, i) => ({
  i,
  v: +(base + Math.sin(i / 1.7) * base * 0.18 + (i % 3) * base * 0.04).toFixed(2),
}))

export const analyticsForCall = {
  kpis: [
    { key: 'duration', label: 'Avg Call Duration', value: '3:42', delta: 6.2, dir: 'up', spark: spark(220) },
    { key: 'sentiment', label: 'Sentiment Score', value: '0.78', delta: 3.1, dir: 'up', spark: spark(0.7) },
    { key: 'resolution', label: 'Intent Resolution', value: '91%', delta: 1.4, dir: 'up', spark: spark(0.9) },
    { key: 'cost', label: 'Cost / Call', value: '$0.34', delta: 2.0, dir: 'down', spark: spark(0.34) },
  ],
  // Fake amplitude envelope for the waveform (0..1).
  waveform: Array.from({ length: 96 }, (_, i) =>
    0.18 + Math.abs(Math.sin(i / 3.1) * 0.55) + (i % 5 === 0 ? 0.22 : 0) + (i % 11 === 0 ? 0.18 : 0)
  ).map((v) => Math.min(1, v)),
  durationSeconds: 222,
  summary:
    'Caller enquired about 3BHK availability in Pramukh Aurum, Vesu. Vishal confirmed pricing band and offered a Saturday site visit, which the caller accepted. A follow-up reminder and brochure share were promised.',
  transcript: [
    { id: 1, role: 'agent', t: '0:02', sentiment: 'neutral', text: 'Namaste! Pramukh Group mein aapka swagat hai — main Vishal. Bataaiye, main kaise madad karoon?' },
    { id: 2, role: 'customer', t: '0:08', sentiment: 'neutral', text: 'Hi, I saw your ad for the new Vesu project. Do you have 3BHK available?' },
    { id: 3, role: 'agent', t: '0:14', sentiment: 'positive', text: 'Absolutely! Pramukh Aurum in Vesu has 3BHK units ready. May I ask what budget range works for you?' },
    { id: 4, role: 'customer', t: '0:22', sentiment: 'neutral', text: 'Around 1.4 to 1.6 crore. I want to upgrade from my current flat.' },
    { id: 5, role: 'agent', t: '0:30', sentiment: 'positive', text: 'That fits perfectly. The 3BHK there starts at 1.45 crore. Would you like to see it this weekend?' },
    { id: 6, role: 'customer', t: '0:38', sentiment: 'positive', text: 'Yes, Saturday works. But I do not want any cancel or refund hassle on the booking.' },
    { id: 7, role: 'agent', t: '0:46', sentiment: 'positive', text: 'Understood — the booking is fully refundable before agreement. I have you down for Saturday 11 AM.' },
    { id: 8, role: 'customer', t: '0:53', sentiment: 'positive', text: 'Great, thank you. Please send the brochure too.' },
    { id: 9, role: 'agent', t: '0:58', sentiment: 'positive', text: 'Done — brochure on its way, and you will get a reminder before the visit. Have a great day!' },
  ],
}

export const TRANSCRIPT_KEYWORDS = ['refund', 'cancel', 'upgrade', 'complaint', 'budget', 'booking']

// ── Flow graph ─────────────────────────────────────────────────────────────
export const flowNodes = [
  { id: 'start', type: 'START', label: 'Start', x: 60, y: 200, prompt: '', fn: 'none',
    examples: [], transitions: [{ to: 'greeting', label: 'on call connect' }] },
  { id: 'greeting', type: 'LLM', label: 'Greeting', x: 300, y: 80,
    prompt: 'Greet the caller bilingually as Vishal from Pramukh Group and ask how you can help.',
    fn: 'none', examples: ['Namaste! Main Vishal bol raha hoon.', 'Welcome to Pramukh Group!'],
    transitions: [{ to: 'intent_check', label: 'always' }] },
  { id: 'intent_check', type: 'LLM', label: 'Intent Check', x: 300, y: 320,
    prompt: 'Determine whether the caller wants project info, has a complaint, or wants to book a visit.',
    fn: 'lookup_order', examples: ['Are you looking to buy or to enquire?', 'Is this about an existing booking?'],
    transitions: [{ to: 'create_ticket', label: 'if complaint' }, { to: 'closing', label: 'if resolved' }] },
  { id: 'create_ticket', type: 'LLM', label: 'Create Ticket', x: 560, y: 200,
    prompt: 'Collect the issue details and create a support ticket, then confirm the ticket number.',
    fn: 'create_ticket', examples: ['I have logged ticket #4821 for you.'],
    transitions: [{ to: 'closing', label: 'always' }] },
  { id: 'closing', type: 'CLOSING', label: 'Closing', x: 820, y: 200, prompt: '',
    fn: 'none', examples: ['Thank you for calling — have a great day!'], transitions: [] },
]

export const FUNCTION_OPTIONS = ['none', 'create_ticket', 'lookup_order']

// ── Knowledge base ──────────────────────────────────────────────────────────
export const knowledgeBaseDocs = [
  { id: 'doc1', name: 'pramukh-aurum-brochure.pdf', type: 'pdf', ragId: 'rag_a1f9c2', progress: 100, uploaded: '2026-06-12' },
  { id: 'doc2', name: 'price-list-q3.csv', type: 'csv', ragId: 'rag_77be01', progress: 100, uploaded: '2026-06-14' },
  { id: 'doc3', name: 'objection-handling.docx', type: 'docx', ragId: 'rag_3c0d4e', progress: 64, uploaded: '2026-06-18' },
  { id: 'doc4', name: 'pramukhgroup.com/projects', type: 'url', ragId: 'rag_9981aa', progress: 100, uploaded: '2026-06-10' },
  { id: 'doc5', name: 'site-visit-faq.pdf', type: 'pdf', ragId: 'rag_b2c5f7', progress: 38, uploaded: '2026-06-19' },
  { id: 'doc6', name: 'amenities-vesu.docx', type: 'docx', ragId: 'rag_44a0d1', progress: 100, uploaded: '2026-06-09' },
  { id: 'doc7', name: 'rera-registrations.csv', type: 'csv', ragId: 'rag_5e7720', progress: 100, uploaded: '2026-06-05' },
]
