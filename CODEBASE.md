# Codebase Reference — LesCall Voice AI Platform

Last updated: 2026-06-19 (dashboard fully redesigned; manual follow-up, Follow-Up Automation, AI Call Analysis, Conversation Analytics, Agent Settings, Recordings, and all prior features complete)

---

## System Overview

Real-time inbound/outbound voice AI platform. Phone calls arrive via FreeSWITCH → WebSocket → AI pipeline. The agent greets callers bilingually (English / Hindi / Gujarati) and drives a configurable sales flow. A separate dashboard server (`dashboard/server.py`) provides operational visibility, runtime control, and intelligence features — it is a pure read-only observer of the calling pipeline, with the exception of placing outbound calls via FreeSWITCH ESL (`fs_cli`).

---

## Repository Layout

```
/home/angelo/lescall/
├── calling-agent/                    ← AI pipeline (venv Python)
│   ├── backend/
│   │   ├── run.py                    ← CLI entrypoint (--web, --test)
│   │   └── app/voice/
│   │       ├── conversation.py       ← ConversationRouter (LLM agent + stage machine)
│   │       ├── pipeline.py           ← 5-task async pipeline
│   │       ├── memory.py             ← CallState, ConversationMemory, ContextMemory
│   │       ├── shortcuts.py          ← deterministic canned-response shortcuts
│   │       ├── call_audit.py         ← WAV + transcript recording
│   │       ├── stt/                  ← SarvamSTT + WhisperSTT + STTRouter
│   │       └── tts_sarvam.py         ← SarvamTTS streaming
│   └── .env                          ← SARVAM_API_KEY, OPENAI_API_KEY, etc.
├── phase14_runtime/
│   ├── start_all.sh / stop_all.sh    ← start/stop socket_server + ws_audio_receiver
│   ├── check_runtime.sh / tail_logs.sh
│   ├── logs/latest/                  ← live log files (SSE streamed by dashboard)
│   └── live_calls_state.json         ← written by pipeline; read by dashboard
├── recordings/                       ← root:root 755; subdirs 755 world-readable
│   └── <folder>/                     ← one dir per call session
│       ├── metadata.json             ← call_id, caller, start_time, end_time, etc.
│       ├── transcript.txt            ← full dialogue (read by AI analysis)
│       └── recording.wav             ← PCM16 16kHz mono (root:root 644)
├── credit_log/                       ← CSV files written by pipeline per call
│   └── YYYY-MM-DD.csv                ← rows: call_id, event, cost, timestamp, ...
├── socket_server.py                  ← ESL controller (system Python, port 9000)
├── ws_audio_receiver.py              ← media WebSocket + AI bridge (venv, port 9001)
├── dashboard/                        ← dashboard server (system Python)
│   ├── server.py                     ← FastAPI app (port 8080)
│   ├── start.sh                      ← `bash start.sh --bg` to launch
│   ├── dashboard.log                 ← dashboard process log
│   ├── static/                       ← SPA frontend
│   │   ├── index.html
│   │   ├── css/
│   │   └── js/                       ← one JS file per dashboard tab/feature
│   └── *.json                        ← dashboard-owned data stores (see below)
└── CODEBASE.md                       ← this file
```

---

## Process Topology (telephony mode)

```
Caller (PSTN)
    │ SIP/RTP PCMU 8kHz
    ▼
FreeSWITCH
    │ ESL (port 9000)
    ▼
socket_server.py            ← ESL controller; handles CHANNEL_* events, triggers
    │                          playback via validated `bgapi originate` / `execute`
    │ WebSocket (port 9001)
    ▼
ws_audio_receiver.py        ← receives PCMU audio, decodes to PCM16, upsamples
    │                          8kHz→16kHz, calls conversation_call_bridge
    ▼
conversation_call_bridge.py ← creates TelephonySession per call; wires
    │                          TelephonySource/Sink + VoicePipeline
    ▼
pipeline.py                 ← 5-task async pipeline (VAD→STT→Router→TTS→Sink)
    │
    └── TTS audio → TelephonySink → TCP port 9004 → socket_server.py → FreeSWITCH
```

### Port Map

