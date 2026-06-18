# Codebase Reference — Lescall / Pramukh Group Voice Agent

Last updated: 2026-06-11 (branch `apicostreduction` — hybrid TTS cache complete, 3 known cache bugs unfixed)

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
    │ ESL (port 9000)               ← socket_server.py (system Python)
    │ mod_audio_stream (port 9001)  — binary PCMU frames via WebSocket
    ▼
ws_audio_receiver.py                ← WebSocket server (venv Python)
    │  PCMU decode: audioop.ulaw2lin (any frame size — fixed 2026-06-05)
    │  Upsample: audioop.ratecv 8kHz → 16kHz
    │  conversation_call_bridge.call_bridge() → TelephonySession
    ▼
calling-agent/backend/app/voice/pipeline.py   ← AI pipeline (5 async tasks)
    │
    └── TTS audio → TelephonySink
            → writes PCM to /tmp/ai_telephony_playback/<uuid>.wav
            → conversation_call_bridge._playback_loop() polls sink
            → injects WAV path into FreeSWITCH via ESL uuid_broadcast
            → FreeSWITCH plays WAV to caller
```

**Critical**: `socket_server.py` runs under system Python. Everything under `calling-agent/` runs under `calling-agent/venv`. Never mix them.

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
bash phase14_runtime/tail_logs_filtered.sh   # errors + transcripts + AI responses only
bash phase14_runtime/check_runtime.sh        # service health check
```

### AI Pipeline Only (CLI mic, no telephony)
```bash
cd calling-agent && source venv/bin/activate && cd backend
python run.py              # mic input → speaker output
python run.py --web        # + local web console http://127.0.0.1:8000
python run.py --test       # single-turn VAD/mic sanity test (needs audio device)
```

### Environment
File: `calling-agent/.env`

```
SARVAM_API_KEY=sk_...          # STT + TTS (required — validated at startup; exact name matters)
OPENAI_API_KEY=sk-...          # Whisper fallback STT only (not used for LLM)
ENABLE_BARGE_IN=true           # allow caller to interrupt TTS playback
TELEPHONY_MODE=false           # set true when running via FreeSWITCH
ENABLE_CALL_AUDIT=true         # record WAV + transcripts to ./recordings/
```

**CRITICAL**: Env var name is `SARVAM_API_KEY` (NOT `SARVAMAI_API_KEY` — this recurring mistake causes silent startup failure).

LLM uses `OPENAI_API_KEY` via an `AsyncOpenAI` client (OpenAI-compatible). Default model: `gpt-4o-mini` (set in `ConversationRouter.__init__`). The model can be overridden by passing `model=` at construction.

---

## AI Pipeline (pipeline.py)

Five independent async tasks connected by bounded queues:

```
[AudioSource]
    ↓ raw PCM frames (16kHz)
[_vad_task]          — energy VAD, accumulates speech, flushes segments
    ↓ AudioSegment
[_stt_worker]        — sends to Sarvam WS STT, validates, locks language
    ↓ Transcript
[_conversation_layer] — deterministic shortcuts first, then LLM via ConversationRouter
    ↓ Response (with optional cache_key)
[_tts_layer]         — cache-first routing: speak_cached() or live Sarvam TTS
    ↓ PCM audio chunks
[AudioSink]          — LocalSpeakerSink (CLI) or TelephonySink (telephony)
```

Queue bounds prevent backpressure from propagating upward — a slow TTS never blocks audio capture.

### Key Constants (pipeline.py)

| Constant | Value | Rationale |
|---|---|---|
| `SAMPLE_RATE` | 16000 | Internal pipeline rate |
| `MIN_SPEECH_MS` | 300.0ms | Minimum speech to pass to STT |
| `HARD_FLUSH_SILENCE_FRAMES` | 50 (1000ms) | Silence frames before VAD flush. Raised from 30 (600ms) — Hindi/Gujarati callers pause 400–700ms within sentences; 600ms split single utterances into fragments |
| `MAX_SPEECH_SECONDS` | 4 | Hard cap on segment length |
| `_STT_FALLBACK_MIN_SPEECH_MS` | 800ms | Min segment duration to trigger "please repeat" fallback phrase |
| `TTS_ECHO_COOLDOWN_MS` | 400ms (telephony) / 1500ms (local) | Post-TTS mute window to suppress echo |
| `BARGE_IN_COOLDOWN_MS` | 400ms | Min time after barge-in trigger |
| `BARGE_IN_MIN_FRAMES` | 3 (60ms) | Sustained speech needed to trigger barge-in |

