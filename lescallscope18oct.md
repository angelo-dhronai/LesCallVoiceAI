# LesCall — Scope of changes, 18 October 2026

What this file is: the problem list that came out of the full platform
inspection on 2026-10-18. Part 1 is the index of every change, grouped by the
layer it belongs to. Part 2 explains each one: where it lives today, what
happens today, why it matters, and a short idea of how it could be solved.
It is deliberately **not** an implementation plan. Nothing in here has been
started; every item waits for the owner's pick.

Companion files: `docs/PLATFORM.md` (the maintained map of the platform),
`CLAUDE.md` (operating doctrine), `deploy/RUNBOOK.md` (dated change log,
cited as §N).

---

## Glossary — the words used below, in plain language

| Term | Meaning on this platform |
|---|---|
| **Runtime** | The `lescall-runtime` container: the two processes that handle live phone audio (`socket_server.py`, `ws_audio_receiver.py`) and everything they import from `calling-agent/backend/app/voice/`. |
| **Dashboard / API** | The `lescall-api` container running `dashboard/server.py`: everything the web app calls. |
| **SPA / UI** | The React web app under `frontend/src`, served by `lescall-dashboard`. |
| **Agent YAML** | One file per agent, `calling-agent/agents/<slug>/agent.yaml`. All behaviour of an agent is data in this file; the engine code has no business knowledge. |
| **Twin** | An agent exists as two files, `<name>-inbound` and `<name>-outbound`. The slug decides the direction. |
| **Revision** | Every save of an agent YAML creates a numbered revision in Postgres; the editor's History tab can restore one. |
| **Env knob** | A setting read from an environment variable, e.g. `FSMN_MAX_END_SIL_MS=280`. These live in `calling-agent/.env` or `deploy/docker-compose.services.yml`. |
| **Module-level constant** | An env knob that is read once, when the Python file is first imported, into a global like `TURN_CONTINUATION_HOLD_MS = int(os.environ.get(...))`. Changing it requires restarting the container. Almost every voice knob is one. |
| **VAD** | Voice Activity Detection: the component that decides, every 20 ms, whether the caller is speaking. Production uses the FSMN neural model (`vad_stack.py`) behind a cheap WebRTC pre-filter; the old energy-threshold VAD is only the fallback. |
| **Endpointer / endpointing** | After the VAD says speech stopped, the endpointer decides how much longer to wait before treating the sentence as finished. Three profiles: fast (sentence looked complete), default, hold (sentence ended on "and", "but", a digit). File `endpointer.py`. |
| **Turn** | One caller utterance plus the agent's reply to it. |
| **Pause tolerance / pause hold** | Holding a turn open after a pause because the caller is probably not finished. Platform default 900 ms, only for "narrative" callers; per-agent `pause_hold_ms` forces it for everyone. §46, §78. |
| **Continuation merge** | The caller resumes while the agent has started answering: the engine cancels the reply, joins both fragments, answers once. §11. |
| **Stale fragment** | A trailing piece of a turn that was already answered; it is absorbed into context instead of being answered twice. |
| **Barge-in** | The caller interrupts while the agent is talking. The engine stops playback at FreeSWITCH (`uuid_break`). |
| **Back-channel** | A listener's "हाँ", "जी", "hmm", "okay" said while the agent is still talking. Not a turn. Per-agent `backchannel_guard`. §78. |
| **Echo gate** | A guard that stops the agent's own voice, which leaks back on the phone line, from being heard as the caller. |
| **Filler** | "One moment…" played while the model is slow. Not recorded in the transcript. |
| **First audio** | The time from the caller finishing a sentence to the first sound of the agent's reply. Logged as `[LATENCY] speech_end→first_audio`. This is the latency a caller feels. |
| **STT hedge** | A second speech-to-text request started when the first one is slow: a second Sarvam request at 0.6 s, a Whisper request at 1.0 s. |
| **Tool calling** | The model can return a structured call such as `record_customer_details` or `book_appointment` instead of, or in addition to, words. The engine validates and executes it. |
| **`LLM_TOOLS_MODE` on / shadow / off** | How tool calling runs. **on** (production): tools are offered on the main spoken request. **shadow** (the per-agent default in YAML): the spoken request has no tools; a separate background request, the **background extractor** or **shadow extractor**, reads the turn afterwards and fills the slots; action proposals are only logged. **off**: no tools anywhere. The env variable overrides every agent; it is set only in the runtime container. |
| **Slot** | A fact the agent must collect, e.g. `budget`, `location`, defined per agent in YAML. |
| **Stage** | A step of the call flow, e.g. GREETING or INVITE_VISIT. Each agent defines its own list. |
| **Band** | A section of the system prompt, e.g. `━━ VISIT DECLINED ━━`. Some are static per agent, some are added per turn. |
| **Try agent** | The existing text simulation in the dashboard: the real conversation engine runs as a text chat, no audio. Button "Chat with agent" on an agent card, and the "Try" chip in the Copilot. |
| **Copilot chips** | The small round action buttons shown under a Copilot reply after it creates or edits an agent: "Try {agent}" and "Activate {agent}" (`CopilotChat.tsx:249-276`). |
| **Quick controls** | The Copilot's "Voice & speed" side sheet: pick a voice, move a speed slider, saved straight to the agent. §67. |
| **Test call** | The button on the Telephony page that rings the owner's own phone from a connected number with the bound agent on the line. |
| **calltester** | A developer tool outside the repo (`/home/angelo/calltester`) that replays a past call's caller lines, or a written script, through the real engine in text and reports stage, slots and replies against expectations. |
| **callsim** | A developer tool outside the repo (`/home/angelo/callsim`) that runs the real audio pipeline against a fake FreeSWITCH with synthesised or recorded caller audio and measures latency, VAD behaviour, interruption handling and transcription accuracy. |
| **`call_turns`** | A Postgres table with one row per turn of every call: stage, intent, language, STT latency, STT confidence, time to engine, model first-chunk time, time to first audio, filler flag, barge-in flag. 7,006 rows today. No page reads it. |
| **Live state file** | `phase14_runtime/live_calls_state.json`, rewritten every 1.5 s by the runtime; the Live page streams it. |
| **Sentinel** | The host watchdog that restarts containers and emails the operator. It never alerts tenants. |
| **BYON / DID / trunk** | Bring Your Own Number; the phone number; the SIP connection to the carrier that carries it. |
| **Trial floor** | The few limits that apply to free accounts even though billing enforcement is switched off. §44. |
| **Judge** | The second model call in the Intelligence filter that checks each candidate call against a requirement checklist and quotes the evidence. §31. |

---

# Part 1 — Index of all changes

Columns: ID · change · the layer(s) that change · priority (P0 = before selling more, P1 = next, P2 = later).

## A. UI changes (frontend/src)

