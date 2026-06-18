import { useEffect, useState } from 'react'
import { api } from '../api.js'

const BLANK = {
  name: '', company: '', persona: '', system_prompt: '', pitch: '',
  language: 'auto', tts_speaker: 'shubh', greeting: '', is_active: 1,
}

export default function Agents() {
  const [agents, setAgents] = useState([])
  const [sel, setSel] = useState(null)
  const [form, setForm] = useState(BLANK)
  const [msg, setMsg] = useState(null)

  const load = () => api.agents().then(setAgents).catch((e) => setMsg(e.message))
  useEffect(() => { load() }, [])

  const edit = (a) => { setSel(a.id); setForm({ ...BLANK, ...a }) }
  const fresh = () => { setSel(null); setForm(BLANK) }
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const save = async () => {
    try {
      if (sel) await api.updateAgent(sel, form)
      else await api.createAgent(form)
      setMsg('Saved'); await load()
    } catch (e) { setMsg(e.message) }
  }
  const remove = async () => {
    if (sel && confirm('Delete this agent?')) { await api.deleteAgent(sel); fresh(); load() }
  }

  // Pitch maker: assemble a draft pitch from the persona + company fields.
  const makePitch = () => {
    const p = `Hi, this is ${form.name || 'your agent'} from ${form.company || 'our company'}. ` +
      `${form.persona || ''} I'd love to understand what you're looking for and find the right fit — ` +
      `may I ask a couple of quick questions?`
    setForm({ ...form, pitch: p.trim() })
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Agents & Pitch Maker</h1>
        <button className="btn btn-primary" onClick={fresh}>+ New agent</button>
      </div>
      {msg && <div className="mb-4 text-sm text-slate-600">{msg}</div>}
      <div className="grid md:grid-cols-3 gap-6">
        <div className="space-y-2">
          {agents.map((a) => (
            <div key={a.id} onClick={() => edit(a)}
              className={`card cursor-pointer ${sel === a.id ? 'ring-2 ring-brand' : ''}`}>
              <div className="font-semibold">{a.name}</div>
              <div className="text-xs text-slate-500">{a.company} · {a.language}</div>
            </div>
          ))}
          {agents.length === 0 && <div className="text-sm text-slate-400">No agents yet</div>}
        </div>

        <div className="md:col-span-2 card space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Name</label><input className="input" value={form.name} onChange={set('name')} /></div>
            <div><label className="label">Company</label><input className="input" value={form.company || ''} onChange={set('company')} /></div>
          </div>
          <div><label className="label">Persona</label><input className="input" value={form.persona || ''} onChange={set('persona')} /></div>
          <div>
            <div className="flex justify-between items-center">
              <label className="label">Pitch</label>
              <button className="btn btn-ghost text-xs" onClick={makePitch}>✨ Generate pitch</button>
            </div>
            <textarea className="input h-20" value={form.pitch || ''} onChange={set('pitch')} />
          </div>
          <div><label className="label">System prompt</label><textarea className="input h-32" value={form.system_prompt} onChange={set('system_prompt')} /></div>
          <div className="grid grid-cols-3 gap-3">
            <div><label className="label">Language</label>
              <select className="input" value={form.language} onChange={set('language')}>
                <option value="auto">auto</option><option value="en-IN">en-IN</option>
                <option value="hi-IN">hi-IN</option><option value="gu-IN">gu-IN</option>
              </select>
            </div>
            <div><label className="label">TTS speaker</label><input className="input" value={form.tts_speaker} onChange={set('tts_speaker')} /></div>
            <div><label className="label">Greeting</label><input className="input" value={form.greeting || ''} onChange={set('greeting')} /></div>
          </div>
          <div className="flex gap-2 pt-2">
            <button className="btn btn-primary" onClick={save}>{sel ? 'Update' : 'Create'}</button>
            {sel && <button className="btn btn-ghost text-red-600" onClick={remove}>Delete</button>}
          </div>
        </div>
      </div>
    </div>
  )
}
