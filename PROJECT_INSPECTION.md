# PROJECT_INSPECTION — LesCall

> **Status (2026-10-18):** point-in-time inspection written on 2026-09-18 as the brief for the launch video. Several facts have moved since (WhatsApp sending is live via Interakt, 10 tenants / 1,070 calls, machine-line termination, scripted flows, per-agent turn-taking). The maintained platform reference is `docs/PLATFORM.md`; this file is kept as the video brief.

Inspection date: 2026-09-18 · Repo `/home/angelo/lescall` · branch `Bettercallimprov` @ `061464c` (working tree dirty: ~24 modified files under `calling-agent/`, so production may differ slightly from HEAD).

Method: four parallel read-only code sweeps (voice path, backend/API, frontend, docs/tests/assets), each required to cite `file:line`; direct reads of the load-bearing files; read-only probes of the **live** system on this VM; and one real run of the conversation engine. Every claim below is backed by implementation, not by filenames or docs. Where the docs and the code disagree, the code wins and the disagreement is listed in §11.

> **This VM is production.** Six containers are serving real phone calls. The database holds **845 real calls, 961 contacts, 16 users across 6 tenants**, and `recordings/` holds 975 real call folders named by real phone numbers. Nothing in the inspection wrote to the repo, the database, or any service. No secret was read or printed.

---

## 1. Product Overview

**LesCall** ("Every call answered") is a multi-tenant platform for building, running and learning from **AI voice agents that make and answer real phone calls**, built for Indian businesses. The company behind it is **Dhron AI**; the app is `agent.lescall.com`, the marketing site `lescall.com`.

One sentence: *a business owner describes their business in a chat, gets a phone agent that speaks English, Hindi and Gujarati on a real number, and every call it handles comes back as searchable, evidence-backed intelligence.*

It has three layers, all implemented:

1. **A real-time telephony voice pipeline** — FreeSWITCH → VAD → speech-to-text → a stage-driven conversation engine → text-to-speech → back to the caller, with barge-in and human-like turn-taking.
2. **An agent platform** — agents are pure data (one YAML each); a chat **Copilot** builds and edits them; a **Try agent** simulator runs them as a text call; self-service **bring-your-own-number** puts them on a phone line; campaigns and scheduling drive outbound.
3. **A call-intelligence layer** — every finished call is summarised, scored and mined for commitments; calls are searchable in plain language with a **verifying AI judge**; follow-up calls require **human approval**; selections export to Excel/Word; a public `/v1` API feeds CRMs.

## 2. Problem

Small and mid-size businesses lose leads to unanswered and badly-handled phone calls, and cannot afford staff to answer, qualify, follow up and document every call — least of all in the code-mixed Hindi/English/Gujarati their customers actually speak. Generic voice bots fail on exactly the things a phone call is made of: interruptions, pauses mid-sentence, mishearing, language switching, and knowing when a call is really over. And once calls are handled by a machine, the owner needs to *trust* what it reports — not an LLM's unverifiable summary.

## 3. Target User

- **Primary:** the owner/operator or sales/ops lead of an Indian SMB — real estate, recruiting, clinics, tax/finance, textiles, coaching, retail. The 49 agent definitions in the repo span all of these.
- **Secondary:** a developer at that business integrating call data into a CRM/ERP via `/v1`.
- **Internal:** the platform operator (separate admin process on a UNIX socket, not internet-reachable).

Not enterprise/team software: there is one owner per tenant and no role/seat model (`auth.is_admin()` returns `False` for everyone, `dashboard/auth.py:205`).

## 4. Core Workflow

**Build → Test → Go live → Calls happen → Understand → Act.**

