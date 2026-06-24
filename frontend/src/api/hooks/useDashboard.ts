import { useQuery } from '@tanstack/react-query'
import { api } from '../client'
import { demoHealth, demoOverview } from '../demo'

/** Overview metrics — maps the real /api/overview payload onto the shape the
 *  Overview page renders; falls back to demo data when the backend is unreachable. */
export function useOverview() {
  const q = useQuery({
    queryKey: ['overview'],
    queryFn: () => api<any>('/overview'),
    refetchInterval: 30_000,
  })
  const data = q.data ? normalizeOverview(q.data) : demoOverview
  return { ...q, data, isDemo: !q.data }
}

// Real backend period buckets use call_count / est_*_cost / stt_sarvam_billed_seconds.
function bucket(b: any) {
  b = b || {}
  return {
    calls: b.call_count ?? 0,
    cost: b.est_total_cost ?? 0,
    stt_cost: b.est_stt_cost ?? 0,
    tts_cost: b.est_tts_cost ?? 0,
    stt_seconds: b.stt_sarvam_billed_seconds ?? 0,
    tts_chars: b.tts_chars ?? 0,
  }
}

function normalizeOverview(raw: any) {
  const k = raw.kpis || {}
  // Build sparkline series from the real calls-over-time chart (fallback: hourly).
  const series: number[] =
    (raw.charts?.calls_over_time || []).map((c: any) => c.count ?? 0)
    || []
  const hourly: number[] = (raw.hourly_calls || []).map((h: any) => h.count ?? 0)
  const spark = series.length ? series : hourly.length ? hourly : demoOverview.spark.calls
  return {
    today: { ...bucket(raw.today), trend: undefined as number | undefined },
    week: bucket(raw.week),
    month: bucket(raw.month),
    totals: {
      calls: k.total_calls ?? 0,
      inbound: k.inbound_calls ?? 0,
      outbound: k.outbound_calls ?? 0,
      minutes_used: k.minutes_used ?? 0,
      agents: 1,
      campaigns: 1,
      leads: k.follow_ups_required ?? 0,
    },
    kpis: {
      total: k.total_calls ?? 0,
      answered: k.answered_calls ?? 0,
      missed: k.missed_calls ?? 0,
      inbound: k.inbound_calls ?? 0,
      outbound: k.outbound_calls ?? 0,
      appointments: k.appointments_booked ?? 0,
      followups: k.follow_ups_required ?? 0,
      minutes_used: k.minutes_used ?? 0,
    },
    spark: { calls: spark, inbound: spark, minutes: spark, today: hourly.length ? hourly : spark },
    calls_7d: series.length ? series : demoOverview.calls_7d,
    runtime: (raw.runtime_status as 'healthy' | 'degraded') ?? 'healthy',
  }
}

export function useHealth() {
  const q = useQuery({
    queryKey: ['health'],
    queryFn: () => api<any>('/health'),
    refetchInterval: 20_000,
  })
  // /health is a runtime snapshot (no active-call count); poll that separately.
  const live = useQuery({
    queryKey: ['active-count'],
    queryFn: () => api<any>('/calls/active'),
    refetchInterval: 10_000,
  })
  const liveCount = live.data?.count ?? live.data?.active_calls?.length ?? 0
  if (!q.data) return { ...demoHealth, liveCount }
  const services = normalizeServices(q.data.services)
  const overall = (q.data.overall as 'healthy' | 'degraded' | 'down') ?? demoHealth.overall
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
