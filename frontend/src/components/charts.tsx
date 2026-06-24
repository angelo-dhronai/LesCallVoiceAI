import { useId } from 'react'

/** Inline area sparkline rendered as crisp SVG. */
export function Sparkline({
  data, color = 'var(--accent)', height = 44, fill = true, strokeWidth = 2,
}: { data: number[]; color?: string; height?: number; fill?: boolean; strokeWidth?: number }) {
  const id = useId().replace(/:/g, '')
  const w = 100
  const pts = data.length ? data : [0, 0]
  const max = Math.max(...pts), min = Math.min(...pts)
  const range = max - min || 1
  const step = w / (pts.length - 1 || 1)
  const coords = pts.map((v, i) => [i * step, height - ((v - min) / range) * (height - 6) - 3])
  const line = coords.map((c, i) => `${i === 0 ? 'M' : 'L'}${c[0].toFixed(1)},${c[1].toFixed(1)}`).join(' ')
  const area = `${line} L${w},${height} L0,${height} Z`
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      <defs>
        <linearGradient id={`sg${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {fill && <path d={area} fill={`url(#sg${id})`} />}
      <path d={line} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

/** Radial progress ring with centered label. */
export function RadialRing({
  value, max = 100, size = 64, stroke = 7, color = 'var(--accent)', label,
}: { value: number; max?: number; size?: number; stroke?: number; color?: string; label?: string }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const frac = Math.max(0, Math.min(1, value / (max || 1)))
  return (
    <div className="relative inline-grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--border-strong)" strokeWidth={stroke} opacity={0.5} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
          strokeDasharray={c} strokeDashoffset={c * (1 - frac)} strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset .8s cubic-bezier(.22,1,.36,1)' }} />
      </svg>
      <span className="absolute text-[12px] font-bold metric">{label ?? `${Math.round(frac * 100)}%`}</span>
    </div>
  )
}

/** Semi-donut (gauge) chart for sentiment splits. */
export function SemiDonut({
  segments, size = 180, thickness = 22,
}: { segments: { label: string; value: number; color: string }[]; size: number; thickness?: number }) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1
  const r = (size - thickness) / 2
  const cx = size / 2, cy = size / 2
  const circ = Math.PI * r // half circle
  let offset = 0
  return (
    <svg viewBox={`0 0 ${size} ${size / 2 + thickness / 2 + 6}`} className="w-full">
      <g transform={`rotate(180 ${cx} ${cy})`}>
        <path d={describeArc(cx, cy, r, 0, 180)} fill="none" stroke="var(--border-strong)" strokeWidth={thickness} opacity={0.4} strokeLinecap="round" />
        {segments.map((s, i) => {
          const frac = s.value / total
          const len = circ * frac
          const dash = `${len} ${circ}`
          const el = (
            <path key={i} d={describeArc(cx, cy, r, 0, 180)} fill="none" stroke={s.color}
              strokeWidth={thickness} strokeDasharray={dash} strokeDashoffset={-offset} strokeLinecap="round"
              style={{ transition: 'stroke-dashoffset .8s ease, stroke-dasharray .8s ease' }} />
          )
          offset += len
          return el
        })}
      </g>
    </svg>
  )
}

function describeArc(cx: number, cy: number, r: number, startAngle: number, endAngle: number) {
  const start = polar(cx, cy, r, endAngle)
  const end = polar(cx, cy, r, startAngle)
  const largeArc = endAngle - startAngle <= 180 ? '0' : '1'
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 0 ${end.x} ${end.y}`
}
function polar(cx: number, cy: number, r: number, deg: number) {
  const rad = ((deg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}