### Dataclasses (pipeline.py)

```python
@dataclass
class AudioSegment:
    audio: bytes          # PCM16 @ 16kHz
    duration_ms: float
    timestamp: float

@dataclass
class Transcript:
    text: str
    duration_ms: float
    timestamp: float
    stt_latency_ms: float = 0.0

@dataclass
class Response:           # queue item from _conversation_layer to _tts_layer
    text: str             # live TTS text (or fallback if cache misses)
    is_shortcut: bool
    timestamp: float
    cache_key: str | None = None   # if set, _tts_layer tries speak_cached() first
```

### Greeting Gate

Greeting fires only after `_greeting_done = False → True` transition, which happens after the first audio frame arrives from the caller. This prevents TTS credit burn on abandoned WebSocket connections that never establish a real call.

---

## Audio Path: FreeSWITCH → Pipeline

### What FreeSWITCH sends
`socket_server.py` starts the media stream with:
```
bgapi uuid_audio_stream <uuid> start ws://127.0.0.1:9001 mono 8000
```
mod_audio_stream streams raw PCMU (G.711 µ-law) at 8 kHz. TCP batching produces variable frame sizes (160B, 320B, 480B+).

### PCMU Decode (fixed 2026-06-05)
```python
# Previous BUG: only 20ms (160B) frames decoded; larger frames passed as raw µ-law → garbled
if len(norm_msg) == 160:
    norm_msg = audioop.ulaw2lin(norm_msg, 2)

# Fixed: decode all non-empty frames
if INPUT_CODEC == "pcmu" and len(norm_msg) > 0:
    norm_msg = audioop.ulaw2lin(norm_msg, 2)
```

### Resampling
```python
norm_msg, resample_state = audioop.ratecv(norm_msg, 2, 1, 8000, 16000, resample_state)
```
Result: PCM16 at 16kHz → `TelephonySource.push_frame()` → pipeline VAD task.

---

## Audio Path: Pipeline → FreeSWITCH (TTS return)

```
_tts_layer
    → CachedTTS.speak_cached()  or  SarvamTTS.speak()
    → TelephonySink.write_frame(pcm_chunk)   [streams PCM as it arrives]
    → TelephonySink.flush()
        → writes PCM to /tmp/ai_telephony_playback/<uuid>_<ts>.wav
        → downsamples 16kHz → 8kHz (audioop.ratecv)
        → validates WAV (size > 0, duration > 100ms)
        → queues path in self._pending_wavs
    ← conversation_call_bridge._playback_loop() polls TelephonySink._pending_wavs
    → ESL: uuid_broadcast <uuid> 'playback /tmp/...' aleg
    → FreeSWITCH plays WAV to caller
```

**PLAYBACK ERROR verdict**: If FreeSWITCH returns `Application-Response=PLAYBACK ERROR` (not `FILE PLAYED`), the WAV file failed to play. Root cause is usually a missing or zero-byte WAV. Logged as `CRITICAL PHASE-5 REGRESSION SUSPECTED` in `socket_server.py`.

---

## VAD Layer (vad.py)

Energy-based (RMS), self-calibrating over first 25 frames (500ms).

| Parameter | TELEPHONY_MODE=True | Local mode |
|---|---|---|
| `onset_frames` | 2 (40ms) | 2 |
| `hold_frames` | 10 (200ms) | 10 |
| `minimum_threshold` | 260.0 | lower |
| `calibration_frames` | 25 (500ms) | 25 |

**Known issue — calibration on greeting echo**: The VAD calibrates during the first 500ms of the call. If TTS greeting audio echoes back during calibration, the threshold is set too high → VAD temporarily deaf. Partially mitigated by `TTS_ECHO_COOLDOWN_MS`.

