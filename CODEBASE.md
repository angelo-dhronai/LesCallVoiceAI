# Codebase Reference — LesCall Voice AI Platform

Last updated: 2026-06-29 (multi-agent registry + RAG KB, WS STT/TTS, TurnManager, CreditTracker, watchdog, runtime_guard; TTS fix: remove enable_preprocessing for bulbul:v3)

---

## System Overview

Real-time inbound/outbound voice AI platform. Phone calls arrive via FreeSWITCH → WebSocket → AI pipeline. The agent greets callers bilingually (English / Hindi / Gujarati) and drives a configurable sales flow. A separate dashboard server (`dashboard/server.py`) provides operational visibility, runtime control, and intelligence features — it is a pure read-only observer of the calling pipeline, with the exception of placing outbound calls via FreeSWITCH ESL (`fs_cli`).

---

## Repository Layout

```
/home/angelo/lescall/
├── calling-agent/                    ← AI pipeline (venv Python)
│   ├── agents/                       ← Multi-agent storage (persona + KB per agent)
│   │   ├── registry.json             ← Which agent is active; list of all agents
│   │   └── <agent_id>/               ← One dir per agent (e.g. vishal_default/)
│   │       ├── persona.json          ← Agent identity/brand/portfolio fields
│   │       ├── knowledge/            ← KB docs + chunks.jsonl
│   │       │   └── raw/              ← Uploaded source files (PDF, DOCX, TXT, MD)
│   │       └── index/                ← Numpy embedding matrix for fast RAG lookup
│   ├── backend/
│   │   ├── run.py                    ← CLI entrypoint (--web, --test)
│   │   └── app/
│   │       ├── agent/                ← Multi-agent registry + KB management
│   │       │   ├── registry.py       ← CRUD: create/update/delete agents, load persona
│   │       │   └── knowledge.py      ← Ingestion (PDF/DOCX/TXT→chunks→embeddings) + RAG query
│   │       └── voice/
│   │           ├── conversation.py       ← ConversationRouter (LLM agent + stage machine)
│   │           ├── conversation_engine.py / conversation_engine_llm.py ← LLM prompt builder
│   │           ├── pipeline.py           ← 5-task async pipeline
│   │           ├── memory.py             ← CallState, ConversationMemory, ContextMemory
│   │           ├── shortcuts.py          ← deterministic canned-response shortcuts
│   │           ├── turn_manager.py       ← Intelligent turn-taking (silence + semantic)
│   │           ├── credit_tracker.py     ← Per-call STT/TTS cost tracking → credit_log/
│   │           ├── tracking_stt.py       ← SarvamSTT + WhisperSTT wrappers (credit tracking)
│   │           ├── tracking_tts.py       ← SarvamTTS wrapper (credit tracking)
│   │           ├── tts_sarvam.py         ← SarvamTTS HTTP streaming (production TTS)
│   │           ├── tts_sarvam_ws.py      ← SarvamWSTTS WebSocket TTS (alternative; see note)
│   │           ├── tts_cached.py / tts_cartesia.py ← other TTS backends
│   │           ├── vad.py                ← energy-based VAD, self-calibrating
│   │           ├── call_audit.py         ← WAV + transcript recording
│   │           ├── event_hub.py          ← async pub/sub for web console events
│   │           └── stt/
│   │               ├── router.py         ← STTRouter: Sarvam primary, Whisper fallback
│   │               ├── sarvam.py         ← SarvamSTT HTTP (production STT)
│   │               ├── sarvam_ws.py      ← SarvamWSSTT WebSocket STT (alternative; see note)
│   │               ├── openai_whisper.py ← OpenAI Whisper fallback
│   │               ├── post_processor.py ← transcript validation/cleanup
│   │               └── base.py           ← STTProvider base class + audio normalization
│   └── .env                          ← SARVAM_API_KEY, OPENAI_API_KEY, etc.
├── phase14_runtime/
│   ├── start_all.sh / stop_all.sh    ← start/stop socket_server + ws_audio_receiver
│   ├── check_runtime.sh / tail_logs.sh
│   ├── watchdog.sh                   ← auto-healing watchdog (run from cron, every 1 min)
│   ├── logs/latest/                  ← live log files (SSE streamed by dashboard)
│   └── live_calls_state.json         ← written by pipeline; read by dashboard
├── recordings/                       ← root:root 755; subdirs 755 world-readable
│   └── <folder>/                     ← one dir per call session
│       ├── metadata.json             ← call_id, caller, start_time, end_time, etc.
│       ├── transcript.txt            ← full dialogue (read by AI analysis)
│       └── recording.wav             ← PCM16 16kHz mono (root:root 644)
├── credit_log/                       ← JSONL files written by pipeline per call
│   └── YYYY-MM-DD.jsonl              ← lines: call_start, stt_event, tts_event, call_end
├── socket_server.py                  ← ESL controller (system Python, port 9000)
├── ws_audio_receiver.py              ← media WebSocket + AI bridge (venv, port 9001)
├── conversation_call_bridge.py       ← TelephonySession: wires Source/Sink + VoicePipeline
├── runtime_guard.py                  ← root-process guard + shared runtime dir bootstrap
├── call_audit.py                     ← WAV + transcript recorder (top-level, called by bridge)
├── dashboard/                        ← dashboard server (venv Python)
│   ├── server.py                     ← FastAPI app (port 8080)
│   ├── start.sh                      ← `bash start.sh --bg` to launch
│   ├── dashboard.log / dashboard.pid ← dashboard process log + PID
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
  → vad_queue   → [VADLayer]            energy-based, self-calibrating
  → stt_queue   → [STTWorker]           TrackingSarvamSTT primary / TrackingWhisperSTT fallback
  → transcript_queue → [ConversationRouter]  deterministic shortcuts → LLM
  → tts_queue   → [TTSPlayer]           TrackingSarvamTTS HTTP streaming
  → [AudioSink]                         LocalSpeakerSink (CLI) or TelephonySink (telephony)
```

