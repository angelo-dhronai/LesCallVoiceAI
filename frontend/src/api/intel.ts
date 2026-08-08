/** Intelligence layer API types + fetchers (backend: dashboard/intel_api.py). */
import { api } from './client'

export type IntelCallRow = {
  call_id: number; call_key: string | null; phone: string | null
  direction: string; agent_slug: string | null; started_at: string | null
  duration_s: number | null; outcome: string | null; contact_id: number | null
  language: string | null; contact_name: string | null
  summary: string | null; customer_intent: string | null
  sentiment: string | null; urgency: string | null
  lead_score: number | null; opportunity_score: number | null
  risk_score: number | null; compliance_score: number | null
  confidence: number | null; low_confidence: number
  tags: string[]; processed_at: string | null; model: string | null
  followup_id: number | null; followup_decision: string | null
  followup_status: string | null; followup_at: string | null
  followup_priority: string | null
  snippet?: string; score?: number
}

export type Commitment = {
  id: number; made_by: string; kind: string; description: string
  quote: string | null; deadline_text: string | null; deadline_at: string | null
  is_vague: number; clarified: number; source: string; confidence: number | null
  status: string
}

export type ActionItem = {
  id: number; description: string; status: string; priority: string
  assigned_to: string | null; due_at: string | null; source: string
}

export type Followup = {
  id: number; owner: string; call_id: number | null; contact_id: number | null
  decision: string; confidence: number | null; explanation: string | null
  reason: string | null; summary: string | null; followup_type: string | null
  promised_by: string | null; scheduled_at: string | null; timezone: string
  auto_scheduled: number; priority: string; status: string
  assigned_to: string | null; source: string
  notes: { at: string; by: string; text: string }[]
  audit: { at: string; by: string; action: string; detail: string }[]
  created_at: string; completed_at: string | null
  call_key?: string | null; phone?: string | null; contact_name?: string | null
  /** String id the outbound dialler keys on — present when the follow-up is
   *  linked to a contact, absent when it is not dialable. */
  contact_handle?: string | null
  dial_phone?: string | null
  /** Auto-dialer state: attempts made, and why the last sweep held off. */
  attempts?: number
  last_attempt_at?: string | null
  last_attempt_error?: string | null
  autodial_blocked_at?: string | null
  /** Execution mode (0008): 'auto' = the platform places the call at the
   *  scheduled time; 'manual' (default) = a human does. */
  mode?: 'manual' | 'auto'
  max_attempts?: number | null
  next_attempt_at?: string | null
  executed_call_id?: number | null
  execution_outcome?: string | null
  /** Human-in-the-loop gate (0011). No follow-up is ever auto-dialled while
   *  this is anything other than 'approved' — `mode: 'auto'` on its own is
   *  only a preference and grants nothing. */
  approval_state?: 'pending' | 'approved' | 'declined'
  approved_at?: string | null
  approved_by?: string | null
  approval_expires_at?: string | null
  declined_at?: string | null
  declined_by?: string | null
}

/** One recorded promise, with the words that made it. */
export type FollowupEvidence = {
  /** 'quoted' — a commitment was extracted from the call with the verbatim
   *  words. 'inferred' — nothing was promised; the AI proposed this itself. */
  strength: 'quoted' | 'inferred'
  commitments: {
    made_by: string; kind: string; description: string
    quote: string | null; deadline_text: string | null; confidence: number | null
  }[]
  transcript_tail: { at: string; speaker: string; text: string }[]
}

/** A follow-up awaiting a human's call decision, with its proof attached. */
export type FollowupReviewItem = Followup & {
  customer_intent?: string | null
  source_call_at?: string | null
  dnd?: boolean | null
  evidence: FollowupEvidence
}

export type IntelDetail = {
  call: Record<string, any>
  audio_url: string | null
  intelligence: Record<string, any> | null
  transcript: { timestamp: string; speaker: string; text: string }[]
  commitments: Commitment[]
  action_items: ActionItem[]
  followup: Followup | null
  memory: Record<string, any> | null
}

export type Report = {
  id: number; title: string; query_prompt: string | null; call_count: number
  status: string; model: string | null; error: string | null
  created_at: string; completed_at: string | null
  document?: string | null; call_ids?: number[]
}

export type ContactEvent = {
  id: number; kind: string; ref_table: string | null; ref_id: number | null
  occurred_at: string | null; payload: Record<string, any>
}

export type MemoryFact = {
  text: string; call_id: number | null; date: string | null
  confidence?: number; status?: string; updated_at?: string | null
}

export type ContactHubPayload = {
  contact: Record<string, any>
  access: 'owner' | 'edit' | 'read'
  stats: {
    calls: number; talk_seconds: number; calls_30d: number
    first_call_at: string | null; last_call_at: string | null
    outcome_mix: Record<string, number>
  }
  memory: Record<string, any>
  memory_meta: {
    calls_count: number; last_call_at: string | null; version: number
    digest: string | null; digest_updated_at: string | null
  } | null
  scores: {
    trend: { call_id: number; call_key: string | null; started_at: string | null
      lead_score: number | null; opportunity_score: number | null
      risk_score: number | null; sentiment: string | null }[]
    latest: { lead_score: number | null; opportunity_score: number | null
      risk_score: number | null; sentiment: string | null } | null
  }
  open_commitments: Commitment[]
  upcoming_followups: Followup[]
  recommendations: Record<string, any>
  latest_analysis: { summary: string | null; customer_intent: string | null
    processed_at: string | null } | null
  grants: { id: number; grantee_email: string; access: string; created_at: string }[]
}

export const searchIntel = (q: string, limit = 25) =>
  api<{ items: IntelCallRow[]; degraded: boolean; query: string }>(
    `/intel/search?q=${encodeURIComponent(q)}&limit=${limit}`)

/**
 * Which calls a report covers. Either an explicit `call_ids` selection (the
 * Intelligence page's tick-the-rows flow) or a `filters` object the server
 * resolves with the SAME predicates the Intelligence list uses — so a report
 * built from a filter covers exactly what that filter showed.
 */
export type ReportFilters = {
  days?: number; search?: string; sentiment?: string; decision?: string
  direction?: string; min_lead?: number; limit?: number
}

export const createReport = (body: {
  title?: string; query_prompt?: string
  call_ids?: number[]; filters?: ReportFilters
}) => api<{ ok: boolean; id: number }>('/intel/reports', {
  method: 'POST', body: JSON.stringify(body),
})

/** How many analyzed calls a filter would pull in, before committing to a
 *  background generation job. */
export const previewReport = (filters: ReportFilters) =>
  api<{ ok: boolean; call_count: number; capped: boolean }>('/intel/reports/preview', {
    method: 'POST', body: JSON.stringify({ filters }),
  })