| Port | Service | Notes |
|------|---------|-------|
| 9000 | socket_server.py | FreeSWITCH ESL |
| 9001 | ws_audio_receiver.py | media WebSocket |
| 9004 | TelephonySink TCP | TTS audio back to FS |
| 8080 | dashboard/server.py | operational dashboard |

---

## AI Pipeline (pipeline.py)

Five independent async tasks connected by bounded queues:

```
[AudioSource]
  → vad_queue   → [VAD task]            energy-based, self-calibrating
  → stt_queue   → [STT worker]          SarvamSTT primary / WhisperSTT fallback
  → transcript_queue → [ConversationRouter]  deterministic shortcuts → LLM
  → tts_queue   → [TTS player]          SarvamTTS HTTP streaming
  → [AudioSink]                         LocalSpeakerSink (CLI) or TelephonySink
```

Queue bounds prevent backpressure from propagating upstream (slow TTS never blocks audio capture).

### STT Layer

- `STTRouter`: always tries Sarvam first; exponential backoff (60→300s) on HTTP 429, falls back to Whisper. Empty transcripts (no speech) do NOT trigger fallback.
- `SarvamSTT`: multipart WAV POST to `api.sarvam.ai/speech-to-text`, model `saaras:v3`, mode `codemix`. PCM amplitude-normalized before upload.
- `validate_transcript()`: rejects hallucinations (YouTube phrases + Devanagari equivalents), unsupported scripts, Marathi markers, filler words, repetition, and function-word-only fragments (quality score < 0.3).
- Language locking: after `language_probability ≥ 0.75` the BCP-47 code is stored in `_stt_locked_language` and used for all subsequent STT/TTS calls.

### Conversation Engine (conversation.py)

- `ConversationRouter`: stage machine + LLM agent. Stages: `GREETING → DISCOVERY → PROJECT_MATCH → INVITE_VISIT → CONFIRMATION → CLOSING`.
- `CallState` (memory.py): canonical session object — `customer_name`, `customer_phone`, `selected_project`, `selected_location`, `appointment_date/time`, `language`, `conversation_stage`. Injected into every LLM system prompt.
- Deterministic shortcuts (shortcuts.py): common phrases bypass the LLM entirely.
- Project lock: first confirmed project is stored and never overridden.
- **INVARIANT**: `conversation.py` is NEVER imported by the dashboard. Agent Settings reads it via regex on the raw source text only.

### Memory (memory.py)

Three layers injected into each LLM call:
1. `ConversationMemory` — sliding window of last 5 turns.
2. `ContextMemory` — extracted `user_name`, `intent`, `topic`, `sentiment`.
3. `CallState` — structured booking fields (ground truth for what has been confirmed).

---

## Call Audit (call_audit.py)

When `ENABLE_CALL_AUDIT=true`, each call produces:
- `recordings/<folder>/recording.wav` — PCM16 16kHz mono, root:root 644 (world-readable)
- `recordings/<folder>/metadata.json` — call_id, caller, start_time, end_time, duration
- `recordings/<folder>/transcript.txt` — full dialogue

Recording folders are root:root 755 (world-readable, not writable by dashboard process). Dashboard never writes into recording folders — all its own data goes to `dashboard/*.json`.

---

## Dashboard Server (dashboard/server.py)

**Framework**: FastAPI + Starlette (port 8080)
**Auth**: none (single-tenant, internal)
**Pattern**: stateless read-mostly; writes only to dashboard-owned JSON files
**Frontend**: vanilla JS SPA served from `static/index.html`

### Critical design rules

1. Never import or execute `conversation.py`, `socket_server.py`, `ws_audio_receiver.py`, or `call_audit.py`.
2. Never write into the `recordings/` tree or `credit_log/` directory.
3. Never modify live calling pipeline config.
4. Outbound calls (and follow-up automation calls) use `fs_cli -x "originate ..."` — the same mechanism as manual Outbound Calls. The live conversation engine is untouched.

### Environment variables (dashboard)

| Variable | Default | Purpose |
|----------|---------|---------|
| `OUTBOUND_CALLER_ID` | `912264231897` | Outbound caller ID sent to FreeSWITCH |
| `OUTBOUND_GATEWAY` | `16584853209835561.zt.plivo.com` | SIP gateway |
| `DASHBOARD_ANALYSIS_MODEL` | `gpt-4o-mini` | LLM model for on-demand AI Call Analysis |

