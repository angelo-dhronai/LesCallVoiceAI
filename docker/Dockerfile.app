# lescall app image — runs the telephony bridge + AI pipeline.
# One image, two compose services (socket_server, ws_audio_receiver) via different commands.
# Python 3.11 is REQUIRED: ws_audio_receiver.py uses the stdlib `audioop` module,
# which was removed in Python 3.13.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# libportaudio2 only needed if local-mic mode is ever used; harmless for telephony.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libportaudio2 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps first for layer caching.
COPY calling-agent/requirements.txt /app/calling-agent/requirements.txt
RUN pip install -r /app/calling-agent/requirements.txt

# Source. (.dockerignore strips venv/, recordings/, __pycache__, archive/, data/*.db)
COPY calling-agent/ /app/calling-agent/
COPY shared/ /app/shared/
COPY outbound/ /app/outbound/
COPY socket_server.py ws_audio_receiver.py conversation_call_bridge.py call_audit.py /app/

# call_bridge expects `calling-agent` importable as `backend.*`; it does
# sys.path.insert(0, abspath('calling-agent')), so /app must be the CWD.
ENV PYTHONPATH=/app:/app/calling-agent

# Default command is overridden per-service in docker-compose.yml.
CMD ["python", "socket_server.py"]
