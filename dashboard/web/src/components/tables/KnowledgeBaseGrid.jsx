import { useState, useEffect, useRef, useCallback } from 'react'
import {
  FileText, FileSpreadsheet, Link2, File, CheckCircle2, UploadCloud, X, Trash2,
  Loader2, RefreshCw, AlertTriangle, Download,
} from 'lucide-react'
import { api } from '../../api.js'

const TYPE_ICON = { pdf: FileText, csv: FileSpreadsheet, url: Link2, docx: File, txt: FileText }
const TYPE_TINT = {
  pdf: 'bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400',
  csv: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400',
  url: 'bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400',
  docx: 'bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400',
  txt: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
}
const fmtSize = (b) => !b ? '' : b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1048576).toFixed(1)} MB`

function UploadModal({ onClose, onUploaded }) {
  const [url, setUrl] = useState('')
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const inputRef = useRef(null)

  const submit = async () => {
    setBusy(true); setErr('')
    try {
      const fd = new FormData()
      if (file) fd.append('file', file)
      else if (url.trim()) fd.append('url', url.trim())
      else { setErr('Choose a file or paste a URL.'); setBusy(false); return }
      await api.uploadDocument(fd)
      onUploaded(); onClose()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="card w-full max-w-lg shadow-flyout" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">Upload document</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X className="h-5 w-5" /></button>
        </div>
        <div onClick={() => inputRef.current?.click()}
          className="rounded-2xl border-2 border-dashed border-slate-300 dark:border-slate-700 p-10 text-center cursor-pointer hover:border-accent transition-colors">
          <UploadCloud className="h-10 w-10 text-slate-400 mx-auto mb-3" />
          <p className="text-sm text-slate-600 dark:text-slate-300 font-medium">{file ? file.name : 'Click to choose a file'}</p>
          <p className="text-xs text-slate-400 mt-1">PDF, DOCX, CSV, TXT · up to 25 MB</p>
          <input ref={inputRef} type="file" className="hidden" accept=".pdf,.docx,.doc,.csv,.txt,.md"
            onChange={(e) => { setFile(e.target.files?.[0] || null); setErr('') }} />
        </div>
        <div className="mt-4">
          <div className="label">…or index a URL</div>
          <input className="input" value={url} onChange={(e) => { setUrl(e.target.value); setFile(null) }} placeholder="https://pramukhgroup.com/projects" />
        </div>
        {err && <div className="mt-3 text-sm text-rose-500">{err}</div>}
        <button className="btn btn-primary w-full mt-4" onClick={submit} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />} Start indexing
        </button>
      </div>
    </div>
  )
}

export default function KnowledgeBaseGrid() {
  const [docs, setDocs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [upload, setUpload] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { setDocs(await api.knowledge()) } catch (e) { setError(e.message) } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const remove = async (id) => {
    if (!confirm('Delete this document?')) return
    try { await api.deleteDocument(id); await load() } catch (e) { setError(e.message) }
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-slate-900 dark:text-white">Knowledge Base</h1>
          <p className="text-xs text-slate-400">{loading ? 'Loading…' : `${docs.length} document${docs.length === 1 ? '' : 's'}`}</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-outline" onClick={load}><RefreshCw className="h-4 w-4" /> Refresh</button>
          <button className="btn btn-primary" onClick={() => setUpload(true)}><UploadCloud className="h-4 w-4" /> Upload Document</button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 dark:border-rose-500/30 dark:bg-rose-500/10 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
          <AlertTriangle className="h-4 w-4" /> {error}
        </div>
      )}

      {loading ? (
        <div className="grid place-items-center py-16 text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>
      ) : docs.length === 0 ? (
        <div className="card-flat grid place-items-center py-16 text-center">
          <UploadCloud className="h-8 w-8 text-slate-300 mb-3" />
          <div className="text-sm font-medium text-slate-600 dark:text-slate-300">No documents yet</div>
          <div className="text-xs text-slate-400 mt-1">Upload brochures, price lists or FAQs to ground your agents.</div>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {docs.map((d) => {
            const type = d.doc_type || 'txt'
            const Icon = TYPE_ICON[type] || FileText
            const indexed = (d.progress ?? 0) >= 100 || d.status === 'indexed'
            return (
              <div key={d.id} className="card-flat p-4 hover:shadow-pop transition-shadow group">
                <div className="flex items-start justify-between">
                  <span className={`h-10 w-10 rounded-xl grid place-items-center ${TYPE_TINT[type] || TYPE_TINT.txt}`}>
                    <Icon className="h-5 w-5" />
                  </span>
                  <div className="flex items-center gap-1">
                    {d.stored_name && (
                      <a href={api.documentUrl(d.id)} className="text-slate-300 hover:text-accent opacity-0 group-hover:opacity-100 transition" title="Download"><Download className="h-4 w-4" /></a>
                    )}
                    <button onClick={() => remove(d.id)} className="text-slate-300 hover:text-rose-500 opacity-0 group-hover:opacity-100 transition" title="Delete"><Trash2 className="h-4 w-4" /></button>
                  </div>
                </div>
                <div className="mt-3 text-sm font-semibold text-slate-800 dark:text-slate-100 truncate" title={d.name}>{d.name}</div>
                <div className="text-[11px] text-slate-400 mt-0.5">{type.toUpperCase()}{d.size_bytes ? ` · ${fmtSize(d.size_bytes)}` : ''}</div>

                <div className="mt-3">
                  <div className="flex items-center justify-between text-[11px] mb-1">
                    <span className={indexed ? 'text-emerald-600 flex items-center gap-1' : 'text-amber-600'}>
                      {indexed ? <><CheckCircle2 className="h-3 w-3" /> Indexed</> : 'Processing'}
                    </span>
                    <span className="text-slate-400">{d.progress ?? 0}%</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                    <div className={`h-full rounded-full ${indexed ? 'bg-emerald-500' : 'bg-amber-400'}`} style={{ width: `${d.progress ?? 0}%` }} />
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {upload && <UploadModal onClose={() => setUpload(false)} onUploaded={load} />}
    </div>
  )
}
