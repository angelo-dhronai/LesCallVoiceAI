# Multi-Tenant / BYON / Kamailio — Deploy Runbook

Companion to `docs/MULTITENANT_BYON_PLAN.md`. All code is in the repo; this is
the ordered list of **operational steps that were deliberately NOT executed
automatically** because they touch the live phone system. Steps are grouped by
blast radius. Nothing below is required for the system to keep working exactly
as it does today — every code path falls back to legacy behavior until these
steps are taken.

Live topology today (verified 2026-07-11):

- FreeSWITCH binds `10.160.0.8` — internal profile :5060, external profile
  :5080 (`vars.xml external_sip_port=5080`). Public IP `35.207.225.237` NATs to
  this host; Plivo origination therefore hits **:5080** directly.
- Inbound DID routing is the `plivo_inbound` extension **inline in
  `/etc/freeswitch/dialplan/public.xml`** with a `.*` catch-all, placed BEFORE
  the `public/*.xml` include — it shadows any per-DID file until removed.
- `/etc/freeswitch` is `freeswitch:freeswitch` mode 755 — the dashboard user
  (angelo, member of group `freeswitch`) cannot write it without step 2.
- Kamailio and Postgres are NOT installed.

---

## 0. Restart services to activate the new code (small window, ~10s)

The DID plumbing + per-call routing only take effect when the processes reload.
BOTH stacks are systemd-managed (`lescall-runtime.service` wraps
start_all/stop_all; `lescall-dashboard.service` Restart=always) — use
systemctl, NOT the raw scripts: launching via `start.sh --bg` while the unit
is enabled leaves systemd crash-looping against the occupied port (this
happened 2026-07-12; the fix was killing the manual process so the unit binds).

```bash
sudo systemctl restart lescall-runtime     # socket_server + ws_audio_receiver
sudo systemctl restart lescall-dashboard   # dashboard  [DONE 2026-07-12 — new code live]
```

Behavior after restart with the seeded DB: **identical to today** — the DID
+918065953160 is bound to the same slug `active_agent.json` points at, and
every failure path falls back to `active_agent.json`.

## 1. Phase 0 + 1 — database (NO downtime, already runnable)

```bash
cd /home/angelo/lescall
python3 -m shared.db.seed          # users, agents index, Plivo trunk, DID binding
python3 -m shared.db.import_json   # contacts / call analysis / follow-ups (files stay source of truth)
```

Both are idempotent; re-running changes nothing (seed's trunk/DID part is
bootstrap-only — it never resurrects operator-deleted rows). To undo entirely:
`rm shared/db/lescall.db*` (the code falls back to legacy resolution).

Notes: run `import_json` during a quiet period (its per-store transactions
briefly hold the sqlite write lock), and treat it as a **pre-go-live** tool —
once the bridge starts persisting calls rows, re-importing `call_analysis.json`
duplicates those calls (the report prints a warning when it detects this).

## 2. FS config write access for BYON "apply" (NO downtime)

The dashboard's `POST /api/trunks/{id}/apply` writes gateway + dialplan XML.
Give the dashboard user group-write on exactly the two generated-config dirs:

```bash
sudo chgrp freeswitch /etc/freeswitch/sip_profiles/external /etc/freeswitch/dialplan/public /etc/freeswitch/autoload_configs
sudo chmod g+w        /etc/freeswitch/sip_profiles/external /etc/freeswitch/dialplan/public /etc/freeswitch/autoload_configs
sudo chmod g+w        /etc/freeswitch/autoload_configs/acl.conf.xml   # Apply truncates it in place
```

Until this is done, "apply" returns a 500 with a hint pointing here.

**STATUS: DONE 2026-07-12** — verified end-to-end with a disposable trunk
through the live API: gateway XML + acl.conf.xml written, reloadxml/rescan/
reloadacl all +OK, stock `lan`/`domains` ACL lists preserved, test artifacts
removed afterward.

## 3. Per-DID dialplan cutover (SHORT window; rollback = one file edit)

Goal: replace the `.*` catch-all with generated per-DID extensions so a second
tenant's DID can route to a different agent.

1. `POST /api/trunks/{id}/apply` (or run `shared/freeswitch_config.apply_config`
   manually) → writes `dialplan/public/ai_inbound_8065953160.xml`.
2. Confirm the generated file's regex matches the DID forms you see in logs.
3. Edit `/etc/freeswitch/dialplan/public.xml`: comment out the whole
   `plivo_inbound` extension (it currently shadows the include).
4. `fs_cli -x reloadxml`, place a test call.
5. **Rollback:** uncomment `plivo_inbound`, `reloadxml`.

Note: keep `apply-inbound-acl` OFF until Kamailio (step 4) — today Plivo talks
to FS directly and an ACL would block it.

## 4. Kamailio SIP edge (MAINTENANCE WINDOW; inbound is down mid-change)

Decision baked into the prepared configs: **Kamailio takes public :5060; the
carrier origination URI moves from :5080 to :5060.** (Alternative — Kamailio on
5080 and FS external moved — requires an FS port change AND a Plivo change;
strictly worse.)

