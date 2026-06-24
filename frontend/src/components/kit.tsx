import type { ReactNode } from 'react'
import { Search } from 'lucide-react'
import { Badge, cx } from './ui'

/* ------------------------------------------------------------------ Page header */

export function PageHeader({
  title, sub, actions,
}: { title: string; sub?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="font-display text-[22px] font-bold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 text-[13px] text-dim">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/* ------------------------------------------------------------------ Table */

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx('overflow-x-auto', className)}>
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  )
}
export function THead({ children }: { children: ReactNode }) {
  return (
    <thead>
      <tr className="border-b border-[var(--border)] text-left">{children}</tr>
    </thead>
  )
}
export function TH({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th className={cx('whitespace-nowrap px-3 py-2.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-dim', className)}>
      {children}
    </th>
  )
}
export function TR({ children, className, onClick }: { children: ReactNode; className?: string; onClick?: () => void }) {
  return (
    <tr onClick={onClick}
      className={cx('border-b border-[var(--border)] transition-colors hover:bg-[var(--surface-2)]', onClick && 'cursor-pointer', className)}>
      {children}
    </tr>
  )
}
export function TD({ children, className, colSpan, title }: { children?: ReactNode; className?: string; colSpan?: number; title?: string }) {
  return <td colSpan={colSpan} title={title} className={cx('px-3 py-2.5 align-middle text-soft', className)}>{children}</td>
}

/* ------------------------------------------------------------------ Pagination */

export function Pagination({
  page, pages, total, onPage,
}: { page: number; pages: number; total?: number; onPage: (p: number) => void }) {
  if (pages <= 1 && !total) return null
  return (
    <div className="mt-3 flex items-center justify-between gap-3 text-[12px] text-dim">
      <div>{total != null ? `${total.toLocaleString('en-IN')} total` : ''}</div>
      <div className="flex items-center gap-2">
        <button disabled={page <= 1} onClick={() => onPage(page - 1)}
          className="rounded-lg px-3 py-1.5 font-semibold ring-1 ring-[var(--border)] transition hover:bg-[var(--surface-2)] disabled:opacity-40">
          Prev
        </button>
        <span className="tabular-nums">Page {page} / {Math.max(1, pages)}</span>
        <button disabled={page >= pages} onClick={() => onPage(page + 1)}
          className="rounded-lg px-3 py-1.5 font-semibold ring-1 ring-[var(--border)] transition hover:bg-[var(--surface-2)] disabled:opacity-40">
          Next
        </button>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ Status badge */

type Tone = 'mint' | 'indigo' | 'amber' | 'rose' | 'neutral'

const STATUS_TONE: Record<string, Tone> = {
  // call status
  answered: 'mint', completed: 'mint', called: 'mint', interested: 'mint', site_visit_booked: 'mint',
  no_answer: 'amber', busy: 'amber', missed: 'amber', pending: 'amber', scheduled: 'amber',
  to_contact_later: 'amber', in_doubt: 'amber', inquiry: 'indigo', calling: 'indigo', dialing: 'indigo',
  failed: 'rose', declined: 'rose', rejected: 'rose', cancelled: 'rose', down: 'rose', dismissed: 'neutral',
  not_called: 'neutral', not_classified: 'neutral',
  // priority
  high: 'rose', medium: 'amber', low: 'indigo',
  // sentiment / lead
  positive: 'mint', hot: 'rose', warm: 'amber', cold: 'indigo', negative: 'rose', neutral: 'neutral',
  healthy: 'mint', degraded: 'amber',
}

export function StatusBadge({ value }: { value?: string | null }) {
  if (!value) return <span className="text-dim">—</span>
  const key = String(value).toLowerCase()
  const tone = STATUS_TONE[key] ?? 'neutral'
  return <Badge tone={tone}>{String(value).replace(/_/g, ' ')}</Badge>
}

/* ------------------------------------------------------------------ Search input */

export function SearchInput({
  value, onChange, placeholder = 'Search…', className,
}: { value: string; onChange: (v: string) => void; placeholder?: string; className?: string }) {
  return (
    <div className={cx('relative', className)}>
      <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-xl bg-[var(--surface-2)] py-2 pl-9 pr-3 text-[13px] text-[var(--text)] outline-none ring-1 ring-[var(--border)] transition placeholder:text-dim focus:ring-mint-400/40"
      />
    </div>
  )
}

/* ------------------------------------------------------------------ Form inputs */

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input {...props}
      className={cx('w-full rounded-xl bg-[var(--surface-2)] px-3 py-2 text-[13px] text-[var(--text)] outline-none ring-1 ring-[var(--border)] transition placeholder:text-dim focus:ring-mint-400/40', props.className)} />
  )
}
export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea {...props}
      className={cx('w-full rounded-xl bg-[var(--surface-2)] px-3 py-2 text-[13px] text-[var(--text)] outline-none ring-1 ring-[var(--border)] transition placeholder:text-dim focus:ring-mint-400/40', props.className)} />
  )
}
export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props}
      className={cx('rounded-xl bg-[var(--surface-2)] px-3 py-2 text-[13px] font-medium text-[var(--text)] outline-none ring-1 ring-[var(--border)] transition focus:ring-mint-400/40', props.className)} />
  )
}
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12px] font-semibold text-soft">{label}</span>
      {children}
    </label>
  )
}

/* ------------------------------------------------------------------ Segmented control */

export function Segmented<T extends string>({
  options, value, onChange,
}: { options: { value: T; label: string }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-xl bg-[var(--surface-2)] p-1 ring-1 ring-[var(--border)]">
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange(o.value)}
          className={cx('rounded-lg px-3 py-1.5 text-[12px] font-semibold transition',
            value === o.value ? 'bg-[var(--surface-solid)] text-[var(--text)] shadow-sm' : 'text-dim hover:text-soft')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ Horizontal bar list (analytics) */

export function BarList({
  items, color = 'var(--accent)', empty = 'No data',
}: { items: { label: string; value: number }[]; color?: string; empty?: string }) {
  if (!items.length) return <div className="py-6 text-center text-[13px] text-dim">{empty}</div>
  const max = Math.max(...items.map((i) => i.value), 1)
  return (
    <div className="space-y-2.5">
      {items.map((it, i) => (
        <div key={i}>
          <div className="mb-1 flex items-center justify-between gap-2 text-[12px]">
            <span className="truncate text-soft">{it.label}</span>
            <span className="shrink-0 font-semibold tabular-nums text-dim">{it.value.toLocaleString('en-IN')}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]">
            <div className="h-full rounded-full transition-all" style={{ width: `${(it.value / max) * 100}%`, background: color }} />
          </div>
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ Async boundary */

export function AsyncState({
  loading, error, empty, emptyLabel = 'Nothing here yet', children,
}: { loading?: boolean; error?: unknown; empty?: boolean; emptyLabel?: string; children: ReactNode }) {
  if (loading) {
    return (
      <div className="space-y-2 py-2">
        {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-11 w-full" />)}
      </div>
    )
  }
  if (error) {
    return <div className="rounded-xl bg-rose-500/10 px-4 py-3 text-[13px] font-medium text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">Failed to load: {String((error as Error)?.message ?? error)}</div>
  }
  if (empty) {
    return <div className="py-12 text-center text-[13px] text-dim">{emptyLabel}</div>
  }
  return <>{children}</>
}