Queue bounds prevent backpressure from propagating upstream (slow TTS never blocks audio capture).

### TurnManager (turn_manager.py)

`TurnManager` decides when the caller has finished speaking. Fires only when BOTH:
1. Silence exceeds `silence_threshold_ms` (1400ms in telephony mode), AND
2. Accumulated text is semantically complete (ends with punctuation OR word count ≥ threshold)

An additional `grace_window_ms` (300ms) absorbs trailing fragments. There is also a hard-flush path: `HARD_FLUSH_SILENCE_FRAMES` consecutive silent frames trigger a send regardless of turn state (prevents the agent going deaf on a very quiet line).

### CreditTracker (credit_tracker.py)

`SarvamCreditTracker` accumulates per-call STT/TTS usage and writes JSONL records to `credit_log/<YYYY-MM-DD>.jsonl`. Record types:

```jsonl
{"type": "call_start", "call_id": "...", "ts": "ISO"}
{"type": "stt_event",  "call_id": "...", "ts": "ISO", "provider": "sarvam|whisper", "audio_seconds": float}
{"type": "tts_event",  "call_id": "...", "ts": "ISO", "chars": int, "completed": bool}
{"type": "call_end",   "call_id": "...", "ts": "ISO", ...aggregated fields...}
```

Pricing defaults: STT ₹0.5/min, TTS ₹3.0/1K chars (configurable via `SARVAM_STT_PRICE_PER_MIN`, `SARVAM_TTS_PRICE_PER_1K_CHARS` in `.env`).

### STT Layer

- `STTRouter`: always tries Sarvam first; exponential backoff (60→300s) on HTTP 429, falls back to Whisper. Empty transcripts (no speech) do NOT trigger fallback.
- `TrackingSarvamSTT` / `TrackingWhisperSTT`: thin subclasses of `SarvamSTT` / `OpenAIWhisperSTT` that call `CreditTracker.record_stt()` after each successful transcription.
- `SarvamSTT`: multipart WAV POST to `api.sarvam.ai/speech-to-text`, model `saaras:v3`, mode `codemix`. PCM amplitude-normalized before upload. Returns `language_probability` used for language locking.
- `validate_transcript()` (post_processor.py): rejects hallucinations, unsupported scripts, Marathi markers, filler words, repetition, and function-word-only fragments (quality score < 0.3).
- Language locking: after `language_probability ≥ 0.75` the BCP-47 code is stored in `_stt_locked_language` and used for all subsequent STT/TTS calls.
- **Alternative**: `SarvamWSSTT` (`stt/sarvam_ws.py`) — persistent WebSocket connection to `wss://api.sarvam.ai/speech-to-text/ws`. Not used by the main pipeline (which goes through `STTRouter`); available for direct use.

