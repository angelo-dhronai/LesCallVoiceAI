"""
Pramukh Group — proactive outbound calling agent.

Drives conversation through a structured appointment booking flow:
  GREETING -> INTEREST_CONFIRM -> (INTEREST_RECONFIRM) -> LOCATION
  -> PROPERTY_TYPE -> SCHEDULING -> CONFIRMATION -> CLOSING

Agent goes first with bilingual greeting ("Hello, Namaste!").
User's language is detected from their response and mirrored.
Interest is confirmed before advancing to the booking flow.
Uses parallel LLM execution, filler responses, and deterministic
shortcuts for low latency.
"""

from __future__ import annotations

import json
import asyncio
import os
import re
import time
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from .memory import ConversationMemory, BookingData, CallState
from .stt.post_processor import match_location, match_bhk
from ..agent_definition import AgentDefinition, get_pramukh_agent

# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

STAGE_GREETING = "GREETING"
STAGE_DISCOVERY = "DISCOVERY"
STAGE_PROJECT_MATCH = "PROJECT_MATCH"
STAGE_INVITE_VISIT = "INVITE_VISIT"
STAGE_CONFIRMATION = "CONFIRMATION"
STAGE_CLOSING = "CLOSING"

_STAGE_ORDER = {
    STAGE_GREETING: 0,
    STAGE_DISCOVERY: 1,
    STAGE_PROJECT_MATCH: 2,
    STAGE_INVITE_VISIT: 3,
    STAGE_CONFIRMATION: 4,
    STAGE_CLOSING: 5,
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RESPONSE_LENGTH = 400
LLM_TIMEOUT = 5.0
LLM_MAX_TOKENS = 160
# Lowered 2.0 → 1.0s: the LLM's first sentence typically lands at ~1.8s, so a
# 2.0s gate almost never fired and the caller heard dead air. At 1.0s a short
# acknowledgment ("One moment…") plays ~1s after the caller stops, masking the
# LLM round-trip and cutting *perceived* turn latency without touching the
# turn-end wait or the model itself.
FILLER_TIMEOUT_S = 1.0
LLM_STREAM_TIMEOUT_S = 7.0
INTENT_CLASSIFY_TIMEOUT_S = 1.0

logger = logging.getLogger(__name__)

_MARKDOWN_RE = re.compile(r"[*#`~_\[\]()>|]")
_EMOJI_RE = re.compile(
    "[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff\U00002600-\U000026ff]+",
    re.UNICODE,
)

# Gujarati copula markers in Devanagari (STT sometimes writes Gujarati in
# Devanagari when language_code=hi-IN). These copulas are exclusively Gujarati
# and NEVER appear as word-stems in Hindi. One hit is sufficient for detection.
# Word-boundary anchors prevent false matches inside Hindi words like "छोटा".
_STRONG_GU_DEVA_RE = re.compile(
    r"(?<!\w)(?:छे|छो|छु|छुं|छीए|केम|तमे|नथी|हसे|जोइए)(?!\w)"
)


@dataclass
class EngineResult:
    text: str
    is_shortcut: bool
    intent: str


class ConversationState:
    def __init__(self) -> None:
        self.last_intent: str | None = None
        self.turn_count: int = 0
        self.is_closing: bool = False
        self.language: str = "hi"
        self.language_history: list[str] = []
        self.stage: str = STAGE_GREETING
        self.lang_confidence: float = 1.0
        self.lang_source: str = "default"
        self.filler_used: bool = False
        self.llm_cancelled: bool = False
        self.parallel_execution: bool = False
        self.objection_handled: bool = False
        self.language_locked: bool = False
        self.language_lock_turns: int = 0
        self.decline_count: int = 0
        # Discovery tracking (inbound flow)
        self.discovery_turns: int = 0
        self.purpose: str | None = None          # "self_use" / "investment" / "exploring"
        self.project_recommended: str | None = None  # project name mentioned to caller




# Universal objection/deferral phrases — fallback when an agent defines no
# objection_patterns of its own (English + romanized Hindi core set). Without
# this, agents with an empty YAML list silently lose objection handling.
_DEFAULT_OBJECTION_PATTERNS = (
    "not interested", "busy right now", "call later", "call me later",
    "call back later", "don't need", "dont need", "no time", "don't want",
    "stop calling", "not looking", "no need", "wrong number",
    "remove my number", "talk later", "later please", "maybe later",
    "some other time", "another time", "not right now",
    "baad mein baat", "baad me baat", "phir baat", "phir baat karenge",
    "abhi nahi", "abhi nai", "abhi busy", "fursat nahi",
)


def detect_intent(
    text: str,
    stage: str = STAGE_GREETING,
    *,
    location_aliases: frozenset[str] = frozenset(),
    objection_patterns: list[str] | tuple[()] = (),
    time_patterns: list[str] | tuple[()] = (),
) -> str:
    """Classify user input into a domain intent. Fast and deterministic."""
    if not text or not text.strip():
        return "general"

    lower = text.lower().strip()

    # Objection — highest priority
    if any(p in lower for p in (objection_patterns or _DEFAULT_OBJECTION_PATTERNS)):
        return "objection"
    if any(w in lower for w in ["दिलचस्पी नहीं", "बिज़ी", "ज़रूरत नहीं",
                                  "રસ નથી", "વ્યસ્ત", "જરૂર નથી"]):
        return "objection"
    # Deferral patterns — clear "later / not now" in Devanagari and Gujarati script.
    # Keep this narrow to avoid false positives.
    if any(w in text for w in [
        "अभी नहीं", "अभी नही", "अभी बिज़ी", "अभी busy",
        "फिर बात करेंगे",
        "અત્યારે નહીં", "અત્યારે નથી",
    ]):
        return "objection"

    # Language switch request — highest priority after objection.
    # Catches romanized, Devanagari, and Gujarati script requests.
    _lang_switch_phrases = [
        "hindi me", "hindi mai", "hindi mein", "hindi boliye", "hindi bolo",
        "hindi me baat", "hindi mai baat", "hindi mein baat",
        # Gujarati postposition "ma" (in) — "hindi ma", "english ma"
        "hindi ma", "hindi maa", "hindi ma vaat", "hindi ma baat",
        "hindi ma bol", "hindi ma karo",
        "english ma", "english maa", "english ma vaat", "english ma baat",
        "english me", "english mai", "english mein", "english boliye", "english bolo",
        "english me baat", "english mai baat", "english mein baat",
        "gujarati ma", "gujarati maa", "gujarati mai", "gujarati mein", "gujarati boliye", "gujarati bolo",
        "gujarati ma vaat", "gujarati mai baat", "gujarati ma baat",
        "gu ma bolo", "gu mein bolo", "gu ma baat", "gu maa bolo",
        "bolo gujarati", "bol gujarati", "bolav gujarati",
        "speak hindi", "speak english", "speak gujarati",
        "talk in hindi", "talk in english", "talk in gujarati",
        "speak in hindi", "speak in english", "speak in gujarati",
        "switch to hindi", "switch to english", "switch to gujarati",
        "in english", "in hindi", "in gujarati",
        # Common short asks — "english please", "english only", etc.
        "english please", "hindi please", "gujarati please",
        "only english", "only hindi", "only gujarati",
        "english only", "hindi only", "gujarati only",
        "can you speak english", "can you speak hindi", "can you speak gujarati",
        "can you talk english", "can you talk hindi", "can you talk gujarati",
        "could you speak english", "could you speak hindi", "could you speak gujarati",
        "talk english", "talk hindi", "talk gujarati",
        # Romanized question forms — "kya aap hindi mein baat kar sakte hain"
        "kya aap hindi", "kya aap english", "kya aap gujarati",
        "kya hindi", "kya english", "kya gujarati",
        # "vaat" = Gujarati for "talk/speech" — covers "hindi ma vaat karvi che"
        "hindi vaat", "english vaat", "gujarati vaat",
        "hindi ma vaat", "english ma vaat",
    ]
    if any(p in lower for p in _lang_switch_phrases):
        return "language_switch"
    # Standalone language name as the whole message — "gujarati", "hindi", "english"
    if re.match(r"^\s*(gujarati|hindi|english)\s*[?.!]?\s*$", lower):
        return "language_switch"
    # Devanagari script — all spellings of language names + में/बोलो/बात
    _lang_switch_devanagari = [
        "हिंदी में", "हिन्दी में", "हिंदी बोल", "हिन्दी बोल",
        "हिंदी में बात", "हिन्दी में बात",
        "अंग्रेज़ी में", "अंग्रेजी में", "इंग्लिश में",
        "अंग्रेज़ी बोल", "अंग्रेजी बोल", "इंग्लिश बोल",
        "अंग्रेज़ी में बात", "अंग्रेजी में बात", "इंग्लिश में बात",
        "गुजराती में", "गुजराती बोल", "गुजराती में बात",
        "गुजराती में बोलो", "हिंदी में बोलो",
        # Gujarati-syntax "ma" postposition in Devanagari — hi-IN STT
        # renders "Gujarati ma vaat karo" as "गुजराती मा वात करो".
        "गुजराती मा", "गुजराती माँ",
        "हिंदी मा", "हिंदी माँ",
        "इंग्लिश मा", "इंग्लिश माँ",
    ]
    if any(p in text for p in _lang_switch_devanagari):
        return "language_switch"
    # Broader Devanagari catch-all: language name + any postposition or verb.
    # Catches STT variants that spell the postposition differently.
    if re.search(
        r"(?:\u0917\u0941\u091C\u0930\u093E\u0924\u0940|\u0939\u093F\u0902\u0926\u0940|\u0939\u093F\u0928\u094D\u0926\u0940|\u0905\u0902\u0917\u094D\u0930\u0947\u091C\u0940|\u0906\u0928\u094D\u0917\u094D\u0930\u0947\u091C\u0940|\u0907\u0902\u0917\u094D\u0932\u093F\u0936)"
        r"[^\n]{0,12}"
        r"(?:\u092E\u093E|\u092E\u093E\u0901|\u092E\u0947\u0902|\u092E\u0947|\u092E\u0947\u0901|\u0935\u093E\u0924|\u092C\u093E\u0924|\u092C\u094B\u0932|\u0915\u0930\u094B|kariye|karo)",
        text,
    ):
        return "language_switch"
    # Gujarati script — comprehensive list including common colloquial forms
    if any(w in text for w in [
        "ગુજરાતીમાં", "ગુજરાતી માં", "અંગ્રેજીમાં", "હિન્દીમાં",
        "ઇંગ્લિશમાં", "ગુજરાતી બોલ", "ગુજ બોલ",
        "અંગ્રેજી બોલ", "હિન્દી બોલ", "ઇંગ્લિશ બોલ",
        "ગુજ માં", "ગુ માં",
        "ગુજ મા", "ગુ મા", "ગુજરાતી મા",
    ]):
        return "language_switch"
    # Catch-all: any mention of a language name with "baat/bol/bolo" nearby
    if re.search(r"(?:english|hindi|gujarati|इंग्लिश|अंग्रेज़ी|अंग्रेजी|हिंदी|हिन्दी|गुजराती|ગુજરાતી|અંગ્રેજી|હિન્દી|ઇંગ્લિશ).{0,15}(?:बात|बोल|bol|baat|bolo|boliye|vaat|vat|વાત|બોલ)", lower):
        return "language_switch"
    if re.search(r"(?:बात|बोल|bol|baat|bolo|boliye|vaat|vat|વાત|બોલ).{0,15}(?:english|hindi|gujarati|इंग्लिश|अंग्रेज़ी|अंग्रेजी|हिंदी|हिन्दी|गुजराती|ગુજરાતી|અંગ્રેજી|હિન્દી|ઇંગ્લિશ)", lower):
        return "language_switch"

    # Two language names co-mentioned within a 30-char window — strong signal
    # the user is talking about switching ("english instead of hindi", "switch
    # from hindi to english", "hindi or english", etc.).
    if re.search(
        r"(english|hindi|gujarati).{1,30}(english|hindi|gujarati)",
        lower,
    ):
        return "language_switch"

    # Negation / restriction patterns — user implicitly asks to switch by
    # saying "I don't know X" / "X नहीं आती" / "સિર્ફ Y" / "only Y". These
    # must be classified as language_switch so the deterministic handler
    # responds (instead of the LLM, which sometimes hallucinates refusals
    # like "मैं हिंदी में ही बात करूँगा"). The destination language is
    # resolved by _extract_requested_language's last-position fallback.
    _lang_neg_lat = r"(?:english|hindi|gujarati|inglish|angrezi)"
    _lang_neg_dev = r"(?:इंग्लिश|अंग्रेज़ी|अंग्रेजी|हिंदी|हिन्दी|गुजराती)"
    _lang_neg_guj = r"(?:ગુજરાતી|અંગ્રેજી|હિન્દી|ઇંગ્લિશ)"
    _lang_neg_any = f"(?:{_lang_neg_lat[3:-1]}|{_lang_neg_dev[3:-1]}|{_lang_neg_guj[3:-1]})"
    # English: "don't know/speak X", "can't speak X", "only know/speak X"
    if re.search(
        rf"(?:don'?t|do not|can'?t|cannot|only|just)\s+(?:know|speak|talk|understand)\s+{_lang_neg_lat}",
        lower,
    ):
        return "language_switch"
    # Negation word near a language name (either order, ~25 char window).
    # Covers Devanagari नहीं, Gujarati નથી, romanized nahi/nahin.
    _neg_words = r"(?:नहीं|नहि|नही|न्ही|નથી|નહિ|નહી|નહીં|nahi|nahin|nai)"
    if re.search(rf"{_lang_neg_any}[^\n]{{0,25}}{_neg_words}", text, re.IGNORECASE):
        return "language_switch"
    if re.search(rf"{_neg_words}[^\n]{{0,25}}{_lang_neg_any}", text, re.IGNORECASE):
        return "language_switch"
    # Restriction word ("only" in various scripts) near a language name.
    _only_words = r"(?:सिर्फ|केवल|खाली|बस|ફક્ત|માત્ર|સિર્ફ|બસ|ખાલી|sirf|sirff|fakt|fakta|kewal|khali)"
    if re.search(rf"{_only_words}[^\n]{{0,20}}{_lang_neg_any}", text, re.IGNORECASE):
        return "language_switch"
    if re.search(rf"{_lang_neg_any}[^\n]{{0,20}}{_only_words}", text, re.IGNORECASE):
        return "language_switch"

    # Location selection
    loc = match_location(lower)
    if loc:
        return "location_selection"
    if location_aliases and any(w in lower for w in location_aliases):
        return "location_selection"

    # BHK / property type
    bhk = match_bhk(lower)
    if bhk:
        return "property_type"
    if re.search(r"\d\s*bhk|\d\s*bed", lower, re.IGNORECASE):
        return "property_type"

    # Time preference
    if any(p in lower for p in time_patterns):
        return "time_preference"

    # Affirmation
    if re.search(r"\b(yes|yeah|yep|sure|okay|ok|haan|ji|theek|ha|barobar)\b", lower):
        return "affirmation"
    if any(w in lower for w in ["हां", "हाँ", "जी", "ठीक", "હા", "બરાબર"]):
        return "affirmation"

    # Identity question — "who is this", "kon bol raha hai", etc.
    # Highest priority among questions — gets a fast deterministic response
    _identity_phrases = [
        "who is this", "who are you", "who is calling", "who is speaking",
        "who am i speaking", "who am i talking",
        "which company", "what company", "where are you calling from",
        "where you calling from", "kahan se", "kis taraf se",
        "kon hai", "kaun hai", "kon bol", "kaun bol", "kon baat", "kaun baat",
        "aap kon", "aap kaun", "ap kon", "ap kaun", "tum kon", "tum kaun",
        "ye kon", "ye kaun", "yeh kon", "yeh kaun",
        "tame kon", "tame kaun", "aa kon", "aa kaun",
        "kon che", "kaun che", "kon bole che", "kon baat kar",
        "kidhar se", "kahan se bol",
    ]
    # Also check Devanagari/Gujarati script identity questions
    if any(w in lower for w in [
        "कौन है", "कौन बोल", "कौन बात", "आप कौन", "ये कौन", "कहां से",
        "કોણ છે", "કોણ બોલ", "કોણ વાત", "તમે કોણ", "ક્યાંથી",
    ]):
        return "identity_question"
    if any(p in lower for p in _identity_phrases):
        return "identity_question"

    # Question — check before negation so कितना isn't caught by ना substring
    if lower.endswith("?") or re.search(r"\b(what|how|when|where|why|which|who|kitna|kab|kahan|kaun|kya|kyun|kon)\b", lower):
        return "question"
    if any(w in lower for w in ["कौन", "क्या", "कैसे", "कब", "कहां", "कितना", "क्यों",
                                  "કોણ", "શું", "કેવી", "ક્યારે", "ક્યાં", "કેટલા", "કેમ"]):
        return "question"
    # Common caller questions without explicit question words
    _caller_questions = [
        "is this", "are you", "tell me about",
        "can you tell", "i want to know", "details", "more information",
    ]
    if any(p in lower for p in _caller_questions):
        return "question"

    # Negation
    if re.search(r"\b(no|nope|nah|nahi|na)\b", lower):
        return "negation"
    if any(w in lower for w in ["नहीं", "ના", "નહીં"]):
        return "negation"
    if re.search(r"(?:^|\s)ना(?:\s|$)", lower):
        return "negation"

    # Farewell
    if re.search(r"\b(bye|goodbye|see you|alvida|aavjo)\b", lower):
        return "farewell"
    if any(w in lower for w in ["अलविदा", "फिर मिलते", "આવજો", "ફરી મળીશું"]):
        return "farewell"

    # Greeting — includes Gujarati/Hindi common greetings
    if re.search(r"\b(hello|helo|hellow|hi|hey|namaste|namaskar|kem cho|kemcho|hallo|halo|bol|bolo|suno|suniye|boliye)\b", lower):
        return "greeting"
    if any(w in lower for w in ["नमस्ते", "નમસ્તે", "હેલો", "કેમ છો", "बोलो", "बोल", "सुनिए", "हेलो"]):
        return "greeting"

    # Gratitude
    if re.search(r"\b(thank|thanks|dhanyavaad|shukriya|aabhar)\b", lower):
        return "gratitude"
    if any(w in lower for w in ["धन्यवाद", "शुक्रिया", "આભાર", "थैंक", "થેંક"]):
        return "gratitude"

    return "general"


def detect_language(text: str) -> str:
    if not text or not text.strip():
        return "en"
    if any("઀" <= c <= "૿" for c in text):
        return "gu"
    if any("ऀ" <= c <= "ॿ" for c in text):
        return "hi"
    return "en"


# ---------------------------------------------------------------------------
# Transliteration patterns for language detection v2
# ---------------------------------------------------------------------------

_HINDI_PATTERNS = [
    "hai", "tha", "thi", "hoga", "kya", "kyun", "kaise",
    "karna", "chahiye", "hona", "mujhe", "tum", "aap",
    "nahi", "accha", "theek", "namaste", "dhanyavaad",
    "shukriya", "haan", "dekhna", "dikhao", "samay",
    "subah", "dopahar", "shaam", "kal", "parso", "aaj",
    "baat", "raha", "rahi", "batao", "bolo", "boliye",
    "kaun", "kon", "kahan", "kidhar", "suniye", "bhai",
]

_GUJARATI_PATTERNS = [
    "che", "chu", "chhe", "cho", "chho", "chie", "chiye",
    "hatu", "hase", "hati", "hoy",
    "shu", "kem", "kemcho", "kem cho",
    "tame", "hu", "pan", "tamne", "mane", "amne", "amara", "tamara",
    "majama", "barobar", "saru", "sarun",
    "savare", "bapore", "saanje", "aaje", "kaale",
    "joie", "joiye", "jova", "joi", "jovun",
    "aavjo", "aavo", "jao",
    "saheb", "mari", "tari",
    "batavo", "janavo", "bolo",
    "karo", "karso", "kariye",
    "atyare", "pachhi", "pela",
    "nathi", "nahi", "haji",
]

_LANG_CLASSIFY_PROMPT = (
    "Classify the dominant language of this text.\n"
    "Options: English, Hindi, Gujarati\n"
    "The input may be transliterated (romanized) or mixed-language.\n"
    'Text: "{text}"\n'
    "Return ONLY one word: English, Hindi, or Gujarati."
)

_LANG_NAME_TO_CODE = {"english": "en", "hindi": "hi", "gujarati": "gu"}


def _pattern_score(text: str, patterns: list[str]) -> int:
    words = set(text.split())
    return sum(1 for p in patterns if p in words)


_SLOT_HOUR_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)", re.IGNORECASE)


