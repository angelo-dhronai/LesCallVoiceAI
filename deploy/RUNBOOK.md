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
- Backlog of 21 recordings uploaded; GCS-fallback streaming verified on the
  public link after local deletion.

### Auth: attached service account, NOT a login (durable fix, 2026-07-31)

**Doctrine: a production VM must never authenticate as a human.** The first
build used impersonated ADC — `gcloud auth application-default login
--impersonate-service-account=lescall-recsync@…` — because the org policy
`iam.disableServiceAccountKeyCreation` blocks JSON *and* HMAC key files. That
credential rides on alwynantonyben@'s Google session, so org session control
expired it on **2026-07-14** and every sync since died with `RefreshError:
Reauthentication is needed`. By 07-31: 270 of 311 calls local-only, log
shipping dead on the same credential, and the 41 calls that *had* uploaded
were unservable — retention had deleted their local copies and the GCS
fallback needs the same dead token. Re-running the login is a 90-day snooze
on the same outage, not a fix.

The durable answer is the third option GCE gives you, between "key file"
(blocked) and "human session" (expires): **attach the SA to the instance and
let the metadata server mint tokens.** No key material exists, nothing to
rotate, nothing to renew — tokens refresh for the life of the VM.

Both the SA and its scopes are boot-time properties, so this needs the
instance **STOPPED** (~2-3 min of inbound-call downtime — schedule it). The
external IP `agent` 35.207.225.237 is a *reserved static* address and
survives the stop, so the Plivo trunk ACL needs no change.

Run from Cloud Shell or any authed admin machine — deliberately NOT from the
VM, which is the whole point:

```bash
SA=lescall-recsync@lescall.iam.gserviceaccount.com

# 1. Roles the metadata SA must carry. objectAdmin on lescall-recordings is
#    already granted; the other three replace what the default compute SA
#    was doing (log shipping + the Cloud Ops agent, which is enabled here —
#    forget these and ops-agent telemetry goes dark at the same moment).
gcloud storage buckets add-iam-policy-binding gs://lescall-logs \
  --member="serviceAccount:$SA" --role=roles/storage.objectAdmin
gcloud projects add-iam-policy-binding lescall \
  --member="serviceAccount:$SA" --role=roles/logging.logWriter
gcloud projects add-iam-policy-binding lescall \
  --member="serviceAccount:$SA" --role=roles/monitoring.metricWriter

# 2. Swap the SA (VM must be stopped for this API call to be accepted).
gcloud compute instances stop  lescall-agent --zone asia-south1-c
gcloud compute instances set-service-account lescall-agent \
  --zone asia-south1-c --service-account "$SA" --scopes cloud-platform
gcloud compute instances start lescall-agent --zone asia-south1-c
```

`--scopes cloud-platform` is required, not cosmetic: legacy scopes cap IAM,
and the default compute SA sat at `devstorage.read_only`, which silently
downgrades every write no matter what roles you grant.

### The two credential-precedence traps (both bite AFTER the stop/start)

`google.auth.default()` resolves in a fixed order and metadata is **last**:
`GOOGLE_APPLICATION_CREDENTIALS` → `~/.config/gcloud/application_default_
credentials.json` → metadata server. So attaching the SA changes nothing on
its own if either of the first two still resolves. There are two distinct
consumers and each is blocked by a different one:

**Containers** are blocked by the env var — and `restart: unless-stopped`
does NOT re-read the compose file. On VM boot Docker restarts the *existing*
container objects with the environment baked in at creation time (verified
2026-07-31: all three still carried the dead `GOOGLE_APPLICATION_CREDENTIALS`
from their July create dates). The compose edit only lands when the
containers are **recreated**:

```bash
docker compose -f deploy/docker-compose.services.yml up -d   # recreates api/runtime/workers
```

**Host processes** (`lescall-sentinel.service`, `lescall-recsync.timer`) set
no env var, so they fall through to the well-known ADC path and keep using
the stale user token until the file is gone:

```bash
rm -f ~/.config/gcloud/application_default_credentials.json
```

Do the `rm` **after** container-side GCS is verified, not before — while the
file still exists, `git checkout deploy/docker-compose.services.yml && docker
compose up -d` is a complete app-layer rollback with no VM stop.

### Cutover order

1. `gcloud compute instances stop / set-service-account / start` (above).
2. On the VM: `docker compose -f deploy/docker-compose.services.yml up -d`.
3. Verify metadata identity + a real GCS round-trip (below).
4. `rm -f ~/.config/gcloud/application_default_credentials.json`.
5. Confirm host-side too: `journalctl -u lescall-sentinel -n 30` shows no
   `gcs-auth` alert on the next hourly check.

### Verify

```bash
# 1. identity — must be lescall-recsync@, NOT 927969746216-compute@
curl -H Metadata-Flavor:Google \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email
# 2. scopes — must include cloud-platform
curl -H Metadata-Flavor:Google \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/scopes
# 3. no stale env survived the recreate — must print (unset)
docker exec lescall-workers printenv GOOGLE_APPLICATION_CREDENTIALS || echo "(unset)"
# 4. WRITE round-trip (read alone proves nothing — read_only scope was the bug)
docker exec lescall-workers python -c "
from google.cloud import storage
b = storage.Client().bucket('lescall-recordings')
b.blob('.healthcheck').upload_from_string('ok'); b.blob('.healthcheck').delete()
print('GCS write OK')"
# 5. drain the backlog (~270 calls), then confirm the DB stamps moved
docker exec lescall-workers python -m shared.recording_storage --once
docker exec lescall-pg psql -U lescall -d lescall -c \
  "SELECT count(*) total, count(gcs_uploaded_at) uploaded, max(gcs_uploaded_at) FROM calls;"
```

Step 4 is the one that matters: a `list_blobs` read succeeds under
`devstorage.read_only` too, so only a write proves the scope change took.

Watch disk during the drain — `recordings/` is ~1.5 GB. Nothing was lost in
the 17-day outage because `cleanup_local` only prunes what is confirmed in
the bucket.

### Rollback

The cutover cannot destroy recordings: local WAVs are deleted only after a
successful upload is stamped, so the worst case is uploads stay broken
exactly as they are today.

- **App layer (no VM stop, seconds):** `git checkout
  deploy/docker-compose.services.yml deploy/.pg_env && docker compose -f
  deploy/docker-compose.services.yml up -d` — restores the env var and the
  gcloud mount. Only works while the ADC file still exists, which is why the
  `rm` is step 4 and not step 2.
- **Full SA revert (needs another stop, ~3 min):** re-attach the default
  compute SA with its original scope set, captured 2026-07-31 before the
  cutover:

```bash
gcloud compute instances stop lescall-agent --zone asia-south1-c
gcloud compute instances set-service-account lescall-agent \
  --zone asia-south1-c \
  --service-account 927969746216-compute@developer.gserviceaccount.com \
  --scopes=https://www.googleapis.com/auth/devstorage.read_only,\
https://www.googleapis.com/auth/logging.write,\
https://www.googleapis.com/auth/monitoring.write,\
https://www.googleapis.com/auth/service.management.readonly,\
https://www.googleapis.com/auth/servicecontrol,\
https://www.googleapis.com/auth/trace.append
gcloud compute instances start lescall-agent --zone asia-south1-c
```

- **If calls break rather than recordings** (the SA swap touches nothing in
  the telephony path, but): `bash phase14_runtime/check_runtime.sh`, then
  `docker compose -f deploy/docker-compose.services.yml restart runtime`.
  FreeSWITCH/Kamailio are on the host and unaffected by the container
  recreate; the static IP survives the stop so the Plivo ACL is unchanged.

NOTE: `lescall-recsync.timer` on the host is still enabled and duplicates the
containerized recsync pass every 15 min. Harmless (uploads are idempotent),
but disable it once the container pass is confirmed working:
`sudo systemctl disable --now lescall-recsync.timer`.

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
  **Superseded 2026-07-29 (§17)**: termination requests are now REAL
  engine wiring via `call_flow.termination` — the doctrine directs the
  Copilot to write them there instead of refusing.
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

- Kill switch: `INTEL_AUTODIAL_ENABLED` in `deploy/.pg_env`. Set to `false` on
  2026-07-29 as the incident stop, and **back to `true` the same day once the
  gate below was live** (log line: `follow-up auto-dialer ON`; all 266 rows sat
  at `pending`, `due_candidates()` returned 0 with the dialer running). It is a
  kill switch, not the consent mechanism — off stops everything, on grants
  nothing. Change it with
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

### Campaign engine — same limits, applied 2026-07-29

The bulk dialler was the other path that could ring a list of people, and it
had none of the above. All in `dashboard/server.py`, pinned by
`test_campaign_guardrails.py` (21):

- **DND.** `disposition='rejected'` — how an in-call "stop calling me" is
  recorded (`test_dnd.py`) — is dropped from the audience *before* the cap, so
  a list of opted-out contacts can't eat the budget. Re-read from the DB before
  each dial too (`_contact_is_dnd`, **fails closed**): someone who opts out on
  call 3 is not dialled on call 40 of the same run.
- **Caps.** `CAMPAIGN_MAX_CALLS` (200) and `CAMPAIGN_MIN_DELAY_SECONDS` (3),
  both env-overridable. `max_calls:0` now means "up to the ceiling", not
  "unlimited"; a larger request is clamped. Ceilings ride along on
  `/campaign/status` so the form states the real number.
- **Calling hours.** Refuses to start outside `autodial.within_window()` and
  **halts mid-run** when the window closes, stamping `camp["error"]` with how
  many were left undialled.
- **Honest refusals.** An empty or all-rejected audience now returns **400**;
  it used to return `ok:false` with HTTP 200, which the SPA toasted as
  "Campaign started" while nothing had been queued.
- **Audience fix.** `to_contact_later` is a *disposition*; the runner matched
  only `call_status`, so that audience silently resolved to zero.
  `_campaign_audience` handles both columns.
- **UI.** Start is behind a confirm naming the exact number of people; tallies
  gained a "Do not call N" badge.

### Known, NOT fixed by this change (needs a decision)

- **`POST /api/outbound/dial/{handle}`** does not check DND; `?force=1` removes
  the duplicate-dial guard. It is one phone per click by a logged-in human, so
  it was left alone deliberately — but a mis-click on a rejected contact still
  connects.
- **Sentinel loopback probe** (`shared/sentinel.py`, 03:00 UTC daily) self-calls
  our own DIDs from `phone_numbers` — it does **not** ring customers today. If a
  BYON tenant ever registers a number that terminates on a human handset it
  would, so make it per-number opt-in before that happens.

## 17. Call-termination policy — configurable, Copilot-controlled (2026-07-29)