### Conversation Engine (conversation.py)

- `ConversationRouter`: stage machine + LLM agent. Stages: `GREETING → DISCOVERY → PROJECT_MATCH → INVITE_VISIT → CONFIRMATION → CLOSING`.
- `CallState` (memory.py): canonical session object — `customer_name`, `customer_phone`, `selected_project`, `selected_location`, `appointment_date/time`, `language`, `conversation_stage`. Injected into every LLM system prompt.
- Persona loaded from active agent via `agent.registry.load_persona()` at conversation init.
- Deterministic shortcuts (shortcuts.py): common phrases bypass the LLM entirely.
- Project lock: first confirmed project is stored and never overridden.
- **INVARIANT**: `conversation.py` is NEVER imported by the dashboard's legacy `/api/agent/config` endpoint. That endpoint reads it via regex on raw source text only.

### Memory (memory.py)

Three layers injected into each LLM call:
1. `ConversationMemory` — sliding window of last 5 turns.
2. `ContextMemory` — extracted `user_name`, `intent`, `topic`, `sentiment`.
3. `CallState` — structured booking fields (ground truth for what has been confirmed).

### TTS Layer

- `SarvamTTS` (`tts_sarvam.py`): HTTP streaming to `api.sarvam.ai/text-to-speech/stream`, model `bulbul:v3`, speaker `shubh`. Audio chunks written to `AudioSink` as they arrive (true streaming). **Production TTS**.
- `TrackingSarvamTTS` (`tracking_tts.py`): subclass of `SarvamTTS`; calls `CreditTracker.record_tts()` after each `speak()`. Used in telephony mode.
- **Alternative**: `SarvamWSTTS` (`tts_sarvam_ws.py`) — persistent WebSocket TTS to `wss://api.sarvam.ai/text-to-speech/ws`. Eliminates per-utterance TCP/TLS reconnect overhead. **Known fix**: when using `bulbul:v3`, do NOT include `enable_preprocessing` in the config message — it is only valid for `bulbul:v2` and causes a 422 error. This has been fixed in the codebase.

---

## Multi-Agent System (agent/registry.py + agent/knowledge.py)

### Registry

`calling-agent/agents/registry.json` tracks which agent is active. Each agent has:
- A `persona.json` with editable fields: `agent_name`, `role_line`, `identity`, `brand`, `portfolio`, `default_language`, `kb_enabled`.
- A `knowledge/` directory for uploaded source docs and chunked text.
- An `index/` directory with a pre-built embedding matrix for in-RAM cosine similarity search.

`agent.registry` module provides:
- `ensure_default_agent()` — idempotent migration: seeds "vishal_default" if registry is empty.
- `load_persona(agent_id)` — loads persona with fallback to `DEFAULT_PERSONA` for missing keys.
- Full CRUD: `create_agent`, `update_persona`, `set_active`, `delete_agent`.
- Stdlib-only imports — safe to import in BOTH the venv pipeline and the dashboard.

**Backward compatibility**: if `registry.json` doesn't exist, `load_persona(None)` returns `DEFAULT_PERSONA` (the exact strings from `conversation.py`). The single-agent behaviour is fully preserved.

### Knowledge Base (RAG)

`agent.knowledge` module:
- **Ingestion** (`ingest_cli.py` or dashboard upload): PDF/DOCX/TXT/MD → text → 800-char chunks (150-char overlap) → embeddings via `all-MiniLM-L6-v2` (SentenceTransformer) → `.npz` matrix + `chunks.jsonl`.
- **Query** (`KnowledgeIndex.query(text)`): single BLAS matmul (cosine on L2-normalized vectors), TOP_K=3, MIN_SIM=0.18. ~10–20ms, no network.
- Embedding model loaded lazily as a singleton into RAM.

---

## Watchdog (phase14_runtime/watchdog.sh)

Added after the 2026-06-27 outage where `socket_server.py` died silently.

