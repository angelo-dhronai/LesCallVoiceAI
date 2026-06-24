import { Card } from '../components/ui'
import { Sparkles } from 'lucide-react'

export default function Placeholder({ title, sub }: { title: string; sub?: string }) {
  return (
    <Card className="flex flex-col items-center justify-center gap-3 p-16 text-center">
      <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-mint-400/20 to-indigo-500/20 text-indigo-500 dark:text-indigo-300">
        <Sparkles size={26} />
      </div>
      <h2 className="font-display text-xl font-bold tracking-tight">{title}</h2>
      <p className="max-w-md text-[13px] text-dim">
        {sub ?? 'This page is part of the full 17-page build and will carry the same premium design system you see on the Overview — glass cards, mint + indigo accents, micro-charts, and modern tables.'}
      </p>
    </Card>
  )
}
