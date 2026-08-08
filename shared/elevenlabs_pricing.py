"""The ElevenLabs rate card — companion to sarvam_pricing.py, same doctrine:
one source of truth, everything env-overridable, ₹ figures for the same
dashboards.

ElevenLabs bills in CREDITS against the workspace subscription:

    TTS  eleven_flash_v2_5 / turbo_v2_5      0.5 credits / character
         eleven_multilingual_v2 / v3         1.0 credit  / character
    STT  scribe (batch)                      billed per HOUR of audio

The ₹ value of a credit depends entirely on the subscription tier
(Starter $5/30K ≈ ₹0.014/credit; Scale $330/11M ≈ ₹0.0025/credit) — set
EL_INR_PER_CREDIT to the tier actually purchased or every figure here is a
default-tier estimate. Defaults below assume the current Starter workspace
(2026-08-07) and USD→INR 88.

Unlike Sarvam, ElevenLabs HAS a quota endpoint (GET /v1/user/subscription),
so these estimates are cross-checkable against the real meter — the
watchdog does exactly that. Per-request exactness is also available from
the `character-cost` response header when we want to true-up.
"""

from __future__ import annotations

import os

USD_TO_INR = float(os.environ.get("EL_USD_TO_INR", "88.0"))

# Starter: $5 / 30,000 credits (annual billing shows 40k grant).
EL_INR_PER_CREDIT = float(
    os.environ.get("EL_INR_PER_CREDIT", str(5.0 * USD_TO_INR / 30_000)))

# Credits per character by model family.
EL_TTS_CREDITS_PER_CHAR_FLASH = float(
    os.environ.get("EL_TTS_CREDITS_PER_CHAR_FLASH", "0.5"))
EL_TTS_CREDITS_PER_CHAR_HQ = float(
    os.environ.get("EL_TTS_CREDITS_PER_CHAR_HQ", "1.0"))

# Scribe batch STT — $0.40 / hour equivalent (API list price band).
EL_STT_PRICE_PER_HOUR_INR = float(
    os.environ.get("EL_STT_PRICE_PER_HOUR_INR", str(0.40 * USD_TO_INR)))


def tts_credits(chars: float, model: str = "eleven_flash_v2_5") -> float:
    per_char = (EL_TTS_CREDITS_PER_CHAR_FLASH
                if ("flash" in (model or "") or "turbo" in (model or ""))
                else EL_TTS_CREDITS_PER_CHAR_HQ)
    return chars * per_char


def tts_cost(chars: float, model: str = "eleven_flash_v2_5") -> float:
    """₹ for EL TTS. Takes characters SUBMITTED (same contract as Sarvam)."""
    return round(tts_credits(chars, model) * EL_INR_PER_CREDIT, 6)


def stt_cost(audio_seconds: float) -> float:
    """₹ for EL Scribe STT. No per-request ceiling documented — raw seconds."""
    return round((audio_seconds / 3600.0) * EL_STT_PRICE_PER_HOUR_INR, 6)


def rate_card() -> dict:
    return {
        "inr_per_credit": EL_INR_PER_CREDIT,
        "tts_credits_per_char_flash": EL_TTS_CREDITS_PER_CHAR_FLASH,
        "tts_credits_per_char_hq": EL_TTS_CREDITS_PER_CHAR_HQ,
        "stt_per_hour_inr": EL_STT_PRICE_PER_HOUR_INR,
        "usd_to_inr": USD_TO_INR,
        "currency": "INR",
        "source": "https://elevenlabs.io/pricing (tier-dependent — set EL_INR_PER_CREDIT)",
    }
