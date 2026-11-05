# DHRON ↔ LesCall Voice Stack — Integration Discovery Report

**Scope**: read-only discovery of the in-production real-time voice pipeline in this
repository (`/home/angelo/lescall`), for a consumer (DHRON) that wants to drive it from a
clean device microphone over WebRTC instead of phone lines. Nothing in this repository was
modified.

**Method**: source reading, plus production configuration read from the live
`lescall-runtime` container and real log lines from
`phase14_runtime/logs/docker/ws_audio_receiver.log.1` (rotated 2026-08-13). Values marked
*(prod)* were read from the running system, not from defaults.

**Repo-relative paths** are used throughout. Repo root contains `ws_audio_receiver.py`,
`socket_server.py`, `conversation_call_bridge.py`; the AI pipeline lives under
`calling-agent/backend/app/`.

**The single most important finding, stated up front**: this stack has **no streaming STT
and therefore no partial hypotheses**. The WebSocket STT provider was deleted on
2026-07-31 by owner decision (`deploy/RUNBOOK.md` §24). The live path is a *batch*
multipart POST of one complete VAD-delimited segment. DHRON's `transcript.partial` event
has **no source in this codebase today**. Section 3 and Section 7 explain the consequences.

---

## 1. Audio ingress

### 1.1 How audio enters today

| Property | Value | Source |
|---|---|---|
| Producer | FreeSWITCH `mod_audio_stream`, dispatched by the ESL controller | `socket_server.py` (`AUDIO_STREAM_WS_URL`, line ~384) |
| Transport | WebSocket, **client → server**; FreeSWITCH dials us | `ws_audio_receiver.py:main_async` (`websockets.serve`) |
| Endpoint | `ws://127.0.0.1:9001/<fs_uuid>/<caller>/<did>/<campaign_id>/<agent_slug>` | `ws_audio_receiver.py:handle_connection` (path parse) |
| Bind address | `127.0.0.1` (loopback only, deliberate — see §6.2) | `ws_audio_receiver.py` `HOST_DEFAULT` / `LESCALL_WS_HOST` |
| Codec on the wire | **L16** (raw little-endian PCM16), *not* PCMU | `ws_audio_receiver.py` `INPUT_CODEC` / `TELEPHONY_INPUT_CODEC`, default `"l16"` |
| Sample rate in | 8 000 Hz | `--sample-rate` arg, default `8000`; set by `deploy/runtime_entry.sh` from `TELEPHONY_SAMPLE_RATE` (unset in prod → 8000) |
| Bit depth / channels | 16-bit signed, mono | `DEFAULT_BYTES_PER_SAMPLE = 2`; `pcm_to_wav(..., channels=1)` |
| Frame size | 20 ms → 160 samples → **320 bytes** | `EXPECTED_CHUNK_MS = 20` (`ws_audio_receiver.py:63`) |
| Alternate framing accepted | Twilio-style JSON text frames `{"event":"media","media":{"payload":"<base64>"}}` | `ws_audio_receiver.py:handle_connection`, text-payload branch |
| WS keepalive | server pings every 20 s, 10 s timeout (FreeSWITCH idle-times-out at ~30 s otherwise) | `ws_audio_receiver.py:main_async` |
| Max WS message | 1 MiB (`max_size=2**20`) | same |

A PCMU (µ-law) decode branch exists (`audioop.ulaw2lin`) but is **dead in production** —
it runs only if `TELEPHONY_INPUT_CODEC=pcmu`, and `mod_audio_stream` delivers already-decoded
L16 from the FreeSWITCH media bug.

### 1.2 Where audio becomes plain PCM frames — **the injection seam**

The normalization block in `ws_audio_receiver.py:handle_connection` does, per frame:

1. decode PCMU → PCM16 *(skipped in prod)*;
2. reject empty frames and odd byte counts (`[AUDIO_DROP]`);
3. **resample** `audioop.ratecv(norm_msg, 2, 1, args.sample_rate, TARGET_SR, resample_state)` —
   8 000 → 16 000 Hz, with `resample_state` carried across frames;
4. optionally forward to an external STT socket (`STT_FORWARD_HOST`, unset in prod);
5. `audit_manager.push_audio(conn_id, norm_msg)` — call recording;
6. **`call_bridge.push_audio(conn_id, norm_msg)`** ← *this is the seam*.

The call chain from there is three thin hops with no transformation:

```
ws_audio_receiver.py            call_bridge.push_audio(conn_id, pcm)
  → conversation_call_bridge.py SessionManager.push_audio      (dict lookup)
  → conversation_call_bridge.py TelephonySession.push_audio    (stamps first_audio_t)
  → audio_transport/telephony_source.py TelephonySource.push_frame(pcm)
```

**Exact frame format at the seam** — this is what DHRON must produce:

| Field | Required value |
|---|---|
| Container | none — a bare `bytes` object |
| Encoding | PCM, signed 16-bit, **little-endian** |
| Sample rate | **16 000 Hz** |
| Channels | 1 (mono, interleaving not applicable) |
| Frame duration | **20 ms** |
| Frame length | **640 bytes** (320 samples) |
| Byte-count parity | must be even (odd counts are dropped upstream, but nothing downstream re-checks) |

⚠ **The 20 ms frame duration is a hard, unvalidated contract.** Nothing re-frames audio
after the seam: `VoicePipeline._audio_vad_layer` iterates `self.audio.frames()` and calls
`_process_vad_frame(frame)` once per frame, and that method advances all of its clocks with
literal constants — `self._speech_duration_ms += 20.0`, `self._raw_speech_ms += 20.0`,
`0.02 * len(self._pre_speech_buffer)`, `_speech_end_ts = _now_flush - self._silence_frames * 0.02`
(`pipeline.py:1336`, `1389`, `1377`, `1507`). `SAMPLES_PER_FRAME` is imported into
`pipeline.py:25` and never used; `EXPECTED_CHUNK_MS` in `ws_audio_receiver.py:63` is never
read. A 10 ms or 30 ms frame would not raise — it would silently skew every duration
threshold, every SNR window, every speech-span timestamp, and the endpointer.

Additionally, `VoicePipeline._stt_submitter` posts audio to the provider with
`sample_rate=SAMPLE_RATE` **hardcoded to 16 000** (`pipeline.py:1955`, constant from
`voice/audio_input.py:SAMPLE_RATE`), regardless of what the source actually produced. Feeding
48 kHz at the seam would produce correctly-buffered but wrongly-labelled WAVs and garbage
transcripts.

### 1.3 Jitter buffering, resampling, gain between ingress and VAD

| Step | Present? | Where | Telephony-specific? |
|---|---|---|---|
| Jitter buffer / PLC | **No** | — | — |
| Resample 8k→16k | Yes, `audioop.ratecv` with persistent state | `ws_audio_receiver.py` | **Yes** — pure artefact of the 8 kHz phone leg |
| Queue / elastic buffer | `asyncio.Queue(maxsize=500)` = 10 s, **drop-oldest** on overflow | `telephony_source.py:TelephonySource.push_frame` | No |
| Underrun handling | `read_frame()` waits 1.0 s then returns a synthetic silence frame of `samples_per_frame * 2` bytes | `telephony_source.py:read_frame` | No |
| Gap/stall detection | Counters only (`GAP_STALL_MS = 200`, `max_gap_ms`, `stalls`) — logged at session end, **never acted on** | `ws_audio_receiver.py:handle_connection` | No |
| AGC / gain before VAD | **None.** The VAD sees raw PCM. | — | — |
| AGC / gain before STT | Yes, but only on the **upload** path: `DynamicNormalizer.process()` (per-call AGC + noise gate + soft limit), falling back to `normalize_pcm_audio(target_rms=3000.0, max_gain=8.0)` | `stt/sarvam.py:transcribe`; `stt/base.py:normalize_pcm_audio`; `stt/audio_norm.py` | **Yes** — written for quiet telephone lines |
| Spectral noise gate | Exists (`NoiseGate`, `voice/audio_input.py`) but **only** on `LocalMicSource`, and off by default (`noise_suppression=False`) | `audio_transport/local_mic_source.py:98` | No — this is the mic path |
| Debug tap | First 5 s of raw ingress written to `/tmp/phase15_audio_debug/` | `ws_audio_receiver.py:_Phase15AudioDebugger` | No |

There is no acoustic echo canceller anywhere. Its absence is load-bearing: the entire
echo-suppression / shadow-capture apparatus in §2 and §4 exists *because* the agent's own TTS
returns on the uplink through `mod_audio_stream` and nothing cancels it.

---

## 2. VAD and endpointing

Production runs `VAD_ENGINE=fsmn` *(prod, read from the `lescall-runtime` container env)*.
The legacy `energy` engine (`voice/vad.py:VoiceActivityDetector`) is still present as a
fallback and as the degraded mode, but does not decide speech in production.

### 2.1 The model and its I/O

**Two stages**, both in `voice/vad_stack.py:FsmnVadStack`.

**Stage 1 — WebRTC VAD pre-filter** (`webrtcvad.Vad(1)`, aggressiveness 1 "permissive"):

| Property | Value |
|---|---|
| Input | the raw 20 ms / 16 kHz / mono `bytes` frame, via `self._webrtc.is_speech(frame, self.sample_rate)` |
| Output | `bool` per frame |
| Authority | **none over turn-taking.** It can only skip FSMN inference during confirmed idle |
| Gate condition | skipped only when no segment is open *and* `idle_ms >= VAD_PREGATE_IDLE_MS` (**600 ms**) |
| Failure mode | import failure → gate permanently open, FSMN runs full-rate (logged, not fatal) |
| Re-open behaviour | on re-open the model cache is reset and the last ≤3 hops are replayed so onsets are not scored cold |

**Stage 2 — FunASR FSMN-VAD (the decision maker)**:

| Property | Value | Source |
|---|---|---|
| Library | `funasr_onnx.Fsmn_vad_online` (ONNX Runtime, Apache-2.0) | `vad_stack.py:preload_model` |
| Model files | vendored in-repo at `data/models/fsmn_vad` (`FSMN_VAD_MODEL_DIR` overrides) | `vad_stack.py:_DEFAULT_MODEL_DIR` |
| Quantization | `quantize=False` — the quantized model measured **slower** on this VM (dequant overhead) | `vad_stack.py:preload_model` + module docstring |
| ORT threads | `intra_op_num_threads=1` (mandatory on the 2-core box) | same |
| Expected input | mono, 16 kHz, **float32 scaled to [-1, 1]** (`pcm16 / 32768.0`) | `vad_stack.py:_worker_loop` |
| Granularity | **100 ms hops** (`HOP_MS = 100`, 5 × 20 ms frames buffered) | `vad_stack.py` |
| Internal end-of-speech silence | `max_end_sil = FSMN_MAX_END_SIL_MS` = **280 ms** | `vad_stack.py:50` |
| Raw output | `segs[0]` — a list of `(beg_ms, end_ms)` pairs; `-1` marks "not yet known" | `vad_stack.py:_worker_loop` |
| Exposed output | **events, not probabilities**: `("speech_start", ms)` / `("speech_end", ms)` via `pop_events()`; latched boolean via `speech_open` property | `vad_stack.py:_push_event`, `pop_events`, `speech_open` |
| Per-frame API | `is_speech(frame: bytes) -> bool` — returns the latched `_speech_open`, **not** a per-frame verdict | `vad_stack.py:is_speech` |

