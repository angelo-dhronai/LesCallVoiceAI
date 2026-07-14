import { useMemo, useRef, useState } from 'react'
import {
  BookUser, FolderPlus, Folder, FolderOpen, Pencil, Plus, Trash2, Upload,
  Users, X,
} from 'lucide-react'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { post, patch, del, postForm } from '../api/mutate'
import { Badge, Button, Card, EmptyState, Spinner, cx } from '../components/ui'
import {
  PageHeader, Table, THead, TH, TR, TD, Pagination, SearchInput, Input,
  Textarea, Select, Field, AsyncState,
} from '../components/kit'
import { Modal } from '../components/Modal'
import { useToast } from '../components/Toast'

type Contact = {
  id: string
  db_id?: number
  phone: string
  name: string
  notes: string
  gender?: string
  details?: Record<string, string>
  group_id?: number | null
  added_at?: string | null
  call_count?: number
  last_called_at?: string | null
}

type Group = { id: number; name: string; count: number }

type GroupsResp = { groups: Group[]; ungrouped: number; total: number }
type ContactsResp = { contacts: Contact[]; total: number }

const PAGE_SIZE = 50

function detailsPreview(d?: Record<string, string>): string {
  if (!d || Object.keys(d).length === 0) return ''
  return Object.entries(d).map(([k, v]) => `${k}: ${v}`).join(' · ')
}

/** details ⇄ textarea: shown as "key: value" lines, parsed back to a dict. */
function detailsToText(d?: Record<string, string>): string {
  if (!d) return ''
  return Object.entries(d).map(([k, v]) => `${k}: ${v}`).join('\n')
}

function textToDetails(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  let free: string[] = []
  for (const line of text.split('\n')) {
    const t = line.trim()
    if (!t) continue
    const i = t.indexOf(':')
    if (i > 0) out[t.slice(0, i).trim()] = t.slice(i + 1).trim()
    else free.push(t)
  }
  if (free.length) out.info = [out.info, ...free].filter(Boolean).join(' | ')
  return out
}

type EditorState = {
  mode: 'add' | 'edit'
  id?: string
  name: string
  phone: string
  gender: string
  group_id: number | ''
  notes: string
  detailsText: string
}

const EMPTY_EDITOR: EditorState = {
  mode: 'add', name: '', phone: '', gender: '', group_id: '', notes: '', detailsText: '',
}

