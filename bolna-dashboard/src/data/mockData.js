// ─────────────────────────────────────────────────────────────────────────
// Bolna.ai dashboard — single source of truth for ALL mock data.
// Every component imports from here; nothing is hardcoded inline elsewhere.
// ─────────────────────────────────────────────────────────────────────────

// Keywords highlighted inside transcript message text (View 2).
export const highlightKeywords = ['refund', 'cancel', 'upgrade', 'complaint', 'escalate', 'order']

export const walletBalance = 5.0

export const workspaces = [
  { id: 'dhron', name: 'Dhron AI', plan: 'Team' },
  { id: 'personal', name: 'Personal', plan: 'Free' },
]

export const currentUser = {
  email: 'tech@dhronai.com',
  name: 'Dhron AI',
  initials: 'DA',
}

export const notifications = [
  { id: 'n1', title: 'Batch campaign finished', body: '“June Reactivation” completed — 142/150 connected.', time: '4m ago', unread: true },
  { id: 'n2', title: 'Low wallet balance', body: 'Balance is below $10. Add funds to avoid interruptions.', time: '1h ago', unread: true },
  { id: 'n3', title: 'Knowledge base indexed', body: 'pricing_2024.pdf finished embedding (100%).', time: '3h ago', unread: false },
  { id: 'n4', title: 'New inbound number live', body: '+1 415 555 0142 is now routed to Inbound Support Agent.', time: 'Yesterday', unread: false },
]

// ── Agents ────────────────────────────────────────────────────────────────
export const agents = [
  {
    id: 'agt_inbound_support',
    name: 'Inbound Support Agent',
    status: 'active',
    language: 'English (US)',
    costPerMinBreakdown: { transcriber: 0.011, llm: 0.038, voice: 0.029, telephony: 0.014, platform: 0.006 },
    welcomeMessage: 'Hi! Thanks for calling Dhron support — how can I help you today?',
    welcomeDelaySeconds: 1,
    prompt:
      '# Role\nYou are **Aria**, a friendly support agent for Dhron AI.\n\n## Goals\n- Greet the caller warmly and identify their intent.\n- Resolve common issues (billing, refunds, account access).\n- Escalate to a human when sentiment turns negative or the request is out of scope.\n\n## Tone\nWarm, concise, never robotic. Keep replies to 1–2 sentences.\n\n## Guardrails\n- Never invent order numbers or refund amounts.\n- Confirm identity before sharing account details.',
  },
  {
    id: 'agt_outbound_sales',
    name: 'Outbound Sales SDR',
    status: 'active',
    language: 'English (UK)',
    costPerMinBreakdown: { transcriber: 0.010, llm: 0.045, voice: 0.031, telephony: 0.013, platform: 0.006 },
    welcomeMessage: 'Hello, this is Max from Dhron — do you have a quick minute?',
    welcomeDelaySeconds: 0,
    prompt:
      '# Role\nYou are **Max**, an outbound SDR booking demos for Dhron AI.\n\n## Flow\n1. Confirm you reached the right person.\n2. One-line value pitch.\n3. Qualify (team size, current tooling).\n4. Book a demo slot.\n\n## Tone\nConfident, respectful of time, no hard selling.',
  },
  {
    id: 'agt_hindi_collections',
    name: 'Reminder & Collections (HI)',
    status: 'active',
    language: 'Hindi',
    costPerMinBreakdown: { transcriber: 0.012, llm: 0.034, voice: 0.027, telephony: 0.015, platform: 0.006 },
    welcomeMessage: 'नमस्ते! मैं Dhron से बात कर रहा हूँ, क्या आपके पास एक मिनट है?',
    welcomeDelaySeconds: 1,
    prompt:
      '# भूमिका\nआप **रिया** हैं, Dhron की पेमेंट रिमाइंडर एजेंट।\n\n## लक्ष्य\n- विनम्रता से बकाया राशि की याद दिलाएँ।\n- भुगतान विकल्प साझा करें।\n- कभी दबाव न डालें।',
  },
  {
    id: 'agt_appointment',
    name: 'Appointment Scheduler',
    status: 'draft',
    language: 'English (US)',
    costPerMinBreakdown: { transcriber: 0.011, llm: 0.030, voice: 0.026, telephony: 0.014, platform: 0.006 },
    welcomeMessage: 'Hi! I can help you book or reschedule your appointment.',
    welcomeDelaySeconds: 2,
    prompt:
      '# Role\nYou are a scheduling assistant.\n\n## Goals\n- Find an open slot that suits the caller.\n- Confirm date, time, and contact number.\n- Send a confirmation summary.',
  },
  {
    id: 'agt_survey',
    name: 'Post-Call CSAT Survey',
    status: 'draft',
    language: 'English (US)',
    costPerMinBreakdown: { transcriber: 0.010, llm: 0.022, voice: 0.024, telephony: 0.013, platform: 0.005 },
    welcomeMessage: 'Thanks for your time — I have just two quick questions about your experience.',
    welcomeDelaySeconds: 0,
    prompt:
      '# Role\nYou run a 2-question CSAT survey.\n\n## Rules\n- Keep it under 45 seconds.\n- Capture a 1–5 score and one free-text reason.',
  },
  {
    id: 'agt_lead_qualify',
    name: 'Lead Qualifier (ES)',
    status: 'active',
    language: 'Spanish',
    costPerMinBreakdown: { transcriber: 0.012, llm: 0.040, voice: 0.030, telephony: 0.014, platform: 0.006 },
    welcomeMessage: '¡Hola! Soy Lucía de Dhron, ¿tienes un momento?',
    welcomeDelaySeconds: 1,
    prompt:
      '# Rol\nEres **Lucía**, calificas leads entrantes.\n\n## Objetivos\n- Confirmar interés y presupuesto.\n- Agendar seguimiento con un humano si califica.',
  },
]

