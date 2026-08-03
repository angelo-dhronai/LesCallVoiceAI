"""LesCall Sentinel — 24/7 self-healing platform watchdog.

Runs on the HOST (never inside the Docker stack it supervises) as
lescall-sentinel.service. Every cycle it checks each layer of the platform
and repairs what is safely repairable; what it cannot fix it alerts on —
loudly and with rate limits.

Checks & remedies (cycle = SENTINEL_INTERVAL, default 30 s):
  containers    each app container running + healthy      -> docker restart
  ports         9000/9001/9004/8080/8081/5432 listening   -> restart owner
  freeswitch    systemd active + fs_cli status responds   -> systemctl restart
  sip-stack     UDP OPTIONS probe answered by FS          -> (counts toward FS restart)
  nginx/edge    systemd active + local 8080/8081 + HTTPS  -> systemctl restart nginx
  database      SELECT 1                                  -> docker restart lescall-pg
  live-state    snapshot file fresh while runtime up      -> restart runtime (idle only)
  workers       reconcile pass logged recently            -> docker restart workers
  inbound-flow  a 'public' CDR row within INBOUND_ALERT_H -> ALERT (carrier-side)
  ledger        no usage_ledger row open > 1 h            -> ALERT (billing!)
  disk          < DISK_ALERT_PCT used                     -> prune docker + old run logs
  memory        > MEM_MIN_MB available                    -> ALERT
  gcs           storage round-trip (hourly)               -> ALERT (re-auth needed)
  voice         Sarvam STT/TTS health: live-call timeout/  -> ALERT + optional
                latency scan every cycle + timed API        escalation call
                probes (shared/voice_health.py — added       (see that module)
                after the 2026-08-03 incident went
                undetected by everything above)

Safety rails:
  - Per-target restart cooldown (10 min) and cap (3/hour) — a genuinely broken
    service degrades to alert-only instead of restart-flapping.
  - The runtime container is NEVER restarted while a call is live (established
    conns on :9001), unless its processes are outright dead.
  - Every action and alert lands in audit_events + the sentinel log; alerts
    also email ALERT_EMAIL (rate-limited per alert key, 1 per 6 h).

CLI: python -m shared.sentinel --once | --interval 30
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger("sentinel")

REPO = Path(__file__).resolve().parent.parent
STATE_PATH = REPO / "data" / "sentinel_state.json"
STATUS_PATH = REPO / "data" / "sentinel_status.json"
LIVE_STATE = REPO / "phase14_runtime" / "live_calls_state.json"
CDR_PATH = os.environ.get("FS_CDR_PATH", "/var/log/freeswitch/cdr-csv/Master.csv")

ALERT_EMAIL = os.environ.get("SENTINEL_ALERT_EMAIL", "tech@dhronai.com")
INBOUND_ALERT_H = float(os.environ.get("SENTINEL_INBOUND_ALERT_H", "26"))
DISK_ALERT_PCT = int(os.environ.get("SENTINEL_DISK_ALERT_PCT", "90"))
MEM_MIN_MB = int(os.environ.get("SENTINEL_MEM_MIN_MB", "300"))
EDGE_URL = os.environ.get("PUBLIC_BASE_URL", "https://agent.lescall.com")

RESTART_COOLDOWN_S = 600
RESTART_CAP_PER_HOUR = 3
ALERT_REPEAT_S = 6 * 3600

CONTAINERS = ("lescall-api", "lescall-runtime", "lescall-workers",
              "lescall-dashboard", "lescall-pg")
PORT_OWNER = {9000: "lescall-runtime", 9001: "lescall-runtime",
              9004: "lescall-runtime", 8080: "lescall-api",
              8081: "lescall-dashboard", 5432: "lescall-pg"}


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except FileNotFoundError:
        return -2, "missing binary"


def _http_ok(url: str, timeout: int = 6) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "lescall-sentinel"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


def _port_listening(port: int) -> bool:
    code, out = _run(["ss", "-ltnH", f"sport = :{port}"], timeout=8)
    return code == 0 and out.strip() != ""


def _established_on(port: int) -> int:
    code, out = _run(["ss", "-tnH", "state", "established",
                      f"( sport = :{port} )"], timeout=8)
    return len(out.strip().splitlines()) if code == 0 and out.strip() else 0


class State:
    def __init__(self):
        try:
            self.d = json.loads(STATE_PATH.read_text())
        except Exception:
            self.d = {}

    def save(self):
        STATE_PATH.parent.mkdir(exist_ok=True)
        STATE_PATH.write_text(json.dumps(self.d))

    def restart_allowed(self, target: str) -> bool:
        now = time.time()
        hist = [t for t in self.d.get(f"restarts:{target}", []) if now - t < 3600]
        self.d[f"restarts:{target}"] = hist
        if hist and now - hist[-1] < RESTART_COOLDOWN_S:
            return False
        return len(hist) < RESTART_CAP_PER_HOUR

    def note_restart(self, target: str):
        self.d.setdefault(f"restarts:{target}", []).append(time.time())
        self.save()

    def alert_allowed(self, key: str) -> bool:
        now = time.time()
        if now - self.d.get(f"alert:{key}", 0) < ALERT_REPEAT_S:
            return False
        self.d[f"alert:{key}"] = now
        self.save()
        return True


def _audit(action: str, target: str, meta: dict):
    try:
        from shared.audit import record
        record("sentinel", action, target, meta)
    except Exception:
        pass


def alert(state: State, key: str, subject: str, body: str):
    logger.error("ALERT[%s] %s — %s", key, subject, body)
    _audit("sentinel_alert", key, {"subject": subject, "body": body})
    if not state.alert_allowed(key):
        return
    try:
        msg = (f"To: {ALERT_EMAIL}\nFrom: sentinel@lescall\n"
               f"Subject: [LesCall SENTINEL] {subject}\n\n{body}\n\n"
               f"host time: {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        subprocess.run(["/usr/sbin/sendmail", "-t"], input=msg.encode(),
                       timeout=15)
    except Exception as exc:
        logger.warning("alert email failed: %r", exc)


def repair_restart_container(state: State, name: str, reason: str) -> bool:
    if not state.restart_allowed(name):
        alert(state, f"cap:{name}", f"{name} keeps failing ({reason})",
              f"Restart cap reached for {name}; manual attention needed. "
              f"Reason: {reason}")
        return False
    logger.warning("REPAIR: docker restart %s (%s)", name, reason)
    _audit("sentinel_repair", name, {"reason": reason, "action": "docker restart"})
    state.note_restart(name)
    code, out = _run(["docker", "restart", name], timeout=90)
    if code != 0:
        alert(state, f"restartfail:{name}", f"docker restart {name} FAILED",
              out[:500])
        return False
    alert(state, f"repaired:{name}", f"{name} was auto-restarted",
          f"Reason: {reason}. Verify at {EDGE_URL}.")
    return True


def repair_restart_unit(state: State, unit: str, reason: str) -> bool:
    if not state.restart_allowed(unit):
        alert(state, f"cap:{unit}", f"{unit} keeps failing ({reason})",
              "Restart cap reached; manual attention needed.")
        return False
    logger.warning("REPAIR: systemctl restart %s (%s)", unit, reason)
    _audit("sentinel_repair", unit, {"reason": reason, "action": "systemctl restart"})
    state.note_restart(unit)
    code, out = _run(["sudo", "-n", "systemctl", "restart", unit], timeout=60)
    if code != 0:
        alert(state, f"restartfail:{unit}", f"systemctl restart {unit} FAILED",
              out[:500])
        return False
    alert(state, f"repaired:{unit}", f"{unit} was auto-restarted",
          f"Reason: {reason}")
    return True


# ---------------------------------------------------------------------------
# checks — each returns (ok, detail)
# ---------------------------------------------------------------------------

def check_containers(state: State) -> dict:
    out = {}
    for name in CONTAINERS:
        code, raw = _run(["docker", "inspect", name, "--format",
                          "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"])
        if code != 0:
            out[name] = "missing"
            if name != "lescall-pg":   # never auto-create the DB container
                repair_restart_container(state, name, "container missing/not inspectable")
            else:
                alert(state, "pg-missing", "lescall-pg container missing",
                      "The database container is gone. NOT auto-fixing; "
                      "bring it back with docker compose -f deploy/"
                      "docker-compose.postgres.yml up -d")
            continue
        running, health = (raw.strip().split() + ["none"])[:2]
        out[name] = f"running={running} health={health}"
        if running != "true" or health == "unhealthy":
            if name == "lescall-runtime" and _established_on(9001) > 0 and running == "true":
                alert(state, "runtime-unhealthy-live",
                      "runtime unhealthy but a call is live",
                      "Deferring restart until the call ends.")
                continue
            repair_restart_container(state, name, f"running={running} health={health}")
    return out


def check_ports(state: State) -> dict:
    out = {}
    for port, owner in PORT_OWNER.items():
        ok = _port_listening(port)
        out[str(port)] = ok
        if not ok:
            if owner == "lescall-runtime" and _established_on(9001) > 0:
                continue
            repair_restart_container(state, owner, f"port {port} not listening")
    return out


def _sip_options_probe(host: str = "10.160.0.8", port: int = 5080) -> bool:
    """Minimal SIP OPTIONS over UDP; any reply counts as alive."""
    branch = f"z9hG4bK{int(time.time() * 1000)}"
    msg = (f"OPTIONS sip:sentinel@{host}:{port} SIP/2.0\r\n"
           f"Via: SIP/2.0/UDP {host}:59999;rport;branch={branch}\r\n"
           f"From: <sip:sentinel@{host}>;tag=snt\r\n"
           f"To: <sip:sentinel@{host}>\r\n"
           f"Call-ID: sentinel-{branch}\r\nCSeq: 1 OPTIONS\r\n"
           f"Max-Forwards: 5\r\nContent-Length: 0\r\n\r\n").encode()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(4)
        s.sendto(msg, (host, port))
        data, _ = s.recvfrom(2048)
        return data.startswith(b"SIP/2.0")
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def check_freeswitch(state: State, cycle: int) -> dict:
    code, out = _run(["systemctl", "is-active", "freeswitch"], timeout=8)
    active = out.strip() == "active"
    responds = _sip_options_probe() if active else False
    if not active:
        repair_restart_unit(state, "freeswitch", "systemd inactive")
    elif not responds:
        misses = state.d.get("fs_probe_misses", 0) + 1
        state.d["fs_probe_misses"] = misses
        state.save()
        if misses >= 3:
            state.d["fs_probe_misses"] = 0
            if _established_on(9001) == 0:
                repair_restart_unit(state, "freeswitch",
                                    "SIP stack unresponsive to OPTIONS x3")
            else:
                alert(state, "fs-hung-live", "FreeSWITCH SIP probe failing "
                      "with a live call", "Not restarting mid-call.")
    else:
        state.d["fs_probe_misses"] = 0
    return {"active": active, "sip_responds": responds}


def check_web(state: State, cycle: int) -> dict:
    code, out = _run(["systemctl", "is-active", "nginx"], timeout=8)
    nginx = out.strip() == "active"
    if not nginx:
        repair_restart_unit(state, "nginx", "systemd inactive")
    api = _http_ok("http://127.0.0.1:8080/login")
    spa = _http_ok("http://127.0.0.1:8081/")
    if not api:
        repair_restart_container(state, "lescall-api", "8080 /login not OK")
    if not spa:
        repair_restart_container(state, "lescall-dashboard", "8081 not OK")
    edge = True
    if cycle % 10 == 0:
        edge = _http_ok(f"{EDGE_URL}/login", timeout=10)
        if not edge and nginx and api:
            alert(state, "edge-down", "HTTPS edge unreachable",
                  f"{EDGE_URL}/login failing while nginx+api are up — "
                  "check DNS/TLS/certbot.")
    return {"nginx": nginx, "api8080": api, "spa8081": spa, "edge": edge}


def check_db(state: State) -> bool:
    try:
        from shared.db.database import get_db
        with get_db() as db:
            db.execute("SELECT 1").fetchone()
        return True
    except Exception as exc:
        logger.error("DB check failed: %r", exc)
        repair_restart_container(state, "lescall-pg", f"SELECT 1 failed: {exc}")
        return False


def check_live_state(state: State) -> bool:
    """ws_audio_receiver writes the snapshot every ~1.5 s. Stale file while
    the container claims healthy = hung event loop."""
    try:
        age = time.time() - LIVE_STATE.stat().st_mtime
    except OSError:
        return True   # never written yet — nothing to conclude
    if age > 300:
        if _established_on(9001) > 0:
            alert(state, "livestate-stale-live", "live snapshot stale during "
                  "a call", f"age={age:.0f}s — runtime may be wedged; not "
                  "restarting mid-call.")
        else:
            repair_restart_container(state, "lescall-runtime",
                                     f"live snapshot stale {age:.0f}s")
        return False
    return True


def check_workers(state: State) -> bool:
    code, out = _run(["docker", "logs", "lescall-workers", "--since", "20m"],
                     timeout=20)
    ok = code == 0 and "reconcile:" in out
    if not ok:
        repair_restart_container(state, "lescall-workers",
                                 "no reconcile pass logged in 20m")
    return ok


def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _sig(s: str) -> str:
    return _digits(s)[-10:]


def check_inbound_flow(state: State) -> dict:
    """Carrier-side canary, PER DID: hours since each registered number last
    received an inbound ('public' context) CDR row. Provider-agnostic — no
    carrier API needed; a DID that has gone quiet past the threshold (and has
    received calls before) raises a per-DID alert. Cannot be auto-fixed
    locally; the loopback probe (below) is the active counterpart."""
    last_by_sig: dict[str, str] = {}
    try:
        with open(CDR_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '","public","' not in line:
                    continue
                parts = line.split('","')
                if len(parts) > 4:
                    dest_sig = _sig(parts[2])
                    if dest_sig:
                        last_by_sig[dest_sig] = parts[4]
    except Exception as exc:
        return {"error": str(exc)}

    dids: list[dict] = []
    try:
        from shared.db.database import get_db
        with get_db() as db:
            dids = [dict(r) for r in db.execute(
                "SELECT number, sig_digits, owner FROM phone_numbers "
                "WHERE direction IN ('both','inbound')").fetchall()]
    except Exception as exc:
        logger.warning("inbound-flow DID lookup failed: %r", exc)

    now = dt.datetime.now(dt.timezone.utc)
    report = {}
    for d in dids:
        sig = d["sig_digits"] or _sig(d["number"])
        stamp = last_by_sig.get(sig)
        if not stamp:
            report[sig] = "never"   # brand-new DID — no baseline, no alert
            continue
        try:
            t = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=dt.timezone.utc)
        except ValueError:
            report[sig] = "unparseable"
            continue
        hours = (now - t).total_seconds() / 3600
        report[sig] = round(hours, 1)
        if hours > INBOUND_ALERT_H:
            alert(state, f"inbound-silence:{sig}",
                  f"No inbound call on {d['number']} for {hours:.0f}h",
                  f"DID {d['number']} (owner {d['owner']}) last received an "
                  f"inbound call {hours:.0f} hours ago ({stamp} UTC). If "
                  "callers report failures, the problem is CARRIER-SIDE "
                  "(trunk/DID/balance at the tenant's provider) — the local "
                  "SIP stack probes OK. Run the loopback probe to confirm: "
                  "python -m shared.sentinel --probe")
    return report


# ---------------------------------------------------------------------------
# Loopback probe — ACTIVE, provider-agnostic inbound verification.
# The platform calls each eligible DID through the tenant's OWN trunk; the
# call exits via their carrier and must come back as a genuine inbound leg.
# Works for any SIP provider because it never talks to a carrier API.
# ---------------------------------------------------------------------------

PROBE_HOUR_UTC = int(os.environ.get("SENTINEL_PROBE_HOUR_UTC", "3"))
PROBE_ENABLED = os.environ.get("SENTINEL_PROBE", "on").lower() != "off"


def _probe_targets() -> list[dict]:
    """DIDs that can self-test: tenant's own applied trunk + their agent —
    the same gates the real outbound dial path enforces."""
    from shared.db.database import get_db
    out = []
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT p.number, p.sig_digits, p.owner, p.inbound_agent_slug, "
            "t.name AS trunk_name, t.config_status, t.owner AS trunk_owner "
            "FROM phone_numbers p JOIN trunks t ON t.id = p.trunk_id "
            "WHERE p.direction = 'both'").fetchall()]
    for r in rows:
        if (r["config_status"] == "applied" and r["trunk_owner"] == r["owner"]
                and r["inbound_agent_slug"]):
            out.append(r)
    return out


def _kill_probe_channels(fs_api, digits: str) -> None:
    """Hang up ONLY the loop's own legs: inbound channels whose caller id is
    our own DID (no real caller can present our DID as their number), plus
    any parked outbound leg to the DID."""
    import json as _json
    try:
        raw = fs_api("show channels as json", timeout=10)
        data = _json.loads(raw)
        for row in data.get("rows", []) or []:
            cid = _sig(row.get("cid_num", ""))
            dest = _sig(row.get("dest", ""))
            if cid == _sig(digits) or dest == _sig(digits):
                fs_api(f"uuid_kill {row.get('uuid')} NORMAL_CLEARING", timeout=10)
    except Exception as exc:
        logger.warning("probe channel cleanup: %r", exc)


def _probe_audio_verdict(since: dt.datetime) -> str:
    """Did FreeSWITCH confirm playing the AI's greeting during the probe?
    Reads socket_server's log for the playback EXEC result after `since`.
    Returns 'ok', 'file-not-found', or 'no-playback-attempted'."""
    log_path = REPO / "phase14_runtime" / "logs" / "docker" / "socket_server.log"
    marker = since.strftime("%H:%M")
    verdict = "no-playback-attempted"
    try:
        lines = log_path.read_text(errors="replace").splitlines()[-400:]
        seen_window = False
        for line in lines:
            if line[:5] >= marker:
                seen_window = True
            if not seen_window or "Application=playback" not in line:
                continue
            if "FILE%20PLAYED" in line or "FILE PLAYED" in line:
                verdict = "ok"
            elif "NOT%20FOUND" in line or "NOT FOUND" in line:
                verdict = "file-not-found"
    except Exception as exc:
        logger.warning("probe audio verdict failed: %r", exc)
        return "log-unreadable"
    return verdict


def loopback_probe(state: State) -> dict:
    """Place one short self-call per eligible DID and verify the inbound leg
    arrives. Probe traffic is marked billable=0 in the usage ledger — it must
    never count toward a tenant's minutes."""
    from shared.fs_esl import fs_api
    results: dict[str, str] = {}
    for t in _probe_targets():
        digits = _digits(t["number"])
        started = dt.datetime.now(dt.timezone.utc)
        cmd = ("originate {ignore_early_media=true,originate_timeout=25,"
               f"origination_caller_id_number={digits},"
               "lescall_probe=1}"
               f"sofia/gateway/{t['trunk_name']}/{digits} &park()")
        try:
            body = fs_api(cmd, timeout=45)
        except Exception as exc:
            results[digits] = f"esl-error: {exc}"
            alert(state, f"probe-esl:{digits}", f"Loopback probe failed to "
                  f"originate for {t['number']}", str(exc))
            continue
        ok = body.strip().startswith("+OK")
        if not ok:
            results[digits] = f"originate-failed: {body.strip()[:120]}"
            alert(state, f"probe-fail:{digits}",
                  f"Inbound loopback FAILED for {t['number']}",
                  f"The self-call through the tenant's own trunk did not "
                  f"connect: {body.strip()[:200]}\nThis is the signature of "
                  "a carrier-side problem (balance/trunk/DID at the tenant's "
                  f"provider). Owner: {t['owner']}.")
            _kill_probe_channels(fs_api, digits)
            continue
        # The originate succeeding means the outbound leg was ANSWERED — and
        # the only thing answering this DID is our own inbound path, so the
        # loop is proven. Give the AI a moment to speak its greeting, then
        # tear down and verify the audio actually PLAYED into the call
        # (catches the dead-air class: FS 'FILE NOT FOUND' when the playback
        # WAV path isn't visible to the host — 2026-07-14 incident).
        time.sleep(8)
        _kill_probe_channels(fs_api, digits)
        audio = _probe_audio_verdict(started)
        results[digits] = audio
        if audio == "ok":
            logger.info("loopback probe OK (answered + audio played) for %s",
                        t["number"])
        else:
            alert(state, f"probe-audio:{digits}",
                  f"Call answered but NO AUDIO played on {t['number']}",
                  "The loopback call connected but FreeSWITCH did not "
                  f"confirm playback ({audio}). Callers likely hear dead "
                  "air. Check /tmp/ai_telephony_playback mount + "
                  "socket_server playback log.")
        # Billing hygiene: mark the probe's ledger rows non-billable.
        try:
            from shared.db.database import get_db
            with get_db() as db:
                db.execute(
                    "UPDATE usage_ledger SET billable=0, "
                    "note=COALESCE(note,'sentinel loopback probe') "
                    "WHERE phone IS NOT NULL AND answered_at >= ? "
                    "AND (phone LIKE ? OR phone LIKE ?)",
                    (started.isoformat(timespec="seconds").replace("+00:00", "Z"),
                     f"%{_sig(digits)}", f"%{_sig(digits)};%"))
            from shared.usage_metering import recompute_monthly
            recompute_monthly()
        except Exception as exc:
            logger.warning("probe ledger hygiene failed: %r", exc)
    state.d["last_probe_date"] = dt.datetime.now(
        dt.timezone.utc).strftime("%Y-%m-%d")
    state.d["last_probe_results"] = results
    state.save()
    return results


