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
- [ ] **Phase 2 — Dockerize:** Dockerfiles (agent-core, dashboard-api, web); FreeSWITCH
      image (host networking); docker-compose.yml; .dockerignore.
- [ ] **Phase 3 — Dashboard backend:** agent CRUD, pitch maker, campaigns/leads, transcripts,
      AI summaries (LLM over recordings), analytics endpoints.
- [ ] **Phase 4 — Dashboard frontend:** dialer, transcripts, summaries, analytics, agent
      builder, pitch maker, live call panel.
- [ ] **Phase 5 — Outbound engine:** dialer loop -> FS originate -> agent core; campaign-driven.
- [ ] **Phase 6 — Cutover (maintenance window):** `git mv` live telephony files into `shared/`,
      update start scripts, restart, verify a real call.

## Data model
`agents`, `campaigns`, `leads`, `calls` — see `shared/db/schema.sql`.
Default agent (Vishal Patel / Pramukh) seeded from the legacy hardcoded config;
`dashboard/outbound_contacts.json` imported into the "Legacy Outbound" campaign.

## Archived (dead code)
`conversation_orchestrator.py`, `sarvam_ws_probe.py`, `stt_stream_worker.py`,
`socket_server.py.bak.20260617`, `phase12-state/` → `archive/`.
