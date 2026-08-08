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

# ─── ElevenLabs (provider switch, 2026-08-07) ────────────────────────────
# Realtime models only — eleven_v3 speaks 70+ languages but is not a
# real-time model (seconds of TTFB, rejects optimize_streaming_latency), so
# it is valid in configs but never a routing default.
EL_VALID_TTS_MODELS = (
    "eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_multilingual_v2",
    "eleven_v3",
)
EL_DEFAULT_TTS_MODEL = "eleven_flash_v2_5"
ALL_VALID_TTS_MODELS = VALID_TTS_MODELS + EL_VALID_TTS_MODELS


def model_provider(model: str) -> str:
    """'sarvam' | 'elevenlabs' from a TTS model name."""
    return "elevenlabs" if (model or "").startswith("eleven_") else "sarvam"

# Sarvam's Indic roster — what bulbul/saarika can speak/hear.
SARVAM_LANGUAGES = (
    "en", "hi", "gu", "mr", "ta", "te", "kn", "ml", "bn", "pa", "od",
)

# eleven_flash_v2_5 / eleven_turbo_v2_5 roster (32 languages, docs
# elevenlabs.io/docs/overview/models, checked 2026-08-07). These are the
# languages ElevenLabs can serve at telephony latency (~176ms TTFB benched
# from this VM). Scribe STT hears all of these (90+ language model).
EL_REALTIME_LANGUAGES = (
    "en", "hi", "ta",                                    # overlap with Sarvam
    "ar", "bg", "cs", "da", "de", "el", "es", "fi", "fil", "fr", "hr",
    "hu", "id", "it", "ja", "ko", "ms", "nl", "no", "pl", "pt", "ro",
    "ru", "sk", "sv", "tr", "uk", "vi", "zh",
)

# Short BCP-47 codes the runtime/STT/TTS chain can carry end-to-end — the
# UNION of both providers' realtime rosters (owner directive 2026-08-07:
# "choose any language provided by the two api providers"). Order: the
# legacy Sarvam 11 first (existing configs sort-stable), then the
# ElevenLabs-only additions.
PLATFORM_LANGUAGES = SARVAM_LANGUAGES + tuple(
    l for l in EL_REALTIME_LANGUAGES if l not in SARVAM_LANGUAGES
)

LANGUAGE_LABELS = {
    "en": "English", "hi": "Hindi", "gu": "Gujarati", "mr": "Marathi",
    "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam",
    "bn": "Bengali", "pa": "Punjabi", "od": "Odia",
    "ar": "Arabic", "bg": "Bulgarian", "cs": "Czech", "da": "Danish",
    "de": "German", "el": "Greek", "es": "Spanish", "fi": "Finnish",
    "fil": "Filipino", "fr": "French", "hr": "Croatian", "hu": "Hungarian",
    "id": "Indonesian", "it": "Italian", "ja": "Japanese", "ko": "Korean",
    "ms": "Malay", "nl": "Dutch", "no": "Norwegian", "pl": "Polish",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sk": "Slovak",
    "sv": "Swedish", "tr": "Turkish", "uk": "Ukrainian", "vi": "Vietnamese",
    "zh": "Chinese",
}


# The one EL model that speaks the full 70+ language set (incl. Gujarati).
# NOT a realtime model: ~1.4s TTFB benched from this VM 2026-08-07 (vs
# ~176ms flash / ~350ms bulbul) — it is the strict-mode fallback, never a
# hybrid-mode routing target.
EL_FALLBACK_TTS_MODEL = "eleven_v3"


