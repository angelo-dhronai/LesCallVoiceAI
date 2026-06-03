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

import asyncio
import os
import re
import time
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from .memory import ConversationMemory, BookingData, CallState
from .stt.post_processor import match_location, match_bhk

# ---------------------------------------------------------------------------
# Known Pramukh Group projects — used for project-context locking
# Keys are lowercase substrings; values are (canonical_name, location).
# Longer keys listed first so _extract_project_from_text() matches
# "aranya ii" before "aranya".
# ---------------------------------------------------------------------------
_KNOWN_PROJECTS: dict[str, tuple[str, str]] = {
    "central park":  ("Central Park",  "Surat"),
    "aranya ii":     ("Aranya II",     "Surat"),
    "aranya 2":      ("Aranya II",     "Surat"),
    "yogi woods":    ("Yogi Woods",    "Silvassa"),
    "revanta":       ("Revanta",       "Surat"),
    "vivanta":       ("Vivanta",       "Surat"),
    "satva":         ("Satva",         "Surat"),
    "agastya":       ("Agastya",       "Surat"),
    "aranya":        ("Aranya",        "Surat"),
    "aura":          ("Aura",          "Vapi"),
    "sahaj":         ("Sahaj",         "Vapi"),
    "vedanta":       ("Vedanta",       "Vapi"),
    "sangath":       ("Sangath",       "Silvassa"),
    "shivanta":      ("Shivanta",      "Silvassa"),
    "swagat":        ("Swagat",        "Silvassa"),
    "orbit":         ("Orbit",         "Surat"),
    # legacy / alias names that may appear in STT output
    "oak cove":      ("Oak Cove",      "Surat"),
}

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
FILLER_TIMEOUT_S = 2.0
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


# ---------------------------------------------------------------------------
# Available appointment slots (mock — replace with real calendar API)
# ---------------------------------------------------------------------------

_AVAILABLE_SLOTS = [
    {"day": "Tomorrow", "time": "10:00 AM"},
    {"day": "Tomorrow", "time": "2:00 PM"},
    {"day": "Day after tomorrow", "time": "11:00 AM"},
    {"day": "This Saturday", "time": "10:00 AM"},
    {"day": "This Saturday", "time": "3:00 PM"},
]

_SLOTS_EN = ", ".join(f"{s['day']} at {s['time']}" for s in _AVAILABLE_SLOTS[:3])
_SLOTS_HI = "कल सुबह 10 बजे, कल दोपहर 2 बजे, या परसों सुबह 11 बजे"
_SLOTS_GU = "કાલે સવારે 10 વાગે, કાલે બપોરે 2 વાગે, અથવા પરમ દિવસે સવારે 11 વાગે"


# ---------------------------------------------------------------------------
# Intent detection — domain-specific for Pramukh Group
# ---------------------------------------------------------------------------

_LOCATION_NAMES = {
    "surat", "vapi", "silvassa",
    "સુરત", "વાપી", "સિલવાસા",
    "सूरत", "वापी", "सिलवासा",
}

_OBJECTION_PATTERNS = [
    "not interested", "busy right now", "call later", "already using",
    "don't need", "no time", "don't want", "stop calling",
    "not looking", "no need", "wrong number", "remove my number",
    # "later" / "not now" — deferral is an objection, treat it kindly
    "talk later", "later please", "maybe later", "some other time",
    "another time", "not right now",
    "baad mein baat", "baad me baat", "baadme baat",
    "phir baat", "phir karte", "phir baat karenge",
    "abhi nahi", "abhi nai", "abhi busy",
    "fursat nahi",
]

_TIME_PATTERNS = [
    "morning", "afternoon", "evening", "tomorrow", "today",
    "this weekend", "saturday", "sunday", "next week",
    "10 am", "11 am", "2 pm", "3 pm",
    "subah", "dopahar", "shaam", "kal", "parso", "aaj",
    "savare", "bapore", "saanje", "kaale", "aaje",
    "सुबह", "दोपहर", "शाम", "कल", "परसों", "आज",
    "સવારે", "બપોરે", "સાંજે", "કાલે", "આજે",
]


def detect_intent(text: str, stage: str = STAGE_GREETING) -> str:
    """Classify user input into a domain intent. Fast and deterministic."""
    if not text or not text.strip():
        return "general"

    lower = text.lower().strip()

    # Objection — highest priority
    if any(p in lower for p in _OBJECTION_PATTERNS):
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
    ]
    if any(p in text for p in _lang_switch_devanagari):
        return "language_switch"
    # Gujarati script — comprehensive list including common colloquial forms
    if any(w in text for w in [
        "ગુજરાતીમાં", "ગુજરાતી માં", "અંગ્રેજીમાં", "હિન્દીમાં",
        "ઇંગ્લિશમાં", "ગુજરાતી બોલ", "ગુજ બોલ",
        "અંગ્રેજી બોલ", "હિન્દી બોલ", "ઇંગ્લિશ બોલ",
        "ગુજ માં", "ગુ માં",
    ]):
        return "language_switch"
    # Catch-all: any mention of a language name with "baat/bol/bolo" nearby
    if re.search(r"(?:english|hindi|gujarati|इंग्लिश|अंग्रेज़ी|अंग्रेजी|हिंदी|हिन्दी|गुजराती|ગુજરાતી|અંગ્રેજી|હિન્દી|ઇંગ્લિશ).{0,15}(?:बात|बोल|bol|baat|bolo|boliye|વાત|બોલ)", lower):
        return "language_switch"
    if re.search(r"(?:बात|बोल|bol|baat|bolo|boliye|વાત|બોલ).{0,15}(?:english|hindi|gujarati|इंग्लिश|अंग्रेज़ी|अंग्रेजी|हिंदी|हिन्दी|गुजराती|ગુજરાતી|અંગ્રેજી|હિન્દી|ઇંગ્લિશ)", lower):
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
    if any(w in lower for w in _LOCATION_NAMES):
        return "location_selection"

    # BHK / property type
    bhk = match_bhk(lower)
    if bhk:
        return "property_type"
    if re.search(r"\d\s*bhk|\d\s*bed", lower, re.IGNORECASE):
        return "property_type"

    # Time preference
    if any(p in lower for p in _TIME_PATTERNS):
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


# ---------------------------------------------------------------------------
# Objection responses (multilingual, Pramukh-specific)
# ---------------------------------------------------------------------------

