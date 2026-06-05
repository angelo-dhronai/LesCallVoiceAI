# Codebase Reference — Lescall / Pramukh Group Voice Agent

Last updated: 2026-06-05 (post root-cause investigation, branch `mvp`)

---

## System Overview

Inbound real-time voice AI for **Pramukh Group** real estate. Phone calls arrive via FreeSWITCH → WebSocket → AI pipeline. The agent (Vishal Patel) greets callers bilingually, detects language (English / Hindi / Gujarati), and drives through a 6-stage sales flow:

```
GREETING → DISCOVERY → PROJECT_MATCH → INVITE_VISIT → CONFIRMATION → CLOSING
```

---

## Process Topology

```
Caller (PSTN)
    │ SIP/RTP PCMU 8kHz
    ▼
FreeSWITCH
    │ ESL (port 9000)          ← socket_server.py (system Python)
    │ mod_audio_stream (port 9001) — binary PCMU frames
    ▼
ws_audio_receiver.py           ← WebSocket server (venv Python)
    │  PCMU decode (ulaw2lin)
    │  Upsample 8kHz → 16kHz (audioop.ratecv)
    │  call_bridge() → TelephonySession
    ▼
calling-agent/backend/app/voice/pipeline.py   ← AI pipeline
    │
    └── TTS audio → TelephonySink → TCP port 9004 → socket_server.py → FreeSWITCH
```

**Critical**: `socket_server.py` runs under system Python. Everything under `calling-agent/` runs under `calling-agent/venv`. Do NOT mix them.

---

## Services and Startup

### Start All
```bash
cd /home/angelo/lescall
bash phase14_runtime/start_all.sh
```
Starts in order:
1. `socket_server.py` (port 9000 — FreeSWITCH ESL controller)
2. `ws_audio_receiver.py` (port 9001 — media WebSocket + AI bridge, `--sample-rate 8000`)

Logs: `phase14_runtime/logs/latest/`

### Stop / Monitor
```bash
bash phase14_runtime/stop_all.sh
bash phase14_runtime/tail_logs.sh
bash phase14_runtime/check_runtime.sh
```

### AI Pipeline Only (CLI mic, no telephony)
```bash
cd calling-agent && source venv/bin/activate && cd backend
python run.py              # mic input → speaker output
python run.py --web        # + local web console http://127.0.0.1:8000
python run.py --test       # single-turn test without mic
```

### Environment
File: `calling-agent/.env`

```
SARVAM_API_KEY=sk_...          # STT + TTS (required — validated at startup)
OPENAI_API_KEY=sk-...          # Whisper fallback STT only
ENABLE_BARGE_IN=true
TELEPHONY_MODE=false           # set true when running via FreeSWITCH
ENABLE_CALL_AUDIT=true         # record WAV + transcripts to ./recordings/
TELEPHONY_INPUT_CODEC=pcmu     # default; FreeSWITCH mod_audio_stream sends PCMU
TELEPHONY_TARGET_SR=16000      # pipeline internal sample rate
AUDIO_STREAM_SAMPLE_RATE=8000  # FreeSWITCH stream sample rate
```

**IMPORTANT**: Env var name is `SARVAM_API_KEY` (not `SARVAMAI_API_KEY` — a recurring past mistake that causes silent key-not-found failures).

---

## AI Pipeline (pipeline.py)

Five independent async tasks connected by bounded queues:

```
[AudioSource]
    → vad_queue   → [VAD task]            energy-based, self-calibrating
    → stt_queue   → [STT worker]          Sarvam saaras:v3 WebSocket primary
    → transcript_queue → [ConversationRouter]  deterministic shortcuts first, then LLM
    → tts_queue   → [TTS player]          Sarvam Bulbul WebSocket stream
    → [AudioSink]                         speaker (local) or TelephonySink (telephony)
```

Queue bounds prevent backpressure from propagating upward — a slow TTS never blocks audio capture.

### Key Constants (pipeline.py)