**Why.** Live calls were effectively only ending when the caller said a
literal "bye" (§15's consented paths). Everything else — the LLM's own
judgment that the conversation was over, callers who walked away mid-call,
business rules like "end once the booking is confirmed" — had no path to a
hangup, and the Copilot doctrine explicitly refused such requests. Fixed by
making termination a per-agent config the engine executes.

**Config: `call_flow.termination`** (agent YAML; all keys optional, `{}` =
platform defaults; Copilot writes this from user requirements):

- `llm_end_call` (default **true**): the `━━ ENDING THE CALL ━━` prompt band
  teaches the model to append the token `[END_CALL]` after its goodbye line.
  The engine strips the token BEFORE `_validate_response` (whose markdown
  filter would leave spoken "ENDCALL" litter) in `_call_llm_stream`, then
  `_consume_llm_with_filler` arms `hangup_pending` exactly like the
  deterministic farewell paths. Caller speech still cancels; §15's drain
  logic unchanged. Marker regex: `_END_CALL_RE` (bracketed any-case, bare
  form upper-case only so prose "end call" survives).
- `end_conditions: [str]`: plain-English business rules rendered every turn
  as `━━ END-CALL RULES ━━`; they take precedence over the permission-to-
  close question and act through `[END_CALL]`.
- `farewell_patterns: [str]`: extra caller phrases (any language/script)
  that close deterministically via `_contains_farewell` — which also gained
  built-in unambiguous hangup phrases ("phone rakhta hoon", "મૂકું છું",
  "gotta go"...). `_DONE_REPLY_RE` now accepts bare thanks
  ("thank you/dhanyavaad/aabhar") as the close-permission confirm.
- `silence_hangup: {enabled, grace_s}` (default enabled, grace 10s): after
  both §13 dead-air reprompts go unanswered plus the grace window,
  `generate_silence_goodbye()` plays one goodbye, stamps
  `call_outcome=no_response` + `extra_flags.silence_hangup`, arms
  `hangup_pending`. The caller speaking re-opens the call and re-arms the
  full repair cycle. Env: `SILENCE_HANGUP_ENABLED` (telephony-default on),
  `SILENCE_HANGUP_GRACE_S`.
- `max_call_minutes` (0/absent = no cap): watchdog fires at the first quiet
  moment past the cap — `generate_timeout_goodbye()` plays a wrap-up line
  and arms a **hard** hangup (`_hangup_hard`), the ONE close caller speech
  does not cancel. `call_outcome=max_duration`.

**Where.** Engine: `conversation.py` (marker strip/arm, farewell vocab,
goodbye generators, hard-close gate at top of `process_stream`). Watchdog:
`pipeline.py` `_silence_watchdog` (now also max-duration + silence-hangup;
decisions in `_silence_hangup_due` / `_max_duration_due`, shared
`_pipeline_quiet`). Prompt: `prompt_blocks.call_closure_block`. Schema:
`CallFlowConfig.termination`. Copilot: schema docs + rewritten hanging-up
doctrine + `_normalize_definition` shape guards (drops <4-char farewell
patterns — "ok" would cut live calls) + two `_quality_warnings` lints
(end_conditions with llm_end_call=false; cap under 2 minutes).

**Tests.** `test_call_termination.py` (30: vocab, marker, silence/max-dur,
watchdog decisions, prompt rendering), `test_copilot_quality_lints.py`
(termination lints + normalization + doctrine pins), golden prompts
regenerated (closure band now carries the token doctrine).

**Rollback.** Per-agent: `termination: {llm_end_call: false}` (kills the
LLM signal), `silence_hangup: {enabled: false}`. Platform:
`SILENCE_HANGUP_ENABLED=false`; §15's `ENABLE_AGENT_HANGUP=false` still
kills ALL agent-initiated hangups at the bridge.

## 18. Connect hello-loop counters — platform-wide (2026-07-30)

**Problem (measured, 2026-07-29 logs).** Calls looped at connect: caller
says "hello" every ~2s, agent stays quiet. Root causes: bare `Hello`
openings (3 agents) invited the ping-pong; median speech-end→first-audio
was 2.47s (p90 3.26s) ≥ the human hello timeout; each next hello BARGED IN
and killed the reply before its first WAV was flushed (socket_server showed
zero PLAYBACK_SEND for the killed replies); Sarvam misdetected bare hellos
as Odia/Kannada forcing a retry (+350ms, 33×/day); a 240ms hello fell under
the 300ms STT floor; merged `hello hello hello` chains filled `name` and
burned stage max_turns. Full diagnosis: `deploy/PLAN-connect-hello-loop.md`.

**Fixes (one shared engine — every agent, current and future).**

| Layer | Behaviour | Where |
|---|---|---|
| Router | Instant identity intro on the first early bare "hello" when the last utterance was itself bare — no LLM turn. Composed from identity when the config opening is bare/placeholder (`connect_intro_line`). | `conversation.py` fast path after the hello-replay branch |
| Router | Dead-air reprompt never parrots a bare "Hello" — substitutes the intro. | `generate_silence_reprompt` |
| Router | Bare hellos never answer QA fields, never gap-fill slots (`_scrub_hello_values`), never burn `discovery_turns`. | `conversation.py` |
| Pipeline | **Delivery guard**: barge-in against a reply with <1s audible (or nothing flushed) is DEFERRED; the STT verdict kills playback only if the interruption wasn't a bare hello. Greeting/replays/intro/watchdog lines are `Response.protected` → guarded at any call age. | `_connect_barge_defer` + deferred verdict in `_conversation_layer` |
| Pipeline | Bare hellos excluded from merge-window/drain merges, CONT-MERGE rollbacks, and accumulator prepends (no more `hello hello hello…`). | `_conversation_layer` |
| STT | Connect window: short segments (<800ms) force the agent's default language instead of `unknown` (kills the Odia/Kannada retry tax); STT floor lowered to 200ms. | `_stt_worker`, `_min_speech_ms` |
| STT | Empty-streak damper: only the SECOND consecutive empty speaks "couldn't hear you" (was: every one of 4 — fed an echo→empty→sorry loop). | `_empty_fallback_speaks` |
| Copilot | OPENING doctrine (see correction below) + `call_flow.connect` schema + normalization; `_enrich_new_agent` fills only a MISSING opening, with a short `Hello!`. | `agent_copilot_api.py` |

**Env knobs (all default ON; delivery guard telephony-only by default).**
`CONNECT_HELLO_FASTPATH` (true) · `CONNECT_DELIVERY_GUARD` (true if
TELEPHONY_MODE) · `CONNECT_WINDOW_S` (12) · `CONNECT_MIN_PLAYED_S` (1.0) ·
`CONNECT_MIN_SPEECH_MS` (200) · `CONNECT_FORCE_LANG` (true) ·
`CONNECT_SHORT_SEG_MS` (800) · `EMPTY_STREAK_DAMPER` (true).
Per-agent: `call_flow.connect: {hello_fastpath: bool, delivery_guard: bool}`
(defaults ON; the block exists only to opt out).

**Rollback.** Per-agent: `call_flow.connect: {hello_fastpath: false,
delivery_guard: false}`. Platform: set the matching env to `false` in
compose (remember: compose *restart* does not re-read env — `up -d`).
All other guards (merge/QA/slot/reprompt hygiene) are unconditional
correctness fixes with no knob.

**Tests.** `tests/test_connect_hello.py` (29) + opening/enricher/connect
tests in `test_copilot_quality_lints.py`. Watchdog/guard decisions are pure
functions tested via SimpleNamespace with bound methods (same pattern as
§17). Golden prompts untouched (no prompt-band changes).

**Doctrine correction (2026-07-30, same day).** The first cut of this fix
also rewrote the 3 bare `Hello` openings into full identity monologues and
made the Copilot lint/repair bare openings. That was WRONG and is reverted:
the owner's intended pattern is **short "Hello" → WAIT → the engine speaks
the identity intro when the caller responds** (the fast-path row above; the
intro is a shortcut so it rides the TTS disk cache — no LLM, ~0ms synth
after first play). Openings are back to `Hello`; the bare-opening lint and
create-time identity-opening composition are deleted; the system-prompt
doctrine now says both styles are valid and the Copilot must NEVER rewrite
a user's opening unless asked (`test_enrich_never_touches_short_opening`
pins this). For short-opening agents, keep `identity.agent_name` /
`company_name` filled — the connect intro is composed from them.

**Open item (5.2 audit).** In call b217ca93 (2026-07-29 11:04) echo
segments born INSIDE the 3.2s ECHO-MUTE window still reached STT — the
echo-gate early-exit (5 frames ≈ 100ms) is the suspect. The damper contains
the blast radius; tuning `ECHO_EARLY_EXIT_FRAMES` needs its own canary.

## 19. TTS accent consistency — platform-wide (2026-07-30)

**Problem (measured, Jul 25-30 logs).** English replies spoken with a heavy
Hindi accent; accent flips mid-call. 87/88 calls opened with hi-IN audio
(state.language was hardcoded "hi" — the agent's `languages.default` only
picked the voice, never the language); the TTS `target_language_code`
followed conversation state, not the text (28 clearly-English lines sent
hi-IN in 5 days, +8% Hindi pace factor riding along); Sarvam's per-segment
STT detection rewrote state unguarded BEFORE transcript validation (21
mid-call accent switches on Jul 29 alone; a Gujarati carrier hold message
flipped an en agent to gu-IN); wrong-accent PCM persisted in the canned
cache ('Hello' replayed 62× from hi-IN audio). Full diagnosis:
`deploy/PLAN-tts-accent-consistency.md`.

**Fixes (one shared engine — every agent, current and future).**

| Layer | Behaviour | Where |
|---|---|---|
| Session | `state.language` seeded from the agent's `languages.default` (clamped to en/hi/gu; exotic codes keep the legacy hi seed). | `ConversationRouter.__init__`, bridge, `run.py`, CLI fallback |
| TTS | `target_language_code` derived from the TEXT being spoken (`tts_language_for`: script wins; code-mixed Devanagari stays hi-IN; Latin follows state only with romanized hi/gu markers). Resolver precedence: Response.language stamp > text > state. | `conversation.py`, `pipeline.py _resolve_tts_language` |
| TTS | LLM sentences stamp their language at ENQUEUE (a mid-queue state flip once spoke an English reply as hi-IN); fillers stamp the state language they were picked by; STT fallback lines stamp their chosen language. | `Response.language` |
| STT | Language mirror guarded: applied only after the transcript survives validation AND the claim matches the transcript's script/markers (bare "hello" and lone "okay" can never flip; en switch needs ≥2 words — same bar as the conversation-side threshold). | `stt_mirror_consistent`, `_stt_worker` deferred write |
| Pace | Follows the same corrected language — English no longer gets the hi ×1.08 factor. | free via the resolver |
| Copilot | New agents get the Sarvam-recommended speaker for their default language (en→ishita/ratan; the old hardcoded simran is below tier for English); missing per-language voice entries inherit the agent's existing speaker (one voice per agent). Existing agents untouched. | `voice_catalog.RECOMMENDED_SPEAKERS`, `_default_definition`, `_enforce_invariants` |
| Logs | One audit line per utterance: `[TTS] lang=<code> src=stamp|text|state speaker=<s>`; `Cache HIT` now records language + speaker. | `pipeline.py`, `tts_sarvam.py` |

**Env knobs.** `TTS_LANG_FROM_TEXT` (true) · `LANG_MIRROR_GUARDED` (true).
Per-agent opt-out: `languages.tts_lang_from_text: false`.

**Cache purge (one-time, at deploy).** `data/tts_cache/` held 13 pure-English
entries keyed hi-IN (created while state was mislabeled). Purge after the
runtime restart: `rm -f data/tts_cache/*.pcm` — self-rebuilding, one synth
per canned line on first play.

**Rollback.** `TTS_LANG_FROM_TEXT=false` → accent follows state again;
`LANG_MIRROR_GUARDED=false` → legacy unguarded immediate mirror; P0 seed is
a constructor arg (one-line revert per call site). Compose env changes need
`up -d` — restart alone does not re-read env.

**Tests.** `tests/test_tts_accent.py` (21) + 4 voice tests in
`test_copilot_quality_lints.py`. Not changed: LLM prompting, response
selection, stage flow, barge-in, cache-key formula, existing agent YAMLs.

**Deliberately out of scope.** IVR/hold-announcement text still counts as a
caller turn for the conversation's own (guarded) language path; stage-goal
fragments spliced into callback greetings (separate bug, recorded in the
plan); per-chunk long-sentence pace damping variation.

## 20. Language fidelity + deterministic contact capture — platform-wide (2026-07-30)

Two live-call failures on `918076562864_20260730_120613` (full diagnosis:
`deploy/PLAN-language-fidelity-and-contact-capture.md`): the agent answered a
pure-Hindi caller in **Gujarati** (twice, back-to-back), and the caller's
email was stored as the LLM's guess `bell@gmail.com`, which the caller's
correction could never overwrite. Everything below is engine-level — it
applies to every existing agent and every future Copilot agent.

### Part A — language fidelity

| Layer | Fix |
|---|---|
| A1 marker regex | `_STRONG_GU_DEVA_RE`/weak tier use Devanagari-block boundaries `(?<![ऀ-ॿ])` — Python's `\w` excludes matras/virama, so "अच्छे" matched marker "छे" mid-word and flipped the call. Weak tier is no longer a substring scan. |
| A2 evidence switch | One per-word vote scorer for all pairs (en/hi/gu): instant switch at density ≥0.6 with ≥2 votes (en additionally needs a function word); single-vote/0.3–0.6 evidence → one-turn confirmation band; acks, names, digits, emails are neutral. Replaces the single-marker fast path and the 2-ASCII-word English switch. 1-turn oscillation guard. Explicit requests stay instant, all pairs. |
| A3 neutral turns | Whisper/script-retry-recovered transcripts never vote (`Transcript.stt_source`), capture/name-pending turns never vote, and the STT mirror's en claim now needs an English function word ("alvin anthony" can no longer flip a call). Mirror skipped entirely for recovered sources. |
| A4 STT pinning | After 2 stable turns the streaming WS reconnects (turn boundary only) pinned to the conversation language — kills the 29×/day pa/bn/kn/ml script roulette. Unpins the moment state moves off it. Mixed-script finalizes salvage clean tokens before any retry ('ਇਹ। K L W Y N' keeps the letters). The forced-language script-retry now actually re-uploads audio via the HTTP rescue client (under streaming it silently retried nothing). Whisper gets the conversation language as a hint. |
| A5 delivery epoch | Every supersede (cont-merge rollback, barge-in drain, deferred kill) bumps an epoch; the TTS player re-checks it between dequeue and synthesis — the double-spoken pre-/post-merge replies are structurally dead. Replays (`Response.replay`) are dropped when a newer response already played. |
| A6 telemetry | `grep RESPONSE-MISMATCH` finds every reply whose text language differs from the directed language. Warning-only. |

### Part B — deterministic contact capture

The LLM asks; the engine captures. `app/voice/contact_capture.py` owns the
spoken-form normalizer (at/dot/एट/डॉट operators, spelled-letter runs incl.
Devanagari letter names "एल दब्यू वाय एल", the ITN `www.` artifact, provider
snap gmial→gmail, TLD fixes). Email-typed slots (engine infers the type for
slots NAMED email even when authored `type: text` — the live LesCall agent):

- **Only writers**: the deterministic normalizer (typed extraction + capture
  turns). `merge_state` rejects `source="llm"` for email/phone slots and
  rejects validator-failing emails from ANY source.
- **Pending until a bare yes**: policy `confirm`, spelled read-back
  ("b. e. n. — at — gmail dot com"), no 2-turn auto-accept, and "okay tell
  me more" does NOT confirm (bare ≤3-word affirmations only). Corrections
  re-enter read-back with the new value.
- **Capture mode** while the email is being asked/confirmed: merge window
  stretches to `CAPTURE_MERGE_WINDOW_MS` (2000ms, up to 8 fragments), STT
  forced en-IN, barge-in threshold ×2, partial spellings buffer silently
  (agent stays quiet while the caller spells).
- **Cap → unresolved, never a guess**: after `max_asks` (2) failed
  read-backs the slot is skipped, `extra_flags.email_unresolved=true` rides
  collected_data into the intel pipeline for human follow-up.
- Copilot provisions new agents' email slots explicitly (type email +
  confirm + spelled + 2 asks) and teaches: ask ONCE, never "speak slowly".
