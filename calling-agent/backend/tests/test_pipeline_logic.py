"""
Unit tests for the core deterministic pipeline logic.

Tests do NOT require API keys or network access.  They cover:
  - Language detection (script, romanized patterns, mixed)
  - Intent detection (every intent category)
  - STT post-processing (city corrections, BHK normalisation, phonetics)
  - Entity extraction (name, location, BHK, budget, timeline)
  - Transcript validation / filtering
  - VAD / segment-minimum constants

Run with:
    cd calling-agent
    source venv/bin/activate
    python -m pytest backend/tests/ -v
"""

from __future__ import annotations

import sys
import os

# Make sure the backend package is importable without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import asyncio

from app.voice.conversation import (
    ConversationRouter,
    detect_intent,
    STAGE_GREETING,
    STAGE_DISCOVERY,
    STAGE_INVITE_VISIT,
    STAGE_CONFIRMATION,
)
from app.voice.stt.router import validate_transcript
from app.voice.stt.post_processor import (
    post_process_transcript,
    match_location,
    match_bhk,
)
from app.voice.pipeline import MIN_SPEECH_MS, HARD_FLUSH_SILENCE_FRAMES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def detect_lang(text: str) -> str:
    """Run detect_language_v3 synchronously (no LLM fallback for these tests)."""
    router = ConversationRouter.__new__(ConversationRouter)
    router.state = type("S", (), {
        "language": "hi",
        "language_locked": False,
        "_lang_cache": {},
    })()
    router._lang_cache = {}

    async def _run():
        return await router.detect_language_v3(text)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run())
    finally:
        loop.close()
    return result[0]


# ---------------------------------------------------------------------------
# Pipeline constants
# ---------------------------------------------------------------------------

class TestPipelineConstants:
    def test_min_speech_ms_is_300(self):
        """MIN_SPEECH_MS must be 300ms to allow short single-word affirmations."""
        assert MIN_SPEECH_MS == 300.0, (
            f"MIN_SPEECH_MS={MIN_SPEECH_MS}. Raising above 300ms drops short "
            "affirmations (haan/ji/yes) and breaks flow control."
        )

    def test_hard_flush_silence_frames_sufficient(self):
        """HARD_FLUSH_SILENCE_FRAMES must allow Hindi/Gujarati intra-utterance pauses."""
        # 30 frames = 600ms is the minimum; 50 = 1000ms is better for South Asian speech
        assert HARD_FLUSH_SILENCE_FRAMES >= 30, (
            f"HARD_FLUSH_SILENCE_FRAMES={HARD_FLUSH_SILENCE_FRAMES} is too low; "
            "Hindi/Gujarati speakers pause 400–700ms between clause groups."
        )


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    # Script-based detection
    def test_gujarati_script(self):
        assert detect_lang("મને ૨ BHK જોઈએ છે") == "gu"

    def test_gujarati_script_2(self):
        assert detect_lang("સુરત માં flat જોવી છે") == "gu"

    def test_devanagari_hindi(self):
        assert detect_lang("मुझे 2 BHK चाहिए") == "hi"

    def test_devanagari_gujarati_markers(self):
        # STT often writes Gujarati in Devanagari via hi-IN model
        assert detect_lang("मने प्रॉपर्टी जोईए छे") == "gu"

    def test_gujarati_copula_ce(self):
        assert detect_lang("kem cho") == "gu"

    def test_gujarati_copula_devanagari(self):
        # "छे" is exclusively Gujarati
        assert detect_lang("saru छे") == "gu"

    # Romanized Hindi patterns
    def test_hindi_romanized(self):
        assert detect_lang("mujhe 2 bhk chahiye") == "hi"

    def test_hindi_romanized_2(self):
        assert detect_lang("haan theek hai batao") == "hi"

    # English
    def test_english_pure(self):
        result = detect_lang("I am looking for a 2 BHK flat in Surat")
        assert result == "en"

    def test_english_short_ascii(self):
        result = detect_lang("yes ok")
        # Short ambiguous ASCII — could be any language; just ensure it's supported
        assert result in ("en", "hi", "gu")

    # Mixed language
    def test_hinglish_word(self):
        # "investment" alone shouldn't flip to English
        result = detect_lang("investment ke liye chahiye")
        assert result == "hi"

    def test_gujarati_english_mix(self):
        result = detect_lang("mane 2 bhk flat joiye che")
        assert result == "gu"


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