_OBJECTION_RESPONSES: dict[str, list[str]] = {
    "en": [
        "Of course, I completely understand. Would another time work better for a quick call? Otherwise no problem at all.",
        "No problem at all — when would be a more convenient time? Just a brief chat.",
    ],
    "hi": [
        "जी बिल्कुल, मैं समझता हूँ। क्या किसी और समय एक छोटी सी बात हो सकती है? वरना कोई बात नहीं।",
        "कोई बात नहीं। आप बताइए कब सुविधाजनक रहेगा, बस एक छोटी सी बात करनी है।",
    ],
    "gu": [
        "જી બિલકુલ, હું સમજું છું. શું બીજા કોઈ સમયે થોડી વાત થઈ શકે? નહીંતર કોઈ વાત નહીં.",
        "કોઈ વાત નહીં. તમે જણાવો ક્યારે અનુકૂળ રહેશે, બસ થોડી વાત કરવી છે.",
    ],
}

# Used after a SECOND deferral/objection in the same call — we back off
# completely with a warm goodbye instead of pushing.
_DECLINE_FAREWELL_RESPONSES: dict[str, str] = {
    "en": "Of course, no problem at all. I won't take any more of your time — have a wonderful day, take care!",
    "hi": "जी बिल्कुल, कोई बात नहीं। मैं आपका और समय नहीं लूँगा। आपका दिन शुभ रहे, फिर मिलते हैं!",
    "gu": "જી બિલકુલ, કોઈ વાત નહીં. હું તમારો વધુ સમય નહીં લઉં. તમારો દિવસ શુભ રહે, ફરી મળીશું!",
}

# ---------------------------------------------------------------------------
# Filler responses (multilingual, Pramukh-specific)
# ---------------------------------------------------------------------------

_FILLER_RESPONSES: dict[str, list[str]] = {
    "en": ["One moment...", "Sure, let me check..."],
    "hi": ["एक सेकंड...", "बिल्कुल, बताता हूँ..."],
    "gu": ["એક સેકન્ડ...", "બિલકુલ, જણાવું છું..."],
}

# ---------------------------------------------------------------------------
# Timeout / error fallbacks
# ---------------------------------------------------------------------------

_TIMEOUT_FALLBACKS: dict[str, str] = {
    "en": "Let me get back to you on that.",
    "hi": "मैं इस पर वापस आता हूँ।",
    "gu": "હું આના પર પાછો આવું છું.",
}

_RESPONSE_FALLBACK = "Sorry, could you repeat that?"

# ---------------------------------------------------------------------------
# Language switch confirmation responses
# ---------------------------------------------------------------------------

_LANG_SWITCH_RESPONSES: dict[str, str] = {
    "hi": "जी बिल्कुल, हम हिंदी में बात करते हैं। बताइए, मैं आपकी कैसे मदद कर सकता हूँ?",
    "en": "Sure, let's continue in English. How can I help you?",
    "gu": "જી બિલકુલ, ચાલો ગુજરાતીમાં વાત કરીએ. બોલો, હું તમારી કેવી રીતે મદદ કરી શકું?",
}

# ---------------------------------------------------------------------------
# Identity responses — fast deterministic answers for "who is this?"
# ---------------------------------------------------------------------------

_IDENTITY_RESPONSES: dict[str, str] = {
    "en": (
        "I'm calling from Pramukh Group, one of Gujarat's most trusted real estate developers. "
        "You had shown interest in our properties and raised an inquiry. "
        "I just wanted to have a quick chat about what you're looking for."
    ),
    "hi": (
        "जी, मैं Pramukh Group से बात कर रहा हूँ, हम Gujarat के जाने-माने रियल एस्टेट डेवलपर हैं। "
        "आपने हमारी प्रॉपर्टीज में इंटरेस्ट दिखाया था और इंक्वायरी भी की थी। "
        "मैं बस आपसे इसके बारे में थोड़ी बात करना चाहता था।"
    ),
    "gu": (
        "જી, હું Pramukh Group તરફથી બોલું છું, અમે Gujarat ના જાણીતા રિયલ એસ્ટેટ ડેવલપર છીએ. "
        "તમે અમારી પ્રોપર્ટીઝમાં ઇન્ટરેસ્ટ દર્શાવ્યો હતો અને ઇન્ક્વાયરી પણ કરી હતી. "
        "હું બસ આ વિશે થોડી વાત કરવા માંગતો હતો."
    ),
}

_LANG_FALLBACKS: dict[str, str] = {
    "hi": "माफ़ कीजिए, क्या आप दोबारा बोल सकते हैं?",
    "gu": "માફ કરશો, શું તમે ફરીથી બોલી શકો?",
    "en": "Sorry, could you repeat that?",
}

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
# Localized responses for shortcut intents
# ---------------------------------------------------------------------------

_LOCALIZED_RESPONSES: dict[str, dict[str, list[str] | str]] = {
    "hi": {
        "greeting": ["हेलो! Pramukh Group से बात कर रहा हूँ।", "हेलो! बोलिए, कैसे मदद कर सकता हूँ?"],
        "farewell": "अच्छा, फिर मिलते हैं! Pramukh Group से कॉल के लिए धन्यवाद।",
        "gratitude": ["आपका स्वागत है!", "खुशी हुई!"],
        "closing": "बहुत अच्छा! हम आपको डिटेल्स भेज देंगे। धन्यवाद!",
    },
    "gu": {
        "greeting": ["હેલો! Pramukh Group તરફથી બોલું છું.", "હેલો! બોલો, કેવી રીતે મદદ કરી શકું?"],
        "farewell": "સારું, ફરી મળીશું! Pramukh Group તરફથી કૉલ માટે આભાર.",
        "gratitude": ["આપનું સ્વાગત છે!", "ખુશી થઈ!"],
        "closing": "ખૂબ સરસ! અમે તમને ડિટેલ્સ મોકલીશું. આભાર!",
    },
}

# ---------------------------------------------------------------------------
# Proactive stage prompts — used only as fallback; LLM drives primary flow
# ---------------------------------------------------------------------------