// ── Transcripts (attached to the calls opened in Call Detail View) ─────────
const T = {
  refund: [
    { speaker: 'agent', text: 'Hi! Thanks for calling Dhron support — how can I help you today?', timestampSeconds: 2, sentiment: 'neutral' },
    { speaker: 'customer', text: 'I want a refund for my last order, it never arrived.', timestampSeconds: 8, sentiment: 'negative' },
    { speaker: 'agent', text: 'I’m sorry to hear that. Can you share the order number so I can look it up?', timestampSeconds: 14, sentiment: 'positive' },
    { speaker: 'customer', text: 'Yes, it’s order 88241.', timestampSeconds: 21, sentiment: 'neutral' },
    { speaker: 'agent', text: 'Thank you. I can see it was delayed in transit. I’ll process a full refund right now.', timestampSeconds: 29, sentiment: 'positive' },
    { speaker: 'customer', text: 'Oh great, thank you so much!', timestampSeconds: 36, sentiment: 'positive' },
    { speaker: 'agent', text: 'You’re welcome! The refund will reflect in 3–5 business days. Anything else?', timestampSeconds: 43, sentiment: 'positive' },
    { speaker: 'customer', text: 'No that’s all, thanks.', timestampSeconds: 49, sentiment: 'positive' },
  ],
  cancel: [
    { speaker: 'agent', text: 'Hi! Thanks for calling Dhron support — how can I help you today?', timestampSeconds: 2, sentiment: 'neutral' },
    { speaker: 'customer', text: 'I’d like to cancel my subscription, it’s too expensive.', timestampSeconds: 9, sentiment: 'negative' },
    { speaker: 'agent', text: 'Understood. Before I cancel, may I offer a 20% loyalty discount?', timestampSeconds: 16, sentiment: 'neutral' },
    { speaker: 'customer', text: 'Hmm, that could work actually.', timestampSeconds: 23, sentiment: 'neutral' },
    { speaker: 'agent', text: 'Great — I’ve applied the discount to your plan. You’ll save $6/month.', timestampSeconds: 31, sentiment: 'positive' },
    { speaker: 'customer', text: 'Okay, let’s keep it then. Thanks for the help.', timestampSeconds: 39, sentiment: 'positive' },
  ],
  upgrade: [
    { speaker: 'agent', text: 'Hello, this is Max from Dhron — do you have a quick minute?', timestampSeconds: 1, sentiment: 'neutral' },
    { speaker: 'customer', text: 'Sure, what’s this about?', timestampSeconds: 6, sentiment: 'neutral' },
    { speaker: 'agent', text: 'You’re on the Starter plan — many teams your size upgrade to Pro for live analytics.', timestampSeconds: 13, sentiment: 'positive' },
    { speaker: 'customer', text: 'We have been thinking about an upgrade actually.', timestampSeconds: 21, sentiment: 'positive' },
    { speaker: 'agent', text: 'Perfect. I can book a 20-minute demo this week — does Thursday work?', timestampSeconds: 29, sentiment: 'positive' },
    { speaker: 'customer', text: 'Thursday afternoon is good.', timestampSeconds: 35, sentiment: 'positive' },
    { speaker: 'agent', text: 'Booked for Thursday 3 PM. You’ll get a calendar invite shortly.', timestampSeconds: 42, sentiment: 'positive' },
  ],
  complaint: [
    { speaker: 'agent', text: 'Hi! Thanks for calling Dhron support — how can I help you today?', timestampSeconds: 2, sentiment: 'neutral' },
    { speaker: 'customer', text: 'This is my third call. I have a serious complaint about your billing.', timestampSeconds: 10, sentiment: 'negative' },
    { speaker: 'agent', text: 'I’m truly sorry. Let me make this right — can you describe what happened?', timestampSeconds: 17, sentiment: 'positive' },
    { speaker: 'customer', text: 'I was charged twice for the same month.', timestampSeconds: 24, sentiment: 'negative' },
    { speaker: 'agent', text: 'I see the duplicate charge. I’ll reverse it and escalate to billing for review.', timestampSeconds: 32, sentiment: 'neutral' },
    { speaker: 'customer', text: 'Okay. I appreciate you actually listening.', timestampSeconds: 40, sentiment: 'positive' },
    { speaker: 'agent', text: 'Of course. You’ll get a confirmation email within the hour.', timestampSeconds: 47, sentiment: 'positive' },
  ],
  order: [
    { speaker: 'agent', text: 'Hi! Thanks for calling Dhron support — how can I help you today?', timestampSeconds: 2, sentiment: 'neutral' },
    { speaker: 'customer', text: 'I want to check the status of my order.', timestampSeconds: 7, sentiment: 'neutral' },
    { speaker: 'agent', text: 'Sure! What’s the order number?', timestampSeconds: 12, sentiment: 'neutral' },
    { speaker: 'customer', text: 'It’s 90233.', timestampSeconds: 17, sentiment: 'neutral' },
    { speaker: 'agent', text: 'It shipped yesterday and arrives Friday. Want tracking by SMS?', timestampSeconds: 25, sentiment: 'positive' },
    { speaker: 'customer', text: 'Yes please.', timestampSeconds: 30, sentiment: 'positive' },
    { speaker: 'agent', text: 'Done — tracking sent. Anything else I can help with?', timestampSeconds: 36, sentiment: 'positive' },
    { speaker: 'customer', text: 'Nope, thank you!', timestampSeconds: 41, sentiment: 'positive' },
  ],
  esc: [
    { speaker: 'agent', text: '¡Hola! Soy Lucía de Dhron, ¿tienes un momento?', timestampSeconds: 1, sentiment: 'neutral' },
    { speaker: 'customer', text: 'Sí, pero tengo una queja sobre el servicio.', timestampSeconds: 8, sentiment: 'negative' },
    { speaker: 'agent', text: 'Lamento escuchar eso. Voy a escalar tu caso a un especialista.', timestampSeconds: 15, sentiment: 'neutral' },
    { speaker: 'customer', text: 'Gracias, eso ayudaría.', timestampSeconds: 21, sentiment: 'positive' },
  ],
}

