import { useGet, useRefresh } from '../api/hooks/useApi'
import { Card, Badge, Button, EmptyState, SkeletonBlock, cx } from '../components/ui'
import { PageHeader, Table, THead, TH, TR, TD, AsyncState, StatusBadge } from '../components/kit'
import { StatCard } from '../components/StatCard'
import {
  HeartPulse, Plug, Mic2, Volume2, BrainCircuit, MonitorSmartphone,
  RefreshCw, CheckCircle2, XCircle, AlertTriangle, HelpCircle, ServerOff,
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
  '9004': 'TCP audio return → FreeSWITCH',
}

function fmtChecked(v?: string): string {
  if (!v) return '—'
  const d = new Date(v)
  if (isNaN(d.getTime())) return String(v)
  return d.toLocaleString('en-IN')
}

function isStale(v?: string): boolean {
  if (!v) return false
  const d = new Date(v)
  if (isNaN(d.getTime())) return false
  return Date.now() - d.getTime() > 60_000
}

type InfraState = 'up' | 'down' | 'unmonitored'

const CHIP_TONE: Record<InfraState, string> = {
  up: 'bg-mint-400/15 text-mint-600 dark:text-mint-300',
  down: 'bg-rose-500/12 text-rose-500 dark:text-rose-300',
  unmonitored: 'bg-[var(--surface-2)] text-soft',
}

function HealthSkeleton() {
  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-3">
        {[0, 1, 2].map((i) => <SkeletonBlock key={i} h={116} className="rounded-2xl" />)}
      </div>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((i) => <SkeletonBlock key={i} h={64} className="rounded-2xl" />)}
      </div>
      <div className="mt-6 grid gap-4 lg:grid-cols-2 items-start">
        <SkeletonBlock h={200} className="rounded-2xl" />
        <SkeletonBlock h={200} className="rounded-2xl" />
      </div>
    </div>
  )
}

