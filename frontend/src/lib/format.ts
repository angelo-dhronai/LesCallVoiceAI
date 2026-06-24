export const inr = (n: number | null | undefined, dp = 0): string => {
  if (n == null || isNaN(Number(n))) return '₹0'
  const v = Number(n)
  return '₹' + v.toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp })
}

export const num = (n: number | null | undefined): string => {
  if (n == null || isNaN(Number(n))) return '0'
  return Number(n).toLocaleString('en-IN')
}

export const compact = (n: number | null | undefined): string => {
  if (n == null) return '0'
  return Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(Number(n))
}

export const dur = (sec: number | null | undefined): string => {
  if (!sec || sec < 0) return '0:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export const pct = (n: number | null | undefined, dp = 0): string => {
  if (n == null) return '0%'
  return `${Number(n).toFixed(dp)}%`
}

export const clockTime = (d = new Date()): string =>
  d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
