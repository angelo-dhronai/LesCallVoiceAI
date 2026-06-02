#!/bin/bash
# start_all.sh - Deterministic startup for Phase 14 runtime
# Manages: socket_server.py (port 9000/9004), ws_audio_receiver.py (port 9001), FreeSWITCH

RUNTIME_DIR="/home/angelo/lescall/phase14_runtime"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RUN_DIR="$RUNTIME_DIR/logs/run_$TIMESTAMP"
mkdir -p "$RUN_DIR"

# Symlink "latest"
rm -f "$RUNTIME_DIR/logs/latest"
ln -s "$RUN_DIR" "$RUNTIME_DIR/logs/latest"

echo "Starting Phase 14 Runtime..."
echo "Log directory: $RUN_DIR"

# ── Disk space guard ──────────────────────────────────────────────────────────
AVAIL_KB=$(df -k / | awk 'NR==2{print $4}')
AVAIL_GB=$(echo "scale=1; $AVAIL_KB / 1048576" | bc 2>/dev/null || echo "?")
if [ -n "$AVAIL_KB" ] && [ "$AVAIL_KB" -lt 1048576 ]; then   # < 1 GB free
    echo "[WARN] Disk critically low: ${AVAIL_GB}GB free. FreeSWITCH may fail to write WAV/logs."
    echo "[WARN] Run: rm -rf $RUNTIME_DIR/logs/run_202605* to free space."
else
    echo "[OK]  Disk space: ${AVAIL_GB}GB free"
fi

# ── Reset runtime ─────────────────────────────────────────────────────────────
/bin/bash "$RUNTIME_DIR/reset_runtime.sh"
if [ $? -ne 0 ]; then
    echo "ERROR: Runtime reset failed. Aborting startup."
    exit 1
fi

# ── Helper: wait for a TCP port to start listening ────────────────────────────
wait_for_port() {
    local port=$1
    local name=$2
    local timeout=${3:-10}
    local count=0
    echo "Waiting for $name to bind to port $port..."
    while ! ss -ltn | grep -q ":$port "; do
        sleep 1
        count=$((count + 1))
        if [ $count -ge $timeout ]; then
            echo "[FAIL] $name failed to bind to port $port within ${timeout}s"
            return 1
        fi
    done
    echo "[CHECK] port $port reachable ($name)"
    return 0
}

# Change to /home/angelo so relative imports and 'calling-agent' path work
cd /home/angelo/lescall

# ── 1. Start socket_server.py (ESL Controller + Playback Listener) ────────────
echo "[1/3] Starting Socket Server (System Python)..."
nohup python3 /home/angelo/lescall/socket_server.py >> "$RUN_DIR/socket_server.log" 2>&1 &
SS_PID=$!
echo $SS_PID > "$RUN_DIR/socket_server.pid"

if ! wait_for_port 9000 "socket_server (ESL)" 15; then
    echo "ERROR: socket_server.py failed to start or bind."
    cat "$RUN_DIR/socket_server.log"
    exit 1
fi

if ! wait_for_port 9004 "socket_server (playback)" 5; then
    echo "ERROR: socket_server.py playback listener failed to bind port 9004."
    cat "$RUN_DIR/socket_server.log"
    exit 1
fi

# ── 2. Start ws_audio_receiver.py (WebSocket Media Receiver + AI Bridge) ──────
echo "[2/3] Starting WS Audio Receiver (Venv Python)..."
if [ -f "/home/angelo/lescall/calling-agent/.env" ]; then
    set -a
    source /home/angelo/lescall/calling-agent/.env
    set +a
fi

nohup /home/angelo/lescall/calling-agent/venv/bin/python /home/angelo/lescall/ws_audio_receiver.py >> "$RUN_DIR/ws_audio_receiver.log" 2>&1 &
WS_PID=$!
echo $WS_PID > "$RUN_DIR/ws_audio_receiver.pid"

if ! wait_for_port 9001 "ws_audio_receiver" 15; then
    echo "ERROR: ws_audio_receiver.py failed to start or bind."
    cat "$RUN_DIR/ws_audio_receiver.log"
    exit 1
fi

# ── 3. Ensure FreeSWITCH is running ───────────────────────────────────────────
echo "[3/3] Checking FreeSWITCH..."
FS_WAS_DOWN=0