- **Run from cron** once per minute (see `install_watchdog_cron.sh`).
- **Kill-switch**: `touch phase14_runtime/watchdog.disabled` pauses it.
- **Cooldown**: won't restart more than once per 180s to avoid restart loops.
- **Active-call guard**: won't restart while a call is in progress (reads `live_calls_state.json`), UNLESS the media port (9001) itself is also down.
- **Refuses to run as root** (mirrors `runtime_guard.py` policy).
- **Restart log**: `phase14_runtime/logs/watchdog_restart.log`.

---

## Runtime Guard (runtime_guard.py)

Shared startup safety module imported by `socket_server.py` and `ws_audio_receiver.py` at the top of `main()`.

Two guards:
1. `assert_not_root(service_name)` — aborts with `SystemExit(1)` if `euid == 0`. Prevents root-owned files from poisoning shared runtime dirs.
2. `bootstrap_runtime_dirs(extra=())` — creates and `chmod 0o777`s all shared dirs (`/tmp/phase15_audio_debug`, `/tmp/esl_recordings`, `/tmp/ai_telephony_playback`) on startup, ensuring both the service user and freeswitch (uid 999) can always write them.

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
**Process**: runs under `calling-agent/venv` python (same venv as pipeline, for shared `agent.*` imports)  
**Auth**: none (single-tenant, internal)  
**Pattern**: stateless read-mostly; writes only to dashboard-owned JSON files  
**Frontend**: vanilla JS SPA served from `static/index.html`

### Critical design rules

1. Never import or execute `socket_server.py`, `ws_audio_receiver.py`, or `call_audit.py`.
2. Never write into the `recordings/` tree or `credit_log/` directory.
3. Never modify live calling pipeline config.
4. Outbound calls (and follow-up automation calls) use `fs_cli -x "originate ..."` — the same mechanism as manual Outbound Calls. The live conversation engine is untouched.
5. The legacy `/api/agent/config` endpoint reads `conversation.py` via regex on raw source text — it never imports it.

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
| GET | `/api/calls/history?page&limit&search&days&call_type&status` | Paginated call log from credit_log JSONL files; merged with call_link.json for direction, outbound_contacts.json for contact name |

### Recordings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/calls/recordings?page&limit&search` | List recording folders with metadata |
| GET | `/api/calls/recordings/{folder}/transcript` | Metadata + full transcript messages |
| GET | `/api/calls/recordings/{folder}/audio` | FileResponse with HTTP 206 Range support; `?download=1` for download |
| GET | `/api/calls/transcripts/search?q=` | Server-side full-text search across all transcript.txt files |

### AI Call Analysis (on-demand)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/analysis/eligible` | Folders with a completed transcript but not yet analyzed |
| GET | `/api/calls/recordings/{folder}/analysis` | Cached analysis result from `call_analysis.json` |
| POST | `/api/analysis/run` | `{folders: [...]}` — runs LLM analysis; JSON mode. Results cached in `call_analysis.json`. |

### Conversation Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/analytics/conversation` | Aggregates all `call_analysis.json` entries; returns lead_intelligence, sentiment, objections, topics, products, customer_questions, ai_recommendations, trends |

### Agent Settings (Legacy Read-Only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent/config` | Regex-extracted sections from `conversation.py` source text (never imported). Returns: name, tone, greeting, identity, guidelines, rules, portfolio, objections. Read-only snapshot. |

### Multi-Agent Registry & Knowledge Base

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agents` | List all agents + active_agent_id |
| POST | `/api/agents` | Create agent: `{name}` |
| GET | `/api/agents/{agent_id}` | Get agent detail + persona + KB summary |
| PATCH | `/api/agents/{agent_id}` | Update persona fields (agent_name, role_line, identity, brand, portfolio, default_language, kb_enabled) |
| POST | `/api/agents/{agent_id}/activate` | Set as active agent |
| DELETE | `/api/agents/{agent_id}` | Delete agent (cannot delete active or only agent) |
| GET | `/api/agents/{agent_id}/kb` | List KB documents (doc_count, n_chunks, docs list) |
| POST | `/api/agents/{agent_id}/kb/upload` | Upload PDF/DOCX/TXT/MD; triggers async ingest + reindex |
| DELETE | `/api/agents/{agent_id}/kb/{doc_id}` | Remove a KB document |
| POST | `/api/agents/{agent_id}/kb/reindex` | Re-run embedding + index build for all docs |

### Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/analytics` | Legacy analytics: calls_today, avg_duration, conversion rate, disposition breakdown, recent recordings |