### VAD flow in `_vad_task`

```
audio frame → VAD.is_speech()
    if speech:   accumulate in _audio_buffer, advance _speech_duration_ms
    if silence:  increment _silence_frames
                 if _silence_frames >= HARD_FLUSH_SILENCE_FRAMES (1000ms)
                    AND _speech_duration_ms >= MIN_SPEECH_MS (300ms):
                        flush → AudioSegment → stt_queue
```

Frames during `_vad_mute_until` (TTS echo cooldown) are silently dropped.

---

## STT Layer (stt/)

### Provider Hierarchy

1. **SarvamWSSTT** (`stt/sarvam_ws.py`) — primary, Sarvam saaras:v3 WebSocket
2. **SarvamSTT** (`stt/sarvam.py`) — HTTP fallback if WebSocket fails
3. **WhisperSTT** (`stt/openai_whisper.py`) — fallback on Sarvam HTTP 429

### SarvamWSSTT (`stt/sarvam_ws.py`)

- Model: `saaras:v3` (telephony-tuned)
- One persistent WebSocket connection per language code
- `flush_signal="true"` — clean segment boundaries
- `high_vad_sensitivity="true"` — Sarvam's internal VAD active

**Connection pool**: `_pool: dict[str, (ws, ctx)]` — one connection per active `lang_code`. `warmup()` pre-opens the `"unknown"` connection.

**Language detection**:
- Before language lock: `language_code="unknown"` → Sarvam detects natively, returns BCP-47 in `message.data.language_code`
- After lock: explicit code (`hi-IN`, `gu-IN`, `en-IN`) on a single connection

**Timeouts**: `TRANSCRIBE_TIMEOUT=10s`, `WARMUP_TIMEOUT=5s`. On timeout/error, connection closed and recreated on next call.

### STTResult dataclass (`stt/base.py`)

```python
@dataclass
class STTResult:
    text: str
    confidence: float = 1.0
    detected_language: str | None = None   # BCP-47, e.g. "hi-IN" — from Sarvam
```

### STTRouter (`stt/router.py`)

- Always returns `STTResult` (not `str`) — `detected_language` propagates upstream
- Exponential backoff on Sarvam 429: 60s → 300s max, routes to Whisper
- Empty transcript (no speech) does NOT trigger Whisper fallback
- `validate_transcript()` post-processor: rejects hallucinations, unsupported scripts (Korean, Tamil, Malayalam, Punjabi), filler words, repetition, noise tokens

### Language Lock (`_stt_worker` in pipeline.py)

```python
if stt_result.detected_language and not state.language_locked and transcript_text:
    short = {"en-IN": "en", "hi-IN": "hi", "gu-IN": "gu"}.get(detected)
    if short:
        state.language = short
        state.language_locked = True
```

Once locked, `language_locked=True` is permanent for the session (unless the caller explicitly switches language, in which case `language_locked` is reset). The locked code is passed on every subsequent STT call.

### STT Quality Gates (`_stt_worker`)

1. **Hallucination check**: `word_count > max(4, segment_ms/250) * 2` → discard
2. **Min length**: `len(transcript.strip()) < 2` → trigger fallback phrase
3. **STT fallback phrase**: If segment ≥ 800ms but transcript is empty/rejected → enqueue `Response(text=fallback, cache_key="stt_fallback")` ← **BUG: `cache_key` not yet set here; fixed in next session**

---

## Conversation Engine (conversation.py)

`ConversationRouter` is the production agent.

### Stage Machine

```
GREETING → DISCOVERY → PROJECT_MATCH → INVITE_VISIT → CONFIRMATION → CLOSING
```

Defined as module-level constants. Current stage is injected into every LLM system prompt as part of the stage goal.

### EngineResult dataclass

```python
@dataclass
class EngineResult:
    text: str                          # LLM or shortcut response text
    is_shortcut: bool                  # True = bypass LLM buffering, enqueue immediately
    intent: str                        # classified intent (farewell, gratitude, etc.)
    cache_key: str | None = None       # set to route _tts_layer to pre-cached WAV
```

