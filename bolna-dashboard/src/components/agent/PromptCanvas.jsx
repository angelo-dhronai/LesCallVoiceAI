import { useEffect, useState } from 'react'
import { FileCode2 } from 'lucide-react'

function PromptCanvas({ prompt, languages = [] }) {
  const [text, setText] = useState(prompt ?? '')

  // The primary language pill comes pre-selected; keep it controllable.
  const primaryLabel = languages.find((l) => l.primary)?.label
  const [activeLang, setActiveLang] = useState(primaryLabel ?? languages[0]?.label ?? '')

  // Swap content when the parent feeds a different agent's prompt.
  useEffect(() => {
    setText(prompt ?? '')
  }, [prompt])

  useEffect(() => {
    setActiveLang(primaryLabel ?? languages[0]?.label ?? '')
  }, [primaryLabel, languages])

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-4 py-3 dark:border-white/10">
        <div className="flex items-center gap-2">
          <FileCode2 className="h-4 w-4 text-gray-400" />
          <span className="section-label">Agent Prompt</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {languages.map((lang) => {
            const isActive = lang.label === activeLang
            return (
              <button
                key={lang.label}
                type="button"
                onClick={() => setActiveLang(lang.label)}
                className={[
                  'rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors',
                  isActive
                    ? 'border-accent bg-accent text-white'
                    : 'border-gray-200 text-gray-600 hover:bg-gray-100 dark:border-white/10 dark:text-gray-300 dark:hover:bg-white/5',
                ].join(' ')}
              >
                {lang.label}
                {lang.primary ? ' (Primary)' : ''}
              </button>
            )
          })}
        </div>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        className="block max-h-80 min-h-64 w-full resize-y overflow-auto bg-gray-50 px-4 py-3 font-mono text-[13px] leading-relaxed text-gray-800 placeholder-gray-400 focus:outline-none dark:bg-[#0f1218] dark:text-gray-200"
      />
    </div>
  )
}

export default PromptCanvas