if ! systemctl is-active --quiet freeswitch 2>/dev/null; then
    FS_WAS_DOWN=1
    echo "[WARN] FreeSWITCH is not running. Attempting to start..."

    # Check for corrupted core.db (symptom of OOM kill) — delete so FS can recreate it
    FS_DB=/var/lib/freeswitch/db/core.db
    if [ -f "$FS_DB" ]; then
        # Quick corruption check: valid SQLite starts with "SQLite format 3\000"
        if ! file "$FS_DB" 2>/dev/null | grep -q "SQLite"; then
            echo "[WARN] core.db appears corrupted (OOM kill?). Removing for fresh creation."
            mv "$FS_DB" "${FS_DB}.corrupted_$(date +%Y%m%d_%H%M%S)"
        fi
    fi

    # Reset systemd failure counter so it can retry even if past start limit
    systemctl reset-failed freeswitch 2>/dev/null || true
    systemctl start freeswitch 2>/dev/null
    sleep 3

    if ! systemctl is-active --quiet freeswitch 2>/dev/null; then
        echo "[WARN] systemctl start failed. Trying direct start as fallback..."
        # Direct fallback: start freeswitch as freeswitch user
        su -s /bin/sh freeswitch -c "/usr/bin/freeswitch -u freeswitch -g freeswitch -ncwait -nonat" >> "$RUN_DIR/freeswitch_start.log" 2>&1 &
        sleep 5
    fi
fi

# Wait for FreeSWITCH SIP port (external profile = 5080)
FS_READY=0
FS_TIMEOUT=20
for i in $(seq 1 $FS_TIMEOUT); do
    if ss -ltn | grep -qE ":(5060|5080) "; then
        FS_READY=1
        break
    fi
    sleep 1
done

if [ $FS_READY -eq 1 ]; then
    echo "[CHECK] freeswitch running"
else
    echo "[FAIL] FreeSWITCH SIP port (5060/5080) not listening after ${FS_TIMEOUT}s"
    echo ""
    echo "  Diagnostic: systemctl status freeswitch"
    systemctl status freeswitch 2>/dev/null | head -20 || true
    echo ""
    echo "  If OOM-killed, free disk/memory first:"
    echo "    du -sh /home/angelo/lescall/recordings/ /home/angelo/lescall/phase14_runtime/logs/"
    echo "    rm -rf /home/angelo/lescall/phase14_runtime/logs/run_202605*"
    echo "    journalctl -u freeswitch -n 50"
    echo ""
    echo "ERROR: FreeSWITCH is required for call routing. Startup INCOMPLETE."
    echo "       Python services (ports 9000/9001/9004) are running but calls will fail."
    # Do not exit — Python services are up, operator may fix FS separately
fi

# ── 4. Optional: Start stt_stream_worker.py ───────────────────────────────────
if [ "$ENABLE_STT_WORKER" == "1" ]; then
    echo "[OPTIONAL] Starting STT Stream Worker..."
    nohup python3 /home/angelo/lescall/stt_stream_worker.py >> "$RUN_DIR/stt_worker.log" 2>&1 &
    STT_PID=$!
    echo $STT_PID > "$RUN_DIR/stt_worker.pid"
    wait_for_port 9002 "stt_stream_worker" 10
fi

# ── 5. Gateway DNS check ──────────────────────────────────────────────────────
GATEWAY_HOST="16584853209835561.lzt.plivo.com"
if host "$GATEWAY_HOST" >/dev/null 2>&1 || nslookup "$GATEWAY_HOST" >/dev/null 2>&1; then
    echo "[CHECK] gateway available ($GATEWAY_HOST resolves)"
else
    echo "[WARN]  gateway DNS failed for $GATEWAY_HOST — check network or Plivo configuration"
fi

# ── 6. ESL readiness check ────────────────────────────────────────────────────
# socket_server.py is listening on 9000. FreeSWITCH connects here on inbound calls.
# Verify socket_server can be reached from localhost (simulates FS's perspective).
if timeout 2 bash -c "echo '' > /dev/tcp/127.0.0.1/9000" 2>/dev/null; then
    echo "[CHECK] esl connected (port 9000 accepts connections)"
else
    echo "[FAIL]  esl not reachable on port 9000"
fi

# ── 7. Call-bridge log filter ─────────────────────────────────────────────────
nohup tail -f "$RUN_DIR/ws_audio_receiver.log" | grep --line-buffered "call_bridge" >> "$RUN_DIR/call_bridge.log" 2>&1 &
FILTER_PID=$!
echo $FILTER_PID > "$RUN_DIR/call_bridge_filter.pid"

# ── Final Summary ─────────────────────────────────────────────────────────────
echo "------------------------------------------------"
echo "Startup validation complete."
echo ""
echo "  [CHECK] port 9000 reachable  (ESL controller)"
echo "  [CHECK] port 9001 reachable  (WebSocket audio receiver)"
echo "  [CHECK] port 9004 reachable  (playback injection listener)"
if [ $FS_READY -eq 1 ]; then
    echo "  [CHECK] freeswitch running"
    echo "  [CHECK] esl connected"
else
    echo "  [FAIL]  freeswitch NOT running — calls will fail until fixed"
fi
echo ""
echo "Use ./tail_logs.sh to monitor."
echo "Use ./check_runtime.sh to re-verify status."
