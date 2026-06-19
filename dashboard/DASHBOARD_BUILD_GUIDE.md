# LesCall Dashboard — Functionality Inventory, Architecture & Rebuild Prompt

> A complete teardown of the operations dashboard in `dashboard/` for the Pramukh Group voice-AI
> calling platform. This document has three parts:
> 1. **Functionality inventory** — every panel and what it does.
> 2. **Architecture & build logic** — how it is actually built, end to end.
> 3. **Known problems + fixes**, then a **ready-to-paste Claude Code build prompt** that bakes the
>    fixes in.

---

## 0. What the dashboard is

A single-process **FastAPI** backend (`dashboard/server.py`, ~2540 lines, uvicorn, `0.0.0.0:8080`,
`workers=1`) serving a **vanilla-JS single-page app** (`dashboard/static/`, no framework, no build
step). nginx reverse-proxies port 80 → 8080 (SSE-friendly config). It is an **operations console**
for the voice pipeline — it never imports the pipeline; it observes it through **shared files/logs**
and drives it through **shell scripts** (`start_all.sh`/`stop_all.sh`) and **FreeSWITCH `fs_cli`
originate** for outbound dialing.

```
Browser SPA (static/)  ──REST + SSE──►  FastAPI (server.py :8080)
                                              │
            ┌─────────────────────────────────┼──────────────────────────────┐
            │ shell-out                        │ read-only files              │ fs_cli
            ▼                                  ▼                              ▼
   sudo start_all/stop_all.sh        credit_log/*.jsonl, recordings/*,   FreeSWITCH
   (lifecycle of voice pipeline)     live_calls_state.json, *.log,       originate (Plivo gw)
                                     conversation.py (text-grepped)
```

Data is persisted in flat JSON files in `dashboard/`:
`outbound_contacts.json`, `call_link.json`, `call_analysis.json`, `sarvam_credits.json`,
`api_key_pool.json`, `api_key_state.json`, `follow_ups.json`, `follow_up_automation.json`,
`minutes_quota.json`.

---

## 1. Functionality inventory (16 panels)

Navigation is grouped in the sidebar (`static/index.html:25-44`). Each panel is a `<div class="tab-panel">`
that exists in the DOM at all times; switching tabs toggles a CSS `.active` class (no router). Each
panel has a JS module under `static/js/` that defines a global `loadX()` loader dispatched from
`app.js`'s `onActivate()`.

### Monitoring group
| Panel | Module | Backend endpoints | What it does |
|---|---|---|---|
| **Overview** | `overview.js` | `GET /api/overview?period`, `POST /api/minutes/quota` | KPI home: call counts & costs (today/week/month), active calls, STT/TTS seconds & cost, sparkline + daily/hourly CSS bar charts, call-type/status distributions, minutes-quota setter. |
| **Live Calls** | `live-calls.js` | `GET /api/calls/live-state`, `GET /api/calls/live-state/stream` (SSE) | Real-time card grid of in-progress calls: caller, elapsed, who's speaking, language, 6-stage progress strip, transcript tail, "View Conversation" → slide-over. REST seeds first paint, SSE updates. |
| **Call Activity** (history) | `history.js` | `GET /api/calls/history?page&limit&search&days&call_type&status` | Paginated/filterable/sortable unified call log (pipeline calls + failed outbound dials): end time, contact, direction badge, duration, status, short id, est. cost. |
| **Recordings** | `recordings.js` | `GET /api/calls/recordings?page&limit&search`, `GET /api/calls/recordings/{folder}/audio[?download]` | Paginated recordings with inline `<audio preload=none>` player + download link. |
| **Follow-Up Tracker** | `followups.js` | `GET /api/followups?status`, `PATCH /api/followups/{id}`, `POST /api/followup-automation` | Follow-ups sourced from outbound dispositions (`to_contact_later`/`in_doubt`) + AI-flagged calls; set status pending/completed/missed; "⚡ Automate" hands off to automation tab. |
| **Live Logs** | `logs.js` | `GET /api/logs/stream?categories` (SSE) | Streaming `tail -F` of `ws_audio_receiver.log` + `socket_server.log`, category filter chips, colorized, ring-buffer (1000 lines), auto-scroll toggle. |