| Constant | Value | Rationale |
|---|---|---|
| `SAMPLE_RATE` | 16000 | Internal pipeline rate (post-upsample) |
| `MIN_SPEECH_MS` | 500ms | Min speech duration to send to STT. Raised from 300ms — sub-500ms segments produce garbled 1-syllable transcripts |
| `HARD_FLUSH_SILENCE_FRAMES` | 50 (1000ms) | Silence frames to trigger flush. Raised from 30 (600ms) — Hindi/Gujarati callers pause 400–700ms within utterances; 600ms was splitting single sentences into 2 fragments |
| `_STT_FALLBACK_MIN_SPEECH_MS` | 800ms | Min duration for a "please repeat" fallback |
| `MAX_SPEECH_SECONDS` | 4 | Hard cap on segment length |
| `TTS_ECHO_COOLDOWN_MS` | 400ms (telephony) / 1500ms (local) | Post-TTS mute window |
| `BARGE_IN_COOLDOWN_MS` | 400ms | Min time after barge-in trigger |
| `BARGE_IN_MIN_FRAMES` | 3 (60ms) | Sustained speech needed to trigger barge-in |

**Turn-manager note**: `TurnManager` is configured with `silence_threshold_ms=1400ms` but in practice `HARD_FLUSH_SILENCE_FRAMES` (1000ms) always fires first. The TurnManager's semantic completeness check uses a word-proxy heuristic and rarely governs timing.

---

## Audio Path in Detail (ws_audio_receiver.py)

### What FreeSWITCH sends
`socket_server.py` starts the stream with:
```
bgapi uuid_audio_stream <uuid> start <ws_url> mono 8000
```
mod_audio_stream streams raw PCMU (G.711 µ-law) at 8 kHz. Each packet is nominally 160 bytes (20ms), but TCP batching can produce 320, 480, or larger frames.

### PCMU Decode (fixed 2026-06-05)
Previous code:
```python
if len(norm_msg) == 160:          # BUG: only 20ms frames decoded
    norm_msg = audioop.ulaw2lin(norm_msg, 2)
else:
    # BYPASS: raw PCMU treated as PCM16 → garbled audio
```

Fixed code:
```python
if INPUT_CODEC == "pcmu" and len(norm_msg) > 0:
    norm_msg = audioop.ulaw2lin(norm_msg, 2)   # handles any frame size
```

**Root cause of garbled transcripts**: 320B+ PCMU batches bypassed ulaw2lin. Raw µ-law bytes interpreted as linear PCM16 produce heavily distorted audio. This was the primary source of garbage Gujarati/Hindi transcripts (e.g. 'λοδ ιιιτρ.', 'λεσκαλλ ιιιτρ').

### Resampling
After PCMU decode (PCM16 at 8kHz):
```python
if TARGET_SR != args.sample_rate:   # 16000 != 8000 → always true
    norm_msg, resample_state = audioop.ratecv(norm_msg, 2, 1, 8000, 16000, resample_state)
```
Result: PCM16 at 16kHz. `--sample-rate 8000` is now passed explicitly from `start_all.sh`.

### Audio Flow
```
raw PCMU bytes (FreeSWITCH)
    → ulaw2lin → PCM16 @ 8kHz
    → ratecv   → PCM16 @ 16kHz
    → RMS log  → TelephonySource.push_frame()
    → pipeline VAD task
```

---

## VAD Layer (vad.py)

Energy-based (RMS), self-calibrating over first 25 frames (500ms).

| Parameter | TELEPHONY_MODE=True | Local mode |
|---|---|---|
| `onset_frames` | 2 (40ms) | 2 |
| `hold_frames` | 10 (200ms) | 10 |
| `minimum_threshold` | 260.0 | lower |
| `minimum_rms_floor` | 60.0 | 50.0 |
| `calibration_frames` | 25 (500ms) | 25 |

**Known issue — calibration on greeting echo**: The VAD calibrates during the first 500ms of the call. In telephony mode, if the greeting TTS audio echoes back before calibration completes, the threshold will be set too high, making the VAD temporarily deaf to normal speech. The `_mic_enabled` flag does not prevent calibration progress. This is mitigated by the greeting gate (`signal_call_active()`) which fires the greeting only after the first audio frame, but the echo can still overlap with calibration.

### VAD in pipeline.py (`_vad_task`)

```
audio frame → VAD.is_speech()
    if speech:   accumulate in _audio_buffer, advance _speech_duration_ms
    if silence:  increment _silence_frames
                 if _silence_frames >= HARD_FLUSH_SILENCE_FRAMES (1000ms)
                    AND _speech_duration_ms >= MIN_SPEECH_MS (500ms):
                        flush → AudioSegment → stt_queue
```

