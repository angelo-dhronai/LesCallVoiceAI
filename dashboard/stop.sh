#!/bin/bash
# stop.sh — Stop the LesCall Dashboard server

DASHBOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DASHBOARD_DIR/dashboard.pid"

stopped=0

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        kill "$PID"
        echo "Dashboard stopped (PID $PID)"
        stopped=1
    fi
    rm -f "$PID_FILE"
fi

# Also kill any other processes holding port 8080 or matching the server path
PIDS=$(pgrep -f "dashboard/server\.py" 2>/dev/null)
if [ -n "$PIDS" ]; then
    echo "$PIDS" | xargs kill 2>/dev/null
    echo "Killed remaining dashboard processes: $PIDS"
    stopped=1
fi

if [ "$stopped" -eq 0 ]; then
    echo "Dashboard not running"
fi
