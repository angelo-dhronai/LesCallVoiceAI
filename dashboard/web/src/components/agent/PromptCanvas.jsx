import { useState } from 'react'

// Markdown prompt editor with clickable language pills above a scrollable editor.
export default function PromptCanvas({ value, languages, onChange }) {
  const [activeLang, setActiveLang] = useState(languages[0])

  return (
    <div className="card-flat p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="section-label">Prompt Canvas</div>
        <div className="flex flex-wrap gap-1.5">
          {languages.map((l) => (
            <button
              key={l}
              onClick={() => setActiveLang(l)}
              className={`px-2.5 py-1 rounded-full text-xs font-medium border transition
                ${activeLang === l
                  ? 'bg-accent text-white border-accent'
                  : 'border-slate-200 text-slate-500 hover:border-slate-300 dark:border-slate-700 dark:text-slate-400'}`}
            >
              {l}
            </button>
          ))}
        </div>
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        className="w-full h-72 resize-none rounded-xl border border-slate-200 dark:border-slate-700
          bg-slate-50 dark:bg-slate-950/60 p-4 font-mono text-[13px] leading-relaxed
          text-slate-700 dark:text-slate-200 outline-none focus:ring-2 focus:ring-accent/40 scroll-thin"
      />
      <div className="mt-2 text-[11px] text-slate-400">
        Editing prompt for <span className="font-medium text-slate-500 dark:text-slate-300">{activeLang}</span> · Markdown supported
      </div>
    </div>
  )
}
