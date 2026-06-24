import type { ReactNode } from 'react'

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(' ')
}

export function Card({ className, children, hover, glow }: { className?: string; children: ReactNode; hover?: boolean; glow?: 'mint' | 'indigo' }) {
  return (
    <div className={cx('card', hover && 'card-hover', glow === 'mint' && 'shadow-glow-mint', glow === 'indigo' && 'shadow-glow-indigo', className)}>
      {children}
    </div>
  )
}

export function Badge({ children, tone = 'neutral', soft }: { children: ReactNode; tone?: 'mint' | 'indigo' | 'amber' | 'rose' | 'neutral'; soft?: boolean }) {
  const tones: Record<string, string> = {
    mint: 'text-mint-700 dark:text-mint-300 bg-mint-400/15 ring-mint-400/30',
    indigo: 'text-indigo-600 dark:text-indigo-300 bg-indigo-500/12 ring-indigo-500/30',
    amber: 'text-amber-600 dark:text-amber-300 bg-amber-400/15 ring-amber-400/30',
    rose: 'text-rose-600 dark:text-rose-300 bg-rose-500/12 ring-rose-500/30',
    neutral: 'text-soft bg-[var(--surface-2)] ring-[var(--border)]',
  }
  return (
    <span className={cx('inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ring-1', tones[tone], soft && 'opacity-90')}>
      {children}
    </span>
  )
}

export function Button({
  children, onClick, variant = 'primary', size = 'md', className, disabled, title,
}: {
  children: ReactNode; onClick?: () => void; variant?: 'primary' | 'ghost' | 'danger' | 'soft'
  size?: 'sm' | 'md'; className?: string; disabled?: boolean; title?: string
}) {
  const variants: Record<string, string> = {
    primary: 'text-[#04150d] bg-gradient-to-b from-mint-300 to-mint-500 hover:from-mint-200 hover:to-mint-400 shadow-glow-mint',
    ghost: 'text-soft bg-[var(--surface-2)] hover:bg-[var(--border)] ring-1 ring-[var(--border)]',
    soft: 'text-indigo-600 dark:text-indigo-300 bg-indigo-500/12 hover:bg-indigo-500/20 ring-1 ring-indigo-500/20',
    danger: 'text-rose-600 dark:text-rose-300 bg-rose-500/12 hover:bg-rose-500/20 ring-1 ring-rose-500/20',
  }
  return (
    <button title={title} disabled={disabled} onClick={onClick}
      className={cx('inline-flex items-center justify-center gap-1.5 rounded-xl font-semibold transition-all active:scale-[.97] disabled:opacity-40 disabled:pointer-events-none',
        size === 'sm' ? 'px-3 py-1.5 text-[12px]' : 'px-4 py-2 text-[13px]', variants[variant], className)}>
      {children}
    </button>
  )
}

export function Spinner({ className }: { className?: string }) {
  return (
    <div className={cx('inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent opacity-60', className)} />
  )
}

export function EmptyState({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      {icon && <div className="grid h-12 w-12 place-items-center rounded-2xl surface-2 text-dim">{icon}</div>}
      <div className="text-sm font-medium text-soft">{title}</div>
      {hint && <div className="max-w-xs text-xs text-dim">{hint}</div>}
    </div>
  )
}

export function SectionHead({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="mb-4 flex items-end justify-between gap-3">
      <div>
        <h2 className="font-display text-lg font-bold tracking-tight">{title}</h2>
        {sub && <p className="mt-0.5 text-[13px] text-dim">{sub}</p>}
      </div>
      {right}
    </div>
  )
}

export function SkeletonBlock({ h = 80, className }: { h?: number; className?: string }) {
  return <div className={cx('skeleton w-full', className)} style={{ height: h }} />
}