### Intelligence group
| Panel | Module | Backend endpoints | What it does |
|---|---|---|---|
| **Call Transcripts** (conversations) | `conversations.js` | `GET /api/calls/recordings`, `GET /api/calls/transcripts/search?q`, `GET /api/calls/recordings/{folder}/transcript` | Master/detail transcript browser: list + chat-bubble transcript pane, client filter + server full-text keyword search (300ms debounce). |
| **AI Call Analysis** | `ai-analysis.js` | `GET /api/analysis/eligible`, `POST /api/analysis/run`, `GET /api/calls/recordings/{folder}/analysis` | On-demand LLM analysis of completed transcripts (multi-select + batch run, cached, billable). Result card: summary, intent, sentiment, lead quality, key points, next action, objections, topics, products, follow-up flag. |
| **Conversation Analytics** | `conv-analytics.js` | `GET /api/analytics/conversation` | Roll-up of all analyzed calls: hot/negative counts, AI recommendations, lead intelligence, sentiment, top questions, objections, topics, product interest, daily trends table. |
| **Analytics** | `analytics.js` | `GET /api/overview`, `GET /api/calls/recordings?limit=200`, `GET /api/outbound/stats` | Client-side-derived metrics: calls today, avg duration, conversion %, visit-booking rate, hourly chart, calls-by-language, outcome breakdown. |
| **Follow-Up Automation** | `followup-automation.js` | `GET/POST /api/followup-automation`, `PATCH/DELETE /api/followup-automation/{id}`, `POST /api/followup-automation/{id}/call-now` | Schedule outbound follow-up calls; inline-edit call-aim/notes/scheduled-time; Call Now / cancel / delete. A 30s server scheduler dials due entries. (Note: notes/context are operator-reference only — **not** injected into the live agent prompt.) |

### Campaigns group
| Panel | Module | Backend endpoints | What it does |
|---|---|---|---|
| **Outbound Calls** | `outbound.js` | `GET/POST /api/outbound/contacts`, `PATCH/DELETE /api/outbound/contacts/{id}`, `DELETE …/all`, `POST …/upload`, `POST /api/outbound/dial/{id}`, `GET /api/outbound/stats`, `POST /api/outbound/campaign/{start,stop}`, `GET /api/outbound/campaign/status` | Contact CRUD, CSV/TXT/XLSX bulk upload (phone/name/designation parsing), per-contact manual dial, disposition tracking, and a **sequential auto-dialer campaign** with live progress bar (polled every 2s). |

### Finance group
| Panel | Module | Backend endpoints | What it does |
|---|---|---|---|
| **API Usage** (sarvam) | `sarvam.js` | `GET/POST /api/sarvam/credits`, `GET /api/sarvam/keys`, `POST /api/sarvam/keys/{replace,bulk}`, `DELETE /api/sarvam/keys/pool`, `POST /api/sarvam/keys/ack-restart`, `GET /api/sarvam/usage?period`, `GET /api/sarvam/usage/history` | Sarvam credit gauge with low/zero alerts + auto-rotation, API-key pool management (replace active key in `.env`, bulk-add backups, clear pool, restart-required badge), daily usage cost table + top-10 costly calls. |