class TestIntentDetection:
    def test_farewell_english(self):
        assert detect_intent("goodbye") == "farewell"

    def test_farewell_gujarati(self):
        assert detect_intent("આવજો") == "farewell"

    def test_farewell_hindi(self):
        assert detect_intent("alvida") == "farewell"

    def test_objection_english(self):
        assert detect_intent("not interested right now") == "objection"

    def test_objection_hindi(self):
        assert detect_intent("abhi nahi") == "objection"

    def test_objection_gujarati(self):
        assert detect_intent("રસ નથી") == "objection"

    def test_affirmation_english(self):
        assert detect_intent("yes") == "affirmation"

    def test_affirmation_hindi(self):
        assert detect_intent("haan ji") == "affirmation"

    def test_affirmation_gujarati(self):
        assert detect_intent("ha barobar") == "affirmation"

    def test_greeting_english(self):
        assert detect_intent("hello") == "greeting"

    def test_greeting_gujarati(self):
        assert detect_intent("kem cho") == "greeting"

    def test_identity_question_english(self):
        assert detect_intent("who is this?") == "identity_question"

    def test_identity_question_hindi(self):
        assert detect_intent("kaun bol raha hai") == "identity_question"

    def test_language_switch_english_to_hindi(self):
        assert detect_intent("speak in hindi please") == "language_switch"

    def test_language_switch_to_gujarati(self):
        assert detect_intent("gujarati ma bolo") == "language_switch"

    def test_language_switch_standalone(self):
        assert detect_intent("gujarati") == "language_switch"

    def test_language_switch_hindi_standalone(self):
        assert detect_intent("hindi") == "language_switch"

    def test_location_selection_surat(self):
        assert detect_intent("surat") == "location_selection"

    def test_location_selection_vapi(self):
        assert detect_intent("vapi mein") == "location_selection"

    def test_property_type_2bhk(self):
        assert detect_intent("2 bhk chahiye") == "property_type"

    def test_gratitude_english(self):
        assert detect_intent("thank you") == "gratitude"

    def test_gratitude_hindi(self):
        assert detect_intent("dhanyavaad") == "gratitude"

    def test_gratitude_gujarati(self):
        assert detect_intent("aabhar") == "gratitude"

    # Ensure greetings do NOT become names / locations
    def test_hello_is_not_location(self):
        intent = detect_intent("hello")
        assert intent == "greeting"
        assert intent != "location_selection"

    def test_thanks_is_not_property_type(self):
        intent = detect_intent("thanks")
        assert intent == "gratitude"
        assert intent != "property_type"


# ---------------------------------------------------------------------------
# STT transcript validation
# ---------------------------------------------------------------------------

class TestTranscriptValidation:
    def test_empty_rejected(self):
        assert validate_transcript("") == ""

    def test_too_short_rejected(self):
        assert validate_transcript("a") == ""

    def test_hallucination_rejected(self):
        assert validate_transcript("thank you for watching") == ""

    def test_youtube_rejected(self):
        assert validate_transcript("subscribe to our youtube channel") == ""

    def test_korean_rejected(self):
        assert validate_transcript("안녕하세요") == ""

    def test_malayalam_rejected(self):
        assert validate_transcript("നമസ്കാരം") == ""

    def test_marathi_copula_rejected(self):
        assert validate_transcript("मला हे आहे") == ""

    def test_devanagari_garbage_rejected(self):
        assert validate_transcript("अ") == ""

    def test_valid_hindi_accepted(self):
        result = validate_transcript("मुझे 2 BHK चाहिए")
        assert result != ""

    def test_valid_gujarati_accepted(self):
        result = validate_transcript("mane flat joiye che")
        assert result != ""

    def test_valid_english_accepted(self):
        result = validate_transcript("I am looking for a flat")
        assert result != ""

    def test_repetition_rejected(self):
        assert validate_transcript("ha ha ha ha ha ha") == ""

    def test_affirmation_passes(self):
        # Single valid affirmation words must NOT be blocked
        assert validate_transcript("haan") != ""
        assert validate_transcript("ji") != ""

    def test_single_garbage_word_rejected(self):
        assert validate_transcript("shoe") == ""
        assert validate_transcript("uh") == ""


