# Call Concurrency Report — Scope, Realistic Outcomes & Risks

**Date**: June 2026
**Pipeline**: Plivo (SIP trunk + DID) → FreeSWITCH (ESL call control) → `ws_audio_receiver.py` → AI pipeline (Sarvam STT/TTS + LLM)

This report complements `COST_ANALYSIS.md` (per-call ₹ cost) by answering a different question: **how many simultaneous calls can this system actually carry today, and what breaks first?**

---

## 1. Scope

Three independent ceilings exist in series — the system's real concurrency limit is whichever is *lowest*, not whichever is named on a pricing page:

| Layer | What gates it | Where it's enforced |
|---|---|---|
| **Plivo trunk** | Account plan: RPS + concurrent-call cap | Plivo's network, before FreeSWITCH ever sees the call |
| **FreeSWITCH / app process** | CPU, memory, file descriptors, port 9000/9001 socket handling | `socket_server.py`, `ws_audio_receiver.py` |
| **Sarvam API** | Plan's requests/min limit (STT + TTS combined) | Per-call `STTRouter` / TTS client inside the pipeline |

This report covers all three but focuses on Sarvam, since it's the one currently on a free/Starter plan while Plivo is already on Professional — i.e. the most likely near-term bottleneck.

---

## 2. Per-call Sarvam request load (measured from code)

Confirmed from `stt/router.py`, `pipeline.py`, `tts_sarvam.py`, `tracking_stt.py`, `tracking_tts.py`:

- **STT**: one request per VAD-flushed utterance (turn-complete or hard-silence flush — `pipeline.py:614-630`, `turn_manager.py:74-89`). Not per-frame.
- **TTS**: one request per agent turn — all LLM sentence fragments for a turn are joined and sent as a single TTS call (`pipeline.py:1023-1038`), not one call per sentence.

Using the same call-mix scenarios as `COST_ANALYSIS.md`:

| Scenario | Duration | STT calls | TTS calls | Total Sarvam requests | Requests/min (sustained) |
|---|---|---|---|---|---|
| A — Short/abandoned | 90s | ~4 | 5 | 9 | 6.0 |
| B — Typical engaged | 4.5 min | ~12 | 14 | 26 | 5.8 |
| C — High-intent long | 9 min | ~20 | 22 | 42 | 4.7 |
| D — Wrong number | 20s | ~1 | 1 | 2 | 6.0 |

**Working number for capacity planning: ~5–6 Sarvam requests/min per concurrent call**, fairly flat across call types (longer calls have more requests but spread over more time).

---

## 3. Sarvam plan vs. sustainable concurrency

Sarvam's stated rate limit is **per account**, shared across every concurrent call. Dividing the plan's req/min by ~5.5 req/min/call gives the realistic ceiling before 429s start:

| Sarvam plan | Rate limit | Sustainable concurrent calls | Notes |
|---|---|---|---|
| **Starter (current, free)** | 60 req/min | **~8–10 calls** | Beyond this, 429s begin |
| Pro (₹10,000, 11,000 credits) | 200 req/min | ~33–36 calls | |
| Business (₹50,000, 57,500 credits) | 1,000 req/min | ~170+ calls | Plivo's 50-call cap becomes the binding limit instead |

**You are currently capped at roughly 8–10 simultaneous live calls before Sarvam starts rate-limiting STT/TTS — far below Plivo Professional's 50-call concurrency ceiling.** Plivo is not the bottleneck today; Sarvam's plan is.

---

## 4. What actually happens at the Sarvam ceiling (not a hard failure)

This is good news, by design: the rate-limit handling is **per-call, not global**. Each `TelephonySession` (one per call, `conversation_call_bridge.py:287-289`) owns its own `STTRouter` instance, so a 429 backoff timer (`stt/router.py:324-372`, 60s→300s exponential) is scoped to that one call only — it does **not** degrade other concurrent calls.

Effect when a given call's STT requests hit a 429:
- That call's STT silently fails over to OpenAI Whisper for 60–300s (`stt/router.py` fallback path), consuming OpenAI quota/cost instead of Sarvam quota.
- Whisper does not replicate Sarvam's `saaras:v3` codemix mode or the same `language_probability`-based language lock — **expect transcription quality to dip for Hindi/Gujarati code-mixed speech specifically during fallback windows**, which is exactly the traffic this system is built for.
- TTS has no equivalent fallback provider in the codebase (`tts_sarvam.py` retries the same Sarvam endpoint up to 2x on timeout/connection-reset only, not on 429) — if Sarvam TTS itself starts 429ing under load, **there is no fallback voice and that turn fails to play**, not just degrades.

So the realistic failure mode above ~10 concurrent calls on the free plan is: **STT quietly degrades language accuracy for the overflow calls, while TTS has no safety net and can drop audio outright.** This is asymmetric and worth fixing or budgeting around before scaling traffic.

---

## 5. Plivo-side limits (Professional plan)

- **50 call concurrency**: not currently the binding constraint (Sarvam free tier caps you lower already).
- **2 RPS**: this is Plivo's API request rate (call control actions — answer, transfer, hangup, trunk signaling), separate from media/audio. For inbound calls arriving one at a time this is rarely hit, but a burst of simultaneous inbound rings (e.g. a marketing push that drives many callers to dial in the same second) could throttle call setup at the trunk before the AI pipeline is even reached. Worth confirming with Plivo whether the 2 RPS cap applies per-second bursts or is smoothed.
- **₹2,00,000/month usage cap**: at the Scenario B (~₹7.67/call) weighted blended average of ~₹5.44/call from `COST_ANALYSIS.md`, this caps Plivo-billed volume around **36,000+ calls/month** before the account-level spend ceiling — comfortably above what Sarvam's free tier would allow concurrency-wise anyway.

---

## 6. Recommendations, in priority order

1. **Upgrade Sarvam off the free Starter plan before any real traffic ramp.** At 8–10 concurrent calls, a single busy hour (e.g. evening call-back rush) will saturate it. Pro (₹10,000) triples the ceiling to ~33 calls for a modest spend and unlocks email support; that alone likely covers near-term needs.
2. **Add a TTS fallback or a hard concurrency gate**, since TTS 429s currently have no recovery path. Either: (a) queue/delay TTS requests instead of dropping them, or (b) cap concurrent calls accepted by the pipeline (e.g. reject/queue new calls once N are active) so Sarvam never sees more load than its plan supports. A soft admission-control limit is cheaper than a plan upgrade and prevents silent audio drops regardless of plan tier.
3. **Decide the Business-tier trigger point**: only worth the ₹50,000 commitment once sustained concurrency needs exceed ~30 calls — i.e. once you're approaching Plivo's 50-call ceiling for real, not before.
4. **Confirm Plivo's 2 RPS semantics with their team** (burst vs. sustained) if you expect synchronized call spikes (campaigns, SMS-triggered callbacks) — this is the one number from the pasted account page not visible anywhere in the codebase, so it can't be cross-checked from logs the way Sarvam's usage can.
5. **Track real production req/min against the Sarvam dashboard**, not just the model above — the ~5.5 req/min/call figure assumes the Scenario A/B/C mix from `COST_ANALYSIS.md`; if real call patterns skew toward more, shorter back-and-forth utterances (e.g. confused callers, lots of "what?"), STT request density per call rises and the safe concurrency ceiling drops accordingly.