---

## Dashboard Data Stores (JSON files)

All files live in `/home/angelo/lescall/dashboard/`. Never written by the calling pipeline.

| File | Schema | Purpose |
|------|--------|---------|
| `outbound_contacts.json` | `[{id, name, phone, disposition, notes, call_notes, last_called_at, ...}]` | Outbound contact list |
| `sarvam_credits.json` | `{credit_balance, updated_at, ...}` | Cached Sarvam API credit balance |
| `api_key_pool.json` | `[{key, label, ...}]` | Sarvam API key pool |
| `api_key_state.json` | `{active_key, ...}` | Which key is currently active |
| `call_link.json` | `[{phone, contact_id, name, dialed_at}]` | Ring-buffer (max 5000) of dial events; used for inbound/outbound classification heuristic |
| `call_analysis.json` | `{folder: analysis_object}` | On-demand AI Call Analysis results keyed by recording folder |
| `follow_ups.json` | `{entry_id: {status, updated_at}}` | Status overrides for outbound/AI-analysis follow-up entries |
| `manual_followups.json` | `[{id, contact_name, contact_phone, reason, follow_up_date, priority, call_id, status, created_at, updated_at}]` | Manually created follow-ups from Call Activity |
| `follow_up_automation.json` | `[{id, source_id, contact_name, contact_phone, past_call_context, call_aim, script_notes, scheduled_time, linked_contact_id, status, created_at, called_at, call_outcome}]` | Follow-Up Automation schedule |
| `minutes_quota.json` | `{total_minutes, updated_at}` | Operator-set minutes quota (created on first save) |

---

## API Endpoints

### Runtime

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/runtime/status` | Start/stop/PID status of socket_server + ws_audio_receiver |
| POST | `/api/runtime/start` | Start all services via `start_all.sh` |
| POST | `/api/runtime/stop` | Stop all services via `stop_all.sh` |
| POST | `/api/runtime/restart` | Stop then start |
| GET | `/api/runtime/output/stream` | SSE stream of command output |

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Process liveness, log file freshness |

### Overview / KPIs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/overview?period=today\|week\|month` | KPIs (total/answered/missed/inbound/outbound/appointments/followups/minutes), minutes_quota, charts (calls_over_time, call_type_distribution, call_status_distribution) |
| GET | `/api/minutes/quota` | Current quota setting |
| POST | `/api/minutes/quota` | Set `{total_minutes}` |

### Live Calls

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/calls/active` | Active calls from `live_calls_state.json` (stale if no call_end within 20 min) |
| GET | `/api/calls/live-state` | Full JSON of `live_calls_state.json` |
| GET | `/api/calls/live-state/stream` | SSE stream, polls every 3s |

### Call Activity / History

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/calls/history?page&limit&search&days&call_type&status` | Paginated call log from credit_log CSV files; merged with call_link.json for direction, outbound_contacts.json for contact name. Fields: all original credit_log columns + call_type, status, contact_name, contact_phone, duration_seconds |

### Recordings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/calls/recordings?page&limit&search` | List recording folders with metadata |
| GET | `/api/calls/recordings/{folder}/transcript` | Metadata + full transcript messages |
| GET | `/api/calls/recordings/{folder}/audio` | FileResponse with HTTP 206 Range support for browser audio player; `?download=1` for download |
| GET | `/api/calls/transcripts/search?q=` | Server-side full-text search across all transcript.txt files |

### AI Call Analysis (on-demand)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/analysis/eligible` | Folders with a completed transcript but not yet analyzed |
| GET | `/api/calls/recordings/{folder}/analysis` | Cached analysis result from `call_analysis.json` |
| POST | `/api/analysis/run` | `{folders: [...]}` — runs LLM analysis for each; model=gpt-4o-mini; JSON mode. Results cached in `call_analysis.json`. Never re-analyzed unless explicitly re-run. |