# ---------------------------------------------------------------------------
# STT post-processing
# ---------------------------------------------------------------------------

class TestPostProcessing:
    def test_city_correction_soorat(self):
        result = post_process_transcript("i want flat in soorat")
        assert "surat" in result

    def test_city_correction_wapi(self):
        result = post_process_transcript("wapi mein flat chahiye")
        assert "vapi" in result

    def test_kem_cho_correction(self):
        result = post_process_transcript("game show sir")
        assert "kem cho" in result.lower()

    def test_bhk_normalisation(self):
        result = post_process_transcript("mujhe two bhk chahiye")
        assert "2 bhk" in result.lower()

    def test_pramukh_correction(self):
        result = post_process_transcript("pramuk group ka flat")
        assert "pramukh" in result.lower()

    def test_devanagari_vapi_normalised(self):
        result = post_process_transcript("वापी में flat chahiye")
        assert "vapi" in result.lower()


# ---------------------------------------------------------------------------
# Location matching
# ---------------------------------------------------------------------------

class TestLocationMatching:
    def test_surat_exact(self):
        assert match_location("i want flat in surat") == "surat"

    def test_vapi_exact(self):
        assert match_location("vapi mein dekhna hai") == "vapi"

    def test_silvassa_exact(self):
        assert match_location("silvassa property") == "silvassa"

    def test_surat_typo(self):
        assert match_location("suraat") == "surat"

    def test_vapi_typo(self):
        assert match_location("wapi me flat") == "vapi"

    def test_no_location(self):
        assert match_location("mujhe property chahiye") is None

    # Cities must not be misidentified as names
    def test_surat_not_name(self):
        # match_location should find surat; _extract_name_if_asked should reject it
        result = match_location("surat")
        assert result == "surat"


# ---------------------------------------------------------------------------
# BHK matching
# ---------------------------------------------------------------------------

class TestBHKMatching:
    def test_2bhk(self):
        assert match_bhk("2 bhk chahiye") == "2 bhk"

    def test_3bhk_word(self):
        assert match_bhk("teen bhk chahiye") == "3 bhk"

    def test_2_bedroom(self):
        assert match_bhk("2 bedroom flat") == "2 bhk"

    def test_no_bhk(self):
        assert match_bhk("i want a flat") is None

    def test_out_of_range_rejected(self):
        # 6 BHK is not in the 1-5 range
        assert match_bhk("6 bhk") is None


# ---------------------------------------------------------------------------
# Entity extraction: name blocklist (greetings must not become names)
# ---------------------------------------------------------------------------

class TestNameBlocklist:
    def _router(self) -> ConversationRouter:
        router = ConversationRouter.__new__(ConversationRouter)
        router.last_agent_response = "May I know your name please?"
        router._NAME_ASK_SIGNALS = ConversationRouter._NAME_ASK_SIGNALS
        router._NAME_BLOCKLIST = ConversationRouter._NAME_BLOCKLIST
        router._MY_NAME_PHRASES = ConversationRouter._MY_NAME_PHRASES
        return router

    def test_hello_rejected_as_name(self):
        router = self._router()
        assert router._extract_name_if_asked("hello") is None

    def test_surat_rejected_as_name(self):
        router = self._router()
        assert router._extract_name_if_asked("surat") is None

    def test_haan_rejected_as_name(self):
        router = self._router()
        assert router._extract_name_if_asked("haan") is None

    def test_valid_name_captured(self):
        router = self._router()
        result = router._extract_name_if_asked("Rahul")
        assert result == "Rahul"

    def test_two_word_name(self):
        router = self._router()
        result = router._extract_name_if_asked("Rahul Shah")
        assert result == "Rahul Shah"

    def test_my_name_phrase_not_captured(self):
        # "my name" itself is not the name
        router = self._router()
        assert router._extract_name_if_asked("my name") is None

    def test_question_not_captured(self):
        router = self._router()
        assert router._extract_name_if_asked("what is your name?") is None


