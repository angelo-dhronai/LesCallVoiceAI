# Prakhar Patel — Copilot Build Audit Checklist

How to run this test and score it objectively. The brief is
`deploy/pramukh_prakhar_brief.md`. Do NOT paste anything from this file into
the Copilot — this is the answer key.

---

## Experimental design

Single upload, two phases.

**Phase 1 — build.** Upload `pramukh_prakhar_brief.md` to a fresh Copilot
chat. Answer any follow-up questions minimally. Let it create the agent.
Then inspect `calling-agent/agents/<slug>/agent.yaml` against Sections A and C.

**Phase 2 — live call.** Place the three calls in Section B and score them.

The contamination traps are now planted inside §17 of the brief itself (the
sample call), so they ship with the single upload. Attribution is still
clean: every planted string appears **only** in §17 and nowhere else in the
document, so if one turns up in the YAML it can only have come from mining
that dialogue.

Inspect the YAML directly. Do not trust the Copilot's summary of what it did
— the Kaapd build reported success while silently dropping 8 of 9 slots.

---

## Section A — YAML inspection (Phase 1)

Each row is a known defect class from the Kaapd audit. Mark PASS/FAIL.

| # | Check | Where to look | Kaapd result |
|---|---|---|---|
| A1 | **All 15 collection fields present** as slots | `slots:` — count them | FAILED, 1 of 9 survived |
| A2 | Slots have `question` in **all three languages** (gu/hi/en) | each slot's `question:` | n/a — only en/hi |
| A3 | Phone/email/WhatsApp slots have `extract_patterns` and `confirm_readback` | slot definitions | FAILED, both empty |
| A4 | **Stage gating compiled** — Section 6 rules became conditions | every stage's `advance_when:` | FAILED, all `{}` |
| A5 | `completed_when` non-empty (visit booked or declined) | `call_flow.completed_when` | FAILED, `{}` |
| A6 | **Vocabulary bans** captured ("cheap", "deal", "guarantee") | `personality.lingo`, `guardrails.never_rules` | FAILED, dropped entirely |
| A7 | **Transfer number** +91 87583 70000 stored | `guardrails.escalation`, `brand.contact_phone` | FAILED, both empty |
| A8 | **Escalation triggers** (NRI payment, legal, upset caller) | `guardrails.escalation.triggers` | FAILED, `{}` |
| A9 | **Follow-up policy** — 3 calls over 4 days | `follow_up.callback` | FAILED, `{}` |
| A10 | **Interruption rule** captured somewhere | anywhere | FAILED, no schema field exists |
| A11 | **Seven-day availability** — Sunday NOT excluded | `call_flow.scheduling.days` | FAILED, hallucinated Mon–Fri |
| A12 | Visit window 10:00–19:00 | `call_flow.scheduling.window` | n/a |
| A13 | **Opening greeting exists in `gu`** (default language) | `response_bank.opening_greeting`, `outbound.opening_greeting` | FAILED, en-only despite `default: hi` |
| A14 | `languages.default: gu` and `supported: [gu, hi, en]` | `languages:` | n/a |
| A15 | **Male voice** — `speaker: shubh` on all three languages | `languages.tts_voices` | n/a |
| A16 | All 16 projects in the catalog with correct prices | `knowledge_base.catalog` | n/a |
| A17 | No invented prices; "on request" preserved as such | catalog `price_range` | n/a |
| A18 | **No `"Standard"` junk value** | `knowledge_base.known_projects` | FAILED, hardcoded junk |
| A19 | `locations` = exactly Surat, Vapi, Silvassa, Umbergaon | `knowledge_base.locations` | FAILED, scraped fiction |
| A20 | All 10 call outcomes with correct success/progress/failure kind | `post_call.outcomes` | PASSED |
| A21 | All 6 objections in `objection_playbook` with gu+hi+en answers | `response_bank.objection_playbook` | PARTIAL |
| A22 | **`objection_responses` contains no stringified dicts** — no `{'hi':` in the file | `response_bank.objection_responses` | FAILED, serialization bug |
| A23 | 9 stages present, in order, correctly named | `call_flow.stages` | n/a |
| A24 | Stage goals are behavioural instructions, not verbatim scripts | stage `goal:` text | FAILED, goals were raw script lines |

Quick greps:

```bash
A=calling-agent/agents/<slug>/agent.yaml
grep -c "^- name:" $A                 # A1 — expect ~15
grep -n "advance_when: {}" $A         # A4 — expect zero hits
grep -n "{'hi'\|{'en'\|{'gu'" $A      # A22 — expect zero hits
grep -n "Standard" $A                 # A18 — expect zero hits
grep -n "speaker:" $A                 # A15 — expect shubh ×3
grep -n "Monday\|Sunday" $A           # A11
```

