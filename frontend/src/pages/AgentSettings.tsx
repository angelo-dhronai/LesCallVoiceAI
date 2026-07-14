import type { ReactNode } from 'react'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { Card, Button, cx } from '../components/ui'
import { PageHeader, AsyncState } from '../components/kit'
import {
  RefreshCw, Phone, MapPin, Check, X, Lock, Quote, Target, ArrowRight,
  ShieldCheck, ShieldAlert, Volume2, MessageSquare, Bot, Building2, BadgeCheck,
} from 'lucide-react'

type AgentConfig = {
  available: boolean
  reason?: string
  agent_name: string | null
  agent_tone: string | null
  greeting: string | null
  identity: string[]
  brand: string[]
  prompt_guidelines: string[]
  firm_rules: string[]
  pitch: { project_portfolio: string[]; objection_handling: string[] }
  source_file: string
  editable: boolean
  editable_note: string
}

/* ---------------------------------------------------------------- parsers
 * The config is generated from the live conversation engine's source, so its
 * strings carry structure (✓/✗ rule prefixes, "City: projects", "obj → reply").
 * We decode that structure rather than dumping flat bullet lists. */

const arr = (x: unknown): string[] => (Array.isArray(x) ? (x as unknown[]).map((s) => String(s)) : [])

function segments(lines: string[]): string[] {
  return lines.flatMap((l) => l.split('·')).map((s) => s.trim()).filter(Boolean)
}

function parseBrand(brand: string[]) {
  const segs = segments(brand)
  const company = segs[0] ?? ''
  const rest = segs.slice(1)
  const phoneRaw = rest.find((s) => /\+?\d[\d ]{6,}/.test(s))
  const phone = phoneRaw ? phoneRaw.replace(/^contact:\s*/i, '').trim() : ''
  const chips = rest.filter((s) => s !== phoneRaw)
  return { company, chips, phone }
}

function parseGuardrails(rules: string[]) {
  const always: string[] = []
  const never: string[] = []
  let last: { list: string[]; idx: number } | null = null
  for (const raw of rules) {
    const line = raw.trim()
    if (!line) continue
    const head = line[0]
    if (head === '✓' || head === '✔') {
      always.push(line.slice(1).trim()); last = { list: always, idx: always.length - 1 }
    } else if (head === '✗' || head === '✕' || head === '×') {
      never.push(line.slice(1).trim()); last = { list: never, idx: never.length - 1 }
    } else if (last) {
      last.list[last.idx] = `${last.list[last.idx]} ${line}`.trim()
    } else {
      never.push(line); last = { list: never, idx: never.length - 1 }
    }
  }
  return { always, never }
}

