# Change record — 2026-07-22

Three problems were reported in sequence; each turned out to have a different
root cause than the symptom suggested. All changes below are **deployed and
verified**, and all are **uncommitted** on branch `CallExperienceTests`.

| # | Reported as | Actual root cause | Status |
|---|---|---|---|
| 1 | "replace the Sarvam key" | Whole key pool was dead (`all_exhausted: true`) | Live |
| 2 | "still can't hear the agent" | Playback dir unwritable after reboot — **not** the key | Fixed |
| 3 | "Copilot keeps failing on Prakhar" | Stale-proposal 409 dead-end + name ambiguity | Fixed |

---

## 1 · Sarvam API key replacement

**Finding beyond the request:** all 7 keys in the rotation pool were already
marked dead. The platform had no funded key at all, not merely a stale one.

The new key was validated against `api.sarvam.ai/text-to-speech` (HTTP 200,
real audio returned) *before* being wired in.

| File | Change |
|---|---|
| `calling-agent/.env:13` | `SARVAM_API_KEY` → `sk_9d9hx9jh_…jCuX` |
| `dashboard/sarvam_key_master.json` | New key prepended to the master list |

The 7 genuinely-dead keys were **left in the dead list** so rotation never
retries them.

### Propagation differs per surface — this is the part worth remembering

- **Live calls — no restart needed.** STT and TTS call
  `key_rotator.current_key()`, which re-reads `.env` on *every* request.
  Verified inside the container that it resolved to the new key.
- **Dashboard voice-preview — restart required.** `agent_builder_api.py:727`
  reads `os.environ`, populated by `load_dotenv` at boot, so it held the old
  key. `lescall-api` was restarted.
- `sarvam_key_watchdog` adopted the swap on its own (`all_exhausted` → `false`).

**Current state:** pool has recovered to 17 remaining, 7 dead (the master list
was expanded to 25 keys after this work).

---

## 2 · Silent calls — agent inaudible on connect

**This had nothing to do with the API key.** Every call failed here:

```
[TTS] [PLAYBACK_ERROR] Failed to write WAV file:
[Errno 13] Permission denied: '/tmp/ai_telephony_playback/raw_….wav'
```

### Mechanism

The runtime container writes TTS audio as a WAV into
`/tmp/ai_telephony_playback`; FreeSWITCH (on the **host**) plays that file. The
directory was `root:root 0755` while the container runs as uid 1000 → write
failed → no file → nothing to play. The directory was completely empty.

Caller audio still reached the agent (STT ran, transcripts recorded), which is
why the failure looked one-directional rather than broken.

### Why it happened *that day*

The box rebooted at **06:15:57**. `/tmp` is cleared on boot, and when a
bind-mount source is missing **Docker recreates it as `root:root`**. This
breaks on *every* reboot — it was never a one-off.

`/tmp/esl_recordings` had the identical problem, which is the cause of the
`Full-recording capture DID NOT land` warnings.

### Fixes

1. **Immediate** — `chown 1000:994` + `chmod 2775` on both directories.
   Verified both directions: container writes, `freeswitch` user reads.
2. **Durable** — new `/etc/tmpfiles.d/lescall.conf` so systemd recreates both
   with correct ownership at boot, *before* Docker starts:

   ```
   d /tmp/ai_telephony_playback 2775 1000 994 -
   d /tmp/esl_recordings        2775 1000 994 -
   ```

   Owner 1000 = container uid; group 994 = `freeswitch` so FreeSWITCH can read
   playback WAVs and the container can read FS `record_session` captures.
   setgid keeps new files in the freeswitch group.

### Separate bug found in the same log

`ws_audio_receiver.py:777` — `final_rms` was bound only inside an
`if log.isEnabledFor(DEBUG)` guard but read unconditionally by the
`[AUDIO_PIPE]` log call below it. **Logging evaluates its arguments eagerly
regardless of level**, so every 20 ms frame raised `UnboundLocalError` into
`[NORMALIZATION_FATAL]`.

It landed *after* `push_audio`, so it was not causing the silence — but it
spammed ERROR on every frame into a log already at 100 MB. Fixed by binding
`final_rms = -1` unconditionally. Zero occurrences since.

---

## 3 · Copilot reliability

Four defects in the Copilot tooling, plus one platform bug it had misdiagnosed.
These were fixed **in the machinery**, not by hand-editing the Prakhar agent.

### 3a · The 409 dead-end (most damaging)

A proposal froze a full snapshot of the agent plus the revision it was built
from. When *anything* wrote the agent in between, that revision went stale and
every apply failed the optimistic-concurrency check. **Nothing ever cleared or
refreshed it**, so the diff card sat there failing identically forever — the
user clicked Apply seven times.

The Copilot **caused it itself**: it proposed a change, then ran `update_stage`
on the same agent, moving the agent underneath its own pending proposal.

**Fix — proposals now rebase instead of dying.** New `_rebase_change()` replays
only the fields the proposal actually claims (`expected_paths`) onto the current
definition. Replaying *just those fields* is what makes it safe: an unrelated
concurrent edit is preserved rather than clobbered by the frozen snapshot.

- A genuine conflict (same field changed underneath) still refuses — but now
  **names the field** and **drops the dead card** so it can't be retried forever.