### System group
| Panel | Module | Backend endpoints | What it does |
|---|---|---|---|
| **Runtime Control** | `control.js` | `GET /api/runtime/status`, `GET /api/runtime/output/stream` (SSE), `POST /api/runtime/{start,stop,restart}`, `POST /api/sarvam/keys/ack-restart` | Start/stop/restart the voice pipeline via `sudo start_all/stop_all.sh`, with a live colorized pseudo-terminal of command output (SSE). |
| **Health** | `health.js` | `GET /api/runtime/status` | Service health grid: per-process running/PID, port checks (9000/9001 via `ss`), `/proc/<pid>` liveness, infra cards (STT/TTS/LLM cards are hardcoded "Not monitored"). |
| **Agent Settings** | `agent-settings.js` | `GET /api/agent/config` | **Read-only** persona view: name, tone, greeting, identity, guidelines, firm rules, project portfolio, objection handling — extracted by **text-grepping `conversation.py`** (never imports it). |

### Shared shell (not a panel)
- `app.js` — theme (localStorage light/dark), `api()` fetch helper, formatters, `esc()` HTML-escaper,
  `switchTab`/`onActivate` navigation, 4 polling timers (clock 1s, health 10s, credits 30s, active-tab
  refresh 15s), status chips, notifications, control-button state.
- `panel.js` — the **conversation slide-over** drawer (SSE-fed for active calls, REST for past calls),
  focus trap, Esc-to-close, copy/export.

---

## 2. Architecture & build logic (how it is actually built)

### 2.1 Backend (`server.py`)
- **FastAPI + uvicorn, single worker.** `workers=1` is load-bearing: campaign state (`_campaign`),
  the command-output hub (`_cmd_hub`), and TTL caches are all in-process globals. CORS is wide open
  (`allow_origins=["*"]`), Swagger/redoc disabled.
- **Data access pattern:** tiny `_load_*` / `_save_*` helpers per JSON file using
  `Path.read_text()` / `write_text(json.dumps(indent=2))`. **No locking, no atomic writes.**
- **Cost/usage source of truth:** the voice pipeline writes `credit_log/<date>.jsonl` (events:
  `call_start`/`call_end`/`stt_event`/`tts_event`). The dashboard reads & aggregates these (cached 30s)
  for overview, analytics, active-calls inference, and Sarvam usage. The dashboard never computes cost
  itself from audio — it trusts the credit log.
- **Direction classification:** there is no shared call-id between an outbound `originate` and the
  resulting pipeline call. `_record_dial()` appends to `call_link.json`; `_classify_direction()`
  tags a pipeline call inbound/outbound by **timestamp proximity (±90s)**. Best-effort, will
  misattribute under concurrency.
- **Outbound dialing:** `_fs_originate_cmd()` builds
  `originate {caller_id,timeout,ignore_early_media}sofia/external/<phone>@<plivo-gw> outbound_ai_connect XML default`,
  run via `fs_cli -x` through `create_subprocess_exec` (no shell). `_parse_fs_result()` maps
  `+OK/NO_ANSWER/USER_BUSY/…` to a disposition. Campaign = one global `_campaign` dict dialed
  sequentially with a delay, cooperatively stopped via `_campaign["stopped"]`.
- **AI analysis:** `AsyncOpenAI`, model `DASHBOARD_ANALYSIS_MODEL` (default `gpt-4o-mini`),
  JSON-mode, `max_tokens=600`, `temperature=0.2`, reads `transcript.txt` only. On-demand + cached to
  `call_analysis.json` (stored in dashboard dir because `recordings/<folder>/` is root-owned).
- **Sarvam credits/keys:** credits are **manually synced** (Sarvam has no quota API).
  `_maybe_auto_rotate_key()` runs **as a side-effect of `GET /api/sarvam/credits`** — if
  `remaining<=0` and the pool is non-empty, it pops the next key, writes it to `.env`, resets the
  balance, and flags `restart_required` (the live agent reads the key only at startup).
- **Background work:** one asyncio startup task = the 30s follow-up automation scheduler. Plus
  one-shot async tasks per dial/campaign, and SSE generators (`tail -F` subprocesses, 1s mtime poll
  on live-state).

