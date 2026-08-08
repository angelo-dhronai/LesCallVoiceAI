/**
 * Share an agent, and import a shared one — the two ends of one feature.
 *
 * They live in one module because they describe the same contract from either
 * side, and the sentence that has to stay true on both is the same:
 *
 *     An imported agent is a COPY. It gets its own name in the recipient's
 *     account, and from that moment the two are unrelated — editing one never
 *     changes the other, and deleting one never touches the other.
 *
 * The UI says that out loud in both dialogs rather than leaving people to
 * discover it, because the wrong guess ("they're editing MY agent") is the one
 * that stops someone from sharing at all.
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, Check, Copy, Download, FileText, GitBranch,
  Languages, Link2, PhoneIncoming, PhoneOutgoing, Share2, Trash2, Users,
} from 'lucide-react'
import { Badge, Button, Spinner, cx } from './ui'
import { Modal } from './Modal'
import { useToast } from './Toast'
import { useConfirm } from './confirm'
import { useRefresh } from '../api/hooks/useApi'
import { parseTs } from '../lib/format'
import {
  createShareLink, getShareLink, importShare, previewShare, revokeShareLink,
  type ImportResult, type ShareLink, type SharePreview,
} from '../api/share'

const errText = (e: any) => String(e?.reason || e?.message || e || 'Something went wrong')

function DirectionBadge({ direction }: { direction: string }) {
  const outbound = direction === 'outbound'
  return (
    <Badge tone={outbound ? 'indigo' : 'mint'}>
      {outbound ? <PhoneOutgoing size={11} /> : <PhoneIncoming size={11} />}
      {outbound ? 'outbound' : 'inbound'}
    </Badge>
  )
}

function formatExpiry(iso: string | null): string {
  const at = parseTs(iso)
  if (!at) return 'never expires'
  const days = Math.round((at.getTime() - Date.now()) / 86_400_000)
  if (days < 0) return 'expired'
  if (days === 0) return 'expires today'
  return `expires in ${days} day${days === 1 ? '' : 's'}`
}

/* ═════════════════════════════════════ sharer ═════════════════════════════ */

