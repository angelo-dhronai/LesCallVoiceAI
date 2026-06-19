import { useState, useEffect, useCallback } from 'react'
import { api } from '../../api.js'
import { agentFromApi, agentToApi } from '../../data/adapters.js'
import AgentList from './AgentList.jsx'
import CostBreakdownBar from './CostBreakdownBar.jsx'
import PromptCanvas from './PromptCanvas.jsx'
import { Save, Trash2, Bot, Loader2, AlertTriangle } from 'lucide-react'

const TABS = ['Agent', 'LLM', 'Audio', 'Engine', 'Call', 'Tools', 'Analytics', 'Inbound']

const BLANK = {
  id: null, name: 'New Agent', status: 'Draft', rawLanguage: 'auto',
  language: 'Auto-detect (Primary)', languages: ['Auto-detect (Primary)', 'English', 'Gujarati'],
  welcomeMessage: '', welcomeDelay: 0.8, prompt: '', company: '', pitch: '',
  llm: { provider: 'OpenAI', model: 'gpt-4o-mini', temperature: 0.6, maxTokens: 220 },
  audio: { transcriber: 'Sarvam saaras:v3', voice: 'Bulbul · shubh', speed: 1.0, speaker: 'shubh' },
  engine: { vad: 'Energy (self-calibrating)', interruptions: true, endpointing: 700 },
  call: { maxDuration: 600, recording: true, voicemailDetection: true },
  inbound: { number: '—', greetingOnFirstFrame: true },
  cost: { transcriber: 0.012, llm: 0.026, voice: 0.039, telephony: 0.012, platform: 0.005 },
}

function Field({ label, children }) {
  return <div><div className="label">{label}</div>{children}</div>
}