def check_ledger(state: State) -> int:
    try:
        from shared.db.database import get_db
        with get_db() as db:
            rows = db.execute(
                "SELECT conn_id, last_beat_at FROM usage_ledger "
                "WHERE status='open'").fetchall()
        stuck = 0
        now = dt.datetime.now(dt.timezone.utc)
        for r in rows:
            try:
                t = dt.datetime.fromisoformat(
                    str(r["last_beat_at"]).replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=dt.timezone.utc)
                if (now - t).total_seconds() > 3600:
                    stuck += 1
            except Exception:
                stuck += 1
        if stuck:
            alert(state, "ledger-stuck",
                  f"{stuck} usage_ledger rows open >1h",
                  "The billing reconciler should have closed these — check "
                  "lescall-workers. Billing accuracy is at risk.")
        return stuck
    except Exception as exc:
        logger.warning("ledger check failed: %r", exc)
        return -1


def check_disk_mem(state: State) -> dict:
    du = shutil.disk_usage("/")
    pct = du.used * 100 // du.total
    if pct >= DISK_ALERT_PCT:
        logger.warning("REPAIR: disk %d%% — pruning", pct)
        _audit("sentinel_repair", "disk", {"pct": pct})
        _run(["docker", "system", "prune", "-f"], timeout=120)
        _run(["find", str(REPO / "phase14_runtime" / "logs"), "-maxdepth", "1",
              "-type", "d", "-name", "run_*", "-mtime", "+7",
              "-exec", "rm", "-rf", "{}", "+"], timeout=60)
        pct2 = shutil.disk_usage("/").used * 100 // du.total
        alert(state, "disk", f"Disk was {pct}% full (now {pct2}%)",
              "Auto-pruned docker + old runtime logs. If still high, "
              "investigate recordings/ and pg volume growth.")
    mem_avail_mb = 0
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                mem_avail_mb = int(line.split()[1]) // 1024
                break
    except Exception:
        pass
    if 0 < mem_avail_mb < MEM_MIN_MB:
        alert(state, "memory", f"Only {mem_avail_mb}MB memory available",
              "The VM is under memory pressure; FreeSWITCH OOM risk "
              "(it has been OOM-killed before). Check `docker stats`.")
    return {"disk_pct": pct, "mem_avail_mb": mem_avail_mb}