- Phone slots keep their authored merge policy (live outbound flows must
  not stall); they gain digit-groups read-back and capture-mode endpointing.

### Knobs (env unless noted)

`LANG_EVIDENCE_SWITCH` (true) / per-agent `languages.evidence_switch`;
`STT_LANG_PIN` (true), `STT_LANG_PIN_TURNS` (2); `CAPTURE_MODE` (true),
`CAPTURE_MERGE_WINDOW_MS` (2000); accent-era knobs unchanged
(`TTS_LANG_FROM_TEXT`, `LANG_MIRROR_GUARDED`). Compose env changes need
`up -d`, not restart.

### Verification

Tests: `tests/test_language_fidelity.py` (26) + `tests/test_contact_capture.py`
(31); full suite green. On a live call grep for: `[LANG] Evidence switch`,
`[LANG] Turn doesn't vote`, `[STT] Language pinned`, `[STT] Script salvage`,
`[TTS] Dropped stale response`, `[CAPTURE] armed/candidate/confirmed`,
`RESPONSE-MISMATCH`.

---

## §21 — Call hearing + capture hardening, platform-wide (2026-07-31)

Follow-up to §20, driven by call `918076562864_20260731_052423` (Hindi, maya
outbound): the §20 language layer held, but streaming-STT segment accounting
silently destroyed real caller speech, and the email capture armed five turns
after the ask, fought the LLM for ownership, then discarded the confirmed
address at call end (DB got `email: null`, `name: "Panj Loa"` — a spelling
fragment). Full diagnosis + fix spec:
`deploy/PLAN-call-hearing-and-capture-hardening.md`. All fixes are
engine-level — every existing agent and every future Copilot agent inherits
them with zero YAML changes.

### Part H — hearing (pipeline / stream)

- **Covered-seconds plausibility** (H1): the words-per-duration hallucination
  guard now scores against `STTRouter.last_covered_seconds` (audio the
  streamed finalize actually covered), not the single VAD chunk. Live: real
  answers rejected as "14 words in 1440ms".
- **Barge-in keeps fed caller audio** (H2, `BARGE_KEEP_STREAM_AUDIO=true`):
  the queue flush no longer `reset_turn()`s the stream — the interrupting
  speech IS the next turn ("cleaning and helping…" and a spelled email
  fragment both vanished via the generation bump).
- **Late-segment stash** (H3): a non-empty stream segment arriving between
  turns is stashed 2.5s and served to the next finalize instantly (was:
  dropped as "stale inter-turn segment" while that same finalize sat in a
  2s timeout).
- **Consumed-episode empties** (H4): an empty finalize <3s after an
  over-covered transcript no longer counts as "unrecognized speech" (was:
  "आवाज़ स्पष्ट नहीं आई" to a caller who had been perfectly heard).
- **Continuation hold** (H5, `MERGE_HOLD_CONTINUATION=true`, 1200ms): a
  transcript ending in a dangling word (जो/कि/तो/and/because/અને…) never
  takes the idle-skip fast path — the resumed speech merges into one turn.
- **"हेलो" allowlisted** (H6): the spelling Sarvam actually emits joins the
  single-word greeting allowlist (the caller's are-you-alive probe was
  skipped; dead-air watchdog fired instead).
- **Condensed watchdog replay** (H7): dead-air reprompts >90 chars replay
  only the final sentence (the question), not the whole essay.

### Part C — capture ownership + state

- **Ask-time arming** (C1, `CAPTURE_ASK_ARM=true`): the engine detects an
  email-ask in the agent's own utterance (LLM or template) and arms capture
  immediately; rejection no longer releases it. `note_asked` was
  scheduler-only — LLM/stage-goal asks (the live norm) left capture dark.
- **Engine-owned readbacks** (C2): while armed, any email in LLM output is
  replaced with the spelled read-back of the engine's pending value (or a
  generic reference) — the LLM can never again "confirm" a value state does
  not hold. Progress queries ("नोट कर लिया?") answered from state. Runtime
  system-prompt doctrine injection covers EXISTING agents (never spell-slowly,
  never claim noted).
- **Read-back-context confirms** (C3): bare yes/no resolves a pending
  contact value only right after a read-back; "हाँ सही है सही है।"
  (all-affirmation-token turns) now confirms; a late "नहीं नहीं" answering
  "any more questions?" no longer discards the email. Applies to the generic
  confirm loop too. Confirmed values are immune to later negations.
- **Expectation-scoped affordances** (C4): the 2s capture merge window,
  en-IN STT forcing and raised barge threshold apply only while the engine's
  last utterance solicited spelling/confirmation — armed-but-digressing turns
  (Zoom, booking) pay normal latency (was: 2s tax on five closing turns).
- **Extraction hygiene** (C5): the LLM slot extractor is skipped during
  spelling turns (name='Panj Loa' from spelled "angelo"); the echo guard
  applies only to Latin-script caller turns (Devanagari "ज़ूम" answering
  "Zoom या Google Meet?" was dropped as an agent echo).
- **Answer-slot overwrite** (C6, `SLOT_ANSWER_OVERWRITE=true`): a caller
  directly answering the slot question the agent just asked may overwrite a
  filled non-contact slot via `source="answer"` (preferred_time='Now' was
  immortal; the booked "आज 4 बजे" never landed). Contact slots stay
  deterministic-only for "answer" too.
- **Normalizer** (C7): "एट द रेट"/"at the rate" variants → @; spelled
  material before an @-token joins the local part
  ("ए एन जी ई एल ओ। डॉट। पी ए। knaan@gmail.com" → angelo.pa.knaan@gmail.com).

### Knobs

`BARGE_KEEP_STREAM_AUDIO` (true), `MERGE_HOLD_CONTINUATION` (true),
`MERGE_HOLD_CONTINUATION_MS` (1200), `CAPTURE_ASK_ARM` (true),
`SLOT_ANSWER_OVERWRITE` (true). H1/H3/H4 ride `ENABLE_STREAMING_STT`;
C2–C5 ride `CAPTURE_MODE`. Compose env changes need `up -d`, not restart.

### Verification

