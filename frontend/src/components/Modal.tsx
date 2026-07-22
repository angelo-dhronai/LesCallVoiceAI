import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { cx } from './ui'

function useEsc(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
}

/** Lock body scroll while an overlay is open. */
function useScrollLock(open: boolean) {
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [open])
}

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'

/**
 * Move focus into the dialog on open, keep Tab cycling inside it, and hand
 * focus back to the opener on close — without this, Tab walks the page
 * hidden behind the backdrop.
 */
function useFocusTrap(open: boolean) {
  const panelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const opener = document.activeElement as HTMLElement | null
    const raf = requestAnimationFrame(() => {
      panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus()
    })
    const h = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !panelRef.current) return
      const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter((el) => el.offsetParent !== null)
      if (!items.length) return
      const first = items[0], last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', h)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('keydown', h)
      opener?.focus?.()
    }
  }, [open])
  return panelRef
}

/** Centered modal dialog. */
export function Modal({
  open, onClose, title, children, footer, wide,
}: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEsc(onClose)
  useScrollLock(open)
  const panelRef = useFocusTrap(open)
  if (!open) return null
  return (
    <div role="dialog" aria-modal="true" aria-labelledby={title ? 'modal-title' : undefined}>
      <div className="fixed inset-0 z-[80] bg-zinc-950/30" onClick={onClose} aria-hidden="true" />
      <div ref={panelRef} className={cx('card relative z-[90] w-full p-5 shadow-glass-lg animate-fade-up', wide ? 'max-w-2xl' : 'max-w-md')}>
        {title && (
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 id="modal-title" className="font-display text-lg font-semibold tracking-tight">{title}</h3>
            <button onClick={onClose} aria-label="Close dialog" className="grid h-8 w-8 place-items-center rounded-lg text-dim transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"><X size={16} /></button>
          </div>
        )}
        <div className="max-h-[70vh] overflow-y-auto">{children}</div>
        {footer && <div className="mt-4 flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  )
}

/**
 * Near-fullscreen dialog: a centered popup that claims almost the whole
 * browser space (true fullscreen on phones). For content that deserves the
 * whole viewport — e.g. a live call transcript.
 */
export function FullscreenModal({
  open, onClose, title, children, headerExtra, footer,
}: {
  open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode
  headerExtra?: ReactNode; footer?: ReactNode
}) {
  useEsc(onClose)
  useScrollLock(open)
  const panelRef = useFocusTrap(open)
  if (!open) return null
  return (
    <div role="dialog" aria-modal="true" aria-labelledby={title ? 'fullscreen-modal-title' : undefined}>
      <div className="fixed inset-0 z-[80] bg-zinc-950/40" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        className="fixed inset-0 z-[90] flex flex-col overflow-hidden bg-[var(--surface-solid)] shadow-glass-lg animate-fade-up"
      >
        <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-[var(--border)] px-4 py-3 sm:px-6">
          <h3 id="fullscreen-modal-title" className="min-w-0 flex-1 truncate font-display text-lg font-bold tracking-tight sm:text-xl">
            {title}
          </h3>
          {headerExtra}
          <button onClick={onClose} aria-label="Close dialog"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-dim transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]">
            <X size={18} />
          </button>
        </div>
        {children}
        {footer && (
          <div className="shrink-0 border-t border-[var(--border)] px-4 py-3 sm:px-6">{footer}</div>
        )}
      </div>
    </div>
  )
}

/** Right-side slide-over panel (used for transcripts / detail views). */
export function SlideOver({
  open, onClose, title, children, headerExtra,
}: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; headerExtra?: ReactNode }) {
  useEsc(onClose)
  useScrollLock(open)
  const panelRef = useFocusTrap(open)
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[80]" role="dialog" aria-modal="true" aria-labelledby={title ? 'slideover-title' : undefined}>
      <div className="absolute inset-0 bg-zinc-950/30" onClick={onClose} aria-hidden="true" />
      <div ref={panelRef} className="absolute right-0 top-0 flex h-full w-[min(94vw,560px)] flex-col bg-[var(--surface-solid)] shadow-glass-lg animate-slide-in-right">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0 flex-1">
            <h3 id="slideover-title" className="truncate font-display text-lg font-bold tracking-tight">{title}</h3>
          </div>
          {headerExtra}
          <button onClick={onClose} aria-label="Close panel" className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-dim hover:bg-[var(--surface-2)]"><X size={16} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  )
}