1. **Build.** In `/copilot` the owner chats with the Agent Copilot. A server-computed 13-layer interview (`dashboard/copilot_interview.py:256-350`) covers identity, objective, voice, languages, call flow, data to capture, knowledge, objections, scheduling, guardrails, greeting/close. The server *refuses to create the agent* until every compulsory layer is answered or explicitly skipped. The Copilot LLM has 28 tools (`agent_copilot_api.py:3829-4315`); the owner's own wording outranks LLM-authored copy.
2. **Test.** "Try agent" opens a simulated call: a real `ConversationRouter`, the agent speaks first, and the UI shows stage, language, captured slots and would-be bookings advancing turn by turn. Only speech is skipped (`agent_copilot_api.py:7289-7527`).
3. **Go live.** In `/telephony` the owner registers a SIP trunk + number; the platform generates FreeSWITCH gateway/dialplan XML, reloads FS over ESL, and binds the number to an inbound and/or outbound agent. "Test call" rings the owner's own phone.
4. **Calls happen.** Inbound calls route DID → agent (direction-aware, tenant-checked). Outbound: single dial, campaigns with live preview, scheduled calls — all through one guarded funnel (`server.py:5014-5108`) that checks duplicates, DND, calling hours, voice-provider health and billing on every dial.
5. **Understand.** A worker runs an 8-stage resumable pipeline per call: `extract → embed → followup → memory → digest → timeline → automation → notify` (`shared/intelligence/pipeline.py:41`). Output: summary, sentiment journey, lead/opportunity/risk/compliance scores, topics, objections, buying signals, key moments, **commitments with verbatim quotes**, action items, and a follow-up judgement.
6. **Act.** Search calls in natural language (verified filter), review and approve follow-up calls, export a workbook, generate a report, or pull everything through `/v1`.

**Input → processing → output for one caller turn:** 8 kHz audio from FreeSWITCH → resampled to 16 kHz PCM16 → FSMN neural VAD segments speech → Sarvam `saarika:v2.5` transcribes (raced/hedged) → transcript validated against hallucination/script rules → conversation engine (deterministic shortcuts first, else Gemini with the stage goal, known slots and RAG evidence) → Sarvam `bulbul:v3` streaming TTS, flushed clause-by-clause → WAVs injected into the FreeSWITCH channel. Interrupt at any time and playback is broken at the switch.

## 5. Architecture

```
PSTN / SIP carrier
      │
 FreeSWITCH ── ESL :9000 ──► socket_server.py      call control, playback, hangup, uuid_break   (2,703 lines)
      │  mod_audio_stream
      └─ WS :9001 ─────────► ws_audio_receiver.py  L16 8k → PCM16 16k                          (1,110 lines)
                                   │
                          conversation_call_bridge.py   one TelephonySession per call; DID→agent routing;
                                   │                    preflight; metering; persistence       (2,173 lines)
                                   ▼
                  calling-agent/backend/app/voice/pipeline.py   6 asyncio tasks, 4 bounded queues  (~5,000 lines)
                  audio_vad → stt_submitter → stt_worker → conversation → tts  (+ silence_watchdog)
                                   │
        TTS WAVs ── TCP :9004 ──► socket_server.py ──► FreeSWITCH ──► caller
```

| Container | Runs | Notes |
|---|---|---|
| `lescall-runtime` | `socket_server.py` + `ws_audio_receiver.py` | host network; verified `VAD_ENGINE=fsmn`, `LLM_TOOLS_MODE=on` |
| `lescall-api` | FastAPI `dashboard/server.py` on :8080 | 217 tenant routes: dashboard, builder, Copilot, intelligence, billing, telephony, `/v1` |
| `lescall-workers` | intel worker, billing sync, metering/outbox/recording-sync/log-ship tick loop | |
| `lescall-admin` | operator plane | UNIX socket only |
| `lescall-dashboard` | nginx serving the React SPA on :8081 | |
| `lescall-pg` | Postgres 16 + pgvector | 56 tables, 28 forward migrations each with a `.down.sql` |

**Stack:** Python 3.11 / FastAPI / asyncio; React 18 + TypeScript + Vite + Tailwind v3 + shadcn/ui + TanStack Query + `motion`; Postgres 16 + pgvector (HNSW) + FTS + `pg_trgm`; FreeSWITCH; Sarvam AI (STT `saarika:v2.5`, TTS `bulbul:v3`); Google Gemini via its OpenAI-compatible endpoint — **verified live config:** calls `gemini-3.5-flash-lite` (reasoning `minimal`), Copilot `gemini-3.6-flash` (reasoning `high`); OpenAI `whisper-1` STT fallback and `text-embedding-3-small` for document RAG; `gemini-embedding-001` (768-d) for call search; GCS for recordings/exports/logs; Razorpay client (unconfigured, see §11).