export function ShareAgentModal({
  slug, title, open, onClose,
}: {
  slug: string
  title?: string
  open: boolean
  onClose: () => void
}) {
  const toast = useToast()
  const confirm = useConfirm()
  const [link, setLink] = useState<ShareLink | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')

  // Load the EXISTING link first rather than minting on open: opening the
  // dialog to check whether you had already shared something must not create
  // a link (and a second one to revoke) as a side effect.
  useEffect(() => {
    if (!open || !slug) return
    let live = true
    setLoading(true); setError(''); setCopied(false)
    getShareLink(slug)
      .then((r) => { if (live) setLink(r.link) })
      .catch((e) => { if (live) setError(errText(e)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [open, slug])

  async function create() {
    setBusy(true); setError('')
    try {
      const r = await createShareLink(slug)
      setLink(r.link)
      await copy(r.link.url)
    } catch (e: any) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      toast('Share link copied', 'success')
      setTimeout(() => setCopied(false), 2200)
    } catch {
      // Clipboard is blocked outside a secure context or without permission —
      // the input below is selectable, so say that instead of failing silently.
      toast('Could not copy automatically — select the link and copy it', 'info')
    }
  }

  async function revoke() {
    if (!link) return
    const ok = await confirm({
      title: 'Revoke this share link?',
      description: link.import_count > 0
        ? `The link stops working immediately. The ${link.import_count} ${link.import_count === 1 ? 'person who has' : 'people who have'} already imported this agent keep their own copy — those copies are independent and cannot be taken back.`
        : 'The link stops working immediately. Nobody has imported this agent yet.',
      confirmLabel: 'Revoke link',
      destructive: true,
    })
    if (!ok) return
    setBusy(true)
    try {
      await revokeShareLink(link.token)
      setLink(null)
      toast('Share link revoked', 'success')
    } catch (e: any) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  return (
    <Modal open onClose={onClose} title={`Share “${title || slug}”`} wide>
      <p className="text-sm text-soft">
        Anyone with this link can import a <span className="font-semibold">copy</span> of this
        agent into their own LesCall account. Their copy is a separate agent from
        the moment they import it — your edits never reach it, and theirs never
        reach yours.
      </p>

      <ul className="mt-3 space-y-1.5 text-xs text-dim">
        <li>· Both the inbound and outbound versions travel together, and count as one agent against their plan.</li>
        <li>· Uploaded knowledge-base documents are copied too.</li>
        <li>· Your phone-number connections and your booked appointments are <span className="font-semibold">not</span> shared.</li>
      </ul>

      {error && (
        <div role="alert" className="mt-4 flex items-start gap-2 rounded-xl bg-rose-500/10 px-3.5 py-3 text-xs text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="mt-4">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-dim">
            <Spinner className="h-4 w-4" /> Checking for an existing link…
          </div>
        ) : link ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <input
                readOnly
                value={link.url}
                aria-label="Share link"
                onFocus={(e) => e.currentTarget.select()}
                className="min-w-0 flex-1 rounded-md bg-[var(--surface-2)] px-3 py-2 font-mono text-xs text-soft ring-1 ring-[var(--border)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              />
              <Button variant="primary" size="sm" onClick={() => copy(link.url)}>
                {copied ? <Check size={14} /> : <Copy size={14} />}
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-dim">
              <Badge tone="neutral"><Users size={11} /> {link.import_count} imported</Badge>
              <Badge tone={link.expires_at ? 'amber' : 'neutral'}>
                <Link2 size={11} /> {formatExpiry(link.expires_at)}
              </Badge>
              <Button variant="ghost" size="sm" className="ml-auto" disabled={busy}
                      onClick={() => { void revoke() }}>
                {busy ? <Spinner className="h-3 w-3" /> : <Trash2 size={13} />} Revoke
              </Button>
            </div>
          </div>
        ) : (
          <Button variant="primary" disabled={busy} onClick={() => { void create() }}>
            {busy ? <Spinner className="h-3.5 w-3.5" /> : <Share2 size={15} />}
            Create share link
          </Button>
        )}
      </div>
    </Modal>
  )
}

/* ════════════════════════════════════ recipient ═══════════════════════════ */

/**
 * The popup a share link lands on. Mounted by the Agents page whenever the URL
 * carries `?import=<token>`; it clears that parameter on close so a refresh
 * does not reopen a dialog the user already dismissed.
 */
export function ImportAgentModal({
  token, open, onClose,
}: {
  token: string
  open: boolean
  onClose: () => void
}) {
  const nav = useNavigate()
  const toast = useToast()
  const refresh = useRefresh()
  const [preview, setPreview] = useState<SharePreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<ImportResult | null>(null)

  useEffect(() => {
    if (!open || !token) return
    let live = true
    setLoading(true); setError(''); setPreview(null); setDone(null)
    previewShare(token)
      .then((p) => { if (live) setPreview(p) })
      .catch((e) => { if (live) setError(errText(e)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [open, token])

  async function doImport() {
    setBusy(true); setError('')
    try {
      const r = await importShare(token)
      setDone(r)
      refresh(['agents'], ['billing', 'summary'])
      toast(`Imported as “${r.primary}”`, 'success')
    } catch (e: any) {
      setError(errText(e))
      // A refused import may have changed what the preview should say (a
      // concurrent import filled the last plan slot), so re-read it.
      previewShare(token).then(setPreview).catch(() => {})
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  const a = preview?.agent
  const atCap = preview?.code === 'cap_agents'

  return (
    <Modal
      open
      onClose={onClose}
      title={done ? 'Agent imported' : 'Import this agent?'}
      wide
      footer={
        done ? (
          <>
            <Button variant="ghost" onClick={onClose}>Stay here</Button>
            <Button variant="primary" onClick={() => { onClose(); nav(`/build/agents/${done.primary}`) }}>
              Open it <ArrowRight size={14} />
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
            {atCap ? (
              <Link to="/billing" onClick={onClose}>
                <Button variant="primary">See plans <ArrowRight size={14} /></Button>
              </Link>
            ) : (
              <Button variant="primary" disabled={busy || loading || !preview?.can_import}
                      onClick={() => { void doImport() }}>
                {busy ? <Spinner className="h-3.5 w-3.5" /> : <Download size={15} />}
                Import to my account
              </Button>
            )}
          </>
        )
      }
    >
      {loading ? (
        <div className="flex items-center gap-2 py-6 text-sm text-dim">
          <Spinner className="h-4 w-4" /> Loading the shared agent…
        </div>
      ) : done ? (
        <div className="space-y-3">
          <p className="text-sm text-soft">
            “{a?.agent_name || done.family}” is now in your account as{' '}
            <code className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 font-mono text-2xs">{done.family}</code>.
            It is entirely yours — edit it freely; the person who shared it will
            never see your changes.
          </p>
          <p className="text-xs text-dim">
            It is not connected to any phone number yet. Open it, review the
            opening line, then Activate it on one of your numbers.
          </p>
        </div>
      ) : error ? (
        <div role="alert" className="flex items-start gap-2 rounded-xl bg-rose-500/10 px-3.5 py-3 text-sm text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : a ? (
        <div className="space-y-4">
          <div>
            <div className="font-display text-xl font-bold tracking-tight">
              {a.agent_name || 'Untitled agent'}
            </div>
            <div className="text-sm text-dim">{a.company || '—'}</div>
            <div className="mt-1 text-xs text-dim">
              Shared by {preview?.shared_by.name
                ? `${preview.shared_by.name} (${preview.shared_by.email})`
                : preview?.shared_by.email}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {a.variants.map((v) => <DirectionBadge key={v.slug} direction={v.direction} />)}
            {a.languages.map((l) => (
              <Badge key={l} tone="neutral"><Languages size={11} /> {l}</Badge>
            ))}
            <span title={a.stages.join(' → ')} className="inline-flex">
              <Badge tone="neutral"><GitBranch size={11} /> {a.stages.length} stages</Badge>
            </span>
            {a.documents > 0 && (
              <Badge tone="neutral">
                <FileText size={11} /> {a.documents} document{a.documents === 1 ? '' : 's'}
              </Badge>
            )}
          </div>

          <div className="rounded-xl bg-indigo-500/10 px-3.5 py-3 text-xs text-soft ring-1 ring-indigo-500/20">
            You get your own copy under a new name. It is independent from the
            original: your changes stay yours, and nothing you do here is
            visible to the person who shared it.
            {a.variants.length > 1 && ' Both directions arrive together and count as one agent on your plan.'}
          </div>

          {!preview?.can_import && preview?.reason && (
            <div
              role="alert"
              className={cx(
                'flex items-start gap-2 rounded-xl px-3.5 py-3 text-sm ring-1',
                atCap
                  ? 'bg-amber-500/10 text-amber-700 ring-amber-500/20 dark:text-amber-300'
                  : 'bg-rose-500/10 text-rose-600 ring-rose-500/20 dark:text-rose-300',
              )}
            >
              <AlertTriangle size={15} className="mt-0.5 shrink-0" />
              <div>
                <div>{preview.reason}</div>
                {atCap && preview.billing?.agents_limit != null && (
                  <div className="mt-1 text-xs opacity-90">
                    Using {preview.billing.agents_used}/{preview.billing.agents_limit} agents.
                    Archive one to free a slot, or upgrade your plan.
                  </div>
                )}
                {preview.code === 'already_imported' && preview.already_imported && (
                  <Link
                    to={`/build/agents/${preview.already_imported.slugs[0]}`}
                    onClick={onClose}
                    className="mt-1 inline-block text-xs font-semibold underline"
                  >
                    Open your copy
                  </Link>
                )}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </Modal>
  )
}