### Follow-Up Tracker

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/followups?status=all\|pending\|completed\|missed` | Merged list from three sources: outbound disposition, AI-analyzed calls, manual entries |
| PATCH | `/api/followups/{entry_id}` | Update status |
| POST | `/api/followups/manual` | Create manual follow-up |
| PATCH | `/api/followups/manual/{id}` | Edit manual entry |
| DELETE | `/api/followups/manual/{id}` | Remove manual entry |

### Follow-Up Automation

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/followup-automation` | List all automation entries |
| POST | `/api/followup-automation` | `{followup_id}` — create entry; deduplicates |
| PATCH | `/api/followup-automation/{id}` | Update `scheduled_time`, `call_aim`, `script_notes`, `status` |
| DELETE | `/api/followup-automation/{id}` | Remove entry |
| POST | `/api/followup-automation/{id}/call-now` | Place real outbound call immediately |

Automation scheduler: `asyncio` background loop polling every 30s. Places calls for entries where `status=scheduled` and `scheduled_time ≤ now`.

### Outbound Calls & Campaign

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/outbound/contacts?page&limit&search&disposition` | Paginated contact list |
| POST | `/api/outbound/contacts` | Add single contact |
| POST | `/api/outbound/contacts/upload` | CSV bulk import |
| DELETE | `/api/outbound/contacts/all` | Clear all contacts |
| DELETE | `/api/outbound/contacts/{id}` | Delete one contact |
| PATCH | `/api/outbound/contacts/{id}` | Update contact fields |
| GET | `/api/outbound/stats` | Disposition breakdown counts |
| POST | `/api/outbound/dial/{contact_id}` | Dial immediately |
| GET | `/api/outbound/campaign/status` | Campaign status |
| POST | `/api/outbound/campaign/start` | Start sequential auto-dialer |
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
| GET | `/api/logs/stream` | SSE stream of live log file tail |
| GET | `/api/logs/tail?lines=N` | Last N lines of log files |

---

## Frontend (static/index.html + static/js/)

Single-page application. Navigation handled by `app.js`.

### Navigation Structure

**Monitoring**
- Overview (`overview.js`) — KPI cards (9 metrics with tabs today/week/month) + 3 bar charts + runtime health
- Live Calls (`live-calls.js`) — SSE-driven active call list with stage/language indicators
- Call Activity (`history.js`) — paginated call log with filters + "⏰ Follow-Up" action per row
- Recordings (`recordings.js`) — audio player + download link per row
- Follow-Up Tracker (`followups.js`) — merged follow-up list; status dropdown + ⚡ Automate + 🗑 delete (manual only)
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
- Agent Settings (`agent-settings.js`) — multi-agent registry management: persona editing, KB upload/delete/reindex, agent create/activate/delete. Also includes legacy read-only extracted agent config.

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

---

## Key Design Decisions and Limitations

### Inbound/Outbound Direction Classification

`credit_log` JSONL files have no `direction` field. The dashboard infers direction using a timestamp-based heuristic:

1. When a manual dial or campaign dial fires, `_record_dial()` appends `{phone, dialed_at}` to `call_link.json` (ring-buffer, max 5000 entries).
2. `_classify_direction(call_start_time)` searches `call_link.json` for the closest entry within ±90 seconds.

**Limitation**: calls before the dashboard was deployed are classified as `inbound` by default.

### On-Demand AI Call Analysis

- Triggered only when the user clicks "Run Analysis" — never automatic.
- Reads `transcript.txt` from disk, sends to OpenAI-compatible API (model: `gpt-4o-mini`, JSON mode).
- Results cached in `call_analysis.json` (keyed by folder). Re-running overwrites and re-bills.
- Dashboard cannot write into `recordings/` folders. Analysis results stored in `call_analysis.json`.

### Follow-Up Tracker — Three Sources

1. **Outbound disposition** (`contact:` prefix): contacts with `disposition in ("to_contact_later", "in_doubt")`. Status overrides in `follow_ups.json`.
2. **AI Call Analysis** (`call:` prefix): analyzed calls with `follow_up_needed: true`. Status overrides in `follow_ups.json`.
3. **Manual** (`manual:` prefix): entries in `manual_followups.json`. Status stored directly on the entry.

### Multi-Agent Knowledge Base

- KB documents are stored per-agent under `calling-agent/agents/<id>/knowledge/`.
- Ingest runs in the dashboard process (or via `ingest_cli.py`). The pipeline process loads the index at call start — there is no hot-reload; a pipeline restart is needed after reindexing to pick up new knowledge.
- Embedding model (`all-MiniLM-L6-v2`) is loaded lazily into RAM as a singleton. First ingest after a process restart takes a few seconds to load.
- `kb_enabled: false` in persona.json disables RAG retrieval for that agent without removing the index.

### WebSocket STT/TTS (sarvam_ws.py, tts_sarvam_ws.py)

These are alternative provider implementations:
- `SarvamWSSTT`: persistent WS to `wss://api.sarvam.ai/speech-to-text/ws`. Lower per-utterance latency; one connection per concurrent language.
- `SarvamWSTTS`: persistent WS to `wss://api.sarvam.ai/text-to-speech/ws`. Eliminates per-utterance TLS overhead.