- Applied to **both** paths: the `apply_update` tool and the Apply-button
  endpoint `copilot_apply_change`.
- `propose_update` now stores a `base` snapshot to make the replay possible.
- Pre-upgrade proposals (no stored base) refuse to replay blindly.

### 3b · "More than one agent matches 'Prakhar'"

Those two are not different agents — they are **one agent in both call
directions** (`outbound_lead_qualifier-inbound` / `-outbound`). The resolver
raised a hard error, and the Copilot had no way to answer it.

Worse: `"Prakhar (outbound)"` previously resolved to **nothing**, so even the
obvious retry failed with "no such agent".

**Fix:** `_resolve_agent_slug` now extracts a direction hint —
`Prakhar (outbound)`, `outbound Prakhar`, `Prakhar outbound` — and settles
direction-only pairs automatically via `_direction_of` / `_direction_pair` /
`_pick_by_direction`. A direction embedded in a real slug
(`outbound_lead_qualifier`) is deliberately *not* treated as a hint.

### 3c · Two silent-corruption bugs found while reading that code

- **`change_id` collision.** Ids were `len(pending)+1` plus `hash(name)`.
  Discarding a change shrinks the dict, so the next proposal **reused an id a
  visible card still pointed at** — clicking its Apply would commit a different
  change than the one on screen. (`hash()` is also salted per process, so the
  suffix changed after any restart.) Now a monotonic `change_seq`, persisted
  across session reloads.
- **Discard was not idempotent.** Discarding an already-gone change raised
  `No pending change 'chg_1_2702'`. Now a no-op success, in both the tool and
  the endpoint.

### 3d · Behavioural rules added to the system prompt

- **6.a2** — resolve inbound/outbound pairs from context instead of asking;
  never repeat the same failing call.
- **6.e3** — pick one lane per agent per turn: never leave a proposal pending
  while directly editing the same agent (this is what caused 3a).
- **6.e4** — never retry a failed apply; re-propose from fresh state.
- **New section** — the platform's call-repair behaviour, so the Copilot
  diagnoses instead of guessing (see §4).

### 3e · Frontend

| File | Change |
|---|---|
| `frontend/src/api/client.ts` | Errors now carry `.status` so callers branch on the code instead of string-matching |
| `frontend/src/pages/CopilotChat.tsx` | On 409, clear the diff card (the server has already dropped it) and explain in plain words |

---

## 4 · The actual "hello" loop — a platform bug, not agent config

The reported symptom: *"the agent keeps repeating hello when I say hello."*

The platform's call-repair rule replays the last utterance when a caller says a
bare "hello" early, assuming they didn't hear it. When the greeting is **itself**
`"Hello?"`, that replays `"Hello?"` — agent says hello, caller says hello, agent
says hello, up to twice.

The rule's premise ("they missed something worth hearing again") is simply wrong
when the last line carried nothing.

**Fix** (`calling-agent/backend/app/voice/conversation.py`): extracted
`_is_bare_hello_text()` and added a guard — the replay is skipped when the last
agent response was itself a bare hello. A hello answering a hello means contact
is established, so the agent falls through to normal stage routing and **gets on
with its script**.

This is the behaviour that was asked for, and it now applies to **every agent**
without changing anyone's greeting. Notably, the Copilot's first instinct had
been to *delete* the `"Hello?"` greeting to dodge a rule it didn't know existed —
hence the prompt section in §3d.

Verified across scripts: `Hello?`, `हेलो`, `હેલો`, `haan hello` are treated as
bare; real greetings (`Hello, am I speaking with Rajesh?`) still replay
correctly.

---

## Verification

| Check | Result |
|---|---|
| Backend test suite | **336 passed** |
| Pre-existing failures | 2 failed + 10 errors — `UndefinedTable` / DB state in `test_intel_stages.py` + `test_precall_context.py`. Confirmed identical with changes stashed. |
| Frontend typecheck | `tsc --noEmit` exit 0 |
| Targeted tests written | resolver + direction pairs, rebase (incl. concurrent-edit preservation), bare-hello detection — all pass |

## Deployment

```bash
docker restart lescall-api        # copilot backend
docker restart lescall-runtime    # conversation.py + ws_audio_receiver.py
docker compose -f deploy/docker-compose.services.yml up -d --build spa
```

All healthy; `/login` HTTP 200, SPA HTTP 200, copilot module imports cleanly in
the live container with all new helpers present.

---

## Outstanding — not addressed

1. **GCS uploads are failing.** `RefreshError('Reauthentication is needed')` —
   recordings are not reaching `gs://`. Needs
   `gcloud auth application-default login`, or the durable fix in RUNBOOK §6b
   (attach a service account to the VM). Pre-existing and separate.
2. **Everything above is uncommitted** on `CallExperienceTests`, alongside a
   large amount of prior branch work. It is all live but a stray checkout would
   lose it.

---

## Scope note

This document covers the work done in **this session**. Other changes dated
2026-07-22 — the shadcn/UX redesign, intel search + analytics rebuild, chart
palette CVD work, test LLM stub, and the shared activation module — were made
outside this session and are recorded separately; they are **not** verified or
described here.
