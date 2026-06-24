import { useEffect, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { cx } from './ui'

function useEsc(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
}

/** Centered modal dialog. */
export function Modal({
  open, onClose, title, children, footer, wide,
}: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEsc(onClose)
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[80] grid place-items-center p-4">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className={cx('card relative z-10 w-full p-5 animate-fade-up', wide ? 'max-w-2xl' : 'max-w-md')}>
        {title && (
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="font-display text-[16px] font-bold tracking-tight">{title}</h3>
            <button onClick={onClose} className="grid h-8 w-8 place-items-center rounded-lg text-dim hover:bg-[var(--surface-2)]"><X size={16} /></button>
          </div>
        )}
        <div className="max-h-[70vh] overflow-y-auto">{children}</div>
        {footer && <div className="mt-4 flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  )
}

/** Right-side slide-over panel (used for transcripts / detail views). */
export function SlideOver({
  open, onClose, title, children, headerExtra,
}: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; headerExtra?: ReactNode }) {
  useEsc(onClose)
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[80]">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="absolute right-0 top-0 flex h-full w-[min(94vw,560px)] flex-col bg-[var(--surface-solid)] shadow-glass-lg animate-fade-up">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0 flex-1">
            <h3 className="truncate font-display text-[16px] font-bold tracking-tight">{title}</h3>
          </div>
          {headerExtra}
          <button onClick={onClose} className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-dim hover:bg-[var(--surface-2)]"><X size={16} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  )
}