| ID | Change | Layers | Prio |
|---|---|---|---|
| UI-1 | Call quality panel on the call page: per-turn latency breakdown, STT confidence, interruption and VAD events | UI, API (API-2), DB (DB-1) | P0 |
| UI-2 | Audio player synced to the transcript on the call page | UI | P1 |
| UI-3 | Live page: stage strip from the agent's own stages, per-turn latency, "waiting since" timer | UI, API (API-3), runtime (ENG-3) | P1 |
| UI-4 | One "Test" workspace per agent: Chat, Script run, Audio simulation, Results history | UI, API (API-1), tester (TEST-1, TEST-2) | P1 |
| UI-5 | Test call result screen with per-turn feedback marks | UI, API, DB (DB-2) | P0 |
| UI-6 | "Call behaviour" tab in the agent editor: voice and delivery, tone, listening, latency budget, with presets | UI, engine (VAD-1, VAD-2, ENG-1), Copilot (COP-1) | P0 |
| UI-7 | Expose the per-agent settings that already exist but are YAML-only | UI | P0 |
| UI-8 | Stop the Builder hard-coding engine values at create time | UI, API (API-4) | P0 |
| UI-9 | Agent quality trend on the agent card | UI, DB (DB-3), tester (TEST-4) | P2 |
| UI-10 | In-product help: contextual explanations, first-login tour, "what changed" surface | UI, docs (DOC-1) | P1 |
| UI-11 | UI strings in Hindi and Gujarati for daily-use pages | UI | P2 |
| UI-12 | Notification preferences page | UI, intelligence (INT-3) | P2 |
| UI-13 | Minute top-up control on Billing | UI, commercial (COM-2) | P1 |
| UI-14 | Actions on a live call: end, listen-in | UI, telephony (TEL-2) | P2 |
| UI-15 | Unify the test entry points (Chat with agent, Copilot Try chip, Test call) under UI-4 | UI | P1 |
| UI-16 | Owner feedback on a finished call that reaches the Copilot | UI, DB (DB-2), Copilot (COP-3) | P1 |
| UI-17 | Real tooltips instead of browser title hints | UI | P2 |
| UI-18 | Quick-jump search over agents, contacts and calls, not only page names | UI | P2 |
| UI-19 | Copilot: a build in progress shows no interview rail or preview when re-opened | UI, Copilot | P2 |

## B. VAD and listening changes (runtime: pipeline.py, vad_stack.py, endpointer.py)

| ID | Change | Layers | Prio |
|---|---|---|---|
| VAD-1 | Make the listening knobs per-agent data instead of process-wide constants | runtime, agent schema, controls registry | P0 |
| VAD-2 | Named listening presets: Patient, Balanced, Snappy, each with its latency cost stated | runtime, UI (UI-6), Copilot | P0 |
| VAD-3 | Record what the VAD and endpointer decided on every turn | runtime, DB (DB-1) | P0 |
| VAD-4 | Remove or wire the inert `HARD_FLUSH_SILENCE_FRAMES=20` override in compose | deploy | P2 |
| VAD-5 | Barge-in sensitivity as a per-agent control | runtime, UI | P1 |
| VAD-6 | A "quiet caller / noisy line" option for the hard-coded energy floors and SNR gate | runtime | P2 |
| VAD-7 | Silence re-prompt timing and count per agent | runtime, UI | P1 |
| VAD-8 | Audio-level simulator inside the product (the VAD half of the tester) | tester (TEST-2) | P1 |

## C. Latency changes (runtime: STT, LLM, TTS path)

| ID | Change | Layers | Prio |
|---|---|---|---|
| LAT-1 | Take `record_customer_details` off the spoken request by default | agent schema default, Builder, Copilot | P0 |
| LAT-2 | Model and reasoning effort per agent instead of one env value for all | runtime, UI | P1 |
| LAT-3 | Latency budget: expected versus measured first-audio time per agent | UI, API, DB | P1 |
| LAT-4 | Filler policy per agent in the UI | UI (UI-7) | P1 |
| LAT-5 | Show the measured seconds of each voice and pace so speed choices are informed | UI, API | P1 |
| LAT-6 | Per-turn "why was this slow" breakdown: VAD close, STT, model, TTS | UI (UI-1), DB (DB-1) | P0 |

## D. Conversation engine and agent model changes

| ID | Change | Layers | Prio |
|---|---|---|---|
| ENG-1 | Tone as presets with an audible sample, on top of the free-text fields | engine prompt blocks, UI, Copilot | P1 |
| ENG-2 | Tool-mode parity: every simulation must run the engine exactly as a phone call does | dashboard env, tester | P0 |
| ENG-3 | Publish the agent's stage list and labels into the live state | runtime | P1 |
| ENG-4 | Pricing and inventory facts with validity dates | agent schema, engine, Builder | P2 |
| ENG-5 | Platform-placed callbacks when the caller says "call me in ten minutes" | engine, scheduling, telephony | P2 |

## E. Testing and simulation

| ID | Change | Layers | Prio |
|---|---|---|---|
| TEST-1 | Agent Response Tester: scripts, runs, per-turn readout, deterministic checks, optional model judge, scorecard, history, compare | UI, API, DB (DB-3), engine (ENG-2) | P1 |
| TEST-2 | Audio simulation in the product, built from callsim | runtime sandbox, API, UI | P1 |
| TEST-3 | Test call that reports back (the result screen plus marks) | UI (UI-5), API, DB (DB-2) | P0 |
| TEST-4 | Scheduled regression suites per agent with a notification on regression | workers, API, UI | P2 |
| TEST-5 | Bring calltester and callsim into the repository | repo, tests | P1 |
| TEST-6 | Script generation: from a past call, from the agent definition via the Copilot, by hand | API, Copilot (COP-4) | P1 |
| TEST-7 | The judgement rubric and the deterministic check list | tester | P1 |

## F. Backend and API

| ID | Change | Layers | Prio |
|---|---|---|---|
| API-1 | Endpoints for test scripts, test runs, simulation runs and feedback marks | dashboard | P1 |
| API-2 | Per-turn data for a call: `GET /api/intel/calls/{key}/turns` | dashboard | P0 |
| API-3 | Live state with latency and the agent's stage list | dashboard, runtime | P1 |
| API-4 | A platform-defaults endpoint for the Builder | dashboard | P0 |
| API-5 | Remove routes that are mounted but refused for every tenant | dashboard | P2 |
| API-6 | Per-call cost in the call record, including the model cost | dashboard, DB | P2 |

## G. Database

| ID | Change | Layers | Prio |
|---|---|---|---|
| DB-1 | New `call_turns` columns for VAD and endpointer decisions and TTS timing | migration | P0 |
| DB-2 | A table for owner feedback marks per turn | migration | P0 |
| DB-3 | Tables for test scripts, runs and per-turn results | migration | P1 |
| DB-4 | Tenant membership and roles tables | migration | P1 |
| DB-5 | Per-call cost ledger for model, STT and TTS | migration | P2 |

## H. Copilot

| ID | Change | Layers | Prio |
|---|---|---|---|
| COP-1 | The Copilot knows and can set the new listening and tone controls | dashboard, controls registry | P0 |
| COP-2 | "Fix with Copilot" from a failed test turn | dashboard, UI | P1 |
| COP-3 | Owner feedback on a call becomes a proposed change | dashboard | P1 |
| COP-4 | The Copilot generates a test suite from the agent definition | dashboard | P1 |

## I. Telephony and live calls

| ID | Change | Layers | Prio |
|---|---|---|---|
| TEL-1 | Test call linked to the resulting call record | dashboard, UI | P0 |
| TEL-2 | Listen-in, whisper and end-call on a live call | FreeSWITCH, socket_server, dashboard, UI | P2 |
| TEL-3 | Transfer to a human with context | FreeSWITCH, engine | P2 |

## J. Intelligence and follow-through

| ID | Change | Layers | Prio |
|---|---|---|---|
| INT-1 | Knowledge-gap console: the questions the agent could not answer | workers, dashboard, UI | P2 |
| INT-2 | Agent quality over time per revision | workers, DB, UI | P2 |
| INT-3 | Tenant notification channels beyond the in-app bell | workers, integrations | P1 |
| INT-4 | Campaign analytics by script version and time of day | dashboard, UI | P2 |

## K. Integrations

| ID | Change | Layers | Prio |
|---|---|---|---|
| IG-1 | Real calendar booking (Google, Outlook) | engine calendar store, dashboard | P1 |
| IG-2 | Outbound webhooks on the public API and a producer for the CRM outbox | public API, workers | P1 |
| IG-3 | Email delivery that works from the workers container | workers image, config | P1 |
| IG-4 | SMS, Slack and Teams automation actions | workers | P2 |

