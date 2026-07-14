# Shared image for the LesCall Python services (api / runtime / workers).
# Code + mutable state stay on the host bind-mount (/app); this image is the
# interpreter + dependencies only, so image rebuilds are only needed when
# requirements change — deploys of code are `docker compose restart`.
#
# Build (from repo root):
#   docker build -f deploy/Dockerfile.app -t lescall-app:latest .
FROM python:3.11-slim

# ffmpeg: recording normalization; libportaudio2: sounddevice import
# (module-level import in the TTS path even in telephony mode).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libportaudio2 curl \
    && rm -rf /var/lib/apt/lists/*

COPY calling-agent/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt openpyxl \
    && rm /tmp/requirements.txt

WORKDIR /app
ENV PYTHONUNBUFFERED=1