⚠ **No speech probability is available anywhere.** The FSMN wrapper emits boundary events
only. If DHRON wants a confidence-per-frame signal it does not exist and would have to be
added inside `_worker_loop`.

⚠ **The `/32768.0` normalization is load-bearing and was a real bug for five days.** funasr's
`WavFrontendOnline` multiplies its input by `1<<15` before the Kaldi fbank, i.e. the model is
calibrated on `[-1,1]` waveforms. Feeding raw int16 made every frame 32768× hot; measured on
call `5aaee2cd`, 7 of 25 flagged "speech" segments were silence (28%), versus 2 of 20 (10%)
at the correct scale (`vad_stack.py:_worker_loop` comment, 2026-08-04).

**Energy is still computed but makes no speech decision**: `_last_energy = mean(abs(int16))`
per frame. It survives as the *only* echo discriminator available to barge-in — a model VAD
happily classifies the agent's own TTS echo as speech.

### 2.2 Threshold reference

| Constant | Prod value | Meaning | Defined in |
|---|---|---|---|
| `VAD_ENGINE` | `fsmn` *(prod)* | engine selector | `pipeline.py:48`, compose env |
| `FSMN_MAX_END_SIL_MS` | 280 | model-internal end-of-segment silence | `vad_stack.py:50` |
| `VAD_PREGATE_IDLE_MS` | 600 | idle before stage-1 gates FSMN off | `vad_stack.py:55` |
| `HOP_MS` / `FRAME_MS` | 100 / 20 | model hop, pipeline frame | `vad_stack.py:43-44` |
| `minimum_threshold` | **260.0** (telephony) / 150.0 (local) | energy floor, echo/barge use only | `pipeline.py:606` |
| `minimum_rms_floor` | **60.0** (telephony) / 50.0 | absolute silence floor (energy engine) | `pipeline.py:607` |
| `barge_in_threshold` | `max(260 × 2.0, 500)` = **520** | barge-in energy bar | `vad_stack.py:barge_in_threshold` |
| `SPEECH_ONSET_MIN_FRAMES` | 2 (40 ms) | onset debounce | `pipeline.py:263` |
| `SPEECH_HOLD_MIN_FRAMES` | **10 (200 ms)** | hysteresis hold after speech stops | `pipeline.py:264` |
| pre-speech ring | **20 frames (400 ms)** on fsmn; 2 frames on energy | recovers onset clipped by model lag | `pipeline.py:1350` |
| `EP_FAST_RESIDUAL_FRAMES` | 2 (40 ms) | `fast_complete` residual | `endpointer.py:36` |
| `EP_DEFAULT_RESIDUAL_FRAMES` | 7 (140 ms) | `default` residual | `endpointer.py:37` |
| `EP_HOLD_RESIDUAL_FRAMES` | 26 (520 ms) | `hold` residual | `endpointer.py:38` |
| `HARD_FLUSH_SILENCE_FRAMES` | **20 (400 ms)** *(prod)* | legacy-engine flush; unused on fsmn | `pipeline.py:275`, compose env |
| `TURN_SILENCE_THRESHOLD_MS` | 1200 | TurnManager backstop | `pipeline.py:278` |
| `MIN_SPEECH_MS` | 300 | padded-duration floor | `pipeline.py:134` |
| `MIN_REAL_SPEECH_MS` | **200** | model-confirmed speech floor (the one that bites) | `pipeline.py:148` |
| `MIN_SPEECH_SNR` | 1.2 | speech-vs-own-ambient ratio | `pipeline.py:170` |
| `SNR_ENFORCE_BELOW_MS` | 600 | SNR guard applies to short segments only | `pipeline.py:176` |
| `SNR_MIN_NOISE_FRAMES` | 10 | ambient frames needed before SNR is judged | `pipeline.py:171` |
| `MAX_SPEECH_SECONDS` | 25 | buffer cap | `pipeline.py:129` |
| `VAD_DEGRADE_DEPTH` | 32 | worker-queue depth → degrade to energy | `vad_stack.py:154` |
| `AEC_RMS_FLOOR` | 60 | classifier silence floor (absolute int16) | `audio_events.py:41` |

### 2.3 How VAD output becomes endpointing

Endpointing is **not** in the VAD. It is a four-layer decision, all inside
`pipeline.py:_process_vad_frame` plus `endpointer.py`.

**Layer A — hysteresis (`pipeline.py:1355-1360`)**

```python
if raw_speech and self._speech_onset_frames < 2 and not self._prev_speech:
    speech = False                     # onset debounce: 40 ms
elif not raw_speech and self._silence_frames < 10 and self._prev_speech:
    speech = True                      # hold: 200 ms of trailing silence stays "speech"
else:
    speech = raw_speech
```

**Layer B — the flush gate (`pipeline.py:1424-1457`)**. Only consulted when
`not speech` *and* `self._speech_duration_ms >= self._min_speech_ms(now)`:

```python
_flush_due = self._endpointer.should_flush(
    self._silence_frames,          # int, 20 ms each
    self._speech_duration_ms,      # padded duration
    self._endpoint_hint_text(),    # see below
    False,                         # stream_active — always False since RUNBOOK §24
)
```

**Layer C — `EndpointController.should_flush` (`endpointer.py:59-87`)**, which picks a
profile fresh on every silence frame:

| Profile | Trigger | Required silence frames |
|---|---|---|
| `hold` | tail matches `_DANGLING` (dangling conjunction in en/hi/gu, incl. `और`, `लेकिन`, `aur`, `lekin`, `but`, `so`, `because`…) **or** `_DIGIT_TAIL` (a number burst — the phone-number/budget dictation trap) | 26 (520 ms) |
| `fast_complete` | tail matches `[.!?।]\s*$` **and** ≥ 2 words | 2 (40 ms) |
| `default` | anything else, including empty text | 7 (140 ms) |

**Critical**: because Layer A holds `speech = True` until `silence_frames >= 10`, the
endpointer is first consulted at 10 frames — already past both the `default` (7) and
`fast_complete` (2) thresholds. **In practice the 200 ms hysteresis hold, not the endpointer,
sets the residual for two of the three profiles.** Production logs confirm this exactly:

```
06:50:41.784 [ENDPOINT] flush profile=default silence=10/7 speech=960ms  text_tail=''
06:50:58.584 [ENDPOINT] flush profile=default silence=10/7 speech=7180ms text_tail=''
06:51:16.284 [ENDPOINT] flush profile=default silence=10/7 speech=1360ms text_tail='ओके।'
```

Only `hold` (26 frames) actually extends the endpoint.

**Semantic signal availability**: `turn_text` is *not* the current utterance — there is no
mid-turn text on a batch path. `pipeline.py:_endpoint_hint_text()` returns the text of the
**previous fragment of the same turn**, and only while within `CONTINUATION_WINDOW_MS`
(2000 ms); otherwise `""`. RUNBOOK §24 states the endpointer "now always receives `("", False)`";
that is stale — the hint was reinstated in the 2026-08-04 VAD audit precisely because
`_DIGIT_TAIL` "had never once executed on a live call" (`pipeline.py:1428-1443`). The sampled
production logs show `text_tail=''` on most turns regardless.

**Layer D — post-flush admission gates (`pipeline.py:1467-1504`)**. A flushed segment is
still discarded if any of:

- `_too_short`: padded `_speech_duration_ms < 300 ms` (120/200 ms inside the connect window);
- `_no_speech`: model-confirmed `_raw_speech_ms < 200 ms` — measured on FSMN verdicts only,
  excluding the 400 ms pre-ring and 200 ms hold;
- `_dead_line`: segment SNR < 1.2× the caller's own ambient, applied **only** to segments
  with < 600 ms of real speech and only when ≥ 10 ambient frames were measured.

**Backstop — `TurnManager`** (`voice/turn_manager.py`), configured
`silence_threshold_ms=1200, grace_window_ms=300, min_words_short=2, min_words_long=3`
(`pipeline.py:835`). On the batch path it is fed **proxy tokens** — `" ".join(["x"] * proxy_words)`
where `proxy_words = max(1, int(speech_duration_ms / 300))` (`pipeline.py:1406`) — so its
semantic path is structurally inert; it fires on word-count-vs-silence only and hard-flush
normally wins first.

**Measured end-to-end endpoint budget** (production, 2026-08-13 log):

| Stage | Cost |
|---|---|
| FSMN internal end-silence | 280 ms |
| Hysteresis hold before flush is considered | 200 ms |
| Endpointer residual (`default`, already satisfied) | 0 ms additional |
| **VAD-side total, speech end → segment enqueued** | **≈ 400–480 ms** |
| Batch STT round trip (see §3) | 179–817 ms observed |
| **`[LATENCY] speech_end→engine`** | **413 ms / 421 ms** (fast turns), up to 7024 ms (merge/queue-bound turns) |

### 2.4 How the VAD is invoked

**In-process Python library call.** No service, no IPC, no protocol.

```python
# calling-agent/backend/app/voice/vad_stack.py
class FsmnVadStack:
    def __init__(self, sample_rate: int = 16000, model_dir: str | None = None,
                 minimum_threshold: float = 260.0, minimum_rms_floor: float = 60.0,
                 barge_in_multiplier: float = 2.0, barge_in_minimum: float = 500.0,
                 event_sink=None) -> None: ...

    def is_speech(self, frame: bytes) -> bool          # 20 ms PCM16 frame in, latched verdict out
    def pop_events(self) -> list[tuple[str, float]]    # [("speech_start", 650.0), ("speech_end", 1740.0)]
    def stats(self) -> dict                            # health snapshot, see below
    @property
    def speech_open(self) -> bool
    @property
    def threshold(self) -> float
    @property
    def last_energy(self) -> float
    @property
    def barge_in_threshold(self) -> float
    def close(self) -> None
```

`pop_events()` timestamps are **stream-relative milliseconds** (audio-time position fed to
FSMN, `self._stream_ms`), not wall clock and not monotonic clock. Real output:

```
06:51:14.186 [fsmn-vad] [VAD-STACK] speech_start @650ms
06:51:14.886 [fsmn-vad] [VAD-STACK] speech_end   @1740ms
```

⚠ `pop_events()` is **drain-on-read and has no consumer in `pipeline.py`.** The pipeline reads
`is_speech()` only. The events exist for diagnostics and the audio-event classifier. If DHRON
wants them, it is currently a free channel — but a second consumer would race the first,
because `pop_events` clears the list.