def _slot_hour_24(time_str: str) -> int:
    """Parse a slot string like '10:00 AM' into 24-hour integer (10, 14, etc)."""
    m = _SLOT_HOUR_RE.match(time_str)
    if not m:
        return -1
    h = int(m.group(1))
    period = m.group(3).upper()
    if period == "PM" and h < 12:
        h += 12
    elif period == "AM" and h == 12:
        h = 0
    return h


def _slot_period(time_str: str) -> str:
    """Classify a slot time as morning / afternoon / evening."""
    h = _slot_hour_24(time_str)
    if 0 <= h < 12:
        return "morning"
    if h < 17:
        return "afternoon"
    return "evening"


# Localized time-of-day words for the {current_time} greeting placeholder.
_TIME_OF_DAY_WORDS = {
    "en": {"morning": "morning", "afternoon": "afternoon", "evening": "evening"},
    "hi": {"morning": "सुबह", "afternoon": "दोपहर", "evening": "शाम"},
    "gu": {"morning": "સવાર", "afternoon": "બપોર", "evening": "સાંજ"},
}


def _time_of_day_word(greeting: str = "") -> str:
    """Morning/afternoon/evening right now in IST (callers are in India).

    If the surrounding greeting text is written in Devanagari or Gujarati
    script, return the word in that script so TTS doesn't code-switch
    mid-greeting.
    """
    from datetime import datetime, timedelta, timezone
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
    except Exception:
        now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    period = "morning" if now.hour < 12 else ("afternoon" if now.hour < 17 else "evening")
    lang = "en"
    if re.search(r"[ऀ-ॿ]", greeting):
        lang = "hi"
    elif re.search(r"[઀-૿]", greeting):
        lang = "gu"
    return _TIME_OF_DAY_WORDS[lang][period]


# ---------------------------------------------------------------------------
_RESPONSE_FALLBACK = "Sorry, could you repeat that?"

# ---------------------------------------------------------------------------
# Repeat-request detection patterns (multilingual)
# ---------------------------------------------------------------------------

_REPEAT_DETECT_PATTERNS = [
    "repeat", "say again", "say that again", "once more", "pardon",
    "dobara bolo", "dobara", "phir se bolo", "phir se", "phir kaho",
    "fari bol", "fari bolsho", "fari janavo",
    "फिर बोलो", "दोबारा", "दोबारा बोलो", "एक बार फिर",
    "ફરી બોલ", "ફરી",
]



# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------


def _validate_response(text: str, expected_lang: str = "en") -> str:
    if not text or not text.strip():
        return _RESPONSE_FALLBACK
    cleaned = _MARKDOWN_RE.sub("", text)
    cleaned = _EMOJI_RE.sub("", cleaned).strip()
    if len(cleaned) > MAX_RESPONSE_LENGTH:
        truncated = cleaned[:MAX_RESPONSE_LENGTH]
        last_sentence = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
        if last_sentence > MAX_RESPONSE_LENGTH // 2:
            cleaned = truncated[: last_sentence + 1]
        else:
            cleaned = truncated.rstrip() + "..."
    if not cleaned:
        return _RESPONSE_FALLBACK
    return cleaned


# ===================================================================
# ConversationRouter — Pramukh Group Proactive Agent
# ===================================================================