Analysis schema (stored in `call_analysis.json[folder]`):
```json
{
  "summary": "", "customer_intent": "", "sentiment": "",
  "outcome": "", "objections": [], "topics": [], "products_mentioned": [],
  "customer_questions": [], "appointments_scheduled": false,
  "follow_up_needed": false, "follow_up_reason": "", "follow_up_priority": "",
  "recommended_follow_up_date": "", "suggested_next_action": "",
  "ai_recommendations": [], "_analyzed_at": ""
}
```

### Conversation Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/analytics/conversation` | Aggregates all `call_analysis.json` entries; returns lead_intelligence, sentiment distribution, objections, topics, products, customer_questions, ai_recommendations, trends table |

### Agent Settings (read-only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent/config` | Regex-extracted sections from `conversation.py` source text (never imported). Returns: name, tone, greeting, identity, guidelines, rules, portfolio, objections. Read-only — pipeline is never modified. |

### Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/analytics` | Legacy analytics: calls_today, avg_duration, conversion rate, disposition breakdown, recent recordings |

### Follow-Up Tracker

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/followups?status=all\|pending\|completed\|missed` | Merged list from three sources: outbound contacts with disposition `to_contact_later`/`in_doubt`, AI-analyzed calls flagged `follow_up_needed`, and manual entries from `manual_followups.json`. Status overrides from `follow_ups.json` (for outbound/ai sources) or direct field (for manual). |
| PATCH | `/api/followups/{entry_id}` | Update status (`pending`/`completed`/`missed`). For `manual:` IDs updates `manual_followups.json` directly; for others updates `follow_ups.json` override map. |
| POST | `/api/followups/manual` | Create manual follow-up: `{contact_name?, contact_phone*, reason?, follow_up_date?, priority?, call_id?}` |
| PATCH | `/api/followups/manual/{id}` | Edit manual entry fields |
| DELETE | `/api/followups/manual/{id}` | Remove manual entry |

**Entry ID format**:
- `contact:{outbound_contact_id}` — from outbound disposition
- `call:{recording_folder}` — from AI Call Analysis
- `manual:{uuid12}` — manually created from Call Activity

### Follow-Up Automation

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/followup-automation` | List all automation entries sorted by scheduled_time |
| POST | `/api/followup-automation` | `{followup_id}` — create entry; resolves context from follow-up source; deduplicates (409 if already scheduled for same ID) |
| PATCH | `/api/followup-automation/{id}` | Update `scheduled_time`, `call_aim`, `script_notes`, or `status` |
| DELETE | `/api/followup-automation/{id}` | Remove entry |
| POST | `/api/followup-automation/{id}/call-now` | Place real outbound call immediately via FreeSWITCH `fs_cli`; updates status/outcome |

Automation scheduler: `asyncio` background loop (started on app startup, polls every 30s). Places calls for entries where `status=scheduled` and `scheduled_time ≤ now`. Dialing: `fs_cli -x "originate sofia/gateway/{gateway}/{phone} &transfer({ext} {ctx})"`. Result parsing detects `+OK UUID` vs error strings.

### Outbound Calls & Campaign

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/outbound/contacts?page&limit&search&disposition` | Paginated outbound contact list |
| POST | `/api/outbound/contacts` | Add single contact |
| POST | `/api/outbound/contacts/upload` | CSV bulk import |
| DELETE | `/api/outbound/contacts/all` | Clear all contacts |
| DELETE | `/api/outbound/contacts/{id}` | Delete one contact |
| PATCH | `/api/outbound/contacts/{id}` | Update contact fields |
| GET | `/api/outbound/stats` | Disposition breakdown counts |
| POST | `/api/outbound/dial/{contact_id}` | Dial immediately; updates disposition; appends to `call_link.json` |
| GET | `/api/outbound/campaign/status` | Campaign running status, progress, current contact |
| POST | `/api/outbound/campaign/start` | Start sequential auto-dialer with configurable delay |
| POST | `/api/outbound/campaign/stop` | Stop campaign |

### Sarvam API Credits (Finance)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sarvam/credits` | Cached credit balance + threshold alert |
| POST | `/api/sarvam/credits` | `{credit_balance}` — manual override |
| GET | `/api/sarvam/keys` | List API key pool |
| POST | `/api/sarvam/keys/replace` | Replace active key |
| POST | `/api/sarvam/keys/bulk` | Add multiple keys |
| DELETE | `/api/sarvam/keys/pool` | Clear key pool |
| POST | `/api/sarvam/keys/ack-restart` | Acknowledge pending restart |
| GET | `/api/sarvam/usage` | Recent credit_log aggregated by day |
| GET | `/api/sarvam/usage/history` | Historical usage series |