Tests: `tests/test_hearing_capture_hardening.py` (43, seeded with this
call's exact ASR); full suite 800 green. On a live call grep for:
`[STT:Stream] late-segment stash`, `[ENGINE] Merge window held`,
`[CAPTURE] ask detected`, `[CAPTURE] LLM spoke an email`,
`[STATE] … source=answer`, absence of `Rejecting implausible` on
multi-chunk streamed turns. Note: `opening handshake failed` every 20s in
ws_audio_receiver.log is the docker healthcheck probing :9001 — cosmetic,
not the Sarvam stream.

## 22. Close-confirmation gate — platform-wide (2026-07-31)

**The call that caused it** (`a0f9a6ae`, 07:16 UTC, ws_audio_receiver.log).
Two independent defects, one after the other:

1. `07:16:14` STT merged segments #21 "Okay" + #22 "Bye" into `okay bye` —
   the caller was actually mid-sentence ("...don't mind giving it sir"
   arrived 1.3s later as #23). The deterministic farewell branch fired
   instantly: **"Thank you for your time."** + `hangup_pending`.
2. The caller kept talking, so the engine cancelled that hangup correctly at
   `07:16:18`. But the LLM's reply to the next turn — *"I understand… **Is
   there anything else I can help you with today?**"* — carried `[END_CALL]`.
   The engine trusted the marker on a turn that was an **open question**, and
   at `07:16:28` the bridge cut the line while the caller was answering it
   ("Can you take my email address?", segment #24 at `07:16:25`).

That is the "mysterious mid-conversation cut": nothing mysterious, the agent
was told to end and the caller's answer was still inside STT when it did.

**Fix — three layers, all default-on for every agent, no YAML edit needed:**

- **Confirm before ending** (`call_flow.termination.confirm_before_end`,
  default **true**). Every *speech-triggered* close now asks one question
  first — owner-specified copy since 2026-08-01: hi "ठीक है, तो फिर मैं कॉल
  कट कर दूँ?", en "Alright then, shall I end the call here?", gu equivalent
  (first-person subjunctive = gender-neutral; override per agent via
  `response_bank.localized_responses.close_confirm`) — and arms
  `hangup_pending` only on a clear yes. The confirmed close then speaks the
  short platform thank-you — hi "बात करने के लिए धन्यवाद, आपका दिन शुभ हो।"
  (`localized_responses.close_confirmed` overrides) — NOT the agent's
  `closing_responses`, because the goodbye may already have been spoken on
  the turn that triggered the confirmation; the closure prompt band also
  tells the LLM to keep its pre-`[END_CALL]` goodbye to ONE short sentence
  so farewells don't stack. `_is_close_confirm_yes` is biased
  toward keeping the call alive: a bare affirmation, a short affirmation-led
  turn (≤4 tokens), a farewell word or an explicit "end/cut/रख" wins; a
  negation, a question, or **anything with content in it** drops the gate and
  the turn is answered as normal conversation. Capped at
  `_CLOSE_CONFIRM_MAX = 2` asks per call so a caller who really is leaving is
  never trapped in a loop.
- **`[END_CALL]` on a question is ignored.** A turn whose text ends in `?` is
  by definition not a goodbye; the marker is dropped and logged
  (`[HANGUP] [END_CALL] ignored — turn ends in a question`). The prompt band
  gained a matching `✗ NEVER write [END_CALL] on a turn that asks the caller
  a question` rule.
- **The bridge waits for silence.** `_maybe_agent_hangup` now also requires
  `pipeline._pipeline_quiet(now)` — the engine only cancels a pending hangup
  when a *finalized* transcript reaches `process_stream`, so a caller who
  starts speaking during the drain window had not cancelled it yet. Bounded
  by `AGENT_HANGUP_MAX_WAIT_S` (20s, then `[HANGUP_FORCED]`) so a stuck
  VAD/STT flag cannot hold a dead call open. A **hard** close (max-duration
  cap) skips the check by design.

**Deliberately NOT gated** — these are not hearing judgements, and asking
again would be worse than the risk: DND / "stop calling me" and wrong-number
(compliance), silence hangup (nobody is there to answer), and
`max_call_minutes` (the platform is ending the call regardless).

**Copilot.** `confirm_before_end` documented in the schema block and the
ENDING CALLS doctrine; `_normalize_definition` coerces it to bool; a new
`_quality_warnings` lint fires when it is set false. New agents inherit the
gate with no configuration.

**Rollback.** Per-agent: `termination: {confirm_before_end: false}` restores
instant closes. Platform: `ENABLE_AGENT_HANGUP=false` still kills all
agent-initiated hangups; `AGENT_HANGUP_MAX_WAIT_S=0` disables the new quiet
wait alone.

### Verification

Tests: `test_call_termination.py` (+14, including a replay of both live
defects), `test_agent_hangup.py` + `test_continuation_merge.py` updated to
drive the two-turn exchange, `test_copilot_quality_lints.py` (+3), 90 golden
prompts regenerated for the two new closure-band lines; full suite **817
green**. On a live call grep ws_audio_receiver.log for
`[HANGUP] close-confirm asked`, `[HANGUP] close confirmed by caller`,
`[HANGUP] close-confirm answered … not a goodbye`, and
`[HANGUP] [END_CALL] ignored`.

## 23. FreeSWITCH event-socket credential (2026-07-31) — OUTAGE FIX

**The outage.** From 08:19 UTC (container recreate after the 08:03 reboot)
until 11:00 UTC, **every outbound call failed** — manual dial, campaigns,
follow-up automations, autodial, number test-call and the nightly loopback
probe. The dashboard returned `{"ok": true, "status": "dialing"}` and then
logged `DIAL <phone> from <did> (own-number) → failed`; FreeSWITCH's log
showed nothing at all, because no originate ever reached it.

Cause: the ESL hardening was applied to **code only**. `shared/fs_esl.py`
dropped the `ClueCon` default (`PASSWORD = os.environ.get("FS_ESL_PASSWORD",
"")`) and now rejects unset / vendor-default / <16-char values, but
`/etc/lescall/` had never been created on the host and
`/etc/freeswitch/autoload_configs/event_socket.conf.xml` still said
`password="ClueCon"`. `fs_api` raised before opening the socket,
`_fs_esl_exec` turned that into `-ERR esl transport failure: …`, and
`_parse_fs_result` read it as `failed`.

**The password lives in exactly two places and they must be rotated
together** — that is the whole point of the loud failure:

1. `/etc/lescall/telephony.env` → `FS_ESL_PASSWORD=` (root, 0600)
2. `/etc/freeswitch/autoload_configs/event_socket.conf.xml` → `password=`

```bash
PW=$(openssl rand -hex 32)
sudo install -d -m 0750 -o root -g root /etc/lescall
printf 'FS_ESL_PASSWORD=%s\n' "$PW" \
  | sudo install -m 0600 -o root -g root /dev/stdin /etc/lescall/telephony.env
sudo cp -a /etc/freeswitch/autoload_configs/event_socket.conf.xml{,.bak-$(date +%Y%m%d)}
sudo sed -i "s|name=\"password\" value=\"[^\"]*\"|name=\"password\" value=\"$PW\"|" \
  /etc/freeswitch/autoload_configs/event_socket.conf.xml
sudo systemctl restart freeswitch            # check `show channels count` first
sudo docker compose -f deploy/docker-compose.services.yml up -d
sudo systemctl restart lescall-sentinel
```

**`docker compose` must now be run with `sudo`.** The `env_file:` is read by
the **compose CLI as the invoking user**, not by the daemon as root — so a
plain `docker compose up -d` as `angelo` dies with
`open /etc/lescall/telephony.env: permission denied`. Keeping the file
root-0600 is deliberate (the repo is bind-mounted into every container as uid
1000, so anything under it is readable by the live-call runtime); running
compose as root is the price. Do **not** relax the file mode instead.

**Never pass `--remove-orphans`.** `lescall-pg` belongs to
`docker-compose.postgres.yml` and compose reports it as an orphan of the
`deploy` project. `--remove-orphans` deletes the database container.

**The host sentinel needs the secret too.** It runs as `User=angelo` under
systemd with only `.pg_env`, so `loopback_probe` (`shared/sentinel.py`)
could not originate. Fixed with a drop-in — systemd reads `EnvironmentFile`
as root *before* dropping privileges, so the secret stays root-only:
`/etc/systemd/system/lescall-sentinel.service.d/10-telephony-env.conf`.

**Consumers of 8021** (all host or `network_mode: host`): `dashboard/server.py`
(`_fs_esl_exec`), `shared/sentinel.py` (`loopback_probe`). `socket_server.py`
is **outbound** ESL — FreeSWITCH dials *out* to port 9000 from the dialplan,
so it never authenticates. That is why inbound calls kept working throughout
the outage while every outbound one failed.

### Verification

```bash
docker exec lescall-api python -c "import sys;sys.path.insert(0,'/home/angelo/lescall');\
from shared.fs_esl import fs_api;print(fs_api('status',10))"
calling-agent/venv/bin/python -m shared.sentinel --probe
```
The probe originates from each DID **to itself** (caller ID = the same DID,
`&park()`, legs killed afterwards) — it rings no human and is marked
`billable=0`. Expect `"ok"` per DID. Confirmed 2026-07-31 11:01 UTC:
`918065953160` ok, `912264231897` ok — answered + audio played.

**Still open**: `deploy/freeswitch-event_socket.conf.xml` (the hardened
template) also moves the bind from `::` to `127.0.0.1` and applies
`loopback.auto` ACL. Only the password was rotated in this fix; installing
the full template is a separate, FS-restarting change.

## 24. WebSocket streaming STT REMOVED — back to batch (2026-07-31)

Owner request. The saaras:v3 WebSocket provider that ran in front of the
batch STT since 2026-07-29 is **gone from the code**, not just switched off.
The live STT path is the batch multipart POST to
`api.sarvam.ai/speech-to-text` per finalized VAD segment — exactly what ran
before the streaming canary.

**Two steps, both done:**

1. `ENABLE_STREAMING_STT: "true"` removed from `deploy/docker-compose.services.yml`
   (runtime service) and `sudo docker compose … up -d runtime`.
2. The code deleted, so the flag no longer exists at all.

**Deleted**: `app/voice/stt/sarvam_stream.py` (476 lines, the live provider)
and `app/voice/stt/sarvam_ws.py` (216 lines, an earlier attempt that nothing
imported).

**Rewired**:
- `conversation_call_bridge.py` — the `if ENABLE_STREAMING_STT` branch is
  gone; the router's primary is always `TrackingSarvamSTT`.
- `pipeline.py` — `ENABLE_STREAMING_STT`, `BARGE_KEEP_STREAM_AUDIO` and the
  `_streaming_stt` handle removed, along with every feed/finalize site: the
  two `feed_nowait` calls in the VAD loop, the mid-turn `turn_text` feed to
  the TurnManager (proxy tokens again), the `reset_turn()` calls on
  too-short-discard and barge-in flush, and the `close()` in `stop()`.
- `stt/router.py` — `last_covered_seconds` and `set_stream_language`
  removed; `_derive_confidence` lost its `covered_seconds` parameter.

**Part H of §21 partly went with it.** H1 (covered-seconds plausibility), H2
(barge keep-audio), H3 (late-segment stash) and H4 (consumed-episode
empties) were mitigations for streaming-specific failure modes — a finalize
spanning several VAD chunks, segments draining after their finalize. On the
batch path a transcript covers exactly the segment it was POSTed for, so
those conditions cannot arise and the code was dead weight, not protection.
**H5 (continuation hold) and all of Part C (capture) are untouched.**

**Deliberately kept**: `LLM_TOOLS_MODE: "on"` (independent of STT),
`VAD_ENGINE: "fsmn"`, `ENABLE_CONTINUATION_MERGE`, the STT language pin
(`_update_stt_lang_pin` feeds `_stt_lang` on the batch request — it was
never streaming-only), and `endpointer.py` itself. The endpointer now always
receives `("", False)` and so decides on acoustics alone; its semantic
hold/fast-complete profiles need mid-turn text that only a streaming
provider could supply.

**Cost of the rollback**: the ~300-800ms overlap saving is gone. Every turn
now pays the full batch POST after the endpoint fires.

**To re-introduce streaming**, restore the provider from git history
(`git log -- calling-agent/backend/app/voice/stt/sarvam_stream.py`) — it is
no longer a flag flip.

### Verification

Full backend suite **812 green** (was 817: 5 streaming tests and 3
`covered_seconds` tests removed, 3 replacements added). In the live
container:

```bash
docker exec lescall-runtime python -c "import sys;\
sys.path.insert(0,'/home/angelo/lescall/calling-agent');\
import backend.app.voice.stt.sarvam_stream"   # → ModuleNotFoundError
```
On the next live call, `[STT:Stream]` and `[PIPELINE] Streaming STT ACTIVE`
must be absent from ws_audio_receiver.log; STT latency per turn returns to
the batch profile.

## 25. Billing, plans & subscriptions (2026-07-31) — SHIPPED, ENFORCEMENT OFF

Implements `deploy/PLAN-razorpay-subscriptions.md`. Everything below is in the
repo and inert: **no Razorpay account exists yet**, and the enforcement switch
is `false`, so the platform behaves exactly as it did before. Turning it on is
one DB flag (§25.6).

### 25.1 What shipped

| Piece | Where |
|---|---|
| Schema | `shared/db/migrations/0013_billing.sql` (+ `.down.sql`) — 10 tables |
| Boot ALTERs | `agents.archived_at`, `usage_ledger.entitlement_check` in `shared/db/database.py` |
| Entitlements | `shared/billing/entitlements.py` — the ONLY thing the hot path reads |
| Gateway | `shared/billing/razorpay_client.py` — inert and honest when unconfigured |
| Checkout/recovery | `shared/billing/service.py` |
| Webhooks | `shared/billing/webhooks.py` |
| Receipts (GST) | `shared/billing/receipts.py` |
| Sync + dunning | `shared/billing/sync.py`, own supervised process in `workers_entry.sh` |
| Tenant API | `dashboard/billing_api.py` → `/api/billing/*` |
| Admin plane | `dashboard/admin_app.py`, compose service `admin` |
| SPA | `frontend/src/pages/Billing.tsx`, `Receipt.tsx`, `api/billing.ts` |
| Tests | `tests/test_billing_core.py` (35, pure), `tests/test_billing_lifecycle.py` (29, PG) |

### 25.2 The plans (from the published pricing page, 2026-07-31)

Prices are **GST-EXCLUSIVE**; `plans.price_paise` stores the base and the
gateway is charged `base × 1.18`.

| | Free | Pilot | Basic | Growth | Enterprise |
|---|---|---|---|---|---|
| Price ex-GST | 0 | ₹2,999 once | ₹14,999/mo | ₹49,999/mo | custom |
| Charged | 0 | ₹3,538.82 | ₹17,698.82 | ₹58,998.82 | — |
| Minutes | 30/mo | 500 / 7 days | 3,000/mo | 8,000/mo | custom |
| Agents | 1 | 3 | 10 | 25 | unlimited |
| Recordings | ✗ | ✓ | ✓ | ✓ | ✓ |
| AI summaries | ✗ | ✓ | ✗ | ✓ | ✓ |
| Transcripts | ✗ | ✗ | ✗ | ✓ | ✓ |
| Conversation analytics | ✗ | ✗ | ✗ | ✓ | ✓ |
| Automated follow-ups | ✗ | ✗ | ✗ | ✓ | ✓ |
| Additional minutes | ✗ | ✗ | ✗ | ✗ | ✗ |

> ⚠ **Both monthly tiers are above the RBI AFA ceiling.** AFA-free e-mandate
> auto-debit stops at **₹15,000 per transaction, GST included**. Basic charges
> ₹17,699 and Growth ₹58,999, so a **card or UPI-Autopay mandate will demand a
> customer approval every single month** on both. The plan document assumed
> only Growth was affected — it is both.
>
> Mitigation, already wired: `/api/billing/plans` returns `afa_free:false` for
> these tiers, the plan card says so, and checkout offers **eMandate/eNACH**
> first (no per-debit AFA up to ₹99,999). Enable eMandate on the Razorpay
> account before selling either tier, or the dunning path
> (`pending → halted → grace → Free`) becomes the normal monthly experience.

**Additional minutes are NOT sold** (owner decision, 2026-08-01). Exhausting
the allowance offers exactly one route: upgrade. Every plan is seeded
`topup_allowed=0`, so `/api/billing/topup` refuses for everyone and the SPA
renders no purchase control. The Orders rail behind it is built and tested —
re-enabling is a data change (`UPDATE plans SET topup_allowed=1,
topup_pack_minutes=…, topup_rate_paise=…`), not a migration.

> The published pricing page still advertises "Additional Usage ₹8/minute"
> (Basic) and "₹6/minute" (Growth). Those two lines should come off the page,
> or it promises something the product deliberately does not do.

Agent caps are the plan document's D2 defaults — the pricing page states no
numbers. Change them in `plans` and re-run `resolve_entitlements`; no code
change is needed.

### 25.3 Setting up Razorpay (when the account exists)

1. KYC-verified account → enable **Subscriptions** → Account Settings → GST
   details → enable cards, **UPI Autopay** and **eMandate/eNACH**.
2. Test keys into `/etc/lescall/billing.env` (see
   `deploy/etc-lescall-env.template`), then
   `sudo docker compose -f deploy/docker-compose.services.yml up -d`
   (**`up -d`, not `restart`** — env_file is only re-read on recreate).
3. Create the two recurring gateway Plans:
   ```bash
   docker exec -w /home/angelo/lescall lescall-api \
     python -m shared.billing.plans_bootstrap          # dry run first
   docker exec -w /home/angelo/lescall lescall-api \
     python -m shared.billing.plans_bootstrap --apply
   ```
   **Razorpay Plans are immutable forever.** A price change means a new plan id
   and re-authorising every subscriber's mandate. Pilot and top-ups need no
   Plan — they are Orders created per purchase, so their price can change freely.
4. Webhook → `https://agent.lescall.com/api/billing/webhook/razorpay`, with its
   **own secret** (≠ API secret, and different again in live). Subscribe to:
   all `subscription.*`, `order.paid`, `payment.captured`, `payment.failed`,
   `refund.processed`, `invoice.paid`.
5. Install the nginx rate limit + 256k body cap for that location
   (`deploy/nginx-agent.lescall.conf`), then `sudo nginx -t && sudo systemctl reload nginx`.

### 25.4 GST invoicing

Ours is the **tax invoice**; Razorpay's auto-invoice is only a payment receipt
(no customer GSTIN, no SAC, no CGST/SGST split). Set `PLATFORM_GSTIN`,
`PLATFORM_LEGAL_NAME`, `PLATFORM_ADDRESS`, `PLATFORM_STATE_CODE` in
`/etc/lescall/billing.env` — `/api/billing/health` reports `invoicing_ready`
and names the missing field.

- Numbering is a consecutive per-FY series (`LC-26-27-00001`), allocated with
  `SELECT … FOR UPDATE` **inside the payment transaction**, and only when the
  payments INSERT actually inserted — so a webhook retry cannot burn a number.
- Credit notes (`LCCN-…`) are issued automatically on a full refund. An issued
  invoice is never deleted.
- The customer's details are **snapshotted onto the payment row at issue**, so
  editing a billing profile later cannot rewrite an invoice already issued.
- GSTIN is validated server-side including the mod-36 check digit, and the
  place of supply is **derived from it** — never accepted as a separate field.

### 25.5 When a payment goes wrong

Every case below is handled; this is where to look when a customer asks.

| Symptom | What happens | Where |
|---|---|---|
| "I paid, nothing happened" | tenant presses **I paid but nothing happened** on `/billing` → polls the gateway → recovers | `POST /api/billing/reconcile` |
| Customer closed the tab after paying | webhook opens the window anyway | `webhooks._settle_order` |
| Webhook lost / not configured | sync recovery pass every 15 min | `sync.pass_recover_payments` |
| Card declined | attempt `failed` + notification; plan untouched | `webhooks._on_payment_failed` |
| Renewal fails | `pending → halted → 72h grace → Free`, and the gateway sub is **cancelled** so they can subscribe again | `sync.pass_grace_expiry` |
| Paid twice | idempotent on `razorpay_payment_id`; one receipt only | `service.record_payment` |
| Amount ≠ plan price | quarantined, nothing granted, alarm | `webhooks._record_subscription_charge` |
| Webhook replayed | deduped on `event_id`, one effect | `webhooks.ingest` |
| Webhook for an unknown id | recorded + alarmed, never granted | `webhooks._unknown` |
| Refund | payment flipped, credit note, minutes recomputed | `webhooks._on_refund` |
| Handler crashed | event left unprocessed, replayed from the stored raw payload | `sync.reprocess_stuck_events` |

Operator views: `GET /admin/api/billing-events?unprocessed_only=1`,
`GET /admin/api/tenants/{owner}` (subscriptions + payments + **attempts**), and
`POST /admin/api/tenants/{owner}/reconcile`.

Every checkout attempt — including the ones that never become money — is a row
in `payment_attempts` with `failure_step` / `failure_code` / `failure_reason`.
That table, not `payments`, is what answers "why did their card fail".

### 25.5b Rollout record — what actually happened (2026-08-01)

Deployed P0–P5 live. **Enforcement is OFF**; flipping it is §25.6.

Three things bit during the rollout; all are fixed, and all are worth knowing:

1. **The 0013 staff seed did not survive.** It wrote
   `entitlements.plan_code='enterprise'` with no `overrides`, and
   `resolve_entitlements()` rebuilds the row wholesale from the
   `subscriptions` history — these accounts have no subscription, so the first
   resolve correctly reset all three staff tenants to Free. Fixed by
   **migration 0014**, which writes the grant as an `overrides` JSON (the
   supported mechanism, and what the admin plane uses) and additionally gives
   every other pre-existing account an explicit Free row. Verified stable
   across repeated re-resolution.
   *Rule: to grant a plan without a gateway subscription, write `overrides`.
   A bare `plan_code` is derived state and will be overwritten.*

2. **`/run/lescall-admin` is tmpfs** — it vanishes on reboot and the admin
   container then crash-loops on `PermissionError`. Made persistent with
   `/etc/tmpfiles.d/lescall-admin.conf`.

3. **A test fixture had written `billing_enforce` into the PRODUCTION settings
   table** (found stamped `by: "pytest restore"`). Cause: `enforcement_on` and
   `_use_test_db` were both autouse and function-scoped, so pytest was free to
   run the former first, while `LESCALL_DB_URL` still pointed at production.
   Fixed by making `enforcement_on` take `_use_test_db` as an explicit
   dependency. Any future billing fixture that writes MUST do the same.

Verified live after deploy: all six containers healthy · billing endpoints 401
without a session · `…/webhook/razorpay` 503 (no secret) while
`…/webhook/razorpay-test` is 401, proving `_OPEN_EXACT` · checkout answers a
clean 503 rather than crashing · every pre-existing surface still 200 ·
9000/9001 bound to 127.0.0.1 · admin reachable only over the UNIX socket
(TCP refused) · **loopback call probe OK on both DIDs, answered + audio**, and
those calls metered with `entitlement_check='ok'` and released their
reservations.

### 25.5c Open signups + the telephony gate (2026-08-01)

`LESCALL_ALLOWED_EMAILS=*` — any verified Google account may register again.

What a brand-new account gets: **Free** (30 min/month, 1 agent) and **no
telephony**. They can build and test an agent in the dashboard; they cannot
register a SIP trunk or attach a DID. Connecting a real number starts at Pilot.

Migration **0015** adds `plans.telephony_enabled` / `entitlements.telephony`
(Free 0, Pilot/Basic/Growth/Enterprise 1) and grandfathers anyone who already
owned a trunk or number — via `overrides`, so it survives re-resolution.

Enforced by `telephony_api.paid_owner`, applied to the six MUTATING endpoints:

| Gated (402) | Open (200) |
|---|---|
| `POST /api/trunks` · `DELETE /api/trunks/{id}` · `POST /api/trunks/{id}/apply` | `GET /api/trunks` |
| `POST /api/numbers` · `PATCH /api/numbers/{id}` · `DELETE /api/numbers/{id}` | `GET /api/numbers` · `GET /api/trunks/{id}/instructions` · `GET /api/telephony/server-info` |

Reads stay open so the SPA can render a locked panel that shows what the plan
unlocks (`TelephonyLocked` in `frontend/src/pages/Telephony.tsx`); a 402 on the
list call would leave it nothing to draw but an error.

> **This gate deliberately ignores `enforcement_enabled()`.** That switch stages
> quota rollout for tenants who already have plans. This one keeps strangers off
> the carrier edge and has to bite the moment signups reopen. Everything else in
> §25.9 still no-ops while enforcement is off.

Every account now also gets an **explicit** entitlements row at first login
(`auth._upsert_user` → `resolve_entitlements`), so the P6 discovery query keeps
returning zero.

⚠ **With enforcement still OFF, a new signup gets unlimited agents and
unlimited Copilot turns.** The Copilot's model quota is shared with live calls,
so that is the remaining exposure from open signups — see §25.6.

### 25.5d BYON in containers — three gaps found live (2026-08-01)

A tenant's trunk "Apply" 500'd. The trunk and number saved fine; only the final
step failed. Three independent containerization gaps, all now fixed:

1. **`/etc/freeswitch` was not mounted into `lescall-api`.** Apply writes the
   generated gateway/dialplan XML there. Fixed: bind mount + `group_add: ["994"]`
   (the dirs are `freeswitch:freeswitch drwxrwxr-x`).
2. **`_fs_reload()` shelled out to `fs_cli`**, which is not installed in the app
   containers — the same gap `shared/fs_esl.py` exists for. Every reload raised
   FileNotFoundError, so `reloaded` was False and apply reported
   `status: "error"` even with the XML written. Now uses the event socket,
   falling back to `fs_cli` for host-side callers.
3. **Generated files were written 0644**, so only the tenant who first claimed a
   DID could ever re-apply it. When a DID moves between tenants the old
   `ai_inbound_<did>.xml` is still owned by `freeswitch` and cannot be
   overwritten. Now written 0664 via `freeswitch_config._write_shared()`.

**Then the call itself failed with SIP 403** — carrier-side, not ours. The
diagnostic that pins it down: a 403 with **no preceding 407/401** means the
carrier rejected the SOURCE, not the credentials. On Plivo Zentrunk that is the
**termination** side: the trunk needs an IP ACL containing this server's egress
IP (`35.207.225.237/32`). Origination (`<ip>:5080`) is a separate setting and
configuring only that gives working inbound and 403 on every outbound.

Also added: **`PATCH /api/trunks/{id}`**. Previously a trunk could only be
created, applied or deleted, so fixing a wrong SIP domain or adding credentials
meant delete-and-recreate — which drops the DIDs and their agent bindings.
`name` stays immutable (it is the FreeSWITCH gateway name and filename), and a
masked `***` password is ignored rather than wiping the stored credential.

### 25.5e DID routing isolation — cross-tenant leak (2026-08-01)

`agents.is_active` is a SINGLE GLOBAL FLAG that every tenant's Activate button
writes. Four tenants had activated agents. Two paths could therefore hand a
call to the wrong tenant:

* a **registered DID with no agent bound** fell through to the global "active
  agent" lookup — so a customer calling tenant A's number would have been
  answered by tenant B's agent, speaking B's company name, billed to B;
* the last-resort lookup itself ordered by `updated_at DESC` across all owners.

Fixed in `conversation_call_bridge.py`:

* a registered-but-unbound DID now returns the `UNROUTED_AGENT` sentinel, which
  `_build_agent_stack` maps to the neutral fallback persona
  (`agent_health='unrouted'`), and `_did_owner()` still attributes the call to
  the number's tenant so quota and ledger land correctly;
* `_resolve_active_agent_slug()` is scoped to `LESCALL_DEFAULT_OWNER` — it
  exists for the founding tenant's pre-tenancy numbers and must never select
  another tenant's agent.

Pinned by `tests/test_did_routing_isolation.py`. That file imports the bridge
**inside a fixture**: a module-scope import pulls in the audio stack, whose
threads segfault the interpreter at shutdown (tests pass, then exit 139).

### 25.5f Manual invoices — money collected outside the gateway (2026-08-01)

Cash, NEFT/RTGS, cheque, or an account put on credit. `shared/billing/invoices.py`
+ the admin console at `/admin/tenant/<email>` → **Issue an invoice**.

Owner-approved design decisions:

| Decision | Choice |
|---|---|
| Unpaid ("yet to be paid") | **Activates the plan immediately**, balance shows as outstanding |
| GST numbering | Unpaid invoices take a **real number** in the `LC-<fy>-NNNNN` series on issue; cancelled by credit note, never deleted |
| Amount | Pre-filled from the plan, **editable** (negotiated + Enterprise deals) |
| Partial payments | Not in v1 — issue two invoices for a 50/50 split |

Rules the module enforces, each for a reason:

* The invoice number comes from the **issue date's** financial year, not
  today's. Back-dating across a FY boundary is refused outright — it would
  insert a number into a series that has already been filed.
* Future-dated invoices are refused.
* The plan grant is written into `overrides`, never as a bare `plan_code` —
  entitlements are derived state and a bare plan_code does not survive the next
  `resolve_entitlements()` (see 0014).
* `payments.razorpay_payment_id` is UNIQUE and is the gateway idempotency key,
  so a manual invoice gets a synthetic `manual-<hex>` value rather than NULL
  (a nullable UNIQUE column admits unlimited NULLs).
* The invoice line names the **plan actually sold**, captured in the snapshot
  at issue — it previously said "Enterprise" for every manual invoice.

Operator flow: **Issue** → (if on credit) **Mark paid** with method + UTR/cheque
ref → or **Void**, which allocates an `LCCN-<fy>-NNNNN` credit note and requires
a reason. `/admin/invoices` is the collections view (unpaid, oldest first,
overdue flagged).

Tenant side: the same invoice appears in their **Billing → Payment history**
with `paid` / `payment due` / `cancelled`, an outstanding-balance banner, and
the printable GST invoice at `/billing/receipt/<id>`.

Migration **0016** adds `issued_at`, `due_date`, `paid_at`, `payment_reference`,
`created_by`, `voided_at`, `void_reason`, and extends `payments.status` with
`due` and `void`.

> ⚠ **Invoicing is BLOCKED until the supplier identity is set.** `issue()`
> refuses while `PLATFORM_GSTIN` / `PLATFORM_LEGAL_NAME` / `PLATFORM_ADDRESS` /
> `PLATFORM_STATE_CODE` are blank in `/etc/lescall/billing.env`, naming the
> missing field. A document with no GSTIN or place of supply is not a tax
> invoice — it burns a number out of the GST series for something the customer
> cannot claim credit against and we cannot file.
>
> That guard was added after the fact: three test invoices (₹58,998 · ₹14,160 ·
> ₹58,998) reached a live tenant's Billing page during this feature's own
> testing. They were hard-deleted and `receipt_counters` reset to 0 for FY
> 26-27, so the first REAL invoice is `LC-26-27-00001`. Hard delete rather than
> the usual credit-note reversal was correct **only** because the platform
> GSTIN had never been configured: nothing had been filed, so no series
> existed to protect, and voiding would have left the customer looking at three
> invoices plus three credit notes for money they never paid. Once a real
> invoice exists, the rule is absolute — cancel with a credit note, never
> delete.

### 25.5g Admin console — operations surfaces (2026-08-01)

`dashboard/admin_ops.py`. Everything below previously needed SSH, `psql` or
`docker ps`. Ten pages total; nav across the top.

| Page | Answers |
|---|---|
| `/admin` | tenants — search, plan, status, minutes, agents, calls, telephony |
| `/admin/tenant/<email>` | everything about one account + plan/lock/invoice controls |
| `/admin/health` | is the platform up — listeners, live calls, freshness, disk, billing integrity |
| `/admin/keys` | **Sarvam key pool** — the highest-impact number on the platform |
| `/admin/calls` | cross-tenant call browser with billing attribution |
| `/admin/agents` | every agent, owner, orphans, legacy active flag |
| `/admin/audit` | who did what, including admin reads |
| `/admin/invoices` | outstanding balances, oldest first |
| `/admin/payments` | all money in |
| `/admin/events` | gateway webhook events, stuck ones flagged |

**Why the key pool page matters most.** Sarvam supplies STT and TTS for every
call. When the pool is exhausted, *every tenant's calls go silent* — and since
roles were flattened, `/api/sarvam/*` is 403 for every session, so there was no
UI for it anywhere. Current state: 13 usable, 11 already exhausted. Rotation is
automatic on HTTP 429; the page exists to top the pool up BEFORE that runs dry.

Two traps that bit while building it, both worth remembering:

* `api_key_pool.json` is `{"pool": [...]}`, not a bare list, and has THREE
  other consumers — `sarvam_key_watchdog.py`, `key_rotator.py` (in the live
  call path) and `server.py`. The admin page therefore calls
  `server._load_api_key_pool` / `_save_api_key_pool` rather than writing the
  file itself; a bare-list write would have silently broken rotation.
* `sarvam_dead_keys.json` wraps its list under `"dead"`. Reading the wrapper as
  the list reported **1** exhausted key when there were **11** — a number an
  operator would have acted on.

**The admin plane deliberately gets no new credentials.** It reads ports by
connecting to them, live state from the repo mount, and everything else from
the database. It does NOT take `FS_ESL_PASSWORD`: it can already change plans
and issue invoices, and adding "can originate calls" to a leaked `ADMIN_TOKEN`
buys only a channel count that port 8021 plus recent call activity already
imply.

### 25.6 Turning enforcement on (P6)

**Run the discovery query first.** Every pre-existing tenant must already have
an entitlements row, or they drop to Free (30 min, 1 agent) and block instantly:

```sql
SELECT DISTINCT owner FROM (
  SELECT email AS owner FROM users UNION SELECT owner FROM agents
  UNION SELECT owner FROM usage_ledger UNION SELECT owner FROM calls) t
WHERE owner <> '' AND owner NOT IN (SELECT owner FROM entitlements);
-- MUST return zero rows.
```

Migration 0013 seeds `tech@`, `angelo@` and `alwynantonyben@dhronai.com` as
Enterprise/unlimited. Anyone else must be granted a plan from the admin plane
first.

```bash
# apply the schema (additive; enforcement still off)
docker exec -w /home/angelo/lescall lescall-workers python -m shared.db.migrate --status
docker exec -w /home/angelo/lescall lescall-workers python -m shared.db.migrate

# flip enforcement (admin plane, audited, reason mandatory)
curl --unix-socket /run/lescall-admin/lescall-admin.sock \
  -X POST http://local/admin/api/enforcement \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled": true, "reason": "P6 enforcement go-live, owner approved"}'
```

Verify it reached **all four** services — the runtime container reads the same
DB setting but caches it for 30s, and inbound enforcement lives there:

```bash
curl -s localhost:8080/api/health | jq .billing
```

`billing.fail_open` must stay empty. A non-empty value means calls are being
admitted without a verifiable entitlement because the billing DB is erroring.

**Rollback** is the same call with `"enabled": false`.

### 25.7 Sync worker

Own supervised process inside `lescall-workers` (never the 5-minute tick — N
sequential gateway calls would make tick duration a function of Razorpay's
latency, and the sentinel restarts the container when `reconcile:` vanishes for
20 minutes).

```bash
docker logs lescall-workers 2>&1 | grep 'billing sync'
docker exec -w /home/angelo/lescall lescall-workers python -m shared.billing.sync --once
```

Alarms are logged as `[BILLING-ALARM]` — the sentinel and GCS log shipping
already watch that stream. They are deliberately not bell-only.

### 25.8 Admin plane

See `deploy/etc-lescall-env.template`. Refuses to start without a ≥32-char
`ADMIN_TOKEN` and refuses any bind that is not the UNIX socket or loopback.
No nginx vhost, no DNS, no firewall opening.

It binds BOTH interfaces: the UNIX socket always, plus loopback TCP when
`ADMIN_TCP_ENABLED=true`. The socket is the security-relevant one (an HTTP-URL
SSRF cannot address a UNIX socket at all); the TCP port exists only so a
browser at the far end of an SSH tunnel can render the page.

```bash
sudo install -d -m 0770 -o angelo -g angelo /run/lescall-admin   # + tmpfiles.d
sudo docker compose -f deploy/docker-compose.services.yml up -d admin
```

**From the VM (scripts, no browser) — the socket:**

```bash
TOK=$(sudo grep -oP '(?<=^ADMIN_TOKEN=).*' /etc/lescall/admin.env)
curl --unix-socket /run/lescall-admin/lescall-admin.sock \
     http://local/admin/api/health -H "Authorization: Bearer $TOK"
```

**From your laptop (browser) — SSH tunnel:**

```bash
ssh -L 8090:127.0.0.1:8090 angelo@35.207.225.237      # keep this open
```
then open <http://127.0.0.1:8090/admin>, paste the token into the sign-in form.

The form POSTs the token in the request BODY (never a query string, so it stays
out of access logs, shell history and Referer) and sets an 8-hour HttpOnly,
SameSite=strict cookie containing a **hash** of the token — so a stolen cookie
cannot be replayed as an API bearer token, and rotating `ADMIN_TOKEN`
invalidates every session for free.

**The cookie authorizes GET only.** Every mutation still requires the
`Authorization: Bearer` header, which is what keeps CSRF structurally
impossible: while the tunnel is open, any page the operator visits could POST
to 127.0.0.1:8090, but it cannot set that header. The HTML UI is read-only by
design, so it loses nothing; changes go through `curl` against `/admin/api/*`.

Turn the TCP bind off again with `ADMIN_TCP_ENABLED=false` + `up -d admin`;
the socket keeps working.

Reads are audited as well as writes: if the token leaks, reads are what an
attacker uses first. Every mutation needs a `reason` of ≥10 characters, and
`pilot-reset` additionally needs `confirm_owner` equal to the path owner so a
body-less SSRF POST cannot trigger it.

### 25.9 Enforcement points

`E1` manual dial · `E2` campaign (start **and** per contact) · `E3` autodialer ·
`E4` automation callback · `E5` number test call · `E6` inbound bridge ·
`E7` agent creation · `E8` autocall staging · `E9` copilot chat ·
`E10` copilot try-agent · `E11` document extraction · `E12` per-tier feature
flags at the API.

The grep gate — every `_fs_esl_exec(_fs_originate_cmd(...))` site must be
preceded by a check:

```bash
python3 - <<'PY'
src = open('dashboard/server.py').read().split('\n')
for i, l in enumerate(src):
    if '_fs_originate_cmd(' in l and 'fs_esl_exec' in l and not l.strip().startswith('#'):
        assert '_billing_check_dial' in '\n'.join(src[max(0,i-60):i]), f'UNGATED line {i+1}'
print('grep gate OK')
PY
```

`shared/sentinel.py`'s loopback probe is exempt by design: it is a synthetic
self-test marked `billable=0` that rings no human.

---

## 26. Share an agent by link — IMPORT makes an independent copy (2026-08-01)

A tenant hands someone a URL; the recipient signs in and imports a **copy** of
that agent into their own account. The two records are unrelated from the
moment of import: separate slug, separate `agent_id`, separate revision
history, separate owner. Editing one never changes the other, and archiving
one never touches the other.

### 26.1 Surfaces

| Where | What |
|---|---|
| Build → Agents card | Share icon (top-right of the card) → link dialog |
| `https://agent.lescall.com/s/<token>` | the link that gets sent |
| `/build/agents?import=<token>` | where the link lands; opens the import popup |
| `dashboard/agent_share_api.py` | `/api/share` (create/read) · `/api/share/{token}` (preview, revoke) · `/api/share/{token}/import` |
| `frontend/src/components/ShareAgent.tsx` | both dialogs |
| migration `0017_agent_share` | `agent_shares`, `agent_share_imports` |

### 26.2 The unit is the FAMILY

One link carries every direction variant the agent has (`x-inbound` +
`x-outbound`), because the plan cap counts families and the card in the UI is
a family. One import therefore consumes exactly one agent slot.

### 26.3 "The same agent cannot be shared by two users"

Three facts, no extra column:

1. `agents.slug` is globally UNIQUE and each row carries exactly one `owner`.
2. `POST /api/share` verifies every slug in the family is owned by the caller.
3. `idx_agent_shares_live` — `UNIQUE (owner, family) WHERE revoked_at IS NULL`
   — allows one live link per agent. Pressing Share twice returns the SAME
   token rather than minting a second, separately-revocable link.

An import mints a brand-new family, so two accounts never point at one row.

### 26.4 What travels, and what deliberately does not

Travels: `agent.yaml` (behaviour, personality, stages, knowledge base) and
`documents/` including the `.npy` embeddings — otherwise the copy references
knowledge it does not have and reads as broken.

Does **not** travel:
* `routing.yaml` — the sharer's DID binding. Copying it would point the
  importer's new agent at someone else's phone number.
* `bookings.json` — the sharer's live appointments, i.e. their customers.
* `revision` / `updated_at` / `updated_by` — a copy claiming "revision 47,
  updated_by someone@else.com" makes the importer's first edit look like a
  rollback.
* `is_active` — imported rows are forced to 0. `agents.is_active` DEFAULTS to
  1 and `conversation_call_bridge` picks the most recently updated `is_active`
  row per owner for calls that match no DID binding, so without this, importing
  an agent to look at it would silently take over answering that account's
  unbound calls.

`agent_id` is **reminted** (`<new-slug>-v1`, uniqueness-checked against every
`agent.yaml` on disk). `documents/store.get_folder_by_agent_id()` resolves a
document folder by scanning for the FIRST matching `agent_id`, so two agents
sharing an id means one tenant's RAG lookups can land in another tenant's
documents.

### 26.5 Refusals the recipient sees

| Situation | Result |
|---|---|
| At the plan agent cap | popup still renders the agent, Import replaced by **See plans** (402 `cap_agents` on the API) |
| Own link | 409 "this is your own agent" |
| Already imported (copy still live) | 409 + a link to their copy |
| Revoked / expired | 410, with which one it was |
| Source archived or deleted | 410 |
| >20 imports in 24h | 429 (abuse ceiling, `LESCALL_MAX_IMPORTS_PER_DAY`) |

Cap and already-imported are reported in the preview BODY (`can_import:false`
+ `reason`) rather than as HTTP errors, because the popup has to render the
agent's details alongside the explanation.

### 26.6 Atomicity

`agent_share_imports` has `UNIQUE (share_id, importer)` and the row is
**reserved before any folder is written**. That is what makes a double-clicked
Import button, a reloaded tab and two browser windows converge on one copy
instead of three. Any failure mid-import rolls back every folder, every
ownership row and the reservation together — half an imported twin pair is
worse than none, because a number could bind to the survivor and run the wrong
direction's prompts. Re-importing is allowed once the earlier copy has been
archived or deleted (the stale row is dropped inside the same transaction).

### 26.7 Deep links now survive login — platform-wide

A share link's entire payload is the token in its URL, and the nginx
`auth_request` wall used to throw the path away:
`error_page 401 → @lescall_login → return 302 /login`. Everyone landed on the
homepage after signing in and the invitation silently evaporated.

Three coordinated changes:

* `deploy/nginx-agent.lescall.conf` → `return 302 /login?next=$request_uri;`
* `auth.safe_return_path()` — accepts same-origin ABSOLUTE PATHS only. `//host`,
  `https://host`, backslashes, `/login*` and `/auth/*` are rejected, so `next`
  cannot be turned into an open redirect.
* `/login` stashes it in the short-lived httponly `lescall_return_to` cookie;
  `/auth/callback` redirects there instead of `/`.

Share links are `/s/<token>` — one path segment, no query string — precisely
because stock nginx has no URL-encoder, so a destination carrying its own `&`
would be truncated in `$request_uri`.

**Installing the nginx change** (root):

```bash
sudo cp deploy/nginx-agent.lescall.conf /etc/nginx/sites-enabled/agent.lescall
sudo nginx -t && sudo systemctl reload nginx
```

Without it, share links still work for anyone already signed in; a logged-out
recipient lands on the dashboard homepage and has to re-open the link.

### 26.8 Deploy

```bash
set -a; . deploy/.pg_env; set +a
./calling-agent/venv/bin/python -m shared.db.migrate --status   # 0017 should show pending
./calling-agent/venv/bin/python -m shared.db.migrate            # apply it
sudo docker restart lescall-api               # picks up the new router
docker compose -f deploy/docker-compose.services.yml up -d --build spa
```

Deployed 2026-08-01. Two traps hit on the way, recorded so they are not hit twice:

* The backend is the **`lescall-api` container** (bind-mounted, host network,
  port 8080). The old `lescall-dashboard` **systemd unit is disabled and must
  stay stopped** — starting it spawns a second `server.py` that loses the race
  for port 8080 and crash-loops under systemd's restart policy until stopped.
* `deploy/nginx-agent.lescall.conf` ends with a `default_server` 444 catch-all.
  `/etc/nginx/sites-enabled/lescall` (the legacy proxy-everything vhost)
  **claimed `default_server`**, so installing the file verbatim fails `nginx -t`
  with a duplicate default server. Resolution (2026-08-01, owner-approved): the
  vhost was installed with the catch-all block stripped, then the catch-all was
  installed separately as `/etc/nginx/sites-enabled/000-lescall-default` and the
  legacy vhost removed (backup: `/etc/nginx/lescall.legacy-vhost.bak-20260801`).
  Bare-IP / stale-DNS requests on :80 now get the connection closed with no
  response; only `Host: agent.lescall.com` is served. Note the 444 block covers
  **port 80 only** — on 443 the agent.lescall server is the sole listener and
  therefore still answers any SNI/Host with its own cert (pre-existing).

Until 0017 is applied every `/api/share` route answers **503 "Agent sharing is
not enabled on this server yet"** rather than a 500 about a missing relation.

### 26.9 Tests

`calling-agent/backend/tests/test_agent_share.py` (34). Pure tier covers the
slug/agent_id/spec algebra and the open-redirect guard; the DB tier runs the
full create → preview → import path against `lescall_test` with `agents/`
redirected at a tmp tree. The load-bearing ones: independence after import,
`is_active` stays 0, routing/bookings excluded, double-import, re-import after
archive, revoked/expired/deleted-source, name-collision walk, cap refusal, and
"a failed import leaves nothing behind".

---

## 27. LLM cost telemetry + prompt-cache readiness (2026-08-11) — AUDIT + SHIPPED

Before this, the platform measured **half** its AI bill. `credit_tracker.py`
knew every call's Sarvam credits to six decimals and recorded **zero LLM
tokens**, so "what does a minute cost?" was unanswerable for the model that
does the talking. This section is the audit that closed that gap and what it
found.

### 27.1 The measured cost of a call-minute

All figures measured, not modelled. Sarvam from 46 days of `credit_log/*.jsonl`
(3,375 real calls), turn rate from `call_turns` (106 calls / 196 min), token
counts from the live endpoint.

| Component | Rate | Measured usage | ₹/min |
|---|---|---|---|
| Sarvam STT `saarika:v2.5` | ₹30/hr, per-request ceil | 15–20 billed-sec/min | **0.14** |
| Sarvam TTS `bulbul:v3` | ₹3/1K chars | ~650 chars/min | **1.95** |
| Whisper fallback | ₹0.53/min | ~0 (never fires) | 0.00 |
| LLM `gemini-3.5-flash` | $1.50/$9.00 per 1M | 3,311 in / 25 out per turn × 4.8 turns/min | **2.20**¹ |

¹ **at list price. Actual LLM spend today is ₹0 — see 27.3.**

Per-day Sarvam is stable at ₹1.93–2.19/min across 29 Jul – 8 Aug. **TTS is 93%
of the Sarvam bill**; STT is noise.

### 27.2 Where the LLM money is: the prompt, re-sent every turn

Input is ~98% of the LLM bill. One turn = 3,311 input tokens vs 25 output. The
system prompt is 12,724 characters ≈ 3,236 tokens and it is re-sent on all 4.8
turns/minute.

`_build_system_prompt` therefore emits its blocks in three bands — STATIC,
PER-CALL, PER-TURN — so a provider prefix cache can serve the stable part.
Measured across the golden fixtures (6 stages × 3 languages × 2 directions):

    86% of the prompt is byte-identical across every stage
    = 2,708–2,946 tokens, diverging exactly at "━━ CALL STATE ━━"

That property is the entire precondition for prompt caching and nothing else in
the repo checked it — a timestamp in the identity block would silently take it
to zero. `tests/test_prompt_cache.py` now pins it, and `prompt_cache.
prefix_fingerprint()` logs a hash of the static band on every turn so a break
is visible in production the turn it happens.

### 27.3 The blocker: the Gemini key is on the FREE TIER

Probed live 2026-08-11 against `LLM_API_KEY`:

| Probe | Result |
|---|---|
| 6 consecutive byte-identical 3,226-token prefixes | `cachedContentTokenCount = 0` every time |
| `cachedContents.create`, all 4 fleet models | `429 TotalCachedContentStorageTokensPerModelFreeTier limit exceeded: limit=0` |
| `generateContent` burst | `429 … quotaValue: '5'` — **5 requests/minute** |

Three consequences, in order of severity:

1. **5 RPM is a hard scaling ceiling.** One call consumes 4.8 LLM turns/minute.
   The platform can serve roughly **one concurrent call** before turns start
   429ing. Today that is invisible because volume is low
   (`llm_fallback_turns > 0` on 3 of 419 calls in 30 days).
2. **Prompt caching is unavailable by policy, not by misconfiguration.**
   Implicit and explicit both. No application code can change `limit=0`;
   it needs billing enabled on the Google Cloud project.
3. **LLM spend is currently ₹0.** The ₹2.20/min above is what the *same
   traffic* costs the day billing is switched on — which it must be, per (1).

### 27.4 What enabling billing is worth

Measured token counts through the rate card in `shared/ai_pricing.py`:

| | per turn | per call-minute | platform total |
|---|---|---|---|
| no prefix cache | ₹0.4586 | ₹2.20 | ₹4.29/min |
| 2,800-token prefix cached | ₹0.1247 | ₹0.60 | **₹2.69/min** |

**−73% of the LLM bill, −37% of the platform bill**, ₹1,603 saved per 1,000
call-minutes. Sarvam's ₹2.09 is unaffected.

Order of operations matters: enable billing → implicit caching begins serving
the already-ordered prefix with no code change. Do not reorder the prompt to
chase this; it is already ordered.

### 27.5 What was deliberately NOT built

Measured first, then declined — each of these is a standard optimisation that
this codebase's data does not support:

| Not built | Why — measured |
|---|---|
| Response/semantic cache | 2,621 agent utterances over 45 days: **85.5% are unique**. Nothing to hit, and replaying a stale reply into a live call is a correctness hazard for a ~0% gain. |
| Wider TTS cache | Only **7.5%** of agent characters are exact repeats, and the top phrases are canned lines the disk cache in `tts_sarvam.speak_cached` already serves. Ceiling is ~₹0.15/min before dedupe, less after. |
| Redis | Nothing needs it. There is no Redis in this deployment (Postgres + a disk cache), and the only genuinely cacheable artefact is the provider-side prompt prefix, which lives at the provider. |
| Explicit `cachedContents` integration | The compat layer cannot reference a cache, and the tier returns `limit=0`, so the code path could not be tested. Untestable code on the live call path is worse than no code. |

### 27.6 What shipped

| File | Change |
|---|---|
| `shared/ai_pricing.py` | NEW. Central LLM rate card (USD/1M in · cached_in · out), `AI_PRICING_FILE`/`AI_PRICING_OVERRIDES`, USD→INR. Re-exports the Sarvam card so one import prices a whole call. Unpriced model → cost `None`, never `0.0`. |
| `app/voice/prompt_cache.py` | NEW. `split_prompt()`, `prefix_fingerprint()`, `estimate_tokens()`, and `probe_cache_capability()` — which turns the free-tier finding into one startup log line instead of a silent leak. |
| `app/voice/credit_tracker.py` | `record_llm()` + per-call/session LLM accumulators; `llm_event` rows in the same JSONL ledger as speech. |
| `app/voice/conversation.py` | `stream_options={"include_usage": True}` on the streaming turn; usage captured and recorded; capability probe fired from `warmup()`. |
| `conversation_call_bridge.py` | Passes the existing `call_id_provider` into the router so tokens and speech land on one ledger row. |
| `credit_report.py` | LLM section: requests, models, in/out tokens, **prefix cache hit-rate**, cost, and a FLOOR-ONLY warning when a model is unpriced. |
| `tests/test_prompt_cache.py` | NEW, 35 tests. Prefix stability per fixture group, provider-minimum check, fingerprint semantics, tier-variant mispricing, unpriced≠free, tracker accumulation. |

### 27.7 Two traps this cost, for whoever touches it next

**The usage chunk is provider-shaped.** OpenAI appends a final chunk with an
EMPTY `choices` list carrying `usage`; **Gemini's compat layer hangs `usage`
off a chunk that also has content.** Reading it only from empty-choice chunks
recorded zero requests against a live Gemini call. Both shapes are handled —
and the empty-`choices` guard is mandatory regardless: unguarded,
`chunk.choices[0]` raises IndexError, the outer handler swallows it, and the
caller hears the fallback line instead of the reply on *every* turn.

**Tier suffixes are not release suffixes.** `gemini-3.5-flash-lite`
`.startswith("gemini-3.5-flash")` is a naming coincidence; pricing it by naive
prefix match overstated it 6×. `llm_rate()` matches segment-wise and rejects a
match when an extra segment names a tier (`lite`, `mini`, `pro`, …).

### 27.8 Feature flags

    ENABLE_LLM_USAGE_TELEMETRY=true    # stream_options + ledger writes
    ENABLE_PROMPT_CACHE_PROBE=true     # startup capability probe
    PROMPT_CACHE_MIN_TOKENS=1024       # provider prefix floor used by the CI guard
    AI_PRICING_FILE= / AI_PRICING_OVERRIDES=   # rate card without a code change
    AI_USD_INR=88.33                   # matches WHISPER_PRICE_PER_MIN_INR

Telemetry fails open everywhere: a provider that rejects `stream_options` gets
one retry without it and the flag flips off for the process (it must never
demote the turn to the fallback model); a tracker that cannot import is a
no-op; the probe is a detached task that cannot delay a call.

### 27.9 LIVE PROOF (2026-08-11, `docker restart lescall-runtime`)

Deployed and verified with three full simulated calls through the real
pipeline (`callsim`, real Sarvam + real LLM). What the live runs established:

**The capability probe fires in production**, one line per process start:

    [PROMPT-CACHE] gemini/gemini-3.5-flash: provider caching UNAVAILABLE —
    free tier, cache quota is zero (…limit=0…) — enable billing on the Google
    Cloud project. The static prompt prefix is re-billed fresh on every turn.

**The cache-ordered prompt WORKS — measured, on a live call.** The primary's
free-tier day quota was spent, so every turn fell back to `gpt-4o-mini`, whose
automatic prompt caching *is* available. The ledger rows it wrote:

    gpt-4o-mini in=3282 cached=1920 (58.5%) out=34  prefix=c09bdeae92b5
    gpt-4o-mini in=3317 cached=1920 (57.9%) out=75  prefix=174f46c14bfb
    gpt-4o-mini in=3330 cached=3200 (96.1%) out=62  prefix=174f46c14bfb
    …
    day total: 22,144 of 30,376 input tokens — 72.9% served from cache

**96% prefix cache on a real turn.** The 27.4 projection is no longer a
projection: give this prompt a provider that offers caching and it hits. The
only thing between the platform and that saving on Gemini is the free tier.

Note the stable `prefix=` hash across turns — that is the fingerprint doing its
job. It changes once early (language lock re-renders the header) and then holds.

### 27.10 The free tier's real cap is 20 requests per DAY

The 5/minute limit in 27.3 is not the binding one. The live call surfaced:

    quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
    quotaValue: '20'   model: gemini-3.5-flash

**20 requests per day per model.** An average call is 8.8 turns, so the
configured voice model is exhausted after roughly **two calls a day**, after
which every turn silently falls back to `gpt-4o-mini` — a different model, a
different persona-following ability, and a different bill. This is invisible
without §27's telemetry: the fallback is logged per turn but nothing
aggregated it, and `calls.llm_models_used` only records that it happened, not
how often it dominated. Enabling billing fixes the cap and the caching in one
action.

### 27.11 Bug this deploy caught, and the guard added

The first live call recorded ZERO tokens despite making 7 LLM requests. Cause:
the retry-without-`stream_options` path treated **any** primary failure as
"provider rejected the parameter" and latched `_llm_usage_supported = False`
for the process. The first 429 (day quota) therefore disabled cost telemetry
permanently — a telemetry feature defeated by the exact condition it exists to
measure.

`_is_param_rejection()` now narrows it to a 400/422 whose message actually
names `stream_options`/`include_usage`. Quota, timeout, auth and 5xx take the
normal fallback path with telemetry left armed. Pinned by
`test_quota_error_is_not_mistaken_for_a_parameter_rejection`.

The fallback provider now also sends `stream_options`: with the primary's day
quota spent it serves every turn, and leaving it unmetered would leave the real
bill unmeasured exactly when it matters.

Separately, `name_confirm_block` was rendering ABOVE the ━━ CALL STATE ━━ cache
boundary — empty in every golden fixture, so nothing caught it, but on a live
call the turn after a caller gives their name it broke its own prefix. Moved
below the marker (fixture-neutral: the goldens are byte-identical). Pinned by
`test_pending_name_does_not_break_the_static_prefix`, which drives a real
router because only live state exposes this class of bug.

---

## 28. Document RAG hardening + measurable evaluation (2026-08-12) — AUDIT + SHIPPED

### What the document RAG was

`calling-agent/backend/app/documents/` grounds live calls in tenant-uploaded
documents. Before this work the whole pipeline was:

```
caller turn -> one embedding HTTP call -> cosine vs every chunk
            -> keep score >= 0.25 -> take 3 -> paste into the prompt
```

No lexical arm, no re-ranking, no citations, no cost accounting, no evaluation,
and — the load-bearing detail — **no record of how the index was built**.

### Four silent failures it had

1. **A model change made every stored vector meaningless.** `_EMBED_MODEL` was
   a module constant, the `.npy` recorded nothing, and the query-time cosine
   either raised a shape error or produced noise. Both were swallowed by
   `except Exception: logger.debug(...)`. The agent just quietly stopped using
   its documents. Now every index carries provider/model/dim/chunk-geometry and
   a content hash (`index_meta.json`); a mismatch is a named condition an
   operator sees in `GET /api/agents/{name}/documents` → `index.needs_reindex`.
2. **A failed embed at upload was permanent and invisible.** Now reported per
   document, repairable with `POST /api/agents/{name}/documents/reindex`, and
   the document stays reachable through the lexical arm meanwhile.
3. **The embedding HTTP call ran inline in the async turn** — measured p50
   320ms / p95 622ms of frozen event loop, taking VAD, STT and TTS with it. Now
   `retrieve_async` runs it in a worker thread with a deadline.
4. **Every turn retrieved, including "hmm".** Fillers now short-circuit before
   the API call: on 300 replayed real caller turns, 97 never reach it.

### What shipped

Hybrid retrieval (BM25 + vector, fused with RRF — the same fusion
`shared/intelligence/search.py` already uses), a conservative abstention gate,
structured citations, a two-tier semantic cache, per-retrieval tracing, and
embedding cost in the existing AI ledger (`credit_tracker.record_embedding_sync`
→ `shared/ai_pricing.embedding_cost`).

Measured on 37 golden questions over a 10-document corpus copied from live
platform content (`rag_eval/`):

| | before | after |
|---|---|---|
| Recall@1 | 0.66 | 0.79 |
| Recall@3 | 0.98 | 0.98 |
| MRR | 0.83 | 0.89 |
| NDCG@5 | 0.84 | 0.90 |
| End-to-end abstention (no-answer questions) | 0.56 | **1.00** |
| Over-abstention (answerable refused) | 0.00 | 0.00 |
| Faithfulness (LLM-judged) | 0.97 | 1.00 |
| Citation accuracy | n/a — none existed | 1.00 |

### Two defaults that are the opposite of the textbook

Both were measured, and both comments in `app/documents/config.py` carry the
table that decided them.

* **MMR is OFF** (`RAG_MMR_LAMBDA=1.0`). Diversity assumes the top hits are
  near-duplicates competing for one slot; here they are often joint evidence,
  because chunking overlaps by 60 words. MMR at 0.75 was worse at all 12 grid
  points — Recall@3 1.00 → 0.80 at the shipped chunking.
* **The similarity threshold stays at 0.25** and is NOT the abstention
  mechanism. Answerable and unanswerable questions overlap almost completely in
  cosine (0.26–0.74 vs 0.27–0.71: "how many employees does DhronAI have?"
  scores 0.71 because it is *about* the document's subject). Raising the floor
  to 0.40 catches 2 of 7 unanswerable questions and wrongly refuses 15 of 28
  answerable ones. Semantic absence is caught at generation time by the
  grounding contract (`app/documents/grounding.py`) instead, and the two layers
  are measured separately.

### Operating it

```bash
cd calling-agent/backend
../venv/bin/python -m rag_eval.runner                 # offline, free, deterministic
../venv/bin/python -m rag_eval.runner --fresh         # real API — the only real latency
../venv/bin/python -m rag_eval.runner --generate --judge --gen-provider openai
../venv/bin/python -m rag_eval.replay --limit 300 --live   # real caller turns
../venv/bin/python -m rag_eval.sweep --live           # parameter grid
../venv/bin/python -m pytest tests/test_rag_*.py      # 42 tests, the CI gate
```

`GET /api/agents/{slug}/rag/metrics` is the live view (latency percentiles,
abstention, cache, index health). `POST /api/agents/{slug}/rag/probe` with
`{"query": "..."}` answers "did retrieval miss, or did the model ignore the
evidence?" without placing a call.

### Migration — documents indexed before today

They keep working (the loader accepts an unlabelled index whose dimension
matches) and are reported as `unlabelled` / `needs_reindex`. Chunking also moved
400 → 250 words, which only applies to documents that are re-chunked. To adopt
both, per agent:

```bash
curl -X POST https://agent.lescall.com/api/agents/<slug>/documents/reindex
```

Re-embedding spends real tokens (~₹0.0000141 per 8-token query at
text-embedding-3-small), so nothing re-indexes itself implicitly.

### 28b. Follow-ups from a second-opinion review (2026-08-12)

The same brief was run independently on another machine; comparing the two
implementations surfaced three real gaps in this one. All three are fixed above
their measurements:

**Embedding spend was recorded but never reported.** `credit_tracker` wrote
`embed_event` to the ledger from day one, but `credit_report.py` only knew
`stt_event` / `tts_event` / `llm_event`, so the cost existed in the log and
nowhere a human looks. It now prints requests, tokens, stage split (query vs
ingest) and cost — at 6 decimals, because a query embedding costs ~₹0.00002 and
4 decimals renders a measured cost as ₹0.0000.

**Citations never reached the call record.** `_rag_citations` was assigned on
the router and read by nothing — the claim "citations are structured data on the
turn record" was true of the design and false of the code. Migration 0018 adds
`call_turns.rag_sources` (JSONB) + `rag_trace_id`; the pipeline attaches them
after generation and the bridge persists them. "The agent quoted a price on
Tuesday, where did that come from?" is now a SQL query against the call record
rather than a grep of a rotating trace file.

**Cross-document contamination was unmeasured.** Precision@K blurs two very
different failures: a chunk of the RIGHT document that is not the answering
passage (harmless padding) and a chunk of the WRONG document (a confident
irrelevant fact). Split out, the average is 0.11 legacy → 0.05 now, but the
average is the wrong number to quote — it is ~0.00 everywhere except
`wrong_context` (0.33) and `multi_hop` (0.10). A relative quality floor
(`RAG_RELATIVE_FLOOR=0.6`, swept) removes the case that motivated it: a caller
asking for a 3BHK in Surat under ₹1.1 crore was being handed the property list,
a rental listing for another city, AND a Nintendo console catalogue. Quality is
untouched at 0.6 and pays real recall past 0.7 — the table is in config.py.

Not adopted: the other implementation reports contamination 0.50 → 0.00 on its
own trap set. Ours starts at 0.11 and lands at 0.05 on this corpus, and the
residual is two documents about the SAME company (a company profile and a
staff-introduction blurb) ranking alongside each other — cross-document by the
metric's definition, benign in a caller's ears. Different corpus, different
number; the mechanism is the same and the measurement here is what shipped.

**Exact-term questions could be refused by their own document.** Found while
probing the live `dhron_calling` agent after the re-index: "what are the six
stages?" retrieved nothing. The chunk that literally contains "Six Stage
Implementation Approach" scores 0.222 — under the absolute floor — because
embeddings under-score short keyword questions, exactly the case the BM25 arm
exists to cover. The pruner then discarded it on the cosine alone, so the
lexical arm could find the answer and never deliver it.

The fix is a lexical FALLBACK: when the vector arm yields nothing above the
floor, the top BM25 hits are admitted instead. Fallback and not addition,
because admitting them alongside working vector results measured Precision@3
0.53 -> 0.46 and contamination 0.05 -> 0.17 — one shared common word is enough
to drag in another tenant's document. As a fallback it changes nothing on the
golden set and rescues the live query. `RetrievedChunk.kept_by` records which
arm admitted each chunk, and it is in the trace.

Worth noting for whoever tunes this next: it was found by probing a REAL agent
after deploying, not by the golden set, whose phrasing of the same question
scores above the floor. Probe production after a retrieval change.

**The tests were writing fake usage into the production cost ledger.** Caught by
reading the patched cost report on real data: it showed 3,457 embedding requests
against a model called `fake-embed-v1`. The RAG pipeline reports embedding
tokens through the credit-tracker singleton, and the unit tests — which embed
constantly with a fake provider — were appending to `credit_log/<date>.jsonl`,
the file `credit_report.py` reads. A cost ledger containing fabricated usage is
worse than one missing the feature entirely.

Closed at both sources: an autouse fixture in `tests/conftest.py` repoints the
tracker's log directory (resolved at import, so the module attribute is patched,
not the env var), and the evaluation harness disables cost tracking for its own
runs — an evaluation's spend is real but it is not a call, and it is already
reported by the eval itself. The 3,907 synthetic rows were removed from
2026-08-12; real STT/TTS/LLM events were untouched.

**Test-suite gotcha, for whoever chases this next:** the Postgres fixture
(`tests/test_intel_queue.pg_test_db`, session-scoped, shared by 10 modules)
rebuilds ONE database — `lescall_test` — with `DROP SCHEMA public CASCADE`.
Two pytest processes against the same host therefore destroy each other's
schema mid-run, and the symptom is a scatter of `UndefinedTable: relation
"phone_numbers" does not exist` in whichever modules happened to be running.
Observed while validating this work: the same commit produced 0, 3, 5 and 41
errors depending on what else was running, and a clean serial run is green.
Run one suite at a time, or give the second one its own database.

**And the reason it happened twice:** `credit_tracker._DEFAULT_LOG_DIR` was the
hardcoded string `/home/angelo/lescall/credit_log`, so ANY second checkout on
this host — a git worktree, a clone, a CI job — filed its test usage into the
production ledger no matter which tree it ran from. It is now resolved from the
module's own location, which is byte-identical in production and in the
container, and correct everywhere else. `SARVAM_CREDIT_LOG_DIR` still overrides.

---

## 38. Admin plane: number handover + reward minutes (2026-09-09) — SHIPPED, LIVE

Two operator capabilities that only existed as `psql` sessions, plus the live
handover of `+91 80 3133 9140` that prompted them.

### 38.1 The handover, and why moving one row is not a transfer

`info@craftidea.in` → `craftidearesearch@gmail.com`, same company, both accounts
already on the platform. The number's row was the obvious thing to change and
the *least* of what had to move. Three separate ownership checks sit on the live
path and all three are read at dial time:

| what | who reads it | what breaks if it stays behind |
|---|---|---|
| `phone_numbers.owner` | `_did_owner()`, `billing_owner()` | the wrong tenant is billed; the caller is matched against the wrong contact book |
| `trunks.owner` | `server._outbound_identity()` | the number answers calls and can **never place one** — "its trunk belongs to another account" |
| `agents.owner` | `_outbound_identity` → `_agent_owned_by()` | same: refused before the originate |

So a transfer moves the number, its trunk and the agents bound to it, or it is a
half-migration that looks finished in the console and fails on the first
outbound call.

**The trunk name is also the FreeSWITCH gateway name**, and it is shown to the
tenant in their own Telephony panel — leaving `info-craftidea-in_plivo-339140`
on the receiving account puts the previous owner's email address in their UI.
`telephony_api.update_trunk` refuses renames for a good reason (the XML would be
orphaned and every live DID would point at a gateway that no longer exists), so
the rename is done properly: write the new gateway XML, `reloadxml` +
`sofia profile external rescan`, verify the new gateway is `UP`, then
`killgw` the old one and delete its file. `reloadxml`/`rescan` **only ever add**
gateways — without `killgw` the retired one keeps accepting originates until
FreeSWITCH restarts.

What did NOT move, deliberately: `calls`, `usage_ledger` and `contacts` rows for
the old account. Those are the record of what that account did.

The DID's dialplan (`ai_inbound_8031339140.xml`) needed no change at all — it is
keyed on the number, not the owner, and routes to `127.0.0.1:9000` regardless.

Verified after the move:

```
inbound  agent: fitcoach_ai-2-inbound        # resolve_agent_slug(did=…)
outbound agent: fitcoach_ai-2-outbound
did owner     : craftidearesearch@gmail.com
outbound id   : gateway craftidearesearch-gmail-com_plivo-339140, caller 918031339140
dial gate     : ALLOWED (growth, 8000 min, telephony=1)
```

### 38.2 `/admin/telephony` — the operator's number plane

`shared/telephony_admin.py` is the logic; the HTML console
(`dashboard/admin_telephony.py`) and the JSON API (`/admin/api/...` in
`admin_app.py`) both call it, so the two surfaces cannot drift on who may bind
what. Per account: add a number, add a trunk, rebind inbound/outbound agents,
**Connect** (write gateway + dialplan, reload), **Disconnect** (delete the
dialplan, keep the row and its bindings), **Remove**, **Transfer**.

Two rules are enforced in the module, not in the page:

* **The receiving account must be entitled to telephony** — `telephony_allowed()`,
  the same predicate the tenant's own panel and the dial gate use, re-checked at
  Connect and not only at create. A number parked on a Free account answers calls
  the billing gate then refuses to meter.
* **A number may only bind to agents in its OWN account.** The inbound resolver
  *trusts* the binding and never re-checks ownership (`resolve_agent_slug`), so a
  cross-tenant binding is RUNBOOK §33 from the other end: another tenant's agent
  answering in their own company's name, billing their own minutes.

`freeswitch_config.remove_inbound_dialplan()` / `remove_gateway()` are new. Both
delete endpoints previously said *"generated config file (if applied) is not
auto-removed; see deploy/RUNBOOK.md"* — fine as a note a human reads, not fine
once a console button does it: a "removed" number kept answering calls.

### 38.3 Reward minutes — and the pool that had to be built

`/admin/tenant/<owner>` → **Reward minutes**: N minutes, valid M months, audited
reason. It runs on the existing `minute_topups` rail (`source='admin_credit'`,
`amount_paise=0`, no gateway order), so `get_entitlements()` folds it into
`minutes_limit` and every gate, meter and invoice picks it up with no other
change. No payment row and no invoice — no money moved, and a ₹0 receipt would
put a fake sale in the GST series.

**The defect that had to be designed out.** A paid top-up expires with the period
that sold it, so "add N to this period's ceiling" was always exact — there is
only ever one period. Validity in MONTHS breaks that: `minutes_limit` is
recomputed as `included + topup` at every rollover, so *1000 minutes for 3
months* would have handed out **3000**. The pool would silently refill.

So a grant drains. `minutes_consumed` + `settled_through` on the row;
`credits.settle_owner()` walks the CLOSED IST calendar months in the grant's
window and charges `max(0, minutes_used − minutes_included)` — only what the
tenant spent **beyond their own plan** — against the oldest grant covering that
month. The live period is never settled: its overspend is already bounded by
`included + remaining`, and settling it would double-count. The entitlement read
became `SUM(GREATEST(minutes - minutes_consumed, 0))`.

Wired into `sync.run_once()` as the `credits` pass, not a cron: if it never ran,
a multi-month grant would behave exactly like the bug.

Two honest limits, both documented in the module: the settlement grid is the IST
calendar month (historical period boundaries are recorded nowhere in this
schema), and it charges against the tenant's CURRENT `minutes_included`, so a
plan change mid-grant settles at the new size.

A grant younger than one calendar month has nothing to settle and behaves
exactly like today's paid top-up — the common case stays on the old fast path.

**Testing note.** The obvious test (*grant 1000/3mo, burn it, roll over, assert
the topup is 0*) passes even with the bug, because settlement marks a fully
drained grant `spent` and `spent` rows drop out of the `status='active'` sum
regardless. Only a **partially** consumed pool pins the entitlement read — that
assertion is in `test_settlement_charges_only_the_overspend`, and it was
confirmed to fail against `SUM(minutes)`.

### 38.4 Deploy

```bash
python -m shared.db.migrate          # 0025_admin_credit_grants
docker restart lescall-admin         # console + JSON API
docker restart lescall-workers       # the credits settlement pass
```

`lescall-api` and `lescall-runtime` need the restart too, once: both IMPORT
`shared/billing/entitlements.py` (the dial gate and the inbound E6 gate), and a
bind mount does not reload a module that is already in memory. Skip them and the
call path keeps summing `minutes` instead of what is LEFT of each pool — the
exact over-grant this shipped to prevent, live on the only surface that spends
the minutes.

```bash
docker restart lescall-api lescall-runtime   # no live calls first: `show channels count`
```

Tests: `tests/test_admin_credits.py` (24) + `tests/test_admin_telephony.py` (22),
both on the `<db>_test` Postgres rebuilt from the real migrations.