// ── Call history (28 records; 6 carry full transcripts) ────────────────────
function ts(daysAgo, h, m) {
  // Build a deterministic ISO timestamp (no Date.now/random — keeps it stable).
  const base = new Date('2026-06-17T00:00:00Z').getTime()
  const d = new Date(base - daysAgo * 86400000)
  d.setUTCHours(h, m, 0, 0)
  return d.toISOString()
}

export const callHistory = [
  { executionId: 'exec_7f3a91c2', agentName: 'Inbound Support Agent', triggerSource: 'inbound', durationSeconds: 49, status: 'success', timestamp: ts(0, 9, 12), transcript: T.refund },
  { executionId: 'exec_b22d4e10', agentName: 'Inbound Support Agent', triggerSource: 'inbound', durationSeconds: 39, status: 'success', timestamp: ts(0, 10, 3), transcript: T.cancel },
  { executionId: 'exec_91ac77de', agentName: 'Outbound Sales SDR', triggerSource: 'outbound', durationSeconds: 42, status: 'success', timestamp: ts(0, 11, 41), transcript: T.upgrade },
  { executionId: 'exec_3dd0fa55', agentName: 'Inbound Support Agent', triggerSource: 'inbound', durationSeconds: 47, status: 'success', timestamp: ts(0, 12, 22), transcript: T.complaint },
  { executionId: 'exec_5c8e2b04', agentName: 'Inbound Support Agent', triggerSource: 'inbound', durationSeconds: 41, status: 'success', timestamp: ts(1, 14, 8), transcript: T.order },
  { executionId: 'exec_aa19bb73', agentName: 'Lead Qualifier (ES)', triggerSource: 'outbound', durationSeconds: 21, status: 'success', timestamp: ts(1, 15, 33), transcript: T.esc },
  { executionId: 'exec_0e44c1a9', agentName: 'Outbound Sales SDR', triggerSource: 'batch', durationSeconds: 0, status: 'live', timestamp: ts(0, 13, 2) },
  { executionId: 'exec_d7720f3b', agentName: 'Reminder & Collections (HI)', triggerSource: 'outbound', durationSeconds: 64, status: 'success', timestamp: ts(1, 9, 50) },
  { executionId: 'exec_1f9e6c80', agentName: 'Inbound Support Agent', triggerSource: 'inbound', durationSeconds: 12, status: 'failed', timestamp: ts(1, 16, 19) },
  { executionId: 'exec_c3b85a47', agentName: 'Outbound Sales SDR', triggerSource: 'batch', durationSeconds: 58, status: 'success', timestamp: ts(2, 10, 5) },
  { executionId: 'exec_2a6df902', agentName: 'Lead Qualifier (ES)', triggerSource: 'outbound', durationSeconds: 33, status: 'success', timestamp: ts(2, 11, 27) },
  { executionId: 'exec_88c4e1d6', agentName: 'Reminder & Collections (HI)', triggerSource: 'batch', durationSeconds: 71, status: 'success', timestamp: ts(2, 12, 44) },
  { executionId: 'exec_4be0a3f1', agentName: 'Inbound Support Agent', triggerSource: 'inbound', durationSeconds: 5, status: 'failed', timestamp: ts(2, 14, 1) },
  { executionId: 'exec_6610bd5c', agentName: 'Appointment Scheduler', triggerSource: 'inbound', durationSeconds: 53, status: 'success', timestamp: ts(2, 15, 38) },
  { executionId: 'exec_f0a2c7e9', agentName: 'Outbound Sales SDR', triggerSource: 'outbound', durationSeconds: 46, status: 'success', timestamp: ts(3, 9, 9) },
  { executionId: 'exec_9d3e54ba', agentName: 'Inbound Support Agent', triggerSource: 'inbound', durationSeconds: 60, status: 'success', timestamp: ts(3, 10, 52) },
  { executionId: 'exec_7c1188af', agentName: 'Reminder & Collections (HI)', triggerSource: 'batch', durationSeconds: 38, status: 'success', timestamp: ts(3, 11, 15) },
  { executionId: 'exec_e5520c63', agentName: 'Lead Qualifier (ES)', triggerSource: 'outbound', durationSeconds: 27, status: 'failed', timestamp: ts(3, 13, 47) },
  { executionId: 'exec_b9a07f24', agentName: 'Outbound Sales SDR', triggerSource: 'batch', durationSeconds: 51, status: 'success', timestamp: ts(3, 14, 30) },
  { executionId: 'exec_31f6ad8e', agentName: 'Inbound Support Agent', triggerSource: 'inbound', durationSeconds: 44, status: 'success', timestamp: ts(4, 9, 25) },
  { executionId: 'exec_0c7e9b15', agentName: 'Appointment Scheduler', triggerSource: 'inbound', durationSeconds: 36, status: 'success', timestamp: ts(4, 10, 41) },
  { executionId: 'exec_a48d2f70', agentName: 'Outbound Sales SDR', triggerSource: 'outbound', durationSeconds: 19, status: 'failed', timestamp: ts(4, 12, 12) },
  { executionId: 'exec_7720e3cc', agentName: 'Reminder & Collections (HI)', triggerSource: 'batch', durationSeconds: 67, status: 'success', timestamp: ts(4, 13, 58) },
  { executionId: 'exec_5e1b9a02', agentName: 'Lead Qualifier (ES)', triggerSource: 'outbound', durationSeconds: 31, status: 'success', timestamp: ts(5, 9, 33) },
  { executionId: 'exec_c2440de8', agentName: 'Inbound Support Agent', triggerSource: 'inbound', durationSeconds: 55, status: 'success', timestamp: ts(5, 11, 4) },
  { executionId: 'exec_9af1376b', agentName: 'Outbound Sales SDR', triggerSource: 'batch', durationSeconds: 48, status: 'success', timestamp: ts(5, 12, 49) },
  { executionId: 'exec_1d8c60e4', agentName: 'Appointment Scheduler', triggerSource: 'inbound', durationSeconds: 22, status: 'failed', timestamp: ts(5, 14, 17) },
  { executionId: 'exec_e803b5f9', agentName: 'Reminder & Collections (HI)', triggerSource: 'outbound', durationSeconds: 59, status: 'success', timestamp: ts(6, 10, 26) },
]