### Logs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/logs/stream` | SSE stream of live log file tail (both socket_server + ws_audio_receiver) |
| GET | `/api/logs/tail?lines=N` | Last N lines of log files |

---

## Frontend (static/index.html + static/js/)

Single-page application. All tabs are rendered in `static/index.html`; each tab has a corresponding JS file. Navigation is handled by `app.js`.

### Navigation Structure

**Monitoring**
- Overview (`overview.js`) — KPI cards (9 metrics with ptabs today/week/month) + 3 bar charts + runtime health
- Live Calls (`live-calls.js`) — SSE-driven active call list
- Call Activity (`history.js`) — paginated call log with filters + "⏰ Follow-Up" action per row
- Recordings (`recordings.js`) — audio player + download link per row
- Follow-Up Tracker (`followups.js`) — merged follow-up list; status dropdown + ⚡ Automate + 🗑 delete (manual entries only)
- Live Logs (`logs.js`) — SSE-driven log tail

**Intelligence**
- Call Transcripts (`conversations.js`) — searchable transcript list; inline slide panel
- AI Call Analysis (`ai-analysis.js`) — checkbox selection, on-demand analysis run, full results display
- Conversation Analytics (`conv-analytics.js`) — aggregated insights from all analyses
- Analytics (`analytics.js`) — legacy per-day stats
- Follow-Up Automation (`followup-automation.js`) — editable automation schedule; Call Now / cancel / delete

**Campaigns**
- Outbound Calls (`outbound.js`) — contact list, dial, campaign runner

**Finance**
- API Usage (`sarvam.js`) — credit balance, key pool, usage history

**System**
- Runtime Control (`control.js`) — start/stop/restart buttons + live output stream
- Health (`health.js`) — process liveness indicators
- Agent Settings (`agent-settings.js`) — read-only extracted agent config from `conversation.py`

### Shared JS utilities (app.js)

- `api(path)` — fetch wrapper, returns JSON, throws on non-2xx
- `esc(s)` — HTML-escape
- `fmtTs(iso)` — format ISO timestamp
- `fmtDur(seconds)` — format duration as mm:ss or Xm Ys
- `R(n, decimals)` — round to decimals
- `shortId(id)` — truncate call ID for display
- `sortRows(rows, key, dir)` — in-memory sort
- `bindSortableHeaders(thead, cb)` — click-to-sort
- `pushNotification(msg, type)` — top-right toast
- `switchTab(tabId)` — navigate to tab programmatically
- `renderHbarList(data, elId)` — horizontal bar list for analytics

### Add to Follow-Up modal

Defined inline in `index.html` (`#addfu-backdrop`). Functions `openAddFuModal(call)`, `closeAddFuModal()`, `submitAddFuModal()` live in `history.js`. Pre-fills contact name/phone from the Call Activity row. Posts to `POST /api/followups/manual`.

---

## Key Design Decisions and Limitations

### Inbound/Outbound Direction Classification

`credit_log` CSVs have no `direction` field. The dashboard infers direction using a timestamp-based heuristic:

1. When a manual dial or campaign dial fires, `_record_dial()` appends `{phone, dialed_at}` to `call_link.json` (ring-buffer, max 5000 entries).
2. `_classify_direction(call_start_time)` searches `call_link.json` for the closest entry whose `dialed_at` is within ±90 seconds of the call start. If found → `outbound`; otherwise → `inbound`.

**Limitation**: calls placed before the dashboard was deployed (or before `call_link.json` had entries) are classified as `inbound` by default. This is a forward-looking heuristic only — historical calls cannot be retroactively classified.

### On-Demand AI Call Analysis