**Critical**: when using `SarvamWSTTS` with `bulbul:v3`, the config message must NOT include `enable_preprocessing`. That parameter is exclusive to `bulbul:v2`; sending it causes a 422 error (`"Input parameters has to be a valid dictionary"`). The fix has been applied in `tts_sarvam_ws.py`.

Neither WS provider is used by the current main pipeline (which uses HTTP `SarvamSTT`/`SarvamTTS`). They are available for direct instantiation when lower latency is needed.

### Known Data Gaps

- **Inbound missed calls**: not tracked — `credit_log` only records calls that reached the AI pipeline.
- **Caller phone number**: `metadata.json` does not store the caller's PSTN number. The recording folder name often IS the phone number but this is not guaranteed.
- **CallState not persisted**: session state lives in memory during a call only. Post-call data: transcript, metadata, credit_log row.
- **Minutes accounting**: estimated from recording durations or credit_log timestamps. Quota is set manually.

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
bash start.sh --bg          # background, logs to dashboard.log
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

### Ingest KB Documents (CLI)
```bash
cd /home/angelo/lescall/calling-agent
source venv/bin/activate
python backend/app/agent/ingest_cli.py --agent vishal_default --file /path/to/doc.pdf
```

---

## Invariants — Never Violate

1. `socket_server.py`, `ws_audio_receiver.py`, and `call_audit.py` are NEVER imported by the dashboard.
2. The dashboard never writes into `recordings/` or `credit_log/`.
3. `socket_server.py` runs under **system Python**; `ws_audio_receiver.py` and all `calling-agent/` code runs under `calling-agent/venv`. Do not mix.
4. All audio in the AI pipeline is 16-bit PCM at 16 kHz. FreeSWITCH delivers PCMU at 8 kHz; `ws_audio_receiver.py` handles decode and upsample.
5. `SARVAM_API_KEY` is the correct env var name (not `SARVAMAI_API_KEY`). Loaded from `calling-agent/.env` — the only authoritative key location.
6. The greeting fires only after the first audio frame is received (`signal_call_active()` gate) — not at WebSocket open — to avoid burning TTS credits on abandoned connections.
7. Follow-Up Automation places real calls via FreeSWITCH — always confirm before using "Call Now" on a production system.
8. **Never start the runtime as root.** `runtime_guard.py` enforces this at process start. Root-owned files in shared dirs (`/tmp/phase15_audio_debug`, `/tmp/esl_recordings`, `/tmp/ai_telephony_playback`) will cause PermissionError on every audio frame.
9. **`SarvamWSTTS` (bulbul:v3)**: never include `enable_preprocessing` in the config message. It is a `bulbul:v2`-only parameter; sending it causes 422 errors. Fixed in `tts_sarvam_ws.py`.
10. `conversation.py` must never be imported by the dashboard. The legacy `/api/agent/config` endpoint reads it as raw source text via regex only.