function parseGuideline(line: string): { lead?: string; text: string } {
  const i = line.indexOf(':')
  if (i > 0 && i <= 24) {
    const lead = line.slice(0, i).trim()
    if (/^[A-Z0-9 +/&'-]+$/.test(lead)) return { lead, text: line.slice(i + 1).trim() }
  }
  return { text: line }
}

function parsePortfolio(lines: string[]) {
  const groups: { city: string; text: string }[] = []
  let match = ''
  for (const raw of lines) {
    const line = raw.trim()
    const m = line.match(/^([A-Z][A-Z ]*[A-Z]):\s*(.*)$/)
    if (m) {
      if (m[1] === 'MATCH') { match = m[2]; continue }
      groups.push({ city: m[1], text: m[2] })
    } else if (groups.length) {
      groups[groups.length - 1].text = `${groups[groups.length - 1].text} ${line}`.trim()
    }
  }
  return { groups, match }
}

function parseProjects(text: string): { name: string; detail: string }[] {
  const out: { name: string; detail: string }[] = []
  const re = /([^,(]+)\(([^)]*)\)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    const name = m[1].replace(/^[,·\s]+/, '').trim()
    if (name) out.push({ name, detail: m[2].trim() })
  }
  return out
}

function parseMatch(s: string) {
  return s.split('|').map((r) => r.trim()).filter(Boolean).map((r) => {
    const [cond, to] = r.split('→')
    return { cond: (cond ?? '').trim(), to: (to ?? '').trim() }
  })
}

function parseObjection(line: string) {
  const [o, r] = line.split('→')
  return {
    objection: (o ?? '').trim().replace(/^['"\s]+|['"\s]+$/g, ''),
    response: (r ?? '').trim(),
  }
}

const titleCase = (s: string) => s.charAt(0) + s.slice(1).toLowerCase()

/* ---------------------------------------------------------------- atoms */

function Waveform() {
  const bars = [5, 9, 14, 8, 18, 11, 6, 16, 10, 20, 7, 13, 9, 17, 6, 12, 8, 15, 5, 10, 7]
  return (
    <div aria-hidden className="flex h-6 items-center gap-[3px]">
      {bars.map((h, i) => (
        <span key={i} style={{ height: h }} className={cx('w-[3px] rounded-full', i % 2 ? 'bg-indigo-400/70' : 'bg-mint-400/80')} />
      ))}
    </div>
  )
}

function Section({ eyebrow, title, desc, icon, children }: {
  eyebrow: string; title: string; desc?: string; icon: ReactNode; children: ReactNode
}) {
  return (
    <section className="animate-fade-up">
      <div className="mb-4 flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--surface-2)] text-indigo-500 ring-1 ring-[var(--border)] dark:text-indigo-300">{icon}</span>
        <div>
          <div className="eyebrow">{eyebrow}</div>
          <h3 className="font-display text-[17px] font-bold tracking-tight">{title}</h3>
          {desc && <p className="text-[12px] text-dim">{desc}</p>}
        </div>
      </div>
      {children}
    </section>
  )
}

/* ---------------------------------------------------------------- page */

export default function AgentSettings() {
  const { data, isLoading, error } = useGet<AgentConfig>(['agent-config'], '/agent/config')
  const refresh = useRefresh()
  const cfg = data

  const name = cfg?.agent_name || 'Agent'
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || 'AI'
  const tone = (cfg?.agent_tone || '').split(',').map((t) => t.trim()).filter(Boolean)
  const greeting = (cfg?.greeting || '').trim()
  const { company, chips, phone } = parseBrand(arr(cfg?.brand))
  const identity = arr(cfg?.identity)
  const guidelines = arr(cfg?.prompt_guidelines).map(parseGuideline)
  const { always, never } = parseGuardrails(arr(cfg?.firm_rules))
  const { groups, match } = parsePortfolio(arr(cfg?.pitch?.project_portfolio))
  const matchRules = parseMatch(match)
  const objections = arr(cfg?.pitch?.objection_handling).map(parseObjection)
  const note = cfg?.editable_note || 'Read-only — generated live from the calling agent’s source.'

  return (
    <div className="space-y-7">
      <PageHeader
        title="Agent Settings"
        sub="The live persona, voice and playbook behind every call"
        actions={
          <Button variant="ghost" onClick={() => refresh(['agent-config'])}>
            <RefreshCw size={15} /> Refresh
          </Button>
        }
      />

      <AsyncState loading={isLoading} error={error}>
        {cfg?.available === false ? (
          <Card className="p-6 text-[13px] text-soft">{cfg?.reason || 'Agent configuration is unavailable.'}</Card>
        ) : (
          <div className="space-y-7">
            {/* ---------- Hero: caller-ID + opening line ---------- */}
            <Card className="relative overflow-hidden p-6 sm:p-8">
              <div className="pointer-events-none absolute -right-16 -top-16 h-52 w-52 rounded-full bg-indigo-500/15 blur-3xl" />
              <div className="pointer-events-none absolute -bottom-20 left-10 h-52 w-52 rounded-full bg-mint-400/15 blur-3xl" />

              <div className="relative grid gap-6 lg:grid-cols-[auto_1fr] lg:items-center">
                <div className="flex items-center gap-4">
                  <div className="relative grid h-20 w-20 shrink-0 place-items-center rounded-3xl bg-gradient-to-br from-mint-400 to-indigo-500 text-[26px] font-black text-[#04150d] shadow-glow-mint">
                    {initials}
                    <span className="absolute -bottom-1.5 -right-1.5 grid h-7 w-7 place-items-center rounded-full bg-[var(--surface-solid)] ring-1 ring-[var(--border)]">
                      <Phone size={13} className="text-mint-500" />
                    </span>
                  </div>
                  <div className="min-w-0">
                    <div className="eyebrow text-mint-600 dark:text-mint-300">AI voice agent</div>
                    <h2 className="break-words font-display text-[26px] font-extrabold leading-tight tracking-tight">{name}</h2>
                    <div className="truncate text-[13px] text-dim">{company || 'Inbound sales consultant'}</div>
                    {tone.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {tone.map((t) => (
                          <span key={t} className="rounded-full bg-[var(--surface-2)] px-2.5 py-0.5 text-[11px] font-semibold capitalize text-soft ring-1 ring-[var(--border)]">{t}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {greeting && (
                  <div className="lg:border-l lg:border-[var(--border)] lg:pl-7">
                    <div className="eyebrow mb-2">Opening line</div>
                    <div className="rounded-2xl rounded-tl-sm bg-[var(--surface-2)] p-4 ring-1 ring-[var(--border)]">
                      <p className="font-display text-[17px] font-semibold leading-snug text-[var(--text)]">“{greeting}”</p>
                      <div className="mt-3 flex items-center gap-2.5 text-dim">
                        <Volume2 size={14} />
                        <Waveform />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {(chips.length > 0 || phone) && (
                <div className="relative mt-6 flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-5">
                  {chips.map((c) => (
                    <span key={c} className="inline-flex items-center gap-1 rounded-lg bg-mint-400/10 px-2.5 py-1 text-[12px] font-semibold text-mint-700 ring-1 ring-mint-400/20 dark:text-mint-300">
                      <BadgeCheck size={13} /> {c}
                    </span>
                  ))}
                  {phone && (
                    <span className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-indigo-500/10 px-3 py-1 text-[12px] font-semibold text-indigo-600 ring-1 ring-indigo-500/20 dark:text-indigo-300">
                      <Phone size={13} /> {phone}
                    </span>
                  )}
                </div>
              )}
            </Card>

            {/* ---------- Identity ---------- */}
            {identity.length > 0 && (
              <Section eyebrow="Persona" title="Who Vishal is" icon={<Bot size={17} />}>
                <Card className="relative overflow-hidden p-6">
                  <Quote size={20} className="mb-2 text-mint-400/70" />
                  <div className="space-y-2.5">
                    {identity.map((l, i) => (
                      <p key={i} className={cx('leading-relaxed', i === 0 ? 'text-[16px] font-semibold text-[var(--text)]' : 'text-[14px] text-soft')}>{l}</p>
                    ))}
                  </div>
                </Card>
              </Section>
            )}

            {/* ---------- Guardrails: Always / Never ---------- */}
            {(always.length > 0 || never.length > 0) && (
              <Section eyebrow="Guardrails" title="What Vishal always — and never — does" desc="Hard rules enforced on every turn" icon={<ShieldCheck size={17} />}>
                <div className="grid gap-4 lg:grid-cols-2">
                  <Card className="p-5">
                    <div className="mb-3 flex items-center gap-2">
                      <ShieldAlert size={16} className="text-rose-500" />
                      <h4 className="font-display text-[14px] font-bold tracking-tight">Never</h4>
                      <span className="ml-auto text-[11px] font-semibold text-dim">{never.length}</span>
                    </div>
                    <ul className="space-y-2">
                      {never.map((r, i) => (
                        <li key={i} className="flex gap-2.5 text-[13px] text-soft">
                          <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md bg-rose-500/12 text-rose-500"><X size={12} /></span>
                          <span className="min-w-0 break-words leading-snug">{r}</span>
                        </li>
                      ))}
                    </ul>
                  </Card>
                  <Card className="p-5">
                    <div className="mb-3 flex items-center gap-2">
                      <ShieldCheck size={16} className="text-mint-500" />
                      <h4 className="font-display text-[14px] font-bold tracking-tight">Always</h4>
                      <span className="ml-auto text-[11px] font-semibold text-dim">{always.length}</span>
                    </div>
                    <ul className="space-y-2">
                      {always.map((r, i) => (
                        <li key={i} className="flex gap-2.5 text-[13px] text-soft">
                          <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md bg-mint-400/15 text-mint-600 dark:text-mint-300"><Check size={12} /></span>
                          <span className="min-w-0 break-words leading-snug">{r}</span>
                        </li>
                      ))}
                    </ul>
                  </Card>
                </div>
              </Section>
            )}

            {/* ---------- Talk style ---------- */}
            {guidelines.length > 0 && (
              <Section eyebrow="Voice" title="How Vishal talks" desc="Principles that shape every response" icon={<MessageSquare size={17} />}>
                <div className="grid gap-3 sm:grid-cols-2">
                  {guidelines.map((g, i) => (
                    <Card key={i} className="p-4">
                      {g.lead && <div className="eyebrow mb-1 text-indigo-500 dark:text-indigo-300">{g.lead}</div>}
                      <p className="text-[13px] leading-relaxed text-soft">{g.text}</p>
                    </Card>
                  ))}
                </div>
              </Section>
            )}

            {/* ---------- Pitch: portfolio by city + matching logic ---------- */}
            {(groups.length > 0 || matchRules.length > 0) && (
              <Section eyebrow="Playbook" title="What Vishal sells" desc="Pramukh Group portfolio, grouped by city" icon={<Building2 size={17} />}>
                <div className="space-y-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    {groups.map((g) => {
                      const projects = parseProjects(g.text)
                      return (
                        <Card key={g.city} className="p-5">
                          <div className="mb-3 flex items-center gap-2">
                            <MapPin size={15} className="text-mint-500" />
                            <h4 className="font-display text-[14px] font-bold tracking-tight">{titleCase(g.city)}</h4>
                          </div>
                          {projects.length > 0 ? (
                            <div className="flex flex-wrap gap-1.5">
                              {projects.map((p, i) => (
                                <span key={i} className="rounded-lg bg-[var(--surface-2)] px-2.5 py-1.5 text-[12px] ring-1 ring-[var(--border)]">
                                  <span className="font-semibold text-[var(--text)]">{p.name}</span>
                                  <span className="ml-1 text-dim">{p.detail}</span>
                                </span>
                              ))}
                            </div>
                          ) : (
                            <p className="text-[13px] leading-relaxed text-soft">{g.text}</p>
                          )}
                        </Card>
                      )
                    })}
                  </div>

                  {matchRules.length > 0 && (
                    <Card className="p-5">
                      <div className="mb-3 flex items-center gap-2">
                        <Target size={15} className="text-indigo-500 dark:text-indigo-300" />
                        <h4 className="font-display text-[14px] font-bold tracking-tight">Project matching logic</h4>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                        {matchRules.map((r, i) => (
                          <div key={i} className="flex items-center gap-2 rounded-xl bg-[var(--surface-2)] p-2.5 text-[12px] ring-1 ring-[var(--border)]">
                            <span className="font-semibold text-[var(--text)]">{r.cond}</span>
                            <ArrowRight size={13} className="shrink-0 text-mint-500" />
                            <span className="font-semibold text-indigo-600 dark:text-indigo-300">{r.to}</span>
                          </div>
                        ))}
                      </div>
                    </Card>
                  )}
                </div>
              </Section>
            )}

            {/* ---------- Objection handling ---------- */}
            {objections.length > 0 && (
              <Section eyebrow="Playbook" title="Handling objections" desc="What Vishal says when a caller pushes back" icon={<Quote size={17} />}>
                <div className="space-y-2.5">
                  {objections.map((o, i) => (
                    <Card key={i} className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:gap-4">
                      <div className="flex items-start gap-2 sm:w-[38%] sm:shrink-0">
                        <Quote size={15} className="mt-0.5 shrink-0 text-rose-400" />
                        <span className="text-[13px] font-semibold text-[var(--text)]">{o.objection}</span>
                      </div>
                      <ArrowRight size={16} className="hidden shrink-0 text-dim sm:block" />
                      <p className="text-[13px] leading-relaxed text-soft sm:flex-1">{o.response}</p>
                    </Card>
                  ))}
                </div>
              </Section>
            )}

            {/* ---------- Read-only footer ---------- */}
            <div className="flex flex-wrap items-center gap-2 rounded-xl bg-[var(--surface-2)] px-4 py-3 text-[12px] text-dim ring-1 ring-[var(--border)]">
              <Lock size={13} className="shrink-0" />
              <span className="min-w-0">{note}</span>
              {cfg?.source_file && <code className="ml-auto truncate font-mono text-[11px]">{cfg.source_file}</code>}
            </div>
          </div>
        )}
      </AsyncState>
    </div>
  )
}
