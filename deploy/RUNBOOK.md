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
OAUTH_REDIRECT_URL=https://agent.lescall.dhronai.com/auth/callback
LESCALL_SESSION_SECRET=<long random string>     # else dashboard/.session_secret is generated
LESCALL_ALLOWED_EMAILS=tech@dhronai.com         # comma-separated; "*" = open signup
```

Google Cloud Console prerequisites (console.cloud.google.com → APIs & Services
→ Credentials → Create OAuth client ID, type "Web application"):
- Authorized redirect URI: `https://agent.lescall.dhronai.com/auth/callback`
  (add `http://localhost:8080/auth/callback` for local testing)
- Configure the OAuth consent screen (app name "LesCall", logo, support email).

What turns on the moment BOTH client id + secret are set (dashboard restart):
- **Login wall** (`auth.install_login_gate`): every route except `/login`,
  `/auth/*`, `/static/*`, `/api/auth/me` requires a session — HTML navigations
  redirect to the branded login page (`static/login.html`, styled after
  lescall.dhronai.com), API calls get 401.
- `/auth/login` → Google → `/auth/callback` verifies + checks
  `LESCALL_ALLOWED_EMAILS`, upserts the `users` row, sets the signed cookie.
- `/auth/logout` clears it.

KEEP the allow-list at trusted emails until the legacy endpoints are
owner-scoped — any allow-listed account currently sees the whole dashboard.
The cookie `Secure` flag follows OAUTH_REDIRECT_URL's scheme, so use the
https URL in production (TLS terminates in front of nginx).

**STATUS: DONE 2026-07-12** — creds in `calling-agent/.env`, dashboard
restarted, live on https://agent.lescall.dhronai.com.

Host-nginx layer (required because agent.lescall proxies "/" to the SPA
*container* on :8081, which would bypass FastAPI's gate): the vhosts in
`deploy/nginx-agent.lescall.conf` + `deploy/nginx-dashboard.lescall.conf` are
installed at `/etc/nginx/sites-enabled/{agent,dashboard}.lescall` (originals
in `/etc/nginx/lescall-backups/`). They send `/auth/`, `/login`, `/api/`,
`/static/` straight to :8080 and put an `auth_request` probe
(`/api/auth/me`) in front of the container SPA — a no-op whenever OAuth is
unconfigured. Rollback: restore the backups + `nginx -t && systemctl reload nginx`.

CAVEAT: DNS for `dashboard.lescall.dhronai.com` points at a DIFFERENT server
(35.207.192.113, the old box) — the wall on THIS host doesn't govern whatever
runs there. Point that DNS record here (or retire it) to close the loop.

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
public link is `https://agent.lescall.dhronai.com/api/recordings/dl/<token>`
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
