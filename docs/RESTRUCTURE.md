# lescall — Restructure & Dashboard Initiative

Tracking doc for the directory reorg, dockerization, and dashboard build.
Branch: `restructure-and-dashboard`.

## Decisions (locked)
- **Sequencing:** phased, reorg first. Each phase verified before the next.
- **FreeSWITCH:** containerized with `network_mode: host` (preserve live RTP/SIP + Plivo trunk).
- **Frontend:** React + Vite + Tailwind SPA, FastAPI as the API backend.
- **Agents:** DB-backed definitions (SQLite, Postgres-portable), editable in UI.

## Live-system constraint
`socket_server.py`, `ws_audio_receiver.py`, and FreeSWITCH run live on this host and
take real calls. **No live telephony file is moved or restarted outside a maintenance
window.** All new work is additive until the Phase 6 cutover.

## Target layout
```
lescall/
├── shared/          # agent-core (AI pipeline) + telephony bridge + db (DONE: db)
├── inbound/         # inbound orchestration + config
├── outbound/        # campaign dialer, lead loop, disposition tracking
├── dashboard/api/   # FastAPI: agents, campaigns, transcripts, summaries, analytics
├── dashboard/web/   # React + Vite + Tailwind SPA
├── freeswitch/etc/  # FS dialplan + SIP configs (DONE: pulled from host)
├── docker/          # Dockerfiles + docker-compose.yml
├── data/            # SQLite DB (gitignored)
├── docs/
└── archive/         # dead code (DONE)
```

## Phases
- [x] **Phase 1 — Foundation:** branch; archive dead code; scaffold dirs; pull FS configs;
      DB schema + data-access layer + seed (default agent + legacy leads imported).
- [x] **Phase 2 — Dockerize:** Dockerfiles (app, dashboard-api, web, freeswitch); compose with
      host networking + fs-container profile; .dockerignore; README. FS image flagged for
      mod_audio_stream verification before production use.
- [~] **Phase 3 — Dashboard backend:** DONE: agent CRUD + pitch fields. TODO: campaigns/leads
      endpoints, transcripts, AI summaries (LLM over recordings), analytics.
- [x] **Phase 3b — Plivo numbers & trunks (NEW):** add/register Plivo DIDs + SIP trunks in the
      dashboard; auto-generate FreeSWITCH gateway + dialplan XML from the `trunks`/`phone_numbers`
      tables and reload FS (`fs_cli -x "sofia profile external rescan"`); UI shows the exact SIP
      trunk config required (domain, IP auth vs REGISTER, allowed IP ranges).
- [x] **Phase 4 — Dashboard frontend:** React+Vite+Tailwind SPA (Overview/analytics, Agents+pitch
      maker, Outbound dialer, Calls+transcripts+AI summaries, Numbers/trunks, Live monitor).
      Builds clean; live WS verified.
- [x] **Phase 5 — Outbound engine:** `outbound/dialer.py` — DB-backed, reuses the proven fs_cli
      originate cmd, status/disposition writeback, concurrency + retry. API `/campaigns/{id}/dial`,
      `/leads/{id}/dial`. Dry-run verified end-to-end.
- [x] **Phase 5b — DB agents wired into the live pipeline:** `agent_config.load_agent()` resolves
      the per-call agent (inbound by DID via `phone_numbers.inbound_agent_id`, outbound by
      `campaigns.agent_id`), loaded from the DB and injected into `ConversationRouter`
      (identity/brand block, greeting, default language). Plumbing: `socket_server` forwards DID +
      `lescall_campaign_id` as WS path segments → `ws_audio_receiver` → `conversation_call_bridge`
      → `VoicePipeline` → `ConversationRouter`. Hard fallback to the legacy hardcoded persona when
      no DB/agent match; `DISABLE_DB_AGENTS=1` kill switch. 112 existing tests still pass; selection
      verified end-to-end. NOTE: takes effect on the next service restart (Phase 6 window).
- [ ] **Phase 6 — Cutover (maintenance window, your go-ahead):** `git mv` live telephony files into
      `shared/`, repoint start scripts, restart, verify a real call. NOT done — needs your window.

## Hosting
Dashboard is live at **https://platform.lescall.dhronai.com** (DNS A → 35.207.206.252).
- `deploy/lescall-dashboard-api.service` — FastAPI on 127.0.0.1:8080 (systemd, enabled, auto-restart).
- `deploy/nginx-platform.conf` — nginx vhost: serves `dashboard/web/dist` SPA + proxies `/api`
  (incl. WSS). New server block; the legacy `lescall` default_server is untouched.
- TLS via Let's Encrypt (certbot --nginx), HTTP→HTTPS redirect, auto-renew timer.
- `deploy/deploy_dashboard.sh <FQDN>` reproduces the whole setup.
- NOTE: ensure the cloud firewall allows inbound 443 (80 is already open).
- Frontend is served from the repo `dist/`; after a rebuild, nginx picks it up (no copy needed).

## Data model
`agents`, `campaigns`, `leads`, `calls` — see `shared/db/schema.sql`.
Default agent (Vishal Patel / Pramukh) seeded from the legacy hardcoded config;
`dashboard/outbound_contacts.json` imported into the "Legacy Outbound" campaign.

## Archived (dead code)
`conversation_orchestrator.py`, `sarvam_ws_probe.py`, `stt_stream_worker.py`,
`socket_server.py.bak.20260617`, `phase12-state/` → `archive/`.