### CallState (memory.py)

Canonical session object, ground truth for all confirmed fields:

| Field | Type | Description |
|---|---|---|
| `language` | `str` | Short code: `en`, `hi`, `gu` |
| `language_locked` | `bool` | Prevents re-detection after first substantive turn |
| `language_lock_turns` | `int` | Decay counter; explicit switch sets to -7 |
| `conversation_stage` | `str` | Current STAGE_* constant |
| `customer_name` | `str` | Captured name |
| `customer_phone` | `str` | Captured phone (never read back to caller) |
| `selected_project` | `str` | First confirmed project — never overridden |
| `selected_location` | `str` | City (Surat, Vapi, Silvassa) |
| `appointment_date` | `str` | Confirmed visit date |
| `appointment_time` | `str` | Confirmed visit time |
| `is_closing` | `bool` | Set True on farewell/second decline |
| `decline_count` | `int` | Objection counter; at 2, close gracefully |

Injected into every LLM call as "CONFIRMED LEAD PROFILE" — prevents the agent from re-asking confirmed fields.

### LLM Client

`AsyncOpenAI` with `OPENAI_API_KEY`. Default model `gpt-4o-mini`. Max response: `MAX_RESPONSE_LENGTH` chars, 1-2 sentences for spoken delivery.

### Deterministic Shortcuts

Shortcut intents are detected in `process_stream()` by `detect_intent()` before the LLM is called. Each shortcut `EngineResult` now carries a `cache_key` that routes the response to a pre-cached WAV:

| Intent / trigger | cache_key |
|---|---|
| Mid-call greeting ("hello?" after opening) | `shortcut_hello` |
| Farewell (any stage) | `shortcut_bye` |
| Second decline / closing farewell | `shortcut_bye` |
| Objection / not interested (1st decline) | `shortcut_not_interested` |
| Gratitude / thanks | `shortcut_thanks` |

`text=resp` is kept on the `EngineResult` as a live TTS fallback if the WAV is missing.

### Cache Router (LLM path)

`_CACHE_ROUTER_INSTRUCTIONS` (module-level string, appended to every system prompt) lists 22 cache keys with trigger conditions. The LLM outputs `CACHE:<key>` as its entire response when a generic cached answer fits.

`_parse_llm_response(raw)` in `_consume_llm_with_filler()` intercepts the first chunk:
```python
def _parse_llm_response(raw: str) -> tuple[str | None, str | None]:
    m = re.match(r'^CACHE:([a-zA-Z_]+)\s*$', raw.strip())
    if m:
        return m.group(1), None   # cache routing
    return None, raw.strip()      # live TTS
```

If a CACHE token is detected, the LLM queue is drained and a single `EngineResult(text="", cache_key=key)` is yielded. **Known issue: LLM rarely/never outputs CACHE tokens in practice — the instructions need to be stronger. Fix pending.**

### Opening Greeting

`generate_opening()` returns `EngineResult(text=_OPENING_GREETING, is_shortcut=True, intent="greeting")`. **Known bug: `cache_key="greeting"` is not set, so the opening greeting always fires a live Sarvam TTS call (106 chars, ~₹0.32/call). Fix: add `cache_key="greeting"` to the return statement.**

The greeting text (`_OPENING_GREETING`):
```
"Hello! Namaste — this is Vishal Patel calling from Pramukh Group. Hope I haven't caught you at a bad time."
```

---

## TTS Layer (pipeline.py `_tts_layer`)

### Cache-First Routing

```
Response dequeued
    ↓
if response.cache_key and tts has speak_cached():
    try CachedTTS.speak_cached(cache_key, lang_short)
    if success → done
    if miss    → fall through to live TTS
↓
Silence protection: if text is empty AND no cache_key → substitute fallback phrase
↓
Preprocess text: normalize em-dashes, ellipsis, bullets
↓
Apply _PRONUNCIATION_MAP corrections
↓
await tts.speak(tts_text)   ← live Sarvam TTS
```

`lang_short` = first part of `self.conversation.state.language` (e.g. `"en"`, `"hi"`, `"gu"`).