### 2.2 Frontend (`static/`)
- **No framework / no build / no modules.** Plain `<script src>` tags load `app.js` → `panel.js` →
  16 tab modules into one global scope; an inline bootstrap fires `pollHealth/loadOverview/checkApiCredits`
  after all load. Communication is via window globals; `onActivate()` is a hardcoded if/else dispatch.
- **Rendering:** template-literal HTML strings assigned to `innerHTML`, with `esc()` for text
  interpolation. SSE (`EventSource`) for live calls, logs, runtime output, and the active-call
  slide-over. CSS-only charts (bar lists, sparklines) — no charting library.
- **Styling:** design tokens in `tokens.css` (teal `#084868` sidebar, green `#18b068` accent,
  light default + `[data-theme=dark]`), `layout.css` (fixed 220px sidebar, responsive at 1100/768/560),
  `components.css` (cards, sticky-header tables, chat bubbles, slide-over).

---

## 3. Known problems & how to fix them

These were found by auditing the full backend + every panel. **Severity-ordered.** The build prompt
in §4 references these by number.

### P1 — No authentication on a privileged, internet-bindable control surface 🔴 critical
`server.py` binds `0.0.0.0:8080`, CORS `*`, and exposes endpoints that **`sudo`-run shell scripts,
place real phone calls, wipe all contacts, read recordings/transcripts, and rewrite API keys in
`.env`** — all unauthenticated.
**Fix:** Add an auth layer before anything else ships beyond localhost:
- A FastAPI dependency requiring a bearer token / session cookie on every `/api/*` route (env-set
  shared secret at minimum; proper user login ideally). Exempt only `/` and `/static`.
- Bind to `127.0.0.1` and let nginx terminate TLS + enforce auth (basic-auth or an auth subrequest)
  if a quick mitigation is needed today.
- Lock CORS to the known dashboard origin.
- Restrict the `sudo` entry to exactly the two scripts via a tight sudoers rule (`NOPASSWD` only for
  those absolute paths), never a general shell.

### P2 — Unlocked, non-atomic concurrent JSON writes can corrupt/erase data 🔴 critical
Every `_save_*` does `write_text()` with no lock and no temp-file+rename. The 30s scheduler, campaign
task, and request handlers all mutate `outbound_contacts.json` / `follow_up_automation.json` /
`call_link.json` concurrently → lost updates; a crash mid-write truncates the file; a truncated file
then reads as `[]` and the next save **silently wipes the data**.
**Fix:**
- Wrap all read-modify-write on a given file in a per-file `asyncio.Lock` (a dict of locks keyed by
  path), held across the read→mutate→write.
- Make every write atomic: write to `path.tmp` then `os.replace(tmp, path)`.
- On load, if JSON parse fails, **do not** return empty-and-overwrite — back up the corrupt file
  (`.corrupt.<ts>`) and surface an error so data is never silently lost.
- Medium term: move the operational stores (contacts, automations, call_link, analysis) to **SQLite**
  (WAL mode) — it gives you atomicity, concurrency, and indexed history/search for free.

### P3 — JS injection via inline event handlers built from interpolated ids 🟠 high
`esc()` only escapes `& < >`, **not quotes** (`app.js:86`). Panels build
`onclick="dialContact('${c.id}')"` etc. A value containing `'` breaks out of the JS string →
arbitrary JS. `followup-automation.js` doesn't even `esc()` the id; `outbound.js` ids come from
**CSV upload** (the most attacker-reachable surface).
**Fix:** Stop building handlers as strings. Render rows with `data-id="…"` attributes and attach **one
delegated `addEventListener`** per table (event delegation), reading `e.target.closest('[data-id]')`.
This removes the entire injection class and is less code. If any string interpolation into HTML
attributes/JS remains, add a `escAttr()`/`JSON.stringify` that also escapes quotes.