// ── Per-call analytics (View 2). Keyed by executionId, with a default. ─────
function mkAnalytics({ avg, pos, neu, neg, intent, cost, dur, sent, intentT, costT }) {
  return {
    avgCallDuration: avg, // seconds
    sentimentBreakdown: { positive: pos, neutral: neu, negative: neg },
    intentResolutionRate: intent, // %
    costPerCall: cost, // $
    // mini time-series for KPI sparklines (one array reused per KPI with offsets)
    series: dur.map((v, i) => ({ t: i, value: v })),
    kpis: [
      { key: 'duration', label: 'Avg Call Duration', value: avg, unit: 's', delta: -4.2, direction: 'down', spark: dur },
      { key: 'sentiment', label: 'Sentiment Score', value: pos, unit: '%', delta: 6.1, direction: 'up', spark: sent },
      { key: 'intent', label: 'Intent Resolution', value: intent, unit: '%', delta: 2.4, direction: 'up', spark: intentT },
      { key: 'cost', label: 'Cost / Call', value: cost, unit: '$', delta: -1.8, direction: 'down', spark: costT },
    ],
  }
}

const defaultAnalytics = mkAnalytics({
  avg: 46, pos: 72, neu: 19, neg: 9, intent: 88, cost: 0.41,
  dur: [52, 48, 51, 45, 47, 44, 46], sent: [60, 64, 62, 68, 70, 71, 72],
  intentT: [80, 82, 81, 85, 86, 87, 88], costT: [0.49, 0.47, 0.46, 0.44, 0.43, 0.42, 0.41],
})