**Agents are data.** An `agent.yaml` holds identity, personality, languages + per-language voice, `call_flow.stages[]` (goal, `collect`, `advance_when`, `branches`), typed `slots[]`, a 19-key `response_bank` incl. an objection playbook, knowledge catalog, guardrails, outbound lines. "No domain knowledge is embedded in the runtime code" (`schema.py:781-790`). Every write goes through one choke point with optimistic concurrency, Postgres revision snapshots (last 50), atomic replace, and a post-write proof that auto-restores if any unrequested path changed (`agent_definition/store.py:1-52`).

## 6. Key Features (verified in code; ★ = also observed running)

**Voice engine**
- ★ Live on real calls today: the runtime logs per-turn telemetry (`speech_end→engine 389ms (stt=187ms)`, `speech_end→first_audio 1594ms`).
- **Barge-in at the telephony layer**: `uuid_break` to FreeSWITCH *before* draining local queues, because audio already handed to the switch keeps playing (`pipeline.py:1319-1330`, `socket_server.py:1019-1050`). Echo-aware: the bar doubles while the agent's own audio is on the wire.
- **Pause tolerance that reads the speaker**: a narrative-length utterance or a caller who has already resumed through a pause gets a 900 ms hold (`pipeline.py:2742-2774`).
- **Continuation merge**: if the caller keeps talking after the agent started answering, it cancels generation, breaks audio, merges both fragments, re-answers, and **retracts the unheard line from the transcript** (`pipeline.py:3436-3548`, `call_audit.py:143-180`).
- **Raced speech recognition**: a second Sarvam request at 0.6 s, a Whisper hedge at 1.0 s; a clean "no speech" verdict beats any hedge, so racing never invents words (`stt/router.py:487-672`).
- **Transcript validation**: rejects hallucinated phrases, unsupported scripts, Marathi look-alikes, repetition, noise tokens (`stt/router.py:242-350`).
- **Neural VAD in production**: WebRTC idle gate → FunASR FSMN-VAD ONNX on a dedicated thread, self-instrumented, with a *visible* energy fallback (`vad_stack.py`).
- **TTS hallucination gate**: a calibrated duration-vs-text model (worst legitimate ratio 0.95 over 88 syntheses; real hallucinations 1.27 and 1.44) refuses the cache write and evicts on read (`tts_sarvam.py:183-241, 682-698`).
- **Evidence-based language following** (en/hi/gu): per-word density voting; an STT language pin that must be *earned* by caller evidence and self-releases every 8 turns (`conversation.py:634-802`, `pipeline.py:2032-2085`).
- **LLM proposes, code disposes**: 5 tools (`record_customer_details`, `book_appointment`, `request_callback`, `flag_do_not_call`, `end_call`) pass deterministic gates — placeholder rejection, past-date rejection, stage legality (`conversation.py:3944-4060`).
- **Never hangs up uninvited**: a goodbye triggers a confirmation question; hangup waits on five independent conditions (`conversation.py:3030-3079`, `conversation_call_bridge.py:1970-2029`).
- **Document RAG on live turns**: BM25 + vector fused with RRF, measured abstention, citations built from evidence (never model output), never raises into a call (`app/documents/retrieval.py`).
- **Four artifacts per call** + a `call_turns` row per turn with latency, STT confidence, barge-in flag and RAG sources (`conversation_call_bridge.py:1748-1789`).

**Agent platform**
- ★ **The real engine, run during this inspection** (see §9/§10): 6 caller turns against the fictional `fitcoach_ai-inbound` agent → genuine replies, **9 of 10 slots captured**, no field re-asked, stage `GREETING → UNDERSTAND_FITNESS_GOAL`.
- Copilot: 13-layer interview gate, 28 tools, provenance ranking, document upload/ingest, voice dictation with vocabulary repair against the tenant's own names (`dashboard/copilot_voice.py`), proposed-change diff cards with Apply/Discard, and **readback of what the caller will actually hear** by running the real engine (`agent_copilot_api.py:2119-2160`).
- Agent editor: structured settings, stages, raw YAML with validate + If-Match, documents/RAG, voice preview, **revision history with Restore**.
- Agent sharing via tokenized link → independent copy.
- BYON self-service telephony with tenant-isolated routing; an unbound registered number answers as a neutral persona rather than leaking to another tenant (`conversation_call_bridge.py:259-390`).
- Outbound: campaigns with a server-computed live preview, per-iteration re-checks of hours/DND/voice/billing, scheduled calls that are *missed, never fired late* after 30 min (`shared/scheduling.py`).

