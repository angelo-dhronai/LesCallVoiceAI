"""
STT Router — tries primary provider (Sarvam), falls back to secondary
(OpenAI Whisper) only on genuine API errors, not on clean empty transcripts.

Key behaviours
--------------
* Sarvam rate-limits (HTTP 429): exponential backoff with per-strike counter.
  During the backoff window every incoming segment is forwarded to Whisper.
  The backoff resets automatically on the next successful Sarvam call.
* Empty transcript from Sarvam (no API error): treated as "no speech" and
  returned immediately — Whisper is NOT called.  Whisper is only invoked when
  Sarvam raises a network/HTTP exception.
* Unsupported scripts: any transcript containing characters from scripts the
  agent doesn't support (Korean, Tamil, Malayalam, Bengali, Arabic, …) is
  rejected before it reaches the conversation engine.
"""

from __future__ import annotations

import re
import time
import logging
from collections import Counter

from .base import STTProvider, STTResult  # noqa: F401 — re-exported for pipeline
from .sarvam import SarvamSTT, SarvamRateLimitError
from .openai_whisper import OpenAIWhisperSTT
from .post_processor import post_process_transcript

logger = logging.getLogger(__name__)

MIN_TRANSCRIPT_LENGTH = 2
MAX_RETRIES = 1
MIN_CONFIDENCE = 0.4

# Initial backoff 60 s, doubles each strike, caps at 5 min
_RL_INITIAL_BACKOFF_S = 60.0
_RL_MAX_BACKOFF_S = 300.0

_FILLER_WORDS = {"um", "uh", "like", "you know"}
_FILLER_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _FILLER_WORDS) + r")\b",
    re.IGNORECASE,
)

_HALLUCINATIONS = [
    "thank you for watching",
    "thanks for watching",
    "subscribe",
    "like and subscribe",
    "please subscribe",
    "thanks for listening",
    "youtube originals",
    "youtube",
]

_DEVANAGARI_HALLUCINATIONS = [
    "प्रस्तुत",
    "यूट्यूब",
    "ओरिजिनल्स",
    "सबस्क्राइब",
    "परमओत",
]

_REPETITION_THRESHOLD = 0.6

_REPEATED_CHAR_RE = re.compile(r"^(.{1,3}[- ]?)\1{2,}[-]?$")

_GARBAGE_SINGLE_WORDS = {
    "shoe", "care", "shoes", "show",
    # Confirmed noise-only transcriptions on near-silence — these words have no
    # conversational meaning as a standalone response.  "sure", "yeah", "oh",
    # "well" are NOT included because callers legitimately respond with a single
    # word affirmation/reaction.
    "the", "a", "an", "is", "it", "to", "of", "huh", "hmm", "uh", "um",
    "ah",
}

# Single Devanagari/Gujarati tokens that surface as STT garbage on noise/breath/clicks.
# INTENTIONALLY narrow: "हा" (yes), "ना"/"ना" (no), "ओके"/"ઓ કે" (OK) are valid
# single-word responses and are NOT included.  Only pure phoneme/vowel artifacts
# with zero conversational meaning are blocked here.
_GARBAGE_SCRIPT_TOKENS = {
    "अ", "आ", "ओ", "न", "ह",   # Devanagari single-consonant/vowel noise
    "અ", "આ", "ઓ",              # Gujarati single-vowel noise
}

# ---------------------------------------------------------------------------
# Transcript quality scoring — rejects function-word-only fragments
# ---------------------------------------------------------------------------
# Pure connective/particle words in Hindi and Gujarati that have zero
# semantic content when they appear ALONE.  Verbs, nouns, pronouns,
# question words, and copulas are deliberately excluded — e.g. "hai"
# (is), "che" / "cho" (are), "kem" (how), "ji" (yes/sir) all carry
# meaning and must NOT be flagged.  The sets are intentionally narrow so
# valid short utterances (greetings, short answers) are never rejected.
_HINDI_FUNC_QUALITY = frozenset({
    # Conjunctions
    "और", "या", "लेकिन", "परंतु", "मगर", "तथा", "व", "अथवा",
    "इसलिए", "क्योंकि",
    # Particles / connectors
    "जैसे", "कि", "भी", "तो", "ही", "बस", "जैसा", "सिर्फ",
})

_GUJARATI_FUNC_QUALITY = frozenset({
    # Conjunctions
    "અને", "અથવા", "પણ", "જ", "ત્યારે",
    "જ્યારે", "કારણ", "જો", "તેથી",
    # Relative pronoun / particles
    "જે", "તો",
})

_QUALITY_THRESHOLD = 0.3  # reject if quality score is below this