`EndpointController` signature (`voice/endpointer.py`):

```python
class EndpointController:
    last_profile: str                      # "fast_complete" | "default" | "hold"
    def should_flush(self, silence_frames: int, speech_ms: float,
                     turn_text: str, stream_active: bool) -> bool
```

### 2.5 Compute profile and saturation

| Property | Value | Source |
|---|---|---|
| Threads per stream | **1** dedicated daemon thread, `name="fsmn-vad"` | `vad_stack.py:__init__` |
| Model instances | **1 shared per process**, guarded by `_MODEL_LOCK`; per-stream state lives entirely in the caller's `param_dict` | `vad_stack.py:preload_model` + docstring |
| Inference concurrency | **serialized process-wide** by `_INFER_LOCK` — deliberate on 2 cores ("batch, don't parallelize") | `vad_stack.py:_worker_loop` |
| Hop queue | `queue.Queue(maxsize=64)` ≈ 6.4 s of audio | `vad_stack.py:143` |
| Per-hop cost (docstring, this VM) | p50 1.6 ms / p95 2.4 ms / max 8 ms | `vad_stack.py` module docstring |
| Per-hop cost (**production, single call**) | p50 **0.2 ms**, p99 **4.5–6.3 ms**, `deadline_misses: 0`, `hops_dropped: 0`, `degraded: False` | live log, e.g. `{'hops_in': 1800, 'hops_dropped': 0, 'queue_depth': 0, 'hop_lat_p50_ms': 0.2, 'hop_lat_p99_ms': 4.8, 'deadline_misses': 0, 'degraded': False}` |
| Frame-path cost | 65 µs p50 | `deploy/PLAN-vad-stack.md` |
| Warm-model preload | `preload_model()` called at process boot when `VAD_ENGINE=fsmn` | `ws_audio_receiver.py:_boot_warmup` |
| Cold-start cost avoided | funasr import ≈ 1–3 s (librosa) on first construction | `vad_stack.py:143` comment |

**What saturation looks like — by design, degradation before loss**:

1. Queue depth ≥ `VAD_DEGRADE_DEPTH` (**32**) → `self.degraded = True`, logged
   `[VAD-STACK] DEGRADED to energy VAD (queue depth N ≥ 32 — CPU starved)`. `is_speech()`
   then returns `last_energy > minimum_threshold` — worse decisions, but **visible**.
2. Recovery when depth < `32 // 4` = 8 → `[VAD-STACK] recovered from degraded mode`.
3. Only at queue-full (64) are hops actually **dropped** (`hops_dropped++`, one-shot warning
   `[VAD-STACK] worker queue full — dropping hops (CPU starved?)`). The comment is explicit
   that dropped hops corrupt endpointing *silently*, which is why the degrade tier exists.
4. Health is logged every 300 hops (~30 s) via `stats()`.

Separately, at the transport layer: `TelephonySource` drops the **oldest** queued frame at 500
frames (10 s), and the STT queue (`QUEUE_SIZE_STT = 6`) evicts the **oldest** segment to admit
the newest, counting `_stt_queue_drops` — described in-code as "an alarm condition… the agent
answered without hearing part of what was said" (`pipeline.py:802`).

**Concurrency ceiling: no code limits concurrent streams.** `SessionManager.sessions` is an
unbounded dict. The only measured sweep found in this environment is
`/home/angelo/callsim/runs/knee_sweep.out` (2026-08-03, `callsim-stress`, herd/staggered,
N = 1…4 against the sandbox):

| Concurrency | wall_s | per-call `resp_p50` (ms) |
|---|---|---|
| 1 | 23.3 | 2418 |
| 2 | 23.2 | 2762, 2189 |
| 3 | 33.2 | 815, 2142, 2119 |
| 4 | 23.2 | 2513, 3150, 1868, 2060 |

No knee is visible up to 4, and `vad_health_lines` came back **empty** in every run — the
harness did not actually harvest the VAD stats it was built to harvest. **The real ceiling is
UNKNOWN.** Note also that `_INFER_LOCK` serializes inference across *all* concurrent calls in
the process, so the VAD is a shared, single-threaded resource: N calls × 10 hops/s each must
fit in one lock at ~0.2–5 ms per hop. Hardware context: a **2-core / 3.8 GB VM** shared with
FreeSWITCH, Postgres and four containers (`deploy/PLAN-vad-stack.md`).

### 2.6 What assumes telephony and needs retuning for a clean 16/48 kHz mic

| Assumption | Where | Impact on a clean mic |
|---|---|---|
| 8 kHz ingress → `ratecv` upsample to 16 k | `ws_audio_receiver.py` | Skip entirely; band-limited 8 k audio is *not* what the mic gives you. FSMN sees genuinely wideband speech for the first time — model behaviour on 16 k-native input is untested here |
| `minimum_threshold=260`, `rms_floor=60` under `TELEPHONY_MODE` | `pipeline.py:606-607` | Tuned for phone-line levels. `TELEPHONY_MODE=false` already selects 150/50 — closer, but still uncalibrated for a WebRTC AGC'd mic |
| `barge_in_threshold = max(260×2, 500) = 520` | `vad_stack.py` | Absolute int16 bar. A WebRTC mic with browser AGC sits at very different levels; barge-in will be either hair-trigger or deaf |
| `ECHO_EXIT_ENERGY_FACTOR × threshold` echo-gate exit | `pipeline.py:250` | Same absolute-level problem |
| `AEC_RMS_FLOOR = 60` absolute int16 in the DSP classifier | `audio_events.py:41` | Same |
| **All echo suppression**: `TTS_ECHO_COOLDOWN_MS`, `_vad_mute_until`, shadow ring, `_is_agent_echo_text` | `pipeline.py:206-259`, `1249-1325`, `1608-1673` | Exists solely because the agent's TTS returns on the telephony uplink. WebRTC gives you AEC for free; this machinery becomes dead weight that *costs* responsiveness (mute windows sized from `playback_busy_until + tail`) |
| Dead-media detector: "µ-law silence decodes to 0, ±8, ±16, ±24 — never one repeated value" | `pipeline.py:548-564`, `_track_media_liveness` | The zero-variance heuristic is a µ-law property. A WebRTC stream with comfort noise / DTX may legitimately produce constant runs → false "dead line" closes. `DEAD_MEDIA_ENABLED` defaults to `TELEPHONY_MODE` |
| Call-progress tone classifier: RINGBACK/BUSY at 400–450 Hz, **India cadences** (busy ≈ 750/750 symmetric; ringback long gap > 1200 ms), FAX CNG 1100 Hz / CED 2100 Hz, DTMF grid | `audio_events.py:_DTMF_ROWS/_COLS`, `_ring_or_busy` | Meaningless on a device mic. Harmless (shadow-only, gates nothing inbound) but wasted CPU |
| `CONNECT_*` window: bare-"hello" loop counters, forced language on short first segments | `pipeline.py:342-362` | Models a caller who cannot tell whether the line is live. Irrelevant with a WebRTC session that has an explicit connect state |
| Playback path: `TTS_OUTPUT_SR=8000`, ffmpeg downsample to 8 k, WAV-file injection | `telephony_sink.py:_normalize_to_8k`; `conversation_call_bridge.py:TTS_OUTPUT_SR` | Whole sink is wrong for WebRTC — see §4 and §7 |
| STT posts at hardcoded 16 000 Hz | `pipeline.py:1955` | Must feed exactly 16 k, or resample before the seam |
| Whisper hedge cannot do Gujarati (`gu` not in `WHISPER_LANGUAGES`) | `stt/openai_whisper.py:20-28` | Unrelated to transport, but inherited |

---

## 3. Streaming STT

### 3.1 There is no streaming STT

`deploy/RUNBOOK.md` §24 (2026-07-31, owner request): the saaras:v3 WebSocket provider was
**deleted from the code**, not switched off — `app/voice/stt/sarvam_stream.py` (476 lines) and
`app/voice/stt/sarvam_ws.py` (216 lines) removed, along with `ENABLE_STREAMING_STT`,
`BARGE_KEEP_STREAM_AUDIO`, the `_streaming_stt` handle, both `feed_nowait` sites in the VAD
loop, the mid-turn `turn_text` feed, and `router.last_covered_seconds` /
`set_stream_language`. The RUNBOOK is explicit: *"To re-introduce streaming, restore the
provider from git history — it is no longer a flag flip."* The stated cost of the rollback:
*"the ~300-800 ms overlap saving is gone. Every turn now pays the full batch POST after the
endpoint fires."*

`app/voice/stt_stream.py` survives as a **dormant abstract interface** with no
implementation — its own docstring says *"Nothing implements it today."* The only concrete
subclass, `PlaceholderSTT`, emits fabricated text (`"[speech 1.2s] detected audio active…"`)
purely to give `TurnManager` word-count signals, and is not on the telephony path.

### 3.2 Engine and invocation (the live path)

| Property | Value | Source |
|---|---|---|
| Primary | Sarvam AI STT | `stt/sarvam.py:SarvamSTT` |
| Transport | **REST**, `POST https://api.sarvam.ai/speech-to-text`, `multipart/form-data` | `stt/sarvam.py:SARVAM_API_URL`, `transcribe` |
| Client | shared per-process `httpx.AsyncClient` (`SARVAM_HTTP_SHARED=true`), keepalive 300 s, max 5 keepalive conns | `stt/sarvam.py:_make_client` |
| Model | `saarika:v2.5` — **owner decision, do not change without sign-off** | `stt/sarvam.py:SARVAM_STT_MODEL` |
| `mode` param | sent **only** for `saaras*` models; never for saarika | `stt/sarvam.py:transcribe` |
| Auth | header `api-subscription-key: <key>`, from a rotating per-call leased key pool | `stt/sarvam.py`, `voice/key_rotator.py` |
| Timeouts | connect **3.0 s**, read **2.5 s**, blanket 10 s | `SARVAM_CONNECT_TIMEOUT_S`, `SARVAM_READ_TIMEOUT_S`, `SARVAM_TIMEOUT` |
| Fallback | OpenAI `whisper-1`, 8.0 s timeout | `stt/openai_whisper.py` |
| Router | `stt/router.py:STTRouter.transcribe` | — |

**Request** (`stt/sarvam.py:transcribe`):

```http
POST /speech-to-text HTTP/1.1
Host: api.sarvam.ai
api-subscription-key: <leased key>
Content-Type: multipart/form-data

form fields:  language_code = "unknown" | "en-IN" | "hi-IN" | "gu-IN"
              model         = "saarika:v2.5"
              (mode         = "codemix")      # saaras models only — absent in prod
file field:   file = ("audio.wav", <in-memory WAV>, "audio/wav")
```

The WAV is built in memory by `stt/base.py:pcm_to_wav(pcm, sample_rate=16000, channels=1,
sample_width=2)` **after** per-call dynamic normalization.

