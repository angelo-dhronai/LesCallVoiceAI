import { useRef, useState } from 'react'
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { Wallet, KeyRound, RefreshCw, AlertTriangle, Phone, Mic, AudioLines, BarChart3, PhoneOff, Copy, Timer, Clock, History } from 'lucide-react'
import { api } from '../api/client'
import { post, del } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useMe } from '../api/hooks/useMe'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, Spinner, EmptyState, SectionHead, cx } from '../components/ui'
import { PageHeader, Table, THead, TH, TR, TD, Input, Textarea, Field, Segmented, AsyncState } from '../components/kit'
import { StatCard } from '../components/StatCard'
import { RadialRing } from '../components/charts'
import { inr, num, dur } from '../lib/format'

type Credits = {
  total_credits?: number
  remaining_credits?: number
  used_credits?: number
  percent_used?: number
  updated_at?: string
  low?: boolean
  exhausted?: boolean
  needs_new_key?: boolean
  pool_count?: number
  restart_required?: boolean
  auto_rotated?: boolean
}

type Keys = {
  active_key_masked?: string
  pool?: string[]
  pool_count?: number
  restart_required?: boolean
  last_changed_at?: string
}

type DailyRow = {
  call_count?: number
  est_stt_cost?: number
  est_tts_cost?: number
  est_total_cost?: number
  stt_sarvam_billed_seconds?: number
  tts_chars?: number
}

type TopCall = {
  call_id?: string
  est_total_cost?: number
  est_tts_cost?: number
  est_stt_cost?: number
  duration_seconds?: number
}

type Usage = {
  period?: string
  start?: string
  end?: string
  summary?: DailyRow
  daily?: Record<string, DailyRow>
  top_calls?: TopCall[]
}

type UsageDay = { date: string; seconds: number; calls: number }

type RecentCall = {
  conn_id?: string
  answered_at?: string
  phone?: string
  agent_slug?: string
  direction?: string
  seconds?: number
  status?: string // open | closed | reconciled
  source?: string | null // cdr | pipeline | backfill | manual | null
}

type UsageMinutes = {
  owner?: string
  month?: string
  seconds_month?: number
  minutes_month?: number
  calls_month?: number
  seconds_today?: number
  seconds_total?: number
  rounding?: string
  unreconciled?: number
  per_day?: UsageDay[]
  recent?: RecentCall[]
}

type Period = 'today' | 'week' | 'month'

function fmtTime(s?: string): string {
  if (!s) return '—'
  const d = new Date(s)
  if (isNaN(d.getTime())) return String(s)
  return d.toLocaleString('en-IN')
}

function fmtDay(s: string): string {
  const d = new Date(s)
  if (isNaN(d.getTime())) return s
  return d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' })
}

