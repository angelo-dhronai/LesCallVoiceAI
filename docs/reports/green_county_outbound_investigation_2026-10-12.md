# Pramukh Green County outbound (Dhaval) — investigation, 2026-10-12

Read-only investigation of `pramukh_green_county-2-outbound` (shubhangi@dhronai.com,
display name Dhaval) after the owner's test calls of 2026-10-11/12. Nothing in the
repo was changed by the investigation; probe scripts ran the real engine offline
against the live YAML with no LLM. Fixes are proposed, not applied.

## State at the time of the investigation

- Branch `Bettercallimprov`, 367 uncommitted paths (223 modified files,
  +34,808/−6,344 lines), 144 untracked, no stashes. Everything below is in that
  uncommitted working tree.
- Both containers bind-mount `/home/angelo/lescall`; `lescall-runtime` started
  2026-10-12T10:00:59Z, `lescall-api` 10:00:50Z. Every engine file hashed identical
  inside the container and on the host. Engine files written before the restart
  (conversation.py 09:59, prompt_blocks.py 09:57, catalog.py 09:35 …) are loaded
  and running — including the §76 "pending approval" F1 amenity clause and F4
  NOT BOOKED band. Written after the restart and therefore present but NOT
  running: `deploy/build_pramukh_projects.py` (10:17), `deploy/build_pramukh_aristo_test.py`
  and the two `pramukh_aristo_test-*` agent YAMLs (10:21). F2 (sheet) is not
  applied to the Green County agents.
- Agent: owner shubhangi@, `is_active=0`, revision 4 written 07:25 by the shared
  builder's `--mirror` pass; byte-identical to angelo's `pramukh_green_county-outbound`
  apart from `agent_id`/`revision`/`updated_*`. Source: `deploy/pramukh_vapi_projects.yaml`
  (Green County entry matches the YAML catalogue) + Ravi's stage skeleton in
  `deploy/build_pramukh_projects.py`. `primary_project = {id: pramukh-green-county,
  mode: exclusive, budget_switch: true}`; languages hi only, `stt_forced_language: hi-IN`,
  `evidence_switch: false`; `call_direction: outbound`; `memory: {}`;
  `follow_up.clarify_commitments.enabled: true`; `call_flow.callback_policy.enabled: true`.
- Prompt path: YAML → `AgentDefinition.from_yaml` → `ConversationRouter._build_system_prompt`
  (conversation.py:7799): static agent band (identity, custom_instructions, KNOWLEDGE
  BASE with ★ PRIMARY PROJECT first and the other six INFORMATION ONLY with prices,
  guardrails) → per-call band (`direction_block` with the authored outbound lines,
  `customer_context_block` = ━━ KNOWN CUSTOMER ━━, THIS CALL style) → per-turn band
  (CALL STATE/slots, NOW, CURRENT GOAL = stage goal, last-question guard, VISIT
  DECLINED, NOT BOOKED, MATCH RULES/PRIMARY PROJECT, BUDGET FIT, WHATSAPP). History
  = last 12 turns (`memory.MAX_SHORT_TERM_TURNS`). ~46.7k chars rendered offline.
- Calls to contact 1278 (+91 87678 65050, `gender=female`, owner shubhangi@):
  1047 (10-09 10:45, rev 1), 1060 (10-11 14:17, rev 2), 1061 (10-11 14:23, rev 2),
  1073 (10-12 09:45, rev 4). 1060/1061 ran the pre-§73 engine. All of 1060/1061/1073
  were labelled `out_of_budget` by the post-call classifier although no budget was
  said (not investigated further).

## Root causes

1. **Swarit came from the returning-customer memory, not from the catalogue or the
   primary pin (confirmed).** Call 1047: caller said ₹2.5 Cr → BUDGET FIT suggested
   Swarit → yes → pitch switched → visit booked for Swarit. The intel pipeline wrote
   preference "Pramukh Swarit bungalows (primary)" into `customer_memory` (per owner +
   contact, shared across every agent of that owner). Calls 1060/1061 loaded it
   (`[CONTEXT] known customer … digest=408ch`); the KNOWN CUSTOMER band read
   "Interested in: Pramukh Swarit bungalows…" while the stage goal said "they'd shown
   interest in one of our projects" → model: "आपने हमारे Pramukh Swarit bungalows में
   interest दिखाया था" (1060), "आप पहले Pramukh Swarit के बारे में देख रहे थे" (1061).
   The rendered MATCH RULES band names only Green County; the Swarit catalogue line is
   INFORMATION ONLY. Opt-out exists (`memory.use_customer_context: false`), unused.
   Files: `conversation_call_bridge.py:_fetch_customer_context` (512),
   `conversation.py:set_customer_context` (2406), `prompt_blocks.py:customer_context_block`
   (514), `shared/intelligence/memory.py:render_digest` (315).