**Response parsing** (`stt/sarvam.py:transcribe`, lines 251-268):

```python
data = response.json()
transcript      = data.get("transcript", "").strip()
_conf_raw       = data.get("confidence")            # always None in practice
confidence      = float(_conf_raw) if _conf_raw is not None else None
detected_lang   = data.get("language_code") or None
_lang_prob      = data.get("language_probability")  # saaras-only, and null when language is pinned
```

Status handling: `402` → mark key exhausted, rotate pool, raise; `429` → `SarvamRateLimitError`
carrying `Retry-After`, then exponential backoff 60 s → 300 s with all traffic routed to
Whisper; timeout / stale-connection → retire the client so the retry gets a fresh socket.

**Router return type** (`stt/base.py:STTResult`):

```python
@dataclass
class STTResult:
    text: str
    confidence: float | None = None       # None when nothing real is known
    detected_language: str | None = None  # "en-IN" | "hi-IN" | "gu-IN"
    source: str = "primary"               # "primary" | "primary-script-retry" | "fallback"
```

### 3.3 Partial hypotheses

**None exist.** There is no emission cadence, no message format, no stability field, and no
revision mechanism, because no partial is ever produced. The dormant type is:

```python
# calling-agent/backend/app/voice/stt_stream.py
@dataclass
class STTPartial:
    text: str
    is_final: bool = False
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.monotonic)
```

Nothing constructs it except `PlaceholderSTT`.

The closest thing to incremental behaviour is the **transcript merge layer**
(`pipeline.py`, `TRANSCRIPT_MERGE_WINDOW_MS = 550`, `_ends_with_continuation_cue`,
`CONTINUATION_*`), which stitches *multiple finals* from one utterance into one turn after the
fact. That is post-hoc repair, not partials.

### 3.4 Final transcripts

| Question | Answer |
|---|---|
| What triggers finalisation? | **Our local endpoint**, not the engine. `_process_vad_frame` flushes an `AudioSegment` → `_stt_submitter` POSTs it → one response = one final |
| Format | plain UTF-8 string, provider-punctuated (Sarvam appends `.` / `।` even mid-thought) |
| Word-level timestamps | **No.** Not requested, not returned, not parsed |
| Utterance-level timestamps | Yes, but **ours**: `AudioSegment.speech_start_ts` / `speech_end_ts` (monotonic clock), computed as `_speech_end_ts = _now_flush - silence_frames * 0.02`, with an implausibility guard that zeroes the span when it disagrees with the buffered duration by > 1 s (`pipeline.py:1506-1523`) |
| Confidence | Provider returns none. `STTRouter._derive_confidence` synthesizes 0.05–1.0 from: words-per-second vs audio duration (×0.3 above 8 wps, ×0.6 above 6 wps — the hallucination over-generation signature), one word from ≥ 1.5 s of audio (×0.6), function-word quality score < 0.45 (×0.75), latency > 10 s (×0.85). Gate `STT_MIN_CONFIDENCE = 0.4` |
| Language tagging | Per-segment `language_code`. Mirrored into conversation state only when `LANG_MIRROR_GUARDED` and `stt_mirror_consistent(...)` agree with the transcript's own script/markers |
| Code-switch (en/hi/gu) | **Segment-level only.** No intra-utterance switch points, no per-word language. The pin (`STT_LANG_PIN`, 2 stable caller turns, forced auto-detect recheck every 8) fights auto-detect misfires |
| Rejection layer | `validate_transcript()` drops YouTube-style hallucinations (Latin + Devanagari), unsupported scripts (Korean/Tamil/Malayalam/Bengali/Arabic…), Marathi via word markers, fillers, repetition, single-word noise tokens, function-word-only fragments (quality < 0.3) |
| Extra plausibility gate | `pipeline.py:2035-2053`: > 2× `max(4, duration_ms/250)` words → discard as hallucination; > 1× → log `[STT] Suspicious word count` |

### 3.5 Latency

| Path | Value | Source |
|---|---|---|
| Frame → partial | **N/A — no partials** | — |
| Endpoint → final (healthy, observed) | **179 / 193 / 211 / 219 / 237 / 272 / 456 / 817 ms** | production log, 2026-08-13 |
| Healthy band (documented) | 150–450 ms median, < 1 s p95 | `stt/sarvam.py:22-31` |
| Primary-race hedge | duplicate primary request at **0.6 s** (`STT_PRIMARY_RETRY_HEDGE_S`) | `stt/router.py:57` |
| Whisper hedge | parallel Whisper at **1.0 s** (`STT_HEDGE_DELAY_S`), primary keeps a 0.3 s grace to win anyway | `stt/router.py:49, 65` |
| Whisper healthy latency | ≈ 1.5 s; capped at 8 s | `stt/openai_whisper.py:30-34` |
| Prefetch | `STT_PREFETCH_AHEAD = 1` — up to ~3 provider requests in flight; results still **delivered strictly in order** | `pipeline.py:123`, `_stt_submitter` / `_stt_worker` |
| Speech end → engine (measured, prod) | **413 ms**, **421 ms** on clean turns; 2610 / 3826 / 7024 ms when the merge window or queue is involved | `[LATENCY] speech_end→engine` |
| Speech end → first audio (measured, prod) | **4827 / 6259 / 11144 ms** — includes LLM + TTS + FreeSWITCH injection | `[LATENCY] speech_end→first_audio` |

### 3.6 Sample payloads

**Partial** — none. Nothing in this repository, its tests, its fixtures, or its logs contains
a partial-transcript message. This is not a gap in my search; the producer was deleted
(RUNBOOK §24).

**Final** — real production log lines (`phase14_runtime/logs/docker/ws_audio_receiver.log.1`,
2026-08-13; utterances chosen to contain no customer identifiers):

```
06:50:22.157 INFO [MainThread] [STT:Sarvam] Transcript: 'Okay.' (confidence=n/a, lang=en-IN)
06:51:15.278 INFO [MainThread] [STT:Sarvam] Transcript: 'ओके।' (confidence=n/a, lang=hi-IN)
06:51:16.504 INFO [MainThread] [STT:Sarvam] Transcript: 'चलो ठीक है, बाय बाय।' (confidence=n/a, lang=hi-IN)
06:51:16.505 INFO [MainThread] [LATENCY] speech_end→engine 421ms (stt=219ms)
```

The corresponding HTTP body, reconstructed field-by-field from the parse in
`stt/sarvam.py:251-268` and the logged values (`confidence=n/a` ⇒ the key is absent or null;
`p=` absent ⇒ `language_probability` null because the language was pinned):

```json
{
  "transcript": "चलो ठीक है, बाय बाय।",
  "language_code": "hi-IN",
  "confidence": null,
  "language_probability": null
}
```

The object the rest of the pipeline actually consumes (`pipeline.py:2248-2257`):

```python
Transcript(
    text="चलो ठीक है, बाय बाय।",
    duration_ms=1360.0,
    timestamp=<monotonic>,
    stt_latency_ms=219.0,
    speech_start_ts=<monotonic>,   # 0.0 when the span failed its plausibility check
    speech_end_ts=<monotonic>,
    confidence=0.85,               # derived, never from the provider
    stt_source="primary",
)
```

⚠ I did not capture a raw HTTP response body off the wire — that would require modifying the
code or proxying live traffic, both out of scope for read-only discovery. The JSON above is a
faithful reconstruction of the four fields the code reads, not a byte-level capture. Any
field Sarvam returns that this code does not read is **UNKNOWN**.

---

## 4. TTS and barge-in

### 4.1 TTS

| Property | Value | Source |
|---|---|---|
| Engine | Sarvam Bulbul | `voice/tts_sarvam.py` |
| Endpoint | `POST https://api.sarvam.ai/text-to-speech/stream` | `SARVAM_TTS_STREAM_URL` |
| Model / speaker | `bulbul:v3` / `shubh` (per-agent + per-language override via `agent.yaml tts_voices`) | `DEFAULT_MODEL`, `DEFAULT_SPEAKER` |
| Valid models (400 otherwise → silent call) | `bulbul:v2`, `bulbul:v3-beta`, `bulbul:v3` | `VALID_MODELS` |
| Streaming? | **Yes** — `client.stream("POST", ...)`, `response.aiter_bytes(4096)`, chunks written to the sink as they arrive | `_stream_audio` |
| Output format | `output_audio_codec: "linear16"`, `speech_sample_rate: self._sample_rate` | `_stream_audio` payload |
| Prod output rate | **8 000 Hz** (`TTS_OUTPUT_SR=8000`) — synthesizes at telephony rate so the sink skips ffmpeg | `conversation_call_bridge.py:TTS_OUTPUT_SR` |
| Request body | `{text, target_language_code, speaker, model, pace, speech_sample_rate, output_audio_codec}` | `_stream_audio` |
| Pace | base × language factor (hi 1.08, gu 1.0, en 1.0) × long-sentence damping (0.93 above 140 chars), clamped [0.5, 2.0] | `_effective_pace` |
| Timeouts | first attempt **2.0 s**, later **3.5 s** inter-chunk; connect 5.0 s; up to 8 retries (also covers 402 key rotation) | `STREAM_TIMEOUT*`, `max_retries` |
| Double-speak guard | once any audio has reached the sink, a mid-stream failure **truncates** instead of retrying | `_stream_audio`, `yielded_any` |
| Canned-line cache | greeting / fillers / response-bank lines replay from disk PCM — 0 synthesis latency, 0 credits (`TTS_CANNED_CACHE=true`, `TTS_CACHE_MAX_ENTRIES=500`) | `speak_cached`, `_cache_path` |
| Incremental flush | utterance split into parts; first part flushes at 0.9–1.6 s of audio, hard ceiling 4.0 s | `TTS_EARLY_FLUSH`, `_EARLY_FLUSH_*`, `_MAX_FLUSH_S` |

**Output path (telephony)** — `audio_transport/telephony_sink.py:TelephonySink`:
`write_frame()` appends PCM to a buffer; `flush()` writes a mono/16-bit WAV to
`/tmp/ai_telephony_playback/`, downsamples to 8 k via an **ffmpeg subprocess** if the rate
isn't already 8 k (dropping the file outright if that fails — "a skipped utterance beats demon
audio"), validates it (> 0 bytes, > 100 ms), and pushes the path onto `wav_queue`. The bridge's
`_playback_loop` then sends a length-prefixed JSON `playback_request` to `socket_server` on TCP
9004, which injects it into the FreeSWITCH channel.

### 4.2 The barge-in path, end to end

