#!/bin/bash
# stop_all.sh - Stop all Phase 14 services

RUNTIME_DIR="/home/angelo/lescall/phase14_runtime"
LATEST_RUN="$RUNTIME_DIR/logs/latest"

if [ ! -d "$LATEST_RUN" ]; then
    echo "No latest run found in $LATEST_RUN"
    echo "Falling back to pkill only..."
fi

echo "Stopping Phase 14 services..."

# 1. Kill PIDs from pid files (graceful)
if [ -d "$LATEST_RUN" ]; then
    for pid_file in "$LATEST_RUN"/*.pid; do
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            name=$(basename "$pid_file" .pid)
            if ps -p "$pid" > /dev/null 2>&1; then
                echo "Killing $name (PID $pid)..."
                kill "$pid" 2>/dev/null
            else
                echo "$name (PID $pid) already stopped."
            fi
            rm -f "$pid_file"
        fi
    done
fi

# 2. Kill by script name (catches orphans not in pid files)
for script in "stt_stream_worker.py" "ws_audio_receiver.py" "socket_server.py"; do
    pkill -f "$script" 2>/dev/null && echo "Killed orphan: $script" || true
done

# 3. Kill any tail/grep filter processes that may have escaped
pkill -f "tail -f.*ws_audio_receiver.log" 2>/dev/null || true

# 4. Wait for processes to terminate
sleep 1

# 5. Force kill anything still holding our ports
for port in 9000 9001 9002 9004; do
    PIDS=$(ss -ltnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | sort -u)
    if [ -n "$PIDS" ]; then
        echo "Force killing processes still on port $port: $PIDS"
        echo "$PIDS" | xargs -r kill -9 2>/dev/null || true
    fi
done

# 6. Verify ports are free
sleep 1
LINGERING=0
for port in 9000 9001 9004; do
    if ss -ltn | grep -q ":$port "; then
        echo "[WARN] Port $port is still bound after stop"
        LINGERING=1
    fi
done

if [ $LINGERING -eq 1 ]; then
    echo "[WARN] Some ports still bound — run reset_runtime.sh to force-clear"
else
    echo "All Phase 14 services stopped."
fi