function Toggle({ on, onChange, label }) {
  return (
    <label className="flex items-center justify-between gap-4 py-2">
      <span className="text-sm text-slate-700 dark:text-slate-200">{label}</span>
      <button type="button" onClick={() => onChange(!on)}
        className={`relative h-6 w-11 rounded-full transition-colors ${on ? 'bg-accent' : 'bg-slate-300 dark:bg-slate-700'}`}>
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${on ? 'translate-x-5' : 'translate-x-0.5'}`} />
      </button>
    </label>
  )
}

function StatCard({ label, value }) {
  return (
    <div className="card-flat p-4">
      <div className="text-[11px] text-slate-400 uppercase tracking-wider">{label}</div>
      <div className="mt-1 text-xl font-bold text-slate-800 dark:text-slate-100">{value}</div>
    </div>
  )
}

export default function AgentConfigView() {
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState(null)
  const [draft, setDraft] = useState(null)
  const [tab, setTab] = useState('Agent')
  const [saving, setSaving] = useState(false)
  const [savedTick, setSavedTick] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async (selectFirst = false) => {
    setLoading(true); setError('')
    try {
      const rows = (await api.agents()).map(agentFromApi)
      setAgents(rows)
      if (selectFirst && rows.length) { setSelectedId(rows[0].id); setDraft(rows[0]) }
    } catch (e) {
      setAgents([]); setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(true) }, [load])

  const select = (id) => {
    const a = agents.find((x) => x.id === id)
    if (a) { setSelectedId(id); setDraft({ ...a }); setTab('Agent') }
  }

  const startNew = () => { setSelectedId(null); setDraft({ ...BLANK }); setTab('Agent') }

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }))
  const setEngine = (patch) => setDraft((d) => ({ ...d, engine: { ...d.engine, ...patch } }))
  const setCall = (patch) => setDraft((d) => ({ ...d, call: { ...d.call, ...patch } }))

  const save = async () => {
    if (!draft?.name?.trim()) return
    setSaving(true)
    try {
      const payload = agentToApi(draft)
      const saved = draft.id
        ? await api.updateAgent(draft.id, payload)
        : await api.createAgent(payload)
      const mapped = agentFromApi(saved)
      const rows = (await api.agents()).map(agentFromApi)
      setAgents(rows)
      setSelectedId(mapped.id); setDraft(mapped)
      setSavedTick(true); setTimeout(() => setSavedTick(false), 1800)
    } catch (e) {
      alert(`Save failed: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!draft?.id) { startNew(); return }
    if (!confirm(`Delete agent "${draft.name}"?`)) return
    await api.deleteAgent(draft.id)
    const rows = (await api.agents()).map(agentFromApi)
    setAgents(rows)
    if (rows.length) select(rows[0].id); else startNew()
  }

  if (loading) {
    return <div className="h-full grid place-items-center text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>
  }

  // Empty playground for a fresh tenant (or a load error — surface it, don't hide it).
  if (!draft && agents.length === 0) {
    return (
      <div className="h-full grid place-items-center p-10">
        <div className="text-center max-w-sm">
          <div className={`mx-auto mb-4 h-14 w-14 rounded-2xl grid place-items-center ${error ? 'bg-rose-100 dark:bg-rose-500/10' : 'bg-accent-soft dark:bg-accent/10'}`}>
            {error ? <AlertTriangle className="h-7 w-7 text-rose-500" /> : <Bot className="h-7 w-7 text-accent" />}
          </div>
          <h2 className="text-lg font-semibold text-slate-800 dark:text-slate-100">{error ? "Couldn't load agents" : 'No agents yet'}</h2>
          <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
            {error || 'This is your private workspace. Create your first voice agent to get started.'}
          </p>
          {error
            ? <button className="btn btn-outline mt-5" onClick={() => load(true)}>Retry</button>
            : <button className="btn btn-primary mt-5" onClick={startNew}>Create your first agent</button>}
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full">
      <AgentList agents={agents} selectedId={selectedId} onSelect={select} onNew={startNew} />

      <div className="flex-1 min-w-0 flex flex-col">
        <div className="px-6 pt-5 border-b border-slate-200 dark:border-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <input
                value={draft.name}
                onChange={(e) => set({ name: e.target.value })}
                className="text-lg font-bold bg-transparent text-slate-900 dark:text-white outline-none border-b border-transparent focus:border-accent w-full"
              />
              <div className="text-xs text-slate-400">{draft.id ? `Agent #${draft.id}` : 'Unsaved'} · {draft.llm.model}</div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button className="btn btn-ghost !px-2" onClick={remove} title="Delete agent"><Trash2 className="h-4 w-4" /></button>
              <button className="btn btn-primary" onClick={save} disabled={saving}>
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                {savedTick ? 'Saved' : (draft.id ? 'Save changes' : 'Create agent')}
              </button>
            </div>
          </div>
          <div className="flex gap-1 mt-4 -mb-px overflow-x-auto scroll-thin">
            {TABS.map((t) => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-3.5 py-2 text-sm font-medium border-b-2 whitespace-nowrap transition-colors
                  ${tab === t ? 'border-accent text-accent' : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}>
                {t}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto scroll-thin p-6 space-y-5">
          <CostBreakdownBar cost={draft.cost} />

          {tab === 'Agent' && (
            <>
              <div className="card-flat p-5 space-y-4">
                <div className="grid sm:grid-cols-2 gap-4">
                  <Field label="Company"><input className="input" value={draft.company} onChange={(e) => set({ company: e.target.value })} /></Field>
                  <Field label="Status">
                    <select className="input" value={draft.status} onChange={(e) => set({ status: e.target.value })}>
                      <option>Active</option><option>Draft</option>
                    </select>
                  </Field>
                </div>
                <div className="flex items-center justify-between">
                  <div className="label">Welcome Message</div>
                  <div className="text-xs text-slate-400">Delay <span className="font-semibold text-slate-600 dark:text-slate-300">{draft.welcomeDelay.toFixed(1)}s</span></div>
                </div>
                <textarea value={draft.welcomeMessage} onChange={(e) => set({ welcomeMessage: e.target.value })}
                  placeholder="First thing the agent says…" className="input h-24 resize-none" />
                <div>
                  <div className="label">Welcome delay (seconds)</div>
                  <div className="flex items-center gap-3">
                    <input type="range" min="0" max="5" step="0.1" value={draft.welcomeDelay}
                      onChange={(e) => set({ welcomeDelay: +e.target.value })} className="flex-1 accent-accent" />
                    <span className="w-10 text-right text-sm font-semibold text-slate-700 dark:text-slate-200">{draft.welcomeDelay.toFixed(1)}s</span>
                  </div>
                </div>
              </div>
              <PromptCanvas value={draft.prompt} languages={draft.languages} onChange={(prompt) => set({ prompt })} />
            </>
          )}

          {tab === 'LLM' && (
            <div className="card-flat p-5 grid sm:grid-cols-2 gap-4">
              <Field label="Provider"><input className="input" value={draft.llm.provider} readOnly /></Field>
              <Field label="Model"><input className="input" value={draft.llm.model} readOnly /></Field>
              <Field label={`Temperature · ${draft.llm.temperature}`}>
                <input type="range" min="0" max="1" step="0.05" defaultValue={draft.llm.temperature} className="w-full accent-accent" />
              </Field>
              <Field label="Max tokens"><input className="input" type="number" defaultValue={draft.llm.maxTokens} /></Field>
            </div>
          )}

          {tab === 'Audio' && (
            <div className="card-flat p-5 grid sm:grid-cols-2 gap-4">
              <Field label="Transcriber"><input className="input" value={draft.audio.transcriber} readOnly /></Field>
              <Field label="Voice (TTS speaker)">
                <select className="input" value={draft.audio.speaker}
                  onChange={(e) => set({ audio: { ...draft.audio, speaker: e.target.value, voice: `Bulbul · ${e.target.value}` } })}>
                  {['shubh', 'maya', 'arjun', 'anushka'].map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </Field>
              <Field label={`Speaking rate · ${draft.audio.speed}×`}>
                <input type="range" min="0.5" max="1.5" step="0.05" defaultValue={draft.audio.speed} className="w-full accent-accent" />
              </Field>
            </div>
          )}

          {tab === 'Engine' && (
            <div className="card-flat p-5 space-y-1">
              <Field label="Voice activity detection"><input className="input mb-2" value={draft.engine.vad} readOnly /></Field>
              <Toggle label="Allow caller interruptions (barge-in)" on={draft.engine.interruptions} onChange={(v) => setEngine({ interruptions: v })} />
              <Field label={`Endpointing silence · ${draft.engine.endpointing} ms`}>
                <input type="range" min="300" max="1200" step="50" value={draft.engine.endpointing} onChange={(e) => setEngine({ endpointing: +e.target.value })} className="w-full accent-accent" />
              </Field>
            </div>
          )}

          {tab === 'Call' && (
            <div className="card-flat p-5 space-y-1">
              <Field label={`Max call duration · ${Math.round(draft.call.maxDuration / 60)} min`}>
                <input type="range" min="60" max="900" step="30" value={draft.call.maxDuration} onChange={(e) => setCall({ maxDuration: +e.target.value })} className="w-full accent-accent" />
              </Field>
              <Toggle label="Record calls" on={draft.call.recording} onChange={(v) => setCall({ recording: v })} />
              <Toggle label="Voicemail detection" on={draft.call.voicemailDetection} onChange={(v) => setCall({ voicemailDetection: v })} />
            </div>
          )}

          {tab === 'Tools' && (
            <div className="card-flat p-5">
              <div className="section-label mb-3">Connected functions</div>
              <div className="space-y-2">
                {['create_ticket', 'lookup_order', 'send_brochure'].map((fn, i) => (
                  <div key={fn} className="flex items-center justify-between rounded-xl border border-slate-200 dark:border-slate-700 px-4 py-3">
                    <div>
                      <div className="font-mono text-sm text-slate-700 dark:text-slate-200">{fn}</div>
                      <div className="text-xs text-slate-400">{i === 2 ? 'Custom webhook' : 'Built-in tool'}</div>
                    </div>
                    <span className={`badge ${i < 2 ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400' : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'}`}>
                      {i < 2 ? 'Enabled' : 'Disabled'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === 'Analytics' && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <StatCard label="Total calls" value="—" />
              <StatCard label="Avg duration" value="—" />
              <StatCard label="Resolution rate" value="—" />
              <StatCard label="Avg sentiment" value="—" />
            </div>
          )}

          {tab === 'Inbound' && (
            <div className="card-flat p-5 space-y-1">
              <Field label="Inbound number"><input className="input mb-2" value={draft.inbound.number} readOnly /></Field>
              <Toggle label="Greet only after first audio frame" on={draft.inbound.greetingOnFirstFrame} onChange={() => {}} />
              <p className="text-xs text-slate-400 pt-2">
                Greeting fires on the first received frame (not at socket open) to avoid burning TTS credits on abandoned calls.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