| # | Step | File / symbol |
|---|---|---|
| 1 | Every 20 ms frame reaches `_process_vad_frame`; while `self._tts_playing`, the method is in **full-mute mode** — only barge-in can pass | `pipeline.py:1066` |
| 2 | Frames below the bar are **not discarded**: `_shadow_capture(frame)` keeps speech-energy frames in a bounded ring (`SHADOW_MAX_MS = 6000`, entry at `0.8 × vad.threshold`) so a caller who talks over the agent is not lost | `pipeline.py:1071`, `_shadow_capture` |
| 3 | Suppressed entirely until `_greeting_done` (greeting echo would otherwise cut the greeting) | `pipeline.py:1078` |
| 4 | Threshold: `vad.barge_in_threshold` = **520**; inside a continuation window `max(threshold × 1.3, 300)`; ×2 again while a spelled email/phone capture expects input | `pipeline.py:1088-1107` |
| 5 | Sustain: `_speech_frame_streak >= min_frames` where `min_frames` = 5 (100 ms) normally, 5 in a continuation window, **10 (200 ms) while `sink.playback_busy_until` is in the future** — i.e. usually 10 during real playback | `pipeline.py:1125-1130` |
| 6 | Elapsed guard: `> BARGE_IN_MIN_ELAPSED_MS` (**250 ms**), or **1500 ms** (`FILLER_ECHO_GRACE_MS`) for a filler and the utterance that follows it | `pipeline.py:1115`, `1131-1134` |
| 7 | **Connect delivery guard**: a barge against a reply the caller has not heard yet (nothing flushed, or < 1.0 s played) is *deferred*, latched, and resolved by the STT verdict — it only kills playback if the interruption was more than a bare "hello" | `_connect_barge_defer`, `pipeline.py:1141-1156` |
| 8 | **Media-layer abort first**: `await self.sink.interrupt_playback()` → `on_interrupt` → `TelephonySession._send_break_request` → length-prefixed JSON `{"type":"break_request","conn_id":…}` on TCP 127.0.0.1:9004 → `socket_server._dispatch_break_request` → FreeSWITCH `uuid_break`. Audio already handed to FS keeps playing otherwise | `telephony_sink.py:interrupt_playback`; `conversation_call_bridge.py:1871` |
| 9 | Then local drain: queued WAVs deleted, PCM buffer cleared, `playback_busy_until = 0`, `abort_event.set()` (releases the playback loop's pacing sleep) | `telephony_sink.py:interrupt_playback` |
| 10 | `await self.tts.stop()`; `_tts_playing = False`; `_barge_in_occurred = True`; session → `USER_INTERRUPTED`; `turn_traces[-1]["barged_in"] = True` | `pipeline.py:1176-1183` |
| 11 | `_flush_pipeline_queues()` — bumps `_delivery_epoch` (stale-response kill switch), drains TTS/STT/transcript queues, cancels in-flight prefetched transcriptions | `pipeline.py:1725` |
| 12 | Cancels `_current_generation_task` (the LLM stream) | `pipeline.py:1194-1198` |
| 13 | **Preserves the interrupting utterance's head**: pre-barge buffer ≥ 200 ms is re-attached through the VAD reset, then `_claim_shadow("barge-in")` prepends the playback-era ring | `pipeline.py:1200-1219` |
| 14 | Turn hand-back: `_turn_accumulator` is deliberately **kept** so the next transcript merges with what the caller said before interrupting; a clean (non-barged) completion clears it instead | `pipeline.py` TTS-layer `finally` block |
| 15 | Echo-text guard: a shadow-tainted transcript matching the agent's own words is discarded (`SHADOW_ECHO_TEXT_RATIO = 0.75`) | `_is_agent_echo_text`, `pipeline.py:2225` |

**Measured cancellation latency** (production, 2026-08-13):

```
06:50:28.724 [BARGE-IN] Interrupting TTS frames=5 energy=1604 threshold=520 cont_window=False
06:50:28.725 [<uuid>] [BARGE-IN] media playback interrupted (dropped 0 queued wavs)
06:49:08.404 [BARGE-IN] Interrupting TTS frames=5 energy=818  threshold=338 cont_window=True
06:49:08.406 [<uuid>] [BARGE-IN] media playback interrupted (dropped 0 queued wavs)
06:49:08.407 [BARGE-IN] Drained 1 items from pipeline queues
```

**1–2 ms** from decision to acknowledged `uuid_break` (that span includes the TCP round trip
to `socket_server` and its ack). The dominant cost is *detection*: 100–200 ms of sustained
above-threshold speech, plus the 250 ms (or 1500 ms post-filler) elapsed guard, plus whatever
FreeSWITCH still has buffered downstream.

---

## 5. Turn-taking / orchestration surface

### 5.1 Internal control signals (not events — shared state)

The orchestration is **not** an event-driven state machine over a bus. `VoicePipeline` is five
`asyncio` tasks joined by four bounded queues, coordinating through instance attributes:

```
audio.frames() → _audio_vad_layer → _stt_queue(6)         → _stt_submitter
                                  → _stt_ready_queue(1)   → _stt_worker
                                  → _transcript_queue(3)  → _conversation_layer
                                  → _tts_queue(8)         → _tts_layer → AudioSink
                                  + _silence_watchdog
```
(`pipeline.py:start`, queue sizes at `pipeline.py:113-127`.)

Payload dataclasses (`pipeline.py:52-111`): `AudioSegment(audio, duration_ms, timestamp,
speech_start_ts, speech_end_ts, shadow)`, `Transcript(text, duration_ms, timestamp,
stt_latency_ms, speech_start_ts, speech_end_ts, confidence, stt_source)`,
`Response(text, is_shortcut, timestamp, is_filler, protected, language, replay)`.

The de-facto "endpoint detected" signal is the `AudioSegment` reaching `_stt_queue`. The
de-facto "barge-in" signal is the mutation of `_tts_playing` / `_barge_in_occurred` /
`_delivery_epoch`. **None of these are published.**

### 5.2 What *is* published — `EventHub`

`voice/event_hub.py:EventHub` is an in-process async pub/sub broadcaster:
`subscribe() -> asyncio.Queue(maxsize=32)`, `unsubscribe(q)`, `emit_nowait(event: dict)` with
drop-oldest per client. A slow consumer loses messages but never blocks the pipeline.

Complete inventory of emitted event types (every `emit_nowait` call site in the codebase):

| `type` | Payload | Emitted at | Meaning |
|---|---|---|---|
| `agent` | `{type, text, llm_latency_ms, first_chunk_ms, total_latency_ms}` | `pipeline.py:931` (opening, all zeros) and `pipeline.py:3336` (each generated reply) | agent utterance finalized |
| `user` | `{type, text, stt_latency_ms, speech_start_mono, speech_end_mono}` | `pipeline.py:2236` | **final transcript**, with VAD speech boundaries on the monotonic clock |
| `mic_state` | `{type, enabled}` | `pipeline.py:1036` | mic gate toggled |
| `session_state` | `{type, state, prev}` | `call_session.py:80` | media-axis FSM transition |

`CallSession` states (`voice/call_session.py:39-47`): `CALL_INIT`, `AI_SPEAKING`,
`LIVE_CONVERSATION`, `USER_INTERRUPTED`, `THINKING`, `IVR_SUSPECT`, `VOICEMAIL_SUSPECT`,
`MUSIC`, `CALL_ENDED`. This is the closest thing to a public turn-taking event stream — and
`call_session.py`'s own docstring flags it as **"SHADOW BY DOCTRINE"**: the pipeline pushes
transitions in, the machine logs them and stamps the hub, *"but no existing consumer reads it
yet."*

Note what is **absent** from the hub: no `endpoint_detected`, no `barge_in`, no partial, no
`vad_speech_start` / `vad_speech_end`, no per-turn confidence. `USER_INTERRUPTED` is the only
barge-in trace, and it arrives as a state transition, not as an event with cause or timing.

### 5.3 Cleanly subscribable today vs internal-only

| Signal | Status |
|---|---|
| `user` (final transcript + STT latency + speech span) | ✅ **Subscribable** via `EventHub.subscribe()` — in-process only |
| `agent` (reply text + LLM latencies) | ✅ Subscribable, same channel |
| `session_state` (incl. `USER_INTERRUPTED`, `THINKING`, `AI_SPEAKING`) | ✅ Subscribable, but only wired when the pipeline was built with `VAD_ENGINE=fsmn` (which creates the `CallSession`) |
| `mic_state` | ✅ Subscribable |
| Endpoint decision (profile, silence frames, speech ms) | ❌ **Internal** — log line + `turn_traces[i]["endpoint_profile"]` only |
| VAD `speech_start` / `speech_end` events | ❌ **Internal** — `FsmnVadStack.pop_events()` is drain-on-read with no consumer; log lines only |
| Barge-in with timing/energy/threshold | ❌ **Internal** — log line + `turn_traces[i]["barged_in"]` boolean |
| Per-turn STT confidence | ⚠ Not on the hub; lands in `turn_traces` and the `call_turns` DB table at teardown |
| Partial transcripts | ❌ Do not exist |

**Reachability of the hub from outside the process** is the practical problem:

- In telephony mode each call gets its own `EventHub` created inside `TelephonySession.__init__`
  (`conversation_call_bridge.py:762`). Its only consumer is `TelephonySession._event_loop`,
  which writes the call-audit transcript and a 200-turn `transcript_tail`. **Nothing exposes
  it over a socket.**
- The FastAPI WebSocket at `app/web/server.py` (`@app.websocket("/ws")`, line 772) *does*
  stream the hub verbatim — but `make_app(hub, pipeline)` is only called from
  `calling-agent/backend/run.py` under `--web` (CLI mic mode, port 8000). It is **not running
  in production**.
- The dashboard's live view (`GET /api/calls/live-state`, `/api/calls/live-state/stream`,
  `dashboard/server.py:1679-1701`) is fed by a **1.5 s disk snapshot**
  (`phase14_runtime/live_calls_state.json`, written by `ws_audio_receiver._live_state_writer_loop`)
  carrying only `{conn_id, caller_number, start_time, language, stage, speaking, transcript_tail,
  owner, agent_slug, direction}`. Far too coarse and too lagged for DHRON's event schema.

---

## 6. Deployment and operational shape

### 6.1 Processes, containers, ports

| Container | Command | Network | Ports | Purpose |
|---|---|---|---|---|
| `lescall-runtime` | `bash deploy/runtime_entry.sh` | **host** | 9000 (ESL), 9001 (media WS), 9004 (playback/break/hangup) — all `127.0.0.1` | the voice stack |
| `lescall-api` | `python server.py` (in `dashboard/`) | host | 8080 | dashboard + Copilot API |
| `lescall-workers` | `bash deploy/workers_entry.sh` | host | — | billing sync, recording sync, intel queue |
| `lescall-admin` | `python admin_app.py` | host | 8090 (loopback/UNIX socket only) | super-admin plane |
| `lescall-pg` | Postgres 16 | — | `127.0.0.1:5432` | the database |
| `lescall-dashboard` | nginx | — | `127.0.0.1:8081` | SPA |