export const analyticsForCall = {
  default: defaultAnalytics,
  exec_7f3a91c2: mkAnalytics({ avg: 49, pos: 78, neu: 14, neg: 8, intent: 94, cost: 0.38,
    dur: [44, 47, 49, 46, 50, 48, 49], sent: [62, 66, 70, 72, 75, 77, 78],
    intentT: [85, 88, 90, 91, 92, 93, 94], costT: [0.45, 0.43, 0.42, 0.40, 0.39, 0.38, 0.38] }),
  exec_3dd0fa55: mkAnalytics({ avg: 47, pos: 41, neu: 22, neg: 37, intent: 71, cost: 0.52,
    dur: [40, 44, 47, 49, 46, 48, 47], sent: [55, 50, 47, 45, 43, 42, 41],
    intentT: [78, 76, 74, 73, 72, 71, 71], costT: [0.44, 0.46, 0.48, 0.50, 0.51, 0.52, 0.52] }),
}

// ── Knowledge base documents ───────────────────────────────────────────────
export const knowledgeBaseDocs = [
  { name: 'pricing_2024.pdf', ragId: 'rag_9f12c7', type: 'pdf', embeddingStatus: 100, uploadedAt: ts(2, 8, 0) },
  { name: 'refund_policy.docx', ragId: 'rag_3a88e1', type: 'docx', embeddingStatus: 100, uploadedAt: ts(3, 8, 0) },
  { name: 'product_catalog.csv', ragId: 'rag_77be20', type: 'csv', embeddingStatus: 64, uploadedAt: ts(0, 8, 0) },
  { name: 'https://docs.dhron.ai/faq', ragId: 'rag_c41d09', type: 'url', embeddingStatus: 100, uploadedAt: ts(5, 8, 0) },
  { name: 'onboarding_guide.pdf', ragId: 'rag_5e2a44', type: 'pdf', embeddingStatus: 38, uploadedAt: ts(0, 9, 30) },
  { name: 'sla_terms.docx', ragId: 'rag_b09f73', type: 'docx', embeddingStatus: 100, uploadedAt: ts(6, 8, 0) },
  { name: 'integrations_list.csv', ragId: 'rag_22ce81', type: 'csv', embeddingStatus: 100, uploadedAt: ts(4, 8, 0) },
  { name: 'https://status.dhron.ai', ragId: 'rag_d7710a', type: 'url', embeddingStatus: 82, uploadedAt: ts(1, 8, 0) },
  { name: 'security_whitepaper.pdf', ragId: 'rag_0a93fe', type: 'pdf', embeddingStatus: 100, uploadedAt: ts(7, 8, 0) },
]