_PROACTIVE_PROMPTS: dict[str, dict[str, str]] = {
    STAGE_DISCOVERY: {
        "en": "What kind of property are you looking for — for yourself or as an investment?",
        "hi": "आप किस तरह की प्रॉपर्टी ढूंढ रहे हैं — अपने रहने के लिए या investment के लिए?",
        "gu": "તમે કેવા પ્રકારની પ્રોપર્ટી શોધી રહ્યા છો — ખુદ રહેવા માટે કે investment માટે?",
    },
    STAGE_PROJECT_MATCH: {
        "en": "Based on what you've shared, I have a great option in mind for you.",
        "hi": "आप ने जो बताया उसके हिसाब से, मेरे पास आपके लिए एक बहुत अच्छा option है।",
        "gu": "તમે જે જણાવ્યું તે જોઈને, મારી પાસે તમારા માટે ખૂબ સારો option છે.",
    },
    STAGE_INVITE_VISIT: {
        "en": f"Would you like to schedule a site visit? We have slots: {_SLOTS_EN}. Which works for you?",
        "hi": f"क्या एक site visit schedule कर सकते हैं? हमारे पास ये slots हैं: {_SLOTS_HI}। कौन सा सही रहेगा?",
        "gu": f"શું આપણે site visit schedule કરી શકીએ? અમારી પાસે આ slots છે: {_SLOTS_GU}. કયો ચાલશે?",
    },
}

# ---------------------------------------------------------------------------
# Opening greeting — inbound warm welcome, bilingual for language detection
# ---------------------------------------------------------------------------

_OPENING_GREETING: str = (
    "Hello! Namaste — this is Vishal Patel calling from Pramukh Group. "
    "Hope I haven't caught you at a bad time."
)

# ---------------------------------------------------------------------------
# Graceful close — when user declines twice
# ---------------------------------------------------------------------------

