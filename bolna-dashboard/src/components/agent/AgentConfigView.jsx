import { useEffect, useMemo, useState } from 'react'
import { agents } from '../../data/mockData.js'
import AgentList from './AgentList.jsx'
import CostBreakdownBar from './CostBreakdownBar.jsx'
import PromptCanvas from './PromptCanvas.jsx'

const TABS = ['Agent', 'LLM', 'Audio', 'Engine', 'Call', 'Tools', 'Analytics', 'Inbound']

// ── Small shared field primitives ──────────────────────────────────────────
function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-gray-700 dark:text-gray-200">
        {label}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-gray-400 dark:text-gray-500">{hint}</span>}
    </label>
  )
}

function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-4"
    >
      <span className="text-sm text-gray-700 dark:text-gray-200">{label}</span>
      <span
        className={[
          'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors',
          checked ? 'bg-accent' : 'bg-gray-300 dark:bg-white/15',
        ].join(' ')}
      >
        <span
          className={[
            'inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform',
            checked ? 'translate-x-4' : 'translate-x-0.5',
          ].join(' ')}
        />
      </span>
    </button>
  )
}

// ── Agent tab (the default, fully built) ────────────────────────────────────
function AgentTab({ agent }) {
  const [welcome, setWelcome] = useState(agent.welcomeMessage)
  const [delay, setDelay] = useState(agent.welcomeDelaySeconds)

  // Re-seed editable fields whenever the selected agent changes.
  useEffect(() => {
    setWelcome(agent.welcomeMessage)
    setDelay(agent.welcomeDelaySeconds)
  }, [agent])

  const languages = useMemo(() => {
    const pills = [{ label: agent.language, primary: true }]
    if (!agent.language.toLowerCase().includes('english')) {
      pills.push({ label: 'English' })
    }
    return pills
  }, [agent])

  return (
    <div className="space-y-6">
      <CostBreakdownBar breakdown={agent.costPerMinBreakdown} />

      <div className="card p-5">
        <h3 className="section-label mb-3">Welcome Message</h3>
        <textarea
          value={welcome}
          onChange={(e) => setWelcome(e.target.value)}
          rows={3}
          className="input resize-y"
          placeholder="What the agent says first…"
        />
        <div className="mt-4">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
              Welcome delay
            </span>
            <span className="badge-blue">{delay}s</span>
          </div>
          <input
            type="range"
            min={0}
            max={5}
            step={1}
            value={delay}
            onChange={(e) => setDelay(Number(e.target.value))}
            className="w-full accent-accent"
          />
        </div>
      </div>

      <PromptCanvas prompt={agent.prompt} languages={languages} />
    </div>
  )
}

// ── Secondary tabs ──────────────────────────────────────────────────────────
function LLMTab() {
  const [model, setModel] = useState('GPT-4o')
  const [temp, setTemp] = useState(0.7)
  const [maxTokens, setMaxTokens] = useState(512)
  return (
    <div className="card space-y-5 p-5">
      <h3 className="section-label">Language Model</h3>
      <Field label="Model">
        <select value={model} onChange={(e) => setModel(e.target.value)} className="input">
          <option>GPT-4o</option>
          <option>Claude</option>
          <option>Llama</option>
        </select>
      </Field>
      <Field label={`Temperature — ${temp.toFixed(1)}`} hint="Higher values make output more creative.">
        <input
          type="range"
          min={0}
          max={1}
          step={0.1}
          value={temp}
          onChange={(e) => setTemp(Number(e.target.value))}
          className="w-full accent-accent"
        />
      </Field>
      <Field label="Max tokens">
        <input
          type="number"
          min={1}
          value={maxTokens}
          onChange={(e) => setMaxTokens(Number(e.target.value))}
          className="input"
        />
      </Field>
    </div>
  )
}

