#!/usr/bin/env bash
# Sarvam usage status report
# Usage: bash scripts/check_sarvam_credits.sh [--reset]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$REPO_DIR/calling-agent/.env"

# Load SARVAM_API_KEY from .env if not already set
if [[ -z "${SARVAM_API_KEY:-}" && -f "$ENV_FILE" ]]; then
    SARVAM_API_KEY="$(grep -E '^SARVAM_API_KEY=' "$ENV_FILE" | cut -d= -f2- | tr -d '[:space:]')"
    export SARVAM_API_KEY
fi

# Prefer the venv Python; fall back to system Python
PYTHON="${REPO_DIR}/calling-agent/venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="$(command -v python3 || command -v python)"
fi

exec "$PYTHON" "$SCRIPT_DIR/sarvam_credits.py" "$@"
