import { api } from './client'
import { del } from './mutate'

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

export type ChatResponse = {
  reply: string
  actions: CopilotAction[]
  agents_changed: boolean
  session?: SessionSummary
}

export type TranscriptMsg = {
  role: 'user' | 'assistant'
  content: string
  actions?: CopilotAction[]
}

/* ───────────────────────── calls ───────────────────────── */
export const copilotChat = (session_id: string, message: string) =>
  api<ChatResponse>('/copilot/chat', {
    method: 'POST',
    body: JSON.stringify({ session_id, message }),
  })

export const copilotSession = (session_id: string) =>
  api<{ session_id: string; transcript: TranscriptMsg[] }>(`/copilot/sessions/${session_id}`)

export const copilotSessions = () =>
  api<{ sessions: SessionSummary[] }>('/copilot/sessions')

export const copilotDeleteSession = (session_id: string) =>
  del(`/copilot/sessions/${session_id}`)