## L. Operations and reliability

| ID | Change | Layers | Prio |
|---|---|---|---|
| OPS-1 | Commit six weeks of uncommitted work and set a commit cadence | repo | P0 |
| OPS-2 | Off-box backups of the database and the agent folders, with a restore drill | host, GCS | P0 |
| OPS-3 | A second speech vendor on the live path | runtime | P2 |
| OPS-4 | Measure the concurrent-call ceiling of this VM | ops | P1 |
| OPS-5 | Stop running the recording sync twice | host timer or workers | P2 |
| OPS-6 | Delete dead code and the core dump | repo | P2 |
| OPS-7 | Status page and incident notices for tenants | dashboard, sentinel | P2 |
| OPS-8 | Keep `docs/PLATFORM.md` current | process | P1 |

## M. Security

| ID | Change | Layers | Prio |
|---|---|---|---|
| SEC-1 | The API listens on every interface on port 8080 | deploy | P0 |
| SEC-2 | Recording and export download links never expire | dashboard | P1 |
| SEC-3 | Trunk passwords stored in plain text | DB, telephony | P1 |
| SEC-4 | No way to revoke a login session | auth | P1 |
| SEC-5 | Open signup for any Google account | config, product decision | P0 |

## N. Commercial and accounts

| ID | Change | Layers | Prio |
|---|---|---|---|
| COM-1 | Decide and switch on a minute gate for free accounts | billing, admin | P0 |
| COM-2 | A way to buy minutes | billing, UI (UI-13) | P1 |
| COM-3 | A working payment path: Razorpay account, or the manual invoice flow surfaced in the product | billing, UI | P1 |
| COM-4 | Teams and roles: viewer, operator, owner | auth, DB (DB-4), UI | P1 |

## O. Documentation

| ID | Change | Layers | Prio |
|---|---|---|---|
| DOC-1 | Help content for every control, written once and reused by the UI and the Copilot | docs, UI | P1 |
| DOC-2 | Archive the finished `deploy/PLAN-*.md` files and keep three living documents | repo | P2 |

---

# Part 2 — Each change in detail

Format for every item: **Where it lives today · What happens today · Why it matters · Idea for a solution · Depends on.**

---

## A. UI changes

### UI-1 Call quality panel on the call page
- **Where it lives today.** `frontend/src/pages/IntelligenceCall.tsx` renders summary, scores, transcript, signals, commitments. The data it needs already exists in the `call_turns` table (filled by `conversation_call_bridge.py:1763-1812` at the end of each call) but no endpoint exposes it and no component reads it (`api/intel.ts:132` types the transcript as timestamp, speaker, text only).
- **What happens today.** When an owner says "the agent was slow" or "it cut me off", nobody can see from the product where the time went or what the listener decided. A developer greps `ws_audio_receiver.log` for `[LATENCY]`, `[ENDPOINT]`, `[VAD-STACK]`, `[BARGE-IN]`, `[ECHO-GATE]` lines.
- **Why it matters.** Latency is the most felt defect, p50 3.4 s this week, and every complaint costs a developer session. The table exists; the page does not.
- **Idea.** A panel per call with one row per turn: caller text, time the listener took to close the turn, STT time, model first-chunk time, time to first audio, STT confidence, whether a filler played, whether the caller interrupted, and after VAD-3 the endpoint profile and the hold applied. Colour the slow part of each turn. Clicking a row plays that turn (UI-2).
- **Depends on.** API-2, DB-1 for the VAD columns, LAT-6 for the breakdown.

### UI-2 Audio player synced to the transcript
- **Where.** `components/AudioPlayer.tsx` is a plain play, pause and seek bar in the call page header. Transcript turns are rendered as text with timestamps.
- **What happens today.** Listening to a specific turn means dragging the seek bar by hand while reading timestamps; the current turn is not highlighted.
- **Why.** The owner reviews calls by ear; the tester results (TEST-1, TEST-3) and the quality panel (UI-1) all point at a turn.
- **Idea.** The player publishes its position; the transcript highlights the current turn and a click on a turn seeks the player. The two-party recording and the transcript already share a time base.
- **Depends on.** None.

### UI-3 Live page: real stages, latency, waiting timer
- **Where.** `pages/Live.tsx:27` hard-codes `['Greeting','Discovery','Match','Schedule','Confirm','Close']`. The live state file carries stage, language, speaking flag and transcript tail (`ws_audio_receiver.py:944-960`), no latency.
- **What happens today.** Every Copilot-built agent has its own stage names, so the strip shows six unlit segments and one badge. There is no indication of how long the caller has been waiting for a reply.
- **Why.** The Live page is the owner's only window into a call in progress; today it cannot tell a healthy call from a stalled one.
- **Idea.** The runtime publishes the agent's ordered stage list and labels (ENG-3) and the last turn's first-audio time; the strip renders from that list; a timer shows seconds since the caller stopped speaking while the agent has not yet answered.
- **Depends on.** ENG-3, API-3.

