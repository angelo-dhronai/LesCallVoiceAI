/**
 * Agent share links.
 *
 * Sharing hands someone a COPY, not access: the recipient's import creates an
 * independent agent in their own account with its own slug, and neither side's
 * edits ever reach the other. Nothing here creates a live relationship, which
 * is why there is no "shared with me" list to keep in sync — once imported, it
 * is just one of your agents.
 */
import { api } from './client'
import { post, del } from './mutate'

/** A link the signed-in user created for one of their own agents. */
export type ShareLink = {
  token: string
  /** Full https URL to hand out — short `/s/<token>` form. */
  url: string
  family: string
  label: string
  state: 'live' | 'revoked' | 'expired'
  import_count: number
  expires_at: string | null
  created_at: string
}

/** What the import popup renders about someone else's agent. */
export type SharePreview = {
  token: string
  agent: {
    agent_name: string
    company: string
    languages: string[]
    stages: string[]
    directions: ('inbound' | 'outbound')[]
    variants: { slug: string; direction: 'inbound' | 'outbound' }[]
    calendar_slots: number
    documents: number
  }
  shared_by: { email: string; name: string }
  expires_at: string | null
  can_import: boolean
  /** Human sentence explaining a refusal the user can act on. */
  reason: string
  /** 'cap_agents' | 'already_imported' | 'rate_limited' | '' */
  code: string
  billing: { agents_used?: number; agents_limit?: number | null; enforcement?: boolean }
  already_imported: { slugs: string[]; at: string } | null
}

export type ImportResult = {
  status: string
  family: string
  slugs: string[]
  /** The slug to open in the editor. */
  primary: string
}

/* ── sharer ── */
export const getShareLink = (slug: string) =>
  api<{ link: ShareLink | null; family: string }>(`/share?slug=${encodeURIComponent(slug)}`)

/** Idempotent: calling twice returns the SAME link rather than a second one. */
export const createShareLink = (slug: string) =>
  post<{ link: ShareLink; created: boolean }>('/share', { slug })

export const revokeShareLink = (token: string) =>
  del<{ status: string; import_count: number }>(`/share/${token}`)

/* ── recipient ── */
export const previewShare = (token: string) =>
  api<SharePreview>(`/share/${encodeURIComponent(token)}`)

export const importShare = (token: string) =>
  post<ImportResult>(`/share/${encodeURIComponent(token)}/import`)