def _score_transcript_quality(text: str) -> float:
    """
    Score 0.0 (garbage) → 1.0 (good).

    Short phrases composed entirely or mostly of function words / connectors
    are almost certainly STT noise or auto-detect misfires.  Longer
    transcripts are penalised proportionally but rarely rejected outright.
    """
    # Strip trailing punctuation from each token before checking membership
    words = [w.strip("।?!.,।") for w in text.strip().split()]
    words = [w for w in words if w]
    if not words:
        return 0.0

    func_count = sum(
        1 for w in words
        if w in _HINDI_FUNC_QUALITY or w in _GUJARATI_FUNC_QUALITY
    )
    ratio = func_count / len(words)

    # Short phrases (≤3 words) are very suspicious when ≥60 % function words
    if len(words) <= 3 and ratio >= 0.6:
        return max(0.0, 0.3 - ratio)   # 0.6→-0.3 → 0.0,  1.0→-0.7 → 0.0

    # Medium phrases (4-5 words) need ≥80 % to trigger rejection
    if len(words) <= 5 and ratio >= 0.8:
        return 0.1

    # Partial penalty for high function-word density in longer phrases
    return max(0.3, 1.0 - ratio * 0.4)

# Marathi uses Devanagari (shared with Hindi) so the unsupported-script regex
# cannot catch it. Sarvam auto-detect sometimes transcribes Gujarati/Hindi speech
# as Marathi. These markers split into two tiers:
#   HIGH_CONFIDENCE — single occurrence is sufficient to reject (these words
#     literally do not exist in Hindi or Gujarati under any reading).
#   NORMAL — require 2+ hits to avoid false positives.
_MARATHI_HIGH_CONFIDENCE = [
    "आहे",   # Marathi copula "is/are" — Hindi equivalent is "है" (different word)
    "आहेत",  # "they are" — Hindi: हैं
    "आहात",  # "you are" formal — Hindi: हो/हैं
]
_MARATHI_MARKERS = [
    "मला",   # "to me" (Hindi: मुझे)
    "तुला",  # "to you" informal (Hindi: तुम्हें)
    "होय",   # "yes" (Hindi: हाँ)
    "कसे",   # "how" (Hindi: कैसे)
    "कसा",   # "how" masculine (Hindi: कैसा)
    "नाही",  # "no/not" Marathi form (Hindi: नहीं — different spelling)
    "आपण",   # "we/you" Marathi pronoun (Hindi: हम/आप)
]

# Unicode blocks the agent SUPPORTS: Latin (en), Devanagari (hi), Gujarati (gu).
# Everything else is a Sarvam auto-detect mis-fire on noisy/short audio.
# Supported languages: English, Hindi, Gujarati ONLY.
# Malayalam, Tamil, Telugu, Kannada, Bengali, Korean, Arabic, etc. are all
# rejected here — they are never valid caller input for this agent.
_UNSUPPORTED_SCRIPT_RE = re.compile(
    r"["
    r"ঀ-৿"           # Bengali / Assamese
    r"਀-੿"           # Gurmukhi (Punjabi)
    r"଀-୿"           # Oriya (Odia)
    r"஀-௿"           # Tamil
    r"ఀ-౿"           # Telugu
    r"ಀ-೿"           # Kannada
    r"ഀ-ൿ"           # Malayalam
    r"඀-෿"           # Sinhala
    r"؀-ۿ"           # Arabic
    r"ༀ-࿿"           # Tibetan
    r"က-႟"           # Myanmar
    r"ᄀ-ᇿ"  # Hangul Jamo (Korean)
    r"가-힣"  # Hangul Syllables (Korean)
    r"㄰-㆏"  # Hangul Compatibility Jamo (Korean)
    r"ꥠ-꥿"  # Hangul Jamo Extended-A (Korean)
    r"ힰ-퟿"  # Hangul Jamo Extended-B (Korean)
    r"぀-ヿ"  # Hiragana / Katakana (Japanese)
    r"一-鿿"  # CJK Unified Ideographs (Chinese/Japanese)
    r"]"
)