### UI-4 One Test workspace per agent
- **Where.** Testing is split across three places: "Chat with agent" on the agent card (`AgentsList.tsx:289-293`), the "Try" chip in the Copilot (`CopilotChat.tsx:249-253`), and the Test call button on the Telephony page (`Telephony.tsx:676-684`).
- **What happens today.** Three entry points, three different results, no history. The owner cannot see yesterday's test next to today's.
- **Why.** The whole point of the tester is to replace manual calls with a repeatable check; that needs one place with memory.
- **Idea.** A "Test" action on each agent card opening a workspace with four tabs: Chat (today's Try agent), Script run (TEST-1), Audio simulation (TEST-2), Results (history of runs, scorecards, compare across revisions). The Copilot chip and the Telephony button open the same workspace on the right tab.
- **Depends on.** TEST-1, TEST-2, API-1.

### UI-5 Test call result screen with feedback marks
- **Where.** `Telephony.tsx:224-227` shows a toast with the server's message and nothing more. The call itself lands in Activity and Intelligence like any other call, unlabelled.
- **What happens today.** The owner hangs up, remembers roughly what felt wrong, and describes it in words to a developer.
- **Why.** The test call is the only place the owner hears the real voice path; the result is lost the moment the call ends.
- **Idea.** When the test call ends, the page opens that call's quality panel (UI-1) and lets the owner mark turns: "cut me off", "waited too long", "talked over me", "too fast", "sounded harsh", "wrong answer", with a free-text box. Marks are stored against the turn (DB-2) and shown next to the setting that governs that behaviour (UI-6).
- **Depends on.** TEL-1, DB-2, UI-1.

### UI-6 "Call behaviour" tab in the agent editor
- **Where.** `pages/agents/EditAgent.tsx` tabs: Settings, Stages, Advanced YAML, Routing, Documents, Voice (read-only), History. Personality fields in `AgentFormSections.tsx:283-309`. The only clickable delivery control anywhere is the Copilot quick-controls sheet (`components/VoiceSpeedControls.tsx`) for voice and speed.
- **What happens today.** Speed, tone and listening behaviour cannot be adjusted from the editor; listening behaviour cannot be adjusted anywhere by a user.
- **Why.** This is the owner's stated problem: "when the agent is too fast, too harsh, too slow, or cuts me off, I have to ask a developer."
- **Idea.** One tab with four groups. Voice and delivery: voice picker with a preview line from this agent, speed per language with measured seconds, figures-in-English toggle. Tone: presets (ENG-1) plus the existing fields. Listening: the presets of VAD-2 and, under Advanced, the individual values of VAD-1, each with its cost in words ("adds about half a second to every reply"). Latency budget: filler timing, reply length cap, record-details-off-the-spoken-path, model effort, with expected first-audio time next to the measured one (LAT-3). The same groups appear in the Copilot sheet.
- **Depends on.** VAD-1, VAD-2, ENG-1, COP-1, LAT-3.

### UI-7 Expose per-agent settings that already exist but are YAML-only
- **Where.** These keys are read by the engine today and are valid per agent, but the editor has no control for them: `languages.tts_voices[lang].pace`, `llm_config.filler_timeout_s`, `filler_min_gap_turns`, `filler_max_per_call`, `max_response_length`, `max_tokens`, `record_details_on_main_call`, `personality.verbosity_max_sentences`, `style_variants`, `response_bank.opening_variants`, `outbound.opening_variants`, `call_flow.turn_taking.*`, `call_flow.connect.*`, `call_flow.termination.*`, `languages.tts_numbers_english`, `stt_enum_recheck`, `evidence_switch`, `auto_switch`.
- **What happens today.** The only way to change them is the Advanced YAML tab or asking the Copilot by name. The frontend sweep found none of these names anywhere in `frontend/src`.
- **Why.** These are exactly the settings that owners ask developers to change.
- **Idea.** Add them as ordinary form controls in the right section, with the engine's defaults shown as placeholders so the owner sees what "unset" means. This is a subset of UI-6 that needs no engine work.
- **Depends on.** None for the keys listed; UI-6 for layout.

### UI-8 Stop the Builder hard-coding engine values at creation
- **Where.** `frontend/src/api/agents.ts:455-463`: a new agent is created with `max_tokens: 130`, `timeout_s: 8.0`, `stream_timeout_s: 10.0`, `filler_timeout_s: 1.5`, `intent_classify_timeout_s: 1.0`, `max_response_length: 160`. On save the editor preserves existing values (`:736-744`), so this is a create-time problem.
- **What happens today.** The engine's schema default for `max_response_length` is 400 (`schema.py:1101`); the Builder writes 160. A Builder-created agent therefore gets replies cut shorter than a Copilot-created one, and nobody chose that.
- **Why.** Two paths produce two different agents from the same intent; the owner cannot see why one sounds clipped.
- **Idea.** The Builder asks the server for the current platform defaults (API-4) and writes nothing it was not asked to set.
- **Depends on.** API-4.

### UI-9 Agent quality trend on the agent card
- **Where.** `AgentsList.tsx` shows name, direction, number binding, revision.
- **What happens today.** After an edit nobody knows whether the agent got better or worse until the next real call goes wrong.
- **Why.** Revisions pile up (Vedanta reached revision 11 in a week); the only comparison is a developer's calltester run.
- **Idea.** A small score per revision from the tester runs (TEST-1) and from real-call quality (INT-2), drawn as a sparkline on the card with the last change that moved it.
- **Depends on.** DB-3, TEST-4, INT-2.

### UI-10 In-product help
- **Where.** `layout/Topbar.tsx:304-323`: a help popover with three static lines. `components/ui/tooltip.tsx` exists and is imported nowhere. No tour, no docs centre, no "what changed".
- **What happens today.** Settings like "decline threshold" or "ask-gated slot" have no explanation where they are edited; the owner learns their meaning from a developer or from RUNBOOK sections written for developers.
- **Why.** Self-service is impossible without self-explanation. This is the cheapest part of the whole independence goal.
- **Idea.** One source of help text per control (DOC-1), rendered as a real tooltip and an inline "what is this" link; a first-login tour that follows the existing "Get set up" checklist; a "what changed" page fed from a short, owner-facing changelog.
- **Depends on.** DOC-1.

### UI-11 UI strings in Hindi and Gujarati
- **Where.** No i18n library; every string is hard-coded English. `Intl` is used only for number and date formatting (`lib/format.ts`).
- **What happens today.** The callers speak Hindi and Gujarati; the operator who reads the dashboard often does too, and gets English.
- **Idea.** String tables for the daily pages first: Overview, Live, Activity, Follow-ups, Outbound, Contacts, Test workspace.
- **Depends on.** None.

### UI-12 Notification preferences
- **Where.** The bell in the topbar (`Topbar.tsx:249-302`) is the only channel; `Settings.tsx` has name, phone and company only.
- **What happens today.** An owner cannot choose which events reach them or where.
- **Idea.** A preferences page listing event types (hot lead, follow-up due, campaign finished, agent regression, vendor degradation) against channels (bell, email, WhatsApp) once INT-3 exists.
- **Depends on.** INT-3.

### UI-13 Minute top-up control
- **Where.** `pages/Billing.tsx:274-276` states that no top-up is offered; `api/billing.ts:218` already has `startTopup` calling `POST /billing/topup` and nothing uses it.
- **What happens today.** A tenant who runs out of minutes has no button to press.
- **Idea.** Surface the existing call behind a quantity picker once COM-2 decides the price and the payment path.
- **Depends on.** COM-2, COM-3.

### UI-14 Actions on a live call
- **Where.** `pages/Live.tsx`: read-only cards and a full-screen transcript.
- **What happens today.** The owner can watch a call go wrong and cannot stop it, join it or hear it.
- **Idea.** An "End call" action first; listen-in and whisper after TEL-2.
- **Depends on.** TEL-2.

### UI-15 Unify the test entry points
- Covered by UI-4; listed separately because it is also a removal: the three current buttons become one action with the same label everywhere.

### UI-16 Owner feedback on a finished call
- **Where.** The call page has action items, commitments and recommendations, but no way for the owner to say "this reply was wrong" or "that is not our price".
- **What happens today.** Feedback reaches the developer in chat, who then edits the agent.
- **Idea.** Per-turn marks and a comment (the same table as UI-5, DB-2) and a button that opens the Copilot with the turn, the agent's reply and the comment attached, so the Copilot proposes a change card (COP-3).
- **Depends on.** DB-2, COP-3.

### UI-17 Real tooltips
- **Where.** Hints are `title=` attributes; the shadcn tooltip component is present and unused.
- **Idea.** Use the component everywhere a hint exists; feed it from DOC-1.

### UI-18 Quick-jump search
- **Where.** `Topbar.tsx:95-107` searches page names only.
- **Idea.** Search agents, contacts and recent calls too, using the existing list endpoints.

### UI-19 Copilot build state on re-open
- **Where.** The interview rail and spec preview are populated only from a `POST /copilot/chat` reply; `GET /copilot/sessions/{sid}` returns the transcript alone (`CopilotChat.tsx:713, 790`; `agent_copilot_api.py:7616`).
- **What happens today.** Re-opening a half-finished build shows the thread with no progress rail and no preview until the next message is sent.
- **Idea.** Return the rail payload with the session.

---

## B. VAD and listening changes

### VAD-1 Make the listening knobs per-agent data
- **Where.** About 135 settings in `pipeline.py`, `vad_stack.py`, `endpointer.py`, `stt/router.py`, `tts_sarvam.py` are read once at import into module-level constants. The ones that decide how the agent listens: `FSMN_MAX_END_SIL_MS` 280, `EP_FAST/DEFAULT/HOLD_RESIDUAL_FRAMES` 2/7/26, `MIN_REAL_SPEECH_MS` 200, `MIN_SPEECH_SNR` 1.2 and the `SNR_*` family, `TURN_CONTINUATION_HOLD_MS` 900, `TURN_NARRATIVE_SPEECH_MS` 2500, `TURN_MAX_MERGED_FRAGMENTS` 4, `TRANSCRIPT_MERGE_WINDOW_MS` 550, `TRANSCRIPT_MERGE_SKIP_WHEN_IDLE`, `MERGE_*`, `STALE_FRAGMENT_MAX_MS` 3000, `CONTINUATION_*`, `TTS_ECHO_COOLDOWN_MS` 400, `ECHO_*`, `SHADOW_*`, `BARGE_GUARD_ALL_REPLIES`, `BACKCHANNEL_MIN_REMAINING_S`, `TAIL_CUT_MIN_REMAINING_S`, `SILENCE_REPROMPT_S` 6, `SILENCE_REPROMPT_MAX` 2, `SILENCE_HANGUP_GRACE_S` 10, `DEAD_MEDIA_S` 5. Only five turn-taking keys (`pause_hold_ms`, `max_merged_fragments`, `backchannel_guard`, `sustained_barge_ms`, `tail_barge_in`), three connect keys and the termination keys are read per agent (`pipeline.py:3022-3100`).
- **What happens today.** Every agent on the platform listens the same way. A scripted agent that must never answer half a sentence and a quick-fire survey agent share one set of numbers. Changing any of them means editing `.env` or compose and restarting the runtime, which cannot be done during a live call. A recruiter's chatty callers and a real-estate agent's hesitant callers cannot both be right.
- **Why it matters.** This is the root of the dependency on developers for VAD complaints. No UI control is possible until the engine can read these values per call.
- **Idea.** A per-call listening configuration object resolved at call start from platform defaults, then the env (kept as the operator's override), then the agent YAML. The pipeline reads from that object instead of module globals. Every key is registered in `controls.py` so the Copilot and the Builder share the same vocabulary, and a change applies on the next call with no restart. The env values remain the defaults, so an agent with nothing set behaves exactly as today.
- **Depends on.** None. Everything in UI-6, VAD-2, VAD-5, VAD-7 depends on this.

### VAD-2 Named listening presets
- **Where.** New. Today an owner who wants "let me finish my sentence" gets a developer to set `pause_hold_ms` on that agent (done for Vedanta, §78).
- **What happens today.** Each VAD-1 value is meaningful to an engineer and meaningless to an owner.
- **Idea.** Three bundles. Patient: longer end-of-speech wait, every pause held, back-channel guard on, barge-in less sensitive, more re-prompt patience; costs roughly half a second per reply. Balanced: today's defaults. Snappy: shorter wait, no forced hold, barge-in more sensitive; risks answering half a sentence. The preset writes the underlying VAD-1 values and the Advanced section shows them, so an owner can start from a preset and nudge one value.
- **Depends on.** VAD-1.

### VAD-3 Record what the listener decided on every turn
- **Where.** `pipeline.py:4398-4422` builds a per-turn trace with `session_state` and `endpoint_profile`, but the insert in `conversation_call_bridge.py:1790-1808` drops them. Discard reasons (`[TURN] Discarded … reason`), echo-gate decisions, merge counts and deferred barge-ins are only log lines.
- **What happens today.** The question "why did it cut me off" cannot be answered from the database.
- **Why.** UI-1 and UI-5 need these facts per turn; the owner's feedback mark needs something to attach to.
- **Idea.** Persist, per turn: endpoint profile, session state, hold applied in ms, number of merged fragments, whether a segment was discarded and why, echo-gate verdict, barge-in attempted, deferred or executed, and TTS first-chunk time. Add columns in DB-1; the trace already carries most of it.
- **Depends on.** DB-1.

### VAD-4 Inert compose override
- **Where.** `deploy/docker-compose.services.yml:105` sets `HARD_FLUSH_SILENCE_FRAMES: "20"`. With `VAD_ENGINE=fsmn` the flush decision belongs to the endpointer; this value is used only on the legacy energy path (`pipeline.py:1700-1732`).
- **What happens today.** The comment next to it says it saves 160 ms per turn; it does not, on the engine that runs.
- **Idea.** Remove it, or map the intent onto `FSMN_MAX_END_SIL_MS` and the endpointer profiles so the comment becomes true.

### VAD-5 Barge-in sensitivity per agent
- **Where.** `BARGE_IN_MIN_FRAMES` 5, `BARGE_IN_MIN_ELAPSED_MS` 250 and the ×2 energy threshold are constants in `pipeline.py:259-271, 1377-1399`; echo cooldown and shadow factors are env.
- **What happens today.** A caller on a speakerphone in a car trips interruptions on an agent whose owner would rather it finished its sentence; a fast caller on a quiet line waits for an agent that will not yield.
- **Idea.** One per-agent "interruption sensitivity" control mapping to the frame count, the minimum elapsed time and the energy multiplier, through VAD-1.
- **Depends on.** VAD-1.

### VAD-6 Quiet caller, noisy line
- **Where.** Energy floors 260 and 500 are hard-coded (`pipeline.py:827-828`, `vad_stack.py:101-102`); FSMN's own thresholds are in the vendored `data/models/fsmn_vad/config.yaml`; the SNR gate (`MIN_SPEECH_SNR` 1.2, §29) can declare a soft voice as noise.
- **What happens today.** A very quiet caller or a very noisy line is handled by whatever the global numbers allow. §29 was an incident of exactly this kind.
- **Idea.** A per-agent, and ideally per-call-detected, "quiet caller" option that lowers the floors and relaxes the SNR gate, plus a diagnostic in UI-1 that says how many segments were discarded as noise on this call.
- **Depends on.** VAD-1, VAD-3.

### VAD-7 Silence re-prompt per agent
- **Where.** `SILENCE_REPROMPT_S` 6, `SILENCE_REPROMPT_MAX` 2, `SILENCE_HANGUP_GRACE_S` 10 are env (`pipeline.py:722-737`); `termination.silence_hangup` is the only per-agent piece.
- **What happens today.** Every agent says "I couldn't hear you" after the same six seconds, twice. A survey agent and a sales agent want different patience, and owners ask for this directly.
- **Idea.** Expose seconds, count and the hang-up grace as per-agent values in the Listening group.
- **Depends on.** VAD-1.

### VAD-8 Audio-level simulator in the product
- Described under TEST-2. Listed here because it is the only way to test a listening change without a phone.

---

## C. Latency changes

### LAT-1 Take `record_customer_details` off the spoken request by default
- **Where.** `llm_config.record_details_on_main_call` defaults to `True` (`schema.py:828-842`). With tools "on" the model answers most turns with the tool call and no words, so the engine asks again for the sentence. Measured 2026-09-15/16: the second request ran on 77% of turns at a median 1,223 ms, inside a median wait of 3,030 ms (§51.1). `False` moves detail capture to the background extractor, which costs the caller nothing; action tools stay.
- **What happens today.** The faster setting exists and is opt-in per agent; most agents still carry the default.
- **Why.** This is the largest single latency saving available with no engine work, on the order of a second on three turns out of four.
- **Idea.** Make `False` the default for new agents in the schema, the Builder and the Copilot; offer a one-click switch for existing agents in the Latency budget group; keep `True` available for agents whose slot capture was measured to need it.
- **Depends on.** None.

### LAT-2 Model and reasoning effort per agent
- **Where.** `LLM_MODEL` in the env overrides `llm_config.model` for every agent (`conversation.py:2087`); `LLM_REASONING_EFFORT=minimal` is global; temperature 0.7 is hard-coded.
- **What happens today.** An agent that needs careful reasoning on a long scripted pitch and a two-question survey run the same model at the same effort.
- **Idea.** Let the agent's `llm_config` win when it names a model and an effort that the platform allows, with the env as the default and a kill switch; show the cost and latency difference in the Latency budget group.
- **Depends on.** None.

### LAT-3 Latency budget: expected versus measured
- **Where.** New. Measured numbers exist per turn in `call_turns`; expected numbers exist only in developers' heads.
- **What happens today.** The owner turns a knob and cannot see what it did.
- **Idea.** Per agent, show the measured p50 and p90 first-audio over the last N calls next to a simple expected figure derived from the settings in force (pause hold, filler timeout, record-details mode, voice). Each control in UI-6 shows its share.
- **Depends on.** API-2, DB-1.

### LAT-4 Filler policy per agent in the UI
- **Where.** `llm_config.filler_timeout_s` (floored by env `FILLER_TIMEOUT_FLOOR_S` 1.5), `filler_min_gap_turns`, `filler_max_per_call` are YAML-only; the Builder hard-codes 1.5 at creation (UI-8).
- **What happens today.** An owner who finds "one moment please" annoying, or who wants it sooner, cannot change it.
- **Idea.** Three controls in the Latency budget group with the platform floor shown.
- **Depends on.** UI-7.

### LAT-5 Measured seconds per voice and pace
- **Where.** §51.1 measured the same 135-character line at pace 1.0: neha 10.6 s, simran 7.0 s, shubh 5.9 s. The quick-controls sheet shows a pace number with no duration.
- **What happens today.** Swapping voices silently changes speed by up to 1.8×; the owner then "fixes" the pace and overshoots.
- **Idea.** The voice preview synthesises one fixed line from this agent at the chosen pace and shows the seconds; the picker lists each voice's measured speed at pace 1.0.
- **Depends on.** None; the TTS preview endpoint exists (`/api/tts/preview`).

### LAT-6 "Why was this slow" per turn
- **Where.** `call_turns` has `stt_latency_ms`, `speech_end_to_engine_ms`, `llm_first_chunk_ms`, `llm_total_ms`, `speech_end_to_first_audio_ms`; missing are the listener's close time (VAD end plus endpoint residual plus any hold) and the TTS first-chunk time.
- **Idea.** Store the two missing pieces (DB-1) and render a stacked bar per turn in UI-1: listening, transcription, model, speech synthesis.
- **Depends on.** DB-1, VAD-3.

---

## D. Conversation engine and agent model

### ENG-1 Tone as presets with an audible sample
- **Where.** `personality.tone`, `energy`, `style`, `verbosity_max_sentences`, `traits`, `custom_instructions`, `style_variants` and `languages.register_notes` are rendered into the prompt by `prompt_blocks.py:260-300` and `:1534-1557`. The Builder exposes tone as a free-text field, energy and style as selects. §48 and §51 show that tone problems were mostly text in `custom_instructions` that told the model to say "Got it" every turn.
- **What happens today.** "Crude, rough" is fixed by a developer rewriting prose; the owner cannot preview what a change sounds like before a call.
- **Idea.** Presets (warm consultant, brisk professional, patient explainer, formal) that set the fields together and are written with the §48 and §51 lessons baked in, never a list of acknowledgements, never a quotable example line. A "hear three sample replies" button runs three fixed caller lines through the tester and plays them in the agent's voice.
- **Depends on.** TEST-1 for the samples.

### ENG-2 Tool-mode parity for every simulation
- **Where.** `LLM_TOOLS_MODE=on` is set only in the runtime container's compose block. The dashboard container does not set it, so the Try-agent simulation runs each agent at its YAML default, `shadow`, in which the spoken request has no tools and a separate background request fills slots. `CLAUDE.md` records this gap.
- **What happens today.** A test in the dashboard can pass while the phone call fails, because the phone call runs a different request shape, with tools on the main call, than the test did.
- **Why.** A tester that does not run the production path proves nothing.
- **Idea.** Give the dashboard, or whatever process runs the tester, the same tool-mode setting as the runtime, and print the mode on every test result so a mismatch is visible.
- **Depends on.** None.

### ENG-3 Publish the agent's stage list into the live state
- **Where.** `ws_audio_receiver.py:944-960` writes stage as a string; the agent's ordered stage list with labels is only in the YAML.
- **Idea.** Include the list once per call in the live state so the Live page can draw the real strip (UI-3).

### ENG-4 Pricing and inventory with validity dates
- **Where.** `knowledge_base.catalog` entries are static text and numbers; `pricing_policy` governs disclosure; §82 and §76 involved stale or invented figures.
- **What happens today.** A price written in July is spoken in October with the same confidence.
- **Idea.** Optional "valid until" on a fact; past that date the agent treats it as unpublished and defers, and the Builder shows the fact as expired.
- **Depends on.** None.

### ENG-5 Platform-placed callbacks
- **Where.** A caller's "call me in ten minutes" closes the call and becomes a follow-up suggestion for a person; the platform never places it (§79).
- **Idea.** A relative callback becomes a scheduled call under the existing human-approval rules, with an auto-approve option per agent for short delays.
- **Depends on.** Scheduling and follow-up approval machinery, both present.

---

## E. Testing and simulation

### TEST-1 Agent Response Tester
- **Where it lives today.** The real engine can already be driven in text: `POST /api/copilot/try/{slug}/start` and `POST /api/copilot/try/{slug}` (`agent_copilot_api.py:7563-7700`) run `ConversationRouter.process_stream` and return reply, stage, slots, flags, bookings, model and revision. calltester, outside the repo, adds scripts with expectations, deterministic checks, an optional model judge, reports and run comparison.
- **What happens today.** The dashboard offers one free-text turn at a time with no script, no scoring, no history. Everything that makes a test repeatable lives in a developer-only tool.
- **Why it matters.** The owner's aim is to stop making manual test calls to judge whether an agent is set up correctly. That needs scripts, verdicts and history in the product.
- **What a test consists of.** A script: direction, contact name, language, and an ordered list of caller lines, each with optional expectations (expected stage, slots written, must contain, must not contain, should or should not hang up). A run: every line played through the real engine in order, paced against the model quota. A result per turn: the agent's reply, stage before and after, slots written, intent, whether a shortcut or the model answered, prompt bands raised, fillers, text-path time, hang-up. A verdict per run from TEST-7. A saved record so two runs of two revisions can be compared.
- **Idea.** Move calltester's logic into the repository (TEST-5) and expose it behind API-1; the UI is the Script run and Results tabs of UI-4. The engine runs exactly as a call does (ENG-2), openings are fixed for determinism with an opt-in variety switch, and runs are charged to the agent-building token budget rather than the live-call one.
- **Depends on.** ENG-2, API-1, DB-3, TEST-6, TEST-7.

### TEST-2 Audio simulation in the product
- **Where it lives today.** callsim (`/home/angelo/callsim`) starts the unmodified production receiver and bridge on sandbox ports 19001 and 19004, stands in for FreeSWITCH with a fake switch that streams caller audio in real-time 20 ms frames and "plays" the agent's WAVs on a virtual clock, and measures at the wire: response time, the pipeline's own first-audio, VAD end lag, endpoint lag, transcription similarity, interruption-to-stop time, double replies, lost turns. Scenario YAML supports say, silence, barge-in after N ms, a pause before speaking, and echo mixed back in. Output includes a stereo WAV of the call.
- **What happens today.** This is the only way to test listening behaviour without a phone, and only a developer can run it.
- **Why it matters.** This is the first half of the owner's testing problem, VAD and latency, and it already exists as a tool.
- **Idea.** An Audio simulation tab in UI-4 that picks a scenario preset (fast talker, slow thinker with pauses, interrupter, noisy line with echo, quiet caller, number dictation, hello loop), synthesises the caller's lines in a voice different from the agent's, runs the sandbox, and shows the per-turn metrics and the WAV. It spends Sarvam and model credits like a real call, needs one-at-a-time scheduling on this VM, and must restart its sandbox after an engine change.
- **Depends on.** TEST-5, API-1, a concurrency guard.

### TEST-3 Test call that reports back
- Covered by UI-5, TEL-1 and DB-2: the one real phone call the owner makes should end in a result, not a toast.

### TEST-4 Scheduled regression suites
- **Where.** New. The intel worker and the scheduling loop already run periodic jobs.
- **What happens today.** A regression is discovered by the next customer.
- **Idea.** Each agent can have a saved suite run nightly or on every revision save; a drop in the verdict raises a bell notification and, after INT-3, an email.
- **Depends on.** TEST-1, INT-3.

### TEST-5 Bring calltester and callsim into the repository
- **Where.** Both import the repo's modules from outside it and have no tests of their own; the repo's tests never load them.
- **Why.** Any engine refactor can silently break them, and the product cannot ship a tool that lives in a home directory.
- **Idea.** Move them under a `tools/` package with their own tests, keep the command-line entry points, and let API-1 import them.

### TEST-6 Script generation
- **Where.** calltester's `derive` already writes a script from a past call's caller lines with timestamps and the real call's stage and reply as reference.
- **Idea.** Three sources in the UI: derive from a past call (pick it from Activity), generate from the agent definition via the Copilot (COP-4), write by hand. Generated suites always include the cases that bit real calls: price asked when pricing policy says defer, wrong number, mid-call language switch, a declined visit, "are you an AI", an abusive caller, number dictation, silence.

### TEST-7 Judgement rubric and deterministic checks
- **Deterministic checks, from calltester, decide pass or fail.** Empty reply; an unresolved `{token}` spoken; a `[S1]` citation spoken; Sir, Ma'am, सर or मैडम with no gender on file; a reply over `max_response_length`; a line repeated verbatim; wrong script for the call's language; English from a Hindi-only agent; an engine exception; a figure spoken where the knowledge base publishes none; an expectation in the script not met.
- **Model judge, advisory only.** Per turn on the Copilot model: answers what the caller asked, on the right stage, grounded in the catalogue, follows the script where one exists, correct language and script, tone matches the persona, length appropriate. Scored 1 to 5 with one sentence of reason.
- **Why two layers.** The model can be wrong about wording; it cannot be allowed to decide a verdict that gates a change.

---

## F. Backend and API

### API-1 Test endpoints
- Scripts: create, list, update, delete per agent. Runs: start a script run or an audio simulation, poll status, fetch results, compare two runs. Feedback: write and read marks per turn. All tenant-scoped like `/api/agents`.

### API-2 Per-turn data for a call
- **Where.** `call_turns` rows exist; `GET /api/intel/calls/{key}` returns no turn metrics.
- **Idea.** `GET /api/intel/calls/{key}/turns` returning the table's columns plus DB-1's additions, owner-scoped.

### API-3 Live state with latency and stage list
- **Where.** `server.py:1766-1787` streams the file as written; the file lacks both.
- **Idea.** After ENG-3 the endpoint passes them through unchanged.

### API-4 Platform-defaults endpoint
- **Where.** Defaults live in `schema.py` dataclass fields and in the Builder's TypeScript, separately.
- **Idea.** One endpoint that serialises the schema defaults; the Builder and the Copilot read it instead of carrying their own copies.

### API-5 Routes mounted but refused for every tenant
- **Where.** Legacy `/api/followups*`, `/api/followup-automation*`, `/api/runtime/*`, `/api/sarvam/*`, `/api/logs/*`, `/api/minutes/quota`, `/api/agent/config` are mounted in `server.py` but match no entry in `auth._TENANT_API_PREFIXES`, so every session gets 403.
- **Idea.** Delete them or move the operator ones to the admin app.

### API-6 Per-call cost in the call record
- **Where.** Activity shows estimated STT and TTS cost per call; model cost is written by `credit_tracker.py` to files under `credit_log/`, not to a table.
- **Idea.** Write model tokens and cost per call into DB-5 and show the full cost per call and per agent.

---

## G. Database

### DB-1 `call_turns` additions
- Columns for: endpoint profile, session state, hold applied ms, merged fragment count, discard reason, echo-gate verdict, barge-in attempted, deferred or executed, TTS first-chunk ms. Migration `0032`, with a down file like every other.

### DB-2 Owner feedback marks per turn
- A table keyed by call and turn index with mark type, comment, author and time; read by UI-1, UI-5, UI-16 and COP-3.

### DB-3 Test scripts, runs, per-turn results
- Scripts per agent with version; runs with agent revision, tool mode, model, verdict, cost; one row per turn with the readout of TEST-1.

### DB-4 Tenant membership and roles
- A tenant becomes an account with members; each member has a role; every `owner` column keeps meaning the account. See COM-4.

### DB-5 Per-call cost ledger
- Model tokens and cost, STT seconds, TTS characters per call; today minutes are in `usage_ledger`, Copilot tokens in `copilot_token_usage`, call model cost in files.

---

## H. Copilot

### COP-1 The Copilot knows the new controls
- **Where.** `controls.py` is the allowlist of real settings for the Copilot, the Builder and the fleet lint; a key not in it is dropped by the schema loader and would otherwise bump a revision while changing nothing.
- **Idea.** Every key introduced by VAD-1, VAD-2, VAD-5, VAD-7, LAT-2 and ENG-1 is registered there, with the same one-line help text as DOC-1, so "make Ravi more patient" maps to the Patient preset.

### COP-2 "Fix with Copilot" from a failed test turn
- A failed or low-scored turn in TEST-1 opens a Copilot session carrying the caller line, the reply, the expectation, the stage and the prompt bands, so the Copilot proposes a change card rather than asking the owner to describe the problem again.

### COP-3 Owner feedback becomes a proposed change
- Same mechanism as COP-2, fed from UI-16's marks on a real call.

### COP-4 The Copilot generates a test suite
- From the agent's stages, slots, objection playbook, pricing policy and guardrails, the Copilot writes the TEST-6 suite in the agent's languages, which the owner can edit.

---

## I. Telephony and live calls

### TEL-1 Test call linked to the resulting call record
- **Where.** `server.py:5183-5260` originates the call and records the dial with label "Test call (you)"; the resulting `calls` row is not linked back to the button.
- **Idea.** Return the originate identifier, mark the call as a test in the record, and let the Telephony page follow it into UI-5 when it ends.

### TEL-2 Listen-in, whisper, end-call on a live call
- **Where.** `socket_server.py` controls the FreeSWITCH channel; the dashboard has no route for any live-call action.
- **What happens today.** The owner watches the transcript and cannot intervene.
- **Idea.** End-call as a guarded dashboard action first; listen-in through a FreeSWITCH eavesdrop leg to the owner's phone or browser; whisper later. Each is a real telephony feature with its own risk and should be its own decision.

### TEL-3 Transfer to a human with context
- **Where.** `guardrails.escalation.handoff_message` promises a callback; no live transfer exists.
- **Idea.** A bridge to a configured human number with a short spoken summary first, as an opt-in per agent.

---

## J. Intelligence and follow-through

### INT-1 Knowledge-gap console
- **Where.** §47 mined caller questions by hand to find what an agent was missing; every call's transcript is already split by speaker in the intelligence pipeline.
- **Idea.** A page per agent listing caller questions that got a deferral, a fallback or a hedge, grouped by topic, with a button to add the answer to the knowledge base through the Copilot.

### INT-2 Agent quality over time
- **Where.** The pipeline scores agent performance per call already (`agent performance` on the call page); nothing aggregates it by revision.
- **Idea.** Aggregate per agent and revision, join with TEST-1 verdicts, and show it on the card (UI-9).

### INT-3 Tenant notification channels
- **Where.** The bell is the only working channel; automation `email` needs a sendmail binary the workers image lacks; `send_sms`, `send_whatsapp`, `slack`, `teams` return not configured (`automation.py:417`).
- **Idea.** One email provider for the platform (IG-3) and WhatsApp to the owner through the account they already connected; preferences in UI-12.

### INT-4 Campaign analytics by script version and time of day
- **Where.** `calls` carries agent revision and start time; the dial funnel records outcomes; no page cuts by them.
- **Idea.** Add revision and hour to the Conversation analytics filters.

---

## K. Integrations

### IG-1 Real calendar booking
- **Where.** `calendar.type` allows `mock | webhook` in the schema (`schema.py:290`); only `mock_slots` minus the `bookings` table is implemented (`calendar_store.py:142-145`).
- **What happens today.** A booking is an internal record; the business copies it into its calendar by hand.
- **Idea.** Google Calendar and Outlook as calendar types with free-busy reads at call time and event creation on booking; the mock stays for agents without a calendar.

### IG-2 Outbound webhooks and a producer for the outbox
- **Where.** `/v1` is pull-only; `shared/outbox.py` can deliver and retry but nothing calls `enqueue()`.
- **Idea.** Signed webhooks for call finished, commitment recorded, follow-up due and consent changed, with a delivery log in Developers; the outbox becomes their queue.

### IG-3 Email delivery from the workers container
- **Where.** `automation.py:259-277` shells out to `sendmail`; `Dockerfile.app` installs none.
- **Idea.** One transactional email provider configured platform-wide, used by automations, INT-3 and TEST-4.

### IG-4 SMS, Slack, Teams
- Behind IG-3's pattern; each is a provider adapter for an action that already exists in name.

---

## L. Operations and reliability

### OPS-1 Commit the working tree
- **Where.** Last commit `061464c` on 2026-09-09; since then 227 modified and 159 untracked files, about 37,000 inserted lines, covering RUNBOOK §39 to §83, migrations 0019 to 0031, the public API, WhatsApp, the verified filter.
- **What happens today.** The production code exists only on this VM's disk and in nightly dumps that live on the same disk.
- **Idea.** Commit in topic-sized pieces matching the RUNBOOK sections, then a rule: a RUNBOOK section is not closed until its commit exists.

### OPS-2 Off-box backups and a restore drill
- **Where.** `deploy/pg_backup.sh` dumps nightly to `/home/angelo/backups/` on this VM, 14 kept; agent YAMLs are gitignored and exist on this disk plus Postgres revision snapshots in the same database.
- **What happens today.** A disk loss loses the database, its backups and every agent at once.
- **Idea.** Copy each dump and a tarball of `calling-agent/agents/` to the GCS bucket the VM already writes to; restore one dump onto a scratch database once and record the time it took.

### OPS-3 A second speech vendor on the live path
- **Where.** Sarvam is the only STT and TTS on calls; Whisper is a fallback for STT only; TTS has no fallback. Key rotation exists (§34).
- **Idea.** A second TTS provider behind the same interface, selected per agent or on degradation detected by the sentinel.

### OPS-4 Measure the concurrent-call ceiling
- **Where.** 4-core VM shared by FreeSWITCH, Postgres and six containers; the frame path runs every 20 ms per call; no measured ceiling.
- **Idea.** Run callsim with N parallel sandbox calls and record where first-audio degrades; publish the number in `docs/PLATFORM.md`.

### OPS-5 Recording sync runs twice
- **Where.** The workers' 15-minute tick and the host `lescall-recsync.timer` both run `recording_storage`.
- **Idea.** Keep one.

### OPS-6 Dead code and the core dump
- **Where.** `tts_sarvam_ws.py`, `tts_cartesia.py`, `stt_stream.py` have no importers; a 259 MB `core` file at the repo root is a memory image of a process that held API keys.
- **Idea.** Delete all four.

### OPS-7 Status page and incident notices
- **Where.** The sentinel emails the operator; tenants learn of a vendor outage from their own callers, as on 2026-08-03 (Sarvam degradation, §9 and the transform-platform mandate).
- **Idea.** A public status page fed by the sentinel's state file and a banner in the dashboard during degradation.

### OPS-8 Keep `docs/PLATFORM.md` current
- A process rule, not code: any session that changes topology, pages, tables or measured numbers updates the map in the same session.

---

## M. Security

### SEC-1 API on every interface
- **Where.** `server.py:6002` binds uvicorn to `0.0.0.0:8080` on host networking; nginx is meant to be the only front door.
- **What happens today.** If the cloud firewall allows 8080, the API is reachable without nginx's rate limits and headers. The login gate still applies.
- **Idea.** Bind to loopback and confirm the firewall rule.

### SEC-2 Download links never expire
- **Where.** `/api/recordings/dl/{token}` and `/api/exports/dl/{token}` check only the token (`server.py:2472-2490`).
- **Idea.** Expiry and rotation per token, with re-issue from the page.

### SEC-3 Trunk passwords in plain text
- **Where.** `trunks.password` (`schema.sql:43`), masked by the API.
- **Idea.** Encrypt at rest with the same Fernet key pattern WhatsApp tokens use.

### SEC-4 No session revocation
- **Where.** Stateless HMAC cookie, seven days (`auth.py:62-93, 233-265`); logout deletes the cookie only.
- **Idea.** A server-side session identifier so a stolen cookie can be invalidated.

### SEC-5 Open signup
- **Where.** `LESCALL_ALLOWED_EMAILS=*` in `calling-agent/.env`.
- **What happens today.** Any Google account becomes a free tenant with Copilot and a shared model quota, held only by the trial floor.
- **Idea.** A product decision first: invite-only, or open with COM-1's gate in force.

---

## N. Commercial and accounts

### COM-1 A minute gate for free accounts
- **Where.** Billing enforcement is off since 2026-08-01 (`entitlements.py:46-50`); the trial floor caps agents and Copilot usage but not call minutes (§44).
- **What happens today.** A free account can place calls without limit on shared voice credits.
- **Idea.** Add minutes to the trial floor, which already ignores the enforcement switch, so free accounts stop at 30 minutes without turning on platform-wide enforcement.

### COM-2 A way to buy minutes
- **Where.** `minute_topups` table and `POST /billing/topup` exist; no UI, no price, no payment path.
- **Idea.** A price per block, the existing endpoint, and COM-3's payment path.

### COM-3 A working payment path
- **Where.** The Razorpay client is complete but no account exists (§25); manual invoices and admin credit grants work but are operator-only.
- **Idea.** Either open the Razorpay account, or show the manual-invoice flow in the product so a tenant can request an invoice and see it paid.

### COM-4 Teams and roles
- **Where.** `is_admin()` always returns False; one email is one tenant; the only sharing is a contact grant and agent share links that import copies.
- **What happens today.** A business that wants a sales manager to review calls and an operator to approve follow-ups must share one Google login.
- **Idea.** Accounts with members and three roles, viewer, operator, owner; approvals, billing and agent edits scoped by role; the public API keys owned by the account.

---

## O. Documentation

### DOC-1 Help content per control
- **Where.** Explanations of settings exist in `schema.py` comments and RUNBOOK sections, written for developers.
- **Idea.** One short owner-facing sentence per control, stored once, rendered by the UI (UI-10, UI-17) and quoted by the Copilot (COP-1).

### DOC-2 Three living documents
- **Where.** `deploy/PLAN-*.md` are finished designs; `PROJECT_INSPECTION.md` and `dhron-voice-interface-report.md` are dated snapshots.
- **Idea.** Keep `CLAUDE.md`, `deploy/RUNBOOK.md` and `docs/PLATFORM.md` as the living set; move the rest under `docs/archive/` with their dates.

---

## How the owner's three requests map onto the index

- **"A tester that shoots questions at the agent and returns the answers for me to judge, replacing manual calls."** TEST-1, TEST-6, TEST-7, UI-4, API-1, DB-3, ENG-2, COP-2, COP-4.
- **"The VAD half of my testing."** TEST-2, VAD-3, DB-1, UI-1, UI-5, TEST-3, TEL-1, LAT-6.
- **"Options in the platform so users fix latency, speed, tone and VAD themselves."** VAD-1, VAD-2, VAD-5, VAD-7, UI-6, UI-7, UI-8, LAT-1, LAT-2, LAT-3, LAT-4, LAT-5, ENG-1, COP-1, DOC-1.