**Intelligence**
- Per-call structured extraction with **code-side guards**: every commitment quote must fuzzy-match the transcript or it is dropped (`extract.py:247-254, 341-342`).
- **Verified smart filter**: planner → hybrid retrieval as *candidate generation* → one judge call per candidate → three code-owned gates, the last being *the quoted evidence must occur in the transcript we hold* (`verify.py:436-468`). Reports `verified=false`, `truncated`, `pool_full` honestly; a verified empty result is presented as an answer.
- Hybrid call search: pgvector HNSW + Postgres FTS + trigram fallback, RRF-fused, degrading to lexical-only (`search.py`).
- **Human approval gate for follow-up calls**: three independent conditions in SQL, re-checked in code, and a third check directly above the originate; DND overrides an operator's click (`autodial.py:224-277`, `server.py:4488-4501`).
- ★ In-call do-not-call handling is live in the engine (`conversation.py:2899, 4043`).
- Contact Hub with governed per-contact memory; conversation analytics; reports (Word/Markdown/Excel); 9-sheet Excel export with an Evidence sheet, GCS-backed with local fallback.
- ★ **Public `/v1` API** — probed live: deny-by-default auth (401), one error envelope with `request_id`, and an OpenAPI document titled "LesCall Integration API" listing 15 operations. Key → account IP allowlist → status → per-key allowlist → dual rate budgets, state in Postgres.

**Operations:** host-level self-healing sentinel (never restarts the runtime mid-call), voice-provider health probes, CDR-refereed minute metering, GCS recording sync, log shipping. **Tests:** 1,677 `def test_` functions in 108 files (RUNBOOK §51.4 reports 2,296 collected); one CI workflow — an offline RAG evaluation gate.

## 7. Demonstrable Features

| Rank | Moment | Why it films well | Safe? |
|---|---|---|---|
| 1 | **Login page** — animated two-band "conversation" waveform, "EVERY CALL ANSWERED" | The best-designed frame in the product; real and public | ✅ real, no data |
| 2 | **Copilot building an agent** — interview rail filling, spec preview assembling, Try/Edit/Activate chips | The product's thesis in one screen | ✅ with a fictional build |
| 3 | **Try agent** — stage badge advancing, slot badges filling turn by turn | Proves "a conversation engine, not a chatbot" | ✅ replays *genuine* engine output |
| 4 | **Verified smart filter** — type a sentence → "AI-verified · read N of M · K matched" → rows showing the quoted evidence | The most differentiated feature | ✅ with sample data |
| 5 | **Single-call workspace** — four score rings, transcript, commitments with verbatim quotes | "Every call becomes a record" | ✅ with sample data |
| 6 | **Follow-up review** — green "promise on record" vs amber "AI-inferred" | The trust story | ✅ with sample data |
| 7 | **Live calls** — AI/Caller speaking equalizer, 6-segment stage strip, latest line | Shows it is a live system | ✅ with sample data |
| 8 | **Overview / Agents grid** — count-up stats, sparkline, "● Live on +91…" | Establishing shots | ✅ with sample data |

Not filmable: Developers (keys), Billing/Receipt (tax identifiers), Settings (personal phone), Telephony (server IP, SIP details), and any list page against live data.

## 8. Visual Assets

**Existing in the repo — almost nothing.** One logo (`frontend/public/logo.png`, 453×166 RGBA). No screenshots, no diagrams (only ASCII blocks in the RUNBOOK), **no existing video**. Eight synthetic TTS voice previews in `data/voice_samples/` (16 kHz WAV, shipped product assets, no customer data).

