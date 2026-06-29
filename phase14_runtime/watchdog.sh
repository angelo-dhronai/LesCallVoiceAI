#!/bin/bash
# watchdog.sh - Detects a degraded/half-dead Phase 14 runtime and self-heals.
#
# Why this exists: on 2026-06-27 socket_server.py (port 9000) died and stayed
# dead silently. The platform showed "degraded" and calls dropped, but nothing
# brought it back until a human noticed. This watchdog closes that gap.
#
# Safe by design:
#   * Kill-switch  - touch phase14_runtime/watchdog.disabled to pause it.
#   * Cooldown     - won't restart more than once per COOLDOWN_S, so a service
#                    that is genuinely broken can't be thrashed in a restart loop.
#   * Active-call  - won't restart while a call is in progress (would drop it),
#     guard          UNLESS the media port itself is down (then no call can be live).
#
# Intended to run once a minute from cron (see install_watchdog_cron.sh).

set -u

RUNTIME_DIR="/home/angelo/lescall/phase14_runtime"
LIVE_STATE="$RUNTIME_DIR/live_calls_state.json"
DISABLE_FLAG="$RUNTIME_DIR/watchdog.disabled"
STAMP_FILE="$RUNTIME_DIR/.watchdog_last_restart"
START_SCRIPT="$RUNTIME_DIR/start_all.sh"

REQUIRED_PORTS=(9000 9001)   # ESL controller + media receiver
MEDIA_PORT=9001
COOLDOWN_S=180               # min seconds between auto-restarts
ACTIVE_CALL_FRESH_S=20       # live-state newer than this == a real active call

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] $*"; }

# Consistency with the rest of the runtime: never operate as root.
if [ "$(id -u)" -eq 0 ]; then
    log "refusing to run as root; exiting."
    exit 1
fi

# Kill-switch.
if [ -f "$DISABLE_FLAG" ]; then
    exit 0
fi

port_up() { ss -ltn 2>/dev/null | grep -q ":$1 "; }

# Determine health.
down=()
for p in "${REQUIRED_PORTS[@]}"; do
    port_up "$p" || down+=("$p")
done

if [ ${#down[@]} -eq 0 ]; then
    exit 0   # healthy, nothing to do (stay quiet)
fi

log "DEGRADED: required port(s) down: ${down[*]}"

# Active-call guard: only relevant if the media port is still up (otherwise no
# call can possibly be live). Reads live_calls_state.json with the venv python.
if port_up "$MEDIA_PORT"; then
    active=$(/home/angelo/lescall/calling-agent/venv/bin/python - "$LIVE_STATE" "$ACTIVE_CALL_FRESH_S" <<'PY' 2>/dev/null
import json, sys, time
try:
    path, fresh = sys.argv[1], float(sys.argv[2])
    d = json.load(open(path))
    calls = d.get("calls") or []
    age = time.time() - float(d.get("updated_at", 0))
    print("1" if calls and age <= fresh else "0")
except Exception:
    print("0")
PY
)
    if [ "$active" = "1" ]; then
        log "active call in progress; deferring restart to avoid dropping it."
        exit 0
    fi
fi

# Cooldown: avoid restart loops.
now=$(date +%s)
if [ -f "$STAMP_FILE" ]; then
    last=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
    if [ $((now - last)) -lt "$COOLDOWN_S" ]; then
        log "in cooldown ($((now - last))s < ${COOLDOWN_S}s since last restart); skipping."
        exit 0
    fi
fi

log "auto-healing: running start_all.sh ..."
echo "$now" > "$STAMP_FILE"
bash "$START_SCRIPT" >>"$RUNTIME_DIR/logs/watchdog_restart.log" 2>&1
rc=$?
if [ $rc -eq 0 ]; then
    log "restart completed (start_all.sh rc=0)."
else
    log "restart FAILED (start_all.sh rc=$rc); see logs/watchdog_restart.log."
fi
exit 0
