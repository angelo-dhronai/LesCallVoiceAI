"""
Gemini LLM configuration — single source of truth for LLM credentials.

TO ROTATE THE API KEY:
    Edit calling-agent/.env → set GEMINI_API_KEY=<new-key>
    No other file needs to be changed.

TO CHANGE THE MODEL:
    Edit GEMINI_MODEL below.

Note: Whisper STT fallback uses a SEPARATE OPENAI_API_KEY (see stt/openai_whisper.py).
      This file controls only the conversation LLM, not the STT fallback.
"""

import os

# ── Credentials ────────────────────────────────────────────────────────────────
# Variable name : GEMINI_API_KEY
# File          : calling-agent/.env
# Set it there; never hardcode the key in source.
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")

# ── Endpoint ───────────────────────────────────────────────────────────────────
# Gemini's OpenAI-compatible REST endpoint.
# The openai SDK is used with this base_url — no SDK replacement needed.
GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

# ── Model ──────────────────────────────────────────────────────────────────────
GEMINI_MODEL: str = "models/gemini-2.5-flash"