**Brand, extracted from source:** name **LesCall**; tagline **"Every call answered"** (`dashboard/static/login.html:208`); SPA title "LesCall — AI Call Intelligence". Login palette: background `#050a07`, emerald `#1dbf67` / `#4ff09a`, text `#f2f7f4`, sage `#8fb9a2`. Dashboard tokens: accent `#00d98c` (light) / `#00f5a0` (dark), secondary indigo `#4f46e5` / `#8b85ff`, dark bg `#070a12`. Fonts: Inter Variable (body), Plus Jakarta Sans Variable (display/metrics); the login headline uses a heavy condensed display face.

**Created for this video** (all outside the repo, in `/home/angelo/lescall-video/`):
- `assets/screens/00-login-real.png` — the real, public login page, captured from the running server.
- `sim/sim_fitcoach.json` — a **genuine run of the production conversation engine** (see §10).
- A capture harness serving the **byte-identical production SPA build** (`frontend/dist`; asset hashes `index-DH_NvLWP.js` / `index-CcLJenB_.css` match the live container exactly) against a mock `/api` with fictional sample data. Response shapes are derived from the real backend handlers and TypeScript types.

**Disclosure that must travel with the video:** UI frames are the real product; the *data* in them is fictional sample data (callers, numbers in the unassignable `+91 55501 00xxx` range, scores, counts). Numbers visible inside UI frames are sample values, not performance claims. The Try-agent conversation is the exception — those agent replies are real engine output.

## 9. Recommended Demo Flow

1. Brand hook — the login-page waveform and "Every call answered".
2. The problem in one beat — a call nobody answers.
3. **Build**: Copilot interview → spec preview → agent created.
4. **Test**: Try agent — real replies, slots filling.
5. **Live**: agent on a number; a live call with the stage strip.
6. **Understand**: smart filter → AI-verified → quoted evidence → single-call score rings.
7. **Trust**: nothing dials without a human's approval.
8. Close on the brand and `lescall.com`.

## 10. Technical Highlights (each supported by implementation)

1. Interruption is handled where it matters — at the phone switch (`uuid_break`), not just in software.
2. It distinguishes "you finished" from "you paused", per speaker, per call.
3. It takes its own answer back — and off the transcript — when the caller wasn't done.
4. Two speech engines race every turn; "no speech" beats a guess.
5. It refuses to speak or cache audio that doesn't match its text (calibrated on 88 live syntheses).
6. A neural VAD runs off the event loop and announces its own degradation.
7. The LLM proposes; deterministic code commits.
8. The smart filter's empty result is trustworthy, because a model cannot fabricate words into a transcript the platform already holds.
9. Follow-up consent is a recorded human action behind three redundant gates — because two production incidents proved anything less insufficient.
10. Agents are pure data: 49 businesses, one runtime, zero domain code.

**Observed in the engine run (this inspection):** `fitcoach_ai-inbound`, 6 caller turns, model `gemini-3.5-flash-lite`, text-path reply times 2.3–4.2 s, 12 LLM requests, 68,878 input / 493 output tokens, est. ₹1.93, run with `LLM_TOOLS_MODE=on` while no real call was in progress, cost ledger redirected to scratch, calendar writes captured. *(Text-path timings are not telephony latency and are not used as a claim.)*

**Written-down measurements in `deploy/RUNBOOK.md`** (citable with their section, not used as headline claims in the video): smart filter — zero false positives across six filters; recall 51% → ~100% after the arm-width fix (§32). RAG over 37 golden questions — Recall@1 0.66→0.79, abstention 0.56→1.00 with 0.00 over-abstention, citation accuracy 1.00 (§28). Measured platform cost ₹4.29/call-minute over 3,375 calls (§27.1). LLM TTFT p50 0.98 s over 172 turns (§43.2). Sentinel repaired a killed container in 18 s (§9).

## 11. Limitations

