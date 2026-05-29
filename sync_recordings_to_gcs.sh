#!/bin/bash

RECORDINGS_DIR="/home/angelo/lescall/recordings"
BUCKET="gs://lescall-recordings"
UPLOADED_LOG="/home/angelo/.recordings_uploaded.log"
POLL_INTERVAL=10  # seconds between checks

touch "$UPLOADED_LOG"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

upload_file() {
    local file="$1"
    local relative="${file#$RECORDINGS_DIR/}"

    if grep -qxF "$relative" "$UPLOADED_LOG" 2>/dev/null; then
        return 0
    fi

    log "Uploading: $relative"
    if gcloud storage cp "$file" "$BUCKET/$relative" --quiet 2>&1; then
        echo "$relative" >> "$UPLOADED_LOG"
        log "Done: $relative"
    else
        log "FAILED: $relative"
    fi
}

upload_all() {
    while IFS= read -r -d '' file; do
        upload_file "$file"
    done < <(find "$RECORDINGS_DIR" -type f -print0 | sort -z)
}

log "Starting GCS sync: $RECORDINGS_DIR -> $BUCKET"
log "Poll interval: ${POLL_INTERVAL}s"

while true; do
    upload_all
    sleep "$POLL_INTERVAL"
done
