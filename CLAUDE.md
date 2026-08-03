# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This System Does

Inbound real-time voice AI for **Pramukh Group** (real estate). Phone calls arrive via FreeSWITCH → WebSocket → AI pipeline. The agent (Vishal Patel) greets callers bilingually, detects language (English / Hindi / Gujarati), and drives them through a 6-stage sales flow: `GREETING → DISCOVERY → PROJECT_MATCH → INVITE_VISIT → CONFIRMATION → CLOSING`.

## Running the System

### Start All Services
```bash
cd /home/angelo/lescall
bash phase14_runtime/start_all.sh
```
Starts two processes in order: `socket_server.py` (port 9000 — FreeSWITCH ESL controller) then `ws_audio_receiver.py` (port 9001 — media WebSocket + AI bridge). Logs land in `phase14_runtime/logs/latest/`.

### Stop / Monitor
```bash
bash phase14_runtime/stop_all.sh
bash phase14_runtime/tail_logs.sh          # all logs
bash phase14_runtime/check_runtime.sh      # service health
```

### AI Pipeline Only (CLI mic, no telephony)
```bash
cd calling-agent
source venv/bin/activate
cd backend
python run.py              # mic input → speaker output
python run.py --web        # + local web console at http://127.0.0.1:8000
python run.py --test       # single-turn test without mic
```

### Environment
Copy `calling-agent/.env` and set:
```
SARVAM_API_KEY=sk_...          # STT + TTS (required)
OPENAI_API_KEY=sk-...          # Whisper fallback STT only
ENABLE_BARGE_IN=true           # interrupt TTS mid-playback
TELEPHONY_MODE=false           # set true when running via FreeSWITCH
ENABLE_CALL_AUDIT=true         # record WAV + transcripts to ./recordings/
```
`run.py` validates `SARVAM_API_KEY` at startup and exits immediately if missing.

### Dependencies
```bash
cd calling-agent
pip install -r requirements.txt
```

---

## Architecture

### Process Topology (telephony mode)

```
FreeSWITCH
    │ ESL (port 9000)
    ▼
socket_server.py          ← ESL controller; handles call events, triggers
    │                       playback via validated execute command
    │ WebSocket (port 9001)
    ▼
ws_audio_receiver.py      ← receives PCMU audio from FS, decodes to PCM16,
    │                       upsamples 8kHz→16kHz, calls call_bridge
    │
    ▼
conversation_call_bridge.py  ← creates one TelephonySession per call;
    │                          wires TelephonySource/Sink + VoicePipeline
    ▼
calling-agent/backend/app/voice/pipeline.py  ← full AI pipeline (see below)
    │
    └── TTS audio → TelephonySink → TCP port 9004 → socket_server.py → FreeSWITCH
```

### AI Pipeline (pipeline.py)

Five independent async tasks connected by bounded queues:

```
[AudioSource]
    → vad_queue   → [VAD task]        energy-based, self-calibrating
    → stt_queue   → [STT worker]      Sarvam primary / Whisper fallback
    → transcript_queue → [ConversationRouter]  deterministic shortcuts first, then LLM
    → tts_queue   → [TTS player]      Sarvam Bulbul HTTP stream
    → [AudioSink]                     speaker (local) or TelephonySink (telephony)
```

- **AudioSource/Sink** are swappable: `LocalMicSource`/`LocalSpeakerSink` for CLI mode; `TelephonySource`/`TelephonySink` for call mode.
- Queue bounds prevent backpressure from propagating upward — a slow TTS never blocks audio capture.
- `ENABLE_BARGE_IN=true` allows the user to interrupt TTS; a cooldown prevents false triggers.

### STT Layer (`app/voice/stt/`)