function AudioTab() {
  const [provider, setProvider] = useState('ElevenLabs · Aria')
  const [speed, setSpeed] = useState(1)
  const [interruptible, setInterruptible] = useState(true)
  return (
    <div className="card space-y-5 p-5">
      <h3 className="section-label">Audio &amp; Voice</h3>
      <Field label="Voice provider">
        <select value={provider} onChange={(e) => setProvider(e.target.value)} className="input">
          <option>ElevenLabs · Aria</option>
          <option>OpenAI · Alloy</option>
          <option>Sarvam · Shubh</option>
        </select>
      </Field>
      <Field label={`Speaking speed — ${speed.toFixed(2)}x`}>
        <input
          type="range"
          min={0.5}
          max={1.5}
          step={0.05}
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="w-full accent-accent"
        />
      </Field>
      <Toggle checked={interruptible} onChange={setInterruptible} label="Interruptible (barge-in)" />
    </div>
  )
}

function EngineTab() {
  const [mode, setMode] = useState('balanced')
  const [endpointing, setEndpointing] = useState(400)
  const modes = [
    { id: 'fast', label: 'Fast' },
    { id: 'balanced', label: 'Balanced' },
    { id: 'quality', label: 'Quality' },
  ]
  return (
    <div className="card space-y-5 p-5">
      <h3 className="section-label">Engine</h3>
      <div>
        <span className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-200">
          Latency mode
        </span>
        <div className="flex flex-wrap gap-2">
          {modes.map((m) => (
            <label
              key={m.id}
              className={[
                'flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors',
                mode === m.id
                  ? 'border-accent bg-accent-soft text-accent dark:bg-accent/15 dark:text-blue-300'
                  : 'border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-white/10 dark:text-gray-300 dark:hover:bg-white/5',
              ].join(' ')}
            >
              <input
                type="radio"
                name="latency-mode"
                value={m.id}
                checked={mode === m.id}
                onChange={() => setMode(m.id)}
                className="accent-accent"
              />
              {m.label}
            </label>
          ))}
        </div>
      </div>
      <Field label={`Endpointing — ${endpointing}ms`} hint="Silence before the agent considers the turn complete.">
        <input
          type="range"
          min={100}
          max={1000}
          step={50}
          value={endpointing}
          onChange={(e) => setEndpointing(Number(e.target.value))}
          className="w-full accent-accent"
        />
      </Field>
    </div>
  )
}

function CallTab() {
  const [maxDuration, setMaxDuration] = useState(600)
  const [hangupOnSilence, setHangupOnSilence] = useState(true)
  return (
    <div className="card space-y-5 p-5">
      <h3 className="section-label">Call Behavior</h3>
      <Field label="Max call duration (seconds)">
        <input
          type="number"
          min={30}
          value={maxDuration}
          onChange={(e) => setMaxDuration(Number(e.target.value))}
          className="input"
        />
      </Field>
      <Toggle checked={hangupOnSilence} onChange={setHangupOnSilence} label="Hang up on prolonged silence" />
    </div>
  )
}

function ToolsTab() {
  const [tools, setTools] = useState([
    { id: 'lookup_order', label: 'lookup_order', desc: 'Fetch order status by ID', enabled: true },
    { id: 'create_ticket', label: 'create_ticket', desc: 'Open a support ticket', enabled: true },
    { id: 'transfer_human', label: 'transfer_to_human', desc: 'Escalate to a live agent', enabled: false },
  ])
  const toggle = (id) =>
    setTools((prev) => prev.map((t) => (t.id === id ? { ...t, enabled: !t.enabled } : t)))
  return (
    <div className="card space-y-3 p-5">
      <h3 className="section-label">Function Tools</h3>
      {tools.map((t) => (
        <div
          key={t.id}
          className="flex items-center justify-between gap-4 rounded-lg border border-gray-200 px-3 py-2.5 dark:border-white/10"
        >
          <div className="min-w-0">
            <div className="truncate font-mono text-sm text-gray-800 dark:text-gray-100">{t.label}</div>
            <div className="truncate text-xs text-gray-400 dark:text-gray-500">{t.desc}</div>
          </div>
          <Toggle checked={t.enabled} onChange={() => toggle(t.id)} label="" />
        </div>
      ))}
    </div>
  )
}