Verified live: `docker ps` shows all six up; `ss -ltnp` confirms 9000/9001/9004 bound to
`127.0.0.1` only.

`runtime_entry.sh` starts **two processes in one container** and treats them as a unit:
`socket_server.py` (ESL controller + playback listener) and
`ws_audio_receiver.py --sample-rate ${TELEPHONY_SAMPLE_RATE:-8000}` (media WS + AI bridge). It
sources `calling-agent/.env`, then `wait -n` — if either dies the container exits and Docker
restarts the pair. Logs land in `phase14_runtime/logs/docker/`; `logs/latest` symlinks to
`docker`.

⚠ Operational trap documented in `CLAUDE.md`: **do not run `phase14_runtime/start_all.sh`** on
this host — it starts a competing second pair on the same ports, the container then cannot
bind and enters a restart loop. `check_runtime.sh` reads pidfiles the container never writes,
so it reports `[FAIL]` even on a perfectly healthy runtime. Verify with `docker ps` and
`ss -ltnp | grep -E ':900[014]'` instead.

Deploy = restart, not rebuild: the repo is bind-mounted at `/home/angelo/lescall` inside every
container, so `docker restart lescall-runtime` picks up new `.py` files.

### 6.2 Configuration

**Layers**, in increasing precedence: module defaults in code → `calling-agent/.env` (sourced
by `runtime_entry.sh`) → `deploy/docker-compose.services.yml` `environment:` → `env_file`
entries (`.pg_env`, `/etc/lescall/telephony.env`, `/etc/lescall/billing.env` — the last
deliberately **absent** from the runtime container so live calls never see payment
credentials).

**Production runtime environment**, read from the live container:

| Var | Value | Effect |
|---|---|---|
| `TELEPHONY_MODE` | `true` | VAD thresholds 260/60, echo cooldown 400 ms, silence reprompt/hangup/dead-media all ON, connect delivery guard ON |
| `VAD_ENGINE` | `fsmn` | FSMN stack + EndpointController + classifier + CallSession |
| `ENABLE_CONTINUATION_MERGE` | `true` | pause-tolerant turns, relaxed in-window barge-in |
| `HARD_FLUSH_SILENCE_FRAMES` | `20` | legacy-engine 400 ms flush (inert on fsmn) |
| `LLM_TOOLS_MODE` | `on` | native tool calling |
| `LESCALL_DB_URL` | Postgres on 127.0.0.1:5432 | — |
| `GCS_RECORDINGS_BUCKET` / `GCS_LOGS_BUCKET` | `lescall-recordings` / `lescall-logs` | — |
| `PUBLIC_BASE_URL` | `https://agent.lescall.com` | — |

Not set → defaults apply: `ENABLE_BARGE_IN=true`, `TELEPHONY_INPUT_CODEC=l16`,
`TELEPHONY_TARGET_SR=16000`, `TELEPHONY_SAMPLE_RATE` unset → `--sample-rate 8000`,
`TTS_OUTPUT_SR=8000`, `LESCALL_WS_HOST=127.0.0.1`, `LESCALL_WS_PORT=9001`,
`ENABLE_PLAYBACK_SHADOW=true`, `STT_LANG_PIN=true`, `BOOT_WARMUP=true`,
`SARVAM_HTTP_SHARED=true`.

From `calling-agent/.env` (non-secret): `LLM_MODEL=gemini-3.5-flash`,
`LLM_REASONING_EFFORT=minimal`, `ENABLE_CALL_AUDIT=true`, `FREESWITCH_ESL_PORT=8021`.
Required secret: `SARVAM_API_KEY` (`run.py:_validate_env` exits immediately if missing; note
the historic footgun — the var is **not** `SARVAMAI_API_KEY`).

### 6.3 Auth between components

| Link | Auth |
|---|---|
| FreeSWITCH → WS 9001 (media) | **None.** Any client that reaches the port can open a session naming any agent slug on disk and run full AI calls billed to that slug's tenant. The mitigation is the bind address: `HOST_DEFAULT = "127.0.0.1"`, and the in-code comment is explicit that `0.0.0.0` "bought nothing and exposed an UNAUTHENTICATED media endpoint" (`ws_audio_receiver.py:50-57`) |
| Bridge → socket_server TCP 9004 | **None.** One-shot length-prefixed JSON (`>I` big-endian length + UTF-8 body), types `playback_request` / `break_request` / `hangup_request`, ack `{"ok": bool, "reason": …}`. Loopback-bound (`PLAYBACK_LISTEN_HOST=127.0.0.1`), 64 KiB frame cap |
| socket_server → FreeSWITCH ESL 8021 | ESL password; loopback ACL added in the 2026-08-03 security sprint |
| Dashboard / API | Google OAuth (`LESCALL_REQUIRE_AUTH=true`), deny-by-default `/api` gate in `dashboard/auth.py`. ⚠ New `/api` prefixes must be allowlisted in `_TENANT_API_PREFIXES` or they 403 |
| Sarvam / OpenAI / LLM | outbound API keys; Sarvam uses a rotating pool with per-call leases (`voice/key_rotator.py`) |

**Net**: inside the runtime there is *no* authentication at all. Everything rests on loopback
binding plus VPC boundary (host iptables is empty — see the security-sprint notes).

### 6.4 Logging and metrics per call

**Per-turn structured trace** — `pipeline.turn_traces` (a `list[dict]`, capped at 500 turns),
built at `pipeline.py:3110`, enriched by the LLM stage, by `TelephonySink.flush()` (via
`turn_trace_ref`), and by the barge-in handler; batch-persisted at teardown by
`conversation_call_bridge.py:_persist_call_turns` into the **`call_turns`** table
(`ON CONFLICT (call_id, turn_index) DO NOTHING`):

| Field | Meaning |
|---|---|
| `turn_index`, `ts`, `stage`, `intent`, `language` | turn identity + conversation context |
| `user_chars`, `agent_chars` | text volumes |
| `stt_latency_ms` | provider round trip |
| `stt_confidence` | the derived score from §3.4 |
| `speech_end_to_engine_ms` | VAD endpoint → LLM dispatch |
| `llm_first_chunk_ms`, `llm_total_ms` | generation latencies |
| `speech_end_to_first_audio_ms` | **the number every latency fix targets**, written by the sink on first flush |
| `is_filler`, `barged_in` | turn classification |
| `rag_sources` (JSON), `rag_trace_id` | which document chunks backed the reply |
| `session_state`, `endpoint_profile` | in-memory only (not columns) |

**Cost/usage**: `voice/credit_tracker.py` + `usage_ledger` rows opened per call
(`METERING_OPEN_DEFERRED=true`), reconciled against FreeSWITCH CDR by the workers container;
`billsec_final` is the only billable number. Price knobs in `.env`:
`SARVAM_STT_PRICE_PER_MIN=0.5`, `SARVAM_TTS_PRICE_PER_1K_CHARS=3.0`.

**Recordings/transcripts**: `ENABLE_CALL_AUDIT=true` → `recordings/<conn_id>/` with
`caller_audio.wav` + `transcript.txt`; transcripts carry per-turn `[spoke A–Bs]` spans derived
from `speech_start_mono`/`speech_end_mono`, converted to recording-relative time in
`_event_loop`. Synced to GCS bucket `lescall-recordings`.

**Greppable log families** (all in `phase14_runtime/logs/docker/ws_audio_receiver.log`):
`[VAD-STACK]`, `[ENDPOINT]`, `[TURN]`, `[STT]`, `[STT:Sarvam]`, `[STT-WORKER]`, `[LATENCY]`,
`[BARGE-IN]`, `[ECHO-GATE]`, `[ECHO-MUTE]`, `[SHADOW]`, `[AUDIO-EVENT]`, `[SESSION]`,
`[TTS]`, `[TTS:Sarvam]`, `[CONT-MERGE]`, `[TURN-TXN]`, `[PLAYBACK_WAV]`, `[MEDIA]`.

Benign noise to expect from the 20 s healthchecks: `InvalidMessage: did not receive a valid
HTTP request` on 9001 and an ESL `EOF during handshake` + empty `CASE R2` block on 9000.
Neither is a call.

### 6.5 What a second consumer would need

**To share this instance** — not advisable as-is:

- Port 9001 accepts any connection with no auth, and the path's 5th segment selects the agent
  (and therefore the tenant that gets billed). A second consumer sharing the port shares the
  billing surface.
- Every session opens a `TelephonySession`, which does DB routing lookups, opens a
  `usage_ledger` row, resolves customer context, writes a `calls` row and `call_turns` at
  teardown, and enqueues an intel job. A DHRON session would appear as a phone call
  everywhere in the business layer.
- `TelephonySink` writes 8 kHz WAV files to `/tmp/ai_telephony_playback` and talks to
  `socket_server` on 9004 expecting a FreeSWITCH channel with a matching `conn_id`. Without
  one, playback, `uuid_break` and hangup all fail.
- The FSMN model is shared per-process behind `_INFER_LOCK`; DHRON traffic would contend with
  live phone calls for it.

**To run its own instance** — this is the realistic option and needs: the repo (bind-mount),
`deploy/Dockerfile.app`, `SARVAM_API_KEY` + `OPENAI_API_KEY` + LLM credentials, a Postgres
(or the DB writes stubbed as `callsim` does), the vendored FSMN model at `data/models/fsmn_vad`,
`funasr_onnx` + `onnxruntime` + `webrtcvad`, an agent YAML under `calling-agent/agents/<slug>/`,
and `VAD_ENGINE=fsmn`. `TELEPHONY_MODE` should be **false** for a clean mic (see §2.6), which
also disables the connect guard, dead-media detector, silence hangup and silence reprompt.

A working precedent already exists: **`/home/angelo/callsim`** (outside the repo) starts a
sandbox running the real `ws_audio_receiver.py` + `conversation_call_bridge.py` on ports
19001/19004 with prod writes (metering, billing, `calls` rows, intel jobs, GCS) no-op'd and
audit recordings redirected — while DB *reads* stay real so routing and customer context
behave normally. It also implements a virtual FreeSWITCH (`fakeswitch.py`) that speaks the
9004 protocol. **DHRON should read `callsim/callsim.py` and `callsim/fakeswitch.py` before
writing anything** — the isolation work is done.

---

## 7. The adapter seam — recommendation

### 7.1 Recommended seam: implement `AudioSource` / `AudioSink`, drive `VoicePipeline` directly

**Do not** feed WebSocket port 9001. That path exists to convert a FreeSWITCH phone leg into
PCM: it hard-codes an 8 k→16 k resample, drags in the `TelephonySession` business layer
(billing, DB rows, DID routing, intel jobs), and returns audio as 8 kHz WAV files injected
over a FreeSWITCH-specific TCP protocol that DHRON has no channel for.

**Do** construct `VoicePipeline` in-process with your own transport objects. The abstraction
is already there and already has two implementations each:

