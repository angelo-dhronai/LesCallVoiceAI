#!/usr/bin/env bash
# Container entrypoint for the background workers, one loop, staggered cadence:
#   every  5 min — usage-metering reconcile (billing referee; CDR join)
#   every  5 min — outbox delivery (client-ERP lead hand-off)
#   every 15 min — recording sync (GCS upload catch-up + local retention)
#   every 60 min — log shipper (rotated/idle logs -> gs://lescall-logs)
set -uo pipefail
cd /app

TICK=300
i=0
echo "workers loop up (tick=${TICK}s)"
while true; do
    python -m shared.usage_metering --once           || echo "WARN metering pass failed"
    python -m shared.outbox_worker --once >/dev/null || echo "WARN outbox pass failed"
    if (( i % 3 == 0 )); then
        python -m shared.recording_storage --once    || echo "WARN recsync pass failed"
    fi
    if (( i % 12 == 0 )); then
        python -m shared.log_shipper --once          || echo "WARN logship pass failed"
    fi
    i=$((i + 1))
    sleep "$TICK"
done
