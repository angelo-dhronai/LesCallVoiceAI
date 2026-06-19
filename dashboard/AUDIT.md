# LesCall Dashboard — Audit (React rebuild: `dashboard/api/` + `dashboard/web/`)

> Scope: the **new** React/Vite SPA (`dashboard/web/`) + FastAPI backend (`dashboard/api/`) on
> branch `restructure-and-dashboard`. The legacy `dashboard/server.py` + `dashboard/static/`
> vanilla-JS console is **out of scope** here (it is the older, separate dashboard).
> Audit only — no code was changed except the creation of this file.
> Date: 2026-06-19.

---

## 1. Summary

Of the **9 sidebar views** (`Sidebar.jsx:8-18`): **3 are genuinely wired to real backends**
(Dashboard, Agent Setup, Call History/Detail), **2 are pure mock** rendering `mockData.js`
(Graph Agent, Knowledge Base), and **4 are ComingSoon stubs** (My Numbers, Batches, Reports,
Analytics — the `else` branch at `App.jsx:69-71`). Even the wired views carry **fabricated
presentational layers**: agent LLM/Audio/Engine/Tools/Analytics tabs are mostly non-persisted
defaults (`adapters.js:25-30`), the Call Detail **audio player is fully simulated** (there is **no
audio endpoint** in the backend — `calls.py` only serves list/transcript/summary/analytics), and
KPI sparklines/waveforms are synthetic. The backend is the healthier half: it is DB-backed
(SQLite/Postgres via `get_db()` with real commit/rollback + WAL), parameterizes all SQL, and never
imports the pipeline. But **~17 backend endpoints have no frontend consumer at all** — the entire
`trunks.py` surface (8), the entire `campaigns.py`/leads surface (8), and the live WebSocket (1) —
several of which are **privileged** (write `/etc/freeswitch`, reload FS, edit the Kamailio ACL,
originate real outbound calls). The single most impactful data finding: the pipeline writes
**no `owner` and no `direction`** into any of the 2,703 `recordings/*/metadata.json` files
(verified 0/2,703), so per-tenant visibility collapses to "admins-only" and every call renders as
"Inbound". In the current `.env` (Google OAuth + `SECRET_KEY` + `LESCALL_ADMINS` all set) auth is
enabled and the admin (`tech@dhronai.com`) does see all real calls — but the open-by-default path
is latent and one unset env var away.

---

## 2. Per-view audit

| View | Status | Data source | Key issue | file:line |
|---|---|---|---|---|
| **Dashboard** (Overview) | ✅ wired | `GET /api/analytics` + `GET /api/calls?limit=8` | Real KPIs/calls, but KPI sparklines are synthetic (`trend()`); shows 0 for non-admin tenants (owner unwritten). | `Overview.jsx:56`, `Overview.jsx:16` |
| **Agent Setup** (AgentConfigView) | ⚠️ partial | `GET/POST/PUT/DELETE /api/agents` | CRUD real, but only name/company/prompt/greeting/language/tts_speaker/is_active persist. LLM/Audio-rate/Engine/Call/Inbound are non-persisted defaults; **Tools tab hardcoded**, **Analytics tab "—"**, cost fabricated. | `adapters.js:35-46` (persisted set), `:25-30` (fabricated), `AgentConfigView.jsx:251`,`:268` |
| **Graph Agent** (FlowCanvas) | 🟡 mock | `mockData.js` `flowNodes` | No backend; node edits via InspectorFlyout are not persisted (the `agents.stages` column exists but is unused). | `FlowCanvas.jsx:4`, `schema.sql:33` |
| **Call History** (CallHistoryTable) | ✅ wired | `GET /api/calls?limit=200` | Real rows, client-side paginate/filter. Direction always "Inbound" (metadata has none); `cost` always null; status is a has-audio/transcript heuristic; Replay disabled. | `CallHistoryTable.jsx:35`, `adapters.js:55-57` |
| → **Call Detail** (CallDetailView) | ⚠️ partial | `GET /api/calls/{id}/transcript`, `POST /api/calls/{id}/summary` | Transcript + AI summary real. **AudioPlayer is simulated — no real audio** (no backend audio route); waveform fake; Download is a no-op; Notes/tags/Push-to-CRM are local-only. | `CallDetailView.jsx:27`, `AudioPlayer.jsx:6`, `NotesPanel.jsx:37`, `adapters.js:78` |
| **My Numbers** | ❌ stub | ComingSoon | Backend fully built (`/numbers`, `/trunks`, `/telephony/server-info`, `/trunks/{id}/apply`) but **no UI mounts it**. | `App.jsx:69-71`, `trunks.py` |
| **Knowledge Base** (KnowledgeBaseGrid) | 🟡 mock | `mockData.js` `knowledgeBaseDocs` | No backend (no `/knowledge` endpoint); upload modal is cosmetic ("Start indexing" just closes). | `KnowledgeBaseGrid.jsx:3,47`, `:27` |
| **Batches** | ❌ stub | ComingSoon | No backend, no UI. | `App.jsx:69-71` |
| **Reports** | ❌ stub | ComingSoon | No backend, no UI. | `App.jsx:69-71` |
| **Analytics** | ❌ stub | ComingSoon | Renders ComingSoon; the existing `GET /api/analytics` is consumed only by the Dashboard, not here. | `App.jsx:69-71`, `calls.py:128` |
| *(shell)* TopHeader: wallet / notifications / Add funds | 🟡 mock | `mockData.js` `wallet`,`notifications` | Wallet balance, unread badge, "Add funds" flow are all fixtures. | `TopHeader.jsx:6,66,96` |
| *(shell)* Sidebar workspaces | 🟡 mock | derived/local | Workspace switcher is cosmetic (derived from email, switching does nothing real). | `Sidebar.jsx:24-30` |