// ── LLM flow node graph (View 3) ───────────────────────────────────────────
export const flowNodes = [
  {
    id: 'node_start', type: 'START', label: 'Call Start', position: { x: 60, y: 80 },
    prompt: 'Entry point. Triggered when the call connects.',
    exampleResponses: [], transitions: ['node_greeting'], connectedFunctionCall: null,
  },
  {
    id: 'node_greeting', type: 'LLM', label: 'Greeting', position: { x: 320, y: 80 },
    prompt: 'Greet the caller warmly and ask how you can help. Keep it to one sentence.',
    exampleResponses: ['Hi! Thanks for calling Dhron — how can I help?', 'Hello! What can I do for you today?'],
    transitions: ['node_intent'], connectedFunctionCall: null,
  },
  {
    id: 'node_intent', type: 'LLM', label: 'Intent Check', position: { x: 580, y: 80 },
    prompt: 'Classify the caller’s intent: billing, refund, technical, or other. Ask a clarifying question if unclear.',
    exampleResponses: ['Are you calling about billing or a technical issue?', 'Got it — is this about an existing order?'],
    transitions: ['node_ticket', 'node_closing'], connectedFunctionCall: 'lookup_order',
  },
  {
    id: 'node_ticket', type: 'LLM', label: 'Create Ticket', position: { x: 580, y: 300 },
    prompt: 'Collect the details needed to open a support ticket, then confirm the ticket number.',
    exampleResponses: ['I’ve opened ticket #4821 for you.', 'Your ticket is logged — you’ll get an email shortly.'],
    transitions: ['node_closing'], connectedFunctionCall: 'create_ticket',
  },
  {
    id: 'node_closing', type: 'CLOSING', label: 'Wrap Up', position: { x: 860, y: 190 },
    prompt: 'Summarize what was done, thank the caller, and end the call politely.',
    exampleResponses: ['Thanks for calling — have a great day!'],
    transitions: [], connectedFunctionCall: null,
  },
]

export const functionCallOptions = ['none', 'create_ticket', 'lookup_order']

// ── Tiny shared formatter (kept here so every panel formats durations alike) ─
export function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}