class ConversationRouter:
    """
    Proactive outbound calling agent for Pramukh Group.

    Drives the conversation through:
      GREETING -> INTEREST_CONFIRM -> (INTEREST_RECONFIRM) -> LOCATION
      -> PROPERTY_TYPE -> SCHEDULING -> CONFIRMATION -> CLOSING.

    Agent speaks first with a bilingual greeting. User's language is
    detected from their first response and mirrored throughout.
    Extracts structured booking data at each stage. Falls back to LLM
    for natural responses and question handling.
    """

    # Class-level warmup flag: LLM API connection warms at most once per
    # process regardless of how many calls arrive (Issue 6).
    _warmup_done_flag: bool = False

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        default_language: str = "hi",
        agent_def: AgentDefinition | None = None,
    ) -> None:
        # Agent definition drives all domain-specific behaviour.
        # Explicit constructor args override the definition for backward compat.
        self._def: AgentDefinition = agent_def or get_pramukh_agent()
        # LLM provider is swappable via env WITHOUT code changes — set all three
        # to move off US-hosted OpenAI onto a fast in-region endpoint (e.g. Gemini
        # OpenAI-compat) and cut ~1s of trans-Pacific latency per turn:
        #   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
        #   LLM_API_KEY=<key>   LLM_MODEL=gemini-2.0-flash-lite
        # Defaults preserve the previous behaviour (OpenAI, gpt-4o-mini).
        self._api_key = (
            api_key
            or os.environ.get("LLM_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "")
        )
        self._base_url = os.environ.get("LLM_BASE_URL", "").strip() or None
        self._model = model or os.environ.get("LLM_MODEL", "").strip() or self._def.llm_config.model
        self._timeout = timeout if timeout is not None else self._def.llm_config.timeout_s
        self._client: AsyncOpenAI | None = None
        _qa_signals = self._def.knowledge_base.qa_field_signals or None
        self.memory = ConversationMemory(field_signals=_qa_signals)
        self.state = ConversationState()
        self.state.language = default_language
        self._response_counter = 0
        self._lang_cache: dict[str, tuple[str, float, str]] = {}
        self._filler_counter = 0
        self._speculative_yes: asyncio.Task | None = None
        self._speculative_no: asyncio.Task | None = None
        self._last_nudge_stage: str | None = None
        # Last-response memory (feature 3)
        self.last_agent_response: str = ""
        self.last_agent_language: str = ""
        # Name-capture confirmation mode (feature 4)
        self._name_pending: str | None = None
        self._name_confirmed: bool = False
        self._name_pending_turn: int = 0   # turn index when _name_pending was set
        self._rag_context: str = ""        # populated from documents before each LLM call

        # Call context — direction defaults to the agent's configured type;
        # the telephony bridge overrides per call via set_call_context()
        # (e.g. an outbound dial carries the dialed contact's name/number).
        self.call_direction: str = (getattr(self._def, "call_direction", "") or "inbound").lower()
        self.outbound_contact_name: str = ""
        self.outbound_contact_phone: str = ""
        self.conn_id: str = ""

        # Conversation State Management layer — per-call structured memory:
        # extraction, merge, audit trail, missing-slot detection, and the
        # single authoritative state block injected into every system prompt.
        from .state_manager import build_state_manager
        self.state_mgr = build_state_manager(self._def, language=default_language)
        # Fallback LLM (lazy; see _get_fallback_client).
        self._fallback_client: AsyncOpenAI | None = None
        self._fallback_model: str = "gpt-4o-mini"
        # Async LLM slot-extractor bookkeeping (strong ref; latest turn only).
        self._slot_extract_task: asyncio.Task | None = None
        self._slot_extract_enabled = (
            os.environ.get("ENABLE_LLM_SLOT_EXTRACTOR", "true").lower() != "false"
        )

        if not self._api_key:
            logger.warning("[CONV] OPENAI_API_KEY not set -- LLM calls will fail")

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            _kwargs = {"api_key": self._api_key, "timeout": self._timeout}
            if self._base_url:
                _kwargs["base_url"] = self._base_url
            self._client = AsyncOpenAI(**_kwargs)
        return self._client

    def _get_fallback_client(self) -> AsyncOpenAI | None:
        """Secondary LLM used when the primary fails (quota/429/auth/network).

        Config: LLM_FALLBACK_BASE_URL / LLM_FALLBACK_MODEL / LLM_FALLBACK_API_KEY.
        Defaults to OpenAI gpt-4o-mini on OPENAI_API_KEY — always funded here,
        slower (US) but a slow answer beats a canned 'please repeat'. Returns
        None when no usable fallback exists or it would duplicate the primary.
        """
        if self._fallback_client is not None:
            return self._fallback_client
        fb_key = (os.environ.get("LLM_FALLBACK_API_KEY", "").strip()
                  or os.environ.get("OPENAI_API_KEY", "").strip())
        fb_base = os.environ.get("LLM_FALLBACK_BASE_URL", "").strip() or None
        self._fallback_model = (os.environ.get("LLM_FALLBACK_MODEL", "").strip()
                                or "gpt-4o-mini")
        if not fb_key:
            return None
        if fb_base == self._base_url and self._fallback_model == self._model:
            return None  # identical to primary — nothing to fall back to
        _kwargs = {"api_key": fb_key, "timeout": self._timeout}
        if fb_base:
            _kwargs["base_url"] = fb_base
        self._fallback_client = AsyncOpenAI(**_kwargs)
        return self._fallback_client

    # ------------------------------------------------------------------
    # Call context (direction + outbound contact identity)
    # ------------------------------------------------------------------

    def set_call_context(
        self,
        direction: str | None = None,
        contact_name: str = "",
        contact_phone: str = "",
        conn_id: str = "",
    ) -> None:
        """Per-call override set by the telephony bridge before the greeting.

        For outbound dials the dashboard logs the contact's name/number at
        dial time (dashboard/call_link.json); the bridge passes them here so
        the agent can greet the contact by name and confirm identity."""
        if direction:
            self.call_direction = direction.lower()
        if contact_name:
            self.outbound_contact_name = contact_name.strip()
            # Prefill so missing-slot detection never re-asks for the name;
            # the prompt still instructs an identity confirmation first.
            if not self.memory.call_state.customer_name:
                self.memory.call_state.customer_name = self.outbound_contact_name
        if contact_phone:
            self.outbound_contact_phone = contact_phone.strip()
        if conn_id:
            self.conn_id = conn_id
        logger.info("[CONTEXT] direction=%s contact=%r", self.call_direction,
                    self.outbound_contact_name or None)

    # ------------------------------------------------------------------
    # Calendar availability + booking persistence
    # ------------------------------------------------------------------

    def _agent_folder(self) -> str:
        try:
            from ..documents.store import get_folder_by_agent_id
            return get_folder_by_agent_id(self._def.agent_id) or ""
        except Exception:
            return ""

    def _available_calendar_slots(self) -> list:
        """Configured mock_slots minus slots already booked in bookings.json."""
        try:
            from ..agent_definition.calendar_store import available_slots
            return available_slots(self._def, self._agent_folder())
        except Exception:
            return list(self._def.calendar.mock_slots or [])

    def _record_calendar_booking(self, appointment_time: str) -> None:
        """Persist a booked slot so later calls see it as taken. Best-effort."""
        try:
            from ..agent_definition.calendar_store import add_booking
            folder = self._agent_folder()
            if not folder:
                return
            slot_day, _, slot_time = appointment_time.partition(" at ")
            b = self.memory.booking
            cs = self.memory.call_state
            add_booking(
                folder,
                slot_day=slot_day.strip(),
                slot_time=slot_time.strip(),
                appointment_time=appointment_time,
                caller_name=b.user_name or cs.customer_name or self.outbound_contact_name,
                caller_phone=cs.customer_phone or self.outbound_contact_phone,
                conn_id=self.conn_id,
            )
        except Exception as exc:
            logger.warning("[CALENDAR] booking persistence failed: %r", exc)

    # ------------------------------------------------------------------
    # Opening greeting (outbound call starts here)
    # ------------------------------------------------------------------

    async def generate_opening(self) -> EngineResult:
        """Inbound warm welcome — agent answers the call.
        Bilingual so caller can respond in their preferred language."""
        greeting = self._def.response_bank.opening_greeting
        # Outbound personalization: greet the dialed contact by name. Canonical
        # form is {contact_name}; also tolerate the template syntaxes LLM
        # editors keep inventing (${contact_name}, {{contact_name}}, [name],
        # <contact>) — a raw template token must NEVER be spoken to a caller.
        _ph = re.compile(
            r"(?:\$\{|\{\{|\{|\[|<)\s*"
            r"(?:contact[ _\-]?name|customer[ _\-]?name|person[ _\-]?name|callee[ _\-]?name|contact|name)"
            r"\s*(?:\}\}|\}|\]|>)"
            r"|\$contact[ _\-]?name\b",
            re.IGNORECASE,
        )
        if _ph.search(greeting):
            _who = self.outbound_contact_name or "sir/ma'am"
            greeting = _ph.sub(lambda _m: _who, greeting)  # lambda: name is literal, not a regex template
        # Time-of-day personalization: {current_time} → morning/afternoon/evening
        # in the caller's timezone (IST). Same tolerance for invented syntaxes.
        _tph = re.compile(
            r"(?:\$\{|\{\{|\{|\[|<)\s*"
            r"(?:current[ _\-]?time|time[ _\-]?of[ _\-]?day|greeting[ _\-]?time|day[ _\-]?part)"
            r"\s*(?:\}\}|\}|\]|>)"
            r"|\$current[ _\-]?time\b",
            re.IGNORECASE,
        )
        if _tph.search(greeting):
            _tod = _time_of_day_word(greeting)
            greeting = _tph.sub(lambda _m: _tod, greeting)
        self.memory.add_assistant(greeting, language=self.state.language)
        self.state.stage = STAGE_GREETING
        # Store for last-response memory (feature 3)
        self.last_agent_response = greeting
        self.last_agent_language = self.state.language
        self.memory.qa_tracker.observe_agent_turn(greeting, self.memory.turn_index)
        logger.info("[OPENING] Inbound welcome delivered")
        return EngineResult(text=greeting, is_shortcut=True, intent="greeting")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(self, text: str) -> EngineResult:
        parts: list[str] = []
        intent = "general"
        is_shortcut = False
        async for result in self.process_stream(text):
            parts.append(result.text)
            intent = result.intent
            is_shortcut = result.is_shortcut
        return EngineResult(text=" ".join(parts), is_shortcut=is_shortcut, intent=intent)

    async def process_stream(self, text: str):
        """
        Proactive streaming conversation engine.

        Extracts structured data, advances the booking flow, and falls back
        to LLM for questions and natural phrasing.
        """
        # Save name state before add_user may auto-extract a name (feature 4)
        _prev_booking_name = self.memory.booking.user_name

        # Context-aware name capture: if agent just asked for name, treat this
        # short response as the caller's name (even without "my name is" prefix).
        # Skipped while a name confirmation is pending — the reply to "Your
        # name is X, right?" is a yes/no, and capturing it here used to store
        # garbage like "Nahi Rajesh" as the confirmed name.
        if not self.memory.booking.user_name and not self._name_pending:
            ctx_name = self._extract_name_if_asked(text)
            if ctx_name:
                # Drop confirmation words that leak into the capture window.
                _toks = [t for t in ctx_name.split()
                         if t.lower() not in ("nahi", "no", "haan", "yes", "ji", "han")]
                ctx_name = " ".join(_toks) if _toks else ""
            if ctx_name:
                self.memory.booking.user_name = ctx_name
                self.memory.context.user_name = ctx_name
                self.memory.call_state.customer_name = ctx_name
                _prev_booking_name = ctx_name  # treat as already known
                # Direct answer to an explicit name question = high confidence:
                # confirm immediately and write through to the state manager.
                # (Previously _name_confirmed stayed False on this path, the
                # sync gate never fired, and the state block demanded the name
                # be re-asked forever — the critical review finding.)
                self._name_confirmed = True
                self.state_mgr.set_externally("name", ctx_name, source="system")
                logger.info("[NAME] Context-aware capture (confirmed): %r", ctx_name)

        self.memory.add_user(text, language=self.state.language)

        # QA: record this user turn against any pending asked-fields
        self.memory.qa_tracker.observe_user_turn(text, self.memory.turn_index)

        # ── Conversation State layer: extract + merge EVERY user turn ──
        # Runs BEFORE shortcut routing so slot values embedded in shortcut
        # turns ("thank you, 2BHK works") are never dropped. Pure regex,
        # <1 ms — adds nothing to perceived latency.
        try:
            _state_changes = self.state_mgr.observe_user(text)
            if _state_changes:
                logger.info("[STATE] Turn extracted %d field(s): %s",
                            len(_state_changes),
                            ", ".join(f"{c.field}={c.new!r}" for c in _state_changes))
            else:
                # LLM gap-filler ONLY when the regex pass found nothing new:
                # structured utterances are already covered, and this halves
                # extractor request volume against the shared Groq quota.
                self._spawn_slot_extractor(text)
        except Exception:
            logger.exception("[STATE] observe_user failed (continuing)")

        # Name-capture: if a new name was just extracted this turn, hold for confirmation
        if (not self._name_confirmed
                and not self._name_pending
                and self.memory.booking.user_name
                and self.memory.booking.user_name != _prev_booking_name):
            self._name_pending = self.memory.booking.user_name
            self._name_pending_turn = self.state.turn_count
            self.memory.booking.user_name = None
            self.memory.context.user_name = None
            self.state_mgr.mark_pending("name")
            logger.info("[NAME] Name pending confirmation: %r", self._name_pending)

        # --- Language detection + stabilization ---
        detected_lang, conf, src = await self.detect_language_v3(text)
        self.state.turn_count += 1
        logger.info('[LANG] "%s" -> %s (src=%s, conf=%.2f, locked=%s)',
                     text, detected_lang, src, conf, self.state.language_locked)

        self.state.language_history.append(detected_lang)
        history = self.state.language_history[-5:]
        self.state.language_history = history

        prev_lang = self.state.language

        is_gujarati_detected = (
            detected_lang == "gu"
            or src == "script_gu_markers"
        )

        # Decay the lock after 10 turns — allows the call to re-evaluate language
        # within a reasonable window when the caller switches naturally.
        # An explicit language_switch intent resets lock_turns to -10 so it
        # persists for 20 turns before decaying, giving strong persistence after
        # a deliberate switch request.
        if self.state.language_locked:
            self.state.language_lock_turns += 1
            if self.state.language_lock_turns > 10:
                self.state.language_locked = False
                logger.info("[LANG] Lock expired after %d turns", self.state.language_lock_turns)

        # Override an incorrect Hindi/English lock to Gujarati when the evidence is clear.
        # Two-tier threshold:
        #   - Strong Devanagari Gujarati markers (छे/छो/छु/केम/etc.) are EXCLUSIVELY
        #     Gujarati — a single turn's evidence is sufficient to override the lock.
        #   - All other Gujarati signals require 2 consecutive turns for confidence.
        # This prevents a Gujarati caller from receiving 2 Hindi responses before the
        # system adapts, while still requiring consistency for weaker signals.
        if is_gujarati_detected and self.state.language_locked and prev_lang != "gu":
            recent_gu = sum(1 for l in history[-2:] if l == "gu")
            single_strong = (recent_gu >= 1 and src == "script_gu_markers")
            if recent_gu >= 2 or single_strong:
                logger.info(
                    "[LANG] Overriding lock to Gujarati (src=%s, recent_gu=%d, "
                    "single_strong=%s)",
                    src, recent_gu, single_strong,
                )
                self.state.language = "gu"
                self.state.language_locked = False
                self.state.language_lock_turns = 0

        # Override a Gujarati lock to Hindi/English when the caller clearly switches.
        # Mirrors the Gujarati override above: 2 consecutive non-Gujarati turns confirm
        # the switch so a single code-switched word doesn't flip the language.
        if (
            not is_gujarati_detected
            and self.state.language_locked
            and prev_lang == "gu"
            and detected_lang in ("hi", "en")
            and conf >= 0.7
        ):
            recent_non_gu = sum(1 for l in history[-2:] if l != "gu")
            if recent_non_gu >= 2:
                logger.info(
                    "[LANG] Overriding Gujarati lock to %s "
                    "(recent_non_gu=%d, src=%s, conf=%.2f)",
                    detected_lang, recent_non_gu, src, conf,
                )
                self.state.language = detected_lang
                self.state.language_locked = False
                self.state.language_lock_turns = 0

        # Gujarati script or markers — switch ONLY if not locked to a different
        # language. Explicit user request (intent="language_switch") is the only
        # thing that can override the lock; Devanagari Gujarati-marker words
        # like "माने" / "छे" appearing inside otherwise-Hindi STT output must
        # not flip the agent back to Gujarati.
        if is_gujarati_detected and (
            not self.state.language_locked or prev_lang == "gu"
        ):
            if self.state.language != "gu":
                logger.info("[LANG] Switching to Gujarati (src=%s)", src)
            self.state.language = "gu"
        elif is_gujarati_detected:
            logger.info(
                "[LANG] Ignoring Gujarati markers (locked to %s, src=%s)",
                prev_lang, src,
            )
        elif src == "pattern" and conf >= 0.7:
            # Pattern-based detection: Hindi/Gujarati romanized words
            self.state.language = detected_lang
        elif src == "script" and detected_lang == "hi":
            # Devanagari text — could be Hindi OR Gujarati via STT.
            # Only switch to Hindi if not currently Gujarati, or if
            # language is not locked.
            if prev_lang != "gu" or not self.state.language_locked:
                self.state.language = "hi"
        elif detected_lang == "en" and src in ("default", "pattern"):
            # Pure-Latin / non-Indic text. Switch to English only on a
            # confident multi-word signal with a raised threshold to prevent
            # Hinglish sentences (e.g. "haan theek hai ok") from flipping a
            # Hindi session. Require 4+ words if currently locked, 2+ if not.
            word_count = len(text.split())
            min_words_for_switch = 4 if self.state.language_locked and prev_lang in ("hi", "gu") else 2
            if word_count >= min_words_for_switch and prev_lang != "en":
                logger.info(
                    "[LANG] Switching to English (ascii multi-word, src=%s, "
                    "words=%d, min=%d, locked=%s)",
                    src, word_count, min_words_for_switch, self.state.language_locked,
                )
                self.state.language = "en"
                # Soft-decay any prior lock so subsequent turns aren't re-locked
                self.state.language_locked = False
                self.state.language_lock_turns = 0
            elif word_count < min_words_for_switch and prev_lang in ("hi", "gu"):
                logger.info(
                    "[LANG] Suppressing English switch (too few words=%d, min=%d, "
                    "maintaining %s)",
                    word_count, min_words_for_switch, prev_lang,
                )
        # Ambiguous/default: preserve current language

        self.state.lang_confidence = conf
        self.state.lang_source = src
        logger.info('[LANG] Response language: %s (detected=%s, prev=%s)',
                     self.state.language, detected_lang, prev_lang)

        # Reset per-turn metrics
        self.state.filler_used = False
        self.state.llm_cancelled = False
        self.state.parallel_execution = True
        self.state.objection_handled = False

        # --- Intent detection ---
        _kb = self._def.knowledge_base
        intent = detect_intent(
            text,
            self.state.stage,
            location_aliases=frozenset(_kb.location_aliases),
            objection_patterns=_kb.objection_patterns,
            time_patterns=_kb.time_patterns,
        )

        # --- Fire LLM in background (parallel with deterministic routing) ---
        llm_queue: asyncio.Queue[str | None] = asyncio.Queue()
        llm_done = asyncio.Event()
        llm_task = asyncio.create_task(
            self._run_llm_to_queue(text, llm_queue, llm_done),
            name="parallel_llm",
        )

        def _cancel_llm():
            if not llm_task.done():
                llm_task.cancel()
            self.state.llm_cancelled = True

        self.state.last_intent = intent
        logger.info('[INTENT] "%s" -> %s (stage=%s)', text, intent, self.state.stage)

        # Reset decline counter when the user re-engages with a non-objection
        # intent — we don't want a stale "1 prior decline" to trip the 2-strike
        # farewell after the user has clearly come back into the conversation.
        if intent not in ("objection", "farewell"):
            self.state.decline_count = 0

        # =============================================================
        # STAGE-AWARE ROUTING
        # =============================================================

        # --- Repeat request (feature 3) — replay or re-render last agent response ---
        if self._is_repeat_request(text) and self.last_agent_response:
            # Check if caller also requested a language change (e.g. "repeat in Gujarati")
            _repeat_has_lang = any(
                p in text.lower()
                for p in [
                    "gujarati", "hindi", "english",
                    "ગુજરાતી", "gu ma", "gu mein",
                    "हिंदी", "हिन्दी", "hindi mein", "hindi mai",
                    "english mein", "english mai",
                ]
            )
            if _repeat_has_lang:
                # Extract desired language and re-render via LLM in the new language
                new_lang = self._extract_requested_language(text)
                if new_lang != self.state.language:
                    logger.info("[REPEAT] Language-change repeat: %s -> %s", self.state.language, new_lang)
                    self.state.language = new_lang
                    self.state.language_locked = True
                    self.state.language_lock_turns = -7
                    self.state.language_history = [new_lang] * 5
                logger.info("[REPEAT+LANG] Re-rendering last response in %s", self.state.language)
                # LLM will see the updated language in the system prompt and re-render
            else:
                _cancel_llm()
                resp = self.last_agent_response
                logger.info("[REPEAT] Replaying last response (lang=%s): %r",
                            self.last_agent_language, resp[:60])
                self.memory.add_assistant(resp, language=self.state.language)
                self._store_last_response(resp)
                yield EngineResult(text=resp, is_shortcut=True, intent="repeat")
                return

        # --- Farewell at any stage ---
        if intent == "farewell":
            _cancel_llm()
            self.state.is_closing = True
            resp = self._localized("closing", "Thank you for your time! We'll be in touch. Have a great day!")
            self.memory.add_assistant(resp, language=self.state.language)
            self._store_last_response(resp)
            self.state.stage = STAGE_CLOSING
            yield EngineResult(text=resp, is_shortcut=True, intent="farewell")
            return

        # --- Objection at any stage (except GREETING, which has its own handler) ---
        if intent == "objection" and self.state.stage != STAGE_GREETING:
            _cancel_llm()
            self.state.objection_handled = True
            self.state.decline_count += 1

            # Second decline: stop pushing and end gracefully.
            if self.state.decline_count >= 2:
                self.state.is_closing = True
                self.state.stage = STAGE_CLOSING
                resp = self._def.response_bank.decline_farewell.get(
                    self.state.language, self._def.response_bank.decline_farewell["en"]
                )
                self.memory.add_assistant(resp, language=self.state.language)
                self._store_last_response(resp)
                yield EngineResult(text=resp, is_shortcut=True, intent="farewell")
                return

            resp = self._pick_objection_response()
            self.memory.add_assistant(resp, language=self.state.language)
            self._store_last_response(resp)
            yield EngineResult(text=resp, is_shortcut=True, intent="objection")
            return

        # --- Gratitude at any stage ---
        if intent == "gratitude":
            _cancel_llm()
            resp = self._localized_list("gratitude", ["You're welcome!", "Happy to help!"])
            self.memory.add_assistant(resp, language=self.state.language)
            self._store_last_response(resp)
            yield EngineResult(text=resp, is_shortcut=True, intent="gratitude")
            return

        # --- Identity question at any stage — fast deterministic response ---
        if intent == "identity_question":
            _cancel_llm()
            lang = self.state.language
            resp = self._def.response_bank.identity_responses.get(lang, self._def.response_bank.identity_responses["en"])
            self.memory.add_assistant(resp, language=lang)
            self._store_last_response(resp)
            if self.state.stage == STAGE_GREETING:
                self.state.stage = self._post_greeting_stage()
            yield EngineResult(text=resp, is_shortcut=True, intent="identity_question")
            return

        # --- Language switch at any stage ---
        if intent == "language_switch":
            _cancel_llm()
            new_lang = self._extract_requested_language(text)
            logger.info(
                "[LANG] Explicit switch request -> %s (was %s, locked=%s)",
                new_lang, self.state.language, self.state.language_locked,
            )
            self.state.language = new_lang
            self.state.language_locked = True
            # Start at -7 so the lock persists for 17 turns before decaying.
            # Explicit caller requests should persist but allow natural switching.
            self.state.language_lock_turns = -7
            self.state.language_history = [new_lang] * 5
            resp = self._def.response_bank.lang_switch_responses.get(new_lang, self._def.response_bank.lang_switch_responses["hi"])
            self.memory.add_assistant(resp, language=new_lang)
            self._store_last_response(resp)
            logger.info("[LANG-LOCK] explicit_switch lang=%s lock_turns=%d (persists ~17 turns)",
                        new_lang, self.state.language_lock_turns)
            yield EngineResult(text=resp, is_shortcut=True, intent="language_switch")
            return

        # =============================================================
        # NON-SHORTCUT ROUTING — extract data, advance stage, let LLM respond
        # =============================================================

        # Extract any structured info as a side effect (non-blocking)
        self._extract_data_from_input(text, intent)

        # Write-through: sync legacy extractor results into the state manager
        # so the CONVERSATION STATE block reflects everything, whichever
        # extractor caught it first.
        try:
            self._sync_state_manager()
        except Exception:
            logger.exception("[STATE] sync failed (continuing)")

        # Name confirmation resolution (feature 4)
        if self._name_pending:
            if intent == "affirmation":
                self.memory.booking.user_name = self._name_pending
                self.memory.context.user_name = self._name_pending
                self._name_confirmed = True
                self.state_mgr.set_externally("name", self._name_pending, source="system")
                logger.info("[NAME] Confirmed name: %r", self._name_pending)
                self._name_pending = None
            elif intent == "negation":
                logger.info("[NAME] Caller rejected name %r — discarding pending", self._name_pending)
                # discard (not clear): also nulls any value that leaked into
                # the slot, so a rejected name can never become ground truth.
                self.state_mgr.discard_pending("name")
                self._name_pending = None
            elif self.state.turn_count - self._name_pending_turn >= 2:
                # Caller neither confirmed nor denied after 2 turns — auto-accept.
                # They likely just said their name and kept talking naturally.
                self.memory.booking.user_name = self._name_pending
                self.memory.context.user_name = self._name_pending
                self.memory.call_state.customer_name = self._name_pending
                self._name_confirmed = True
                self.state_mgr.set_externally("name", self._name_pending, source="system")
                logger.info("[NAME] Auto-accepted name after 2 turns: %r", self._name_pending)
                self._name_pending = None

        # Advance from GREETING on first user response
        if self.state.stage == STAGE_GREETING:
            self.state.stage = self._post_greeting_stage()
            logger.info("[STAGE] GREETING → %s", self.state.stage)

        # =============================================================
        # SOFT STAGE ADVANCEMENT based on collected data
        # =============================================================
        self._maybe_advance_stage()

        # =============================================================
        # LLM handles all non-shortcut turns with full inbound script context
        # The system prompt guides the LLM through the 9-stage flow naturally.
        # =============================================================
        self.state.llm_cancelled = False
        async for result in self._consume_llm_with_filler(
            llm_queue, llm_done, intent, nudge_back=False,
        ):
            yield result


    # ------------------------------------------------------------------
    # Inbound flow helpers
    # ------------------------------------------------------------------

    def _extract_data_from_input(self, text: str, intent: str) -> None:
        """Extract structured data as a side effect — never blocks the flow."""
        lower = text.lower()
        cs = self.memory.call_state

        if "location" not in cs.domain_fields:
            loc = self._extract_location(text)
            if loc:
                cs.domain_fields["location"] = loc
                self.memory.qa_tracker.mark_extracted("location", loc)
                logger.info("[DISCOVERY] Location extracted: %s", loc)

        if "property_type" not in cs.domain_fields:
            bhk = self._extract_bhk(text)
            if bhk:
                cs.domain_fields["property_type"] = bhk.upper()
                self.memory.qa_tracker.mark_extracted("bhk", bhk.upper())
                logger.info("[DISCOVERY] Config extracted: %s", bhk)

        if not self.memory.booking.appointment_time:
            t = self._extract_time_preference(text, stage=self.state.stage)
            if t:
                self.memory.booking.appointment_time = t
                self._record_calendar_booking(t)
                logger.info("[DISCOVERY] Time extracted: %s", t)

        if not self.state.purpose and "purpose" not in cs.domain_fields:
            # Multi-word / script phrases: safe to use substring matching (rare false positives)
            invest_kw_exact = [
                "rental income", "rental return", "return on investment",
                "kiraya", "kiraye",
                "रिटर्न", "निवेश", "ભાડું", "રોકાણ",
            ]
            self_use_kw_exact = [
                "self use", "self-use", "own use",
                "apne liye", "rehna",
                "ઘર", "રહેવા", "ખુદ", "ફેમિલી",
            ]
            # Single English words that need word-boundary matching to avoid
            # false positives ("returns my call", "staying on topic", etc.)
            _invest_bound_re = re.compile(
                r'\b(?:investment|investing|investor|invest|appreciation|'
                r'rental income|rental return|profit|yield)\b'
                r'|\breturn on investment\b',
                re.IGNORECASE
            )
            _self_use_bound_re = re.compile(
                r'\b(?:family|families|stay|staying|live|living|reside|residing|'
                r'ghar|makan|rahega|settle|settling)\b', re.IGNORECASE
            )
            if (any(kw in lower or kw in text for kw in invest_kw_exact)
                    or _invest_bound_re.search(lower)):
                self.state.purpose = "investment"
                self.memory.qa_tracker.mark_extracted("purpose", "investment")
            elif (any(kw in lower or kw in text for kw in self_use_kw_exact)
                    or _self_use_bound_re.search(lower)):
                self.state.purpose = "self_use"
                self.memory.qa_tracker.mark_extracted("purpose", "self_use")

        # ── Budget extraction ─────────────────────────────────────────────
        if "budget" not in cs.domain_fields:
            budget = self._extract_budget(text)
            if budget:
                cs.domain_fields["budget"] = budget
                self.memory.qa_tracker.mark_extracted("budget", budget)
                logger.info("[DISCOVERY] Budget extracted: %s", budget)

        # ── Timeline extraction ───────────────────────────────────────────
        if "timeline" not in cs.domain_fields:
            timeline = self._extract_timeline(text)
            if timeline:
                cs.domain_fields["timeline"] = timeline
                self.memory.qa_tracker.mark_extracted("timeline", timeline)
                logger.info("[DISCOVERY] Timeline extracted: %s", timeline)

        # ── Entity detection (persistent context lock) ───────────────────
        # Match against known_projects/entities in the agent definition.
        # First match is locked for the session (stored in domain_fields).
        if "matched_entity" not in cs.domain_fields:
            entity_result = self._extract_project_from_text(text)
            if entity_result:
                entity_name, entity_loc = entity_result
                cs.domain_fields["matched_entity"] = entity_name
                if entity_loc:
                    cs.domain_fields["entity_location"] = entity_loc
                self.state.project_recommended = entity_name
                logger.info(
                    "[STATE_UPDATE] matched_entity=%r location=%r",
                    entity_name, entity_loc,
                )

        # ── Sync all layers into CallState ───────────────────────────────
        self._update_call_state()
        self.state.discovery_turns += 1

    def _post_greeting_stage(self) -> str:
        """Stage to enter after the greeting turn. Legacy flows land on
        DISCOVERY; custom-id flows (builder/copilot agents whose stages don't
        include DISCOVERY) start at their OWN first stage. Without this, the
        unknown 'DISCOVERY' id fell through _maybe_advance_stage's index-0
        fallback with the turn budget already spent, so the first custom
        stage's goal never governed a single LLM turn (e.g. an outbound
        CONFIRM_PERSON stage was skipped and the pitch fired on turn 1 even
        when a stranger answered the phone)."""
        stages = self._def.call_flow.stages
        if not stages or any(s.id == STAGE_DISCOVERY for s in stages):
            return STAGE_DISCOVERY
        return stages[0].id

    def _maybe_advance_stage(self) -> None:
        """Domain-agnostic stage advancement driven by turn count and booking events."""
        b = self.memory.booking
        cs = self.memory.call_state
        stage_list = self._def.call_flow.stages
        current_idx = next((i for i, s in enumerate(stage_list) if s.id == self.state.stage), 0)
        if current_idx >= len(stage_list) - 1:
            return  # already at last stage

        next_stage_id = stage_list[current_idx + 1].id
        last_stage_id = stage_list[-1].id
        current_def = stage_list[current_idx]
        dt = self.state.discovery_turns

        # Booking confirmed → jump to last stage (CLOSING equivalent)
        if b.confirmed and self.state.stage != last_stage_id:
            self.state.stage = last_stage_id
            self.state.is_closing = True
            self.state.discovery_turns = 0
            logger.info("[STAGE] → %s (booking confirmed)", last_stage_id)
            return

        # Appointment time captured → advance once, then stop reacting to this
        # event. getattr guard: appointment_advanced was never initialized in
        # ConversationState — the bare attribute access raised AttributeError
        # the first time an appointment was ever captured (latent prod bug).
        if b.appointment_time and not getattr(self.state, "appointment_advanced", False):
            self.state.appointment_advanced = True
            self.state.stage = next_stage_id
            self.state.discovery_turns = 0
            logger.info("[STAGE] → %s (appointment_time set)", next_stage_id)
            return

        # Slot-aware early advancement: when the caller front-loads their
        # requirements ("3BHK in Vapi under 50 lakhs"), don't hold them in
        # DISCOVERY for the scripted turn count — advance as soon as every
        # required discovery slot is filled.
        if self.state.stage == STAGE_DISCOVERY:
            gaps = self.state_mgr.detect_missing_slots()
            _required = sum(1 for s in self.state_mgr.slots.values() if s.required)
            # Guards: pending counts as incomplete, and name-only agents
            # (pitch flows) never early-advance — a captured name is not
            # "discovery complete"; their pacing stays turn-count driven.
            if _required >= 2 and not gaps["missing"] and not gaps.get("pending"):
                self.state.stage = next_stage_id
                self.state.discovery_turns = 0
                logger.info("[STAGE] → %s (all required slots filled — early advance)",
                            next_stage_id)
                return

        # Turn-count based advancement
        threshold = current_def.turns_until_advance
        if dt >= threshold:
            self.state.stage = next_stage_id
            self.state.discovery_turns = 0
            logger.info("[STAGE] → %s (turn threshold %d reached)", next_stage_id, threshold)

        logger.debug(
            "[STAGE-STATE] stage=%s turns=%d/%d appt=%s confirmed=%s domain=%s",
            self.state.stage, dt, current_def.turns_until_advance,
            b.appointment_time or "—", b.confirmed, cs.domain_fields,
        )

    # ------------------------------------------------------------------
    # Call-state management helpers
    # ------------------------------------------------------------------

    def _sync_state_manager(self) -> None:
        """Write-through legacy extraction results into the state manager.

        The legacy extractors (_extract_data_from_input, memory name/phone
        capture) write into cs.domain_fields / booking; the state manager is
        the single source of truth for the prompt, so mirror everything in.
        """
        cs = self.memory.call_state
        b = self.memory.booking
        sm = self.state_mgr
        mapping = {
            "location": "location",
            "budget": "budget",
            "timeline": "timeline",
            "purpose": "purpose",
        }
        for src_key, slot in mapping.items():
            val = cs.domain_fields.get(src_key)
            if val is not None:
                sm.merge_state({slot: val}, source="legacy", fill_only=True)
        ptype = cs.domain_fields.get("property_type")
        if ptype:
            # Legacy stores "3 BHK"-style values under property_type.
            target = "bhk" if "bhk" in str(ptype).lower() else "property_type"
            sm.merge_state({target: str(ptype).replace(" ", "").upper()
                            if target == "bhk" else ptype},
                           source="legacy", fill_only=True)
        if b.user_name and self._name_confirmed:
            sm.set_externally("name", b.user_name, source="legacy")
        # Meeting-booking agents: a booked appointment IS the meeting time —
        # write it through so the state block never re-asks after booking.
        if b.appointment_time and "meeting_time" in sm.state:
            when = f"{b.appointment_date or ''} {b.appointment_time}".strip()
            sm.set_externally("meeting_time", when, source="legacy")

    # ── Async LLM slot extraction (gap-filler; never on the hot path) ───

    def _spawn_slot_extractor(self, text: str) -> None:
        """Fire a background LLM extraction for slots the regexes missed.

        Latency-safe: runs concurrently with response generation; result
        merges with fill_only=True so a slow/stale completion can only fill
        still-empty slots, never overwrite anything newer. At most one task
        in flight — a new turn cancels the previous extractor.
        """
        if not self._slot_extract_enabled or not self._api_key:
            return
        if len(text.split()) < 3:
            return
        missing = self.state_mgr.detect_missing_slots()["missing"]
        if not missing:
            return
        if self._slot_extract_task and not self._slot_extract_task.done():
            self._slot_extract_task.cancel()
        self._slot_extract_task = asyncio.create_task(
            self._llm_extract_slots(text, missing), name="slot_extractor",
        )

    async def _llm_extract_slots(self, text: str, missing: list[str]) -> None:
        try:
            client = self._get_client()
            keys = ", ".join(f'"{m}"' for m in missing)
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self._model,
                    messages=[{
                        "role": "system",
                        "content": (
                            "Extract structured fields from one caller utterance "
                            "(may be Hinglish/Hindi/Gujarati/English, possibly noisy "
                            "phone-call ASR). Return ONLY a JSON object with exactly "
                            f"these keys: {{{keys}}}. Use null for anything not "
                            "clearly stated. Values must be short canonical strings "
                            "(e.g. \"3BHK\", \"50 Lakhs\", \"Vapi\", \"Next month\", "
                            "\"Yes\", \"No\")."
                        ),
                    }, {"role": "user", "content": text}],
                    max_tokens=120,
                    temperature=0,
                    response_format={"type": "json_object"},
                ),
                timeout=2.5,
            )
            raw = (resp.choices[0].message.content or "").strip()
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return
            data = {k: v for k, v in json.loads(m.group(0)).items()
                    if isinstance(v, (str, int, float)) and str(v).strip()
                    and str(v).lower() not in ("null", "none", "unknown", "n/a")}
            if data:
                changes = self.state_mgr.merge_state(
                    data, source="llm", utterance=text, fill_only=True)
                if changes:
                    logger.info("[STATE:LLM] Gap-filled %s",
                                ", ".join(f"{c.field}={c.new!r}" for c in changes))
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as exc:
            logger.debug("[STATE:LLM] extractor skipped: %s", exc)

    def _extract_project_from_text(self, text: str) -> tuple[str, str] | None:
        """Return (canonical_name, location) if a known project is mentioned.

        Project keys are sorted longest-first so multi-word names
        like 'aranya ii' are matched before shorter substrings like 'aranya'.
        Word-boundary matching prevents short names (aura, orbit, satva) from
        spuriously matching inside unrelated words.
        """
        lower = text.lower()
        _projects = self._def.knowledge_base.known_projects
        for key in sorted(_projects, key=len, reverse=True):
            if re.search(r'\b' + re.escape(key) + r'\b', lower):
                return _projects[key]
        return None

    def _update_call_state(self) -> None:
        """Sync CallState from ConversationState + BookingData.

        Called after every data-extraction pass so the call_state is always
        up to date before being injected into the system prompt.
        """
        cs = self.memory.call_state
        b = self.memory.booking

        # Customer name — prefer the confirmed booking name
        if b.user_name and not cs.customer_name:
            cs.customer_name = b.user_name
            logger.info("[STATE_UPDATE] customer_name=%r (from booking)", cs.customer_name)
        elif cs.customer_name and not b.user_name:
            b.user_name = cs.customer_name

        # Appointment — sync both directions
        if b.appointment_time and not cs.appointment_time:
            cs.appointment_time = b.appointment_time
        elif cs.appointment_time and not b.appointment_time:
            b.appointment_time = cs.appointment_time
            self._record_calendar_booking(b.appointment_time)
        if b.appointment_date and not cs.appointment_date:
            cs.appointment_date = b.appointment_date

        # Language + Stage always mirror live state
        cs.language = self.state.language
        cs.conversation_stage = self.state.stage

        # Sync name into QA tracker when confirmed
        if cs.customer_name and not self.memory.qa_tracker.is_answered("name"):
            self.memory.qa_tracker.mark_extracted("name", cs.customer_name)

        logger.info(
            "[STATE] name=%s phone=%s appt=%s stage=%s lang=%s domain=%s",
            cs.customer_name or "—",
            ("***" + cs.customer_phone[-4:]) if cs.customer_phone else "—",
            cs.appointment_time or "—",
            cs.conversation_stage,
            cs.language,
            cs.domain_fields,
        )

    def _compute_missing_booking_fields(self) -> list[str]:
        """Return list of still-missing mandatory booking fields.

        No side effects — safe to call from _build_system_prompt.
        """
        cs = self.memory.call_state
        b = self.memory.booking
        missing: list[str] = []
        if b.appointment_time or cs.appointment_time:
            if not (b.user_name or cs.customer_name):
                missing.append("customer name")
            if not cs.customer_phone:
                missing.append("phone number")
        return missing

    def _validate_booking_for_closing(self) -> tuple[bool, list[str]]:
        """Validate mandatory booking fields; log results.

        Returns (is_valid, missing_field_names).
        Called by _maybe_advance_stage before allowing CLOSING.
        """
        cs = self.memory.call_state
        missing = self._compute_missing_booking_fields()
        valid = len(missing) == 0
        logger.info(
            "[BOOKING_VALIDATION] valid=%s missing=%s",
            valid,
            missing if missing else "none",
        )
        if missing:
            logger.info("[MISSING_REQUIRED_FIELDS] %s", ", ".join(missing))
        cs.booking_validated = valid
        return valid, missing

    # ------------------------------------------------------------------
    # Data extraction helpers
    # ------------------------------------------------------------------

    def _extract_budget(self, text: str) -> str | None:
        """Extract budget range from user input (lakh/crore amounts)."""
        lower = text.lower()
        # Crore patterns: "1 crore", "1.5 crore", "डेढ़ करोड़", etc.
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:crore|cr\.?|करोड़|karod|cr\b)", lower)
        if m:
            return f"{m.group(1)} crore"
        # Lakh patterns: "70 lakh", "50-60 lakh", "70L", etc.
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakh|lac|l\b|लाख|lakh)", lower)
        if m:
            return f"{m.group(1)} lakh"
        # Range: "50 to 70 lakh"
        m = re.search(r"(\d+)\s*(?:to|-)\s*(\d+)\s*(?:lakh|lac|l\b|लाख)", lower)
        if m:
            return f"{m.group(1)}-{m.group(2)} lakh"
        # Gujarati/Hindi script numbers with lakh
        if "લાખ" in text or "करोड़" in text:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:લાખ|करोड़)", text)
            if m:
                unit = "lakh" if "લાખ" in text else "crore"
                return f"{m.group(1)} {unit}"
        return None

    def _extract_location(self, text: str) -> str | None:
        loc = match_location(text)
        if loc:
            return loc.capitalize()
        lower = text.lower()
        for city in ["surat", "vapi", "silvassa"]:
            if city in lower:
                return city.capitalize()
        for hi, en in [("सूरत", "Surat"), ("वापी", "Vapi"), ("सिलवासा", "Silvassa")]:
            if hi in text:
                return en
        for gu, en in [("સુરત", "Surat"), ("વાપી", "Vapi"), ("સિલવાસા", "Silvassa")]:
            if gu in text:
                return en
        return None

    def _extract_bhk(self, text: str) -> str | None:
        bhk = match_bhk(text)
        if bhk:
            return bhk
        m = re.search(r"(\d)\s*(?:bhk|bed)", text, re.IGNORECASE)
        if m and 1 <= int(m.group(1)) <= 5:
            return f"{m.group(1)} bhk"
        return None

    def _extract_timeline(self, text: str) -> str | None:
        """Extract caller's stated purchase/move-in timeline."""
        lower = text.lower()
        # Immediate / ready now
        if re.search(r"\b(immediate|right away|asap|ready now|abhi|abhi chahiye|turant)\b", lower):
            return "immediate"
        if any(w in lower for w in ["अभी", "तुरंत", "હમણાં"]):
            return "immediate"
        # Within N months
        m = re.search(r"(\d+)\s*(?:month|mahine|महीने|maheena|महीनों|mashina|mashin)", lower)
        if m:
            return f"{m.group(1)} months"
        # Within N years
        m = re.search(r"(\d+)\s*(?:year|saal|साल|varsh|वर्ष|varsha)", lower)
        if m:
            return f"{m.group(1)} year(s)"
        # End of year / this year
        if re.search(r"\b(end of year|this year|is saal|is varsh|2025|2026)\b", lower):
            return "this year"
        # 6 months / half year
        if re.search(r"\b(6 months?|six months?|chhe mahine|6 mahine)\b", lower):
            return "6 months"
        # 3 months / quarter
        if re.search(r"\b(3 months?|three months?|teen mahine|quarter)\b", lower):
            return "3 months"
        return None

    # Name extraction signals — checked against the last agent response
    _NAME_ASK_SIGNALS = [
        "your name", "naam", "aapka naam", "naam kya", "naam batao",
        "naam bataiye", "naam bata", "apna naam", "apna name",
        "what should i call", "may i know your name", "introduce yourself",
        "aapka naam kya hai", "naam bataiye", "naam kya hai",
        "आपका नाम", "नाम बताइए", "नाम क्या", "अपना नाम",
        "tamaru naam", "tamaro naam", "tamaru", "aapnu naam",
        "tamaru naam shu che", "tamaro naam shu che",
        "તમારું નામ", "નામ", "શું નામ",
    ]

    _NAME_BLOCKLIST = {
        "a", "the", "just", "not", "is", "interested", "busy",
        "calling", "looking", "available", "free", "here", "there",
        "good", "fine", "okay", "sure", "ready", "happy", "sorry",
        "glad", "trying", "going", "coming", "waiting", "talking",
        "asking", "wondering", "thinking", "planning", "searching",
        # Feeling/state words — "i'm even better", "i'm doing well" etc. are
        # small-talk answers to "how are you?", never a name introduction.
        "even", "better", "best", "well", "doing", "great", "alright",
        "blessed", "grateful", "thankful", "tired", "bored", "stressed",
        "exhausted", "sleepy", "hungry", "sick", "ill", "unwell",
        "nervous", "anxious", "worried", "scared", "afraid", "confused",
        "fantastic", "wonderful", "excellent", "perfect", "amazing",
        "awesome", "fabulous", "cool", "nice", "bit",
        # Intensifiers / adverbs that precede a state word
        "very", "really", "so", "too", "quite", "pretty", "more", "much",
        "still", "all", "right", "kind", "sort", "of", "feeling",
        "absolutely", "totally", "super", "completely", "actually",
        "honestly", "literally", "ok",
        # Courtesy / address words that trail a state reply
        "thanks", "thank", "thankyou", "thanku", "thx", "welcome",
        "please", "yaar", "bhai", "sir", "madam", "you", "yourself",
        "today", "now", "also", "anyway", "though",
        "surat", "vapi", "silvassa", "pramukh", "vishal", "patel",
        "yes", "no", "haan", "nahi", "ji", "ha", "na",
        # Greetings — never a name
        "hello", "helo", "hellow", "hi", "hey", "namaste", "namaskar",
        "kem", "kemcho", "hallo", "halo", "bolo", "suno", "suniye",
        "boliye",
        # possessive/pronoun words that precede a name — not a name themselves
        "mera", "meri", "mere", "naam", "my", "name",
        "maro", "mari", "maru", "apnu", "apna", "aapka",
        "tamaru", "tamaro", "tamari", "ame", "tame",
    }

    # Romanized "my name is/was" phrases — user is stating their name will follow,
    # not that the phrase itself IS the name.
    _MY_NAME_PHRASES = frozenset({
        "mera naam", "meri naam", "maro naam", "maru naam",
        "my name", "my name is", "i am", "i'm",
        "tamaro naam", "tamaru naam", "apna naam",
    })

    def _extract_name_if_asked(self, user_text: str) -> str | None:
        """Return a name candidate when the agent's last response asked for the name
        and the user replied with a short phrase that looks like a name.

        This covers the common case where the caller just says their name
        (e.g. "Rahul") without the "my name is" prefix.
        """
        if not self.last_agent_response:
            return None

        agent_lower = self.last_agent_response.lower()
        if not any(s in agent_lower for s in self._NAME_ASK_SIGNALS):
            return None

        user_text = user_text.strip()
        # Reject pure "my name" phrases — the actual name wasn't captured yet
        lower_text = user_text.lower()
        if lower_text in self._MY_NAME_PHRASES or any(
            lower_text == phrase for phrase in self._MY_NAME_PHRASES
        ):
            return None
        # Must be short — names are 1-3 words
        words = user_text.split()
        if len(words) == 0 or len(words) > 4:
            return None
        # Must not contain question markers
        if "?" in user_text:
            return None
        if any(w in user_text.lower() for w in ["what", "kya", "kon", "kaun", "kahan", "kyun"]):
            return None
        # Must not be a location or BHK
        if match_location(user_text) or match_bhk(user_text):
            return None
        # Must not be entirely lowercase short common words
        if all(w.lower() in self._NAME_BLOCKLIST for w in words):
            return None
        # Take consecutive title-cased or long-enough words as the name
        name_parts = []
        for w in words:
            clean = re.sub(r"[^A-Za-z]", "", w)
            if len(clean) < 2:
                break
            if clean[0].isupper() or len(clean) >= 3:
                name_parts.append(clean.capitalize())
        if not name_parts:
            return None
        candidate = " ".join(name_parts)
        if candidate.lower() in self._NAME_BLOCKLIST:
            return None
        return candidate

    def _extract_time_preference(self, text: str, stage: str | None = None) -> str | None:
        """
        Match user-stated time/day to an available slot.

        Returns None when the user gave a SPECIFIC time that isn't on the
        slot list — never silently fudges to a different slot. The engine's
        scheduling reprompt path then offers the real available slots back.

        The ``stage`` parameter gates the bare-affirmation heuristic (Step 5):
        mapping a lone "yes/ok/haan" to the first slot only makes sense when
        the agent just offered slots (INVITE_VISIT/CONFIRMATION).  In earlier
        stages the same words are general affirmations and must NOT book time.
        """
        lower = text.lower()

        # --- Step 1: extract specific hour if present ---
        hour_24: int | None = None
        # English "11 am", "6 pm", "5:00 pm"
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b", lower)
        if m:
            h = int(m.group(1))
            is_pm = "p" in m.group(3)
            hour_24 = (h % 12) + (12 if is_pm else 0)
        else:
            # Hindi/Gujarati/transliterated: "11 बजे", "11 વાગે", "11 baje"
            m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(?:बजे|वजे|વાગે|baje|baaje)", text)
            if m:
                h = int(m.group(1))
                is_evening = bool(re.search(
                    r"(शाम|સાંજે|saanje|shaam|evening|night|रात|રાત)", text + lower))
                is_afternoon = bool(re.search(
                    r"(दोपहर|બપોરે|bapore|dopahar|afternoon)", text + lower))
                is_morning = bool(re.search(
                    r"(सुबह|સવારે|savare|subah|morning)", text + lower))
                if is_evening or is_afternoon:
                    hour_24 = (h % 12) + 12
                elif is_morning:
                    hour_24 = h % 12 if h != 12 else 12
                else:
                    # Default heuristic: 1-7 → PM (afternoon/evening),
                    # 8-12 → AM (morning) — fits typical site-visit phrasing
                    hour_24 = (h % 12) + (12 if 1 <= h <= 7 else 0)

        # --- Step 2: extract day ---
        day: str | None = None
        if re.search(r"\b(today|aaj|aaje)\b", lower) or "आज" in text or "આજે" in text:
            day = "Today"
        elif (re.search(r"\b(tomorrow|kal|kaale)\b", lower)
              or "कल" in text or "કાલે" in text):
            day = "Tomorrow"
        elif (re.search(r"\b(parso|parson|day after tomorrow)\b", lower)
              or "परसों" in text or "परसो" in text
              or "પરમ" in text or "પરસું" in text or "પરસો" in text):
            day = "Day after tomorrow"
        elif re.search(r"\b(saturday|this saturday|weekend)\b", lower):
            day = "This Saturday"

        # --- Step 3: if specific time given, match strictly against slots ---
        # Only offer/accept slots that are still free (bookings.json aware).
        _slots = self._available_calendar_slots()
        if not _slots:
            return None
        if hour_24 is not None:
            for slot in _slots:
                if day and slot.day != day:
                    continue
                if _slot_hour_24(slot.time) == hour_24:
                    # (was slot['day']/slot['time'] — TypeError on dataclass slots)
                    return f"{slot.day} at {slot.time}"
            # User gave a specific hour with no matching slot — caller will
            # reprompt with available options instead of locking a wrong time.
            return None

        # --- Step 4: no specific hour. Match by part-of-day + day. ---
        period: str | None = None
        if re.search(r"(सुबह|સવારે|savare|subah|morning)", text + lower):
            period = "morning"
        elif re.search(r"(दोपहर|બપોરે|bapore|dopahar|afternoon)", text + lower):
            period = "afternoon"
        elif re.search(r"(शाम|સાંજે|saanje|shaam|evening|night)", text + lower):
            period = "evening"

        if day or period:
            for slot in _slots:
                if day and slot.day != day:
                    continue
                if period and _slot_period(slot.time) != period:
                    continue
                return f"{slot.day} at {slot.time}"
            # Day matched but period didn't (e.g. user wants "Tomorrow evening"
            # — no evening slots tomorrow). Don't fudge: caller reprompts.
            if period:
                return None
            # Day-only match: take the first slot of that day.
            for slot in _slots:
                if slot.day == day:
                    return f"{slot.day} at {slot.time}"

        # --- Step 5: bare affirmation → first available slot ---
        # ONLY apply when the agent is actively offering time slots (INVITE_VISIT or
        # CONFIRMATION stage).  In DISCOVERY/GREETING/PROJECT_MATCH stages the same
        # words ("yes", "haan", "ji") are general affirmations — mapping them to a
        # slot would silently pre-book a visit the caller never agreed to schedule.
        _scheduling_stages = (STAGE_INVITE_VISIT, STAGE_CONFIRMATION)
        if stage is None or stage in _scheduling_stages:
            if re.search(r"\b(yes|yeah|ok|okay|sure|haan|ha|ji|theek|first|pehla)\b", lower):
                return f"{_slots[0].day} at {_slots[0].time}"
            if any(w in text for w in ("ठीक", "हां", "हाँ", "જી", "હા", "બરાબર", "ઠીક")):
                return f"{_slots[0].day} at {_slots[0].time}"

        return None

    # ------------------------------------------------------------------
    # Acknowledgment builders
    # ------------------------------------------------------------------

    def _location_ack(self, location: str) -> str:
        lang = self.state.language
        if lang == "hi":
            return f"बढ़िया, {location} एक बहुत अच्छी लोकेशन है!"
        elif lang == "gu":
            return f"સરસ, {location} ખૂબ સારી લોકેશન છે!"
        return f"Excellent choice! {location} is a wonderful location."

    def _bhk_ack(self, bhk: str, location: str) -> str:
        lang = self.state.language
        bhk_upper = bhk.upper()
        if lang == "hi":
            return f"बढ़िया! {location} में {bhk_upper} के लिए हमारे पास शानदार विकल्प हैं।"
        elif lang == "gu":
            return f"સરસ! {location} માં {bhk_upper} માટે અમારી પાસે ઉત્તમ વિકલ્પો છે."
        return f"Great! We have some fantastic {bhk_upper} options in {location}."

    def _build_confirmation_prompt(self) -> str:
        b = self.memory.booking
        cs = self.memory.call_state
        lang = self.state.language
        entity = cs.domain_fields.get("matched_entity") or ""
        appt = b.appointment_time or ""
        if lang == "hi":
            ctx = f"{entity} के लिए " if entity else ""
            return f"तो मैं नोट कर लेता हूँ — {ctx}{appt or 'कल'} का अपॉइंटमेंट। क्या ये सही है?"
        elif lang == "gu":
            ctx = f"{entity} માટે " if entity else ""
            return f"તો હું નોંધ કરી લઉં — {ctx}{appt or 'કાલ'} ની મુલાકાત. આ બરાબર છે?"
        ctx = f"for {entity} " if entity else ""
        return f"Let me confirm — your appointment {ctx}is scheduled for {appt or 'tomorrow'}. Does that work?"

    def _build_closing_message(self) -> str:
        b = self.memory.booking
        cs = self.memory.call_state
        lang = self.state.language
        name = cs.customer_name or ""
        appt = b.appointment_time or ""
        company = self._def.identity.company_name
        if lang == "hi":
            return (
                f"बहुत अच्छा{', ' + name if name else ''}! "
                f"आपका अपॉइंटमेंट {appt} के लिए कन्फर्म हो गया है। "
                f"हम आपको एक कन्फर्मेशन मैसेज भेजेंगे। {company} के लिए धन्यवाद!"
            )
        elif lang == "gu":
            return (
                f"ખૂબ સરસ{', ' + name if name else ''}! "
                f"તમારી {appt} ની મુલાકાત કન્ફર્મ થઈ ગઈ છે. "
                f"અમે તમને કન્ફર્મેશન મેસેજ મોકલીશું. {company} માટે આભાર!"
            )
        return (
            f"Wonderful{', ' + name if name else ''}! "
            f"Your appointment is confirmed for {appt}. "
            f"We'll send you a confirmation message. Thank you for choosing {company}!"
        )

    def _get_proactive_prompt(self, stage: str) -> str:
        lang = self.state.language
        prompts = self._def.response_bank.proactive_prompts.get(stage, {})
        return prompts.get(lang, prompts.get("en", ""))

    # ------------------------------------------------------------------
    # LLM consumption with filler support
    # ------------------------------------------------------------------

    async def _consume_llm_with_filler(
        self,
        llm_queue: asyncio.Queue,
        llm_done: asyncio.Event,
        intent: str,
        nudge_back: bool = False,
    ):
        """Consume LLM queue with filler timeout.

        nudge_back is accepted for API compatibility but no longer appends
        a second utterance — the flow re-prompts naturally on the next turn.
        This prevents the agent from talking non-stop (LLM answer + nudge
        back-to-back without letting the user speak).
        """
        full_parts: list[str] = []

        first_chunk = None
        try:
            first_chunk = await asyncio.wait_for(
                llm_queue.get(), timeout=self._def.llm_config.filler_timeout_s,
            )
        except asyncio.TimeoutError:
            filler = self._pick_filler()
            self.state.filler_used = True
            yield EngineResult(text=filler, is_shortcut=True, intent="filler")
            first_chunk = await llm_queue.get()

        if first_chunk is not None:
            full_parts.append(first_chunk)
            yield EngineResult(text=first_chunk, is_shortcut=False, intent=intent)

            while True:
                chunk = await llm_queue.get()
                if chunk is None:
                    break
                full_parts.append(chunk)
                yield EngineResult(text=chunk, is_shortcut=False, intent=intent)

        if full_parts:
            full_text = " ".join(full_parts)
            self.memory.add_assistant(full_text, language=self.state.language)
            # Last-response memory (feature 3)
            self.last_agent_response = full_text
            self.last_agent_language = self.state.language
            # QA: detect which field this agent response is asking about
            self.memory.qa_tracker.observe_agent_turn(full_text, self.memory.turn_index)
            logger.info("[LAST-RESPONSE] Stored LLM response (lang=%s, len=%d)",
                        self.last_agent_language, len(full_text))

    def _build_flow_nudge(self) -> str | None:
        """Build a gentle nudge to return to the booking flow after a detour. Won't repeat the same nudge."""
        missing = self.memory.booking.next_missing_field()
        if not missing:
            return None

        stage_map = {
            "location": STAGE_DISCOVERY,
            "property_type": STAGE_DISCOVERY,
            "appointment": STAGE_INVITE_VISIT,
        }
        target_stage = stage_map.get(missing)
        if not target_stage:
            return None

        if self._last_nudge_stage == target_stage:
            return None

        self._last_nudge_stage = target_stage
        return self._get_proactive_prompt(target_stage)

    # ------------------------------------------------------------------
    # Parallel LLM helpers
    # ------------------------------------------------------------------

    async def _run_llm_to_queue(
        self, text: str, queue: asyncio.Queue, done: asyncio.Event,
    ) -> None:
        try:
            async for chunk in self._call_llm_stream(text):
                await queue.put(chunk)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[LLM] Background stream failed")
            fallback = self._def.response_bank.lang_fallbacks.get(self.state.language, self._def.response_bank.response_fallback)
            try:
                queue.put_nowait(fallback)
            except asyncio.QueueFull:
                pass
        finally:
            await queue.put(None)
            done.set()

    async def _call_llm_stream(self, text: str):
        client = self._get_client()

        # RAG: retrieve relevant document chunks for this turn (non-blocking try/except
        # so a missing documents module or API error never interrupts the conversation)
        self._rag_context = ""
        try:
            from ..documents.store import get_folder_by_agent_id, has_documents
            _agent_folder = get_folder_by_agent_id(self._def.agent_id)
            if _agent_folder and has_documents(_agent_folder):
                from ..documents.embeddings import retrieve_context
                self._rag_context = retrieve_context(_agent_folder, text)
        except Exception:
            pass

        system = self._build_system_prompt()
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        messages.extend(self.memory.get_history_for_llm())

        # Final language enforcement — closest to generation for maximum effect
        lang_names = {"hi": "Hindi", "gu": "Gujarati", "en": "English"}
        lang_name = lang_names.get(self.state.language, "Hindi")
        _lang_script_note = {
            "hi": "Use Hindi (Devanagari script preferred, or romanized Hindi). NOT Gujarati, NOT English.",
            "gu": "Use Gujarati (Gujarati script preferred, or romanized Gujarati). NOT Hindi, NOT English.",
            "en": "Use English only. NOT Hindi, NOT Gujarati.",
        }
        if getattr(self._def.languages, "allow_code_mixing", False) and self.state.language != "en":
            # Hinglish mode — the opposite of pure-language enforcement. Encourage
            # natural code-mixing so the agent sounds like a real person, not a
            # textbook. Keep the base script (e.g. Devanagari for Hindi) so TTS
            # renders it correctly, but let common English words flow through.
            _final = (
                f"FINAL INSTRUCTION: Reply in natural HINGLISH. Write the {lang_name} words in "
                f"their native script (Devanagari for Hindi) and keep everyday English words in "
                f"English/Latin letters — e.g. 'नमस्ते! मैं Ananya बात कर रही हूँ, Mr. Ben आपसे "
                f"एक quick meeting करना चाहते हैं।'. Do NOT romanize the Hindi (never write 'main "
                f"aapse baat'), and do NOT translate common English words into pure {lang_name}. "
                f"Mix in the English words Indians actually say (meeting, time, business, company, "
                f"help, sure, ok, actually, AI, schedule). Keep it to 1 short, warm spoken sentence."
            )
        else:
            _final = (
                f"FINAL INSTRUCTION: Your ENTIRE response must be in {lang_name}. "
                f"{_lang_script_note.get(self.state.language, '')} "
                f"Do NOT mix languages. Do NOT switch mid-sentence."
            )
        messages.append({"role": "system", "content": _final})

        _SENTENCE_END = re.compile(r'[.!?।]\s')
        # Clause boundary (comma/colon/semicolon too) — used ONLY for the first
        # emission so audio starts at the model's first natural pause instead of
        # waiting for a whole sentence. Cuts first-audio latency noticeably.
        _CLAUSE_END = re.compile(r'[,;:।.!?]\s')
        _first_emitted = False
        deadline = time.monotonic() + self._def.llm_config.stream_timeout_s

        try:
            # Primary LLM, with automatic fallback: Groq's free tier has a
            # shared tokens-per-day cap — when the primary fails (429/quota/
            # auth/network), retry ONCE on the fallback provider instead of
            # dropping the caller to a canned "please repeat" line.
            try:
                stream = await client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    max_tokens=self._def.llm_config.max_tokens,
                    temperature=0.7,
                    stream=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as _primary_exc:
                fb = self._get_fallback_client()
                if fb is None:
                    raise
                logger.warning(
                    "[LLM] Primary (%s) failed: %s — falling back to %s",
                    self._model, _primary_exc, self._fallback_model,
                )
                stream = await fb.chat.completions.create(
                    model=self._fallback_model,
                    messages=messages,
                    max_tokens=self._def.llm_config.max_tokens,
                    temperature=0.7,
                    stream=True,
                )

            buffer = ""
            timed_out = False
            async for chunk in stream:
                if time.monotonic() > deadline:
                    logger.warning("[LLM-TIMEOUT] fallback triggered after %.0fs",
                                   self._def.llm_config.stream_timeout_s)
                    timed_out = True
                    break
                delta = chunk.choices[0].delta
                if delta.content:
                    buffer += delta.content
                    while True:
                        # First chunk: break at the earliest clause pause (>=4
                        # words) to start speaking fast. After that: full
                        # sentences, for natural delivery.
                        pat = _SENTENCE_END if _first_emitted else _CLAUSE_END
                        match = pat.search(buffer)
                        if not match:
                            break
                        sentence = buffer[:match.end()].strip()
                        if not _first_emitted and len(sentence.split()) < 4:
                            break  # too short to ship alone — wait for more
                        buffer = buffer[match.end():]
                        if sentence:
                            yield _validate_response(sentence, self.state.language)
                            _first_emitted = True

            if timed_out:
                if buffer.strip():
                    yield _validate_response(buffer.strip(), self.state.language)
                else:
                    yield self._def.response_bank.timeout_fallbacks.get(
                        self.state.language, self._def.response_bank.timeout_fallbacks["en"]
                    )
            elif buffer.strip():
                yield _validate_response(buffer.strip(), self.state.language)

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("[LLM] Stream failed: %s", exc)
            yield self._def.response_bank.lang_fallbacks.get(self.state.language, self._def.response_bank.response_fallback)

    # ------------------------------------------------------------------
    # System prompt — Pramukh Group domain
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:  # noqa: C901
        b = self.memory.booking
        cs = self.memory.call_state
        lang = self.state.language
        lang_name = {"hi": "Hindi", "gu": "Gujarati", "en": "English"}.get(lang, "English")

        # ── LANGUAGE LOCK — put this FIRST so LLM sees it immediately ────────
        _register_notes = self._def.languages.register_notes
        _supported_langs = set(self._def.languages.supported)

        # Build lang_header only for languages this agent supports.
        # Using .get() so missing entries get a plain lock line instead of KeyError.
        def _make_lang_header(code: str, name: str) -> str:
            note = _register_notes.get(code, "")
            other_langs = ", ".join(c for c in _supported_langs if c != code)
            note_line = f"\n{note}" if note else ""
            return (
                f"⚡ LANGUAGE = {name.upper()}. You MUST write ONLY in {name}.\n"
                f"Every single word must be {name}."
                + (f" Not {other_langs}." if other_langs else "")
                + f" No full sentences in any other language.\n"
                f"Even if previous responses were in another language — respond in {name.upper()} NOW."
                + note_line
            )

        lang_header = {code: _make_lang_header(code, {"hi": "Hindi", "en": "English", "gu": "Gujarati",
                       "mr": "Marathi", "ta": "Tamil", "te": "Telugu"}.get(code, code.upper()))
                       for code in _supported_langs}
        # Ensure the 'en' fallback always exists for .get() below
        if "en" not in lang_header:
            lang_header["en"] = _make_lang_header("en", "English")

        # ── Stage-specific goal ──────────────────────────────────────────────
        _stage_goals = self._def.call_flow.stage_goals
        stage_goal = _stage_goals.get(self.state.stage, next(iter(_stage_goals.values()), ""))

        # ── Collected info ───────────────────────────────────────────────────
        # Use confirmed name only; pending name gets its own confirmation block above
        caller_name = b.user_name or (self.memory.context.user_name if not self._name_pending else None)
        collected_lines = []
        if caller_name:
            collected_lines.append(f"Caller name: {caller_name}")
        if self._name_pending:
            collected_lines.append(f"Name just given (UNCONFIRMED): {self._name_pending!r}")
        if self.state.purpose:
            collected_lines.append(f"Purpose: {self.state.purpose}")

        if self.state.project_recommended:
            collected_lines.append(f"Project discussed: {self.state.project_recommended}")
        if b.appointment_time:
            collected_lines.append(f"Visit scheduled: {b.appointment_time}")
        collected_str = "\n".join(f"  · {c}" for c in collected_lines) or "  · Nothing collected yet"

        # ── Qualification status (for DISCOVERY stage guidance) ───────────────
        _qname = b.user_name or cs.customer_name


        # Name-capture confirmation instruction (feature 4)
        name_confirm_block: list[str] = []
        if self._name_pending:
            name_confirm_block = [
                "",
                f"⚡ NAME CONFIRMATION REQUIRED: Caller just provided their name as {self._name_pending!r}.",
                f"Your VERY NEXT response MUST confirm it: 'Your name is {self._name_pending}, right?' — nothing else first.",
            ]

        # ── Persistent CallState block ────────────────────────────────────────
        # Build "already collected — do not re-ask" list
        # Issues 7 & 8: include ALL collected fields so the LLM never re-asks.
        dont_reask: list[str] = []
        if cs.customer_name:
            dont_reask.append(f"caller's name ({cs.customer_name})")
        if cs.customer_phone:
            dont_reask.append("caller's phone number (stored; do not repeat back)")

        _pers = getattr(self._def, "personality", None)
        _pronouns = (_pers.pronouns if _pers else None) or getattr(self._def.identity, "pronouns", "she")
        _is_female = _pronouns.lower() == "she"
        _pronoun_instruction = (
            "Your pronouns are she/her. Use feminine agreement in every language. "
            "In Hindi: ALWAYS use chahti, sakti, aayi, gayi, karungi, bataungi, samjhi, rahi — "
            "NEVER chahta, sakta, aaya, gaya, karunga, batauga, samjha, raha. "
            "In Gujarati: use feminine verb forms consistently."
            if _is_female else
            "Your pronouns are he/him. Use masculine agreement in every language. "
            "In Hindi: ALWAYS use chahta, sakta, aaya, gaya, karunga, batauga, samjha, raha — "
            "NEVER chahti, sakti, aayi, gayi, karungi, bataungi, samjhi, rahi. "
            "In Gujarati: use masculine verb forms consistently."
        )

        # Personality / speaking style block
        _personality_lines: list[str] = []
        if _pers:
            _energy_map = {
                "low": "Calm and measured. Speak slowly and thoughtfully. Never rush.",
                "medium": "Balanced energy — warm and engaged without being overpowering.",
                "high": "High energy and enthusiasm. Be upbeat, punchy, and genuinely excited. Short sentences. Positive tone.",
            }
            _tone_map = {
                "professional": "Maintain a polished, formal tone. Precise language. No slang.",
                "conversational": "Conversational and natural. Speak like a knowledgeable friend, not a corporate bot.",
                "friendly": "Warm, friendly, and encouraging. Use light informal language. Make the caller feel comfortable.",
                "formal": "Strictly formal. No contractions. Deferential and structured.",
            }
            _style_map = {
                "concise": "1-2 sentences maximum per turn. Be direct.",
                "natural": "2-3 sentences per turn. Natural conversational flow.",
                "detailed": "3-4 sentences. Give context and explanation where useful.",
            }
            _personality_lines = [
                "",
                "━━ PERSONALITY ━━",
                f"Energy: {_energy_map.get(_pers.energy, _energy_map['medium'])}",
                f"Tone: {_tone_map.get(_pers.tone, _tone_map['conversational'])}",
                f"Response length: {_style_map.get(_pers.style, _style_map['natural'])}",
            ]
            if _pers.lingo:
                _personality_lines.append(f"Vocabulary: {_pers.lingo}")
            if _pers.custom_instructions:
                _personality_lines.append(f"Persona: {_pers.custom_instructions}")

        # ── Call direction — inbound vs outbound demand opposite approaches ──
        _direction = (self.call_direction or "inbound").lower()
        _direction_lines: list[str] = ["", ]
        if _direction == "outbound":
            _direction_lines += [
                "━━ CALL TYPE: OUTBOUND — YOU PLACED THIS CALL ━━",
                "You dialed this person; they did not call you. That changes everything about your approach:",
                "▸ YOU LEAD THE CALL. Never wait for the other person to drive — you always know the next step and steer toward it.",
                "▸ State who you are and why you're calling within the first two turns. Never leave them guessing.",
                "▸ Your purpose: connect with them, pitch the relevant product/service, and resolve any queries they had requested answers for.",
                "▸ Respect their time — they didn't plan this call. Be warm but purposeful; every turn moves toward your goal.",
                "▸ If they're busy, offer to keep it to one minute or ask for a better time — do NOT just hang up.",
                "▸ Handle 'not interested' gracefully once, with one strong value point — then respect a second no.",
            ]
            if self.outbound_contact_name:
                _contact_phone_note = (
                    " (number stored internally — never read it out unless they ask you to confirm it)"
                    if self.outbound_contact_phone else ""
                )
                _direction_lines += [
                    "",
                    f"▸ CONTACT: You are calling {self.outbound_contact_name}{_contact_phone_note}.",
                    f"▸ Early in the call, confirm you're speaking with the right person: e.g. 'Am I speaking with {self.outbound_contact_name}?'",
                    f"▸ If it IS them, use their name naturally (not every turn). If it's someone else, politely ask if {self.outbound_contact_name} is available.",
                    "▸ Do NOT re-ask for their name — you already have it.",
                ]
        else:
            _direction_lines += [
                "━━ CALL TYPE: INBOUND — THE CALLER CAME TO YOU ━━",
                "They dialed in with a reason: a question, an issue, a request, or interest in a product/service.",
                "▸ LISTEN FIRST. Find out why they called before pitching anything.",
                "▸ Your purpose: accept and resolve their issues, requests and demands; answer questions accurately; schedule appointments/visits when they're interested.",
                "▸ Make them feel welcomed and heard — they chose to reach out; reward that.",
                "▸ Once their need is clear, guide them decisively to the resolution or the booking — don't leave them hanging.",
            ]

        # ── Calendar availability (live — excludes already-booked slots) ──────
        _cal_lines: list[str] = []
        if self._def.calendar.mock_slots:
            _free = [] if b.appointment_time else self._available_calendar_slots()
            if b.appointment_time:
                pass  # already scheduled — surfaced in CALL STATE above
            elif _free:
                _slot_list = ", ".join(f"{s.day} at {s.time}" for s in _free)
                _cal_lines = [
                    "",
                    "━━ APPOINTMENT CALENDAR (LIVE AVAILABILITY) ━━",
                    f"Free slots right now: {_slot_list}",
                    "▸ Offer ONLY slots from this list — never invent other times.",
                    "▸ Offer at most TWO options at a time.",
                    "▸ When the caller asks what times are available, mention free slots from this list.",
                    "▸ Once the caller picks a slot, confirm it back clearly — it will be reserved for them.",
                ]
            else:
                _cal_lines = [
                    "",
                    "━━ APPOINTMENT CALENDAR ━━",
                    "All published slots are currently booked. Take the caller's preferred day/time as a request and say the team will call back to confirm — do NOT promise a specific slot.",
                ]

        parts = [
            # Language lock FIRST — highest priority
            lang_header.get(lang, lang_header["en"]),
            *name_confirm_block,
            "",
            "━━ AGENT IDENTITY ━━",
            f"You are {self._def.identity.agent_name} — a warm, genuine, experienced {self._def.identity.role_description} for {self._def.identity.company_name}.",
            "You are NOT reading a script. You are a real person having a real conversation.",
            "Your job: make every caller feel genuinely heard and understood.",
            _pronoun_instruction,
            *_direction_lines,
            "",
            "━━ BRAND ━━",
            self._def.identity.company_tagline,
            f"{' · '.join(self._def.knowledge_base.locations)} · Contact: {self._def.identity.contact}",
            "",
            "━━ CALL STATE ━━",
            f"Stage: {self.state.stage}  |  Language: {lang_name}  |  Turn: {self.state.turn_count}",
            *([f"  · Visit scheduled: {b.appointment_time}"] if b.appointment_time else []),
            *(["  · Phone: captured (stored internally — never repeat it back)"]
              if cs.customer_phone else []),
            "",
            # THE single authoritative state block: filled/missing slots,
            # audit-backed values, rolling summary, and the one next question.
            # Replaces the old CONFIRMED FIELDS / ALREADY COLLECTED / Known-so-far
            # trio that used to overlap and occasionally contradict each other.
            self.state_mgr.build_prompt_context(
                self.state.language,
                # Discovery-style prompting for the legacy ids AND for the
                # first two stages of custom-id flows (position-based —
                # custom ids like CONFIRM_PERSON/OPENING are never in the
                # legacy tuple, which silently disabled the strong ask-next-
                # missing-slot directive for builder-created agents).
                stage_discovery=(
                    self.state.stage in (STAGE_GREETING, STAGE_DISCOVERY)
                    or next((i for i, s in enumerate(self._def.call_flow.stages)
                             if s.id == self.state.stage), 0) <= 1
                ),
            ),
            *([
                "",
                "━━ ACTIVE CONTEXT ━━",
                f"The caller is interested in: {cs.domain_fields['matched_entity']}.",
                f"Answer specific questions in the context of: {cs.domain_fields['matched_entity']}.",
                f"DO NOT ask which one they want — it is: {cs.domain_fields['matched_entity']}.",
            ] if "matched_entity" in cs.domain_fields else []),
            "",
            "━━ CURRENT GOAL ━━",
            stage_goal,
            *_cal_lines,
            "",
            f"━━ {self._def.knowledge_base.portfolio_section_label} ━━",
            self._def.knowledge_base.portfolio_prompt_text,
            "",
            "━━ CONVERSATION GUIDELINES ━━",
            "LISTEN + QUALIFY: Acknowledge what the caller said, THEN always move the qualification forward.",
            "Every turn must do two things: (1) respond to what was said, (2) ask the next missing field.",
            "If they asked a question — ANSWER IT, then smoothly return to qualification.",
            "SALES-DRIVEN: You are not a chatbot waiting for direction. You are a sales consultant guiding a conversation toward a booking.",
            "PACE: Speak at the caller's pace. One question at a time. Never stack questions.",
            "3 sentences max per response. Phone call — brevity is warmth.",
            "Mirror their energy: excited → warm and enthusiastic, cautious → softer and reassuring.",
            "NEVER sound like an IVR, chatbot, or scripted call center agent.",
            *_personality_lines,
            "",
            "━━ NATURAL CONVERSATION STYLE (CRITICAL) ━━",
            "",
            "▸ ACKNOWLEDGMENT — weave a brief natural acknowledgment INTO the flow of your response.",
            "  Do NOT bolt a standalone acknowledgment word onto the front of every sentence.",
            "  A filler phrase (e.g. 'One moment please...', 'Haanji!') may already have been",
            "  played to the caller before your response arrives. If so, skip any opening",
            "  acknowledgment and go straight to your answer.",
            "  When no filler was played, a single woven-in word is enough:",
            "    ✓ 'Right, so a 2BHK in Ahmedabad — let me see...'",
            "    ✓ 'Sure, we do cover Gujarat...'",
            "    ✓ 'Yeah, that's a good point actually...'",
            "    ✗ 'Got it! Absolutely! So...' — never stack acknowledgments.",
            "  Vary the word every turn. Never use the same one twice in a row.",
            "",
            "▸ DO NOT ECHO THE CALLER verbatim. Never say 'I understand you're looking for a 2BHK in Ahmedabad.'",
            "  Instead, absorb it and move naturally: 'Okay so a 2BHK in Ahmedabad — let me see...'",
            "  or 'Right, Ahmedabad — good choice actually.' Paraphrase lightly, then continue.",
            "",
            "▸ USE CONTRACTIONS always. Write as you'd speak:",
            "  'I'll' not 'I will', 'I'm' not 'I am', 'we've' not 'we have',",
            "  'it's' not 'it is', 'don't' not 'do not', 'can't' not 'cannot'.",
            "  BANNED phrases: 'I would be happy to assist', 'Thank you for sharing that information',",
            "  'I appreciate your patience', 'I understand your concern', 'As per your requirement'.",
            "",
            "▸ CONVERSATIONAL REPAIR — when correcting yourself or redirecting, sound human:",
            "  Use: 'Actually, let me rephrase that—', 'Sorry, what I mean is—',",
            "       'Hmm, let me put that differently—', 'Right, so actually—'",
            "  NOT: 'I apologize for the confusion, let me clarify', 'Let me correct myself'.",
            "",
            "━━ ANTI-REPETITION (CRITICAL) ━━",
            "Look at the conversation history. If you already asked something, DO NOT ask it again.",
            "If you already introduced yourself or the company, don't repeat that intro.",
            "If you already mentioned a project, don't introduce it as if for the first time.",
            "If the last 2-3 responses look similar, CHANGE your approach entirely.",
            "Never say the same phrase twice in a conversation.",
            "",
            "━━ OBJECTION HANDLING ━━",
            "'Send brochure' → agree to send AND invite visit — photos tell only part of the story.",
            "'Price too high' → ask their comfortable range, suggest lower options.",
            "'Need to ask family' → invite the whole family to visit together.",
            "'Comparing builders' → smart — but see ours first. 30 years, zero stalls, RERA.",
            "",
            "━━ FIRM RULES ━━",
            "✗ NEVER use hollow filler phrases: 'खुशी हुई', 'आपका जवाब सुनकर खुशी हुई', 'Happy to help', "
            "'It was a pleasure', 'आपकी बात सुनकर', 'ખુશી થઈ', 'Glad to hear'. "
            "Every sentence must either gather missing info, handle an objection, recommend a project, or push toward a site visit.",
            "✗ NEVER volunteer pricing, rates, payment plans, ROI, inventory levels, or discounts unprompted — only discuss these when the caller explicitly asks",
            "✗ NEVER repeat the caller's phone number back to them unless they explicitly ask you to confirm it",
            "✗ NEVER ask for information that is already marked ✓ in CONVERSATION STATE",
            "✗ NEVER give more than 2 time options for site visit",
            "✗ NEVER dump the whole portfolio on one caller",
            "✗ NEVER make up prices or dates not listed above",
            "✗ NEVER say 'When can you come?' — always give exactly TWO specific time options",
            "✗ NEVER ask 'Do you have any questions?' or 'Any questions about the property?' — only answer questions when the caller asks one",
            "✗ NEVER push the conversation to a new topic if the caller is still on the current one",
            "✗ NEVER give property pitches until the caller has expressed genuine interest",
            "✗ NEVER interrupt a caller's train of thought with an unrelated topic",
            "✓ Use caller's name naturally — at most once every 4-5 turns. Do NOT open every response with their name.",
            "✓ No bullet points, lists, markdown, or emojis in your spoken responses",
            "✓ Only say things that are a direct, natural response to what the caller just said",
            "✓ Use contractions: I'll, I'm, we've, it's, don't, can't — always",
            "✓ Open each turn with a varied natural acknowledgment (Got it / Sure / Right / Absolutely — never twice in a row)",
        ]

        # QA tracker block — second layer of anti-repetition enforcement
        qa_block = self.memory.qa_tracker.to_prompt_block()
        if qa_block:
            parts.append("")
            parts.append(qa_block)

        context_str = self.memory.context.to_prompt_string()
        if context_str:
            parts.append(f"\nKnown context:\n{context_str}")

        if self._rag_context:
            parts.append("")
            parts.append(self._rag_context)

        return "\n".join(parts)


    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    async def detect_language_v3(self, text: str) -> tuple[str, float, str]:
        text_lower = text.lower().strip()
        if not text_lower:
            return "en", 1.0, "default"

        if text_lower in self._lang_cache:
            return self._lang_cache[text_lower]

        if any("઀" <= c <= "૿" for c in text_lower):
            result = ("gu", 1.0, "script")
        elif any("ऀ" <= c <= "ॿ" for c in text_lower):
            # Devanagari detected — but STT often writes Gujarati in
            # Devanagari. Check for Gujarati marker words in Devanagari.
            if self._has_gujarati_markers_devanagari(text_lower):
                result = ("gu", 0.9, "script_gu_markers")
            else:
                result = ("hi", 1.0, "script")
        else:
            hi_score = _pattern_score(text_lower, _HINDI_PATTERNS)
            gu_score = _pattern_score(text_lower, _GUJARATI_PATTERNS)
            total = hi_score + gu_score

            if total > 0:
                confidence = abs(hi_score - gu_score) / total
                lang = "hi" if hi_score >= gu_score else "gu"
                result = (lang, max(confidence, 0.5), "pattern")
            elif text_lower.isascii():
                # ASCII text with zero Hindi/Gujarati pattern hits is overwhelmingly
                # English. Defaulting to "hi" here forced English speakers into
                # Devanagari STT on the next turn.
                result = ("en", 0.5, "default")
            else:
                lang = await self._classify_language_llm(text_lower)
                result = (lang, 0.9, "llm")

        # Hard guard: only the three supported languages may be returned.
        # If the LLM fallback returned something unexpected (or a new script
        # leaked through), default to Hindi rather than propagating a
        # language code the agent has no responses for.
        if result[0] not in ("en", "hi", "gu"):
            logger.warning(
                "[LANG] detect_language_v3 returned unsupported lang=%r for %r — "
                "overriding to 'hi'",
                result[0], text[:60],
            )
            result = ("hi", 0.3, "guard")

        self._lang_cache[text_lower] = result
        return result

    @staticmethod
    def _has_gujarati_markers_devanagari(text: str) -> bool:
        """Detect Gujarati words written in Devanagari by STT.

        Two-tier detection:
        - Strong copulas (छे/छो/छु/केम/तमे/नथी/हसे/जोइए): exclusively Gujarati,
          never appear as word-stems in Hindi. ONE hit is sufficient.
          Word-boundary matching prevents false matches inside Hindi words like छोटा.
        - Weak markers: ambiguous — require 2+ hits for confidence.

        This fixes the regression where short Gujarati greetings like "kem cho"
        get transcribed as "गेम छो" (Devanagari via hi-IN STT), producing only
        one marker hit ("छो") that the old >= 2 threshold would reject.
        """
        # Strong tier: one word-boundary match is enough
        if _STRONG_GU_DEVA_RE.search(text):
            return True

        # Weak tier: ambiguous markers, need 2+ hits
        markers = [
            "जोईए", "जोइये",
            "माने", "तमने", "अमने", "अमारा", "तमारा",
            "बरोबर", "सारु", "सारुं", "माजामा",
            "केमछो", "केम छो",
            "शुं", "क्यां",
            "आवजो",
            "हतु", "हती",
            "अत्यारे",
            "जोवा", "जोई", "जोवुं",
            "बतावो", "जणावो",
        ]
        hits = sum(1 for m in markers if m in text)
        return hits >= 2

    async def _classify_language_llm(self, text: str) -> str:
        try:
            client = self._get_client()
            response = await client.chat.completions.create(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": _LANG_CLASSIFY_PROMPT.format(text=text),
                }],
                max_tokens=5,
                temperature=0.0,
            )
            raw = response.choices[0].message.content.strip().lower()
            return _LANG_NAME_TO_CODE.get(raw, "en")
        except Exception as exc:
            logger.warning("[LANG] LLM classification failed: %s", exc)
            return "en"

    # ------------------------------------------------------------------
    # Objection + filler helpers
    # ------------------------------------------------------------------

    def _extract_requested_language(self, text: str) -> str:
        lower = text.lower()

        # Identify negated languages up front so they don't leak into
        # instead/directive/last-position resolution. "I can't speak Hindi"
        # contains "speak hindi" which would otherwise match the directive
        # regex and route to Hindi — the opposite of what the user wants.
        _lang_alt = (
            r"english|hindi|gujarati|inglish|angrezi|"
            r"इंग्लिश|अंग्रेज़ी|अंग्रेजी|हिंदी|हिन्दी|गुजराती|"
            r"ગુજરાતી|અંગ્રેજી|હિન્દી|ઇંગ્લિશ"
        )
        _neg_after = r"(?:नहीं|नहि|नही|न्ही|નથી|નહિ|નહી|નહીં|nahi|nahin|nai)"
        _neg_before_en = r"(?:don'?t|do not|can'?t|cannot)\s+(?:know|speak|talk|understand)?"
        _name_to_code = {
            "english": "en", "angrezi": "en", "inglish": "en",
            "hindi": "hi", "gujarati": "gu",
            "इंग्लिश": "en", "अंग्रेज़ी": "en", "अंग्रेजी": "en",
            "ઇંગ્લિશ": "en", "અંગ્રેજી": "en",
            "हिंदी": "hi", "हिन्दी": "hi", "હિન્દી": "hi",
            "ગુજરાતી": "gu", "गुजराती": "gu",
        }
        negated: set[str] = set()
        for pat in (
            rf"({_lang_alt})\s{{0,3}}{_neg_after}",
            rf"{_neg_before_en}\s+({_lang_alt})",
        ):
            for m in re.finditer(pat, text, re.IGNORECASE):
                key = m.group(1)
                code = _name_to_code.get(key) or _name_to_code.get(key.lower())
                if code:
                    negated.add(code)

        # "X instead of Y" / "X not Y" / "X rather than Y" — the language
        # BEFORE the marker is the preferred one. Check this first because the
        # generic directive regex below would otherwise match "of Y" / "to Y".
        instead_match = re.search(
            r"(english|hindi|gujarati|angrezi|inglish)\s+(?:instead\s+of|not|rather\s+than|over|than)\s+(english|hindi|gujarati|angrezi|inglish)?",
            lower,
        )
        if instead_match:
            preferred = instead_match.group(1)
            code = {
                "english": "en", "angrezi": "en", "inglish": "en",
                "hindi": "hi", "gujarati": "gu",
            }[preferred]
            if code not in negated:
                return code

        # Prefer language names that follow a directive word — the destination
        # of "speak in X" / "switch to X" / "talk X" is what the user wants.
        directive_match = re.search(
            r"(?:speak|talk|switch|continue|reply|use|change|in|to|me|mai|mein|ma|mei)\s+(?:in\s+|to\s+)?(english|hindi|gujarati|angrezi|inglish)",
            lower,
        )
        if directive_match:
            target = directive_match.group(1)
            code = {
                "english": "en", "angrezi": "en", "inglish": "en",
                "hindi": "hi", "gujarati": "gu",
            }[target]
            if code not in negated:
                return code

        # Fallback: pick the LAST language name mentioned. Handles cases like
        # "switch from hindi to english" → returns en correctly.
        positions: list[tuple[int, str]] = []
        for word, code in [
            ("english", "en"), ("angrezi", "en"), ("inglish", "en"),
            ("hindi", "hi"), ("gujarati", "gu"),
        ]:
            idx = lower.rfind(word)
            if idx >= 0:
                positions.append((idx, code))
        for word, code in [
            ("इंग्लिश", "en"), ("अंग्रेज़ी", "en"), ("अंग्रेजी", "en"),
            ("અંગ્રેજી", "en"), ("ઇંગ્લિશ", "en"),
            ("हिंदी", "hi"), ("हिन्दी", "hi"), ("હિન્દી", "hi"),
            ("ગુજરાતી", "gu"), ("गुजराती", "gu"),
        ]:
            idx = text.rfind(word)
            if idx >= 0:
                positions.append((idx, code))
        if positions:
            positions.sort(reverse=True)
            for _, code in positions:
                if code not in negated:
                    return code
            # All mentioned languages were negated — keep current language
            # if it's not the negated one; otherwise fall back to script.
            if self.state.language not in negated:
                return self.state.language
            if any("઀" <= ch <= "૿" for ch in text):
                return "gu"
            if any("ऀ" <= ch <= "ॿ" for ch in text):
                return "hi"
            return "en"
        return self.state.language

    def _pick_objection_response(self) -> str:
        lang = self.state.language
        bank = self._def.response_bank.objection_responses
        responses = bank.get(lang, bank["en"])
        resp = responses[self._response_counter % len(responses)]
        self._response_counter += 1
        return resp

    def _pick_filler(self) -> str:
        """Return one filler phrase, chosen by input type.

        Two tiers:
        - APPROVAL filler  — for simple affirmations ("yes", "ok", "haan").
          Signals agreement only, no "I'm looking something up" implication.
          Examples: "Sure!", "Haanji!", "Of course!"
        - THINKING filler  — for information requests / complex questions.
          Signals the agent is retrieving or reasoning.
          Examples: "One moment please...", "Ek minute...", "Batati hoon..."

        The YAML bank (response_bank.filler_responses) provides thinking fillers
        for agents that want branded phrasing. The approval pool is always
        code-driven so it stays short and varied across all agents.
        """
        lang = self.state.language

        # ── Classify the caller's last turn ──────────────────────────────────
        last_user = ""
        for t in reversed(self.memory.turns):
            if t.role == "user":
                last_user = t.text.lower().strip()
                break

        _ACK_TOKENS = {
            # English affirmations
            "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "fine",
            "good", "alright", "right", "agreed", "please", "go ahead",
            "of course", "sounds good", "great", "perfect", "wonderful",
            # Hindi affirmations
            "haan", "ha", "haa", "ji", "haanji", "bilkul", "zaroor",
            "theek", "sahi", "accha", "thik", "wah", "haan ji",
            # Gujarati affirmations
            "ha", "haa", "ji", "bilkul", "zaroor", "saru", "thik chhe",
        }
        clean_words = set(re.sub(r"[^\w\s]", "", last_user).split())
        is_simple_ack = (
            len(clean_words) <= 6
            and bool(clean_words & _ACK_TOKENS)
            and "?" not in last_user
        )

        # ── Approval pool — short, instant, no checking implication ──────────
        _APPROVAL: dict[str, list[str]] = {
            "en": ["Sure!", "Of course!", "Absolutely!", "Okay!", "Right, sure."],
            "hi": ["Haanji!", "Zaroor!", "Bilkul!", "Ji haan!", "Haan, bilkul!"],
            "gu": ["Haanji!", "Zaroor!", "Bilkul!", "Ji!", "Haan!"],
        }

        # ── Thinking pool — implies retrieving / reasoning ───────────────────
        # Use the YAML bank when the agent has branded thinking phrases;
        # fall back to the built-in pool otherwise.
        bank = self._def.response_bank.filler_responses
        yaml_thinking = bank.get(lang, bank.get("en", []))
        _THINKING_BUILTIN: dict[str, list[str]] = {
            "en": [
                "One moment please...",
                "Let me check...",
                "Sure, one second...",
                "Let me look into that...",
            ],
            "hi": [
                "Ek minute...",
                "Batati hoon...",
                "Ek second, dekh leti hoon...",
                "Haan, check karti hoon...",
            ],
            "gu": [
                "Ek minute...",
                "Joi leti hoon...",
                "Ek second...",
                "Jova dyo...",
            ],
        }

        if is_simple_ack:
            pool = _APPROVAL.get(lang, _APPROVAL["en"])
        elif yaml_thinking:
            pool = yaml_thinking
        else:
            pool = _THINKING_BUILTIN.get(lang, _THINKING_BUILTIN["en"])

        resp = pool[self._filler_counter % len(pool)]
        self._filler_counter += 1
        return resp

    # ------------------------------------------------------------------
    # Last-response memory + repeat detection (feature 3)
    # ------------------------------------------------------------------

    def _store_last_response(self, text: str) -> None:
        """Cache the most recent agent utterance for repeat/translate requests."""
        self.last_agent_response = text
        self.last_agent_language = self.state.language
        self.memory.qa_tracker.observe_agent_turn(text, self.memory.turn_index)
        logger.debug("[LAST-RESPONSE] Stored shortcut response (lang=%s): %r",
                     self.last_agent_language, text[:60])

    def _is_repeat_request(self, text: str) -> bool:
        """Return True if the caller is asking to repeat the last agent response."""
        lower = text.lower().strip()
        return any(p in lower for p in _REPEAT_DETECT_PATTERNS)

    # ------------------------------------------------------------------
    # LLM warmup (feature 6)
    # ------------------------------------------------------------------

    async def warmup(self) -> None:
        """Warm up LLM API connection to reduce first-call latency.

        Runs at most once per process (class-level flag) — Issue 6.
        """
        if ConversationRouter._warmup_done_flag:
            logger.debug("[CONV] LLM warmup skipped — already done this process")
            return
        try:
            client = self._get_client()
            await client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                temperature=0.0,
            )
            ConversationRouter._warmup_done_flag = True
            logger.info("[CONV] LLM warmup complete (first time this process)")
        except Exception as exc:
            logger.warning("[CONV] LLM warmup failed: %s", exc)

    # ------------------------------------------------------------------
    # Localization helpers
    # ------------------------------------------------------------------

    def _localized(self, key: str, fallback: str) -> str:
        loc = self._def.response_bank.localized_responses.get(self.state.language, {})
        val = loc.get(key)
        if val is None:
            return fallback
        if isinstance(val, list):
            return val[self._response_counter % len(val)]
        return val

    def _localized_list(self, key: str, en_list: list[str]) -> str:
        loc = self._def.response_bank.localized_responses.get(self.state.language, {})
        val = loc.get(key)
        if isinstance(val, list):
            resp = val[self._response_counter % len(val)]
            self._response_counter += 1
            return resp
        resp = en_list[self._response_counter % len(en_list)]
        self._response_counter += 1
        return resp