**Backend endpoints with no frontend consumer (~17):**
`trunks.py` — `/telephony/server-info`, `/trunks` (GET,POST), `/trunks/{id}/instructions`,
`/trunks/{id}/apply`, `/numbers` (GET,POST), `/numbers/{id}` (DELETE).
`campaigns.py` — `/campaigns` (GET,POST), `/campaigns/{id}/status`, `/campaigns/{id}/leads`
(GET,POST), `/campaigns/{id}/dial`, `/leads/{id}/dial`, `/leads/{id}/dispose`.
`live.py` — `/live/ws` (`liveSocket()` is defined in `api.js:56` but imported by nothing).
*(Consumed: all of `agents.py`; `calls.py` calls/transcript/summary/analytics; `auth.py` config/me/logout.)*

---

## 3. Backend risk pass (R1–R10, re-scoped to this stack)

| # | Risk | Status | Blast radius / detail | file:line |
|---|---|---|---|---|
| **R1** | Open-by-default control surface | ⚠️ latent (mitigated by current `.env`) | CORS `allow_origins=["*"]`; `SECRET_KEY` defaults to `"dev-insecure-change-me"`; auth **falls open to `default@local`** when `GOOGLE_CLIENT_ID/SECRET` are unset. In the live `.env` all three are set → `AUTH_ENABLED=True`, so today it is *not* open. **If creds were ever unset**, anyone reaching `:8080` becomes `default@local` and can call the privileged orphan endpoints below with zero auth. | `main.py:20-25,30`; `auth.py:32,81-82` |
| — | …privileged endpoints reachable when open | ⚠️ | `POST /trunks/{id}/apply` writes FS gateway+dialplan, reloads FS, **rewrites the Kamailio ACL**; `POST /campaigns/{id}/dial` & `POST /leads/{id}/dial` **originate real outbound calls** via `fs_cli`/ESL. | `trunks.py:75-95`; `campaigns.py:107-122`; `dialer.py:78-93` |
| **R2** | DB integrity & concurrency | ✅ largely resolved | Now DB-backed: `get_db()` commits on success / rolls back on error; SQLite WAL + 30s timeout; FK on. The old JSON read-modify-write corruption class is gone for operational data. Remaining JSON: `sarvam_keys.json` (flock + fsync, but **in-place truncate, not temp+rename**; corrupt JSON → `{}` could wipe under lock) and legacy `outbound_contacts.json` (seed-only, read-only). | `database.py:74-85`; `sarvam_keys.py:56-72,75-84` |
| **R3** | Injection / unsafe render | ✅ | React auto-escapes; **no `dangerouslySetInnerHTML`** anywhere. SQL column names in `agents.py`/`campaigns.py`/`trunks.py` come from **static Pydantic field names** (not user data); all values use `?` params; `calls.py:144` placeholders are count-derived with param'd values. | `agents.py:49-53`; `campaigns.py:66`; `calls.py:144` |
| **R4** | Side-effecting GET | ⚠️ low | `GET /api/auth/logout` clears the session (logout-via-`<img>`/prefetch possible; it's what `Sidebar.jsx:123` links to). `GET /api/auth/callback` writes the user row — standard OAuth, acceptable. No GET rotates Sarvam keys in this stack (rotation lives in `sarvam_rotator`/clients, not a dashboard GET). | `auth.py:130-134`,`107-116` |
| **R5** | Blocking I/O on event loop | ⚠️ low | Most heavy work is in **sync `def` endpoints** (`calls.py` list/transcript/summary, `trunks` apply) → FastAPI runs these in a threadpool, so the disk scan of 2,703 dirs and the OpenAI summary call don't block the loop. Real `async def` paths use truly-async subprocess (`dialer.py:80`). Flags: `live.py` tails with **sync `f.readline()`** inside `async def`; `run_campaign` opens sync `get_db()` inside `async def`. | `live.py:53-65`; `campaigns.py:108-113`; `dialer.py:98-105` |
| **R6** | Swallowed frontend errors | ⚠️ medium | `api.js req()` correctly throws on `!res.ok`, but read panels swallow it and render empty (looks like "no data", not "error"): `auth.jsx:29,31` bare `catch {}`; `Overview.jsx:59`; `CallHistoryTable.jsx:36`; `AgentConfigView.jsx:63`; `liveSocket` `onmessage` `catch {}` `api.js:59`. Mutations *do* surface errors via `alert()` (`AgentConfigView.jsx:97`, `NotesPanel.jsx:24`). No per-panel "stale/last-updated" indicator. | as listed |
| **R7** | Orphaned / half-wired data paths | ⚠️ high | `trunks`/`numbers`/`campaigns`/`leads`/`live` are fully built server-side with **no UI** (privileged + reachable if R1 opens). **Verified contract mismatch:** `callFromApi` reads `meta.owner`/`meta.direction`/`meta.duration_seconds`/`meta.start_time`, but the pipeline writes **only** `start_time`/`duration_seconds`/`language_detected` — `owner` & `direction` are **absent in 0/2,703 recordings**. Result: `_can_view()` is false for every recording for non-admins → tenants see nothing; direction is always "Inbound". | `adapters.js:49-63`; `calls.py:48-56`; `call_audit.py:159-166` (writer); pipeline passes `owner` only from `agent_config.owner` (`conversation_call_bridge.py:84-85`), which is `None` on the inbound path |
| **R8** | Live-feed staleness | ❌ not wired | `/api/live/ws` has **no consumer** — `liveSocket()` is dead code. Backend tries Redis pub/sub then falls back to tailing `LESCALL_LOG`, whose default path **`phase14_runtime/logs/latest/ws_audio_receiver.log` does not exist** in this checkout (assumed contract, unverified in container). No client reconnect/reseed exists because there's no client. | `api.js:56-61`; `live.py:21-24,45-66` |
| **R9** | Coupling to the pipeline | ⚠️ | Dashboard **never imports the pipeline** (confirmed — `calls.py` reads files only). Baked contracts: `recordings/<id>/{metadata.json,transcript.txt,caller_audio.wav}` — metadata present (2,688/2,703) **but missing owner/direction** (see R7); dialplan ext `outbound_ai_connect` + channel var `lescall_campaign_id` (`dialer.py:39,43`) — **assumed contract, unverified** (depends on `socket_server` honoring the var); runtime-log path (`live.py:21`) — **assumed contract, unverified** (file absent). | `calls.py:23,31-45`; `dialer.py:39-44`; `live.py:21` |
| **R10** | Hardcoded config | ⚠️ low | `OAUTH_REDIRECT_URI`/`PUBLIC_BASE_URL` default to `platform.lescall.dhronai.com` (overridden in `.env`); `SECRET_KEY` default (overridden); `KAMAILIO_IP="10.160.0.6"` hardcoded. Frontend fabrications shipped to prod: `DEFAULT_COST`, `model: 'gpt-4o-mini'`, `['English','Gujarati']` languages, `fakeWaveform`, plus all `mockData.js` fixtures (wallet, notifications, knowledgeBaseDocs, flowNodes, unused `callHistory`/`analyticsForCall`). | `auth.py:29-31`; `main.py:30`; `freeswitch_config.py:76`; `adapters.js:5,7,25,78` |

---

## 4. Prioritized fix list (by risk/impact)

> Tagged `[backend-only, zero UI risk]` vs `[touches frontend]`. Per the constraint, frontend
> work is limited to **wiring/logic/data** — no restyle, no new DOM structure, no markup changes —
> except where explicitly flagged in §5.

1. **Write `owner` + `direction` into recording metadata (and backfill admins).** *(R7/R9)* —
   highest impact: without it the whole tenant model and direction badge are dead, and every new
   call is mis-tagged. Fix in the pipeline writer (`call_audit.py:159-166`) so `owner` is always
   populated (resolve the inbound DID → agent → owner) and add `direction`.
   `[backend-only, zero UI risk]` (pipeline-side; dashboard already reads these fields).

2. **Decide + enforce the auth posture before exposing `:8080`.** *(R1)* — keep `AUTH_ENABLED`
   required (fail closed instead of fall-open to `default@local`), pin `CORS` to the real origin,
   and require a non-default `SECRET_KEY` at boot. The Docker stack already terminates TLS at the
   `web` container and the API uses `network_mode: host` on `0.0.0.0:8080` — confirm `:8080` is not
   directly reachable from outside (firewall / nginx-only). `[backend-only, zero UI risk]`.

3. **Build the audio endpoint and wire the player to it.** *(view: Call Detail)* — add
   `GET /api/calls/{id}/audio` (owner-checked, path-traversal-guarded, `caller_audio.wav` exists per
   `calls.py:80`) and point `AudioPlayer`/Download at it instead of the simulated playhead +
   `fakeWaveform`. `[touches frontend]` (data wiring; the player markup stays).

4. **Mount the already-built telephony + campaigns backends (My Numbers, and a campaigns surface).**
   *(R7; views: My Numbers + stubs)* — `trunks.py`/`campaigns.py` are complete and unused; this is
   the bulk of "features that aren't there yet." `[touches frontend]` (new views consuming existing
   endpoints).

5. **Surface fetch errors instead of rendering empty.** *(R6)* — route read panels through a
   visible error/"stale" state rather than `catch → []`. `[touches frontend]` (logic; needs the
   §5 decision on the indicator).

6. **Make the agent config tabs honest.** *(view: Agent Setup)* — either persist
   LLM/Audio-rate/Engine/Call settings (extend `AgentIn` + `agents` schema) or clearly mark them as
   defaults; populate the Analytics tab from real per-agent call data; stop showing a hardcoded
   Tools list. `[touches frontend + backend]` (data; no restyle).

7. **Replace Knowledge Base + Graph Agent mock data with real stores or honest empty states.**
   *(views: Knowledge Base, Graph Agent)* — both currently render `mockData.js`. Build a `/knowledge`
   store + a graph persistence path (`agents.stages`), or gate them behind a real "not configured"
   state. `[touches frontend + backend]`.

8. **Atomic-write `sarvam_keys.json` + don't `{}`-on-corrupt.** *(R2)* — temp-file + `os.replace`,
   and back up rather than silently emptying. `[backend-only, zero UI risk]`.

9. **Wire (or remove) the live feed.** *(R8)* — either consume `/api/live/ws` with reconnect+reseed
   or delete the dead `liveSocket()`; fix the default `LESCALL_LOG` path for the container.
   `[touches frontend]` if wired; `[backend-only]` if removed.

10. **Config hygiene.** *(R10)* — make `KAMAILIO_IP` env-driven; confirm prod `SECRET_KEY`/redirect
    are set (they are in `.env`); strip unused `mockData.js` fixtures from the prod bundle.
    `[backend-only]` + `[touches frontend]` (dead-data removal).

---

## 5. Correct fixes that would require a UI/markup change (need your sign-off)

These are the *right* fixes but they cannot be done as pure data wiring — they add or change DOM,
so per the "no new DOM structure / no restyle" rule I need explicit permission for each:

- **Error / "stale since" indicators (R6, fix #5):** showing a real error state or a "last updated"
  badge means adding elements that don't exist today. Without UI changes I can only log/console.
- **"Mock data" / "preview" ribbons** on Knowledge Base and Graph Agent (fix #7) if you want to keep
  them visible but honest rather than building their backends now.
- **New views for My Numbers + Campaigns (fix #4):** these replace ComingSoon stubs with real
  screens — necessarily new markup. (The sidebar nav items already exist.)
- **Audio player wiring (fix #3)** keeps the existing markup but swaps simulated playback for a real
  `<audio>` source; if you want a real decoded waveform instead of `fakeWaveform`, that's a visual
  change.
- **Agent config (fix #6):** marking tabs as "default (not saved)" or hiding the hardcoded Tools
  list changes what's rendered.

---

### Verification notes (assumed vs confirmed)

- **Confirmed against real files:** `recordings/*/metadata.json` shape (2,688/2,703 present; 0 with
  `owner`/`direction`); `.env` has `GOOGLE_CLIENT_ID/SECRET`, `SECRET_KEY`, `LESCALL_ADMINS`
  (`tech@dhronai.com,alwynantonyben@dhronai.com`) set; `credit_log/*.jsonl` exists but is **not read
  by this stack's API** (it was the legacy `server.py`'s source); no `dangerouslySetInnerHTML`; no
  audio endpoint in `calls.py`.
- **Assumed contract, unverified:** the `LESCALL_LOG` runtime-log path (`live.py:21`, file absent in
  this checkout); the `outbound_ai_connect` dialplan ext + `lescall_campaign_id` channel-var handoff
  (`dialer.py:39-44`); Redis pub/sub event shape (`live.py:33-39`).