Frames during `_vad_mute_until` (TTS echo cooldown) are silently dropped.

---

## STT Layer

### Provider Hierarchy

1. **SarvamWSSTT** (`stt/sarvam_ws.py`) — primary, Sarvam saaras:v3 WebSocket
2. **SarvamSTT** (`stt/sarvam.py`) — HTTP fallback if WS fails
3. **WhisperSTT** — fallback on Sarvam HTTP 429 (rate limit)

### SarvamWSSTT (`stt/sarvam_ws.py`)

- Model: `saaras:v3` (telephony-tuned)
- One persistent WebSocket connection per language code
- `flush_signal="true"` — clean segment boundaries
- `high_vad_sensitivity="true"` — Sarvam's internal VAD

**Language detection strategy (current)**:
- Before language lock: `language_code="unknown"` → Sarvam detects natively, returns detected code in `message.data.language_code`
- After lock: explicit BCP-47 code (`hi-IN`, `gu-IN`, `en-IN`) — single connection

Previous broken approach (removed 2026-06-04): dual parallel connections with language-specific heuristics, `asyncio.gather([hi-IN, gu-IN])`, custom Gujarati script detectors, ~120 lines of heuristic code. Replaced with native Sarvam detection.

**Connection pool**: `_pool: dict[str, (ws, ctx)]` — one connection per active `lang_code`. `warmup()` pre-opens the `"unknown"` connection.

**Timeouts**: `TRANSCRIBE_TIMEOUT=10s`, `WARMUP_TIMEOUT=5s`. On timeout or error, connection is closed and recreated on next call.

### STTResult dataclass (`stt/base.py`)

```python
@dataclass
class STTResult:
    text: str
    confidence: float = 1.0
    detected_language: str | None = None  # BCP-47 e.g. "hi-IN", from Sarvam
```

### STTRouter (`stt/router.py`)

- Returns `STTResult` (not `str`) — `detected_language` propagates from provider
- Exponential backoff on Sarvam 429: 60s → 300s max
- Routes to Whisper only on genuine API errors; empty transcript (silence) does NOT trigger fallback
- `validate_transcript()` post-processor: rejects hallucinations, unsupported scripts (Korean, Tamil, Malayalam, etc.), filler words, repetition, noise tokens

### Language Lock (pipeline.py `_stt_worker`)

```python
if stt_result.detected_language and not state.language_locked and transcript_text:
    short = {"en-IN": "en", "hi-IN": "hi", "gu-IN": "gu"}.get(stt_result.detected_language)
    if short:
        state.language = short
        state.language_locked = True
```

Once locked, `language_locked=True` prevents re-detection. The locked language is passed to STT on subsequent turns (`hi-IN`, `gu-IN`, `en-IN`).

### STT Quality Gates (pipeline.py `_stt_worker`)

1. **Hallucination check**: `word_count > max(4, segment_ms/250) * 2` → reject
2. **Suspicious count**: `word_count > max(4, segment_ms/250)` → warn
3. **Min length**: `len(transcript.strip()) < 2` → trigger fallback phrase
4. **RMS diagnostic**: logged at submission — `[STT-WORKER] rms=NNN` — for post-hoc audio quality analysis

---

## Conversation Engine (`app/voice/conversation.py`)

`ConversationRouter` is the production agent.

### Stage Machine

```
GREETING → DISCOVERY → PROJECT_MATCH → INVITE_VISIT → CONFIRMATION → CLOSING
```

Stages advance only after explicit conditions. Current stage is injected into every LLM system prompt.

### CallState (`memory.py`)

Canonical session object, ground truth for confirmed fields:
- `customer_name`, `customer_phone`, `selected_project`, `selected_location`
- `appointment_date`, `appointment_time`
- `language` (short code: `en`, `hi`, `gu`)
- `language_locked` (bool — prevents re-detection after first turn)
- `conversation_stage`

Injected into every LLM prompt as "CONFIRMED LEAD PROFILE" — prevents agent from re-asking confirmed fields.

### LLM

OpenAI-compatible client, `claude-sonnet-4-6` (or configured endpoint). Responses capped at 1-2 sentences for spoken delivery.

### Deterministic Shortcuts (`shortcuts.py`)

Common phrases (hello, bye, thanks, yes/no) return canned responses instantly, bypassing LLM. Checked before every LLM call.