### P4 — Side-effecting GET triggers key rotation 🟠 high
`GET /api/sarvam/credits` calls `_maybe_auto_rotate_key()`, which mutates `.env` + pool files. Any
poller/crawler/double-request can rotate keys; two concurrent polls can both pop the pool.
**Fix:** Make GET idempotent (read-only). Move rotation to either an explicit
`POST /api/sarvam/keys/rotate` or the background scheduler, guarded by the per-file lock from P2.

### P5 — Blocking I/O on the single-worker event loop 🟠 high
All `_load_*`/`_save_*` do sync file I/O inside async handlers; `transcripts_search` reads **every**
transcript per request; `log_tail`/`_check_port` use sync `subprocess.run`; `outbound_upload` calls
`time.sleep(0.001)` for id uniqueness. On one worker, each blocks all other requests.
**Fix:** Offload blocking work with `await asyncio.to_thread(...)` (or `aiofiles`). Replace the
full-scan transcript search with a SQLite FTS index (ties into P2). Generate ids from a monotonic
counter, not `time.sleep`. Use `asyncio.create_subprocess_exec` for port/tail checks.

### P6 — Frontend silently swallows errors / missing `response.ok` checks 🟡 medium
Read-only panels `catch(e){console.error}` and show stale data as if live. Many raw `fetch()` calls
(`updateMinutesQuota`, `setDisposition`, `updateFollowupStatus`, `updateAutomationField`,
`cancelAutomation`, delete handlers, `clearApiKeyPool`) never check `r.ok` — a 4xx/5xx looks like
success.
**Fix:** Route every call through the shared `api()` helper (which throws on `!r.ok`). On failure show
a visible toast/error row via `pushNotification`, not just `console.error`. Add a per-panel
"last updated / stale" indicator.

### P7 — `datetime-local` UTC↔local round-trip bug 🟡 medium
`followup-automation.js` populates a `datetime-local` input from `toISOString().slice(0,16)` (UTC) but
the browser treats it as local, and onchange re-serializes local→UTC. Scheduled times shift by the
operator's UTC offset.
**Fix:** Convert UTC→local for display (subtract the offset before slicing) and local→UTC on save
consistently, or store/display a single explicit timezone (IST) and label it.

### P8 — SSE has no client-side reseed; live durations freeze between messages 🟡 medium
`live-calls.js`/`logs.js` rely solely on browser auto-reconnect; after a long drop, stale cards
persist and the REST seed never re-runs. Card durations only change when the server resends.
**Fix:** On `EventSource.onerror`, after N failed reconnects, re-run the REST seed. Tick elapsed
durations client-side from a stored `started_at` with a 1s timer rather than trusting server pushes.

### P9 — Fragile coupling: agent config & stage strip are hand-mirrored 🟡 medium
`/api/agent/config` text-greps `conversation.py` for exact marker strings and a literal greeting;
`app.js STAGE_STEPS` hand-mirrors the pipeline stages. Any prompt/stage refactor silently breaks these.
**Fix:** Have the pipeline expose its persona/stages as a small JSON (a `agent_meta.json` it writes, or
a read-only endpoint on the pipeline) and consume that, instead of grepping source.

### P10 — Hardcoded host paths, caller-id, gateway, and "always up" health 🟢 low
Everything is hardcoded under `/home/angelo/lescall`; Plivo gateway and caller-id are literals; the
Dashboard health card is tautologically "up"; STT/TTS/LLM cards are "Not monitored".
**Fix:** Move paths/caller-id/gateway/model to env or a `config.json`. Add real STT/TTS/LLM probes
(a cheap ping or last-success timestamp from the credit log) so the infra grid means something.

---

## 4. Ready-to-paste Claude Code build prompt

> Paste the block below into Claude Code (in a fresh repo or alongside the existing one) to rebuild the
> dashboard correctly. It encodes the architecture above and bakes in the P1–P10 fixes.