**Must not be claimed** (code does not support it):
- **Calendar integration** — `calendar.type` supports only `mock`; `webhook` is unimplemented. Bookings are internal records.
- **WhatsApp / SMS / Slack / Teams / CRM push** — automation actions return `not_configured` (`automation.py:417`). The agent can *promise* a WhatsApp; a human sends it.
- **Streaming / word-by-word transcription** — removed 2026-07-31. STT is batch per utterance. The Copilot chat does **not** token-stream either.
- **"Any language"** — deep support is English, Hindi, Gujarati only.
- **Scale / concurrency / HA** — one VM; no measured capacity figure; the LLM key is a free tier shared with live calls.
- **Latency figures** — today's log shows `speech_end→first_audio` of 1.6 s and 5.0 s; too variable for a "sub-second" claim.
- **Live payments / enforced plan limits** — Razorpay has no account (checkout 503s); `billing_enforce` is off. Only the free-tier floor and the BYON gate bind.
- **Open self-signup** — gated by `LESCALL_ALLOWED_EMAILS`; default is a single address.
- **Teams/roles; automated backups; API webhooks; Kamailio SIP edge** — not present / not installed.
- **Exhaustive filter recall** — the filter itself reports `pool_full`/`truncated`. Claim precision and evidence, not completeness.

**Other honest notes**
- `CLAUDE.md` is stale in 8 places vs. the code — notably it calls the VAD "energy-based" (production runs the FSMN neural stack), says audio arrives as PCMU (it arrives as L16), describes 5 tasks (there are 6), and frames the system as "for Pramukh Group" (it is a multi-tenant platform).
- Dead code that looks live: `tts_sarvam_ws.py`, `tts_cartesia.py`, `stt_stream.py` (zero importers); `pipeline.py.save`.
- Two follow-up systems and two analysis paths coexist during a documented migration.
- In the engine run, the stage stayed at `UNDERSTAND_FITNESS_GOAL` for turns 2–6 although the questions covered later stages' topics — stage advance is conservative for this agent. The video shows only the transition that actually happened.
- **Found while capturing the real UI (all are genuine product behaviour, none were worked around):**
  - *Live calls · stage strip.* `StageStrip` only lights segments whose id equals or starts with one of six fixed names (Greeting/Discovery/Match/Schedule/Confirm/Close). An agent with custom stage ids — every Copilot-built agent, e.g. `UNDERSTAND_FITNESS_GOAL` — shows a correct "Stage" badge above **six unlit segments**. The video shows this as it is.
  - *Copilot · interview rail and spec preview* are populated only from a `POST /copilot/chat` reply; `GET /copilot/sessions/{sid}` returns the transcript alone (`agent_copilot_api.py:7616`, `CopilotChat.tsx:713, 790`). Re-opening a build in progress therefore shows the thread with no rail and no preview until the next message is sent.
  - *Dark mode · sidebar logo.* `public/logo.png` is navy text on transparency, so in dark mode the wordmark is close to invisible against the sidebar. There is no dark variant. (The login page avoids this by using an HTML wordmark.)
  - *Call workspace · score rings* render the label inside a 64 px ring; "Opportunity" and "Compliance" overflow it (`components/charts.tsx:64`).
- A 259 MB Python core dump sits at the repo root (`core`, gitignored). It is a memory image of a process that held API keys — worth deleting; **not** touched here.

**Validation limits.** Login is Google-OAuth-only, so the authenticated dashboard could not be driven against production without forging a session — which was not done. No phone call was placed (it would spend credits and ring a real line). UI behaviour was therefore observed on the production build against a mock API; engine behaviour was observed for real.

## 12. Video Concept

**Title:** *LesCall — Every call answered.*
**Angle:** not "AI is changing everything" — a concrete loop the viewer can follow in 45 seconds: **describe your business → hear your agent → it answers the phone → every call comes back as evidence you can trust.**
**Tone:** `polished` — quiet, premium, confident; the dark/emerald world of the real login page; real UI as the hero; type that holds long enough to read.
**Proof over adjectives:** the agent's replies on screen are real engine output; the filter shows the *quote* that qualified each call; the approval queue shows the recorded promise.
**Format:** 1920×1080 landscape, ~45 s, music + restrained UI sound, no voiceover.
**Deliberate deviation from brag's default:** brag targets 15–25 s social clips; this is a stakeholder presentation of a three-layer platform, so it runs ~45 s via brag's `--duration` override.
