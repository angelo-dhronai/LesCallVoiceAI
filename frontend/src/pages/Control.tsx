import { useEffect, useRef, useState } from 'react'
import { Play, Square, RotateCw, Trash2, Server } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { post } from '../api/mutate'
import { useGet } from '../api/hooks/useApi'
import { useSSE } from '../api/useSSE'
import { useToast } from '../components/Toast'
import { Card, Badge, Button, cx } from '../components/ui'
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

export default function Control() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data, isLoading, error } = useGet<RuntimeStatus>(
    ['runtime-status'],
    '/runtime/status',
    { refetchInterval: 3000 },
  )

  const [lines, setLines] = useState<Line[]>([])
  const scrollRef = useRef<HTMLDivElement | null>(null)

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
    if (el) el.scrollTop = el.scrollHeight
  }, [lines])

  const cmdRunning = !!data?.cmd_running

  const runAction = async (action: 'start' | 'stop' | 'restart') => {
    try {
      const res = await post<{ ok: boolean; reason?: string; msg?: string }>(`/runtime/${action}`)
      toast(res?.msg || res?.reason || `${action} triggered`, res?.ok ? 'success' : 'warn')
    } catch (e) {
      toast(String((e as Error)?.message ?? e), 'error')
    } finally {
      qc.invalidateQueries({ queryKey: ['runtime-status'] })
    }
  }

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

  return (
    <div className="space-y-6">
      <PageHeader title="Runtime Control" sub="Start, stop and monitor the calling agent" />

      <AsyncState loading={isLoading} error={error}>
        {/* Agent Status Card */}
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span
                className="inline-block h-3 w-3 shrink-0 rounded-full"
                style={{ background: dotColor, boxShadow: `0 0 10px ${dotColor}` }}
              />
              <div className="flex items-center gap-2">
                <Server size={18} className="text-dim" />
                <span className="text-[14px] font-semibold text-[var(--text)]">{statusLabel}</span>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary" disabled={cmdRunning} onClick={() => runAction('start')}>
                <Play size={15} /> Start
              </Button>
              <Button variant="danger" disabled={cmdRunning} onClick={() => runAction('stop')}>
                <Square size={15} /> Stop
              </Button>
              <Button variant="ghost" disabled={cmdRunning} onClick={() => runAction('restart')}>
                <RotateCw size={15} /> Restart
              </Button>
            </div>
          </div>
        </Card>

        {/* Quick Info Card */}
        <Card className="p-5">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.1em] text-dim">
                ESL Port 9000
              </div>
              {portBadge('9000')}
            </div>
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.1em] text-dim">
                Media WS 9001
              </div>
              {portBadge('9001')}
            </div>
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.1em] text-dim">
                Logs Dir
              </div>
              <div className="break-all text-[12px] font-medium text-soft">
                {data?.logs_dir || '—'}
              </div>
            </div>
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.1em] text-dim">
                Last Command
              </div>
              <div className="text-[12px] font-medium text-soft">
                {data?.cmd_last || '—'}
                {data?.cmd_exit != null && (
                  <span className="ml-1 text-dim">(exit {data.cmd_exit})</span>
                )}
              </div>
            </div>
          </div>
        </Card>

        {/* Terminal Card */}
        <Card className="overflow-hidden p-0">
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-2.5">
            <div className="flex items-center gap-2">
              <span className="inline-block h-3 w-3 rounded-full bg-rose-500/80" />
              <span className="inline-block h-3 w-3 rounded-full bg-amber-400/80" />
              <span className="inline-block h-3 w-3 rounded-full bg-mint-400/80" />
              <span className="ml-2 flex items-center gap-1.5 text-[12px] font-medium text-dim">
                <span
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
          <div
            ref={scrollRef}
            className="h-[48vh] overflow-y-auto whitespace-pre-wrap rounded-xl bg-[#0b0f1a] p-4 font-mono text-[12px]"
          >
            {lines.length === 0 ? (
              <div className="text-[#586079]">Waiting for output…</div>
            ) : (
              lines.map((l, i) => (
                <div key={i} style={{ color: l.color }} className={cx('leading-relaxed')}>
                  {l.text}
                </div>
              ))
            )}
          </div>
        </Card>
      </AsyncState>
    </div>
  )
}