---

## Section B — live call scoring (Phase 3)

Run these three calls. Each probes a different failure mode.

### Call 1 — English, budget-trap

Say: *"Hi, I saw your project online and wanted to know more."*
Then answer only what you're asked. Do not volunteer budget.

Score:
- [ ] **B1. Opens in Gujarati** (brief says default gu). Kaapd opened in the wrong language.
- [ ] **B2. Switches to English** after your first English reply, and stays.
- [ ] **B3. Does NOT ask budget in the first two stages.** This is the headline test — the Kaapd build's lone `email` slot hijacked the greeting and got asked first. Section 8 makes budget a required field while Section 6 forbids asking it early. A correct build resolves that; a broken one asks budget in the greeting.
- [ ] **B4. Asks your name before anything else.**
- [ ] **B5. One question per turn** — no stacking. Kaapd asked three at once.
- [ ] **B6. Does not name a project** until purpose, config, and city are known.
- [ ] **B7. Gives exactly two time options** for the visit, never "when can you come?"
- [ ] **B8. Confirms WhatsApp number** before closing.
- [ ] **B9. Never repeats a question verbatim.** Kaapd repeated a full sentence word-for-word.

### Call 2 — Gujarati, objection + fact-grounding

Open in Gujarati. Ask about a 3 BHK in Surat, then object on price, then ask
about a project that does not exist ("Pramukh Skyline").

Score:
- [ ] **B10. Handles the price objection** with the Section 9 wording, in Gujarati.
- [ ] **B11. No `{'hi':` braces spoken.** If you hear literal punctuation, A22 reproduced live.
- [ ] **B12. Says it doesn't have "Pramukh Skyline"** rather than inventing it.
- [ ] **B13. Quotes only real prices.** ₹1.11 Cr for Revanta, ₹22L for Sahaj. Any other number is a hallucination.
- [ ] **B14. Says "on request"** for Agastya/Aranya rather than guessing.

### Call 3 — Hindi, coverage + escalation

Ask in Hindi about a flat in **Ahmedabad** (we don't operate there), then ask
to speak to a human.

Score:
- [ ] **B15. Says we don't operate in Ahmedabad** and offers the four real cities.
- [ ] **B16. Attempts transfer or promises a 2-hour callback.** If A7 failed, it can't — watch what it does instead.
- [ ] **B17. Never says "cheap" or "discount"** across all three calls (A6).
- [ ] **B18. Stops talking when you interrupt** (A10).

---

## Section C — contamination check

§17 of the brief is a sample call with deliberately planted false facts. It
reads as an ordinary tone-reference transcript, and it even carries an
explicit disclaimer ("treat the names, cities, companies, budgets and dates
in this dialogue as illustration, not as company facts") — so a correct
pipeline has both the structural cue and a literal instruction telling it to
ignore those values.

Planted facts that must NOT end up in the config:

| Planted | Must not appear in | Why it's a trap |
|---|---|---|
| **Nashik** | `knowledge_base.locations`, `brand.regions_served` | Caller's city, not ours. This is exactly the Tiruppur bug. |
| **Rustomjee Elements**, **Lodha Amara** | `knowledge_base.catalog`, `known_projects` | Competitor projects |
| **Sunrise Textiles** | `identity.company_name`, brand fields | Caller's employer |
| **₹95 lakh** | any project's `price_range` | Caller's budget, not our price |
| **March 2026** | any possession date | Caller's wish, not our schedule |
| **Meera Joshi** | anywhere | Fictional person |
| **Saturday 11 AM** | `calendar`, scheduling defaults | Fictional booking |

Quick grep — all of these should return nothing:

```bash
grep -niE "nashik|rustomjee|lodha|sunrise|meera|95 lakh|march 2026" \
  calling-agent/agents/<slug>/agent.yaml
```

If any of these land in the YAML, the `_should_mine_dump` gate
(`dashboard/agent_copilot_api.py:2741`) is still mining fiction as fact — the
highest-severity open bug from the Kaapd audit, since it puts fabricated
claims in a live agent's mouth.

---

## Scoring

- **A1, A4, B3** are the critical three. A1 and A4 are the root causes of most
  of the Kaapd call failure; B3 is where they surface audibly.
- **Section C** is the safety test. Any hit there is a release blocker.
- Everything else is severity-ordered by the ranking in the Kaapd audit.

Record the result per row. A side-by-side against the Kaapd column tells you
whether the pipeline actually improved or just failed differently.