def tts_route(language: str, provider_pref: str = "sarvam",
              strict: bool = False) -> tuple[str, str]:
    """(provider, default_model) serving `language` at realtime latency.

    provider_pref is the platform/agent-level preference (VOICE_PROVIDER):
    a preferred provider that CANNOT speak the language is overruled — a
    Gujarati agent under provider_pref=elevenlabs still speaks through
    bulbul (flash has no gu), and a Spanish agent under provider_pref=sarvam
    still speaks through flash (bulbul has no es). This routing is what
    makes the union roster safe to expose.

    strict=True (VOICE_PROVIDER_STRICT, owner directive 2026-08-07: "remove
    the sarvam fallback, I only want to test the ElevenLabs api"): NOTHING
    routes to the other provider. Under an elevenlabs pref, languages flash
    cannot speak ride eleven_v3 — slower (~1.4s TTFB) but pure-EL. The
    knob is deliberately symmetric: strict+sarvam pins everything to bulbul.
    """
    lang = (language or "en").split("-")[0].lower()
    el_ok = lang in EL_REALTIME_LANGUAGES
    sv_ok = lang in SARVAM_LANGUAGES
    if strict:
        if provider_pref == "elevenlabs":
            return ("elevenlabs",
                    EL_DEFAULT_TTS_MODEL if el_ok else EL_FALLBACK_TTS_MODEL)
        return ("sarvam", DEFAULT_TTS_MODEL)
    if provider_pref == "elevenlabs":
        if el_ok:
            return ("elevenlabs", EL_DEFAULT_TTS_MODEL)
        if sv_ok:
            return ("sarvam", DEFAULT_TTS_MODEL)
        return ("elevenlabs", EL_DEFAULT_TTS_MODEL)   # unknown code: EL tries
    if sv_ok:
        return ("sarvam", DEFAULT_TTS_MODEL)
    if el_ok:
        return ("elevenlabs", EL_DEFAULT_TTS_MODEL)
    return ("sarvam", DEFAULT_TTS_MODEL)


def stt_primary_provider(supported: list[str] | tuple,
                         provider_pref: str = "sarvam",
                         strict: bool = False) -> str:
    """Which provider is the STT primary for an agent's language set.

    Sarvam saarika only hears the Indic roster — an agent supporting any
    language outside it MUST ride Scribe (which hears all 39). Otherwise the
    platform preference stands. (Whisper hedge/fallback is unaffected.)

    strict pins the preference outright — Scribe hears every platform
    language, so strict+elevenlabs needs no capability escape.
    """
    if strict and provider_pref in ("sarvam", "elevenlabs"):
        return provider_pref
    langs = [(l or "").split("-")[0].lower() for l in (supported or [])]
    if any(l not in SARVAM_LANGUAGES for l in langs if l):
        return "elevenlabs"
    return provider_pref if provider_pref in ("sarvam", "elevenlabs") else "sarvam"


def language_capabilities() -> dict:
    """Per-language capability map for the dashboard/builder pickers."""
    out = {}
    for lang in PLATFORM_LANGUAGES:
        out[lang] = {
            "code": lang,
            "label": LANGUAGE_LABELS.get(lang, lang),
            "stt": (["sarvam", "elevenlabs"] if lang in SARVAM_LANGUAGES
                    else ["elevenlabs"]),
            "tts": ([p for p, ok in (
                ("sarvam", lang in SARVAM_LANGUAGES),
                ("elevenlabs", lang in EL_REALTIME_LANGUAGES)) if ok]),
            "deep_support": lang in ("hi", "en", "gu"),
        }
    return out