def validate_transcript(text: str) -> str:
    """
    Clean and validate an STT transcript. Returns empty string to reject.

    Steps: normalize -> strip fillers -> reject short -> reject hallucinations
    -> reject unsupported scripts -> reject excessive repetition.
    """
    if not text:
        return ""

    # Normalize
    cleaned = text.lower().strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Strip filler words
    cleaned = _FILLER_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Length check
    if len(cleaned) < 2:
        logger.info("[STT-FILTER] reason=too_short text=%r", text)
        return ""

    # Unsupported-script filter — Sarvam's auto-detect sometimes transcribes
    # noisy/short audio into Korean, Malayalam, Tamil, etc. which are never
    # valid user input. Reject immediately.
    if _UNSUPPORTED_SCRIPT_RE.search(text):
        logger.info("[STT-FILTER] reason=unsupported_script text=%r", text)
        return ""

    # Marathi rejection: Marathi shares Devanagari with Hindi so the script
    # regex cannot filter it. High-confidence copulas (आहे/आहेत/आहात) reject
    # on a single hit since they never appear in Hindi or Gujarati.
    # Other Marathi markers require 2+ hits to avoid false positives.
    if any(m in text for m in _MARATHI_HIGH_CONFIDENCE):
        logger.info("[STT-FILTER] reason=marathi_copula text=%r", text)
        return ""
    _marathi_hits = sum(1 for m in _MARATHI_MARKERS if m in text)
    if _marathi_hits >= 2:
        logger.info("[STT-FILTER] reason=marathi_transcript hits=%d text=%r", _marathi_hits, text)
        return ""

    # Hallucination filter
    for hallucination in _HALLUCINATIONS:
        if hallucination in cleaned:
            logger.info("[STT-FILTER] reason=hallucination text=%r match=%r", text, hallucination)
            return ""

    # Repeated character/syllable patterns like "s-s-s-s-s-" or "na na na"
    if _REPEATED_CHAR_RE.match(cleaned):
        logger.info("[STT-FILTER] reason=repeated_pattern text=%r", text)
        return ""

    # Devanagari hallucination filter
    for hallucination in _DEVANAGARI_HALLUCINATIONS:
        if hallucination in text:
            logger.info("[STT-FILTER] reason=devanagari_hallucination text=%r match=%r",
                        text, hallucination)
            return ""

    # Repetition check: if >60% of words are the same word
    words = cleaned.split()
    if len(words) >= 3:
        counts = Counter(words)
        most_common_count = counts.most_common(1)[0][1]
        if most_common_count / len(words) > _REPETITION_THRESHOLD:
            logger.info("[STT-FILTER] reason=repetition text=%r ratio=%.2f",
                        text, most_common_count / len(words))
            return ""

    # Single-word garbage: common misrecognitions from noise
    if len(words) == 1 and cleaned in _GARBAGE_SINGLE_WORDS:
        logger.info("[STT-FILTER] reason=garbage_single_word text=%r", text)
        return ""

    # Single Devanagari/Gujarati script garbage tokens (raw text, not lowercased —
    # the lower/strip pass mangles non-ASCII and we want exact match).
    raw_stripped = text.strip()
    if raw_stripped in _GARBAGE_SCRIPT_TOKENS:
        logger.info("[STT-FILTER] reason=garbage_script_token text=%r", text)
        return ""

    # Quality scoring: reject function-word-only short fragments.
    # Uses the ORIGINAL text so Devanagari/Gujarati characters are intact.
    # Applied after all other filters to minimise false-positive rejections
    # on valid short utterances (greetings, single-word answers).
    quality = _score_transcript_quality(text)
    if quality < _QUALITY_THRESHOLD:
        logger.info("[STT-FILTER] reason=low_quality score=%.2f text=%r", quality, text)
        return ""

    return cleaned