function AnalyticsTab() {
  const tiles = [
    { label: 'Total Calls', value: '1,284' },
    { label: 'Avg Duration', value: '46s' },
    { label: 'Intent Resolution', value: '88%' },
  ]
  return (
    <div className="card p-5">
      <h3 className="section-label mb-4">Analytics</h3>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {tiles.map((t) => (
          <div
            key={t.label}
            className="rounded-lg border border-gray-200 p-4 dark:border-white/10"
          >
            <div className="metric-number">{t.value}</div>
            <div className="mt-1 text-xs text-gray-400 dark:text-gray-500">{t.label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function InboundTab() {
  const [number, setNumber] = useState('+1 415 555 0142')
  const [webhook, setWebhook] = useState('https://hooks.dhron.ai/inbound')
  return (
    <div className="card space-y-5 p-5">
      <h3 className="section-label">Inbound Routing</h3>
      <Field label="Assigned phone number">
        <select value={number} onChange={(e) => setNumber(e.target.value)} className="input">
          <option>+1 415 555 0142</option>
          <option>+1 628 555 0199</option>
          <option>+91 80 3133 9140</option>
        </select>
      </Field>
      <Field label="Webhook URL" hint="POSTed on each inbound call event.">
        <input
          type="url"
          value={webhook}
          onChange={(e) => setWebhook(e.target.value)}
          className="input"
        />
      </Field>
    </div>
  )
}

// ── Top-level view ──────────────────────────────────────────────────────────
function AgentConfigView() {
  const [selectedId, setSelectedId] = useState(agents[0]?.id)
  const [activeTab, setActiveTab] = useState('Agent')

  const agent = useMemo(
    () => agents.find((a) => a.id === selectedId) ?? agents[0],
    [selectedId],
  )

  const renderTab = () => {
    switch (activeTab) {
      case 'Agent':
        // Key forces editable state to re-seed cleanly on agent switch.
        return <AgentTab key={agent.id} agent={agent} />
      case 'LLM':
        return <LLMTab />
      case 'Audio':
        return <AudioTab />
      case 'Engine':
        return <EngineTab />
      case 'Call':
        return <CallTab />
      case 'Tools':
        return <ToolsTab />
      case 'Analytics':
        return <AnalyticsTab />
      case 'Inbound':
        return <InboundTab />
      default:
        return null
    }
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* Left inner rail */}
      <aside className="card w-full shrink-0 overflow-hidden lg:h-[calc(100vh-8rem)] lg:w-72">
        <div className="border-b border-gray-200 px-4 py-3 dark:border-white/10">
          <h2 className="page-title text-base">Agents</h2>
        </div>
        <div className="h-[22rem] lg:h-[calc(100%-3.25rem)]">
          <AgentList agents={agents} selectedId={agent.id} onSelect={setSelectedId} />
        </div>
      </aside>

      {/* Center panel */}
      <section className="min-w-0 flex-1">
        <div className="mb-5">
          <h1 className="page-title">{agent.name}</h1>
          <p className="mt-0.5 text-sm text-gray-400 dark:text-gray-500">
            {agent.language} · {agent.status === 'active' ? 'Active' : 'Draft'}
          </p>
        </div>

        {/* Sub-nav tabs */}
        <div className="mb-5 flex flex-wrap gap-1 border-b border-gray-200 dark:border-white/10">
          {TABS.map((tab) => {
            const isActive = tab === activeTab
            return (
              <button
                key={tab}
                type="button"
                onClick={() => setActiveTab(tab)}
                className={[
                  '-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'border-accent text-accent dark:text-blue-300'
                    : 'border-transparent text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100',
                ].join(' ')}
              >
                {tab}
              </button>
            )
          })}
        </div>

        {renderTab()}
      </section>
    </div>
  )
}

export default AgentConfigView