```python
# calling-agent/backend/app/audio_transport/base.py
class AudioSource(abc.ABC):
    @abc.abstractmethod
    async def read_frame(self) -> bytes: ...   # ONE 20 ms PCM16 mono 16 kHz frame = 640 bytes
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def frames(self) -> AsyncIterator[bytes]: ...   # default impl loops read_frame()

class AudioSink(abc.ABC):
    @abc.abstractmethod
    async def write_frame(self, pcm: bytes) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def flush(self) -> None: ...
```

**(a) Feeding mic PCM in.** Subclass `AudioSource` (or reuse `TelephonySource` verbatim — it is
already transport-agnostic, just a bounded queue with `push_frame(pcm)`). From a WebRTC track:
decode Opus → PCM16 mono → **resample to exactly 16 000 Hz** → **re-chunk to exactly 640-byte
frames** → `push_frame()`. The re-chunking is not optional (§1.2). `aiortc` delivers 20 ms
frames at 48 kHz by default, so this is a resample plus a small byte accumulator.

**(b) Getting events out.** Pass your own `EventHub` into `VoicePipeline(event_hub=...)` and
`subscribe()` to it. That gives you `user` (final transcript + `stt_latency_ms` +
`speech_start_mono` + `speech_end_mono`), `agent`, `session_state` and `mic_state` today, with
no code changes. Everything else needs the small additions below.

```python
from backend.app.voice.pipeline import VoicePipeline
from backend.app.voice.event_hub import EventHub
from backend.app.audio_transport.telephony_source import TelephonySource

hub    = EventHub()
source = TelephonySource(sample_rate=16000, frame_duration_ms=20)
sink   = DhronWebRtcSink()          # your AudioSink: PCM16 out to the peer connection
pipe   = VoicePipeline(audio_source=source, audio_sink=sink,
                       event_hub=hub, conversation=<ConversationRouter>,
                       mic_enabled=True, skip_greeting=False)
await pipe.start()
q = hub.subscribe()                 # asyncio.Queue(maxsize=32), drop-oldest
```

Run with `TELEPHONY_MODE=false` (thresholds 150/50, no connect guard, no dead-media detector,
no silence hangup) and `VAD_ENGINE=fsmn`.

**Why this seam**: it is the only place where the stack is already abstract, it skips 100% of
the telephony-specific ingress and egress, it needs **zero modifications** to feed audio in,
and it keeps the parts DHRON actually wants — the FSMN VAD, the endpointer, the segment
admission gates, the hedged STT router with its derived confidence, and the merge/continuation
logic.

### 7.2 Minimal changes required, with effort estimates

Ordered by necessity. None of these are refactors; all are additive emissions or small
subclasses.

| # | Change | Why | Effort |
|---|---|---|---|
| 1 | **`DhronWebRtcSink(AudioSink)`** — accept PCM16 at 16 k, push to the WebRTC track; implement `interrupt_playback()` (drop buffered audio, set `abort_event`) and expose `playback_busy_until` + `last_flush_duration` | `TelephonySink` writes 8 k WAV files and calls FreeSWITCH. The pipeline reads `playback_busy_until` for the barge-in guard and the echo-mute window; a sink lacking it degrades to a fixed cooldown (`LocalSpeakerSink` already proves that path works) | **1–2 days** |
| 2 | **Emit `endpoint` on the hub** — one `emit_nowait` at `pipeline.py:1452` where `_flush_due` becomes true, carrying `{profile, silence_frames, speech_ms, raw_speech_ms, snr, speech_start_mono, speech_end_mono}` | The endpoint decision currently exists only as a log line and a `turn_traces` field. This is DHRON's turn-boundary signal and it already has every value in local scope | **0.5 day** |
| 3 | **Emit `barge_in` on the hub** — one `emit_nowait` at `pipeline.py:1158`, carrying `{frames, energy, threshold, cont_window, elapsed_ms, utt_seq}`; optionally a second after `interrupt_playback()` returns so cancellation latency is measurable | `USER_INTERRUPTED` on `session_state` is the only current trace and carries no timing or cause | **0.5 day** |
| 4 | **Add `confidence` and `stt_source` to the `user` event** — the `Transcript` already carries both (`pipeline.py:2255-2256`), they are simply not put on the hub two lines earlier | DHRON's `transcript.final` wants confidence. Without this you would have to wait for the `call_turns` DB write at call teardown | **0.25 day** |
| 5 | **Reference-clock event** — emit one `{type: "clock", mono: time.monotonic(), wall: time.time()}` at pipeline start | `speech_start_mono` / `speech_end_mono` are on Python's monotonic clock with an arbitrary origin. DHRON cannot map them to wall time or to its own parent-link timeline without one anchor. `_event_loop` already does this conversion ad hoc (`conversation_call_bridge.py:1667-1673`) — this just makes it available | **0.25 day** |
| 6 | **Frame-contract guard** — reject or re-chunk any frame that is not 640 bytes at the source boundary, loudly | §1.2: a wrong frame size silently corrupts every duration threshold. Cheap insurance for a new transport | **0.25 day** |
| 7 | **Bypass the business layer** — instantiate `VoicePipeline` directly rather than via `TelephonySession`, or copy `callsim`'s no-op patching of metering/billing/`calls`/intel writes | Otherwise every DHRON session bills a tenant and writes a phone-call row | **1 day** (or ~0 if you reuse `callsim/callsim.py`'s `start_sandbox`) |
| 8 | **Re-tune the level constants** for mic audio: `minimum_threshold`, `barge_in_minimum`, `ECHO_EXIT_ENERGY_FACTOR`, `AEC_RMS_FLOOR`, `SHADOW_ENERGY_FACTOR` | All are absolute int16 values calibrated against phone-line levels (§2.6). Constructor args and env vars — no code changes, but real measurement work | **2–3 days** (measurement-bound) |
| 9 | **Optional: disable the echo/shadow machinery** once WebRTC AEC is confirmed — `ENABLE_PLAYBACK_SHADOW=false`, `TTS_ECHO_COOLDOWN_MS` low | It costs responsiveness and exists only because telephony has no AEC. Verify first: half-duplex WebRTC setups still echo | **0.5 day** + validation |
| 10 | **If DHRON genuinely needs `transcript.partial`** — restore the deleted streaming provider (`git log -- calling-agent/backend/app/voice/stt/sarvam_stream.py`) or write a new one against the `StreamingSTT` ABC, and re-add the `feed_nowait` sites in the VAD loop | This is the one hard gap. RUNBOOK §24 is explicit that it is not a flag flip. It also reopens the four failure modes §21-H was written to mitigate | **5–10 days**, and it needs the owner's sign-off given the deletion was a deliberate decision |

**Total for a working adapter without partials: ≈ 4–6 engineering days**, plus the level
re-tuning. Items 2–5 are ~1.5 days combined and are what turn this from "a pipeline you can
feed" into "a pipeline that reports timestamps and confidences", which is what DHRON's schema
needs.

### 7.3 Mapping to DHRON's event schema

| DHRON event | Source after the changes above | Notes |
|---|---|---|
| `transcript.partial` | **No source.** Would require item 10 | The honest answer today is that DHRON must either drop partials or fund the streaming provider |
| `transcript.final` | hub `user` event + item 4 | `text`, `confidence` (derived 0.05–1.0, never provider-native), `stt_latency_ms`, `speech_start_mono`/`speech_end_mono` |
| endpoint | item 2 | `profile` ∈ {fast_complete, default, hold}; residual 200 ms in practice for two of three profiles |
| barge-in | item 3 | detection 100–200 ms; cancellation 1–2 ms |
| parent links | `turn_index` from `turn_traces`, plus the clock anchor from item 5 | `AudioSegment`/`Transcript` already thread `speech_start_ts`/`speech_end_ts` end to end |
| latency | `stt_latency_ms` (hub), `speech_end_to_engine_ms`, `llm_first_chunk_ms`, `llm_total_ms`, `speech_end_to_first_audio_ms` (all in `turn_traces` / `call_turns`) | Consider emitting `turn_traces` entries on the hub at turn completion rather than only persisting at teardown |

⚠ **Confidence caveat for DHRON's schema**: `confidence` here is a *heuristic derived from the
transcript's own shape* (words-per-second, single-word-from-long-audio, function-word ratio,
latency), not an acoustic posterior from the recognizer. Sarvam's REST response has no
confidence field at all. Do not present it to downstream consumers as model confidence — its
real job is hallucination detection.

---

## 8. Known limitations worth inheriting

Every item below is documented in this repository (or in the `callsim` harness that ships
alongside it) with the incident that produced it.

**Measurement and harness**

1. **Synthetic-caller audio is unrealistically clean.** `callsim` synthesizes caller lines with
   Sarvam TTS (speaker `simran`) and defaults to `--echo 0` — "a clean headset line". Real
   line echo must be forced with `--echo 0.05`, which is exactly what stresses the echo and
   barge-in guards. Anything tuned against default sim runs is tuned against an easy case.
   (`callsim/README.md`.)
2. **Wire latency from the sim is a floor, not the caller experience.** FreeSWITCH injection
   overhead (~0.3–0.7 s) and RTP jitter/loss are not simulated. DTMF and mid-call codec
   changes are not simulated. (`callsim/README.md`, "Known gaps".)
3. **Cold-start contaminates the first turn.** A fresh sandbox pays funasr import (~1–3 s) plus
   TTS/LLM pool warmup on turn 1 — same as a production restart. Use `--keep-sandbox --attach`
   for timing runs, and restart fresh after any code change.
4. **WER baselines from energy-segmented replay were inflated by an alignment artifact.** The
   2026-08-03 numbers (hi 0.80 / en 1.00 / gu 0.67) were measurement noise, not accent gap;
   span-aligned segments replay at WER 0.00. Only span-documented recordings (post-`bacbd82`,
   `[spoke A–Bs]` in the transcript) give a trustworthy read.
   (`callsim/ACCENT_GAP_RUN.md`.)
5. **Script flips are a separate failure class from accuracy.** The same Hindi speech rendered
   as Devanagari vs Latin vs Hinglish is language-pin instability, and must be counted
   separately or it pollutes WER. (`callsim/ACCENT_GAP_RUN.md`.)
6. **The concurrency-knee harness did not capture what it was built to capture.**
   `knee_sweep.out` shows `"vad_health_lines": []` in all four runs, so the VAD degradation
   curve across N was never actually measured. No knee is visible at N ≤ 4; beyond that is
   unmeasured.
7. **Backend tests hit the real LLM unless the `stub_llm` fixture is used**, and `.env`
   collection order can pollute test runs. Billing tests must use `pg_test_db` or they hit
   **production**.

**VAD and endpointing**

8. **The scale bug hid for five days while half-working.** int16 fed to a model calibrated on
   `[-1,1]` shifted every log-mel bin by a constant and still produced plausible output — 28%
   of flagged speech segments were silence. Any re-scaling of the audio path must be
   re-validated against segment-level ground truth, not "does it still detect speech".
   (`vad_stack.py:_worker_loop`.)
