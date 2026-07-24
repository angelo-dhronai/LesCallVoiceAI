import { api } from './client'
import { del, post } from './mutate'

/* ───────────────────────── types ───────────────────────── */
export type CopilotAction = { tool: string; summary: string; ok: boolean }

export type SessionSummary = {
  session_id: string
  title: string
  agent: string | null
  agent_name: string | null
  created_at?: string | null
  updated_at?: string | null
  message_count: number
}

export type InterviewLayer = {
  id: string
  title: string
  done: boolean
  skipped: boolean
}

export type InterviewProgress = {
  phase: 'idle' | 'interviewing' | 'built'
  complete: boolean
  layers: InterviewLayer[]
  next_layer_id: string | null
  draft: Record<string, any>
}

export type PendingChange = {
  change_id: string
  slug: string
  diff: string
  reason?: string
}

/** A playable TTS voice preview (served from /api/voices/sample/<speaker>). */
export type VoiceSample = { speaker: string; gender: string; url: string }

export type ChatResponse = {
  reply: string
  actions: CopilotAction[]
  agents_changed: boolean
  session?: SessionSummary
  interview?: InterviewProgress
  pending_changes?: PendingChange[]
  voice_samples?: VoiceSample[]
}

export type TranscriptMsg = {
  role: 'user' | 'assistant'
  content: string
  actions?: CopilotAction[]
  voice_samples?: VoiceSample[]
}

/* ───────────────────────── calls ───────────────────────── */
export const copilotChat = (session_id: string, message: string, attachments?: string[]) =>
  api<ChatResponse>('/copilot/chat', {
    method: 'POST',
    body: JSON.stringify(
      attachments?.length ? { session_id, message, attachments } : { session_id, message },
    ),
  })

export const copilotSession = (session_id: string) =>
  api<{ session_id: string; transcript: TranscriptMsg[] }>(`/copilot/sessions/${session_id}`)

export const copilotSessions = () =>
  api<{ sessions: SessionSummary[] }>('/copilot/sessions')

export const copilotDeleteSession = (session_id: string) =>
  del(`/copilot/sessions/${session_id}`)

/** Attach a document to the conversation; pass the doc_id in the next chat. */
export async function copilotUpload(session_id: string, file: File) {
  const fd = new FormData()
  fd.append('session_id', session_id)
  fd.append('file', file)
  const res = await fetch('/api/copilot/upload', { method: 'POST', body: fd })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<{ doc_id: string; session_id: string }>
}

/** Diff-card buttons — the click is the confirmation. */
export const applyPendingChange = (session_id: string, change_id: string) =>
  post<{ status: string; revision: number }>(
    `/copilot/sessions/${session_id}/changes/${change_id}/apply`)

export const discardPendingChange = (session_id: string, change_id: string) =>
  post<{ status: string }>(
    `/copilot/sessions/${session_id}/changes/${change_id}/discard`)
