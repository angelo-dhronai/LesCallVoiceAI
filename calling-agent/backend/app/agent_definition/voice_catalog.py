"""
Voice/language catalog — the single source of truth for what the platform's
TTS/STT layer accepts. Re-exported by schema.py and consumed by tts_sarvam.py
and the Copilot; nothing else should hardcode model or speaker names.

PLATFORM_LANGUAGES is deliberately generous (all Sarvam-supported Indic codes):
`from_dict` warn-drops only codes Sarvam cannot speak at all. The deep
conversational experience (fillers, register notes, deterministic responses)
is still authored for hi/en/gu — the interview sets that expectation.
"""

from __future__ import annotations

# The only TTS models Sarvam accepts; anything else 400s every utterance and
# the call goes silent. Platform default is bulbul:v3 unless explicitly set.
VALID_TTS_MODELS = ("bulbul:v2", "bulbul:v3-beta", "bulbul:v3")
DEFAULT_TTS_MODEL = "bulbul:v3"
DEFAULT_TTS_SPEAKER = "shubh"

# Short BCP-47 codes the runtime/STT/TTS chain can carry end-to-end.
PLATFORM_LANGUAGES = (
    "en", "hi", "gu", "mr", "ta", "te", "kn", "ml", "bn", "pa", "od",
)

# speaker -> {"gender": ..., "models": (...)} — informational (warn-only
# validation): Sarvam adds speakers faster than we ship, so an unknown
# speaker is a warning, never a rejection.
SPEAKER_CATALOG: dict[str, dict] = {
    "simran":   {"gender": "female", "models": VALID_TTS_MODELS},
    "anushka":  {"gender": "female", "models": VALID_TTS_MODELS},
    "manisha":  {"gender": "female", "models": VALID_TTS_MODELS},
    "vidya":    {"gender": "female", "models": VALID_TTS_MODELS},
    "ishita":   {"gender": "female", "models": VALID_TTS_MODELS},
    "shubh":    {"gender": "male",   "models": VALID_TTS_MODELS},
    "abhilash": {"gender": "male",   "models": VALID_TTS_MODELS},
    "karun":    {"gender": "male",   "models": VALID_TTS_MODELS},
}


def speaker_gender(speaker: str) -> str:
    """'male' / 'female' / '' (unknown speaker)."""
    return SPEAKER_CATALOG.get((speaker or "").strip().lower(), {}).get("gender", "")