export default function Contacts() {
  const toast = useToast()
  const refresh = useRefresh()
  const [groupSel, setGroupSel] = useState<'all' | 'none' | number>('all')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [newGroupOpen, setNewGroupOpen] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const groupsQ = useGet<GroupsResp>(['contact-groups'], '/outbound/groups')
  const groups = groupsQ.data?.groups ?? []

  const groupParam = groupSel === 'all' ? 'all' : groupSel === 'none' ? 'none' : String(groupSel)
  const contactsQ = useGet<ContactsResp>(
    ['contacts-book', groupParam, search],
    `/outbound/contacts?group=${groupParam}&q=${encodeURIComponent(search)}`,
  )
  const all = contactsQ.data?.contacts ?? []
  const pages = Math.max(1, Math.ceil(all.length / PAGE_SIZE))
  const rows = useMemo(
    () => all.slice((Math.min(page, pages) - 1) * PAGE_SIZE, Math.min(page, pages) * PAGE_SIZE),
    [all, page, pages],
  )

  const groupName = (id?: number | null) => groups.find((g) => g.id === id)?.name

  const refreshAll = () => refresh(['contacts-book'], ['contact-groups'], ['outbound-contacts'])

  const saveContact = async () => {
    if (!editor) return
    setBusy(true)
    try {
      const body = {
        name: editor.name.trim(),
        gender: editor.gender,
        notes: editor.notes.trim(),
        group_id: editor.group_id === '' ? null : editor.group_id,
        details: textToDetails(editor.detailsText),
      }
      if (editor.mode === 'add') {
        await post('/outbound/contacts', { ...body, phone: editor.phone.trim() })
        toast('Contact added', 'success')
      } else {
        await patch(`/outbound/contacts/${editor.id}`, body)
        toast('Contact updated', 'success')
      }
      setEditor(null)
      refreshAll()
    } catch (e: any) {
      toast(String(e?.message || e).includes('409') ? 'That phone number already exists in your contacts' : `Save failed: ${e?.message || e}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  const removeContact = async (c: Contact) => {
    if (!window.confirm(`Delete ${c.name || c.phone} from your contacts?`)) return
    try {
      await del(`/outbound/contacts/${c.id}`)
      toast('Contact deleted', 'success')
      refreshAll()
    } catch (e: any) {
      toast(`Delete failed: ${e?.message || e}`, 'error')
    }
  }

  const createGroup = async () => {
    const name = newGroupName.trim()
    if (!name) return
    setBusy(true)
    try {
      await post('/outbound/groups', { name })
      toast(`Group “${name}” created`, 'success')
      setNewGroupOpen(false)
      setNewGroupName('')
      refresh(['contact-groups'])
    } catch (e: any) {
      toast(String(e?.message || e).includes('409') ? 'A group with that name already exists' : `Failed: ${e?.message || e}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  const removeGroup = async (g: Group) => {
    if (!window.confirm(`Delete group “${g.name}”? Its ${g.count} contact(s) move to No group.`)) return
    try {
      await del(`/outbound/groups/${g.id}`)
      if (groupSel === g.id) setGroupSel('all')
      toast('Group deleted', 'success')
      refreshAll()
    } catch (e: any) {
      toast(`Delete failed: ${e?.message || e}`, 'error')
    }
  }

  const uploadFile = async (file: File) => {
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const r = await postForm<{ ok: boolean; added?: number; duplicates?: number; parse_errors?: number; reason?: string }>(
        '/outbound/contacts/upload', form,
      )
      if (r.ok) {
        toast(`Imported ${r.added} contact(s)` +
          (r.duplicates ? `, ${r.duplicates} duplicate(s) skipped` : '') +
          (r.parse_errors ? `, ${r.parse_errors} row(s) unreadable` : ''),
          'success')
        refreshAll()
      } else {
        toast(r.reason || 'Nothing imported', 'warn')
      }
    } catch (e: any) {
      toast(`Upload failed: ${e?.message || e}`, 'error')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const sidebarItem = (
    active: boolean, label: string, count: number | undefined,
    onClick: () => void, icon: React.ReactNode, onDelete?: () => void,
  ) => (
    <div
      key={label}
      className={cx(
        'group flex cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-[13px] font-medium transition',
        active ? 'bg-[var(--surface-2)] text-[var(--text)] ring-1 ring-[var(--border)]' : 'text-dim hover:bg-[var(--surface-2)] hover:text-soft',
      )}
      onClick={onClick}
      role="button"
    >
      <span className="shrink-0">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {count != null && <span className="tabular-nums text-[11px] text-dim">{count}</span>}
      {onDelete && (
        <button
          onClick={(e) => { e.stopPropagation(); onDelete() }}
          aria-label={`Delete group ${label}`}
          title="Delete group"
          className="grid h-7 w-7 -my-1 shrink-0 place-items-center rounded-lg text-dim transition-opacity hover:text-rose-400 lg:my-0 lg:h-auto lg:w-auto lg:opacity-0 lg:group-hover:opacity-100"
        >
          <X size={13} />
        </button>
      )}
    </div>
  )

  return (
    <div className="space-y-6">
      <PageHeader
        title="Contacts"
        sub="Your phone book — people, groups & imported details"
        actions={
          <>
            <input
              ref={fileRef} type="file" accept=".csv,.xlsx,.xls,.txt" className="hidden"
              onChange={(e) => e.target.files?.[0] && uploadFile(e.target.files[0])}
            />
            <Button variant="ghost" onClick={() => fileRef.current?.click()} disabled={uploading}>
              {uploading ? <Spinner className="h-4 w-4" /> : <Upload size={14} />} Import CSV
            </Button>
            <Button onClick={() => { setEditor({ ...EMPTY_EDITOR, group_id: typeof groupSel === 'number' ? groupSel : '' }) }}>
              <Plus size={14} /> Add contact
            </Button>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
        {/* Groups sidebar */}
        <Card className="h-fit p-3">
          <div className="mb-2 flex items-center justify-between px-1">
            <span className="text-[11px] font-bold uppercase tracking-wider text-dim">Groups</span>
            <button
              onClick={() => setNewGroupOpen(true)}
              aria-label="New group" title="New group"
              className="grid h-9 w-9 -m-1.5 place-items-center rounded-lg text-dim transition hover:bg-[var(--surface-2)] hover:text-soft lg:m-0 lg:h-6 lg:w-6"
            >
              <FolderPlus size={14} />
            </button>
          </div>
          <div className="space-y-0.5">
            {sidebarItem(groupSel === 'all', 'All contacts', groupsQ.data?.total, () => { setGroupSel('all'); setPage(1) }, <Users size={14} />)}
            {sidebarItem(groupSel === 'none', 'No group', groupsQ.data?.ungrouped, () => { setGroupSel('none'); setPage(1) }, <Folder size={14} />)}
            {groups.map((g) =>
              sidebarItem(groupSel === g.id, g.name, g.count, () => { setGroupSel(g.id); setPage(1) },
                groupSel === g.id ? <FolderOpen size={14} /> : <Folder size={14} />,
                () => removeGroup(g)),
            )}
          </div>
        </Card>

        {/* Contact list */}
        <Card className="p-5">
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1) }} placeholder="Search name or number" className="w-full max-w-sm" />
            {contactsQ.isFetching && <Spinner className="h-4 w-4 shrink-0" />}
            <div className="ml-auto">
              {all.length > 0 && <Badge tone="neutral">{all.length.toLocaleString('en-IN')} contact{all.length === 1 ? '' : 's'}</Badge>}
            </div>
          </div>

          <AsyncState loading={contactsQ.isLoading} error={contactsQ.error}>
            {rows.length === 0 ? (
              <EmptyState
                icon={<BookUser size={20} />}
                title={search ? `No contacts match “${search}”` : 'No contacts here yet'}
                hint={search ? 'Try a shorter name or the last digits of the number.' : 'Add a person or import a CSV — name, phone and gender columns are picked up automatically; every other column is kept in Details.'}
                action={!search ? (
                  <Button size="sm" onClick={() => setEditor({ ...EMPTY_EDITOR })}><Plus size={14} /> Add contact</Button>
                ) : undefined}
              />
            ) : (
              <>
                <Table>
                  <THead>
                    <TH>Name</TH>
                    <TH>Phone</TH>
                    <TH>Gender</TH>
                    <TH>Group</TH>
                    <TH>Details</TH>
                    <TH className="text-right">Actions</TH>
                  </THead>
                  <tbody>
                    {rows.map((c) => (
                      <TR key={c.id} className="group">
                        <TD className="font-medium text-[var(--text)]">{c.name || <span className="text-dim">—</span>}</TD>
                        <TD className="whitespace-nowrap font-mono text-[12.5px]">{c.phone}</TD>
                        <TD>{c.gender ? <Badge tone={c.gender === 'female' ? 'rose' : c.gender === 'male' ? 'indigo' : 'neutral'} soft>{c.gender}</Badge> : <span className="text-dim">—</span>}</TD>
                        <TD>{groupName(c.group_id) ? <Badge tone="mint" soft>{groupName(c.group_id)}</Badge> : <span className="text-[12px] text-dim">No group</span>}</TD>
                        <TD className="max-w-[260px]">
                          <span className="block truncate text-[12px] text-dim" title={detailsPreview(c.details)}>
                            {detailsPreview(c.details) || (c.notes || '—')}
                          </span>
                        </TD>
                        <TD className="text-right">
                          <div className="flex items-center justify-end gap-1 transition-opacity lg:opacity-0 lg:focus-within:opacity-100 lg:group-hover:opacity-100">
                            <button
                              onClick={() => setEditor({
                                mode: 'edit', id: c.id, name: c.name || '', phone: c.phone,
                                gender: c.gender || '', group_id: c.group_id ?? '',
                                notes: c.notes || '', detailsText: detailsToText(c.details),
                              })}
                              aria-label="Edit contact" title="Edit"
                              className="grid h-9 w-9 place-items-center rounded-lg text-dim transition hover:bg-[var(--surface-2)] hover:text-soft lg:h-7 lg:w-7"
                            >
                              <Pencil size={14} />
                            </button>
                            <button
                              onClick={() => removeContact(c)}
                              aria-label="Delete contact" title="Delete"
                              className="grid h-9 w-9 place-items-center rounded-lg text-dim transition hover:bg-[var(--surface-2)] hover:text-rose-400 lg:h-7 lg:w-7"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </TD>
                      </TR>
                    ))}
                  </tbody>
                </Table>
                <Pagination page={Math.min(page, pages)} pages={pages} total={all.length} onPage={setPage} />
              </>
            )}
          </AsyncState>
        </Card>
      </div>

      {/* Add / edit contact */}
      <Modal
        open={editor != null}
        onClose={() => setEditor(null)}
        title={editor?.mode === 'edit' ? 'Edit contact' : 'Add contact'}
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditor(null)}>Cancel</Button>
            <Button onClick={saveContact} disabled={busy || (editor?.mode === 'add' && !editor.phone.trim())}>
              {busy ? <Spinner className="h-4 w-4" /> : null} {editor?.mode === 'edit' ? 'Save changes' : 'Add contact'}
            </Button>
          </>
        }
      >
        {editor && (
          <div className="space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Name">
                <Input value={editor.name} onChange={(e) => setEditor({ ...editor, name: e.target.value })} placeholder="Full name" />
              </Field>
              <Field label="Phone">
                <Input
                  value={editor.phone}
                  onChange={(e) => setEditor({ ...editor, phone: e.target.value })}
                  placeholder="+91 98765 43210"
                  disabled={editor.mode === 'edit'}
                />
              </Field>
              <Field label="Gender">
                <Select value={editor.gender} onChange={(e) => setEditor({ ...editor, gender: e.target.value })}>
                  <option value="">—</option>
                  <option value="male">Male</option>
                  <option value="female">Female</option>
                  <option value="other">Other</option>
                </Select>
              </Field>
              <Field label="Group">
                <Select
                  value={editor.group_id === '' ? '' : String(editor.group_id)}
                  onChange={(e) => setEditor({ ...editor, group_id: e.target.value === '' ? '' : Number(e.target.value) })}
                >
                  <option value="">No group</option>
                  {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                </Select>
              </Field>
            </div>
            <Field label="Notes">
              <Input value={editor.notes} onChange={(e) => setEditor({ ...editor, notes: e.target.value })} placeholder="Anything worth remembering" />
            </Field>
            <Field label="Details (one “key: value” per line)">
              <Textarea
                rows={4}
                value={editor.detailsText}
                onChange={(e) => setEditor({ ...editor, detailsText: e.target.value })}
                placeholder={'company: Pramukh Group\ncity: Ahmedabad'}
              />
            </Field>
          </div>
        )}
      </Modal>

      {/* New group */}
      <Modal
        open={newGroupOpen}
        onClose={() => { setNewGroupOpen(false); setNewGroupName('') }}
        title="New group"
        footer={
          <>
            <Button variant="ghost" onClick={() => { setNewGroupOpen(false); setNewGroupName('') }}>Cancel</Button>
            <Button onClick={createGroup} disabled={busy || !newGroupName.trim()}>
              {busy ? <Spinner className="h-4 w-4" /> : <FolderPlus size={14} />} Create group
            </Button>
          </>
        }
      >
        <Field label="Group name">
          <Input
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && createGroup()}
            placeholder="e.g. Site-visit leads"
            autoFocus
          />
        </Field>
      </Modal>
    </div>
  )
}
