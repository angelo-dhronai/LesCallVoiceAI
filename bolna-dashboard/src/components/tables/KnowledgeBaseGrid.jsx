import { useState } from 'react'
import {
  Upload,
  UploadCloud,
  FileText,
  FileType,
  Globe,
  Sheet,
  Check,
  Loader2,
  X,
} from 'lucide-react'
import { knowledgeBaseDocs } from '../../data/mockData.js'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// "Jun 15"
function formatShortDate(iso) {
  const d = new Date(iso)
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`
}

const TYPE_META = {
  pdf: { Icon: FileText, wrap: 'bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-400' },
  docx: { Icon: FileType, wrap: 'bg-accent-soft text-accent dark:bg-accent/15 dark:text-blue-300' },
  url: { Icon: Globe, wrap: 'bg-purple-50 text-purple-600 dark:bg-purple-500/10 dark:text-purple-400' },
  csv: { Icon: Sheet, wrap: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400' },
}

function DocCard({ doc }) {
  const meta = TYPE_META[doc.type] || TYPE_META.pdf
  const { Icon } = meta
  const done = doc.embeddingStatus === 100

  return (
    <div className="card p-4 transition-colors hover:border-gray-300 dark:hover:border-white/20 hover:bg-gray-50/60 dark:hover:bg-white/[0.03]">
      <div className="flex items-start gap-3">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${meta.wrap}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-gray-900 dark:text-gray-100" title={doc.name}>
            {doc.name}
          </p>
          <p className="truncate font-mono text-xs text-gray-400 dark:text-gray-500">{doc.ragId}</p>
        </div>
      </div>

      {/* Embedding status */}
      <div className="mt-4 space-y-1.5">
        <div className="flex items-center justify-between text-xs">
          {done ? (
            <span className="inline-flex items-center gap-1 font-medium text-emerald-600 dark:text-emerald-400">
              <Check className="h-3.5 w-3.5" />
              Indexed
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 font-medium text-amber-600 dark:text-amber-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Processing {doc.embeddingStatus}%
            </span>
          )}
          <span className="text-gray-400 dark:text-gray-500">{formatShortDate(doc.uploadedAt)}</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-white/10">
          <div
            className={`h-full rounded-full ${done ? 'bg-emerald-500' : 'bg-accent'}`}
            style={{ width: `${doc.embeddingStatus}%` }}
          />
        </div>
      </div>
    </div>
  )
}

function UploadModal({ onClose }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-md p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Upload document"
      >
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white">Upload Document</h2>
            <p className="mt-0.5 text-sm text-gray-500 dark:text-gray-400">
              Add a file or URL to the knowledge base.
            </p>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost p-2" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Drop zone (visual only) */}
        <div className="mt-5 flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed border-gray-300 dark:border-white/15 bg-gray-50 dark:bg-white/[0.02] px-6 py-10 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-accent dark:bg-accent/15 dark:text-blue-300">
            <UploadCloud className="h-6 w-6" />
          </div>
          <p className="text-sm text-gray-600 dark:text-gray-300">Drag &amp; drop or click to browse</p>
          <p className="text-xs text-gray-400 dark:text-gray-500">PDF, DOCX, CSV or a URL</p>
          <button type="button" className="btn-outline mt-1">
            Choose file
          </button>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn-outline">
            Cancel
          </button>
          <button type="button" onClick={onClose} className="btn-primary">
            <Upload className="h-4 w-4" />
            Upload
          </button>
        </div>
      </div>
    </div>
  )
}

export default function KnowledgeBaseGrid() {
  const [modalOpen, setModalOpen] = useState(false)

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="page-title">Knowledge Base</h1>
          <span className="text-sm text-gray-400 dark:text-gray-500">{knowledgeBaseDocs.length} documents</span>
        </div>
        <button type="button" onClick={() => setModalOpen(true)} className="btn-primary">
          <Upload className="h-4 w-4" />
          Upload Document
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {knowledgeBaseDocs.map((doc) => (
          <DocCard key={doc.ragId} doc={doc} />
        ))}
      </div>

      {modalOpen && <UploadModal onClose={() => setModalOpen(false)} />}
    </div>
  )
}