class STTRouter:
    """
    Multi-provider STT with intelligent fallback.

    Primary provider: Sarvam (always tried first).
    Fallback provider: OpenAI Whisper (only on Sarvam API errors).

    Fallback policy
    ---------------
    * Sarvam returns empty/short cleanly → return "" immediately, no Whisper.
      (This means the audio was silence or too noisy — Whisper would also fail.)
    * Sarvam raises a network/HTTP exception → try Whisper once.
    * Sarvam returns HTTP 429 → exponential backoff; route to Whisper during
      backoff window; reset on next successful Sarvam call.

    This prevents the empty-transcript retry loop that exhausted both API
    quotas on every silent/noisy segment.
    """

    def __init__(
        self,
        primary: STTProvider | None = None,
        fallback: STTProvider | None = None,
        min_length: int = MIN_TRANSCRIPT_LENGTH,
        max_retries: int = MAX_RETRIES,
        min_confidence: float = MIN_CONFIDENCE,
    ) -> None:
        self._primary = primary or SarvamSTT()
        self._fallback = fallback or OpenAIWhisperSTT()
        self._min_length = min_length
        self._max_retries = max_retries
        self._min_confidence = min_confidence

        # Rate-limit state (Sarvam HTTP 429 tracking)
        self._sarvam_rl_until: float = 0.0   # monotonic time when backoff expires
        self._sarvam_rl_count: int = 0        # consecutive rate-limit strikes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16_000,
        language: str = "en",
    ) -> STTResult:
        duration_ms = len(audio_bytes) / (sample_rate * 2) * 1000
        logger.info("[STT] Audio segment: %.0f ms, %d bytes", duration_ms, len(audio_bytes))

        now = time.monotonic()

        # ── Sarvam rate-limited: route directly to fallback ──────────────
        if now < self._sarvam_rl_until:
            remaining = self._sarvam_rl_until - now
            logger.warning(
                "[STT] Sarvam rate-limited (%.0fs remaining) — routing to fallback",
                remaining,
            )
            text, _, _ = await self._try_provider(
                self._fallback, "fallback", audio_bytes, sample_rate, language
            )
            return STTResult(text=text)

        # ── Try primary (Sarvam) ─────────────────────────────────────────
        try:
            text, had_error, detected_lang = await self._try_provider(
                self._primary, "primary", audio_bytes, sample_rate, language
            )
        except SarvamRateLimitError as exc:
            # Apply exponential backoff; reset on the next successful call
            self._sarvam_rl_count += 1
            backoff = min(
                _RL_INITIAL_BACKOFF_S * (2 ** (self._sarvam_rl_count - 1)),
                _RL_MAX_BACKOFF_S,
            )
            self._sarvam_rl_until = time.monotonic() + backoff
            logger.warning(
                "[STT] Sarvam rate-limited (strike %d) — backoff %.0fs, "
                "retry-after=%s — routing to fallback",
                self._sarvam_rl_count,
                backoff,
                exc.retry_after,
            )
            text, _, _ = await self._try_provider(
                self._fallback, "fallback", audio_bytes, sample_rate, language
            )
            return STTResult(text=text)

        if text:
            # Successful Sarvam call → reset rate-limit strike counter
            self._sarvam_rl_count = 0
            logger.info("[STT] Primary succeeded: %r", text)
            return STTResult(text=text, detected_language=detected_lang)

        if not had_error:
            # Sarvam processed the audio cleanly but found no speech.
            # Whisper would produce the same (empty or hallucinated) result,
            # so skip it entirely to avoid wasting quota.
            logger.info(
                "[STT] Primary returned no speech (clean empty) — skipping fallback"
            )
            return STTResult(text="")

        # ── Primary had a genuine API/network error → try fallback ───────
        logger.info("[STT] Primary errored — routing to fallback provider")
        text, _, _ = await self._try_provider(
            self._fallback, "fallback", audio_bytes, sample_rate, language
        )
        if text:
            logger.info("[STT] Fallback succeeded: %r", text)
        else:
            logger.warning("[STT] Both providers failed — returning empty transcript")
        return STTResult(text=text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post_process(self, text: str, language: str) -> str:
        if not text:
            return text
        return post_process_transcript(text, language)

    async def _try_provider(
        self,
        provider: STTProvider,
        label: str,
        audio_bytes: bytes,
        sample_rate: int,
        language: str,
    ) -> tuple[str, bool, str | None]:
        """
        Attempt transcription with up to (max_retries + 1) attempts.

        Returns
        -------
        (transcript, had_error, detected_language)
            transcript : str
                Clean, post-processed text; empty string if nothing usable.
            had_error : bool
                True if at least one attempt raised an exception (network /
                HTTP error). False means the provider responded cleanly but
                found no speech — the caller should NOT fall back in this case.
            detected_language : str | None
                BCP-47 language code returned by the provider (e.g. "gu-IN"),
                or None if the provider did not report one.
        """
        had_error = False

        for attempt in range(1, self._max_retries + 2):
            try:
                result: STTResult = await provider.transcribe(
                    audio_bytes, sample_rate, language
                )
                text = result.text
                confidence = result.confidence
                detected_lang = result.detected_language

                if confidence < self._min_confidence:
                    logger.info(
                        "[STT-FILTER] reason=low_confidence score=%.2f text=%r "
                        "provider=%s attempt=%d",
                        confidence, text, label, attempt,
                    )
                    return "", False, None

                if len(text) < self._min_length:
                    logger.info(
                        "[STT] %s short/empty transcript %r (attempt %d) — no speech",
                        label, text, attempt,
                    )
                    return "", False, None

                validated = validate_transcript(text)
                if validated:
                    processed = self._post_process(validated, language)
                    logger.info(
                        "[STT] %s attempt %d: %r -> validated %r -> processed %r",
                        label, attempt, text, validated, processed,
                    )
                    return processed, False, detected_lang

                logger.info(
                    "[STT] %s transcript %r rejected by validation (attempt %d)",
                    label, text, attempt,
                )
                return "", False, None

            except SarvamRateLimitError:
                raise

            except Exception as exc:
                logger.warning(
                    "[STT] %s attempt %d/%d exception: %s",
                    label, attempt, self._max_retries + 1, exc,
                )
                had_error = True

            if attempt <= self._max_retries:
                logger.info(
                    "[STT] Retrying %s (attempt %d/%d)",
                    label, attempt + 1, self._max_retries + 1,
                )

        return "", had_error, None
