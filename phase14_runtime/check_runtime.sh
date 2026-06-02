#!/bin/bash
# check_runtime.sh - Verify health of Phase 14 runtime

RUNTIME_DIR="/home/angelo/lescall/phase14_runtime"
LATEST_LOGS="$RUNTIME_DIR/logs/latest"

echo "--- Phase 14 Runtime Status ---"

if [ ! -d "$LATEST_LOGS" ]; then
    echo "[FAIL] No latest logs directory found at $LATEST_LOGS"
    exit 1
fi

# 1. Check PIDs from files
echo "Checking PIDs from log directory..."
PASS=1
for pid_file in "$LATEST_LOGS"/*.pid; do
    [ -e "$pid_file" ] || continue
    name=$(basename "$pid_file" .pid)
    pid=$(cat "$pid_file")
    if ps -p "$pid" > /dev/null; then
        echo "[OK] $name (PID $pid) is running."
    else
        echo "[FAIL] $name (PID $pid) is NOT running!"
        PASS=0
    fi
done

# 2. Check Listening Ports
echo "--- Listening Ports ---"
ss -ltnp | grep -E '9000|9001|9002|9004'

# 3. Verify exactly one listener per port
echo "--- Port Ownership Verification ---"
PORTS=(9000 9001 9004)
if [ -f "$LATEST_LOGS/stt_worker.pid" ]; then
    PORTS+=(9002)
fi

for port in "${PORTS[@]}"; do
    COUNT=$(ss -ltn | grep -c ":$port ")
    if [ "$COUNT" -eq 1 ]; then
        echo "[OK] Port $port has exactly one listener."
    elif [ "$COUNT" -gt 1 ]; then
        echo "[FAIL] Port $port has MULTIPLE listeners ($COUNT)!"
        PASS=0
    else
        echo "[FAIL] Port $port has NO listeners!"
        PASS=0
    fi
done

# 4. Verify ws_audio_receiver python environment
echo "--- Process Environment Verification ---"
WS_PID=$(cat "$LATEST_LOGS/ws_audio_receiver.pid" 2>/dev/null)
if [ -n "$WS_PID" ] && ps -p "$WS_PID" > /dev/null; then
    CMDLINE=$(cat /proc/$WS_PID/cmdline | tr '\0' ' ')
    if [[ "$CMDLINE" == *"/calling-agent/venv/bin/python"* ]]; then
        echo "[OK] ws_audio_receiver command line uses venv: $CMDLINE"
    else
        echo "[FAIL] ws_audio_receiver command line does NOT use venv! (Cmd: $CMDLINE)"
        PASS=0
    fi
else
    echo "[FAIL] ws_audio_receiver PID not found or process dead."
    PASS=0
fi

# 5. Check FreeSWITCH
echo "--- FreeSWITCH Status ---"
if systemctl is-active --quiet freeswitch 2>/dev/null; then
    echo "[OK] freeswitch service is active"
else
    echo "[FAIL] freeswitch service is NOT active"
    PASS=0
fi

# Check SIP ports
if ss -ltn 2>/dev/null | grep -qE ":(5060|5080) "; then
    echo "[OK] freeswitch SIP port (5060/5080) is listening"
else
    echo "[FAIL] freeswitch SIP port (5060/5080) is NOT listening"
    PASS=0
fi

# 6. Disk space warning
echo "--- Disk Space ---"
AVAIL_KB=$(df -k / | awk 'NR==2{print $4}')
if [ -n "$AVAIL_KB" ] && [ "$AVAIL_KB" -lt 2097152 ]; then
    echo "[WARN] Less than 2GB free on /: $(df -h / | awk 'NR==2{print $4}') available"
else
    echo "[OK] Disk space: $(df -h / | awk 'NR==2{print $4}') free"
fi

echo "------------------------------------------------"
if [ $PASS -eq 1 ]; then
    echo "OVERALL STATUS: PASS"
    exit 0
else
    echo "OVERALL STATUS: FAIL"
    exit 1
fi
