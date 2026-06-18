#!/usr/bin/env bash
# Phase-12 pipeline launcher.
#
# Boots the four processes that make up the realtime AI calling
# pipeline in the order required for Phase 12 to work:
#
#   socket_server.py            127.0.0.1:9000  (FreeSWITCH outbound ESL)
#                               127.0.0.1:9004  (Phase-12 playback inject listener)
#   ws_audio_receiver.py        0.0.0.0:9001    (mod_audio_stream WS)
#   stt_stream_worker.py        127.0.0.1:9002  (PCM forward target)
#   conversation_orchestrator   127.0.0.1:9003  (final-transcript target)
#
# Provider selection is driven by env vars; sensible defaults below.
# Override anything by exporting before invoking, e.g.:
#   TTS_PROVIDER=sarvam ./start_phase12.sh
#
# Logs:   /tmp/phase12_logs/<service>.log
# Stop:   pkill -f 'socket_server\.py|ws_audio_receiver\.py|stt_stream_worker\.py|conversation_orchestrator\.py'

set -u

ROOT="${ROOT:-/home/angelo/lescall}"
LOG_DIR="${LOG_DIR:-/tmp/phase12_logs}"

# --- Provider config (defaults are mock-everything except STT) -----------
export STT_PROVIDER="${STT_PROVIDER:-sarvam}"      # sarvam | mock
export LLM_PROVIDER="${LLM_PROVIDER:-mock}"        # mock | openai
export TTS_PROVIDER="${TTS_PROVIDER:-mock}"        # mock | sarvam

# Sarvam key (required when STT_PROVIDER=sarvam OR TTS_PROVIDER=sarvam).
# Pre-set if absent; replace with your own as needed.
: "${SARVAM_API_KEY:=***REMOVED***}"
export SARVAM_API_KEY

# OpenAI key (only required when LLM_PROVIDER=openai).
: "${OPENAI_API_KEY:=}"
: "${OPENAI_MODEL:=gpt-4o-mini}"
export OPENAI_API_KEY OPENAI_MODEL

# --- Phase-12 plumbing ---------------------------------------------------
export ORCH_FORWARD_ENABLED="${ORCH_FORWARD_ENABLED:-true}"
export ORCH_FORWARD_HOST="${ORCH_FORWARD_HOST:-127.0.0.1}"
export ORCH_FORWARD_PORT="${ORCH_FORWARD_PORT:-9003}"
export PLAYBACK_LISTEN_ENABLED="${PLAYBACK_LISTEN_ENABLED:-true}"
export PLAYBACK_LISTEN_HOST="${PLAYBACK_LISTEN_HOST:-127.0.0.1}"
export PLAYBACK_LISTEN_PORT="${PLAYBACK_LISTEN_PORT:-9004}"

# --- Phase-9/10 plumbing (UNCHANGED from prior phases) -------------------
export AUDIO_STREAM_WS_URL="${AUDIO_STREAM_WS_URL:-ws://127.0.0.1:9001}"
export AUDIO_STREAM_SAMPLE_RATE="${AUDIO_STREAM_SAMPLE_RATE:-8000}"
export STT_FORWARD_HOST="${STT_FORWARD_HOST:-127.0.0.1}"
export STT_FORWARD_PORT="${STT_FORWARD_PORT:-9002}"

# --- Filesystem fixtures -------------------------------------------------
mkdir -p "$LOG_DIR" /tmp/ai_responses /tmp/orch_transcripts \
         /tmp/stt_transcripts /tmp/esl_recordings

# Refuse to start if any port is busy.
for p in 9000 9001 9002 9003 9004; do
  if ss -tlnp 2>/dev/null | grep -q ":$p "; then
    echo "ERROR: port $p is already bound. Stop the existing pipeline first." >&2
    ss -tlnp 2>/dev/null | grep ":$p " >&2
    exit 1
  fi
done

cd "$ROOT"

start_one() {
  local label="$1"; shift
  local script="$1"; shift
  echo "[$label] starting $script"
  nohup python3 -u "$ROOT/$script" "$@" \
        >"$LOG_DIR/$label.log" 2>&1 &
  disown
  echo "[$label] pid=$!"
}

start_one socket_server  socket_server.py
sleep 0.4
start_one ws_receiver    ws_audio_receiver.py
sleep 0.3
start_one stt_worker     stt_stream_worker.py
sleep 0.3
start_one orchestrator   conversation_orchestrator.py
sleep 1.0

echo
echo "=== ports ==="
ss -tlnp 2>/dev/null | grep -E ':900[0-4]' | awk '{print $4, $6}'
echo
echo "=== providers ==="
echo "  STT_PROVIDER=$STT_PROVIDER"
echo "  LLM_PROVIDER=$LLM_PROVIDER"
echo "  TTS_PROVIDER=$TTS_PROVIDER"
echo
echo "=== logs ==="
echo "  $LOG_DIR/{socket_server,ws_receiver,stt_worker,orchestrator}.log"
echo
echo "Place a call now. Stop with:"
echo "  pkill -f 'socket_server\\.py|ws_audio_receiver\\.py|stt_stream_worker\\.py|conversation_orchestrator\\.py'"