function fmtRangeDate(s?: string): string {
  if (!s) return '—'
  const d = new Date(s)
  if (isNaN(d.getTime())) return String(s)
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

/** Compact date + time for the recent-calls table. */
function fmtWhen(s?: string): string {
  if (!s) return '—'
  const d = new Date(s)
  if (isNaN(d.getTime())) return String(s)
  return d.toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** h:mm from seconds — for lifetime totals. */
function hmm(sec?: number | null): string {
  const s = Math.max(0, Math.floor(sec ?? 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}:${m.toString().padStart(2, '0')}`
}

/** Call reconciliation status → badge. */
function StatusTag({ status }: { status?: string }) {
  if (status === 'reconciled') return <Badge tone="mint">verified</Badge>
  if (status === 'closed') return <Badge tone="amber">pending</Badge>
  if (status === 'open') return <Badge tone="indigo">live</Badge>
  return <Badge tone="neutral">{status || '—'}</Badge>
}

export default function Usage() {
  const qc = useQueryClient()
  const toast = useToast()
  const [period, setPeriod] = useState<Period>('today')

  const { data: me } = useMe()
  const isAdmin = me?.is_admin !== false

  const minutes = useGet<UsageMinutes>(['usage-minutes'], '/usage/minutes', { refetchInterval: 60000 })

  const credits = useGet<Credits>(['credits'], '/sarvam/credits', { enabled: isAdmin })
  const keys = useGet<Keys>(['keys'], '/sarvam/keys', { enabled: isAdmin })
  const usage = useQuery<Usage>({
    queryKey: ['usage', period],
    queryFn: () => api<Usage>(`/sarvam/usage?period=${period}`),
    placeholderData: keepPreviousData,
    enabled: isAdmin,
  })

  const [remainInput, setRemainInput] = useState('')
  const [replaceKey, setReplaceKey] = useState('')
  const [bulkKeys, setBulkKeys] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const m = minutes.data ?? {}
  const perDay = (m.per_day ?? []).slice(-30)
  const recent = m.recent ?? []
  const maxDaySec = perDay.reduce((mx, d) => Math.max(mx, d.seconds ?? 0), 60)

  const c = credits.data ?? {}
  const k = keys.data ?? {}
  const u = usage.data ?? {}
  const summary = u.summary ?? {}
  const daily = Object.entries(u.daily ?? {}).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  const topCalls = u.top_calls ?? []
  const maxDayTotal = daily.reduce((mx, [, row]) => Math.max(mx, row.est_total_cost ?? 0), 0)
  const isRefreshing = usage.isFetching && !usage.isLoading
  const perCallCost =
    (summary.call_count ?? 0) > 0 && summary.est_total_cost != null
      ? `≈ ${inr(summary.est_total_cost / (summary.call_count as number), 2)} / call`
      : undefined
  const creditsHealthy =
    credits.data != null && !c.exhausted && !c.low && !c.needs_new_key && !c.restart_required

  const pctUsed = c.percent_used ?? 0
  const ringColor = pctUsed > 90 ? '#fb7185' : pctUsed > 70 ? '#f59e0b' : 'var(--accent)'

  function refreshAll() {
    qc.invalidateQueries({ queryKey: ['usage-minutes'] })
    qc.invalidateQueries({ queryKey: ['credits'] })
    qc.invalidateQueries({ queryKey: ['keys'] })
    qc.invalidateQueries({ queryKey: ['usage'] })
  }

  async function copyCallId(id?: string) {
    if (!id) return
    try {
      await navigator.clipboard.writeText(id)
      toast('Call ID copied', 'success')
    } catch {
      toast('Could not copy to clipboard', 'error')
    }
  }

  async function saveCredits() {
    const v = Number(remainInput)
    if (!remainInput || isNaN(v)) {
      toast('Enter a valid number', 'warn')
      return
    }
    setPending('sync')
    try {
      await post('/sarvam/credits', { remaining_credits: v })
      toast('Credits updated', 'success')
      setRemainInput('')
      qc.invalidateQueries({ queryKey: ['credits'] })
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    } finally {
      setPending(null)
    }
  }

  async function doReplaceKey() {
    if (!replaceKey.trim()) {
      toast('Enter an API key', 'warn')
      return
    }
    setPending('replace')
    try {
      await post('/sarvam/keys/replace', { api_key: replaceKey.trim() })
      toast('API key replaced', 'success')
      setReplaceKey('')
      qc.invalidateQueries({ queryKey: ['keys'] })
      qc.invalidateQueries({ queryKey: ['credits'] })
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    } finally {
      setPending(null)
    }
  }

  async function addBulk() {
    const list = bulkKeys.split('\n').map((s) => s.trim()).filter(Boolean)
    if (!list.length) {
      toast('Enter at least one key', 'warn')
      return
    }
    setPending('bulk')
    try {
      await post('/sarvam/keys/bulk', { keys: list })
      toast(`Added ${list.length} key${list.length === 1 ? '' : 's'}`, 'success')
      setBulkKeys('')
      qc.invalidateQueries({ queryKey: ['keys'] })
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    } finally {
      setPending(null)
    }
  }

  async function clearPool() {
    if (!confirmClear) {
      setConfirmClear(true)
      if (clearTimer.current) clearTimeout(clearTimer.current)
      clearTimer.current = setTimeout(() => setConfirmClear(false), 3000)
      return
    }
    if (clearTimer.current) clearTimeout(clearTimer.current)
    setConfirmClear(false)
    setPending('clear')
    try {
      await del('/sarvam/keys/pool')
      toast('Key pool cleared', 'success')
      qc.invalidateQueries({ queryKey: ['keys'] })
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    } finally {
      setPending(null)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="API Usage"
        sub="Call minutes used by your agents — the number your billing is based on"
        actions={
          <Button variant="ghost" onClick={refreshAll}>
            <RefreshCw size={14} /> Refresh
          </Button>
        }
      />

      {/* ------------------------------------------------ Call minutes (everyone) */}
      <AsyncState loading={minutes.isLoading} error={minutes.error}>
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard label="Minutes this month" value={num(m.minutes_month)} icon={<Timer size={18} />} iconTone="mint" sub={m.month} />
          <StatCard label="Calls this month" value={num(m.calls_month)} icon={<Phone size={18} />} />
          <StatCard label="Today" value={dur(m.seconds_today)} icon={<Clock size={18} />} sub="min:sec" />
          <StatCard label="All time" value={hmm(m.seconds_total)} icon={<History size={18} />} sub="hrs:min" />
        </div>

        <div className="grid gap-4 lg:grid-cols-5">
          <Card className="p-5 lg:col-span-2">
            <h3 className="mb-3 font-display text-base font-bold tracking-tight">Minutes per day</h3>
            {perDay.length ? (
              <div className="space-y-2.5">
                {perDay.map((d) => (
                  <div key={d.date}>
                    <div className="mb-1 flex items-center justify-between gap-2 text-[12px]">
                      <span title={`${d.date} — ${num(d.calls)} call${d.calls === 1 ? '' : 's'}`} className="truncate text-soft">
                        {fmtDay(d.date)}
                      </span>
                      <span className="shrink-0 font-semibold tabular-nums text-dim">{num(Math.round((d.seconds ?? 0) / 60))} min</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]">
                      <div
                        className="h-full rounded-full bg-mint-500/60 transition-all"
                        style={{ width: `${Math.max(2, ((d.seconds ?? 0) / maxDaySec) * 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                icon={<BarChart3 size={20} />}
                title="No calls yet this month"
                hint="Per-day minutes appear here once your agents take calls."
              />
            )}
          </Card>

          <Card className="p-5 lg:col-span-3">
            <h3 className="mb-3 font-display text-base font-bold tracking-tight">Recent calls</h3>
            {recent.length ? (
              <Table>
                <THead>
                  <TH>When</TH>
                  <TH>Number</TH>
                  <TH>Agent</TH>
                  <TH>Direction</TH>
                  <TH className="text-right">Duration</TH>
                  <TH>Status</TH>
                </THead>
                <tbody>
                  {recent.map((r, i) => (
                    <TR key={r.conn_id ?? i}>
                      <TD className="whitespace-nowrap text-soft">{fmtWhen(r.answered_at)}</TD>
                      <TD className="font-mono text-[12px] tabular-nums">{r.phone || '—'}</TD>
                      <TD className="text-soft">{r.agent_slug || '—'}</TD>
                      <TD className="capitalize text-dim">{r.direction || '—'}</TD>
                      <TD className="text-right tabular-nums">{dur(r.seconds)}</TD>
                      <TD><StatusTag status={r.status} /></TD>
                    </TR>
                  ))}
                </tbody>
              </Table>
            ) : (
              <EmptyState
                icon={<PhoneOff size={20} />}
                title="No calls yet"
                hint="Your most recent calls and their durations will show here."
              />
            )}
            {(m.unreconciled ?? 0) > 0 && (
              <p className="mt-3 text-[11px] text-dim">
                {num(m.unreconciled)} call{m.unreconciled === 1 ? '' : 's'} awaiting CDR verification — numbers may adjust slightly.
              </p>
            )}
          </Card>
        </div>
      </AsyncState>

      {/* ------------------------------------------------ Platform costs (admin only) */}
      {isAdmin && (
        <>
          <SectionHead title="Platform costs (admin)" sub="Sarvam STT/TTS credits, API keys & estimated spend" />

          <div className="grid gap-4 lg:grid-cols-2">
            {/* Credits Card */}
            <Card className="p-5">
              <div className="mb-4 flex items-center gap-2">
                <Wallet size={16} className="text-mint-500" />
                <h2 className="font-display text-base font-bold tracking-tight">Credits</h2>
              </div>

              <AsyncState loading={credits.isLoading} error={credits.error}>
                <div className="flex items-center gap-5">
                  <RadialRing value={pctUsed} max={100} size={92} color={ringColor} label={`${Math.round(pctUsed)}%`} />
                  <div className="min-w-0 space-y-1.5 text-[13px]">
                    <div className="text-soft">
                      <span className="metric text-[20px]">{num(c.remaining_credits)}</span>
                      <span className="text-dim"> / {num(c.total_credits)} remaining</span>
                    </div>
                    <div className="text-dim">Used: {num(c.used_credits)}</div>
                    <div className="text-dim">Updated: {fmtTime(c.updated_at)}</div>
                    {c.pool_count != null && <div className="text-dim">Pool: {num(c.pool_count)} keys</div>}
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-2">
                  {creditsHealthy && <Badge tone="mint">Healthy</Badge>}
                  {c.restart_required && (
                    <Badge tone="amber"><AlertTriangle size={12} /> Restart required</Badge>
                  )}
                  {c.exhausted && <Badge tone="rose">Exhausted</Badge>}
                  {c.low && !c.exhausted && <Badge tone="amber">Low balance</Badge>}
                  {c.needs_new_key && <Badge tone="rose">Needs new key</Badge>}
                </div>
                {c.auto_rotated ? (
                  <div className="mt-2 flex items-center gap-1.5 text-[12px] text-indigo-500 dark:text-indigo-300">
                    <RefreshCw size={13} /> Key was auto-rotated from the pool.
                  </div>
                ) : null}

                <div className="mt-4 border-t border-[var(--border)] pt-4">
                  <Field label="Set remaining credits">
                    <div className="flex gap-2">
                      <Input
                        type="number"
                        className="min-w-0 flex-1"
                        value={remainInput}
                        onChange={(e) => setRemainInput(e.target.value)}
                        placeholder="e.g. 5000"
                      />
                      <Button variant="ghost" className="shrink-0" onClick={saveCredits} disabled={pending === 'sync'}>
                        {pending === 'sync' ? <><Spinner className="h-3 w-3" /> Saving…</> : 'Sync'}
                      </Button>
                    </div>
                  </Field>
                  <p className="mt-1 text-[11px] text-dim">
                    Manually sync with the balance shown in the Sarvam dashboard when auto-tracking drifts.
                  </p>
                </div>
              </AsyncState>
            </Card>

            {/* API Keys Card */}
            <Card className="p-5">
              <div className="mb-4 flex items-center gap-2">
                <KeyRound size={16} className="text-indigo-400" />
                <h2 className="font-display text-base font-bold tracking-tight">API Keys</h2>
              </div>

              <AsyncState loading={keys.isLoading} error={keys.error}>
                <div className="space-y-1.5 text-[13px]">
                  <div className="text-dim">Active key</div>
                  <div className="font-mono text-[12px] text-soft break-all">{k.active_key_masked ?? '—'}</div>
                  <div className="text-dim">
                    Pool: <span className="font-semibold text-soft">{num(k.pool_count)}</span> keys
                  </div>
                  {(k.pool?.length ?? 0) > 0 && (
                    <details>
                      <summary className="cursor-pointer text-[12px] text-indigo-600 dark:text-indigo-400">
                        Show pool keys
                      </summary>
                      <div className="mt-1.5 max-h-32 space-y-0.5 overflow-y-auto rounded-lg surface-2 p-2">
                        {(k.pool ?? []).map((pk, i) => (
                          <div key={i} className="font-mono text-[12px] text-dim break-all">
                            <span className="tabular-nums">{i + 1}.</span> {pk}
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                  {k.restart_required && (
                    <div className="pt-1">
                      <Badge tone="amber"><AlertTriangle size={12} /> Restart required</Badge>
                    </div>
                  )}
                  {k.last_changed_at && <div className="text-dim">Last changed: {fmtTime(k.last_changed_at)}</div>}
                </div>

                <div className="mt-4 border-t border-[var(--border)] pt-4">
                  <Field label="Replace active key">
                    <div className="flex gap-2">
                      <Input
                        className="min-w-0 flex-1"
                        value={replaceKey}
                        onChange={(e) => setReplaceKey(e.target.value)}
                        placeholder="sk_..."
                      />
                      <Button variant="ghost" className="shrink-0" onClick={doReplaceKey} disabled={pending === 'replace'}>
                        {pending === 'replace' ? <><Spinner className="h-3 w-3" /> Replacing…</> : 'Replace'}
                      </Button>
                    </div>
                  </Field>
                </div>

                <div className="mt-4">
                  <Field label="Add keys to pool (one per line)">
                    <Textarea
                      rows={3}
                      value={bulkKeys}
                      onChange={(e) => setBulkKeys(e.target.value)}
                      placeholder={'sk_aaa...\nsk_bbb...'}
                    />
                  </Field>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Button variant="soft" size="sm" onClick={addBulk} disabled={pending === 'bulk'}>
                      {pending === 'bulk' ? <><Spinner className="h-3 w-3" /> Adding…</> : 'Add to pool'}
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      className="ml-auto"
                      onClick={clearPool}
                      disabled={pending === 'clear'}
                      title="Removes every pooled key — cannot be undone"
                    >
                      {pending === 'clear'
                        ? <><Spinner className="h-3 w-3" /> Clearing…</>
                        : confirmClear
                          ? `Click again to clear ${num(k.pool_count)} keys`
                          : 'Clear pool'}
                    </Button>
                  </div>
                </div>
              </AsyncState>
            </Card>
          </div>

          {/* Usage section */}
          <SectionHead
            title="Usage"
            sub={cx(
              u.start && u.end && `${fmtRangeDate(u.start)} – ${fmtRangeDate(u.end)} · `,
              'STT ₹0.5/min · TTS ₹3.0/1K chars — estimates',
            )}
            right={
              <div className="flex items-center gap-2">
                {isRefreshing && <Spinner className="h-3.5 w-3.5" />}
                <Segmented<Period>
                  value={period}
                  onChange={setPeriod}
                  options={[
                    { value: 'today', label: 'Today' },
                    { value: 'week', label: 'Week' },
                    { value: 'month', label: 'Month' },
                  ]}
                />
              </div>
            }
          />

          <AsyncState loading={usage.isLoading} error={usage.error}>
            <div className={cx('space-y-6 transition-opacity', isRefreshing && 'opacity-60')}>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard label="Calls" value={num(summary.call_count)} icon={<Phone size={18} />} />
                <StatCard label="STT Cost" value={inr(summary.est_stt_cost, 2)} icon={<Mic size={18} />} />
                <StatCard label="TTS Cost" value={inr(summary.est_tts_cost, 2)} icon={<AudioLines size={18} />} />
                <StatCard label="Total Cost" value={inr(summary.est_total_cost, 2)} icon={<Wallet size={18} />} iconTone="mint" sub={perCallCost} />
              </div>

              <Card className="p-5">
                <h3 className="mb-3 font-display text-base font-bold tracking-tight">Daily Breakdown</h3>
                {daily.length ? (
                  <Table>
                    <THead>
                      <TH>Date</TH>
                      <TH className="text-right">Calls</TH>
                      <TH className="text-right">STT (s)</TH>
                      <TH className="text-right">TTS chars</TH>
                      <TH className="text-right">STT ₹</TH>
                      <TH className="text-right">TTS ₹</TH>
                      <TH className="text-right">Total ₹</TH>
                    </THead>
                    <tbody>
                      {daily.map(([date, row]) => (
                        <TR key={date}>
                          <TD className="font-medium text-soft" title={date}>{fmtDay(date)}</TD>
                          <TD className="text-right tabular-nums">{num(row.call_count)}</TD>
                          <TD className="text-right tabular-nums">{num(row.stt_sarvam_billed_seconds)}</TD>
                          <TD className="text-right tabular-nums">{num(row.tts_chars)}</TD>
                          <TD className="text-right tabular-nums">{inr(row.est_stt_cost, 2)}</TD>
                          <TD className="text-right tabular-nums">{inr(row.est_tts_cost, 2)}</TD>
                          <TD className="text-right font-semibold tabular-nums">
                            {inr(row.est_total_cost, 2)}
                            <div
                              className="ml-auto mt-1 h-1 rounded-full bg-mint-500/60"
                              style={{ width: maxDayTotal > 0 ? `${Math.max(4, ((row.est_total_cost ?? 0) / maxDayTotal) * 100)}%` : '0%' }}
                            />
                          </TD>
                        </TR>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr className="border-t border-[var(--border-strong)]">
                        <TD className="font-semibold text-soft">Total</TD>
                        <TD className="text-right font-semibold tabular-nums">{num(summary.call_count)}</TD>
                        <TD className="text-right font-semibold tabular-nums">{num(summary.stt_sarvam_billed_seconds)}</TD>
                        <TD className="text-right font-semibold tabular-nums">{num(summary.tts_chars)}</TD>
                        <TD className="text-right font-semibold tabular-nums">{inr(summary.est_stt_cost, 2)}</TD>
                        <TD className="text-right font-semibold tabular-nums">{inr(summary.est_tts_cost, 2)}</TD>
                        <TD className="text-right font-semibold tabular-nums">{inr(summary.est_total_cost, 2)}</TD>
                      </tr>
                    </tfoot>
                  </Table>
                ) : (
                  <EmptyState
                    icon={<BarChart3 size={20} />}
                    title="No usage in this period"
                    hint="Costs appear here once your agents take calls — try Week or Month above."
                    action={
                      period !== 'month' ? (
                        <Button variant="soft" size="sm" onClick={() => setPeriod('month')}>View month</Button>
                      ) : undefined
                    }
                  />
                )}
              </Card>

              <Card className="p-5">
                <h3 className="mb-3 font-display text-base font-bold tracking-tight">Top Calls</h3>
                {topCalls.length ? (
                  <Table>
                    <THead>
                      <TH>#</TH>
                      <TH>Call ID</TH>
                      <TH className="text-right">Duration</TH>
                      <TH className="text-right">STT ₹</TH>
                      <TH className="text-right">TTS ₹</TH>
                      <TH className="text-right">Total ₹</TH>
                    </THead>
                    <tbody>
                      {topCalls.map((tc, i) => (
                        <TR key={tc.call_id ?? i}>
                          <TD className="text-dim tabular-nums">#{i + 1}</TD>
                          <TD>
                            {tc.call_id ? (
                              <button
                                type="button"
                                title={`${tc.call_id} — click to copy`}
                                aria-label={`Copy call ID ${tc.call_id}`}
                                onClick={() => copyCallId(tc.call_id)}
                                className="group inline-flex max-w-[180px] items-center gap-1.5 py-2 -my-2 font-mono text-[12px] text-soft transition-colors hover:text-[var(--text)]"
                              >
                                <span className="truncate">{tc.call_id}</span>
                                <Copy size={12} className="shrink-0 text-dim opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-visible:opacity-100" />
                              </button>
                            ) : (
                              <span className="font-mono text-[12px] text-soft">—</span>
                            )}
                          </TD>
                          <TD className="text-right tabular-nums">{dur(tc.duration_seconds)}</TD>
                          <TD className="text-right tabular-nums">{inr(tc.est_stt_cost, 2)}</TD>
                          <TD className="text-right tabular-nums">{inr(tc.est_tts_cost, 2)}</TD>
                          <TD className="text-right font-semibold tabular-nums">{inr(tc.est_total_cost, 2)}</TD>
                        </TR>
                      ))}
                    </tbody>
                  </Table>
                ) : (
                  <EmptyState
                    icon={<PhoneOff size={20} />}
                    title="No calls in this period"
                    hint="The most expensive calls in this window will rank here."
                  />
                )}
              </Card>
            </div>
          </AsyncState>
        </>
      )}
    </div>
  )
}
