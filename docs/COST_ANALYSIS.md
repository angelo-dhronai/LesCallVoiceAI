# API Cost Analysis — Pramukh Group AI Calling Pipeline

**Date**: June 2026  
**Pipeline**: Inbound real-estate voice AI (FreeSWITCH → Sarvam STT → GPT-4o-mini → Sarvam TTS → FreeSWITCH)

---

## Services in Use

| Service | Provider | Purpose | Rate |
|---|---|---|---|
| Inbound Voice Call | Plivo (Local Number) | Caller connects to FreeSWITCH via SIP | ₹0.6 / min |
| Phone Number Rental | Plivo | One DID number for inbound calls | ₹250 / month |
| STT (Primary) | Sarvam `saaras:v3` | Transcribes caller audio, billed per second (rounded up/request) | ₹0.5 / min (₹30 / hr) |
| TTS | Sarvam `bulbul:v3` | Synthesizes AI responses, billed per character | ₹3.0 / 1,000 chars |
| LLM | OpenAI `gpt-4o-mini` | Generates AI replies, capped at 160 output tokens/turn | $0.15 / 1M input · $0.60 / 1M output (~₹12.75 / 1M input · ₹51 / 1M output) |
| STT (Fallback) | OpenAI Whisper | Used only if Sarvam returns HTTP 429 | $0.006 / min (~₹0.51 / min) |

**Services from Plivo's rate card that are NOT used:**
- Plivo Transcription Auto (₹0.76/min) — replaced by Sarvam STT
- Plivo ASR (₹1.6/15s) — replaced by Sarvam STT
- Recording storage (₹0.032/min/month) — recordings stored locally, not in Plivo
- Toll-Free numbers — not used

---

## TTS Deep-Dive: Practical Character Counts

Sarvam charges per character including spaces. The AI is instructed to respond in 1–2 short sentences for spoken delivery.

### Typical AI Response Lengths by Conversation Stage

| Stage | Sample Response | Char Count |
|---|---|---|
| **GREETING** | "Namaste! Main Vishal Patel bol raha hoon Pramukh Group se. Aap kaise help kar sakta hoon?" | 91 |
| **DISCOVERY — question** | "Aap property investment ke liye dekh rahe hain ya apne liye rehne ke liye?" | 75 |
| **DISCOVERY — follow-up** | "Budget roughly kitna hai aapka? Ready-to-move chahiye ya under-construction bhi theek hai?" | 92 |
| **PROJECT_MATCH** | "Aapke budget mein Pramukh Olympia fit hoga — 2BHK around 65 lakh, Surat mein great location hai." | 96 |
| **INVITE_VISIT** | "Ek site visit karein toh bahut better idea milega. Aap kab free hain — weekend ya weekday?" | 91 |
| **CONFIRMATION** | "Sunday 11 baje book karta hoon aapke liye. Address confirm kar deta hoon SMS pe." | 80 |
| **CLOSING** | "Bilkul. Koi bhi questions hoon toh call kar lijiye. Milte hain Sunday ko!" | 71 |
| **Short acknowledgement** | "Haan, bilkul." / "Sure, ek second." | 15–25 |

**Average per AI turn: ~80 characters** (mix of full responses and short acknowledgements)

### TTS Cost per Character Count

| Chars | ₹ Cost | Example scenario |
|---|---|---|
| 100 chars | ₹0.30 | 1 full response |
| 500 chars | ₹1.50 | ~6 turns |
| 1,000 chars | ₹3.00 | ~12 turns |
| 2,000 chars | ₹6.00 | ~25 turns (long call) |
| 5,000 chars | ₹15.00 | 100-call batch aggregate |

---

## Per-Call Cost Scenarios

All scenarios assume inbound caller on a local Plivo number. Plivo bills in 60-second intervals (rounds up). STT billed per-second rounded up per utterance.

### Scenario A — Short / Abandoned Call

> Caller asks about one project, not interested, disconnects.

| Metric | Value |
|---|---|
| Call duration | 90 seconds |
| Caller speaking time | ~40s (split into ~4 utterances, avg 10s each) |
| AI turns | 5 |
| TTS chars | ~400 (5 × 80 chars avg) |

| Cost Item | Calculation | ₹ |
|---|---|---|
| Plivo inbound | 2 min × ₹0.6 | 1.20 |
| Sarvam STT | 40s billed → (40/60) × ₹0.5 | 0.33 |
| Sarvam TTS | 400 chars → (400/1,000) × ₹3.0 | 1.20 |
| OpenAI LLM | 5 turns × ~700 input + 40 output tokens → $0.00057 | 0.05 |
| **Total** | | **₹2.78** |

---

### Scenario B — Typical Engaged Call *(most common)*

> Caller asks about project, language detected (Hindi/Gujarati), project matched, visit invite given, books appointment.

| Metric | Value |
|---|---|
| Call duration | 4.5 minutes |
| Caller speaking time | ~2.25 min (135s, split into ~12 utterances) |
| AI turns | 14 |
| TTS chars | ~1,120 (14 × 80 chars avg) |