### Project Lock

First confirmed Pramukh project stored in `CallState.selected_project` — never overridden mid-call.

---

## TTS Layer

### SarvamWSTTS (`tts_sarvam_ws.py`) — primary

WebSocket TTS, model `bulbul:v3`, speaker `shubh`. True streaming: audio chunks written to AudioSink as they arrive. Barge-in support via `ENABLE_BARGE_IN`.

### SarvamTTS (`tts_sarvam.py`) — fallback

HTTP streaming to `api.sarvam.ai/text-to-speech/stream`. Same model/speaker. Used if WS TTS fails.

### Greeting Gate

Greeting fires only after `signal_call_active()` — the first audio frame from the caller. This prevents burning TTS credits on abandoned WebSocket connections that never complete a call.

---

## Memory System (`memory.py`)

Three-layer memory injected into each LLM call:

1. **`ConversationMemory`** — sliding window, last 5 turns
2. **`ContextMemory`** — extracted `user_name`, `intent`, `topic`, `sentiment`
3. **`CallState`** — structured booking fields (ground truth)

Phone numbers stored internally, never read back to caller.

---

## Web Console (`app/web/`)

Optional `--web` flag adds FastAPI server (port 8000). `EventHub` (`event_hub.py`) is async pub/sub broadcaster — `emit_nowait()` is non-blocking. Each WebSocket client has its own bounded queue. Slow client drops messages, never blocks pipeline.

---

## Call Audit (`call_audit.py`)

`ENABLE_CALL_AUDIT=true` → WAV recording + transcript in `./recordings/<conn_id>/`. Managed by `audit_manager` singleton imported by `ws_audio_receiver.py`.

---

## Root Cause Investigation (2026-06-05)

Full investigation of STT degradation vs. Phase 12 (streaming) architecture.

### Finding 1 — CRITICAL: PCMU Decode Bypassed for Batched Frames

**File**: `ws_audio_receiver.py`
**Impact**: All batched PCMU frames (320B+) were passed to the pipeline as raw µ-law bytes, interpreted as PCM16. This produced severely distorted audio — primary cause of garbage/garbled transcripts.
**Fix**: Removed `len(norm_msg) == 160` guard. Now decodes all non-empty frames when `INPUT_CODEC == "pcmu"`.

### Finding 2 — CRITICAL: Architecture Regression vs Phase 12

Phase 12 streamed raw PCM directly to Sarvam's WebSocket continuously. Sarvam's own neural VAD found utterance boundaries. This was superior because:
- No Python VAD pre-segmentation required
- Sarvam received complete utterances with full context
- No resampling artifacts (PCM forwarded at native 8kHz)
- Language detection via Sarvam's internal model from day one