```bash
# a. install (kamailio + sqlite backend)
sudo apt-get install -y kamailio kamailio-extra-modules kamailio-sqlite-modules

# b. seed Kamailio's own DB (address/trusted tables)
sudo sqlite3 /etc/kamailio/kamailio.db < /home/angelo/lescall/kamailio/schema-sqlite.sql

# c. config
sudo cp /home/angelo/lescall/kamailio/kamailio.cfg   /etc/kamailio/kamailio.cfg
sudo cp /home/angelo/lescall/kamailio/dispatcher.list /etc/kamailio/dispatcher.list
sudo kamailio -c -f /etc/kamailio/kamailio.cfg        # syntax check BEFORE starting

# d. free :5060 on the private IP — FS *internal* profile holds it (unused for
#    carrier traffic). In /etc/freeswitch/vars.xml set internal_sip_port=5062,
#    then: fs_cli -x 'sofia profile internal restart'
#    (Kamailio's own FS-facing internal socket is :5064 — see kamailio.cfg —
#    so the ports never collide: Kamailio 5060+5064, FS 5062+5080.)

# e. start the edge
sudo systemctl enable --now kamailio
sudo cp /home/angelo/lescall/deploy/lescall-kamailio-acl-seed.service /etc/systemd/system/
sudo systemctl enable --now lescall-kamailio-acl-seed.service
sudo mkdir -p /etc/systemd/system/kamailio.service.d
sudo cp /home/angelo/lescall/deploy/kamailio.service.d-10-lescall-resilience.conf \
        /etc/systemd/system/kamailio.service.d/10-lescall-resilience.conf
sudo systemctl daemon-reload

# e2. give the dashboard a working live-ACL path — WITHOUT this, trunk applies
#     write the app DB but the Kamailio ACL silently lags until next boot:
sudo chgrp freeswitch /etc/kamailio/kamailio.db && sudo chmod g+w /etc/kamailio/kamailio.db /etc/kamailio
#     (angelo is in group freeswitch; or create a dedicated group)
#     AND install the trigger-file fallback units so reloads work even if the
#     kamcmd ctl socket stays root-only:
sudo cp /home/angelo/lescall/kamailio/systemd/lescall-kamailio-reload.{path,service} /etc/systemd/system/
sudo systemctl enable --now lescall-kamailio-reload.path

# f. carrier ACL: put Plivo's signaling CIDRs into the trunk's provider_ips via
#    the dashboard (PATCH is not needed — edit + re-apply), or directly:
#    kamcmd permissions.dumpAddresses   # to verify after sync

# g. lock FS behind the proxy: in sip_profiles/external.xml set
#      <param name="apply-inbound-acl" value="trusted_kamailio"/>
#    ensure autoload_configs/acl.conf.xml exists (any dashboard "apply" writes
#    it, or render_acl manually) — it ALWAYS contains trusted_kamailio
#    (127.0.0.1 + KAMAILIO_IP env, default matches 10.160.0.8 via generated file).
#    Set env KAMAILIO_IP=10.160.0.8 for the dashboard process before applying.
#    fs_cli -x 'reloadacl' ; fs_cli -x 'sofia profile external rescan'

# h. carrier: change Plivo INBOUND zentrunk origination URI to
#    sip:35.207.225.237:5060  (was :5080)

# i. test: inbound call → kamailio log shows relay → FS answers → agent speaks.
```

**Rollback:** point Plivo origination back at :5080, remove
`apply-inbound-acl`, `systemctl stop kamailio`. FS keeps working as before.

GCP note: open UDP 5060 to the carrier IP ranges in the VPC firewall (5080 can
be closed to the world after cutover). RTP range stays as-is (media is direct
carrier↔FS).

## 5. Google OAuth (login page + login wall)

Single-tenant fallback is active until these are set (they can live in
`calling-agent/.env` — auth.py loads it — or the dashboard process env):

```
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
OAUTH_REDIRECT_URL=https://agent.lescall.com/auth/callback
LESCALL_SESSION_SECRET=<long random string>     # else dashboard/.session_secret is generated
LESCALL_ALLOWED_EMAILS=tech@dhronai.com         # comma-separated; "*" = open signup
```

Google Cloud Console prerequisites (console.cloud.google.com → APIs & Services
→ Credentials → Create OAuth client ID, type "Web application"):
- Authorized redirect URI: `https://agent.lescall.com/auth/callback`
  (add `http://localhost:8080/auth/callback` for local testing)
- Configure the OAuth consent screen (app name "LesCall", logo, support email).

What turns on the moment BOTH client id + secret are set (dashboard restart):
- **Login wall** (`auth.install_login_gate`): every route except `/login`,
  `/auth/*`, `/static/*`, `/api/auth/me` requires a session — HTML navigations
  redirect to the branded login page (`static/login.html`, styled after
  lescall.com), API calls get 401.
- `/auth/login` → Google → `/auth/callback` verifies + checks
  `LESCALL_ALLOWED_EMAILS`, upserts the `users` row, sets the signed cookie.
- `/auth/logout` clears it.

KEEP the allow-list at trusted emails until the legacy endpoints are
owner-scoped — any allow-listed account currently sees the whole dashboard.
The cookie `Secure` flag follows OAUTH_REDIRECT_URL's scheme, so use the
https URL in production (TLS terminates in front of nginx).

**STATUS: DONE 2026-07-12** — creds in `calling-agent/.env`, dashboard
restarted, live on https://agent.lescall.com.

**DOMAIN MOVE 2026-07-25**: agent.lescall.dhronai.com → **agent.lescall.com**
(dhronai.com DNS records deleted). New Let's Encrypt cert issued; the legacy
dhronai vhosts and their certs were fully removed the same day (certbot
delete + dashboard.lescall vhost dropped — the old names no longer resolve).
Google OAuth client: redirect URI + JS origin updated to agent.lescall.com in
the console. Stored `calls.recording_url` links were rewritten in the DB.
Topology note: apex `lescall.com`/`www` stay on the separate marketing box
(35.207.233.208); only `agent.lescall.com` points at this VM.