2. **Devanagari callback requests are invisible to `_detect_callback_request`
   (confirmed).** `_CALLBACK_ASK_RE` (8542) is Latin-only; `_CALLBACK_TIME_RE` (8516)
   knows मिनट/घंटे but the second clause requires a Latin call/baat/phone; `_BUSY_NOW_RE`
   covers few Devanagari forms. Probe: "पांच मिनट में कॉल बैक कर सकते हैं", "बाद में कॉल
   कर सकते हो?", "कल ग्यारह बजे कॉल कर दीजिए" → False; English equivalents → True. The
   clarify gate (4549) and the objection branch's callback hook (4594) both depend on it,
   so the line fell to `_pick_objection_response` (8499) = the canned "ज़्यादा टाइम नहीं
   लूँगा… कब कॉल करूँ" or to the model, which kept qualifying (1073: family size asked
   twice after the request). `_parse_day_time`/`_parse_relative_offset` already read
   "कल" and "पांच मिनट". Regression risks: `_clarify_question`/`_clarify_confirmation`
   (9946–9995) and `_offset_phrase` speak ROMANIZED Hindi; `_parse_day_time` returns
   English day labels; no per-turn band tells the model a callback is pending
   (`extra_flags.callback_requested` is set but never rendered). Corpus: a wide
   Devanagari pattern hits 48/11,556 caller lines with false positives (DND, received
   call, "our team makes calls"); the request-shaped candidate hits 30 with ~3 false
   positives (past tense "कॉल की थी", narration).
3. **Duplicate intro = continuation-merge rollback of the good-time fast path
   (confirmed, call 1061).** 14:23:40.46 fast path spoke the authored good-time line
   (TTS 9.5 s); caller's "ठीक है?" at 41.5–42.3 s during playback; merge window →
   `[TURN-TXN] Rolled back provisional turn 'हां जी।' (stage restored to GREETING)`;
   merged "हां जी। ठीक है?" fails `_is_identity_yes` (1238, "?" → False); LLM ran at
   INTRO_AND_TIME_CHECK with the good-time line erased from history and `_good_time_spoken`
   restored to False (`turn_transaction.py:41`) → re-introduced. Precedent: closing turns
   call `commit_pending_turn()` so they are never rolled back (`_arm_callback_close` 9936,
   wrong-number close 4427); the good-time fast path (4912–4922) does not.
4. **"मैडम" is the contact's gender on file (confirmed).** `contacts.gender='female'` →
   `address.HONORIFIC_WORDS['hi']['female']='मैडम'` → `prompt_blocks._address_lines`:
   "use 'मैडम' on its own … for the WHOLE call" → 10 of 44 agent lines on 1060. No
   gender → "never any honorific, name + जी". No per-agent जी preference exists.
   Separate decision.
5. **Investor asked who will live there (1060)** — fixed by §73 action-only branches
   (`_maybe_branch` 5168 → `state_manager.retire_slots` 1037); probe confirms
   `family_size` retired and "Family size: not applicable". Fires one turn late when
   purpose arrives via the async LLM extractor (1061 pattern).
6. **"अभी नहीं, अभी फीचर्स बताइए" (1060)** — today's engine records 'Maybe later'
   (`_detect_visit_decline` 8961; `_VISIT_ANSWER_FIRST_RE` 1180 needs "पहले") and arms
   "never invite again"; a details-request widening hits 3/11,556 corpus lines, all
   genuine. "कल ग्यारह बजे कॉल कर दीजिए" after an invitation matches `_VISIT_ALT_TIME_RE`
   (1133) and is read as visit-time negotiation.
7. **Unsupported claims / repetition.** "मैंने डिटेल्स भेज दी हैं" (1060): no send tool,
   nothing in the prompt forbids claiming a send is done (`share_tracker.band` 374–410).
   "4 BHK ठीक रहेगा?" ×3 (1061): "जी" to the agent's offered value never fills the slot
   (regex needs Latin "bhk"; LLM extractor filled it two turns later). Blue Stone
   volunteered for 2 BHK (1060): model behaviour against an explicit band; other
   projects' prices sit in the same prompt (F3, on hold).

## Decisions pending with the owner

- Keep `budget_switch` on Green County (it is the sanctioned route by which Swarit
  can be suggested)?
- मैडम: clear gender on the test contact, or an agent-level address style that maps
  Hindi/Gujarati honorifics to nothing so the name + जी wording applies.
- F3 (other projects' prices in the prompt) stays on hold.

Probe scripts: session scratchpad `probe_gc.py`, `probe_gc2.py`, `probe_gc3.py`
(read-only; `ConversationRouter` over the live YAML, no LLM).