# speaker -> {"gender": ..., "models": (...)} — informational (warn-only
# validation): Sarvam adds speakers faster than we ship, so an unknown
# speaker is a warning, never a rejection.
#
# The bulbul:v3 roster below is the OFFICIAL list (23 male + 14 female,
# docs.sarvam.ai, verified against the live API 2026-07-24 — the API 400s
# any other speaker on v3 and the call goes silent). The legacy block at the
# bottom is v2-only: those speakers were removed in v3, which is why the old
# catalog entries claiming VALID_TTS_MODELS for them were a dead-air trap.
_V3 = ("bulbul:v3-beta", "bulbul:v3")
_V3_MALE = (
    "shubh", "aditya", "rahul", "rohan", "amit", "dev", "ratan", "varun",
    "manan", "sumit", "kabir", "aayan", "ashutosh", "advait", "anand",
    "tarun", "sunny", "mani", "gokul", "vijay", "mohit", "rehan", "soham",
)
_V3_FEMALE = (
    "ritu", "priya", "neha", "pooja", "simran", "kavya", "ishita", "shreya",
    "roopa", "tanya", "shruti", "suhani", "kavitha", "rupali",
)
SPEAKER_CATALOG: dict[str, dict] = {
    **{s: {"gender": "male", "models": _V3} for s in _V3_MALE},
    **{s: {"gender": "female", "models": _V3} for s in _V3_FEMALE},
    # bulbul:v2 legacy speakers (dropped in v3):
    "anushka":  {"gender": "female", "models": ("bulbul:v2",)},
    "manisha":  {"gender": "female", "models": ("bulbul:v2",)},
    "vidya":    {"gender": "female", "models": ("bulbul:v2",)},
    "abhilash": {"gender": "male",   "models": ("bulbul:v2",)},
    "karun":    {"gender": "male",   "models": ("bulbul:v2",)},
}


# Sarvam's per-language recommended speakers (best-practices CER tables,
# docs.sarvam.ai, captured 2026-07-30): not all speakers pronounce every
# language equally — shubh is a Hindi-tier voice and is NOT recommended for
# English (ratan/ishita are). Keyed (short_lang, gender). Used by the Copilot
# when generating tts_voices for NEW agents; existing agents are never
# rewritten. ONE speaker per agent across its languages — switching speakers
# mid-call changes the person, which is worse than an accent shift.
RECOMMENDED_SPEAKERS: dict[tuple[str, str], str] = {
    ("en", "male"): "ratan", ("en", "female"): "ishita",
    ("hi", "male"): "shubh", ("hi", "female"): "priya",
    ("gu", "male"): "ratan", ("gu", "female"): "priya",
    ("mr", "male"): "ratan", ("mr", "female"): "priya",
    ("ta", "male"): "ratan", ("ta", "female"): "ishita",
    ("te", "male"): "shubh", ("te", "female"): "neha",
    ("kn", "male"): "shubh", ("kn", "female"): "neha",
    ("bn", "male"): "rehan", ("bn", "female"): "roopa",
    ("pa", "male"): "mani",  ("pa", "female"): "roopa",
    ("od", "male"): "shubh", ("od", "female"): "ritu",
    ("ml", "male"): "shubh", ("ml", "female"): "pooja",
}


def recommended_speaker(language: str, gender: str = "female") -> str:
    """Best default speaker for a language+gender per Sarvam's CER tables.
    Falls back to the top overall voices (priya / mani) for unknown codes."""
    g = (gender or "female").strip().lower()
    key = ((language or "en").strip().lower(), g)
    return RECOMMENDED_SPEAKERS.get(key, "priya" if g == "female" else "mani")


def speaker_gender(speaker: str) -> str:
    """'male' / 'female' / '' (unknown speaker)."""
    return SPEAKER_CATALOG.get((speaker or "").strip().lower(), {}).get("gender", "")


def speaker_models(speaker: str) -> tuple | None:
    """Models this speaker can ride, or None for unknown speakers (warn-only
    doctrine: Sarvam adds speakers faster than we ship — never reject those)."""
    info = SPEAKER_CATALOG.get((speaker or "").strip().lower())
    return info["models"] if info else None


