#!/bin/bash
# stop_all.sh - Stop all Phase 14 services

RUNTIME_DIR="/home/angelo/lescall/phase14_runtime"
LATEST_RUN="$RUNTIME_DIR/logs/latest"

if [ ! -d "$LATEST_RUN" ]; then
    echo "No latest run found in $LATEST_RUN"
    exit 1
fi

echo "Stopping Phase 14 services in $LATEST_RUN..."

for pid_file in "$LATEST_RUN"/*.pid; do
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        name=$(basename "$pid_file" .pid)
        if ps -p "$pid" > /dev/null; then
            echo "Killing $name (PID $pid)..."
            kill "$pid"
        else
            echo "$name (PID $pid) already stopped."
        fi
        rm "$pid_file"
    fi
done

# Cleanup any remaining processes just in case
pkill -f "stt_stream_worker.py"
pkill -f "ws_audio_receiver.py"
pkill -f "socket_server.py"

echo "All services stopped."