Host-nginx layer (required because agent.lescall proxies "/" to the SPA
*container* on :8081, which would bypass FastAPI's gate): the vhosts in
`deploy/nginx-agent.lescall.conf` + `deploy/nginx-dashboard.lescall.conf` are
installed at `/etc/nginx/sites-enabled/{agent,dashboard}.lescall` (originals
in `/etc/nginx/lescall-backups/`). They send `/auth/`, `/login`, `/api/`,
`/static/` straight to :8080 and put an `auth_request` probe
(`/api/auth/me`) in front of the container SPA — a no-op whenever OAuth is
unconfigured. Rollback: restore the backups + `nginx -t && systemctl reload nginx`.

(Historical caveat, resolved by the 2026-07-25 domain move: DNS for
`dashboard.lescall.dhronai.com` pointed at a different old server; that
record is deleted along with the rest of the dhronai.com zone.)

## 5b. Number-verification dialplan (outbound caller-ID proof) — DONE 2026-07-12

Outbound calls only use a caller ID the tenant has VERIFIED (proved control of
by answering an OTP call) AND that egresses through the tenant's OWN applied
trunk gateway (`sofia/gateway/<name>/…`, so their carrier authorises the
caller-ID + billing) AND uses an agent the tenant OWNS — else the call is
REFUSED. The verification call speaks a code via this static dialplan:

```bash
sudo cp /home/angelo/lescall/deploy/freeswitch-lescall-verify.xml \
        /etc/freeswitch/dialplan/default/lescall_verify.xml
sudo fs_cli -x reloadxml    # needs mod_say_en (default-loaded)
```

Installed on this host. Requires each tenant's trunk to be **applied** (so its
FreeSWITCH gateway exists) before verify/dial works. Admins bypass the verified
gate; the agent-ownership gate applies to everyone (closes the "another
tenant's agent answered my call" leak).

## 6. Outbox worker (client-ERP lead hand-off)

```
LESCALL_ERP_WEBHOOK_URL=https://...   # unset = log-only mode (safe default)
LESCALL_ERP_WEBHOOK_TOKEN=...         # optional bearer
python3 -m shared.outbox_worker --interval 30   # run under systemd/tmux
```

Enqueue from any code: `shared.outbox.enqueue(payload, owner=...)` — same lead
enqueued twice stays one row; delivery retries with backoff; carrier "duplicate"
responses are terminal-success.

## 6b. Call recordings → GCS + public download links (2026-07-13)

Every finished call writes a full `calls` row (owner, agent_slug, callee
phone, duration_s, transcript, summary, collected_data slots, follow_up from
AI analysis, recording_path) plus a **256-bit random `download_token`**. The
public link is `https://agent.lescall.com/api/recordings/dl/<token>`
— auth-exempt, unguessable, and any miss returns an identical 404, so a
leaked link cannot be edited into someone else's recording.

Pieces:
- `shared/recording_storage.py` — token mint, GCS upload, catch-up sync,
  local retention cleanup. CLI: `python -m shared.recording_storage --once`.
- Bridge uploads the WAV right after call end; the
  `lescall-recsync.timer` (15 min, installed 2026-07-13) retries misses,
  backfills tokens, and deletes local WAVs `RECORDING_LOCAL_RETENTION_DAYS`
  (default 3) days after they're safely in GCS. Playback/download endpoints
  fall back to streaming from GCS when the local file is gone.
- Config in `deploy/.pg_env` (both services + timer read it).

### GCS setup — DONE 2026-07-14
- Bucket `gs://lescall-recordings` (asia-south1, uniform access, PRIVATE —
  zero `allUsers` grants; downloads always go through the tokenized endpoint).
- SA `lescall-recsync@lescall.iam.gserviceaccount.com` with
  `roles/storage.objectAdmin` scoped to the bucket only.
- **Credentials = impersonated ADC, no key file.** The org policy
  `iam.disableServiceAccountKeyCreation` blocks JSON *and* HMAC keys, so auth
  is `~/.config/gcloud/application_default_credentials.json` created via
  `gcloud auth application-default login --impersonate-service-account=
  lescall-recsync@lescall.iam.gserviceaccount.com` (alwynantonyben@ holds
  `roles/iam.serviceAccountTokenCreator` on the SA). All three units run as
  User=angelo and pick it up automatically.
- Backlog of 21 recordings uploaded; GCS-fallback streaming verified on the
  public link after local deletion.

CAVEAT: the ADC rides on alwynantonyben@'s Google session. If org session
control expires it, uploads pause (timer retries; local serving unaffected)
until the login command above is re-run. Durable fix: attach the
`lescall-recsync` SA to the VM (`gcloud compute instances set-service-account
lescall-agent --zone asia-south1-c --service-account lescall-recsync@lescall.
iam.gserviceaccount.com --scopes cloud-platform`, VM must be STOPPED —
~2 min downtime; external IP `agent` 35.207.225.237 is a reserved static
address, so the Plivo trunk ACL survives the restart). Afterwards the
metadata server supplies tokens and the ADC file can be deleted.

## 7. Verification checklist (plan §6)

- [ ] `python3 -m unittest discover tests` — all green (also under
      `calling-agent/venv/bin/python`).
- [ ] After service restart: place an inbound call — log line
      `Active agent: <slug> (direction=inbound did=8065953160 ...)` and a
      `calls` row appears (`sqlite3 shared/db/lescall.db 'select * from calls'`).
- [ ] Outbound dial from dashboard — bridge log shows the same slug that was
      active at dial time; `call_link.json` entries now carry `agent_slug`.
- [ ] Register a 2nd DID bound to a different slug (dashboard → numbers),
      apply, remove catch-all (step 3), call both DIDs concurrently → two
      sessions log two different agents; two `calls` rows carry two owners.
- [ ] `kamcmd permissions.dumpAddresses` matches the union of provider_ips.
- [ ] Kill test: `mv shared/db/lescall.db{,.bak}` → calls still answered by the
      active_agent.json agent (legacy fallback) → restore.

## 8. Containerized services + minute metering + log shipping (2026-07-14)

### Architecture after cutover
`docker compose -f deploy/docker-compose.services.yml` runs, on the HOST
network (FreeSWITCH/nginx loopback expectations unchanged):
- **lescall-api** — dashboard/server.py on :8080
- **lescall-runtime** — socket_server (:9000, :9004) + ws_audio_receiver
  (:9001) via deploy/runtime_entry.sh; logs in phase14_runtime/logs/docker/
- **lescall-workers** — deploy/workers_entry.sh loop: metering reconcile +
  outbox (5 min), recording sync (15 min), log shipper (60 min)
- **lescall-dashboard** (spa, :8081) — unchanged nginx static container
- **lescall-pg** — SEPARATE compose (docker-compose.postgres.yml); never
  touch it during app deploys.
The repo is bind-mounted at /app: deploying code = `docker compose ...
restart api runtime workers`. Rebuild the image only when requirements
change: `docker build -f deploy/Dockerfile.app -t lescall-app:latest .`

### Cutover / rollback
Cutover (done 2026-07-14): `systemctl stop --now lescall-dashboard
lescall-runtime lescall-recsync.timer && systemctl disable ...` then
`docker compose -f deploy/docker-compose.services.yml up -d`.
ROLLBACK: `docker compose -f deploy/docker-compose.services.yml down`
then `systemctl enable --now lescall-dashboard lescall-runtime
lescall-recsync.timer`. Units are left installed for exactly this.

### Minute metering (BILLING — handle with care)
- `usage_ledger` (one row per call, conn_id == FS UUID) + `usage_monthly`
  rollups. Written by the bridge (open/15s-heartbeat/close), refereed by
  `python -m shared.usage_metering --once` (workers loop): FreeSWITCH CDR
  `billsec` (/var/log/freeswitch/cdr-csv/Master.csv, read via freeswitch
  group gid 994) overrides the pipeline clock; crashes recover from the last
  heartbeat; no-CDR fallback after 24 h. `billsec_final` is the only number
  invoicing may use; manual fixes ONLY via usage_metering.set_manual (note
  required). Cross-check: `python -m shared.usage_metering --report`.
- User-facing: GET /api/usage/minutes, Topbar minutes chip, Usage page.
- BILLING_ROUNDING in deploy/.pg_env (per_call_ceil default).
- BURN-IN: treat numbers as shadow until a week of --report shows no
  unexplained clock_disagreement rows.

### Logging
- Every API request → dashboard/logs/requests.jsonl (rotated 20MB×10).
- Semantic actions → audit_events table (shared/audit.py).
- Hourly shipper → gs://lescall-logs (90-day lifecycle), state in
  shipped_logs table. Config: GCS_LOGS_BUCKET in deploy/.pg_env.

## 9. Sentinel — 24/7 self-healing watchdog (2026-07-14)

`lescall-sentinel.service` (host systemd, NOT in Docker) runs
`shared/sentinel.py` every 30s: checks containers/ports/FreeSWITCH(SIP
OPTIONS probe)/nginx/edge/DB/live-state freshness/workers/inbound-CDR
silence/stuck ledger rows/disk/memory/GCS auth. Auto-repairs by restarting
the owning container or unit — with 10-min cooldown and 3-per-hour cap per
target (then alert-only), and NEVER restarts runtime/FreeSWITCH while a call
is live on :9001. Alerts → sentinel.log + audit_events (owner='sentinel') +
email via sendmail to SENTINEL_ALERT_EMAIL (default tech@dhronai.com,
rate-limited 1/6h per key; exim delivery is best-effort). Status snapshot:
data/sentinel_status.json. Logs: phase14_runtime/logs/sentinel.log.
Verified 2026-07-14: killed SPA container → detected+repaired in 18s.
Tunables (deploy/.pg_env): SENTINEL_INTERVAL, SENTINEL_INBOUND_ALERT_H,
SENTINEL_DISK_ALERT_PCT, SENTINEL_MEM_MIN_MB, SENTINEL_ALERT_EMAIL.

KNOWN LIMIT: carrier-side failures (Plivo not delivering inbound) can only
be DETECTED (inbound-silence alert), not fixed locally — check Plivo console:
account credit, inbound zentrunk 68928093167632317 status, DID origination
URI must be sip:35.207.225.237:5080.

### 9b. Inbound loopback probe (provider-agnostic, 2026-07-14)
The sentinel actively verifies each tenant DID's INBOUND delivery daily at
SENTINEL_PROBE_HOUR_UTC (default 03:xx UTC) — no carrier API needed, works
for any SIP provider: it originates a short parked self-call through the
tenant's OWN trunk to their own DID; the call must come back as a genuine
inbound leg. +OK answer = loop proven; NO_ANSWER = carrier-side failure →
alert naming the owner. Probe legs are killed within seconds and their
usage_ledger rows are marked billable=0 ('sentinel loopback probe').
Manual run: `python -m shared.sentinel --probe`. Disable: SENTINEL_PROBE=off.
Inbound-silence alerts are per-DID (CDR public-context rows by destination).
First live run (2026-07-14): caught +912264231897 (alwynantonyben@) inbound
DEAD (NO_ANSWER via their own trunk) while +918065953160 verified OK.

## 10. Intelligence layer (2026-07-20) — automatic post-call pipeline

Design doc: `deploy/PLAN-intelligence-layer.md`. Every completed call is
analyzed automatically: extraction (gemini-2.5-flash) → embeddings
(gemini-embedding-001, pgvector) → follow-up decision → customer memory →
automation rules → notifications. No manual analysis exists anymore
(`/api/analysis/run` returns 410; the legacy followup-automation auto-dial
scheduler is disabled in server.py startup).

**Processes**
- `shared/intelligence/worker.py` runs as a second, supervised process inside
  lescall-workers (see deploy/workers_entry.sh) — NEVER fold it into the
  5-min billing tick. Liveness: `docker logs lescall-workers | grep 'intel:'`
  and the `settings(owner='', key='intel_worker_beat')` row.
- Queue = `intel_jobs` table (SKIP LOCKED claims, backoff, DLQ status
  'failed'). DLQ surface: `GET /api/intel/jobs?status=failed`, re-drive via
  `POST /api/intel/jobs/{id}/retry`. A sweeper enqueues any call the teardown
  hook missed — coverage is guaranteed, backfills happen automatically.

**Database**
- Versioned migrations: `shared/db/migrations/NNNN_*.sql`, applied MANUALLY:
  `docker exec -w /home/angelo/lescall lescall-workers python -m shared.db.migrate`
  (`--status`, `--dry-run`, `--down NNNN` for the testing env). Never at boot.
- pgvector 0.8.1 was compiled from source INSIDE lescall-pg (survives
  restarts, lost only on container recreation — which is forbidden). Disaster
  recovery: `bash deploy/pg_provision_pgvector.sh` then re-run migrations.
- Tests run against a `lescall_test` database in the same instance
  (`calling-agent/backend/tests/test_intel_*.py`) and rebuild it from scratch
  — that fixture doubles as the DDL disaster-recovery drill.

**LLM / quota**
- Extraction + reports: `gemini-2.5-flash` (INTEL_LLM_MODEL) — a different
  quota bucket from the live-call `gemini-3.1-flash-lite`, keys shared via
  calling-agent/.env; point INTEL_LLM_API_KEY at a dedicated key to isolate
  further. reasoning_effort is set explicitly (thinking eats max_tokens).
- Embeddings: `gemini-embedding-001`, 768-dim, via the same OpenAI-compat
  endpoint's /embeddings.

**In-call commitment clarification** is per-agent opt-in:
`follow_up.clarify_commitments.enabled: true` in agents/<slug>/agent.yaml.
Off by default — existing agents' behavior (and the golden prompt fixtures)
are untouched.

**Deploy**: api/runtime/workers = `docker compose restart <svc>` (bind mount);
SPA = `docker compose build spa && up -d spa`. Migrations before restarts.

## 11. Call-experience latency + continuation merge (2026-07-21)

Greeting/turn latency fixes + pause-tolerant turns. All tunables are env vars
on the **runtime** container (calling-agent/.env or compose environment);
`docker compose -f deploy/docker-compose.services.yml restart runtime` to apply.

**Greeting path** (was ~2.2s ESL→playback; target ≤0.8s warm)
- `PREFLIGHT_CACHE=true` — preflight verdict cached per (slug, sha, revision);
  agent edits self-invalidate via the registry watcher.
- Session routing DB work (dial lookup → slug → owner) now runs in ONE worker
  thread (`TelephonySession.create`), not serially on the event loop.
- `METERING_OPEN_DEFERRED=true` — usage-ledger row opens in background (≤1s
  later); close/reconcile unchanged.
- `TTS_CANNED_CACHE=true`, `TTS_CACHE_DIR=<repo>/data/tts_cache`,
  `TTS_CACHE_MAX_ENTRIES=500` — greeting/fillers/canned lines replay from disk
  PCM: 0ms synth, 0 Sarvam credits after first synthesis per variant.
- `TTS_OUTPUT_SR=8000` — Sarvam synthesizes at 8k; TelephonySink skips the
  blocking ffmpeg downsample entirely. Rollback: `TTS_OUTPUT_SR=16000`.

**Per-turn dead air** (was ~1.25s fixed before STT/LLM even start)
- `HARD_FLUSH_SILENCE_FRAMES=28` (560ms endpoint; old 35=700ms). Tier-b after
  continuation merge is validated: `20` (400ms).
- `TRANSCRIPT_MERGE_SKIP_WHEN_IDLE=true` — the 550ms merge wait now only
  happens when a follow-on segment can actually exist (`TRANSCRIPT_MERGE_WINDOW_MS`).
- `TURN_SILENCE_THRESHOLD_MS=1200` (TurnManager backstop; was 1600).
- `SARVAM_HTTP_SHARED=true`, `SARVAM_KEEPALIVE_EXPIRY_S=300` — one HTTP pool
  per process for STT/TTS (per-call clients made warmup a no-op after call 1).
- `BOOT_WARMUP=true` — ws_audio_receiver warms STT/TTS/LLM at process boot.
- `LANG_LLM_CLASSIFY_FIRST_TURN=false` — first turn never blocks on the
  language-classify LLM round-trip.
- `GREETING_ECHO_EARLY_EXIT_FRAMES=3`, `GREETING_ECHO_TAIL_MS=100` — the
  post-greeting echo gate opens faster for the caller's "hello".
- Grep `[LATENCY] speech_end→engine` for the per-turn fixed-tax number;
  `[ENGINE] Merge window skipped (idle)` should be the common case.

**Continuation merge** (pause-tolerant turns; OFF until canary-validated)
- `ENABLE_CONTINUATION_MERGE=false` → flip `true` to enable. When ON: caller
  pauses mid-utterance → agent answers the partial input fast → if the caller
  resumes within `CONTINUATION_WINDOW_MS=2000` of their own speech end, the
  reply is cut, the router rolls the turn back (memory, slots, stage, audit —
  see app/voice/turn_transaction.py) and re-runs ONE merged utterance.
  Calendar bookings defer until turn commit so a superseded turn never writes.
- Hearing during the window: `CONTINUATION_BARGE_FACTOR=1.3`,
  `CONTINUATION_BARGE_FRAMES=5`, `CONTINUATION_ECHO_EXIT_FRAMES=4`,
  `CONTINUATION_MAX_MERGES=3`. Outside the window all barge-in/echo constants
  are unchanged.
- Grep `[CONT-MERGE]` (merges) and `[TURN-TXN]` (rollbacks/commits).
- Tests: `calling-agent/backend/tests/test_continuation_merge.py`; full suite
  must pass with the flag both unset and set.

## 12. Customer Intelligence Hub — persistent memory in live calls (2026-07-21)

Closes the memory loop: `customer_memory` (written after every call since the
intelligence layer shipped) is now READ — by the extraction LLM and, crucially,
by the live-call runtime, so returning customers are greeted as known.

- **Write side (workers):** pipeline stages are now
  `extract → embed → followup → memory → digest → timeline → automation → notify`.
  `digest` precomputes `customer_memory.context_digest` (≤600 chars) at write
  time; `timeline` appends `contact_events` rows (unified per-contact timeline,
  idempotent via `uq_contact_events_ref`). The sweeper backfilled both across
  all historical calls automatically. Memory itself upgraded from capped lists
  to a governed fact lifecycle: `{text, call_id, date, confidence,
  status: active|superseded|expired, updated_at}` — dedup-by-meaning
  (similarity ≥0.60 updates in place), slot conflicts supersede (flip, never
  delete), per-category TTL expiry (promises 45d, objections 240d, …), plus
  `preferred_language` (from calls.language) and `preferred_call_time`
  (call-start-hour histogram, IST buckets).
- **Failed dials:** the intel worker's sweeper runs a CDR read-alongside pass
  (`shared/intelligence/dial_outcomes.py`, every 5 min) that joins unanswered
  CDR legs to `call_links` dials and lands classified `call.missed` events
  (busy|no_answer|rejected|invalid_number|network_failure) with retry hints.
  Billing/`usage_ledger` untouched.
- **Read side (runtime):** `_resolve_call_routing` (already off-loop) adds one
  contacts lookup + one customer_memory read (~1ms, 5-min in-proc TTL cache)
  and hands the digest to `ConversationRouter.set_customer_context()` → the
  `━━ KNOWN CUSTOMER ━━` prompt band + `{contact_name}` greeting
  personalization + no-re-ask name prefill. Fetch failure → None → call
  proceeds exactly as before. Grep `[BRIDGE] customer context` for fetch
  timing and `[CONTEXT] known customer` for injection.
- **Flags:** env `INTEL_PRECALL_CONTEXT=false` kills the fetch platform-wide;
  per-agent `memory: {use_customer_context: false}` in agent.yaml opts one
  agent out (default ON). `INTEL_DIAL_OUTCOMES_ENABLED=false` stops the CDR
  outcome pass.
- **Hub API/UI:** `GET /api/intel/contacts/{id}` (+`/timeline`, `/calls`),
  owner-scoped with `contact_grants` sharing (read|edit, owner-only to grant —
  flattened-roles doctrine, no admin). `PATCH /api/intel/contacts/{id}` sets
  `contacts.primary_agent_slug` (bridge defaults it to the first agent that
  talked to the contact). SPA: `/contacts` rows click through to
  `/contacts/:id` (ContactHub.tsx) — timeline, AI profile, score trend,
  memory, follow-ups, promises, lifetime stats. SPA is baked: UI changes need
  `docker compose -f deploy/docker-compose.services.yml up -d --build spa`.
- **Migrations:** `0006_contact_hub` (+`.down.sql`) — `context_digest`/
  `digest_updated_at` on customer_memory, `contact_events`, `contact_grants`,
  contact indexes on followups/commitments. `contacts.primary_agent_slug` is a
  boot MIGRATIONS ALTER (core table). Never edit 0006 once applied.
- **Tests:** `test_intel_memory.py`, `test_intel_timeline.py`,
  `test_precall_context.py`, `test_contact_hub_api.py` + extended
  `test_intel_stages.py` (8-stage e2e). Full suite: 334 passing.

## 13. Call-repair platform rules — hello-replay + dead-air reprompt (2026-07-21)

Two universal behaviors for EVERY agent (no YAML needed), both in the live
engine:

- **Early-call "hello?" replay** (`conversation.py`): a bare hello — any
  supported script (hello/हेलो/હેલો), optionally repeated or with filler
  ("are hello haan") — within `CALL_REPAIR_HELLO_WINDOW_S` (default 25s) of
  the greeting means the caller didn't hear us. The agent replays its last
  utterance verbatim instead of chatting. Capped at 2 replays per call so a
  deliberate "hello" opener still gets a conversation. Grep `[REPAIR] Early
  'hello'`. Gotcha encoded in the tokenizer: Python's `\W` shreds Indic
  matras — the hello matcher keeps the Devanagari/Gujarati blocks whole.
- **Dead-air reprompt** (`pipeline.py:_silence_watchdog`): agent finished
  speaking, then NO caller voice activity for `SILENCE_REPROMPT_S` (default
  6s) with nothing in flight anywhere (no STT segments, no queued
  transcripts/TTS, no LLM generation, playback fully drained, no farewell/
  outcome decided) → the agent says "Sorry, I couldn't hear you." (localized
  en/hi/gu) + repeats the last question. If the caller has not spoken at all
  yet (turn 0), it replays the utterance plain — no "couldn't hear YOU"
  accusation. Max `SILENCE_REPROMPT_MAX` (default 2) consecutive reprompts;
  the cycle resets on any real caller turn. The reprompt is recorded in
  conversation memory but never overwrites last-response memory, so repeats
  never compound. Defaults ON in telephony, OFF for CLI mic
  (`SILENCE_REPROMPT_ENABLED` overrides either way). Grep `[REPAIR] Dead air`.
- Interplay: the older STT-empty fallback (speech detected but empty
  transcript, feature 2/2026-05-30) and this watchdog are mutually exclusive
  by construction — one requires detected speech, the other requires none.
- Tests: `tests/test_call_repair.py` (14) — replay/cap/window/scripts,
  reprompt composition per language, and the watchdog decision matrix.

## 14. Follow-up execution modes — auto-dialling ON by default (2026-07-27)

The 0007 auto-dialer dialled EVERY decision='yes' follow-up (it placed its
first 12 real calls the moment the Thu 10:00 IST window opened). Migration
0008 made auto-dialling opt-in — and the opt-in was never exercised: 225
follow-ups accumulated with 0 `mode='auto'` rows, so the executor ran ~720
empty sweeps/day and every "call me tomorrow" silently died. On 2026-07-27
the business made follow-up calling a **platform universal rule**: ON for
every agent, existing and future.

- `followups.mode` `manual` | `auto` — only `auto` rows are dial candidates
  (`autodial._DUE_SQL`). Per-row toggle in FollowUps → Edit (re-arming
  clears a previous permanent block); also settable at create.
- **Default is now auto**: `_agent_auto_execute` (shared/intelligence/
  followup.py) returns True when the agent YAML says nothing — missing
  agent dir/file/key all mean True. An agent opts OUT with
  `follow_up.auto_execute: {enabled: false}`. The schema-side default
  (agent_definition/schema.py from_dict) mirrors this — change both
  together. `INTEL_AUTODIAL_ENABLED` stays the platform kill switch above
  everything.
- **Relative callbacks** ("call me in 10 minutes"): captured in-call by the
  clarifier (`_parse_relative_offset`, commitments carry `offset_min`),
  extracted post-call (`follow_up.suggested_offset_minutes` in the LLM
  schema), scheduled from CALL END. Customer-stated times
  (`auto_scheduled=0`) skip the 5-min grace and use the courtesy window
  (`INTEL_AUTODIAL_CB_HOUR_START/END`, default 07-21 IST, any weekday)
  instead of the business window; AI-picked times keep grace + 10-19
  Mon-Sat. Poll is 60s (`INTEL_AUTODIAL_POLL_S`).
- **Opening line**: the executor stamps `call_links.callback_meta`
  ({reason, topic=customer_intent, requested_when, source_call_at}); the
  bridge feeds it to `set_callback_context(meta=...)` and
  `_callback_opening()` renders "we spoke earlier today about X and you'd
  asked me to call you back — is now a good time?" in the agent's language
  (en/hi/gu templates, no LLM on the latency-critical opening).
- New guardrails: `superseded` (a newer call to the contact after the
  follow-up was created — permanent), per-row `max_attempts` (NULL =
  platform default 3), explicit `next_attempt_at` linear backoff
  (gap × attempt#). A dangling contact no longer blocks: the dial phone
  falls back to the originating call's number (the 38-blocked-rows
  incident).
- Memory on the call: the executor writes the follow-up reason + chased
  commitment + prior-call summary into `call_links.context_note`
  (→ WHY-YOU-ARE-CALLING-BACK prompt band); the KNOWN CUSTOMER band already
  carries the customer digest. `call_links.followup_id` links the dial.
- Outcome loop: originate result → `followups.execution_outcome`
  (provisional); when the placed call is ANALYZED with a real caller turn,
  `stage_followup`'s completion hook marks the follow-up completed with
  `executed_call_id` (voicemail nuance documented in code). `sweep_overdue`
  no longer flips in-retry auto rows to 'missed'.
- Tests: `test_autodial_guardrails.py` (57) — mode gate, superseded,
  per-row caps, backoff, phone-fallback.
- Open product items (deliberately NOT built): in-call "stop calling me" →
  DND disposition; TRAI NDNC scrubbing; billing stamp for platform-placed
  calls.

## 15. Agent-initiated hangup + close-permission rule (2026-07-27)

Before this, no engine-initiated hangup existed — the closing line played
and the channel stayed up until the caller hung up (dead air, burned
minutes). Platform universal rule, every agent:

- **Supreme rule — permission to close**: the prompt band
  `━━ ENDING THE CALL ━━` (prompt_blocks.call_closure_block, rendered
  unconditionally in every system prompt) makes the agent ask "anything
  else I can help you with?" before any goodbye. Deterministic backstop:
  `_is_close_confirmation` (agent's last response asked the permission
  question + caller replies a short "no / nahi bas / that's all") routes
  into the farewell close. `_contains_farewell` additionally catches
  goodbyes the intent classifier scores as affirmation ("okay bye").
- **hangup_pending** (ConversationRouter) arms ONLY on caller-consented
  or terminal closes: farewell exchange, close-permission confirm,
  **confirmed callback commitment** ("call me in 10 minutes" → confirm +
  goodbye tail → close; the request to be called later IS consent to end
  now — `_arm_callback_close`), wrong number, DND / decline threshold,
  LLM-failure graceful close. It is NOT `state.is_closing` (which also
  fires on booking-confirmed while the conversation continues). Any new
  caller utterance clears it — "wait, one more question" after the
  goodbye is answered, not cut off.
- **Busy brush-offs** ("sorry not free right now / abhi busy / atyare
  nahi" — `_BUSY_NOW_RE`) route into the callback pin-down flow instead
  of the objection strike counter: one polite "about how many minutes
  later should I call you back?" question, then confirm + close on the
  answer. No decline strike is burned.
- **Prompt text can NEVER hang up a call** — only the engine paths above
  do. The Copilot doctrine (agent_copilot_api.py) forbids the builder
  from writing guardrails that claim the agent will "end the call"; a
  real user incident (2026-07-27) had the Copilot ship exactly that
  rule, which also fought the pin-down ("do NOT ask for a better time").
- **Execution**: `TelephonySession._playback_loop` polls (0.5s get
  timeout) → `_maybe_agent_hangup` waits for wav_queue empty +
  `playback_busy_until` + grace, min 3s after arming (covers TTS synth
  of the farewell) → `hangup_request` over TCP 9004 → socket_server
  `_dispatch_hangup_request` queues an **event-locked** `hangup
  NORMAL_CLEARING` app, so FreeSWITCH ends the channel only after queued
  farewell audio finishes. Normal teardown (recording, metering, intel
  enqueue) then runs off the WS close as always.
- Kill switch: `ENABLE_AGENT_HANGUP=false` (bridge env). Tuning:
  `AGENT_HANGUP_MIN_WAIT_S` (3.0), `AGENT_HANGUP_GRACE_S` (1.0).
- Tests: `test_agent_hangup.py` (arming/cancel semantics),
  `test_autodial_guardrails.py` (customer-stated time grace/window),
  `test_commitment_clarify.py` (relative offsets, default-on).

## 16. Follow-up calls require human approval (2026-07-29) — INCIDENT FIX

**What happened.** At 05:36 UTC the auto-dialer called a real customer
(+91 87678 65050, contact 88) on follow-up 216 → call 304, `placed_by=autodial`.
The source was a 44-second call two days earlier on which **nobody promised
anything** — the pipeline recorded it as `promised_by='agent_promised'`,
`confidence 0.9`, and the dialer trusted that. Of the 20 rows armed that
morning, 15 had no commitment on the source call at all, and several were calls
that had failed on *our* side ("call failed due to technical audio issues")
logged as agent promises with confidence 1.0.

**This was the second occurrence.** Migration 0008 made auto-dialling opt-in on
2026-07-23 after the 0007 executor placed 12 unwanted calls; commit `b693a8c`
(§14) then flipped the per-agent `auto_execute` default back ON and reopened it.
The lesson: `mode='auto'` is a *preference* and a code default can synthesise
it. Approval is a *fact about a person's action* and nothing but the approve
endpoint can write it.

### The gate — three independent layers, all must pass

1. **SQL** — `shared/intelligence/autodial.py` `_DUE_SQL`:
   `AND f.approval_state = 'approved'` +
   `AND (f.approval_expires_at IS NULL OR f.approval_expires_at > now())`.
2. **Pure guardrail** — `blocked_reason()` returns `not_approved` /
   `approval_expired`, evaluated second (only DND outranks it). Absence reads
   as unapproved: a missing key never means "go ahead". Neither code is in
   `PERMANENT` — an unapproved row must stay rescuable by the human it waits on.
3. **Dial site** — `dashboard/server.py` `_autodial_once()`, immediately above
   `_record_dial`: refuses and `logger.error`s if it ever sees an unapproved
   row. If that line fires, layers 1–2 have regressed; treat it as a bug.

Approval cannot be manufactured elsewhere: `approval_state` is **not** in
`_FU_PATCHABLE` (so `PATCH /followups/{id}` cannot grant it), and neither the
pipeline, manual create, nor automation rules set it — everything defaults to
`pending` via migration 0011.

### Schema (0011)

`followups.approval_state` (`pending|approved|declined`, NOT NULL DEFAULT
`pending`), `approved_at`, `approved_by`, `approval_expires_at`, `declined_at`,
`declined_by`. `idx_followups_autodial` rebuilt with the approval predicate;
new `idx_followups_review` for the queue. **No backfill** — the DEFAULT put all
264 existing rows in `pending`, which is exactly the intended blast radius.

### API (`dashboard/intel_api.py`, all owner-scoped, all audited)

- `GET  /api/intel/followups/review-queue?include_declined=0|1&limit=`
  → `{items[], total (pending only), shown, approval_ttl_hours, max_attempts_per_approval}`
- `POST /api/intel/followups/{id}/approve` `{mode?, expires_in_h?}`
- `POST /api/intel/followups/approve` `{ids[], mode?, expires_in_h?}` (bulk)
- `POST /api/intel/followups/{id}/decline` `{note?}`

Approval TTL is **72 h** (`_APPROVAL_TTL_H`, matched to `AUTODIAL_MAX_AGE_H`).
`_approve_rows` refuses DND contacts regardless of what the UI sent, and
deliberately does **not** clear `autodial_blocked_at` — approving the *content*
of a call is not re-arming a permanently blocked row.

### Evidence — why the reviewer is shown words, not conclusions

`_evidence_for()` joins `commitments` **on `call_id`, not
`followups.commitment_id`** (that FK is NULL on essentially every row, while
all 89 commitments carry a machine-verified verbatim `quote`). Two tiers:
`strength='quoted'` (≥1 commitment → shows the quote and who said it) and
`strength='inferred'` (none → amber block stating *"No promise was recorded on
this call"*, and when `promised_by=='agent_promised'`, *"— despite labelling it
an agent promise"*). Plus `right(transcript, 4000)` → last 8 turns as context.
**The UI contradicts the AI's claim rather than restating it.** That is the
point of the screen; do not "simplify" it back into a summary.

### UI

`frontend/src/components/FollowupReview.tsx` — `FullscreenModal` opened from
"Review & approve calls" in the Follow-ups header (plus an amber banner while
anything is pending). Per-row Approve / Don't call, bulk select-all (skips
declined and DND), "Show declined" toggle so a decline is never a one-way door.
Row badges: `auto-call approved` only when `mode==='auto' && approval_state==='approved'`;
`needs approval` otherwise. Manual "Call now" stamps a `mode='manual'` approval
before dialling, so every call — hand-placed or automatic — has a named
authoriser in `followups.audit`.

### Tests

- `test_autodial_guardrails.py` (75) — pure-function gate, incl. fail-safe on a
  missing key and a source assertion that the predicate is still in `_DUE_SQL`.
- `test_followup_approval.py` (12) — **PG-backed**, calls `due_candidates()` for
  real. This is the layer a pure-function test cannot reach and the one that
  would have caught the incident.

### Operating it

- Kill switch: `INTEL_AUTODIAL_ENABLED` in `deploy/.pg_env`. **Set to `false`
  on 2026-07-29 as the incident stop.** Turning it back on does *not* by itself
  dial anyone — every row is `pending` — but do it deliberately:
  `docker compose -f deploy/docker-compose.services.yml up -d api`
  (**a bare `restart` does not re-read the env file**; that bit the cont-merge
  canary on 07-24). Verify with `docker exec lescall-api env | grep AUTODIAL`.
- Resting-state check — this should return only rows you intend to be called:
  `SELECT id, approved_by, approval_expires_at FROM followups
    WHERE approval_state='approved' AND approval_expires_at > now();`
- One approval permits up to `INTEL_AUTODIAL_MAX_ATTEMPTS` (3) attempts over
  the TTL; the approve dialog states this. Set the env to 1 if the policy is
  literally one call per approval.

### Also removed

The dormant legacy `_automation_scheduler_loop` (`followup_automations`) is
**deleted**, not commented out. It dialled real phones for any past-due
'scheduled' row with no approval/DND/window/cap, read a table the approval gate
never queries, and sat one uncommented line from live. `_dial_automation_entry`
survives for the operator-triggered `call-now` endpoint.

### Known, NOT fixed by this change (needs a decision)

- **Campaign engine** (`POST /api/outbound/campaign/start`): one authenticated
  request with `max_calls:0, delay_seconds:0` dials the whole contact book as
  fast as FreeSWITCH accepts, **ignores `disposition='rejected'` (DND)**, and
  has no calling-window check. Starting it is a human act, so it is outside the
  follow-up gate — but the DND blindness is a real compliance gap.
- **`POST /api/outbound/dial/{handle}`** does not check DND either; `?force=1`
  removes the duplicate-dial guard.
- **Sentinel loopback probe** (`shared/sentinel.py`, 03:00 UTC daily) self-calls
  our own DIDs from `phone_numbers` — it does **not** ring customers today. If a
  BYON tenant ever registers a number that terminates on a human handset it
  would, so make it per-number opt-in before that happens.