| Cost Item | Calculation | ₹ |
|---|---|---|
| Plivo inbound | 5 min × ₹0.6 | 3.00 |
| Sarvam STT | 135s billed → (135/60) × ₹0.5 | 1.13 |
| Sarvam TTS | 1,120 chars → (1.12) × ₹3.0 | 3.36 |
| OpenAI LLM | 14 turns × ~900 input + 50 output tokens → $0.0021 | 0.18 |
| **Total** | | **₹7.67** |

---

### Scenario C — High-Intent Long Call

> Serious buyer — asks detailed questions about amenities, pricing, location. Full 6-stage progression with barge-ins.

| Metric | Value |
|---|---|
| Call duration | 9 minutes |
| Caller speaking time | ~4.5 min (270s, split into ~20 utterances) |
| AI turns | 22 |
| TTS chars | ~1,980 (barge-ins mean some turns are shorter) |

| Cost Item | Calculation | ₹ |
|---|---|---|
| Plivo inbound | 9 min × ₹0.6 | 5.40 |
| Sarvam STT | 270s billed → (270/60) × ₹0.5 | 2.25 |
| Sarvam TTS | 1,980 chars → (1.98) × ₹3.0 | 5.94 |
| OpenAI LLM | 22 turns × ~1,100 input (growing history) + 55 output → $0.0040 | 0.34 |
| **Total** | | **₹13.93** |

---

### Scenario D — Wrong Number / Immediate Hangup

> Caller disconnects within 20 seconds after the greeting.

| Cost Item | Calculation | ₹ |
|---|---|---|
| Plivo inbound | 1 min × ₹0.6 (minimum) | 0.60 |
| Sarvam STT | ~5s billed (minimal caller audio) | 0.04 |
| Sarvam TTS | ~90 chars (1 greeting) → (0.09) × ₹3.0 | 0.27 |
| OpenAI LLM | 1 turn → negligible | 0.01 |
| **Total** | | **₹0.92** |

---

## Monthly Volume Projections

Assumed call mix: **50% short (A), 35% typical (B), 10% long (C), 5% wrong number (D)**  
Weighted average cost per call: `0.50×2.78 + 0.35×7.67 + 0.10×13.93 + 0.05×0.92` = **₹5.44/call**

| Volume | Daily Cost | Monthly Cost | Notes |
|---|---|---|---|
| 30 calls/day | ₹163 | ₹4,900 | Small pilot / single agent |
| 100 calls/day | ₹544 + ₹8.33 DID | **₹16,570** | Moderate live deployment |
| 250 calls/day | ₹1,360 + ₹8.33 DID | **₹41,050** | Active sales period |
| 500 calls/day | ₹2,720 + ₹8.33 DID | **₹81,850** | High-volume campaign |

*DID (phone number) is ₹250/month — fixed regardless of volume.*

---

## Cost Breakdown by Service (at 100 calls/day, typical mix)

| Service | % of Bill | Monthly ₹ | Notes |
|---|---|---|---|
| Plivo (voice minutes) | ~33% | ~₹5,460 | Largest single item |
| Sarvam TTS | ~28% | ~₹4,640 | Second-largest; scales with AI verbosity |
| Sarvam STT | ~17% | ~₹2,820 | Scales with call duration |
| OpenAI GPT-4o-mini | ~2% | ~₹330 | Very cheap due to short output cap |
| Plivo DID | <2% | ₹250 | Fixed |

---

## TTS Cost Sensitivity

Because TTS is the second-largest cost and fully controlled by prompt engineering, small changes in response length have real impact:

| Avg chars/response | Monthly TTS cost (100 calls/day, 14 turns avg) | Change |
|---|---|---|
| 60 chars | ₹3,528 | -24% vs baseline |
| **80 chars (current)** | **₹4,704** | baseline |
| 100 chars | ₹5,880 | +25% vs baseline |
| 120 chars | ₹7,056 | +50% vs baseline |

**Key lever**: Every 20-char reduction in average AI response saves ~₹1,175/month at 100 calls/day. The current `max_tokens=160` output cap is well-set; the system prompt instruction for 1–2 sentences keeps actual TTS chars close to 60–100.

---

## Notes & Assumptions

- **USD/INR** rate used: ₹85 per $1
- **GPT-4o-mini** pricing: $0.15/1M input tokens, $0.60/1M output tokens
- **Whisper fallback** cost not included above — triggered only on Sarvam HTTP 429 (rare in practice). If 5% of STT calls fall back: adds ~₹0.03/call at typical scenario.
- **Plivo billing interval** is 60 seconds — a 61-second call costs 2 minutes.
- **Sarvam STT billing** is per-request rounded up to nearest second. Multiple short utterances cost more than one long utterance of the same total duration.
- **Barge-in (ENABLE_BARGE_IN=true)**: cancelled TTS requests are still billed by Sarvam for the characters sent — they do not prorate on cancellation. This is captured in `tts_cancelled_calls` in the credit tracker.
- **Recording storage**: WAVs are stored locally under `./recordings/`; Plivo recording storage (₹0.032/min/month) is not used.