### TTS Providers

| Class | File | Usage |
|---|---|---|
| `CachedTTS` | `tts_cached.py` | **Default** — serves pre-generated WAVs; inherits `TrackingSarvamTTS` |
| `TrackingSarvamTTS` | `tracking_tts.py` | SarvamTTS + credit tracking; base class for CachedTTS |
| `SarvamTTS` | `tts_sarvam.py` | HTTP streaming to Sarvam API; `bulbul:v3`, speaker `shubh` |
| `SarvamWSTTS` | `tts_sarvam_ws.py` | WebSocket TTS; same model/speaker |

`VoicePipeline` instantiates `CachedTTS` by default (as of branch `apicostreduction`).

### Echo-Mute Window

After TTS completes (`flush()` / `speak()` returns), `_vad_mute_until` is set to suppress echo for `TTS_ECHO_COOLDOWN_MS` (400ms telephony / 1500ms local). For cached WAV playback, `CachedTTS.speak_cached()` calls `sink.flush()` which sets `sink.last_flush_duration` — the pipeline uses this to size the mute window correctly.

---

## Hybrid TTS Cache System (branch: apicostreduction)

Pre-generated WAV files for 92 common responses. Cache hit = **~5ms latency, ₹0.00 cost**. Cache miss = **live Sarvam TTS, ~800ms, normal credit cost**.

### Architecture

```
LLM system prompt lists 22 CACHE keys
    ↓
LLM outputs "CACHE:faq_home_loan" (entire response)
    ↓
_parse_llm_response() detects CACHE token
    ↓
EngineResult(text="", cache_key="faq_home_loan") → _tts_layer
    ↓
CachedTTS.speak_cached("faq_home_loan", "hi")
    → reads tts_cache/faq_home_loan_hi.wav
    → streams PCM chunks to sink
    → sink.flush() → WAV injection → FreeSWITCH plays to caller
```

### Files

| File | Purpose |
|---|---|
| `calling-agent/tts_cache/` | 92 WAV files (30.9MB, ~735s total audio) |
| `calling-agent/tts_cache/manifest.json` | Index: `{key_lang: {key, lang, path, duration_s, chars}}` |
| `calling-agent/backend/generate_tts_cache.py` | Generator script (see below) |
| `calling-agent/backend/app/voice/tts_cached.py` | `CachedTTS` class |

### Cache Catalogue (32 keys × 3 languages = 92 WAVs)

**Shortcuts** (6 keys): `shortcut_hello`, `shortcut_bye`, `shortcut_thanks`, `shortcut_not_interested`, `shortcut_busy`, `stt_fallback`

**Stage transitions** (8 keys): `greeting`, `mid_call_greeting`, `ask_name`, `ask_property_type`, `ask_budget`, `ask_location_preference`, `invite_visit`, `confirm_interest`, `ask_appointment_date`, `appointment_confirmed`

**FAQs** (13 keys): `faq_about_company`, `faq_trust_track_record`, `faq_rera`, `faq_projects_overview`, `faq_price_range_general`, `faq_payment_plan`, `faq_home_loan`, `faq_booking_amount`, `faq_construction_quality`, `faq_possession_timeline`, `faq_amenities_general`, `faq_site_visit`, `faq_nearby_amenities`, `faq_location_connectivity`

**Language confirmations** (2 keys): `language_confirm_hi`, `language_confirm_gu`

All WAVs use voice `shubh`, model `bulbul:v3`. Language `en` is the fallback when a caller's language (`hi`, `gu`) key is missing.

### CachedTTS (`tts_cached.py`)

```python
class CachedTTS(TrackingSarvamTTS):
    def cache_has(self, key: str, lang: str) -> bool
    async def speak_cached(self, cache_key: str, lang: str, sink=None) -> bool
    # speak() inherited unchanged — live Sarvam TTS
```

`speak_cached()`:
1. Looks up manifest: exact `lang` match, then `"en"` fallback
2. Opens WAV, reads PCM in 4096-byte chunks → `sink.write_frame()`
3. Calls `sink.flush()` after all frames (sets `last_flush_duration` for echo-mute)
4. Checks `self._cancelled` before each chunk (respects barge-in)
5. Returns `True` on success, `False` on miss or I/O error

