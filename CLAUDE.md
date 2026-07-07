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

- **`stt/router.py` — STTRouter**: always tries Sarvam first. On HTTP 429, exponential backoff (60s → 300s max) and routes to Whisper. On clean empty transcript (no speech), returns `""` immediately — Whisper is **not** called. Only genuine API errors trigger fallback.
- **`stt/sarvam.py` — SarvamSTT**: multipart WAV POST to `api.sarvam.ai/speech-to-text`, model `saaras:v3`, mode `codemix`. PCM audio is amplitude-normalized before upload (telephony audio is often quiet). Returns `language_probability` field used for language locking. Uses persistent `httpx.AsyncClient` with 60s keepalive expiry.
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

### Conversation State Management (`app/voice/state_manager.py`)

`ConversationStateManager` is the **single write path for qualification slots** (location, configuration/BHK, budget, timeline, purpose, loan_required, transaction_type, occupation, property_category, project). One instance per call, owned by `ConversationRouter` (`router.state_mgr`).

- **`ingest_user_turn(text, intent)`** runs on EVERY turn (including shortcut turns — a "thanks, 3BHK in Vapi" no longer loses its slots) and strictly **before** the speculative LLM task is created, so the system prompt always sees fresh state. It is synchronous pure Python — never add an `await` into this path.
- **Merge policies**: a null extraction never overwrites data; preference slots are LATEST_WINS ("actually make it a 2BHK" updates state + audit trail); `project` is CORRECTION_GATED (passing mentions rejected, explicit correction cues like "nahi nahi, X" accepted); name/phone/appointment are EXTERNAL — still owned by the pending-confirmation and stage-gated machinery in `conversation.py`, the manager only audits them via `observe_external()`.
- **Write-through**: the manager writes both legacy stores (`BookingData` and `CallState`) so all `b.X or cs.X` readers stay consistent. `CallState` remains the external contract.
- **`build_prompt_context()`** renders PERSISTENT CALL STATE + RUNNING SUMMARY + RECENT CORRECTIONS + ALREADY COLLECTED + QUALIFICATION STATUS (with the single NEXT FIELD TO ASK and a suggested question in the caller's language) — injected into every system prompt by `_build_system_prompt`.
- **Audit trail**: every accepted/rejected slot change (`SlotChange`: field, old, new, turn, source, correction) — surfaced in the `{"type": "state"}` event-hub event after each turn and persisted to `recordings/<conn_id>/conversation_state.json` at call end by the bridge.
- Optional LLM-assisted gap-filling behind `ENABLE_LLM_SLOT_EXTRACTION=true` (fill-only, never overwrites a deterministic capture; off by default).

### Memory (`app/voice/memory.py`)

Three-layer memory injected into each LLM call:
1. **`ConversationMemory`** — sliding window of last 5 turns.
2. **`ContextMemory`** — extracted `user_name`, `intent`, `topic`, `sentiment`.
3. **`CallState`** — structured booking fields; the ground truth for what has been confirmed. Slot writes go through `ConversationStateManager` (above); name/phone keep their dedicated capture flows.

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