```
Build an operations dashboard for a real-time voice-AI phone-calling platform (FreeSWITCH → WebSocket
→ Sarvam STT/TTS + LLM pipeline). It is a single-host internal console that OBSERVES the voice
pipeline via shared files/logs and CONTROLS it via shell scripts and FreeSWITCH `fs_cli` — it must
never import the pipeline code.

## Stack
- Backend: FastAPI + uvicorn (single worker is acceptable; keep in-process state minimal and guarded).
- Frontend: vanilla JS SPA, no build step, tab/panel system (panels are divs toggled by a CSS .active
  class; one JS module per panel registered in a small registry, NOT a hardcoded if/else).
- Persistence: SQLite (WAL mode) for operational data (contacts, campaigns, automations, call_link,
  cached AI analysis). Keep flat JSON only for tiny single-object config (credits, key pool, quota).
- Reverse proxy: nginx on :80 → :8080 with SSE-friendly settings (proxy_buffering off, long read
  timeout, Connection '').

## Security (MANDATORY — do this first)
- Bind uvicorn to 127.0.0.1. Require auth on every /api/* route via a FastAPI dependency (bearer token
  from env at minimum; session login preferred). Exempt only `/` and `/static`.
- Lock CORS to the dashboard origin (no wildcard).
- The only privileged action is start/stop/restart of the pipeline. Run it through `sudo` restricted by
  sudoers to exactly two absolute script paths; never pass user input to a shell.
- Build all FreeSWITCH originate commands with create_subprocess_exec (no shell). Sanitize phone to
  digits before use.

## Data integrity (MANDATORY)
- Every read-modify-write on a store goes through a per-resource asyncio.Lock held across read→mutate→write.
- Every write is atomic: write to a temp file then os.replace().
- On a corrupt/unparseable store, back it up (.corrupt.<ts>) and raise — NEVER return empty and let the
  next save wipe it.
- Do all blocking file/subprocess work via asyncio.to_thread (or aiofiles / async subprocess). No sync
  file I/O, no time.sleep, no subprocess.run inside async handlers.
- All GET endpoints are idempotent (read-only). No GET may mutate state (key rotation lives in a POST
  endpoint or the background scheduler, lock-guarded).

## Backend features (endpoints)
1. Runtime control: GET /api/runtime/status (probe ports + PIDs + /proc); POST start|stop|restart
   (sudo scripts, single-flight lock, 409 if busy); GET /api/runtime/output/stream (SSE of command
   output).
2. Overview/KPIs: GET /api/overview?period=today|week|month — aggregate cost/usage/active-calls from
   the pipeline's credit_log/<date>.jsonl (events call_start/call_end/stt_event/tts_event), cached ~30s.
   GET/POST /api/minutes/quota.
3. Live calls: GET /api/calls/live-state (read pipeline's live_calls_state.json); GET
   /api/calls/live-state/stream (SSE, file-mtime poll).
4. History: GET /api/calls/history with pagination/search/filter (days, call_type, status). Direction
   is inferred by matching outbound dials (call_link store, ±90s window) to pipeline calls — document
   this as best-effort.
5. Recordings/transcripts: list (paginated), serve audio (path-traversal guarded), get transcript,
   full-text search via SQLite FTS (not a per-request file scan).
6. AI analysis: POST /api/analysis/run (batch folders, force flag) using an LLM in JSON mode over the
   transcript; cache results in SQLite; GET eligible/list/result; GET /api/analytics/conversation
   rollup. Make provider+model configurable via env.
7. Sarvam credits/keys: GET/POST credits (manual sync; credits have no provider API); key pool
   management (replace active key in the pipeline .env, bulk add, clear, restart-required flag +
   ack-restart). Auto-rotation runs in the background scheduler or an explicit POST — never on a GET.
8. Outbound: contact CRUD, CSV/TXT/XLSX bulk upload (parse phone/name/designation; recover Excel
   scientific-notation numbers), single dial, stats, and a sequential auto-dial campaign with live
   status (one campaign at a time, cooperative stop, lock-guarded global state).
9. Follow-ups: GET tracker (from outbound dispositions + AI-flagged calls, with manual status
   overrides); follow-up automation CRUD + call-now + a background scheduler (poll ~30s) that dials
   due entries.
10. Logs: GET /api/logs/stream (SSE tail -F of pipeline logs, category-colorized); GET /api/logs/tail.
11. Agent config: GET /api/agent/config — read the pipeline's persona/stages from a JSON the pipeline
    publishes (agent_meta.json or a pipeline endpoint), NOT by grepping source code.

## Frontend
- Panels (16): Overview, Live Calls, Call Activity (history), Recordings, Follow-Up Tracker, Live Logs,
  Call Transcripts, AI Call Analysis, Conversation Analytics, Analytics, Follow-Up Automation, Outbound
  Calls, API Usage (Sarvam), Runtime Control, Health, Agent Settings. Group them: Monitoring,
  Intelligence, Campaigns, Finance, System.
- One shared app.js: fetch helper `api()` that throws on !r.ok; an `escHtml()` that escapes & < > " '
  AND an `escAttr()`/JSON.stringify path for attribute/JS contexts. Theme toggle (localStorage),
  formatters, a panel registry, polling timers that PAUSE when document.hidden.
- Tables: render rows with data-* attributes and ONE delegated addEventListener per table. Never build
  onclick="fn('${id}')" handler strings. (This kills the JS-injection class.)
- Every fetch goes through api(); on failure show a visible toast and a per-panel "stale since …"
  indicator — never swallow errors into console only.
- SSE panels (live calls, logs, runtime, slide-over): on repeated onerror, re-run the REST seed. Tick
  live call durations client-side from started_at every 1s; don't depend on server pushes for the clock.
- Times: store/display in one explicit timezone (IST), labeled. For datetime-local inputs, convert
  UTC↔local consistently so scheduled times don't drift by the operator's offset.
- A conversation slide-over drawer: SSE-fed for active calls (append only new transcript turns), REST
  for completed calls; proper dialog semantics (role, aria-modal, focus trap, Esc).
- Accessibility: nav items and row actions are <button>/<a>, keyboard-focusable. CSS-only charts are
  fine (no charting lib). Dark teal sidebar (#084868) + green accent (#18b068), light/dark themes via
  design tokens.

## Config (no hardcoded host paths)
- All paths, FreeSWITCH caller-id + gateway, LLM provider/model, and the bind address come from env or
  a config.json. Provide a .env.example.
- Health panel: add real STT/TTS/LLM probes (last-success timestamp from credit_log, or a cheap ping)
  instead of "Not monitored"; don't show the dashboard itself as tautologically "up".

Deliver: server.py (or a small package), static/ (index.html + css tokens/layout/components + per-panel
JS modules + app.js + panel.js), start.sh/stop.sh, nginx.conf, schema/migration for the SQLite stores,
and a README documenting the credit_log/live_calls_state/recordings contracts with the voice pipeline.
```

---

### Appendix — file map of the existing dashboard
```
dashboard/
  server.py                 FastAPI backend (all endpoints + JSON stores + fs_cli + LLM analysis)
  start.sh / stop.sh        launch under calling-agent venv, port 8080
  nginx.conf                :80 → :8080, SSE-tuned
  *.json                    flat-file stores (contacts, call_link, analysis, credits, key pool,
                            key state, follow_ups, automation, minutes_quota)
  static/
    index.html              SPA shell: 16 nav-items + 16 tab-panels
    js/app.js               navigation, api(), esc(), formatters, polling, theme, notifications
    js/panel.js             conversation slide-over (SSE + REST)
    js/<panel>.js           one module per panel (overview, live-calls, history, recordings,
                            followups, logs, conversations, ai-analysis, conv-analytics, analytics,
                            followup-automation, outbound, sarvam, control, health, agent-settings)
    css/{tokens,layout,components}.css
```
```
```
