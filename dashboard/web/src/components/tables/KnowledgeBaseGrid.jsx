import { useState } from 'react'
import { FileText, FileSpreadsheet, Link2, File, CheckCircle2, UploadCloud, X } from 'lucide-react'
import { knowledgeBaseDocs } from '../../data/mockData.js'

const TYPE_ICON = { pdf: FileText, csv: FileSpreadsheet, url: Link2, docx: File }
const TYPE_TINT = {
  pdf: 'bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400',
  csv: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400',
  url: 'bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400',
  docx: 'bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400',
}

function UploadModal({ onClose }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="card w-full max-w-lg shadow-flyout" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">Upload document</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X className="h-5 w-5" /></button>
        </div>
        <div className="rounded-2xl border-2 border-dashed border-slate-300 dark:border-slate-700 p-10 text-center">
          <UploadCloud className="h-10 w-10 text-slate-400 mx-auto mb-3" />
          <p className="text-sm text-slate-600 dark:text-slate-300 font-medium">Drag &amp; drop files here</p>
          <p className="text-xs text-slate-400 mt-1">PDF, DOCX, CSV or a URL · up to 25 MB</p>
          <button className="btn btn-outline mt-4">Browse files</button>
        </div>
        <button className="btn btn-primary w-full mt-4" onClick={onClose}>Start indexing</button>
      </div>
    </div>
  )
}

export default function KnowledgeBaseGrid() {
  const [upload, setUpload] = useState(false)

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-slate-900 dark:text-white">Knowledge Base</h1>
          <p className="text-xs text-slate-400">{knowledgeBaseDocs.length} documents · RAG indexed</p>
        </div>
        <button className="btn btn-primary" onClick={() => setUpload(true)}><UploadCloud className="h-4 w-4" /> Upload Document</button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {knowledgeBaseDocs.map((d) => {
          const Icon = TYPE_ICON[d.type]
          const indexed = d.progress >= 100
          return (
            <div key={d.id} className="card-flat p-4 hover:shadow-pop transition-shadow">
              <div className="flex items-start justify-between">
                <span className={`h-10 w-10 rounded-xl grid place-items-center ${TYPE_TINT[d.type]}`}>
                  <Icon className="h-5 w-5" />
                </span>
                <span className="text-[10px] uppercase tracking-wider text-slate-400">{d.type}</span>
              </div>
              <div className="mt-3 text-sm font-semibold text-slate-800 dark:text-slate-100 truncate" title={d.name}>{d.name}</div>
              <div className="font-mono text-[11px] text-slate-400 mt-0.5">{d.ragId}</div>

              <div className="mt-3">
                <div className="flex items-center justify-between text-[11px] mb-1">
                  <span className={indexed ? 'text-emerald-600 flex items-center gap-1' : 'text-amber-600'}>
                    {indexed ? <><CheckCircle2 className="h-3 w-3" /> Indexed</> : 'Processing'}
                  </span>
                  <span className="text-slate-400">{d.progress}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${indexed ? 'bg-emerald-500' : 'bg-amber-400'}`}
                    style={{ width: `${d.progress}%` }}
                  />
                </div>
              </div>
              <div className="mt-3 text-[11px] text-slate-400">Uploaded {d.uploaded}</div>
            </div>
          )
        })}
      </div>

      {upload && <UploadModal onClose={() => setUpload(false)} />}
    </div>
  )
}