# ─── ElevenLabs voice roster (workspace-added 2026-08-07) ────────────────
# voice_id → metadata. These are professional voices from the EL shared
# library, added to THIS workspace (library voices must be workspace-added
# before the API accepts them). All are Hindi-fine-tuned conversational
# voices; EL voices are multilingual, so the same voice speaks every
# EL_REALTIME_LANGUAGES entry (accent quality varies — hi/en verified).
EL_VOICE_CATALOG: dict[str, dict] = {
    "JvXnN4vCCagzhSTjMMjl": {
        "name": "Shivank", "gender": "male",
        "description": "Expressive human agent — natural hi-IN conversational",
    },
    "EVsUc9VDoheXQmgm2NpK": {
        "name": "Aryaman", "gender": "male",
        "description": "Energetic, quick support voice",
    },
    "d2osXrwa36lUEkuaO4sP": {
        "name": "Maahir", "gender": "male",
        "description": "Warm care voice, Gujarati accent",
    },
    "mnpQAOH9h8vn2tVUYJWD": {
        "name": "Roohi", "gender": "female",
        "description": "Real interactive sales caller — hi-IN",
    },
    "Em37RvLnPRGl6d9Suxcj": {
        "name": "Samhita", "gender": "female",
        "description": "Grounded, confident, warm",
    },
    "Wu4iEB9NxSm711OvcrLo": {
        "name": "Mohini", "gender": "female",
        "description": "Calm customer care, Gujarati accent",
    },
}

_EL_DEFAULT_MALE = "JvXnN4vCCagzhSTjMMjl"    # Shivank
_EL_DEFAULT_FEMALE = "mnpQAOH9h8vn2tVUYJWD"  # Roohi

# bulbul speaker → EL voice translation shim. Agent YAMLs are NEVER
# rewritten for the provider switch — when routing sends a bulbul-configured
# language through ElevenLabs, this maps the configured speaker to the
# closest EL voice (matched by gender + register, auditioned 2026-08-07).
# An unmapped speaker falls back by gender via SPEAKER_CATALOG.
_SPEAKER_TO_EL_VOICE = {
    "shubh":  "JvXnN4vCCagzhSTjMMjl",   # male hi-tier lead → Shivank
    "ratan":  "JvXnN4vCCagzhSTjMMjl",   # male en-recommended → Shivank
    "simran": "mnpQAOH9h8vn2tVUYJWD",   # female sales default → Roohi
    "priya":  "mnpQAOH9h8vn2tVUYJWD",   # female hi-tier → Roohi
    "ishita": "Em37RvLnPRGl6d9Suxcj",   # female en-recommended → Samhita
    "neha":   "Em37RvLnPRGl6d9Suxcj",
}


# EL display name (lowercase) → voice_id, so a YAML/copilot write may say
# "speaker: roohi" as naturally as a bulbul name and land on the right voice.
_EL_NAME_TO_VOICE = {
    meta["name"].lower(): vid for vid, meta in EL_VOICE_CATALOG.items()
}


def el_voice_for_speaker(speaker: str) -> str:
    """EL voice_id for a configured speaker.

    Accepts an EL voice_id (identity), an EL display name ("Roohi"), or a
    bulbul speaker name (shim-mapped). Unknown names fall back by catalog
    gender, then to the male default.
    """
    s = (speaker or "").strip()
    if s in EL_VOICE_CATALOG:            # already an EL voice_id
        return s
    low = s.lower()
    if low in _EL_NAME_TO_VOICE:         # EL display name
        return _EL_NAME_TO_VOICE[low]
    mapped = _SPEAKER_TO_EL_VOICE.get(low)
    if mapped:
        return mapped
    # Defensive fallback — a call must never dead-air over a voice name.
    # But LOUDLY: a silent default here is how "change the voice to X"
    # produced default-voice calls with zero errors anywhere (2026-08-07).
    fb = (_EL_DEFAULT_FEMALE if speaker_gender(s) == "female"
          else _EL_DEFAULT_MALE)
    import logging
    logging.getLogger(__name__).warning(
        "[voice-catalog] unknown speaker %r — falling back to default EL "
        "voice %s (%s). If a specific voice was chosen, it did NOT apply.",
        s, EL_VOICE_CATALOG.get(fb, {}).get("name", "?"), fb)
    return fb


def el_voice_gender(voice_id: str) -> str:
    """'male' / 'female' / '' for an EL voice_id."""
    return EL_VOICE_CATALOG.get((voice_id or "").strip(), {}).get("gender", "")