- Analysis is triggered only when the user selects a recording in the AI Call Analysis tab and clicks "Run Analysis" — it is never automatic.
- The dashboard reads `transcript.txt` from disk and sends it to an OpenAI-compatible API (model: `gpt-4o-mini`, JSON mode).
- Results are cached in `call_analysis.json` (keyed by folder name). Re-running the same folder re-bills and overwrites the cached result.
- Recording folders are root:root — the dashboard cannot create files inside them. Analysis results are stored in the dashboard-owned `call_analysis.json` instead.

### Follow-Up Tracker — Three Sources

1. **Outbound disposition** (`contact:` prefix): contacts in `outbound_contacts.json` with `disposition in ("to_contact_later", "in_doubt")`. Status overrides in `follow_ups.json`.
2. **AI Call Analysis** (`call:` prefix): analyzed calls with `follow_up_needed: true`. Status overrides in `follow_ups.json`.
3. **Manual** (`manual:` prefix): entries in `manual_followups.json` created by operator from Call Activity. Status stored directly on the entry (not in the override map).

### Follow-Up Automation

- Creating an automation entry resolves context from the source (`_build_followup_context`): outbound disposition → contact notes; AI analysis → call summary/reason; manual → stored reason/date/call_id.
- Automation only places the call — it does NOT alter what the agent says. Script notes and call aim are for the operator's reference only (shown in the dashboard, not injected into the live conversation engine).
- Scheduler polls every 30s via asyncio background loop started on app `startup` event.
- Dialing reuses the exact same FreeSWITCH `originate` command as manual outbound dials.

### Agent Settings (Read-Only)

The dashboard extracts agent name, tone, greeting, and prompt sections from `conversation.py` using regex over the raw source text. The module is **never imported**. There is no write path — editing agent settings via the dashboard is intentionally out of scope (changes to the live agent require direct code edits and a pipeline restart).

### Known Data Gaps

- **Inbound missed calls**: not tracked — `credit_log` only records calls that reached the AI pipeline. Abandoned calls before connection appear in FreeSWITCH CDR but not here.
- **Caller phone number**: `metadata.json` does not store the caller's PSTN number. For recent calls, the recording folder name often IS the phone number (e.g., `919599761331_20260619_061410`), but this is not guaranteed for older calls.
- **CallState not persisted**: session state (`CallState`) lives in memory during a call and is not written to disk after the call ends. Post-call data available: transcript, metadata, credit_log row.
- **Minutes accounting**: minutes used is estimated from recording durations (if available) or credit_log timestamps. The quota is set manually by the operator — there is no automatic sync with the telephony provider's billing.

---

## Running the System

### Start All Services
```bash
cd /home/angelo/lescall
bash phase14_runtime/start_all.sh
```

### Dashboard Only
```bash
cd /home/angelo/lescall/dashboard
bash start.sh --bg          # starts in background, logs to dashboard.log
bash start.sh               # foreground
```

### Stop / Monitor
```bash
bash phase14_runtime/stop_all.sh
bash phase14_runtime/tail_logs.sh
bash phase14_runtime/check_runtime.sh
```

### AI Pipeline Only (CLI test, no telephony)
```bash
cd /home/angelo/lescall/calling-agent
source venv/bin/activate
cd backend
python run.py               # mic → speaker
python run.py --web         # + local web console at http://127.0.0.1:8000
python run.py --test        # single-turn test, no mic
```

---

## Invariants — Never Violate

1. `socket_server.py`, `ws_audio_receiver.py`, `conversation.py`, `call_audit.py` are NEVER imported or modified by the dashboard.
2. The dashboard never writes into `recordings/` or `credit_log/`.
3. `socket_server.py` runs under **system Python**; `ws_audio_receiver.py` and all `calling-agent/` code runs under `calling-agent/venv`. Do not mix.
4. All audio in the AI pipeline is 16-bit PCM at 16 kHz. FreeSWITCH delivers PCMU at 8 kHz; `ws_audio_receiver.py` handles decode and upsample.
5. `SARVAM_API_KEY` is the correct env var name (not `SARVAMAI_API_KEY`).
6. The greeting fires only after the first audio frame is received (`signal_call_active()` gate) — not at WebSocket open — to avoid burning TTS credits on abandoned connections.
7. Follow-Up Automation places real calls via FreeSWITCH — always confirm before using "Call Now" on a production system.