- **`stt/router.py` — STTRouter**: always tries Sarvam first. If the primary is silent past 0.6s a SECOND Sarvam request races it (`STT_PRIMARY_RETRY_HEDGE_S` — Sarvam hangs are per-request; the duplicate usually lands in ~200ms and keeps the turn on saarika); past 1.0s a Whisper hedge starts in parallel (`STT_HEDGE_DELAY_S`). Whisper only gets a language hint once the caller has established the conversation language (`fallback_hint` from the pipeline) — before that it auto-detects, so Hindi speech comes back in Devanagari, not romanized junk. On HTTP 429, exponential backoff (60s → 300s max) and routes to Whisper. On clean empty transcript (no speech), returns `""` immediately — Whisper is **not** called. The pipeline's `_stt_submitter` starts transcriptions as segments arrive (`STT_PREFETCH_AHEAD`) while the worker consumes results strictly in order — one slow request never serializes the segments behind it.
- **`stt/sarvam.py` — SarvamSTT**: multipart WAV POST to `api.sarvam.ai/speech-to-text`, model `saarika:v2.5` (owner's choice — do not switch to saaras without asking; `SARVAM_STT_MODEL`/`SARVAM_STT_MODE` env to experiment). PCM audio is amplitude-normalized before upload (telephony audio is often quiet). Uses a persistent shared `httpx.AsyncClient`; per-phase timeout caps (`SARVAM_CONNECT_TIMEOUT_S`=3s / `SARVAM_READ_TIMEOUT_S`=5s) keep a hung Sarvam request from stalling the STT worker, and a timeout resets the client so the retry gets a fresh connection.
- **`stt/router.py` — `validate_transcript()`**: rejects hallucinations (YouTube phrases + Devanagari equivalents), unsupported scripts (Korean, Tamil, Malayalam, etc.), Marathi (shares Devanagari with Hindi — caught by specific word markers), filler words, repetition, single-word noise tokens, and function-word-only fragments (quality score < 0.3). Returns `""` to reject; caller skips Whisper fallback on clean empty results.
- **Language locking** (`pipeline.py`): After Sarvam returns `language_probability ≥ 0.75`, the detected BCP-47 code (e.g. `hi-IN`) is locked in `_stt_locked_language` and passed on every subsequent STT call, preventing mid-call language bounce.
- **Audio normalization** (`stt/base.py`): PCM amplitude is boosted before upload to compensate for quiet telephone callers.

### Conversation Engine (`app/voice/conversation.py`)

`ConversationRouter` is the production agent. Key design points:

- **Stage machine**: `CallState.conversation_stage` drives which goal the LLM is given. Stages advance only after explicit conditions are met (e.g. interest confirmed before moving to booking).
- **`CallState`** (in `memory.py`) is the canonical session object — `customer_name`, `customer_phone`, `selected_project`, `selected_location`, `appointment_date/time`, `language`, `conversation_stage`. It is injected into every LLM system prompt so the agent never re-asks confirmed fields.
- **Deterministic shortcuts** (`shortcuts.py`): common phrases (hello, bye, thanks) return a canned response instantly, bypassing the LLM.
- **Project lock**: first confirmed Pramukh project is stored in `CallState.selected_project` and never overridden — prevents the agent from being steered to a different project mid-call.
- **Language detection**: set from the caller's first substantive response and mirrored for the rest of the call. `SarvamSTT` and `SarvamTTS` both accept a `language` field (`en-IN`, `hi-IN`, `gu-IN`).
- **LLM**: OpenAI-compatible client pointed at the configured endpoint (see `.env`). Responses are capped at 1-2 sentences for spoken delivery.

### TTS Layer

- **`tts_sarvam.py` — SarvamTTS**: HTTP streaming to `api.sarvam.ai/text-to-speech/stream`, model `bulbul:v3`, speaker `shubh`. Audio chunks are written to the `AudioSink` as they arrive (true streaming, no buffer-all-first).
- **`tts_stream.py` — StreamingTTS**: abstract base class. `MockStreamingTTS` available for testing.

### Memory (`app/voice/memory.py`)

Three-layer memory injected into each LLM call:
1. **`ConversationMemory`** — sliding window of last 5 turns.
2. **`ContextMemory`** — extracted `user_name`, `intent`, `topic`, `sentiment`.
3. **`CallState`** — structured booking fields; the ground truth for what has been confirmed.

Phone numbers are extracted and stored internally; they are never read back verbatim to the caller.

### Web Console (`app/web/`)

Optional `--web` flag adds a FastAPI server (port 8000). `EventHub` (`event_hub.py`) is an async pub/sub broadcaster — pipeline calls `emit_nowait()` (non-blocking); each WebSocket client has its own bounded queue. A slow client drops messages but never blocks the pipeline.

### Call Audit (`call_audit.py`)

When `ENABLE_CALL_AUDIT=true`, each call produces a WAV recording and transcript in `./recordings/<conn_id>/`. Managed by `audit_manager` (singleton imported by `ws_audio_receiver.py`).

---

## Key Invariants

- **`SARVAM_API_KEY`** env var name is `SARVAM_API_KEY` (not `SARVAMAI_API_KEY` — a common past mistake).
- The greeting fires only after the first audio frame is received (`signal_call_active()` gate), not at WebSocket open, to avoid burning TTS credits on abandoned connections.
- `socket_server.py` runs under system Python; `ws_audio_receiver.py` (and everything under `calling-agent/`) runs under `calling-agent/venv`. Do not mix them.
- All audio throughout the AI pipeline is 16-bit PCM at 16 kHz. FreeSWITCH delivers PCMU at 8 kHz; `ws_audio_receiver.py` handles the decode and upsample before forwarding.

---

## Frontend design system (`frontend/`)

React 18 + TS + Vite + Tailwind **v3** + shadcn/ui, redesigned 2026-07-22
(full record: `deploy/PLAN-ux-redesign.md`). If you touch a page, match these —
they are the whole point of the redesign, and re-introducing the old patterns
silently undoes it.

- **shadcn is on Tailwind v3** → add components with `npx shadcn@2 add <c>`
  (the 3.x CLI targets v4 and will corrupt `tailwind.config.ts`). Primitives
  live in `src/components/ui/`.
- **Token namespacing is load-bearing.** shadcn's `--border`/`--accent` are
  renamed `--ui-border`/`--ui-accent` because the app's `:root` already owns
  those names (brand emerald). Dark `--ui-border`/`--input` carry baked-in
  alpha → never use opacity modifiers (`border-border/50`) on them.
- **Type scale is closed**: `text-2xs` 11 · `xs` 12 · `sm` 13 (body default) ·
  `base` 14 · `lg` 16 · `xl` 18 · `2xl` 20 · `3xl` 24, plus a display tier
  `4xl` 30 / `5xl` 40 **for metric values only**. Never write `text-[Npx]`.
- **Radius law**: controls `rounded-md`, cards `rounded-2xl`, pills
  `rounded-full`.
- **One confirm idiom**: `useConfirm()` / `usePrompt()` from
  `components/confirm.tsx` (awaitable). No `window.confirm`/`prompt`/`alert`.
- **Chart colour comes from `lib/chartColors.ts`** (`CHART`, `TONE`,
  `scoreColor`) — never hardcode a hex. This is what fixed the old
  Negative-vs-Hot collision.
- **Loading/empty/error go through `AsyncState`** (`variant="rows|cards|text"`,
  `rows`, or a `skeleton` override). Empty labels state what would put
  something there, per filter — not "Nothing here".
- **Icon-only buttons are `IconButton`** with a specific `label` (it becomes
  both `aria-label` and tooltip): name the row's subject, don't ship four
  identical "Edit"s.
- **Shared composites before bespoke markup**: `components/kit.tsx`
  (PageHeader/Table/Pagination/SearchInput/Segmented/BarList),
  `components/insight.tsx` (ScoreCell/Chips/FilterChips/CommitmentCard/
  TimelineList/StatTile), `components/analyticsCharts.tsx`.
- **List state belongs in the URL** via `useUrlParam`/`useUrlNumber`
  (`lib/hooks.ts`); debounce text search with `useDebounced`.
- **All page I/O** goes through `api/client.ts` (`api`, `sse`) and
  `api/mutate.ts` (`post/patch/put/del/postForm`). Zero raw `fetch` in pages.
- **Routes are lazy** in `App.tsx` — only Overview + shell are eager. Adding a
  static page import back re-inflates the entry bundle (it was 740 kB; it is
  385 kB).
- **IA**: `/` is the Overview dashboard; the Copilot chat is `/copilot`.

Deploy the SPA with
`docker compose -f deploy/docker-compose.services.yml up -d --build spa`
(bind-mounted containers serve stale code until rebuilt).