### WAV Generator (`backend/generate_tts_cache.py`)

```bash
cd calling-agent && source venv/bin/activate && cd backend

python generate_tts_cache.py              # generate all missing WAVs
python generate_tts_cache.py --dry-run    # preview only
python generate_tts_cache.py --validate   # check all WAVs on disk
python generate_tts_cache.py --force      # overwrite existing WAVs
python generate_tts_cache.py --key greeting   # regenerate one key only
```

Calls `POST https://api.sarvam.ai/text-to-speech` (not the streaming endpoint), base64-decodes `audios[0]`, writes WAV. Reads `SARVAM_API_KEY` from environment. 1s sleep between calls.

---

## Credit Tracking (credit_tracker.py)

`SarvamCreditTracker` (singleton) records every STT and TTS API call to JSONL files in `/home/angelo/lescall/credit_log/YYYY-MM-DD.jsonl`.

Each call end logs:
```
[CREDITS] Call ended: <uuid> | STT: Xs billed=Ys (N calls Sarvam / M Whisper) | TTS: C chars (N calls, B barge-in) | est ₹X.XXXX
```

`TrackingSarvamTTS.speak()` records TTS credit (chars + completion status) after each utterance. `barge-in=N` counts calls cancelled mid-stream.

**Credit cost reference** (approximate at current Sarvam rates):
- Greeting (106 chars, 1 TTS call) ≈ ₹0.32 per call
- Full conversation (1478 chars, 9 TTS calls) ≈ ₹4.59
- STT: 19 billed-seconds Sarvam ≈ included above

---

## Known Bugs (as of 2026-06-11, unfixed)

### Bug 1 — Greeting never served from cache (HIGH PRIORITY)

**Cost**: ₹0.32 per call wasted — every call fires a live TTS for the greeting even though `greeting_en.wav` exists.

**Root cause**: Two missing lines:
- `conversation.py:840` — `generate_opening()` returns `EngineResult(text=greeting, is_shortcut=True, intent="greeting")` with no `cache_key`.
- `pipeline.py:354` — the `Response` built from `opening` does not pass `cache_key=opening.cache_key`.

**Fix**:
```python
# conversation.py generate_opening(), line ~840
return EngineResult(text=greeting, cache_key="greeting", is_shortcut=True, intent="greeting")

# pipeline.py, line ~354
self._tts_queue.put_nowait(Response(
    text=opening.text,
    is_shortcut=True,
    cache_key=opening.cache_key,   # ADD THIS
    timestamp=time.monotonic(),
))
```

### Bug 2 — STT fallback phrase never served from cache

**Root cause**: `pipeline.py:864` creates `Response(text=fallback, is_shortcut=True, ...)` without `cache_key="stt_fallback"`. The `stt_fallback_en/hi/gu.wav` files are never used.

**Fix**: Add `cache_key="stt_fallback"` to the `Response` at line ~864.

### Bug 3 — LLM never outputs `CACHE:` tokens

**Root cause**: `_CACHE_ROUTER_INSTRUCTIONS` lists 22 keys with trigger conditions but the model prefers natural conversation over cache routing. In production logs, zero `[CACHE-ROUTE]` entries appear.

**Fix**: Rewrite instructions to be stage-aware and more forceful. Add: "OUTPUT ONLY 'CACHE:<key>' — nothing else, no greeting, no prefix." Organize keys by stage group with explicit negative examples.

### Bug 4 — Agent too social at GREETING stage

**Symptom**: Caller says "No, not at all. How are you?" → agent responds "I'm doing well, thank you! I appreciate you asking. How about you?" instead of advancing to name capture. Burns extra TTS credits on social chat.

**Fix**: Add to GREETING stage goal: "Do not engage in social chat or answer 'how are you?' questions. After the caller confirms it's a good time, immediately ask for their name."

---

## Memory System (memory.py)

Three-layer memory injected into each LLM call:

