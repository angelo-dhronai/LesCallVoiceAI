import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Wallet, KeyRound, RefreshCw, AlertTriangle } from 'lucide-react'
import { post, del } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, cx } from '../components/ui'
import { PageHeader, Table, THead, TH, TR, TD, Input, Textarea, Field, Segmented, AsyncState } from '../components/kit'
import { StatCard } from '../components/StatCard'
import { RadialRing } from '../components/charts'
import { inr, num } from '../lib/format'

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

type Period = 'today' | 'week' | 'month'

function fmtTime(s?: string): string {
  if (!s) return '—'
  const d = new Date(s)
  if (isNaN(d.getTime())) return String(s)
  return d.toLocaleString('en-IN')
}

export default function Usage() {
  const qc = useQueryClient()
  const toast = useToast()
  const [period, setPeriod] = useState<Period>('today')

  const credits = useGet<Credits>(['credits'], '/sarvam/credits')
  const keys = useGet<Keys>(['keys'], '/sarvam/keys')
  const usage = useGet<Usage>(['usage', period], `/sarvam/usage?period=${period}`)

  const [remainInput, setRemainInput] = useState('')
  const [replaceKey, setReplaceKey] = useState('')
  const [bulkKeys, setBulkKeys] = useState('')

  const c = credits.data ?? {}
  const k = keys.data ?? {}
  const u = usage.data ?? {}
  const summary = u.summary ?? {}
  const daily = Object.entries(u.daily ?? {}).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  const topCalls = u.top_calls ?? []

  const pctUsed = c.percent_used ?? 0
  const ringColor = pctUsed > 90 ? '#fb7185' : pctUsed > 70 ? '#f59e0b' : 'var(--accent)'

  async function saveCredits() {
    const v = Number(remainInput)
    if (!remainInput || isNaN(v)) {
      toast('Enter a valid number', 'warn')
      return
    }
    try {
      await post('/sarvam/credits', { remaining_credits: v })
      toast('Credits updated', 'success')
      setRemainInput('')
      qc.invalidateQueries({ queryKey: ['credits'] })
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    }
  }

  async function doReplaceKey() {
    if (!replaceKey.trim()) {
      toast('Enter an API key', 'warn')
      return
    }
    try {
      await post('/sarvam/keys/replace', { api_key: replaceKey.trim() })
      toast('API key replaced', 'success')
      setReplaceKey('')
      qc.invalidateQueries({ queryKey: ['keys'] })
      qc.invalidateQueries({ queryKey: ['credits'] })
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    }
  }

  async function addBulk() {
    const list = bulkKeys.split('\n').map((s) => s.trim()).filter(Boolean)
    if (!list.length) {
      toast('Enter at least one key', 'warn')
      return
    }
    try {
      await post('/sarvam/keys/bulk', { keys: list })
      toast(`Added ${list.length} key${list.length === 1 ? '' : 's'}`, 'success')
      setBulkKeys('')
      qc.invalidateQueries({ queryKey: ['keys'] })
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    }
  }

  async function clearPool() {
    if (!window.confirm('Clear the entire key pool? This cannot be undone.')) return
    try {
      await del('/sarvam/keys/pool')
      toast('Key pool cleared', 'success')
      qc.invalidateQueries({ queryKey: ['keys'] })
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="API Usage" sub="Sarvam STT/TTS credits & usage" />

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Credits Card */}
        <Card className="p-5">
          <div className="mb-4 flex items-center gap-2">
            <Wallet size={16} className="text-mint-500" />
            <h2 className="font-display text-base font-bold tracking-tight">Credits</h2>
          </div>
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
                  value={remainInput}
                  onChange={(e) => setRemainInput(e.target.value)}
                  placeholder="e.g. 5000"
                />
                <Button variant="ghost" onClick={saveCredits}>Sync</Button>
              </div>
            </Field>
          </div>
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
                    value={replaceKey}
                    onChange={(e) => setReplaceKey(e.target.value)}
                    placeholder="sk_..."
                  />
                  <Button variant="ghost" onClick={doReplaceKey}>Replace</Button>
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
              <div className="mt-2 flex items-center justify-between gap-2">
                <Button variant="soft" size="sm" onClick={addBulk}>Add to pool</Button>
                <Button variant="danger" size="sm" onClick={clearPool}>Clear pool</Button>
              </div>
            </div>
          </AsyncState>
        </Card>
      </div>

      {/* Usage section */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-lg font-bold tracking-tight">Usage</h2>
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

      <AsyncState loading={usage.isLoading} error={usage.error}>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Calls" value={num(summary.call_count)} />
          <StatCard label="STT Cost" value={inr(summary.est_stt_cost, 2)} />
          <StatCard label="TTS Cost" value={inr(summary.est_tts_cost, 2)} />
          <StatCard label="Total Cost" value={inr(summary.est_total_cost, 2)} iconTone="mint" />
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
                    <TD className="font-medium text-soft">{date}</TD>
                    <TD className="text-right tabular-nums">{num(row.call_count)}</TD>
                    <TD className="text-right tabular-nums">{num(row.stt_sarvam_billed_seconds)}</TD>
                    <TD className="text-right tabular-nums">{num(row.tts_chars)}</TD>
                    <TD className="text-right tabular-nums">{inr(row.est_stt_cost, 2)}</TD>
                    <TD className="text-right tabular-nums">{inr(row.est_tts_cost, 2)}</TD>
                    <TD className="text-right font-semibold tabular-nums">{inr(row.est_total_cost, 2)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          ) : (
            <div className="py-8 text-center text-[13px] text-dim">No usage in this period</div>
          )}
        </Card>

        <Card className="p-5">
          <h3 className="mb-3 font-display text-base font-bold tracking-tight">Top Calls</h3>
          {topCalls.length ? (
            <Table>
              <THead>
                <TH>Call ID</TH>
                <TH className="text-right">TTS ₹</TH>
                <TH className="text-right">Total ₹</TH>
              </THead>
              <tbody>
                {topCalls.map((tc, i) => (
                  <TR key={tc.call_id ?? i}>
                    <TD className="font-mono text-[12px] text-soft">{tc.call_id ?? '—'}</TD>
                    <TD className="text-right tabular-nums">{inr(tc.est_tts_cost, 2)}</TD>
                    <TD className="text-right font-semibold tabular-nums">{inr(tc.est_total_cost, 2)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          ) : (
            <div className="py-8 text-center text-[13px] text-dim">No calls in this period</div>
          )}
        </Card>

        <p className={cx('text-[12px] text-dim')}>
          STT ₹0.5/min · TTS ₹3.0/1K chars — estimates
        </p>
      </AsyncState>
    </div>
  )
}