9. **A duration floor measured on padded audio cannot reject anything.** `MIN_SPEECH_MS=300`
   was applied to a buffer that already included a 400 ms pre-ring and a 200 ms hold, so a
   single 20 ms speech frame scored ~620 ms. Result: 9 of 23 STT submissions in one audited
   call carried no caller speech, and an 80 ms blip padded to 980 ms came back as `"₹1 lakh."`
   in a call about the caller's budget. Hence `MIN_REAL_SPEECH_MS` on raw model frames.
   (`test_vad_real_speech_floor.py`, `pipeline.py:135-148`.)
10. **The SNR guard must stay permissive.** A first attempt at 2.0× rejected genuine caller
    speech that measured 1.8×, while other real speech in the *same call* measured 2.0–8.4× —
    i.e. 2.0 sat inside the real-speech distribution. It now asserts only that speech rose
    above ambient at all (1.2×), applies to short segments only, and fails open twice over.
    **Deafness costs far more than a wasted STT call.** (`pipeline.py:160-176`.)
11. **A model VAD classifies your own TTS echo as speech.** Frame energy is the *only* echo
    discriminator in this stack. The echo-gate early exit once counted the model verdict alone
    and broke out of a 5.4 s echo window on 4 frames, shipping 700 ms of digital silence
    (RMS 8) to a paid STT call. Real caller speech measured 737–3655 RMS; echo measured 8–89.
    (`pipeline.py:239-251`.) **On WebRTC with AEC this whole class may disappear — verify
    rather than assume.**
12. **The endpointer's semantic profiles were structurally unreachable for months.** Both
    `hold` and `fast_complete` are gated on `if text:`, and the batch path passed `""` — so
    every production `[ENDPOINT]` line read `profile=default`, and `_DIGIT_TAIL` (written so
    phone-number and budget dictation isn't chopped between bursts) had **never once
    executed** on a live call. (`pipeline.py:1428-1443`.)
13. **Timing alone cannot separate "finished" from "mid-pause".** Both land ~240 ms after
    speech end, because FSMN confirms speech 100–300 ms *after* true onset — so a caller who
    has already resumed still reads as idle. Word count was the discriminator that worked:
    every observed split fragment was 1–3 words, and no full sentence was ever the head of a
    split. Hence `MERGE_NO_FASTPATH_MAX_WORDS=3`. (`pipeline.py:288-305`.)
14. **Silent hop loss corrupts endpointing untraceably**, which is why the stack degrades to
    energy VAD (visible, logged) at queue depth 32 rather than dropping. Preserve that
    property in any port.
15. **Dropping the newest queued segment is the worst available choice** — the caller's most
    recent words are the ones about to be answered. 47 segments of real speech were lost this
    way on 2026-08-03; the queue now evicts the *oldest*. (`pipeline.py:1565-1590`.)

**STT**

16. **Provider hangs are per-request, not per-service.** During the 2026-08-03 incident ~1 in 3
    Sarvam requests hung server-side while the rest answered in 150–450 ms; an immediately
    issued duplicate succeeded in ~200 ms. Hence the two-tier hedge (primary race at 0.6 s,
    Whisper at 1.0 s) and the tight per-phase timeouts. A blanket 10 s timeout turned every
    hang into 10–22 s turns and STT-queue overflow. (`stt/router.py`, `test_stt_hedge.py`.)
17. **The primary's clean-empty verdict is the anti-hallucination authority.** When Sarvam
    returns empty cleanly, Whisper is *not* called and any parked hedge text is discarded —
    otherwise the fallback invents transcripts from noise. (`stt/router.py:550-586`.)
18. **A fabricated confidence disables your own gate.** For months every transcript carried a
    hard-coded `1.0` and `MIN_CONFIDENCE` never fired once in production. Providers must
    return `None`, not a guess. (`stt/base.py:STTResult` docstring.)
19. **Whisper cannot do Gujarati** (`gu` is not in `whisper-1`'s language list — it is a hard
    HTTP 400, not a degraded transcript), and hinting Whisper with the *agent's* default
    language before the caller has established one makes it transliterate Hindi into Latin
    junk, which then starves the script-based language evidence and locks the call into the
    wrong language. (`stt/openai_whisper.py:20-28`, `pipeline.py:670-677`.)
20. **A language pin is self-sealing**: the pinned language shapes the transcript, which
    re-confirms the pin. Two guards exist — only caller evidence counts toward the pin, and
    every 8 turns the pin is dropped for one auto-detect turn so a wrong pin always has a way
    out. (`pipeline.py:389-398`.)

**Playback, barge-in, turn integrity**

21. **Media-layer abort must come first.** Audio already injected into FreeSWITCH keeps playing
    no matter what you drop locally. Any transport port needs the equivalent of `uuid_break`
    *before* draining local queues. (`telephony_sink.py:interrupt_playback`.)
22. **Incremental TTS flushing means echo of part 1 arrives during part 2**, which trips
    barge-in and cancels the agent's own real response — the caller hears the filler, then
    silence. Hence `FILLER_ECHO_GRACE_MS=1500` and the streak reset at every utterance
    boundary. (`pipeline.py:185-196`.)
23. **A delivery epoch is needed, not just a queue drain.** A `Response` dequeued a moment
    before its turn was superseded used to synthesize and play anyway — both the pre-merge and
    post-merge replies were spoken back to back on a live call. (`pipeline.py:656-663`.)
24. **Mid-stream TTS retry causes double-speak.** Once any audio has reached the sink, a
    failure must truncate, not retry from the start. (`tts_sarvam.py:_stream_audio`.)
25. **Never queue a wrong-sample-rate WAV.** A 16 k file handed to an 8 k player plays at half
    speed, an octave down. The drop-on-normalization-failure path was documented as fixed on
    2026-06-02 but never survived into committed code — it was dormant only because the
    default made it unreachable. (`telephony_sink.py:184-200`.)
26. **A caller speaking over the agent used to be simply never heard**: 18 of 31 fleet agents
    lost whole turns, and heads of straddling utterances were cut ("my name is rahul sharma" →
    "rahul sharma."). The fix moved echo protection from the time domain (mute everything) to
    the text domain (shadow ring + agent-echo text match). (`pipeline.py:209-234`.)
27. **A quiet caller and a dead line need opposite responses.** In one call every sample from
    second 64 onward was exactly `-1` for 61 of 125 seconds; VAD correctly reported no speech,
    but nothing distinguished it from silence, so the agent re-asked twice and delivered a
    farewell into a dead socket, billing TTS throughout. Zero *variance* is the discriminator,
    not level — and that test is µ-law-specific. (`test_dead_media.py`, `pipeline.py:548-564`.)

**Operational**

28. **The runtime is a container**; `docker restart lescall-runtime` is how voice code ships.
    Running `phase14_runtime/start_all.sh` starts a competing pair on the same ports and puts
    the container into a restart loop while an unmanaged host pair serves calls.
    `check_runtime.sh` reads pidfiles the container never writes and always reports FAIL.
29. **Bind-mounted containers serve stale code until restarted** — a compose `up` is not an env
    apply, and `.env` is read at import time.
30. **Port 9001 has no authentication.** It was `0.0.0.0` once; anyone reaching it could run
    full AI calls billed to any tenant whose agent slug exists on disk.

---

## UNKNOWNS

1. **Concurrency ceiling of the VAD/pipeline.** No code limits concurrent sessions. The only
   sweep (`callsim/runs/knee_sweep.out`, N = 1…4) shows no knee, and its `vad_health_lines`
   harvest came back empty in every run. Behaviour at N > 4 on the 2-core VM is unmeasured.
2. **Per-stream memory cost of `FsmnVadStack`.** The ONNX session is shared; per-stream state
   (`param_dict`, hop ring, event list) is small but was never measured.
3. **Raw Sarvam STT HTTP response body.** Only four fields are read by the code
   (`transcript`, `confidence`, `language_code`, `language_probability`). Whether the API
   returns anything else — timestamps, alternatives, segment structure — cannot be determined
   from this repository.
4. **Whether `saarika:v2.5` returns word-level timestamps if asked.** Nothing requests them;
   `docs.sarvam.ai` was not consulted for this report.
5. **Real end-to-end latency of a partial**, since no streaming provider exists. The deleted
   provider's documented benefit was a "~300–800 ms overlap saving" (RUNBOOK §24), but no
   measured partial-emission cadence survives in the repo.
6. **FSMN VAD behaviour on clean wideband 16/48 kHz microphone audio.** Every measurement in
   this repo is on 8 kHz telephony upsampled to 16 kHz. The model's calibration on genuinely
   wideband input is untested here.
7. **Correct level thresholds for WebRTC mic audio** (`minimum_threshold`, `barge_in_minimum`,
   `ECHO_EXIT_ENERGY_FACTOR`, `AEC_RMS_FLOOR`, `SHADOW_ENERGY_FACTOR`). All current values are
   absolute int16 numbers measured on phone lines.
8. **Whether WebRTC's AEC makes the shadow/echo machinery unnecessary.** Strongly likely, but
   it depends on DHRON's client topology (speakerphone, half-duplex, no-AEC embedded clients),
   and was not tested.
9. **`TELEPHONY_SAMPLE_RATE`** is unset in production, so `runtime_entry.sh` falls back to
   `--sample-rate 8000`. Whether any deployment sets it otherwise is unknown.
10. **Exact `mod_audio_stream` frame cadence under load.** `EXPECTED_CHUNK_MS = 20` is declared
    but never read or enforced, and `max_gap_ms` / `stalls` counters are logged only at session
    end — no distribution was collected.
11. **Sarvam TTS `bulbul:v3` output framing.** The stream is read in fixed 4096-byte HTTP
    chunks (`CHUNK_READ_SIZE`) and treated as raw `linear16`; whether the provider emits a
    header or any container framing on the first chunk is handled only by
    `_check_stream_rate`, whose logic was not audited for this report.
12. **Whether `pop_events()` timestamps can be reconciled to the monotonic clock.** They are
    audio-time positions (`_stream_ms`), advanced only for hops the worker actually processed —
    so gated-off idle periods and dropped hops would desynchronize them from wall time by an
    unmeasured amount.
13. **The intended production value of `HARD_FLUSH_SILENCE_FRAMES=20`.** It is set in the
    runtime compose env, but `VAD_ENGINE=fsmn` routes the decision to `EndpointController`
    instead, making it inert. Whether that is intentional or a leftover from the pre-fsmn
    canary is not documented.
14. **Which of `deploy/RUNBOOK.md` §24's claims are still current.** It states the endpointer
    "now always receives `("", False)`", which `pipeline.py:1444-1449` contradicts
    (`_endpoint_hint_text()` was reinstated 2026-08-04). Other §24 statements may have drifted
    similarly.
