#!/bin/bash
# start.sh — Launch the LesCall Dashboard server
#
# Usage: bash /home/angelo/lescall/dashboard/start.sh [--bg]
# Port:  8080
# URL:   http://localhost:8080

VENV_PYTHON="/home/angelo/lescall/calling-agent/venv/bin/python"
DASHBOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER="$DASHBOARD_DIR/server.py"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found at $VENV_PYTHON"
    exit 1
fi

# Kill any existing dashboard process before starting
EXISTING=$(pgrep -f "dashboard/server\.py" 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "Stopping existing dashboard (PID $EXISTING)..."
    echo "$EXISTING" | xargs kill 2>/dev/null
    sleep 1
fi
rm -f "$DASHBOARD_DIR/dashboard.pid"

echo "Starting LesCall Dashboard..."
echo "URL: http://localhost:8080"

if [ "$1" = "--bg" ]; then
    LOG="$DASHBOARD_DIR/dashboard.log"
    nohup "$VENV_PYTHON" "$SERVER" >> "$LOG" 2>&1 &
    PID=$!
    echo "$PID" > "$DASHBOARD_DIR/dashboard.pid"
    sleep 1
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Dashboard started (PID $PID) — http://localhost:8080"
        echo "Logs: $LOG"
    else
        echo "ERROR: Dashboard failed to start. Check $LOG"
        exit 1
    fi
else
    exec "$VENV_PYTHON" "$SERVER"
fi