export default function Health() {
  const refreshFn = useRefresh()
  const refresh = () => refreshFn(['runtime-status'])

  const { data, isLoading, isFetching, error } = useGet<RuntimeStatus>(
    ['runtime-status'], '/runtime/status', { refetchInterval: 10000 },
  )

  const services = Object.entries(data?.services ?? {})
  const ports = Object.entries(data?.ports ?? {})

  const overall = data?.overall ?? ''
  const isHealthy = overall === 'healthy'
  const isDegraded = overall === 'degraded'
  const stale = isStale(data?.checked_at)

  const servicesRunning = services.filter(([, s]) => s?.running === true).length
  const portsListening = ports.filter(([, ok]) => ok === true).length

  const issues = [
    ...services.filter(([, s]) => s?.running !== true).map(([n]) => `${n} stopped`),
    ...ports.filter(([, ok]) => !ok).map(([p]) => `port ${p} not listening`),
  ]

  // AI pipeline: any service whose key includes ws_audio / pipeline and is running
  const pipelineUp = services.some(
    ([name, s]) => /ws_audio|pipeline/i.test(name) && s?.running === true,
  )
  const eslUp = data?.ports?.['9000'] === true

  const infra: { icon: React.ReactNode; name: string; state: InfraState; status: React.ReactNode }[] = [
    {
      icon: <Plug size={16} />, name: 'FreeSWITCH ESL Bridge', state: eslUp ? 'up' : 'down',
      status: eslUp
        ? <Badge tone="mint"><CheckCircle2 size={12} /> Up</Badge>
        : <Badge tone="rose"><XCircle size={12} /> Down</Badge>,
    },
    {
      icon: <Mic2 size={16} />, name: 'AI Pipeline', state: pipelineUp ? 'up' : 'down',
      status: pipelineUp
        ? <Badge tone="mint"><CheckCircle2 size={12} /> Up</Badge>
        : <Badge tone="rose"><XCircle size={12} /> Down</Badge>,
    },
    {
      icon: <Mic2 size={16} />, name: 'STT Provider', state: 'unmonitored',
      status: (
        <span title="This provider has no health check wired up yet">
          <Badge tone="neutral"><HelpCircle size={12} /> Not monitored</Badge>
        </span>
      ),
    },
    {
      icon: <Volume2 size={16} />, name: 'TTS Provider', state: 'unmonitored',
      status: (
        <span title="This provider has no health check wired up yet">
          <Badge tone="neutral"><HelpCircle size={12} /> Not monitored</Badge>
        </span>
      ),
    },
    {
      icon: <BrainCircuit size={16} />, name: 'LLM', state: 'unmonitored',
      status: (
        <span title="This provider has no health check wired up yet">
          <Badge tone="neutral"><HelpCircle size={12} /> Not monitored</Badge>
        </span>
      ),
    },
    {
      icon: <MonitorSmartphone size={16} />, name: 'Dashboard', state: 'up',
      status: <Badge tone="mint"><CheckCircle2 size={12} /> Online</Badge>,
    },
  ]

  return (
    <div className="space-y-6">
      <PageHeader
        title="Health"
        sub="Service & infrastructure status"
        actions={
          <>
            {overall && <StatusBadge value={overall} />}
            <Button variant="ghost" disabled={isFetching} onClick={refresh}>
              <RefreshCw size={14} className={cx(isFetching && 'animate-spin')} /> Refresh
            </Button>
          </>
        }
      />

      {isLoading ? <HealthSkeleton /> : (
      <AsyncState loading={false} error={error}>
        {/* Top row */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Card hover className={cx('flex flex-col justify-between p-5 animate-fade-up sm:col-span-2 lg:col-span-1', isDegraded && 'ring-1 ring-amber-400/40')}>
            <div className="eyebrow">Overall</div>
            <div className="mt-2 flex flex-wrap items-center gap-2" role="status" aria-live="polite">
              <HeartPulse
                size={26}
                aria-hidden="true"
                className={cx(isHealthy ? 'text-mint-500' : isDegraded ? 'text-amber-500' : 'text-dim')}
              />
              <span className={cx(
                'metric inline-flex min-w-0 items-center gap-2 text-[30px] leading-none',
                isHealthy ? 'text-mint-600 dark:text-mint-300'
                  : isDegraded ? 'text-amber-600 dark:text-amber-300'
                  : 'text-soft',
              )}>
                {isHealthy ? <CheckCircle2 size={20} aria-hidden="true" /> : isDegraded ? <AlertTriangle size={20} aria-hidden="true" /> : null}
                {isHealthy ? 'Healthy' : isDegraded ? 'Degraded' : (overall || 'Unknown')}
              </span>
            </div>
            {!isHealthy && issues.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {issues.map((i) => <Badge key={i} tone="rose" soft>{i}</Badge>)}
              </div>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-dim">
              <span>Checked: {fmtChecked(data?.checked_at)}</span>
              <span className="inline-flex items-center gap-1.5">
                <span className={cx('h-1.5 w-1.5 rounded-full animate-pulse-soft', stale ? 'bg-amber-400' : 'bg-mint-400')} />
                {stale ? 'Live · refreshes every 10s · stale' : 'Live · refreshes every 10s'}
              </span>
            </div>
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
            <Card key={item.name} className={cx('flex items-center justify-between gap-3 p-4', item.state === 'unmonitored' && 'opacity-70')}>
              <div className="flex items-center gap-2.5 min-w-0">
                <div className={cx('grid h-9 w-9 shrink-0 place-items-center rounded-xl', CHIP_TONE[item.state])}>
                  {item.icon}
                </div>
                <div className="min-w-0">
                  <div className="truncate text-[13px] font-semibold text-soft">{item.name}</div>
                  {item.state === 'unmonitored' && (
                    <div className="truncate text-[11px] text-dim">No health probe yet — not an outage</div>
                  )}
                </div>
              </div>
              <div className="shrink-0">{item.status}</div>
            </Card>
          ))}
        </div>

        {/* Runtime detail + Port status */}
        <div className="mt-6 grid gap-4 lg:grid-cols-2 items-start">
          <Card className="p-5">
            <div className="eyebrow mb-3">Runtime Detail</div>
            <Table>
              <THead>
                <TH>Service</TH>
                <TH>PID</TH>
                <TH>Status</TH>
              </THead>
              <tbody>
                {services.length === 0 ? (
                  <TR>
                    <TD colSpan={3}>
                      <EmptyState
                        icon={<ServerOff size={20} />}
                        title="No services reported"
                        hint="The runtime status probe returned nothing — the runtime may be stopped. Try Refresh, or check the lescall-runtime service."
                        action={<Button size="sm" variant="soft" className="min-h-9 sm:min-h-0" onClick={refresh}><RefreshCw size={13} /> Refresh</Button>}
                      />
                    </TD>
                  </TR>
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

          <Card className="p-5">
            <div className="eyebrow mb-3">Port Status</div>
            <Table>
              <THead>
                <TH>Port</TH>
                <TH>Description</TH>
                <TH>Status</TH>
              </THead>
              <tbody>
                {ports.length === 0 ? (
                  <TR>
                    <TD colSpan={3}>
                      <EmptyState
                        icon={<Plug size={20} />}
                        title="No ports reported"
                        hint="The runtime status probe returned nothing — the runtime may be stopped. Try Refresh, or check the lescall-runtime service."
                        action={<Button size="sm" variant="soft" className="min-h-9 sm:min-h-0" onClick={refresh}><RefreshCw size={13} /> Refresh</Button>}
                      />
                    </TD>
                  </TR>
                ) : ports.map(([port, ok]) => (
                  <TR key={port}>
                    <TD className="font-medium tabular-nums text-[var(--text)]">{port}</TD>
                    <TD>{PORT_DESCRIPTIONS[port] ?? <span className="italic text-dim">Unknown service</span>}</TD>
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
        </div>
      </AsyncState>
      )}
    </div>
  )
}
