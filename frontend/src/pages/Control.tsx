import { useEffect, useRef, useState } from 'react'
import { Play, Square, RotateCw, Trash2, Server, TerminalSquare } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { post } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useSSE } from '../api/useSSE'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, Spinner, SkeletonBlock, cx } from '../components/ui'
import { PageHeader, AsyncState } from '../components/kit'

type Service = { pid: string; running: boolean }
type RuntimeStatus = {
  services: Record<string, Service>
  ports: Record<string, boolean>
  overall: 'healthy' | 'degraded'
  checked_at: string
  logs_dir: string
  cmd_running: boolean
  cmd_last: string
  cmd_exit: number | null
}

const MAX_LINES = 800

type Line = { text: string; color: string }

function classify(text: string, isError: boolean): string {
  if (isError || /\[FAIL\]|error/i.test(text)) return '#fb7185' // rose
  if (/\[OK\]|success/i.test(text)) return '#34d399' // mint
  return '#9aa4bd'
}

function relativeTime(iso?: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const s = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
}

type Action = 'start' | 'stop' | 'restart'

export default function Control() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data, isLoading, error } = useGet<RuntimeStatus>(
    ['runtime-status'],
    '/runtime/status',
    { refetchInterval: 3000 },
  )

  const [lines, setLines] = useState<Line[]>([])
  const [showJump, setShowJump] = useState(false)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const stickRef = useRef(true)

  const pushLines = (next: Line[]) => {
    setLines((prev) => {
      const merged = [...prev, ...next]
      return merged.length > MAX_LINES ? merged.slice(merged.length - MAX_LINES) : merged
    })
  }

  const sseStatus = useSSE('/runtime/output/stream', (ev: any) => {
    if (!ev || typeof ev !== 'object') return
    switch (ev.type) {
      case 'cmd_start':
        pushLines([{ text: `$ ${ev.cmd ?? ''}`, color: '#9aa4bd' }])
        break
      case 'line': {
        const t = String(ev.text ?? '')
        pushLines([{ text: t, color: classify(t, false) }])
        break
      }
      case 'error': {
        const t = String(ev.text ?? '')
        pushLines([{ text: t, color: classify(t, true) }])
        break
      }
      case 'cmd_end':
        pushLines([{ text: `— exit ${ev.exit_code ?? '?'}`, color: '#9aa4bd' }])
        break
      default:
        break
    }
  }, [])

  useEffect(() => {
    const el = scrollRef.current
    if (!el || lines.length === 0) return
    if (stickRef.current) {
      el.scrollTop = el.scrollHeight
    } else {
      setShowJump(true)
    }
  }, [lines])

  const onTerminalScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48
    stickRef.current = atBottom
    if (atBottom) setShowJump(false)
  }

  const jumpToBottom = () => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
    stickRef.current = true
    setShowJump(false)
  }

  const cmdRunning = !!data?.cmd_running

  const [pending, setPending] = useState<Action | null>(null)
  const [confirming, setConfirming] = useState<'stop' | 'restart' | null>(null)
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current)
  }, [])

  const runAction = async (action: Action) => {
    setPending(action)
    try {
      const res = await post<{ ok: boolean; reason?: string; msg?: string }>(`/runtime/${action}`)
      toast(res?.msg || res?.reason || `${action} triggered`, res?.ok ? 'success' : 'warn')
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    } finally {
      setPending(null)
      qc.invalidateQueries({ queryKey: ['runtime-status'] })
    }
  }

  const requestDestructive = (action: 'stop' | 'restart') => {
    if (confirming === action) {
      if (confirmTimer.current) clearTimeout(confirmTimer.current)
      setConfirming(null)
      void runAction(action)
      return
    }
    if (confirmTimer.current) clearTimeout(confirmTimer.current)
    setConfirming(action)
    confirmTimer.current = setTimeout(() => setConfirming(null), 4000)
  }

  const busy = !!pending || cmdRunning
  const busyTitle = busy ? 'Wait for the current command to finish' : undefined

  // Status dot + label
  let dotColor = '#9aa4bd'
  let statusLabel = 'Unknown'
  if (data) {
    if (data.cmd_running) {
      dotColor = '#6366f1'
      statusLabel = `Running: ${data.cmd_last || '…'}`
    } else if (data.overall === 'healthy') {
      dotColor = '#34d399'
      statusLabel = 'Healthy'
    } else if (data.overall === 'degraded') {
      dotColor = '#fbbf24'
      statusLabel = 'Degraded'
    }
  }

  const ports = data?.ports ?? {}
  const portBadge = (p: string) =>
    ports[p] ? <Badge tone="mint">listening</Badge> : <Badge tone="rose">down</Badge>

  const sseDotColor =
    sseStatus === 'open' ? '#34d399' : sseStatus === 'error' ? '#fb7185' : '#fbbf24'

  const services = Object.entries(data?.services ?? {})

  return (
    <div className="space-y-6">
      <PageHeader title="Runtime Control" sub="Start, stop and monitor the calling agent" />

      {isLoading ? (
        <>
          <SkeletonBlock h={84} className="rounded-2xl" />
          <SkeletonBlock h={110} className="rounded-2xl" />
          <SkeletonBlock h={420} className="rounded-2xl" />
        </>
      ) : (
      <AsyncState error={error}>
        {/* Agent Status Card */}
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-3">
              <span
                aria-hidden="true"
                className={cx('inline-block h-3 w-3 shrink-0 rounded-full', cmdRunning && 'animate-pulse-soft')}
                style={{ background: dotColor, boxShadow: `0 0 10px ${dotColor}` }}
              />
              <div className="flex min-w-0 items-center gap-2">
                <Server size={18} className="text-dim" />
                <span role="status" aria-live="polite" className="min-w-0 break-all text-[14px] font-semibold text-[var(--text)]">
                  {statusLabel}
                </span>
              </div>
              {data?.checked_at && (
                <span className="text-[11px] tabular-nums text-dim">
                  Checked {relativeTime(data.checked_at)}
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="primary"
                disabled={busy}
                title={busyTitle}
                onClick={() => runAction('start')}
              >
                {pending === 'start'
                  ? <><Spinner className="h-3.5 w-3.5" /> Starting…</>
                  : <><Play size={15} /> Start</>}
              </Button>
              <Button
                variant="danger"
                disabled={busy}
                title={busyTitle}
                className={confirming === 'stop' ? 'ring-2 ring-rose-500/50 animate-pulse-soft' : undefined}
                onClick={() => requestDestructive('stop')}
              >
                {pending === 'stop'
                  ? <><Spinner className="h-3.5 w-3.5" /> Stopping…</>
                  : confirming === 'stop'
                    ? <><Square size={15} /> Confirm stop?</>
                    : <><Square size={15} /> Stop</>}
              </Button>
              <Button
                variant="ghost"
                disabled={busy}
                title={busyTitle}
                className={confirming === 'restart' ? 'ring-2 ring-rose-500/50 animate-pulse-soft' : undefined}
                onClick={() => requestDestructive('restart')}
              >
                {pending === 'restart'
                  ? <><Spinner className="h-3.5 w-3.5" /> Restarting…</>
                  : confirming === 'restart'
                    ? <><RotateCw size={15} /> Confirm restart?</>
                    : <><RotateCw size={15} /> Restart</>}
              </Button>
            </div>
          </div>
          {services.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {services.map(([name, svc]) => (
                <Badge key={name} tone={svc.running ? 'mint' : 'rose'}>
                  {name} · {svc.running ? `pid ${svc.pid}` : 'stopped'}
                </Badge>
              ))}
            </div>
          )}
          <p className="mt-2 text-[11px] text-dim">Stop and Restart interrupt any calls in progress.</p>
        </Card>

        {/* Quick Info Card */}
        <Card className="p-5">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <div className="eyebrow mb-1">ESL Port 9000</div>
              {portBadge('9000')}
            </div>
            <div>
              <div className="eyebrow mb-1">Media WS 9001</div>
              {portBadge('9001')}
            </div>
            <div>
              <div className="eyebrow mb-1">Logs Dir</div>
              <div className="break-all text-[12px] font-medium text-soft">
                {data?.logs_dir || '—'}
              </div>
            </div>
            <div>
              <div className="eyebrow mb-1">Last Command</div>
              <div className="flex flex-wrap items-center gap-1.5 text-[12px] font-medium text-soft">
                <span className="min-w-0 break-all">{data?.cmd_last || '—'}</span>
                {data != null && data.cmd_exit != null && (
                  <Badge tone={data.cmd_exit === 0 ? 'mint' : 'rose'}>exit {data.cmd_exit}</Badge>
                )}
              </div>
            </div>
          </div>
        </Card>

        {/* Terminal Card */}
        <Card className="overflow-hidden p-0">
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-2.5">
            <div className="flex items-center gap-2">
              <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full bg-rose-500/80" />
              <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full bg-amber-400/80" />
              <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full bg-mint-400/80" />
              <span
                role="status"
                aria-live="polite"
                className="ml-2 flex items-center gap-1.5 text-[12px] font-medium text-dim"
              >
                <span
                  aria-hidden="true"
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: sseDotColor }}
                />
                {sseStatus === 'open'
                  ? 'Live'
                  : sseStatus === 'error'
                    ? 'Disconnected'
                    : 'Connecting…'}
              </span>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setLines([])}>
              <Trash2 size={14} /> Clear
            </Button>
          </div>
          {sseStatus === 'error' && (
            <div role="alert" className="bg-rose-500/10 px-4 py-1.5 text-[11px] font-medium text-rose-600 dark:text-rose-300">
              Live stream disconnected — reconnecting automatically; lines may be missing.
            </div>
          )}
          <div className="relative">
            <div
              ref={scrollRef}
              onScroll={onTerminalScroll}
              role="log"
              aria-label="Runtime command output"
              tabIndex={0}
              className="h-[48vh] overflow-y-auto whitespace-pre-wrap break-words rounded-xl bg-[#0b0f1a] p-4 font-mono text-[12px]"
            >
              {lines.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
                  <div className="grid h-11 w-11 place-items-center rounded-xl bg-white/5">
                    <TerminalSquare size={20} className="text-[#9aa4bd]" />
                  </div>
                  <div className="text-[13px] font-medium text-[#9aa4bd]">No output yet</div>
                  <div className="max-w-xs text-[12px] text-[#7c86a2]">
                    Press Start to launch the runtime — start_all.sh output streams here live.
                  </div>
                </div>
              ) : (
                lines.map((l, i) => (
                  <div key={i} style={{ color: l.color }} className={cx('leading-relaxed')}>
                    {l.text}
                  </div>
                ))
              )}
            </div>
            {showJump && (
              <button
                onClick={jumpToBottom}
                className="absolute bottom-3 right-4 min-h-[36px] rounded-full bg-[var(--surface-solid)] px-4 py-2 text-[12px] font-semibold text-soft ring-1 ring-[var(--border)] shadow-glass lg:min-h-0 lg:px-3 lg:py-1 lg:text-[11px]"
              >
                Jump to latest ↓
              </button>
            )}
          </div>
        </Card>
      </AsyncState>
      )}
    </div>
  )
}
