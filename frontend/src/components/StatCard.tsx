import type { ReactNode } from 'react'
import { Card, cx } from './ui'
import { Sparkline, RadialRing } from './charts'
import { ArrowUpRight, ArrowDownRight } from 'lucide-react'

type Props = {
  label: string
  value: ReactNode
  sub?: ReactNode
  icon?: ReactNode
  iconTone?: 'mint' | 'indigo' | 'amber' | 'rose'
  spark?: number[]
  sparkColor?: string
  radial?: { value: number; max?: number; label?: string; color?: string }
  trend?: number
  size?: 'sm' | 'lg'
  className?: string
}

const toneBg: Record<string, string> = {
  mint: 'bg-mint-400/15 text-mint-600 dark:text-mint-300',
  indigo: 'bg-indigo-500/12 text-indigo-500 dark:text-indigo-300',
  amber: 'bg-amber-400/15 text-amber-600 dark:text-amber-300',
  rose: 'bg-rose-500/12 text-rose-500 dark:text-rose-300',
}

export function StatCard({ label, value, sub, icon, iconTone = 'indigo', spark, sparkColor, radial, trend, size = 'sm', className }: Props) {
  return (
    <Card hover className={cx('flex flex-col p-5 animate-fade-up', size === 'lg' && 'p-6', className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="eyebrow truncate">{label}</div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className={cx('metric leading-none', size === 'lg' ? 'text-[40px]' : 'text-[30px]')}>{value}</span>
            {trend != null && (
              <span className={cx('inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] font-bold',
                trend >= 0 ? 'text-mint-600 bg-mint-400/15' : 'text-rose-500 bg-rose-500/12')}>
                {trend >= 0 ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}{Math.abs(trend)}%
              </span>
            )}
          </div>
          {sub && <div className="mt-1.5 text-[12px] text-dim">{sub}</div>}
        </div>
        {radial ? (
          <RadialRing value={radial.value} max={radial.max} label={radial.label} color={radial.color} size={size === 'lg' ? 76 : 60} />
        ) : icon ? (
          <div className={cx('grid h-10 w-10 shrink-0 place-items-center rounded-xl', toneBg[iconTone])}>{icon}</div>
        ) : null}
      </div>
      {spark && (
        <div className="-mx-1 mt-3">
          <Sparkline data={spark} color={sparkColor ?? 'var(--accent)'} height={size === 'lg' ? 56 : 40} />
        </div>
      )}
    </Card>
  )
}