def check_gcs(state: State) -> bool:
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(os.environ.get("GCS_RECORDINGS_BUCKET",
                                              "lescall-recordings"))
        list(client.list_blobs(bucket, max_results=1))
        return True
    except Exception as exc:
        alert(state, "gcs-auth", "GCS access failing",
              f"{exc!r}\nRecording/log uploads are paused (local serving "
              "still works, and nothing is deleted locally until it is "
              "safely in the bucket). Auth is the VM's attached service "
              "account via the metadata server — there is no key file and "
              "no human session to renew, so this should NOT be a "
              "re-login. Check, in order: (1) is "
              "GOOGLE_APPLICATION_CREDENTIALS set anywhere (it overrides "
              "metadata ADC — it must be unset), (2) does the attached SA "
              "still hold roles/storage.objectAdmin on the bucket, (3) is "
              "the instance still running with --scopes cloud-platform. "
              "See RUNBOOK 6b.")
        return False


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------

def run_cycle(state: State, cycle: int) -> dict:
    status: dict = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(
        timespec="seconds"), "cycle": cycle}
    status["containers"] = check_containers(state)
    status["ports"] = check_ports(state)
    status["freeswitch"] = check_freeswitch(state, cycle)
    status["web"] = check_web(state, cycle)
    status["db_ok"] = check_db(state)
    status["live_state_ok"] = check_live_state(state)
    try:
        from shared import voice_health
        status["voice"] = voice_health.check_voice(state, cycle, alert)
    except Exception:
        logger.exception("voice health check failed")
    if cycle % 10 == 0:          # every ~5 min
        status["workers_ok"] = check_workers(state)
        status["inbound"] = check_inbound_flow(state)
        status["ledger_stuck"] = check_ledger(state)
        status["disk_mem"] = check_disk_mem(state)
    if cycle % 120 == 0:         # hourly
        status["gcs_ok"] = check_gcs(state)
    # Daily inbound loopback probe (provider-agnostic active test).
    now = dt.datetime.now(dt.timezone.utc)
    if (PROBE_ENABLED and now.hour == PROBE_HOUR_UTC
            and state.d.get("last_probe_date") != now.strftime("%Y-%m-%d")):
        status["loopback_probe"] = loopback_probe(state)
    try:
        STATUS_PATH.parent.mkdir(exist_ok=True)
        STATUS_PATH.write_text(json.dumps(status, indent=1))
    except Exception:
        pass
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--probe", action="store_true",
                        help="run the inbound loopback probe now and exit")
    parser.add_argument("--interval", type=int,
                        default=int(os.environ.get("SENTINEL_INTERVAL", "30")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s sentinel %(levelname)s %(message)s")
    state = State()
    cycle = 0
    if args.probe:
        print(json.dumps(loopback_probe(state), indent=2))
        return
    if args.once:
        print(json.dumps(run_cycle(state, cycle), indent=2))
        return
    logger.info("sentinel up (interval=%ss)", args.interval)
    while True:
        try:
            run_cycle(state, cycle)
        except Exception:
            logger.exception("cycle failed")
        cycle += 1
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
