import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { CheckCircle2, AlertTriangle, Info, XCircle } from 'lucide-react'
import { cx } from './ui'

type ToastType = 'success' | 'error' | 'info' | 'warn'
type Toast = { id: number; text: string; type: ToastType }

const ToastCtx = createContext<(text: string, type?: ToastType) => void>(() => {})

export function useToast() {
  return useContext(ToastCtx)
}

const ICONS = { success: CheckCircle2, error: XCircle, warn: AlertTriangle, info: Info }
const TONES: Record<ToastType, string> = {
  success: 'text-mint-600 dark:text-mint-300 ring-mint-400/30',
  error: 'text-rose-500 dark:text-rose-300 ring-rose-500/30',
  warn: 'text-amber-600 dark:text-amber-300 ring-amber-400/30',
  info: 'text-indigo-500 dark:text-indigo-300 ring-indigo-500/30',
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const seq = useRef(0)

  const push = useCallback((text: string, type: ToastType = 'info') => {
    const id = ++seq.current
    setToasts((t) => [...t, { id, text, type }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000)
  }, [])

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(92vw,360px)] flex-col gap-2">
        {toasts.map((t) => {
          const Icon = ICONS[t.type]
          return (
            <div key={t.id}
              className={cx('glass pointer-events-auto flex items-start gap-2.5 rounded-xl px-3.5 py-3 text-[13px] font-medium shadow-glass-lg ring-1 animate-fade-up', TONES[t.type])}>
              <Icon size={16} className="mt-0.5 shrink-0" />
              <span className="text-[var(--text)]">{t.text}</span>
            </div>
          )
        })}
      </div>
    </ToastCtx.Provider>
  )
}