# ---------------------------------------------------------------------------
# Budget extraction
# ---------------------------------------------------------------------------

class TestBudgetExtraction:
    def _router(self) -> ConversationRouter:
        import os
        os.environ.setdefault("OPENAI_API_KEY", "dummy")
        return ConversationRouter(api_key="dummy")

    def test_lakh_extraction(self):
        router = self._router()
        assert router._extract_budget("70 lakh budget hai") == "70 lakh"

    def test_crore_extraction(self):
        router = self._router()
        assert router._extract_budget("1.5 crore mein chahiye") == "1.5 crore"

    def test_range_extraction(self):
        router = self._router()
        result = router._extract_budget("50 to 70 lakh")
        assert result is not None and "lakh" in result

    def test_no_budget(self):
        router = self._router()
        assert router._extract_budget("mujhe flat chahiye") is None


# ---------------------------------------------------------------------------
# Timeline extraction
# ---------------------------------------------------------------------------

class TestTimelineExtraction:
    def _router(self) -> ConversationRouter:
        import os
        os.environ.setdefault("OPENAI_API_KEY", "dummy")
        return ConversationRouter(api_key="dummy")

    def test_immediate(self):
        router = self._router()
        assert router._extract_timeline("abhi chahiye") == "immediate"

    def test_months(self):
        router = self._router()
        assert router._extract_timeline("3 mahine mein") == "3 months"

    def test_years(self):
        router = self._router()
        result = router._extract_timeline("2 saal mein plan hai")
        assert result is not None and "year" in result

    def test_no_timeline(self):
        router = self._router()
        assert router._extract_timeline("flat chahiye") is None


# ---------------------------------------------------------------------------
# Language-switch intent coverage (multilingual)
# ---------------------------------------------------------------------------

class TestLanguageSwitchCoverage:
    """Ensure all language-switch phrasing styles are detected."""

    _cases = [
        # English
        "speak in hindi",
        "speak in gujarati",
        "can you speak english",
        "talk in hindi please",
        "switch to gujarati",
        "english only",
        "hindi please",
        # Romanized Hindi
        "hindi me baat karo",
        "hindi mein boliye",
        "gujarati ma bolo",
        # Devanagari
        "हिंदी में बोलो",
        "गुजराती में बात",
        # Gujarati script
        "ગુજરાતી બોલ",
        # Standalone
        "gujarati",
        "hindi",
        "english",
    ]

    @pytest.mark.parametrize("text", _cases)
    def test_language_switch_detected(self, text):
        intent = detect_intent(text)
        assert intent == "language_switch", (
            f"Expected language_switch for {text!r}, got {intent!r}"
        )


# ---------------------------------------------------------------------------
# Objection / deferral coverage
# ---------------------------------------------------------------------------

class TestObjectionCoverage:
    _cases = [
        "not interested",
        "busy right now",
        "call me later",
        "don't need this",
        "abhi nahi",
        "abhi busy",
        "baad mein baat",
        "phir baat karenge",
        "अभी नहीं",
        "રસ નથી",
    ]

    @pytest.mark.parametrize("text", _cases)
    def test_objection_detected(self, text):
        intent = detect_intent(text)
        assert intent == "objection", (
            f"Expected objection for {text!r}, got {intent!r}"
        )
