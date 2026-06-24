import { useQuery } from '@tanstack/react-query'
import { api } from '../client'
import { demoHealth, demoOverview } from '../demo'

/** Overview metrics — falls back to demo data when the backend is unreachable. */
export function useOverview() {
  const q = useQuery({
    queryKey: ['overview'],
    queryFn: () => api<any>('/overview'),
    refetchInterval: 30_000,
  })
  // Map backend payload onto the demo shape; on error use demo wholesale.
  const data = q.data ? { ...demoOverview, ...normalizeOverview(q.data) } : demoOverview
  return { ...q, data, isDemo: !q.data }
}

function normalizeOverview(raw: any) {
  // Backend shape is flexible; we defensively pick known fields and keep demo
  // sparklines (backend doesn't return per-metric series).
  try {
    return {
      today: { ...demoOverview.today, ...(raw.today || {}) },
      month: { ...demoOverview.month, ...(raw.month || {}) },
      totals: { ...demoOverview.totals, ...(raw.totals || {}) },
      kpis: { ...demoOverview.kpis, ...(raw.kpis || {}) },
    }
  } catch {
    return {}
  }
}

export function useHealth() {
  const q = useQuery({
    queryKey: ['health'],
    queryFn: () => api<any>('/health'),
    refetchInterval: 20_000,
  })
  if (!q.data) return demoHealth
  const services = normalizeServices(q.data.services)
  const overall = (q.data.overall as 'healthy' | 'degraded' | 'down') ?? demoHealth.overall
  const liveCount = q.data.active_calls ?? q.data.live_count ?? demoHealth.liveCount
  return { services, overall, liveCount }
}

/**
 * Backend /api/health returns `services` as an object keyed by name
 * ({ ws_audio_receiver: { running: true }, ... }), but older builds expected an
 * array. Accept either shape so a single unexpected payload can't blank the app.
 */
function normalizeServices(raw: any) {
  if (Array.isArray(raw)) {
    return raw.map((s: any) => ({ name: s.name ?? s.service, ok: !!(s.ok ?? s.running) }))
  }
  if (raw && typeof raw === 'object') {
    return Object.entries(raw).map(([name, v]: [string, any]) => ({
      name,
      ok: !!(v?.ok ?? v?.running),
    }))
  }
  return demoHealth.services
}