1. **`ConversationMemory`** — sliding window, last 5 turns (configurable)
2. **`ContextMemory`** — extracted `user_name`, `intent`, `topic`, `sentiment`
3. **`CallState`** — structured booking fields (ground truth, see above)

`QATracker` within `ConversationMemory` tracks agent turn quality to prevent repetition.

---

## Web Console (app/web/)

Optional `--web` flag adds FastAPI server (port 8000). `EventHub` (`event_hub.py`) is an async pub/sub broadcaster — `emit_nowait()` is non-blocking. Each WebSocket client has its own bounded queue. Slow client drops messages, never blocks pipeline.

---

## Call Audit (call_audit.py)

`ENABLE_CALL_AUDIT=true` → WAV recording + transcript in `./recordings/<conn_id>/`. Managed by `audit_manager` singleton imported by `ws_audio_receiver.py`.

---

## Directory Structure

```
/home/angelo/lescall/
├── socket_server.py                      ESL controller (system Python)
├── ws_audio_receiver.py                  WebSocket media bridge (venv)
├── conversation_call_bridge.py           Per-call session: wires TelephonySource/Sink
│                                         + VoicePipeline + playback injection loop
├── phase14_runtime/
│   ├── start_all.sh                      Service startup (order matters)
│   ├── stop_all.sh
│   ├── tail_logs.sh                      All logs
│   ├── tail_logs_filtered.sh             Errors + AI responses only
│   ├── check_runtime.sh                  Health check
│   └── logs/latest/                      Current run logs (ws_audio.log, socket.log)
├── credit_log/
│   └── YYYY-MM-DD.jsonl                  Per-call credit records
├── calling-agent/
│   ├── .env                              API keys + feature flags
│   ├── requirements.txt
│   ├── venv/                             Isolated Python env (MUST use for all backend)
│   ├── tts_cache/                        Pre-generated WAV files (30.9MB, 92 files)
│   │   └── manifest.json                 Index of all cached WAVs
│   └── backend/
│       ├── run.py                        CLI entrypoint (mic mode + --test + --web)
│       ├── generate_tts_cache.py         WAV cache generator script
│       └── app/
│           ├── audio_transport/
│           │   ├── base.py               AudioSource/AudioSink ABCs
│           │   ├── telephony_source.py   Receives PCM from ws_audio_receiver
│           │   ├── telephony_sink.py     Buffers PCM → WAV → queues for injection
│           │   └── local_*.py            Mic/speaker for CLI mode
│           └── voice/
│               ├── pipeline.py           Pipeline orchestrator (5 async tasks)
│               ├── vad.py                Energy VAD with auto-calibration
│               ├── turn_manager.py       Semantic turn completion heuristic
│               ├── conversation.py       ConversationRouter: stage machine + LLM + shortcuts
│               │                         Contains: EngineResult, CallState, _CACHE_ROUTER_INSTRUCTIONS,
│               │                                   _parse_llm_response(), detect_intent()
│               ├── memory.py             ConversationMemory + ContextMemory + CallState
│               ├── shortcuts.py          SHORTCUTS dict (trigger → response text)
│               │                         Note: inline shortcut dispatch is in conversation.py,
│               │                               not here — shortcuts.py is not called directly
│               ├── tts_cached.py         CachedTTS: speak_cached() + manifest loader
│               ├── tracking_tts.py       TrackingSarvamTTS: credit recording wrapper
│               ├── tts_sarvam.py         SarvamTTS HTTP streaming (DEFAULT_SPEAKER="shubh")
│               ├── tts_sarvam_ws.py      SarvamWSTTS WebSocket (DEFAULT_SPEAKER="shubh")
│               ├── tracking_stt.py       TrackingSarvamSTT: credit recording wrapper
│               ├── credit_tracker.py     SarvamCreditTracker singleton + JSONL logging
│               ├── event_hub.py          Async pub/sub for web console events
│               ├── call_audit.py         WAV + transcript recording
│               ├── main_loop.py          run.py bootstrap: mic test, pipeline init
│               └── stt/
│                   ├── base.py           STTProvider ABC + STTResult dataclass
│                   ├── router.py         STTRouter: Sarvam WS → HTTP → Whisper cascade
│                   │                     Contains: validate_transcript(), backoff logic
│                   ├── sarvam_ws.py      SarvamWSSTT: saaras:v3 WebSocket STT (primary)
│                   ├── sarvam.py         SarvamSTT: HTTP fallback STT
│                   ├── openai_whisper.py OpenAI Whisper fallback
│                   └── post_processor.py validate_transcript() helper functions
└── recordings/                           Call audit output (one dir per call)
```

