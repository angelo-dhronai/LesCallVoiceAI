import { useGet, useRefresh } from '../api/hooks/useApi'
import { Card, Badge, Button, cx } from '../components/ui'
import { PageHeader, Table, THead, TH, TR, TD, AsyncState } from '../components/kit'
import { StatCard } from '../components/StatCard'
import {
  HeartPulse, Plug, Mic2, Volume2, BrainCircuit, MonitorSmartphone,
  RefreshCw, CheckCircle2, XCircle,
} from 'lucide-react'

type ServiceInfo = { pid?: string; running?: boolean }
type RuntimeStatus = {
  services?: Record<string, ServiceInfo | null>
  ports?: Record<string, boolean>
  overall?: 'healthy' | 'degraded' | string
  checked_at?: string
}

const PORT_DESCRIPTIONS: Record<string, string> = {
  '9000': 'socket_server.py — ESL',
  '9001': 'ws_audio_receiver.py — Media WS',
}

function fmtChecked(v?: string): string {
  if (!v) return '—'
  const d = new Date(v)
  if (isNaN(d.getTime())) return String(v)
  return d.toLocaleString('en-IN')
}

export default function Health() {
  const refreshFn = useRefresh()
  const refresh = () => refreshFn(['runtime-status'])

  const { data, isLoading, error } = useGet<RuntimeStatus>(
    ['runtime-status'], '/runtime/status', { refetchInterval: 10000 },
  )

  const services = Object.entries(data?.services ?? {})
  const ports = Object.entries(data?.ports ?? {})

  const overall = data?.overall ?? ''
  const isHealthy = overall === 'healthy'
  const isDegraded = overall === 'degraded'

  const servicesRunning = services.filter(([, s]) => s?.running === true).length
  const portsListening = ports.filter(([, ok]) => ok === true).length

  // AI pipeline: any service whose key includes ws_audio / pipeline and is running
  const pipelineUp = services.some(
    ([name, s]) => /ws_audio|pipeline/i.test(name) && s?.running === true,
  )
  const eslUp = data?.ports?.['9000'] === true

  const infra: { icon: React.ReactNode; name: string; status: React.ReactNode }[] = [
    {
      icon: <Plug size={16} />, name: 'FreeSWITCH ESL Bridge',
      status: eslUp
        ? <Badge tone="mint"><CheckCircle2 size={12} /> Up</Badge>
        : <Badge tone="rose"><XCircle size={12} /> Down</Badge>,
    },
    {
      icon: <Mic2 size={16} />, name: 'AI Pipeline',
      status: pipelineUp
        ? <Badge tone="mint"><CheckCircle2 size={12} /> Up</Badge>
        : <Badge tone="rose"><XCircle size={12} /> Down</Badge>,
    },
    { icon: <Mic2 size={16} />, name: 'STT Provider', status: <Badge tone="neutral">Not monitored</Badge> },
    { icon: <Volume2 size={16} />, name: 'TTS Provider', status: <Badge tone="neutral">Not monitored</Badge> },
    { icon: <BrainCircuit size={16} />, name: 'LLM', status: <Badge tone="neutral">Not monitored</Badge> },
    {
      icon: <MonitorSmartphone size={16} />, name: 'Dashboard',
      status: <Badge tone="mint"><CheckCircle2 size={12} /> Online</Badge>,
    },
  ]

  return (
    <div className="space-y-6">
      <PageHeader
        title="Health"
        sub="Service & infrastructure status"
        actions={<Button variant="ghost" onClick={refresh}><RefreshCw size={14} /> Refresh</Button>}
      />

      <AsyncState loading={isLoading} error={error}>
        {/* Top row */}
        <div className="grid gap-4 sm:grid-cols-3">
          <Card className="flex flex-col justify-between p-5">
            <div className="eyebrow">Overall</div>
            <div className="mt-2 flex items-center gap-2">
              <HeartPulse
                size={26}
                className={cx(isHealthy ? 'text-mint-500' : isDegraded ? 'text-amber-500' : 'text-dim')}
              />
              <span className={cx(
                'text-[26px] font-bold leading-none',
                isHealthy ? 'text-mint-600 dark:text-mint-300'
                  : isDegraded ? 'text-amber-600 dark:text-amber-300'
                  : 'text-soft',
              )}>
                {isHealthy ? '✓ Healthy' : isDegraded ? '⚠ Degraded' : (overall || 'Unknown')}
              </span>
            </div>
            <div className="mt-2 text-[12px] text-dim">Checked: {fmtChecked(data?.checked_at)}</div>
          </Card>

          <StatCard
            label="Services Running"
            value={`${servicesRunning} / ${services.length}`}
            icon={<CheckCircle2 size={18} />}
            iconTone="mint"
          />
          <StatCard
            label="Ports Listening"
            value={`${portsListening} / ${ports.length}`}
            icon={<Plug size={18} />}
            iconTone="indigo"
          />
        </div>

        {/* Infrastructure status */}
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {infra.map((item) => (
            <Card key={item.name} className="flex items-center justify-between gap-3 p-4">
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--surface-2)] text-soft">
                  {item.icon}
                </div>
                <span className="truncate text-[13px] font-semibold text-soft">{item.name}</span>
              </div>
              {item.status}
            </Card>
          ))}
        </div>

        {/* Runtime detail */}
        <Card className="mt-6 p-5">
          <div className="eyebrow mb-3">Runtime Detail</div>
          <Table>
            <THead>
              <TH>Service</TH>
              <TH>PID</TH>
              <TH>Status</TH>
            </THead>
            <tbody>
              {services.length === 0 ? (
                <TR><TD colSpan={3} className="text-dim">No services reported</TD></TR>
              ) : services.map(([name, s]) => (
                <TR key={name}>
                  <TD className="font-medium text-[var(--text)]">{name}</TD>
                  <TD className="tabular-nums">{s?.pid ?? '—'}</TD>
                  <TD>
                    {s?.running
                      ? <Badge tone="mint"><CheckCircle2 size={12} /> Running</Badge>
                      : <Badge tone="rose"><XCircle size={12} /> Stopped</Badge>}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>

        {/* Port status */}
        <Card className="mt-6 p-5">
          <div className="eyebrow mb-3">Port Status</div>
          <Table>
            <THead>
              <TH>Port</TH>
              <TH>Description</TH>
              <TH>Status</TH>
            </THead>
            <tbody>
              {ports.length === 0 ? (
                <TR><TD colSpan={3} className="text-dim">No ports reported</TD></TR>
              ) : ports.map(([port, ok]) => (
                <TR key={port}>
                  <TD className="font-medium tabular-nums text-[var(--text)]">{port}</TD>
                  <TD>{PORT_DESCRIPTIONS[port] ?? '—'}</TD>
                  <TD>
                    {ok
                      ? <Badge tone="mint"><CheckCircle2 size={12} /> Listening</Badge>
                      : <Badge tone="rose"><XCircle size={12} /> Not listening</Badge>}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      </AsyncState>
    </div>
  )
}