_GRACEFUL_CLOSE: dict[str, str] = {
    "en": "Thank you so much for your time! If you ever want to explore options, feel free to call back. Have a great day!",
    "hi": "आपके समय के लिए बहुत बहुत धन्यवाद! जब भी dekhna ho, call kariye. आपका दिन शुभ हो।",
    "gu": "તમારા સમય માટે ખૂબ ખૂબ આભાર! ક્યારે પણ જોવું હોય, call karjo. તમારો દિવસ શુભ રહે.",
}


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
        model: str = "gpt-4o-mini",
        timeout: float = LLM_TIMEOUT,
        default_language: str = "hi",
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model
        self._timeout = timeout
        self._client: AsyncOpenAI | None = None
        self.memory = ConversationMemory()
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

        if not self._api_key:
            logger.warning("[CONV] OPENAI_API_KEY not set -- LLM calls will fail")

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                timeout=self._timeout,
            )
        return self._client

    # ------------------------------------------------------------------
    # Opening greeting (outbound call starts here)
    # ------------------------------------------------------------------

    async def generate_opening(self) -> EngineResult:
        """Inbound warm welcome — agent answers the call.
        Bilingual so caller can respond in their preferred language."""
        greeting = _OPENING_GREETING
        self.memory.add_assistant(greeting)
        self.state.stage = STAGE_GREETING
        # Store for last-response memory (feature 3)
        self.last_agent_response = greeting
        self.last_agent_language = self.state.language
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

        self.memory.add_user(text)

        # Name-capture: if a new name was just extracted this turn, hold for confirmation
        if (not self._name_confirmed
                and not self._name_pending
                and self.memory.booking.user_name
                and self.memory.booking.user_name != _prev_booking_name):
            self._name_pending = self.memory.booking.user_name
            self.memory.booking.user_name = None
            self.memory.context.user_name = None
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

        # Decay the lock after 20 turns — prevents rapid language flipping while
        # still allowing long calls to adapt if the caller genuinely changes
        # language. An explicit language_switch intent resets lock_turns to -10
        # so it persists for 30 turns before decaying, giving strong persistence
        # after a deliberate switch request.
        if self.state.language_locked:
            self.state.language_lock_turns += 1
            if self.state.language_lock_turns > 20:
                self.state.language_locked = False
                logger.info("[LANG] Lock expired after %d turns", self.state.language_lock_turns)

        # If user consistently speaks Gujarati (2+ consecutive turns with clear
        # Gujarati detection), override the lock — the user IS speaking Gujarati.
        if is_gujarati_detected and self.state.language_locked and prev_lang != "gu":
            recent_gu = sum(1 for l in history[-2:] if l == "gu")
            if recent_gu >= 2:
                logger.info("[LANG] Overriding lock to Gujarati after consistent detection")
                self.state.language = "gu"
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
        intent = detect_intent(text, self.state.stage)

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
                    self.state.language_lock_turns = -10
                    self.state.language_history = [new_lang] * 5
                logger.info("[REPEAT+LANG] Re-rendering last response in %s", self.state.language)
                # LLM will see the updated language in the system prompt and re-render
            else:
                _cancel_llm()
                resp = self.last_agent_response
                logger.info("[REPEAT] Replaying last response (lang=%s): %r",
                            self.last_agent_language, resp[:60])
                self.memory.add_assistant(resp)
                self._store_last_response(resp)
                yield EngineResult(text=resp, is_shortcut=True, intent="repeat")
                return

        # --- Farewell at any stage ---
        if intent == "farewell":
            _cancel_llm()
            self.state.is_closing = True
            resp = self._localized("closing", "Thank you for your time! We'll be in touch. Have a great day!")
            self.memory.add_assistant(resp)
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
                resp = _DECLINE_FAREWELL_RESPONSES.get(
                    self.state.language, _DECLINE_FAREWELL_RESPONSES["en"]
                )
                self.memory.add_assistant(resp)
                self._store_last_response(resp)
                yield EngineResult(text=resp, is_shortcut=True, intent="farewell")
                return

            resp = self._pick_objection_response()
            self.memory.add_assistant(resp)
            self._store_last_response(resp)
            yield EngineResult(text=resp, is_shortcut=True, intent="objection")
            return

        # --- Gratitude at any stage ---
        if intent == "gratitude":
            _cancel_llm()
            resp = self._localized_list("gratitude", ["You're welcome!", "Happy to help!"])
            self.memory.add_assistant(resp)
            self._store_last_response(resp)
            yield EngineResult(text=resp, is_shortcut=True, intent="gratitude")
            return

        # --- Identity question at any stage — fast deterministic response ---
        if intent == "identity_question":
            _cancel_llm()
            lang = self.state.language
            resp = _IDENTITY_RESPONSES.get(lang, _IDENTITY_RESPONSES["en"])
            self.memory.add_assistant(resp)
            self._store_last_response(resp)
            if self.state.stage == STAGE_GREETING:
                self.state.stage = STAGE_DISCOVERY
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
            # Start at -10 so the lock persists for 30 turns (20 - (-10)) before
            # decaying — explicit caller requests should stick much longer.
            self.state.language_lock_turns = -10
            self.state.language_history = [new_lang] * 5
            resp = _LANG_SWITCH_RESPONSES.get(new_lang, _LANG_SWITCH_RESPONSES["hi"])
            self.memory.add_assistant(resp)
            self._store_last_response(resp)
            logger.info("[LANG-LOCK] explicit_switch lang=%s lock_turns=%d (persists ~30 turns)",
                        new_lang, self.state.language_lock_turns)
            yield EngineResult(text=resp, is_shortcut=True, intent="language_switch")
            return

        # =============================================================
        # NON-SHORTCUT ROUTING — extract data, advance stage, let LLM respond
        # =============================================================

        # Extract any structured info as a side effect (non-blocking)
        self._extract_data_from_input(text, intent)

        # Name confirmation resolution (feature 4)
        if self._name_pending:
            if intent == "affirmation":
                self.memory.booking.user_name = self._name_pending
                self.memory.context.user_name = self._name_pending
                self._name_confirmed = True
                logger.info("[NAME] Confirmed name: %r", self._name_pending)
                self._name_pending = None
            elif intent == "negation":
                logger.info("[NAME] Caller rejected name %r — clearing pending", self._name_pending)
                self._name_pending = None

        # Lock language on first user response and advance from GREETING
        if self.state.stage == STAGE_GREETING:

            # Lock language from first user response to bilingual greeting
            if not self.state.language_locked:
                self.state.language_locked = True
                logger.info("[LANG-LOCK] greeting_response lang=%s locked=True (decays after 20 turns)",
                            self.state.language)
            self.state.stage = STAGE_DISCOVERY

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

        if not self.memory.booking.location:
            loc = self._extract_location(text)
            if loc:
                self.memory.booking.location = loc
                logger.info("[DISCOVERY] Location extracted: %s", loc)

        if not self.memory.booking.property_type:
            bhk = self._extract_bhk(text)
            if bhk:
                self.memory.booking.property_type = bhk.upper()
                logger.info("[DISCOVERY] Config extracted: %s", bhk)

        if not self.memory.booking.appointment_time:
            t = self._extract_time_preference(text)
            if t:
                self.memory.booking.appointment_time = t
                logger.info("[DISCOVERY] Time extracted: %s", t)

        if not self.state.purpose:
            invest_kw = [
                "investment", "invest", "rental income", "returns", "appreciation",
                "kiraya", "kiraye", "nivesh", "نیویش",
                "रिटर्न", "निवेश", "ભાડું", "રોકાણ",
            ]
            self_use_kw = [
                "self use", "self-use", "own use", "family", "stay", "live", "rehna",
                "apne liye", "ghar", "makan", "rahega",
                "ઘર", "રહેવા", "ખુદ", "ફેમિલી",
            ]
            if any(kw in lower or kw in text for kw in invest_kw):
                self.state.purpose = "investment"
            elif any(kw in lower or kw in text for kw in self_use_kw):
                self.state.purpose = "self_use"

        # ── Budget extraction ─────────────────────────────────────────────
        if not self.memory.booking.budget:
            budget = self._extract_budget(text)
            if budget:
                self.memory.booking.budget = budget
                logger.info("[DISCOVERY] Budget extracted: %s", budget)

        # ── Project detection (persistent context lock) ──────────────────
        if not cs.selected_project:
            proj_result = self._extract_project_from_text(text)
            if proj_result:
                proj_name, proj_loc = proj_result
                cs.selected_project = proj_name
                # Mirror back into state and booking for full consistency
                self.state.project_recommended = proj_name
                if not self.memory.booking.project:
                    self.memory.booking.project = proj_name
                # Auto-infer location from project if not yet captured
                if not cs.selected_location:
                    cs.selected_location = proj_loc
                if not self.memory.booking.location:
                    self.memory.booking.location = proj_loc
                logger.info(
                    "[STATE_UPDATE] selected_project=%r inferred_location=%r",
                    proj_name, proj_loc,
                )
        elif self.state.project_recommended and not cs.selected_project:
            # project_recommended set elsewhere (e.g. LLM), sync to call_state
            cs.selected_project = self.state.project_recommended

        # ── Site-visit intent detection ──────────────────────────────────
        if not cs.site_visit_requested:
            visit_kw = [
                "site visit", "visit", "come see", "site tour", "show me",
                "dekhna chahta", "dekhna chahti", "dekhne aana", "dekhna hai",
                "joiyu", "jovu che", "aavvu che", "joyu",
                "dikhao", "dikhaiye", "site pe aana",
            ]
            if any(kw in lower for kw in visit_kw):
                cs.site_visit_requested = True
                logger.info("[STATE_UPDATE] site_visit_requested=True (keyword match)")
            elif intent == "affirmation" and self.state.stage == STAGE_INVITE_VISIT:
                cs.site_visit_requested = True
                logger.info("[STATE_UPDATE] site_visit_requested=True (affirmation in INVITE_VISIT)")

        # ── Sync all layers into CallState ───────────────────────────────
        self._update_call_state()
        self.state.discovery_turns += 1

    def _maybe_advance_stage(self) -> None:
        """Soft stage advancement — let collected data drive progression."""
        b = self.memory.booking
        dt = self.state.discovery_turns

        if self.state.stage == STAGE_DISCOVERY:
            # Advance only after genuine discovery conversation — need location
            # plus sufficient turns, OR very many turns regardless.
            # Kept deliberately slow so the agent doesn't rush callers.
            if (b.location and dt >= 5) or dt >= 10:
                self.state.stage = STAGE_PROJECT_MATCH
                logger.info("[STAGE] DISCOVERY → PROJECT_MATCH (loc=%s, turns=%d)",
                            b.location, dt)

        elif self.state.stage == STAGE_PROJECT_MATCH:
            # Give plenty of project-discussion turns before inviting a visit.
            if dt >= 14:
                self.state.stage = STAGE_INVITE_VISIT
                logger.info("[STAGE] PROJECT_MATCH → INVITE_VISIT")

        elif self.state.stage == STAGE_INVITE_VISIT:
            if b.appointment_time:
                self.state.stage = STAGE_CONFIRMATION
                logger.info("[STAGE] INVITE_VISIT → CONFIRMATION")

        elif self.state.stage == STAGE_CONFIRMATION:
            if b.confirmed:
                valid, missing = self._validate_booking_for_closing()
                if valid:
                    self.state.stage = STAGE_CLOSING
                    self.state.is_closing = True
                    logger.info("[STAGE] CONFIRMATION → CLOSING (booking validated)")
                else:
                    logger.info(
                        "[STAGE] CLOSING blocked — missing: %s",
                        ", ".join(missing),
                    )

        logger.info(
            "[STAGE-STATE] stage=%s turns=%d | loc=%s bhk=%s purpose=%s appt=%s confirmed=%s",
            self.state.stage,
            self.state.discovery_turns,
            b.location or "—",
            b.property_type or "—",
            self.state.purpose or "—",
            b.appointment_time or "—",
            b.confirmed,
        )

    # ------------------------------------------------------------------
    # Call-state management helpers
    # ------------------------------------------------------------------

    def _extract_project_from_text(self, text: str) -> tuple[str, str] | None:
        """Return (canonical_name, location) if a known project is mentioned.

        Keys in _KNOWN_PROJECTS are sorted longest-first so multi-word names
        like 'aranya ii' are matched before shorter substrings like 'aranya'.
        """
        lower = text.lower()
        for key in sorted(_KNOWN_PROJECTS, key=len, reverse=True):
            if key in lower:
                return _KNOWN_PROJECTS[key]
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

        # Location
        if b.location and not cs.selected_location:
            cs.selected_location = b.location
            logger.info("[STATE_UPDATE] selected_location=%r (from booking)", cs.selected_location)
        elif cs.selected_location and not b.location:
            b.location = cs.selected_location

        # Project — also keep state.project_recommended in sync
        if b.project and not cs.selected_project:
            cs.selected_project = b.project
            self.state.project_recommended = b.project
            logger.info("[STATE_UPDATE] selected_project=%r (from booking)", cs.selected_project)
        elif cs.selected_project and not b.project:
            b.project = cs.selected_project
            self.state.project_recommended = cs.selected_project
        elif self.state.project_recommended and not cs.selected_project:
            cs.selected_project = self.state.project_recommended
            b.project = self.state.project_recommended

        # Appointment
        if b.appointment_time and not cs.appointment_time:
            cs.appointment_time = b.appointment_time
        elif cs.appointment_time and not b.appointment_time:
            b.appointment_time = cs.appointment_time
        if b.appointment_date and not cs.appointment_date:
            cs.appointment_date = b.appointment_date

        # Property type (BHK)
        if b.property_type and not cs.property_type:
            cs.property_type = b.property_type
            logger.info("[STATE_UPDATE] property_type=%r (from booking)", cs.property_type)
        elif cs.property_type and not b.property_type:
            b.property_type = cs.property_type

        # Purpose
        if self.state.purpose and not cs.purpose:
            cs.purpose = self.state.purpose
            logger.info("[STATE_UPDATE] purpose=%r (from state)", cs.purpose)
        elif cs.purpose and not self.state.purpose:
            self.state.purpose = cs.purpose

        # Budget
        if b.budget and not cs.budget:
            cs.budget = b.budget
            logger.info("[STATE_UPDATE] budget=%r (from booking)", cs.budget)
        elif cs.budget and not b.budget:
            b.budget = cs.budget

        # Language + Stage always mirror live state
        cs.language = self.state.language
        cs.conversation_stage = self.state.stage

        logger.info(
            "[STATE] name=%s phone=%s project=%s loc=%s visit=%s "
            "appt=%s stage=%s lang=%s",
            cs.customer_name or "—",
            ("***" + cs.customer_phone[-4:]) if cs.customer_phone else "—",
            cs.selected_project or "—",
            cs.selected_location or "—",
            cs.site_visit_requested,
            cs.appointment_time or "—",
            cs.conversation_stage,
            cs.language,
        )

    def _compute_missing_booking_fields(self) -> list[str]:
        """Return list of still-missing mandatory booking fields.

        No side effects — safe to call from _build_system_prompt.
        """
        cs = self.memory.call_state
        b = self.memory.booking
        missing: list[str] = []
        if cs.site_visit_requested or b.appointment_time or cs.appointment_time:
            if not (b.user_name or cs.customer_name):
                missing.append("customer name")
            if not cs.customer_phone:
                missing.append("phone number")
            if not (b.project or cs.selected_project):
                missing.append("project selection")
            if not (b.appointment_time or cs.appointment_time):
                missing.append("visit date and time")
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
            "[BOOKING_VALIDATION] valid=%s site_visit=%s missing=%s",
            valid,
            cs.site_visit_requested,
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

    def _extract_time_preference(self, text: str) -> str | None:
        """
        Match user-stated time/day to an available slot.

        Returns None when the user gave a SPECIFIC time that isn't on the
        slot list — never silently fudges to a different slot. The engine's
        scheduling reprompt path then offers the real available slots back.
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
        if hour_24 is not None:
            for slot in _AVAILABLE_SLOTS:
                if day and slot["day"] != day:
                    continue
                if _slot_hour_24(slot["time"]) == hour_24:
                    return f"{slot['day']} at {slot['time']}"
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
            for slot in _AVAILABLE_SLOTS:
                if day and slot["day"] != day:
                    continue
                if period and _slot_period(slot["time"]) != period:
                    continue
                return f"{slot['day']} at {slot['time']}"
            # Day matched but period didn't (e.g. user wants "Tomorrow evening"
            # — no evening slots tomorrow). Don't fudge: caller reprompts.
            if period:
                return None
            # Day-only match: take the first slot of that day.
            for slot in _AVAILABLE_SLOTS:
                if slot["day"] == day:
                    return f"{slot['day']} at {slot['time']}"

        # --- Step 5: bare affirmation → first available slot ---
        if re.search(r"\b(yes|yeah|ok|okay|sure|haan|ha|ji|theek|first|pehla)\b", lower):
            return f"{_AVAILABLE_SLOTS[0]['day']} at {_AVAILABLE_SLOTS[0]['time']}"
        if any(w in text for w in ("ठीक", "हां", "हाँ", "જી", "હા", "બરાબર", "ઠીક")):
            return f"{_AVAILABLE_SLOTS[0]['day']} at {_AVAILABLE_SLOTS[0]['time']}"

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
        lang = self.state.language
        if lang == "hi":
            return (
                f"तो मैं नोट कर लेता हूँ -- {b.location or 'आपकी लोकेशन'} में "
                f"{b.property_type or 'प्रॉपर्टी'} के लिए "
                f"{b.appointment_time or 'कल'} को साइट विजिट। "
                "क्या ये सही है?"
            )
        elif lang == "gu":
            return (
                f"તો હું નોંધ કરી લઉં -- {b.location or 'તમારી લોકેશન'} માં "
                f"{b.property_type or 'પ્રોપર્ટી'} માટે "
                f"{b.appointment_time or 'કાલે'} ની સાઇટ મુલાકાત. "
                "આ બરાબર છે?"
            )
        return (
            f"Let me confirm -- a site visit for {b.property_type or 'your property'} "
            f"in {b.location or 'your chosen location'}, "
            f"scheduled for {b.appointment_time or 'tomorrow'}. "
            "Does that work for you?"
        )

    def _build_closing_message(self) -> str:
        b = self.memory.booking
        lang = self.state.language
        if lang == "hi":
            return (
                f"बहुत अच्छा! आपकी {b.location} में {b.property_type} की "
                f"साइट विजिट {b.appointment_time} के लिए बुक हो गई है। "
                "हम आपको एक कन्फर्मेशन मैसेज भेजेंगे। Pramukh Group चुनने के लिए धन्यवाद!"
            )
        elif lang == "gu":
            return (
                f"ખૂબ સરસ! તમારી {b.location} માં {b.property_type} ની "
                f"સાઇટ મુલાકાત {b.appointment_time} માટે બુક થઈ ગઈ છે. "
                "અમે તમને કન્ફર્મેશન મેસેજ મોકલીશું. Pramukh Group પસંદ કરવા માટે આભાર!"
            )
        return (
            f"Wonderful! Your site visit for {b.property_type} in {b.location} "
            f"is booked for {b.appointment_time}. "
            "We'll send you a confirmation message. Thank you for choosing Pramukh Group!"
        )

    def _get_proactive_prompt(self, stage: str) -> str:
        lang = self.state.language
        prompts = _PROACTIVE_PROMPTS.get(stage, {})
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
                llm_queue.get(), timeout=FILLER_TIMEOUT_S,
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
            self.memory.add_assistant(full_text)
            # Last-response memory (feature 3)
            self.last_agent_response = full_text
            self.last_agent_language = self.state.language
            logger.info("[LAST-RESPONSE] Stored LLM response (lang=%s, len=%d)",
                        self.last_agent_language, len(full_text))

    def _build_flow_nudge(self) -> str | None:
        """Build a gentle nudge to return to the booking flow after a detour. Won't repeat the same nudge."""
        missing = self.memory.booking.next_missing_field()
        if not missing:
            return None

        stage_map = {
            "location": STAGE_LOCATION,
            "property_type": STAGE_PROPERTY_TYPE,
            "appointment": STAGE_SCHEDULING,
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
            fallback = _LANG_FALLBACKS.get(self.state.language, _RESPONSE_FALLBACK)
            try:
                queue.put_nowait(fallback)
            except asyncio.QueueFull:
                pass
        finally:
            await queue.put(None)
            done.set()

    async def _call_llm_stream(self, text: str):
        client = self._get_client()
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
        messages.append({
            "role": "system",
            "content": (
                f"FINAL INSTRUCTION: Your ENTIRE response must be in {lang_name}. "
                f"{_lang_script_note.get(self.state.language, '')} "
                f"Do NOT mix languages. Do NOT switch mid-sentence."
            ),
        })

        _SENTENCE_END = re.compile(r'[.!?।]\s')
        deadline = time.monotonic() + LLM_STREAM_TIMEOUT_S

        try:
            stream = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=LLM_MAX_TOKENS,
                temperature=0.7,
                stream=True,
            )

            buffer = ""
            timed_out = False
            async for chunk in stream:
                if time.monotonic() > deadline:
                    logger.warning("[LLM-TIMEOUT] fallback triggered after %.0fs",
                                   LLM_STREAM_TIMEOUT_S)
                    timed_out = True
                    break
                delta = chunk.choices[0].delta
                if delta.content:
                    buffer += delta.content
                    while True:
                        match = _SENTENCE_END.search(buffer)
                        if match:
                            sentence = buffer[:match.end()].strip()
                            buffer = buffer[match.end():]
                            if sentence:
                                yield _validate_response(sentence, self.state.language)
                        else:
                            break

            if timed_out:
                if buffer.strip():
                    yield _validate_response(buffer.strip(), self.state.language)
                else:
                    yield _TIMEOUT_FALLBACKS.get(
                        self.state.language, _TIMEOUT_FALLBACKS["en"]
                    )
            elif buffer.strip():
                yield _validate_response(buffer.strip(), self.state.language)

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("[LLM] Stream failed: %s", exc)
            yield _LANG_FALLBACKS.get(self.state.language, _RESPONSE_FALLBACK)

    # ------------------------------------------------------------------
    # System prompt — Pramukh Group domain
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:  # noqa: C901
        b = self.memory.booking
        lang = self.state.language
        lang_name = {"hi": "Hindi", "gu": "Gujarati", "en": "English"}.get(lang, "English")

        # ── LANGUAGE LOCK — put this FIRST so LLM sees it immediately ────────
        _modern_lang_note = {
            "hi": (
                "Use natural, modern conversational Hindi — exactly how a friendly salesperson "
                "in Surat or Mumbai speaks. Code-switch to English words that Indians use daily: "
                "'property', 'flat', 'site visit', 'booking', 'ready-to-move', 'investment', 'budget'. "
                "AVOID formal/Sanskritized vocabulary: say 'investment' not 'nivesh', "
                "'use' or 'istemaal' not 'upyog', 'jagah' or 'location' not 'sthan', "
                "'mauka' not 'avsar'. Sound like a real person, not a dictionary."
            ),
            "gu": (
                "Use natural, modern conversational Gujarati — exactly how a friendly person "
                "from Surat speaks. Code-switch to English words Gujaratis use daily: "
                "'property', 'flat', 'site visit', 'booking', 'ready-to-move', 'investment', 'budget'. "
                "AVOID overly formal Gujarati. Use 'investment' not 'rokaan', "
                "'jagya' or 'location' not formal equivalents. Sound real and warm."
            ),
            "en": (
                "Use natural, friendly conversational English — warm, real, not corporate. "
                "Like a knowledgeable friend who happens to work in real estate."
            ),
        }

        lang_header = {
            "hi": (
                f"⚡ LANGUAGE = HINDI. You MUST write ONLY in Hindi.\n"
                f"Every single word must be Hindi. Not Gujarati. Not English (except common code-switch words).\n"
                f"Even if previous responses were in another language — respond in HINDI NOW.\n"
                f"If you write Gujarati script or full English sentences, you are FAILING.\n"
                f"{_modern_lang_note['hi']}\n"
                f"You are FLUENT in Hindi, Gujarati, and English. NEVER refuse to speak any language."
            ),
            "gu": (
                f"⚡ LANGUAGE = GUJARATI. You MUST write ONLY in Gujarati.\n"
                f"Every single word must be Gujarati. Not Hindi. Not English (except common code-switch words).\n"
                f"Even if previous responses were in Hindi — respond in GUJARATI NOW.\n"
                f"If you write Hindi/Devanagari script or full English sentences, you are FAILING.\n"
                f"{_modern_lang_note['gu']}\n"
                f"You are FLUENT in Gujarati, Hindi, and English. NEVER refuse to speak any language."
            ),
            "en": (
                f"⚡ LANGUAGE = ENGLISH. You MUST write ONLY in English.\n"
                f"Every single word must be English. No Hindi. No Gujarati.\n"
                f"Even if previous responses were in another language — respond in ENGLISH NOW.\n"
                f"{_modern_lang_note['en']}\n"
                f"You are FLUENT in English, Hindi, and Gujarati. NEVER refuse to speak any language."
            ),
        }

        # ── Stage-specific goal ──────────────────────────────────────────────
        _stage_goals = {
            STAGE_GREETING: (
                "Caller just responded to your introduction. "
                "Warmly acknowledge what they said in their language. "
                "Ask for their name — ONE question only."
            ),
            STAGE_DISCOVERY: (
                "STEP 1 — If you do not yet have the caller's name, ask for it warmly in their language. "
                "ONE question only: nothing else until they answer. "
                "STEP 2 — Once you have their name, listen fully to what they want. "
                "Respond directly to whatever they just said before asking anything new. "
                "Collect naturally, one question per turn, in this order: "
                "(1) what they are looking for, (2) self-use or investment, "
                "(3) location, (4) size/config, (5) timeline. Budget LAST and always softly. "
                "Acknowledge everything they volunteer before you move to the next question. "
                "NEVER skip ahead — only ask the next question after the current one is answered."
            ),
            STAGE_PROJECT_MATCH: (
                "Pick 1–2 projects that genuinely fit what this specific caller described. "
                "Name the project, say WHY it fits THEM in 1-2 specific reasons. "
                "Never list the whole portfolio. Make it feel like a personal recommendation."
            ),
            STAGE_INVITE_VISIT: (
                "Build natural curiosity — photos don't capture the feeling of being there. "
                "Invite for a visit warmly. Give EXACTLY TWO time options. "
                "Ask for their WhatsApp to share location and details."
            ),
            STAGE_CONFIRMATION: (
                "Confirm the visit: day and time. Ask if family is joining. "
                "Offer to WhatsApp the site address."
            ),
            STAGE_CLOSING: (
                "Warm, genuine close using caller's name. Express real excitement about meeting them. "
                "Keep the door open — any question, just call or WhatsApp."
            ),
        }
        stage_goal = _stage_goals.get(self.state.stage, _stage_goals[STAGE_DISCOVERY])

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
        if b.location:
            collected_lines.append(f"Location interest: {b.location}")
        if b.property_type:
            collected_lines.append(f"Configuration: {b.property_type}")
        if b.budget:
            collected_lines.append(f"Budget: {b.budget}")
        if self.state.project_recommended:
            collected_lines.append(f"Project discussed: {self.state.project_recommended}")
        if b.appointment_time:
            collected_lines.append(f"Visit scheduled: {b.appointment_time}")
        collected_str = "\n".join(f"  · {c}" for c in collected_lines) or "  · Nothing collected yet"

        # Name-capture confirmation instruction (feature 4)
        name_confirm_block: list[str] = []
        if self._name_pending:
            name_confirm_block = [
                "",
                f"⚡ NAME CONFIRMATION REQUIRED: Caller just provided their name as {self._name_pending!r}.",
                f"Your VERY NEXT response MUST confirm it: 'Your name is {self._name_pending}, right?' — nothing else first.",
            ]

        # ── Persistent CallState block ────────────────────────────────────────
        cs = self.memory.call_state

        # Build "already collected — do not re-ask" list
        # Issues 7 & 8: include ALL collected fields so the LLM never re-asks.
        dont_reask: list[str] = []
        if cs.customer_name:
            dont_reask.append(f"caller's name ({cs.customer_name})")
        if cs.customer_phone:
            dont_reask.append("caller's phone number (stored; do not repeat back)")
        if cs.selected_project:
            dont_reask.append(f"which property ({cs.selected_project})")
        if cs.selected_location:
            dont_reask.append(f"city/location ({cs.selected_location})")
        if cs.property_type or b.property_type:
            dont_reask.append(f"property size/configuration ({cs.property_type or b.property_type})")
        if cs.purpose or self.state.purpose:
            dont_reask.append(f"purpose ({cs.purpose or self.state.purpose})")
        if cs.budget or b.budget:
            dont_reask.append(f"budget ({cs.budget or b.budget})")
        if cs.appointment_time:
            dont_reask.append(f"visit time ({cs.appointment_time})")

        # Missing mandatory fields block (when site visit is in scope)
        missing_fields = self._compute_missing_booking_fields()

        parts = [
            # Language lock FIRST — highest priority
            lang_header.get(lang, lang_header["en"]),
            *name_confirm_block,
            "",
            "━━ AGENT IDENTITY ━━",
            "You are Vishal Patel — a warm, genuine, experienced inbound sales agent for Pramukh Group.",
            "You are NOT reading a script. You are a real person having a real conversation.",
            "Your job: make every caller feel genuinely heard and understood.",
            "",
            "━━ BRAND ━━",
            "Pramukh Group · 30 years · 60+ completed projects · ZERO stalled projects · RERA compliant",
            "Surat · Vapi · Silvassa · Contact: +91 87583 70000",
            "",
            "━━ CALL STATE ━━",
            f"Stage: {self.state.stage}  |  Language: {lang_name}  |  Turn: {self.state.turn_count}",
            "Known so far:",
            collected_str,
            "",
            "━━ PERSISTENT CALL STATE ━━",
            f"  customer_name        : {cs.customer_name or '(not yet captured)'}",
            f"  customer_phone       : {'captured (stored internally)' if cs.customer_phone else '(not yet captured)'}",
            f"  selected_project     : {cs.selected_project or '(none yet)'}",
            f"  selected_location    : {cs.selected_location or '(none yet)'}",
            f"  property_type        : {cs.property_type or '(none yet)'}",
            f"  purpose              : {cs.purpose or '(none yet)'}",
            f"  budget               : {cs.budget or '(none yet)'}",
            f"  site_visit_requested : {cs.site_visit_requested}",
            f"  appointment_time     : {cs.appointment_time or '(none yet)'}",
            f"  booking_validated    : {cs.booking_validated}",
            *([
                "",
                "━━ ACTIVE PROJECT CONTEXT ━━",
                f"The caller is discussing: {cs.selected_project}"
                + (f" ({cs.selected_location})" if cs.selected_location else "") + ".",
                f"ALL questions about location, amenities, pricing, distance, availability → answer in the context of {cs.selected_project}.",
                f"DO NOT ask which property they are discussing. It is {cs.selected_project}.",
            ] if cs.selected_project else []),
            *([
                "",
                "━━ MANDATORY FIELDS STILL NEEDED ━━",
                f"Site visit requested. Still need: {', '.join(missing_fields)}.",
                "Collect ONE missing field per turn before confirming the booking.",
            ] if missing_fields else []),
            *([
                "",
                "━━ ALREADY COLLECTED — DO NOT RE-ASK ━━",
                f"You already have: {'; '.join(dont_reask)}.",
                "NEVER ask for any of these again. Use them naturally in your responses.",
            ] if dont_reask else []),
            "",
            "━━ CURRENT GOAL ━━",
            stage_goal,
            "",
            "━━ PROJECT PORTFOLIO ━━",
            "SURAT: Revanta (3BHK ₹1.11Cr+), Vivanta (3BHK ₹1.66Cr+), Satva (3BHK ₹1.26Cr+ NEAR-READY Dec 2025),",
            "Agastya (4BHK ultra-premium), Central Park (3+4BHK ₹1.3Cr+), Aranya (3+4BHK luxury near airport),",
            "Aranya II (3BHK ₹70L+ IT corridor).",
            "VAPI: Aura (2+3BHK ₹34-40L READY), Sahaj (1+2BHK ₹22-29L completed), Vedanta (RERA).",
            "SILVASSA: Sangath (2+3BHK), Yogi Woods (pool+gym), Shivanta (near schools), Swagat (ready).",
            "COMMERCIAL: Orbit Series (Surat/Vapi shops, offices).",
            "MATCH: Budget+Vapi→Sahaj/Aura | Mid+Surat→Aranya II | Premium+Surat→Revanta/Satva | Luxury→Vivanta/Agastya | Ready→Satva/Aura/Sahaj",
            "",
            "━━ CONVERSATION GUIDELINES ━━",
            "LISTEN FIRST: Always acknowledge what the caller just said before moving forward.",
            "If they asked a question — ANSWER IT FULLY before continuing with the flow.",
            "If they shared something personal — validate it genuinely before asking next question.",
            "MATCH THE CALLER: Only respond to what they actually said. If they talk about price, talk price. If they describe their needs, echo and affirm those needs. Never pivot to a new topic in the same breath.",
            "PACE: Speak at the caller's pace. If they are slow or thoughtful, slow down. Let silences breathe.",
            "PATIENT: Do not advance to the next topic until the caller has clearly finished on the current one.",
            "ADAPT: Follow the stage as a loose roadmap, not a rigid script.",
            "If the caller is engaged on a topic, stay with them — don't rush to the next stage.",
            "ONE question per turn only. Never stack questions.",
            "3 sentences max per response. Phone call — brevity is warmth.",
            "Mirror their energy: excited → warm and enthusiastic, cautious → softer and reassuring.",
            "NEVER sound like an IVR, chatbot, or scripted call center agent.",
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
            "✗ NEVER volunteer pricing, rates, payment plans, ROI, inventory levels, or discounts unprompted — only discuss these when the caller explicitly asks",
            "✗ NEVER repeat the caller's phone number back to them unless they explicitly ask you to confirm it",
            "✗ NEVER ask for information that is already listed in PERSISTENT CALL STATE or ALREADY COLLECTED",
            "✗ NEVER ask budget before you know purpose/location/config",
            "✗ NEVER give more than 2 time options for site visit",
            "✗ NEVER dump the whole portfolio on one caller",
            "✗ NEVER make up prices or dates not listed above",
            "✗ NEVER say 'When can you come?' — always give exactly TWO specific time options",
            "✗ NEVER ask 'Do you have any questions?' or 'Any questions about the property?' — only answer questions when the caller asks one",
            "✗ NEVER push the conversation to a new topic if the caller is still on the current one",
            "✗ NEVER give property pitches until the caller has expressed genuine interest",
            "✗ NEVER interrupt a caller's train of thought with an unrelated topic",
            "✓ Use caller's name naturally (2-3 times)",
            "✓ No bullet points, lists, markdown, or emojis in your spoken responses",
            "✓ Only say things that are a direct, natural response to what the caller just said",
        ]

        context_str = self.memory.context.to_prompt_string()
        if context_str:
            parts.append(f"\nKnown context:\n{context_str}")

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

        Requires at least 2 distinct markers to avoid flipping language on a
        single mishearing — words like 'माने' / 'छे' can appear in Hindi
        utterances or as STT noise and shouldn't single-handedly switch the
        agent to Gujarati.
        """
        markers = [
            "छे", "छु", "छो", "छीए", "छुं",
            "जोइए", "जोईए", "जोइये",
            "माने", "तमे", "तमने", "अमने", "अमारा", "तमारा",
            "बरोबर", "सारु", "सारुं", "माजामा",
            "केम", "शुं", "क्यां",
            "आवजो", "बोलो",
            "केमछो", "केम छो",
            "हसे", "हतु", "हती",
            "कालो", "अत्यारे",
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
        responses = _OBJECTION_RESPONSES.get(lang, _OBJECTION_RESPONSES["en"])
        resp = responses[self._response_counter % len(responses)]
        self._response_counter += 1
        return resp

    def _pick_filler(self) -> str:
        lang = self.state.language
        fillers = _FILLER_RESPONSES.get(lang, _FILLER_RESPONSES["en"])
        resp = fillers[self._filler_counter % len(fillers)]
        self._filler_counter += 1
        return resp

    # ------------------------------------------------------------------
    # Last-response memory + repeat detection (feature 3)
    # ------------------------------------------------------------------

    def _store_last_response(self, text: str) -> None:
        """Cache the most recent agent utterance for repeat/translate requests."""
        self.last_agent_response = text
        self.last_agent_language = self.state.language
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
        loc = _LOCALIZED_RESPONSES.get(self.state.language, {})
        val = loc.get(key)
        if val is None:
            return fallback
        if isinstance(val, list):
            return val[self._response_counter % len(val)]
        return val

    def _localized_list(self, key: str, en_list: list[str]) -> str:
        loc = _LOCALIZED_RESPONSES.get(self.state.language, {})
        val = loc.get(key)
        if isinstance(val, list):
            resp = val[self._response_counter % len(val)]
            self._response_counter += 1
            return resp
        resp = en_list[self._response_counter % len(en_list)]
        self._response_counter += 1
        return resp