---

## Key Invariants

- `SARVAM_API_KEY` is the correct env var name (not `SARVAMAI_API_KEY`)
- `socket_server.py` runs under system Python; everything else under `calling-agent/venv`
- All audio inside the AI pipeline is PCM16 at 16kHz
- FreeSWITCH delivers PCMU at 8kHz; `ws_audio_receiver.py` decodes (any frame size) and upsamples
- Greeting fires only after first audio frame from caller (`_greeting_done` gate)
- `selected_project` in `CallState` is never overridden after first confirmation
- `language_locked=True` is permanent per session unless caller explicitly switches language
- TTS echo cooldown applies in both telephony and local mode (different durations)
- Both TTS files (`tts_sarvam.py`, `tts_sarvam_ws.py`) use `DEFAULT_SPEAKER = "shubh"` (bulbul:v3)
- `VoicePipeline` instantiates `CachedTTS` (not `TrackingSarvamTTS`) by default
- `_parse_llm_response()` requires CACHE token to be the ENTIRE LLM response — even one extra word causes cache routing to fail
- WAV files injected to FreeSWITCH go via `/tmp/ai_telephony_playback/` and are played via ESL `uuid_broadcast`

---

## Historical Fixes

| Date | File | Fix |
|---|---|---|
| 2026-06-05 | `ws_audio_receiver.py` | PCMU decode: removed 160B size guard — now decodes all non-empty PCMU frames |
| 2026-06-05 | `pipeline.py` | `HARD_FLUSH_SILENCE_FRAMES` 30→50 (600ms→1000ms) to prevent Hindi/Gu sentence fragmentation |
| 2026-06-05 | `stt/sarvam_ws.py` | Full rewrite: single `unknown` connection, native language detection, removed 120 lines of heuristic dual-connection code |
| 2026-06-05 | `phase14_runtime/start_all.sh` | Added explicit `--sample-rate 8000` |
| 2026-06-02 | `pipeline.py` | Greeting gate: greeting fires after first audio frame, not at WebSocket open |
| 2026-06-02 | `stt/router.py` | Added unsupported-script fallback; ffmpeg normalization failure now drops WAV |
| 2026-06-11 | `tts_sarvam.py`, `tts_sarvam_ws.py` | `DEFAULT_SPEAKER` changed from `"priya"` to `"shubh"` |
| 2026-06-11 | `pipeline.py` | `Response` dataclass: added `cache_key: str \| None = None` |
| 2026-06-11 | `pipeline.py` | `VoicePipeline`: uses `CachedTTS` instead of `TrackingSarvamTTS` |
| 2026-06-11 | `pipeline.py` | `_tts_layer`: cache-first routing block; silence protection skips cache responses |
| 2026-06-11 | `pipeline.py` | `_conversation_layer`: propagates `cache_key` for both shortcut and LLM paths |
| 2026-06-11 | `conversation.py` | `EngineResult`: added `cache_key: str \| None = None` |
| 2026-06-11 | `conversation.py` | `_parse_llm_response()`, `_CACHE_ROUTER_INSTRUCTIONS`, `_consume_llm_with_filler()` — CACHE token interception |
| 2026-06-11 | `conversation.py` | Shortcut yields: `shortcut_bye`, `shortcut_thanks`, `shortcut_not_interested`, `shortcut_hello` wired to cache_key |
| 2026-06-11 | `tts_cached.py` | NEW: `CachedTTS` class with `speak_cached()` |
| 2026-06-11 | `generate_tts_cache.py` | NEW: WAV cache generator, 92 files generated and validated |