Current architecture (batch-per-segment) mitigates this via:
- `HARD_FLUSH_SILENCE_FRAMES=50` (1000ms) to reduce premature segmentation
- `MIN_SPEECH_MS=500ms` to avoid sub-word fragments
- `language_code="unknown"` native detection (same as Phase 12's `DEFAULT_LANGUAGE="unknown"`)

A full return to streaming (Phase 12 `SarvamStreamingSTTAdapter`) would be higher fidelity but requires significant re-engineering of the VAD layer and telephony path.

### Finding 3 — SIGNIFICANT: Hard Flush Overrode TurnManager

600ms hard flush fired before TurnManager's 1400ms silence threshold. Natural clause-boundary pauses in Indian languages (400–700ms) caused single sentences to split into 2 fragments. Raised to 1000ms.

### Finding 4 — MODERATE: VAD Calibration on Greeting Echo

500ms calibration window can capture TTS greeting echo → threshold set too high → VAD temporarily deaf. Not yet fixed. Mitigation: TTS echo cooldown (`TTS_ECHO_COOLDOWN_MS`) after greeting completes suppresses most secondary echo.

### Finding 5 — MODERATE: MIN_SPEECH_MS=300ms Too Short

300ms segments reliably produce sub-word STT outputs ("haan", fragments, noise tokens). Raised to 500ms.

### Finding 6 — MINOR: Start Script Missing `--sample-rate`

`start_all.sh` didn't pass `--sample-rate 8000` explicitly. Worked by coincidence (argparse default). Now explicit.

---

## Comparison: Phase 12 (Streaming) vs Current (Batch)

| Aspect | Phase 12 | Current (post-fixes) |
|---|---|---|
| Audio to Sarvam | Raw PCM16 streaming | WAV per-segment request |
| Segmentation | Sarvam neural VAD | Python energy VAD + 1000ms flush |
| Language detection | `language_code="unknown"` | `language_code="unknown"` (restored) |
| Min segment | Sarvam decides | 500ms Python gate |
| Resampling | None (8kHz native) | 8kHz → 16kHz ratecv |
| PCMU decode | N/A (PCM forwarded) | Fixed: any frame size |
| STT model | saaras:v1 (circa Phase 12) | saaras:v3 |
| Dual-language heuristics | None (native unknown) | None (removed 2026-06-04) |

---

## Files Modified (2026-06-04 to 2026-06-05)

| File | Change |
|---|---|
| `calling-agent/backend/app/voice/stt/base.py` | Added `detected_language: str \| None` to `STTResult` |
| `calling-agent/backend/app/voice/stt/sarvam_ws.py` | Full rewrite: single `unknown` connection, native language detection, removed ~120 lines of heuristics |
| `calling-agent/backend/app/voice/stt/router.py` | `transcribe()` returns `STTResult` (not `str`); `detected_language` threaded through |
| `calling-agent/backend/app/voice/pipeline.py` | `_stt_worker` uses `STTResult`; language lock from Sarvam detection; `HARD_FLUSH_SILENCE_FRAMES` 30→50; `MIN_SPEECH_MS` 300→500; RMS diagnostic log |
| `ws_audio_receiver.py` | PCMU decode: removed 160B size check, decodes any non-empty PCMU frame |
| `phase14_runtime/start_all.sh` | Added `--sample-rate 8000` to `ws_audio_receiver.py` invocation |

---

## Key Invariants

- `SARVAM_API_KEY` is the correct env var name (not `SARVAMAI_API_KEY`)
- Greeting fires only after first audio frame (`signal_call_active()` gate)
- `socket_server.py` runs under system Python; `ws_audio_receiver.py` runs under venv
- All audio in the AI pipeline is PCM16 at 16kHz
- FreeSWITCH delivers PCMU at 8kHz; `ws_audio_receiver.py` decodes and upsamples
- Language lock (`language_locked=True`) is permanent per session once set
- `selected_project` in `CallState` is never overridden after first confirmation
- TTS echo cooldown applies in both telephony and local mode (different durations)

---

## Directory Structure

```
/home/angelo/lescall/
├── socket_server.py                      ESL controller (system Python)
├── ws_audio_receiver.py                  WebSocket media bridge (venv)
├── conversation_call_bridge.py           Per-call session wiring
├── phase14_runtime/
│   ├── start_all.sh                      Service startup
│   ├── stop_all.sh
│   ├── tail_logs.sh
│   ├── check_runtime.sh
│   └── logs/latest/                      Current run logs
├── calling-agent/
│   ├── .env                              API keys and feature flags
│   ├── requirements.txt
│   ├── venv/                             Isolated Python environment
│   └── backend/
│       ├── run.py                        CLI entrypoint
│       └── app/voice/
│           ├── pipeline.py               Pipeline orchestrator + VAD task
│           ├── vad.py                    Energy VAD with calibration
│           ├── turn_manager.py           Semantic turn completion logic
│           ├── conversation.py           ConversationRouter (LLM agent)
│           ├── memory.py                 CallState + ConversationMemory
│           ├── shortcuts.py              Deterministic phrase shortcuts
│           ├── audio_input.py            AudioSource/Sink base classes
│           ├── call_audit.py             WAV + transcript recording
│           ├── stt/
│           │   ├── base.py               STTProvider ABC + STTResult + helpers
│           │   ├── router.py             STTRouter (Sarvam WS → HTTP → Whisper)
│           │   ├── sarvam_ws.py          Sarvam saaras:v3 WebSocket STT
│           │   ├── sarvam.py             Sarvam saarika:v2.5 HTTP STT (fallback)
│           │   ├── post_processor.py     validate_transcript()
│           │   └── whisper.py            OpenAI Whisper fallback
│           └── tts_sarvam_ws.py          Sarvam Bulbul WebSocket TTS
│           └── tts_sarvam.py             Sarvam Bulbul HTTP TTS (fallback)
└── recordings/                           Call audit output (WAV + transcripts)
```
