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
from datetime import datetime, timedelta, timezone

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

from .stage_roles import derive_stage_role as _derive_stage_role

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
LLM_STREAM_TIMEOUT_S = 7.0
INTENT_CLASSIFY_TIMEOUT_S = 1.0

# First-turn ambiguous-language handling: the LLM classify round-trip used to
# run SEQUENTIALLY before the main LLM on the caller's very first utterance.
# Default off = first turn trusts STT's detection (already mirrored into
# state.language by the pipeline) / agent default; later turns keep the LLM.
LANG_LLM_CLASSIFY_FIRST_TURN = os.environ.get(
    "LANG_LLM_CLASSIFY_FIRST_TURN", "false").lower() == "true"

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


# "Stop calling me" — the compliance opt-out, distinct from an objection or a
# "call me later" preference. Every pattern requires the never/again/stop/
# remove semantics, so scheduling talk ("don't call me today", "abhi call mat
# karna", "call me after 8") never trips it. Pure + module-level: unit-tested
# against both directions in tests.
_DND_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\b(?:do\s*n[o']t|never)\s+(?:call|phone|contact)\s+me\s+(?:again|any\s*more|ever)",
    r"\bnever\s+(?:call|phone|contact)\s+me\b",
    r"\bstop\s+(?:calling|phoning|contacting)\b",
    r"\b(?:remove|delete|take)\s+(?:my|me)\s+(?:number|off)\b",
    r"\btake\s+me\s+off\s+(?:your|the|this)\s+list\b",
    r"\bunsubscribe\b",
    r"\bdo\s+not\s+disturb\b",
    # Hindi (romanized): dobara/phir kabhi + call/phone + mat/na, band karo
    r"\b(?:dobara|dubara|phir\s+kabhi)\s+(?:call|phone|फ़ोन|फोन|कॉल)\s*(?:mat|na|नहीं|मत)",
    r"\b(?:call|phone)\s+karna\s+band\s+kar",
    r"\bmera\s+number\s+(?:hata|delete|nikal)",
    # Hindi (Devanagari)
    r"दोबारा\s*(?:कॉल|फोन|फ़ोन)\s*(?:मत|न(?:हीं)?\s*कर)",
    r"फिर\s*कभी\s*(?:कॉल|फोन|फ़ोन)\s*(?:मत|न)",
    r"(?:कॉल|फोन|फ़ोन)\s*करना\s*बंद\s*कर",
    r"मेरा\s*नंबर\s*(?:हटा|निकाल|डिलीट)",
    # Gujarati
    r"ફરી\s*(?:ફોન|કૉલ|કોલ)\s*(?:ન\s*કર|કરશો\s*નહીં|ના\s*કર)",
    r"મારો\s*નંબર\s*(?:કાઢી|હટાવ|ડિલીટ)",
))


def _detect_dnd_request(text: str) -> bool:
    """True when the caller asked us to stop contacting them — ever."""
    t = (text or "").strip()
    if len(t) < 8:  # too short to carry the semantics; avoids noise tokens
        return False
    return any(p.search(t) for p in _DND_PATTERNS)


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


# --- Call repair (platform rules — every agent, every call) ---------------
# Early-call "hello?" window: a bare hello inside this many seconds of the
# greeting means the caller didn't hear us — replay the last utterance
# instead of chatting. Tunable via env; see ConversationRouter._is_early_hello.
EARLY_HELLO_WINDOW_S = float(os.environ.get("CALL_REPAIR_HELLO_WINDOW_S", "25"))
# Hello words across the platform's scripts (en / Devanagari / Gujarati).
_HELLO_CORE = {"hello", "hallo", "helo", "hullo", "hellow",
               "हेलो", "हैलो", "हलो", "हॅलो", "હેલો", "હલો", "હૅલો"}
# Filler that may ride along with a bare hello without changing its meaning.
_HELLO_FILLER = {"haan", "haa", "han", "ha", "ji", "are", "arey", "arre",
                 "हां", "हाँ", "जी", "अरे", "હા", "જી", "અરે"}
# Dead-air reprompt phrases (used with the last question re-asked after them).
_COULDNT_HEAR = {
    "en": "Sorry, I couldn't hear you.",
    "hi": "माफ़ कीजिए, मुझे आपकी आवाज़ नहीं आई।",
    "gu": "માફ કરશો, મને તમારો અવાજ સંભળાયો નહીં.",
}
_SAY_AGAIN = {
    "en": "Could you please say that again?",
    "hi": "कृपया दोबारा बताइए।",
    "gu": "કૃપા કરીને ફરી કહેશો?",
}

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
    "dobara bolo", "dobara", "dubara", "phir se bolo", "phir se", "phir kaho",
    "fari bol", "fari bolsho", "fari janavo",
    "फिर बोलो", "फिर से", "दोबारा", "दोबारा बोलो", "दुबारा", "एक बार फिर",
    "ફરી બોલ", "ફરી",
    # "the same thing (in Hindi)" — live call 2026-07-28 looped the canned
    # language ack three turns because none of these matched. "सेम ची" is a
    # prefix of "सेम चीज़" in both the combining-nukta and precomposed forms.
    "same thing", "same cheez", "sem cheez", "सेम ची", "वही बात", "वही बता",
    "એ જ વાત",
]

# Tokens that make up a *bare* language-switch request — language names,
# switch directives, postpositions, and politeness/filler across the three
# focus languages (en/hi/gu) and their scripts. _lang_switch_has_content()
# strips these; whatever survives is a real ask the caller wants answered,
# not just a request to change language.
_LANG_SWITCH_STOP_TOKENS = frozenset([
    # language names (incl. common suffixed/agglutinated forms)
    "english", "hindi", "gujarati", "angrezi", "angreji", "inglish",
    "इंग्लिश", "अंग्रेज़ी", "अंग्रेजी", "हिंदी", "हिन्दी", "गुजराती",
    "हिंदीमें", "गुजरातीमें",
    "ગુજરાતી", "અંગ્રેજી", "હિન્દી", "ઇંગ્લિશ",
    "ગુજરાતીમાં", "હિન્દીમાં", "અંગ્રેજીમાં", "ઇંગ્લિશમાં",
    # switch directives / verbs
    "speak", "talk", "say", "tell", "switch", "continue", "reply", "use",
    "change", "know", "understand",
    "bolo", "boliye", "bol", "bola", "bolna", "baat", "vaat", "vat",
    "karo", "kariye", "kare", "karna", "kar",
    "batao", "bataiye", "bata", "batana", "samajh", "aata", "aati",
    "बोलो", "बोलिए", "बोल", "बोलें", "बोलना", "बात",
    "बताओ", "बताइए", "बताना", "बता",
    "करो", "करिए", "कीजिए", "करें", "कर", "समझ", "आता", "आती",
    "વાત", "બોલો", "બોલ", "બોલવું", "કરો", "કહો", "જણાવો", "આવડે", "આવડતું",
    # postpositions / auxiliaries
    "in", "to", "me", "mai", "mein", "ma", "maa", "mei", "se", "par", "pe",
    "ki", "ka", "ke", "ko", "hai", "hain", "ho", "hu", "hun", "che", "chhe",
    "में", "मैं", "मा", "माँ", "से", "को", "का", "की", "के",
    "है", "हैं", "हूँ", "हूं", "हो",
    "માં", "મા", "ને", "નું", "ની", "નો", "થી", "છે", "છું",
    # politeness / fillers / pronouns / negation / restriction
    "please", "pls", "plz", "kya", "kyaa", "aap", "ap", "tum", "tu",
    "i", "you", "we", "can", "could", "would", "will", "do", "does",
    "don't", "dont", "can't", "cant", "cannot", "not", "no", "yes",
    "ok", "okay", "na", "ji", "sir", "madam", "bhai", "hello", "hi",
    "haan", "ha", "arre", "are", "ek", "ye", "yeh", "wo", "woh",
    "the", "a", "an", "this", "that", "it", "only", "just", "now", "abhi",
    "zara", "thoda", "mujhe", "muje", "mujhko", "hume", "humein",
    "mera", "meri", "apna", "nahi", "nahin", "nai",
    "sirf", "fakt", "kewal", "khali", "bas",
    "जी", "ना", "नहीं", "नही", "हाँ", "हां", "एक", "ये", "यह", "वो", "वह",
    "अभी", "जरा", "थोड़ा", "मुझे", "मुझको", "हमें", "मेरा", "मेरी", "अरे",
    "क्या", "आप", "तुम", "कृपया", "सिर्फ", "केवल", "खाली", "बस",
    "હા", "ના", "નથી", "નહીં", "નહિ", "મને", "હું", "તમે", "જરા", "એક",
    "ફક્ત", "માત્ર", "બસ", "ખાલી", "પ્લીઝ", "જી",
])



# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------


def _validate_response(text: str, expected_lang: str = "en",
                       max_len: int = MAX_RESPONSE_LENGTH) -> str:
    if not text or not text.strip():
        return _RESPONSE_FALLBACK
    # llm_config.max_response_length is finally wired (D8); values below the
    # historical effective constant are clamped up to it — the sub-400 values
    # in old YAMLs were never in effect and must not start truncating now.
    max_len = max(int(max_len or MAX_RESPONSE_LENGTH), MAX_RESPONSE_LENGTH)
    cleaned = _MARKDOWN_RE.sub("", text)
    cleaned = _EMOJI_RE.sub("", cleaned).strip()
    if len(cleaned) > max_len:
        truncated = cleaned[:max_len]
        last_sentence = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
        if last_sentence > max_len // 2:
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
        # Agent definition drives all domain-specific behaviour. It MUST be
        # explicit: the old `agent_def or get_pramukh_agent()` fallback meant a
        # failed tenant-agent load silently answered the call as Vishal Patel
        # from Pramukh Group. Callers that want Pramukh (CLI default) pass
        # get_pramukh_agent() themselves; the telephony bridge substitutes the
        # neutral fallback agent on load failure.
        if agent_def is None:
            raise ValueError(
                "ConversationRouter requires an explicit agent_def — pass "
                "get_pramukh_agent() for the CLI default or "
                "get_neutral_fallback_agent() for a degraded call."
            )
        self._def: AgentDefinition = agent_def
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
        # Gemini 3.x models "think" by default and thinking tokens count against
        # max_tokens, so short-budget calls (warmup max_tokens=1, language
        # classify max_tokens=5) come back empty. LLM_REASONING_EFFORT=none
        # disables it. Primary client only — the OpenAI fallback (gpt-4o-mini)
        # rejects the param.
        _reff = os.environ.get("LLM_REASONING_EFFORT", "").strip()
        self._llm_extra: dict = {"reasoning_effort": _reff} if _reff else {}
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
        # Call-repair state (platform rules, all agents): early-call "hello?"
        # replay window anchor + replay counter, see _is_early_hello().
        self._opening_ts: float = 0.0
        self._hello_replays: int = 0
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
        # Callback context — why this (outbound) call is being made, carried
        # from the original call's notes (call_links.context_note). Rendered
        # as a WHY-YOU-ARE-CALLING-BACK prompt block when non-empty.
        self.callback_context: str = ""
        # Structured metadata for a platform-scheduled follow-up call
        # (call_links.callback_meta, stamped by the auto-dial executor):
        # {reason, topic, requested_when, source_call_at}. When present, the
        # opening line references the earlier conversation instead of the
        # cold outbound greeting.
        self.callback_meta: dict = {}
        # Agent-initiated hangup (platform rule, all agents): set ONLY on the
        # truly-final close paths (farewell exchange, wrong number, DND /
        # decline threshold, LLM-failure graceful close, close-permission
        # confirm). The telephony bridge polls this after the final utterance
        # drains and tears the channel down. NOT the same as state.is_closing,
        # which also fires on booking-confirmed while the conversation is
        # still live.
        self.hangup_pending: bool = False
        # Persistent customer memory (returning caller), set by the bridge via
        # set_customer_context(). Empty dict = unknown caller = no band.
        self.customer_context: dict = {}
        # v2 branch bookkeeping: per-call take counters (loop prevention) and
        # deterministic side effects queued by branch actions.
        self._branch_takes: dict = {}
        self._slot_confirm_turn: dict[str, int] = {}
        self._whatsapp_promised: bool = False
        self._callback_requested: str = ""
        self.call_outcome: str = ""
        # v3 in-call commitment clarification (flag-gated per agent via
        # follow_up.clarify_commitments.enabled; PLAN-intelligence-layer §2.8).
        # Typed commitments ride extra_flags["commitments"] → collected_data →
        # the post-call pipeline, which schedules clarified ones precisely.
        self._commitments: list[dict] = []
        self._clarify_asked: bool = False
        self._pending_clarify: dict | None = None
        # While > 0, _extract_time_preference is suppressed: the answer to
        # "what day works?" must refine the commitment, NOT create a calendar
        # booking (phantom-booking doctrine).
        self._suppress_time_match_turns: int = 0

        # Conversation State Management layer — per-call structured memory:
        # extraction, merge, audit trail, missing-slot detection, and the
        # single authoritative state block injected into every system prompt.
        from .state_manager import build_state_manager
        self.state_mgr = build_state_manager(self._def, language=default_language)
        # Fallback LLM (lazy; see _get_fallback_client).
        self._fallback_client: AsyncOpenAI | None = None
        self._fallback_model: str = "gpt-4o-mini"
        # Per-call fidelity record: which model(s) actually spoke, and how
        # many turns rode the fallback. Persisted into the calls table by the
        # telephony bridge — the historical log holds 666 silent mid-call
        # switches to the US-hosted fallback with only a WARNING line.
        self.llm_models_used: list[str] = [self._model]
        self.llm_fallback_turns: int = 0
        # Filler frequency governors (llm_config; 0/0 = uncapped = today).
        self._fillers_played: int = 0
        self._last_filler_turn: int = -999
        # Consecutive-LLM-failure streak (env LLM_FAILURE_STREAK_LIMIT,
        # 0/unset = off = today's endless canned-fallback behaviour).
        self._llm_failure_streak: int = 0
        # One-turn LLM directive armed by the language-switch handler when the
        # caller's switch request carries a real ask (or names the language
        # already active) — consumed by _call_llm_stream, cleared every turn.
        self._turn_lang_directive: str | None = None
        # Async LLM slot-extractor bookkeeping (strong ref; latest turn only).
        self._slot_extract_task: asyncio.Task | None = None
        self._slot_extract_enabled = (
            os.environ.get("ENABLE_LLM_SLOT_EXTRACTOR", "true").lower() != "false"
        )
        # Turn transaction (continuation merge): snapshot of pre-turn state,
        # open from process_stream entry until commit or rollback. See
        # turn_transaction.py — new per-turn-mutable fields must be added to
        # its capture surface.
        self._continuation_enabled = (
            os.environ.get("ENABLE_CONTINUATION_MERGE", "false").lower() == "true"
        )
        self._pending_turn = None
        self._deferred_bookings: list[str] = []

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

    def set_callback_context(self, context_note: str,
                             meta: dict | None = None) -> None:
        """Context for a scheduled callback (why we're calling back) —
        rendered as a WHY-YOU-ARE-CALLING-BACK block in the system prompt.
        `meta` (call_links.callback_meta, from the auto-dial executor) makes
        the OPENING line itself reference the earlier call."""
        self.callback_context = (context_note or "").strip()
        if isinstance(meta, dict) and meta:
            self.callback_meta = dict(meta)

    def set_customer_context(self, ctx: dict | None) -> None:
        """Persistent customer memory for a RETURNING caller, pre-fetched by
        the telephony bridge (customer_memory.context_digest + thin header).
        Rendered as the ━━ KNOWN CUSTOMER ━━ prompt band; the known name also
        prefills the no-re-ask slot and the {contact_name} greeting
        placeholder. Per-agent opt-out: memory.use_customer_context: false."""
        if not ctx:
            return
        if not (getattr(self._def, "memory", None) or {}).get(
                "use_customer_context", True):
            logger.info("[CONTEXT] customer context suppressed by agent flag")
            return
        self.customer_context = dict(ctx)
        name = (ctx.get("name") or "").strip()
        if name and not self.outbound_contact_name:
            self.outbound_contact_name = name
            if not self.memory.call_state.customer_name:
                self.memory.call_state.customer_name = name
        logger.info("[CONTEXT] known customer: name=%r calls=%s digest=%dch",
                    name or None, ctx.get("calls_count"),
                    len(ctx.get("digest") or ""))

    def _stage_def(self, stage_id: str | None = None):
        """StageDefinition for a stage id (default: the current stage)."""
        sid = stage_id if stage_id is not None else self.state.stage
        return next((s for s in self._def.call_flow.stages if s.id == sid), None)

    def _stage_role(self, stage_id: str | None = None) -> str:
        """Semantic role of a stage: explicit StageDefinition.role wins,
        else derived from the legacy id (v2 role shim)."""
        sd = self._stage_def(stage_id)
        explicit = (getattr(sd, "role", "") or "") if sd else ""
        if explicit:
            return explicit
        sid = stage_id if stage_id is not None else self.state.stage
        return _derive_stage_role(sid)

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
        """Persist a booked slot so later calls see it as taken. Best-effort.

        While a turn transaction is open (continuation merge), the write is
        DEFERRED to commit_pending_turn(): a continuation may roll this turn
        back, and add_booking has no undo. memory.booking is set immediately
        either way, so the in-call flow never re-offers slots.
        """
        if self._continuation_enabled and self._pending_turn is not None:
            self._deferred_bookings.append(appointment_time)
            logger.info(
                "[CALENDAR] Booking %r deferred until turn commit "
                "(continuation window open)", appointment_time,
            )
            return
        self._write_calendar_booking(appointment_time)

    def _write_calendar_booking(self, appointment_time: str) -> None:
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
    # Turn transactions (continuation merge)
    # ------------------------------------------------------------------
    # The pipeline drives these: begin_turn_transaction at every
    # process_stream entry; rollback_pending_turn when a continuation of the
    # previous utterance arrives; commit_pending_turn when the turn is final
    # (next distinct turn, window expiry, closing shortcut, pipeline stop).

    def begin_turn_transaction(self, text: str) -> None:
        if not self._continuation_enabled:
            return
        if self._pending_turn is not None:
            self.commit_pending_turn()
        try:
            from .turn_transaction import capture
            self._pending_turn = capture(self, text)
        except Exception:
            logger.exception("[TURN-TXN] capture failed — turn not supersedable")
            self._pending_turn = None

    def has_pending_turn(self) -> bool:
        return self._pending_turn is not None

    def rollback_pending_turn(self) -> str | None:
        """Rewind the provisional turn; returns its user text for merging."""
        snap = self._pending_turn
        if snap is None:
            return None
        self._pending_turn = None
        # A stale extractor completion must not land after the restore.
        task = self._slot_extract_task
        if task is not None and not task.done():
            task.cancel()
        try:
            from .turn_transaction import restore
            restore(self, snap)
        except Exception:
            logger.exception("[TURN-TXN] restore failed — keeping current state")
            return None
        return snap.user_text

    def commit_pending_turn(self) -> None:
        self._pending_turn = None
        if self._deferred_bookings:
            pending, self._deferred_bookings = self._deferred_bookings, []
            for t in pending:
                self._write_calendar_booking(t)
                logger.info("[TURN-TXN] Committed deferred booking %r", t)

    # ------------------------------------------------------------------
    # Opening greeting (outbound call starts here)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Call repair (platform rules — every agent, every call)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_bare_hello_text(text: str) -> bool:
        """Is this utterance nothing but hello(s) plus filler, in any supported
        script? Script-safe tokenisation, no time window."""
        # NOT [\W_]: re's \W matches Indic combining vowel signs (category
        # Mn), which would shred "हेलो" into bare consonants. Keep the
        # Devanagari + Gujarati blocks whole.
        toks = re.sub(r"[^0-9a-zऀ-ॿ઀-૿]+", " ",
                      (text or "").lower()).split()
        if not toks or len(toks) > 4:
            return False
        if not any(t in _HELLO_CORE for t in toks):
            return False
        return all(t in _HELLO_CORE or t in _HELLO_FILLER for t in toks)

    def _is_early_hello(self, text: str) -> bool:
        """True when this utterance is a bare "hello?" (any supported script,
        optionally repeated / with filler) inside the early-call window.

        On a phone call that pattern means the caller did not hear what we
        just said — the right response is to say it again, not to treat
        "hello" as a conversational turn."""
        if self._opening_ts <= 0.0:
            return False
        if time.monotonic() - self._opening_ts > EARLY_HELLO_WINDOW_S:
            return False
        return self._is_bare_hello_text(text)

    def generate_silence_reprompt(self) -> str:
        """Dead-air repair: the caller has been silent well past their turn —
        most likely STT missed their reply (or they never heard us). Say we
        couldn't hear them and repeat the question; if they have not spoken
        at all yet, replay the last utterance plain (saying "I couldn't hear
        you" to someone who said nothing would be wrong). Returns "" when
        there is nothing sensible to say. Called by the pipeline watchdog."""
        last = (self.last_agent_response or "").strip()
        if not last:
            return ""
        if self.state.turn_count == 0:
            text = last
        else:
            phrase = _COULDNT_HEAR.get(self.state.language, _COULDNT_HEAR["en"])
            if len(last) <= 220:
                text = f"{phrase} {last}"
            else:
                text = f"{phrase} {_SAY_AGAIN.get(self.state.language, _SAY_AGAIN['en'])}"
        # Record the reprompt so the LLM knows it happened — but do NOT
        # overwrite last_agent_response: a second reprompt must repeat the
        # original question, not compound "couldn't hear you" prefixes.
        self.memory.add_assistant(text, language=self.state.language)
        return text

    def _callback_opening(self) -> str | None:
        """Opening line for a platform-scheduled follow-up call: warmly
        reference the earlier conversation and the caller's own callback
        request, then ask if now is a good time. Deterministic (no LLM on the
        latency-critical opening); language = the agent's opening language."""
        meta = self.callback_meta or {}
        if not meta:
            return None
        lang = (self.state.language or "en")[:2]
        name = (self.outbound_contact_name or "").strip()
        agent = (getattr(self._def.identity, "agent_name", "") or "").strip()
        company = (getattr(self._def.identity, "company_name", "") or "").strip()
        # How long ago the original call was, for a natural time reference.
        when_key = "recent"
        src = str(meta.get("source_call_at") or "")
        try:
            src_dt = datetime.fromisoformat(src.replace("Z", "+00:00"))
            if src_dt.tzinfo is None:
                src_dt = src_dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
            now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
            age = now_ist - src_dt
            if age <= timedelta(hours=3):
                when_key = "just_now"
            elif src_dt.astimezone(now_ist.tzinfo).date() == now_ist.date():
                when_key = "today"
            elif (now_ist.date() - src_dt.astimezone(now_ist.tzinfo).date()).days == 1:
                when_key = "yesterday"
            else:
                when_key = "recent"
        except (ValueError, TypeError):
            pass
        when = {
            "en": {"just_now": "a little while ago", "today": "earlier today",
                   "yesterday": "yesterday", "recent": "a few days ago"},
            "hi": {"just_now": "abhi thodi der pehle", "today": "aaj",
                   "yesterday": "kal", "recent": "kuch din pehle"},
            "gu": {"just_now": "hamna thodi var pehla", "today": "aaje",
                   "yesterday": "gai kale", "recent": "thoda divas pehla"},
        }.get(lang, {}).get(when_key) or {"just_now": "a little while ago",
                                          "today": "earlier today",
                                          "yesterday": "yesterday",
                                          "recent": "a few days ago"}[when_key]
        topic = str(meta.get("topic") or "").strip().rstrip(".")
        if len(topic) > 90:  # keep the opening breath-sized
            topic = ""
        who = f" {name}" if name else ""
        ident = " and ".join(x for x in (agent,) if x)
        frm = f" from {company}" if company else ""
        if lang == "hi":
            about = f" {topic} ke baare mein" if topic else ""
            return (f"Namaste{who}! Main {ident or 'aapka agent'}"
                    f"{' ' + company + ' se' if company else ''}. "
                    f"Humari {when} baat hui thi{about}, aur aapne mujhe call "
                    f"karne ko kaha tha — isliye aapko call kiya. "
                    f"Kya abhi baat karna theek rahega?")
        if lang == "gu":
            about = f" {topic} vishe" if topic else ""
            return (f"Namaste{who}! Hu {ident or 'tamaro agent'}"
                    f"{' ' + company + ' thi' if company else ''}. "
                    f"Aapni {when} vaat thai hati{about}, ane tame mane call "
                    f"karva kahyu hatu — etle call karyo chhe. "
                    f"Su atyare vaat kari shakiye?")
        about = f" about {topic}" if topic else ""
        return (f"Hello{who}! This is {ident or 'your assistant'}{frm}. "
                f"We spoke {when}{about}, and you'd asked me to give you a "
                f"call back — so here I am. Is now a good time to talk?")

    async def generate_opening(self) -> EngineResult:
        """Inbound warm welcome — agent answers the call.
        Bilingual so caller can respond in their preferred language."""
        greeting = self._def.response_bank.opening_greeting
        # D6: outbound agents may carry a per-language outbound greeting —
        # preferred when this call is outbound and the field is non-empty.
        if self.call_direction == "outbound":
            _ob_greeting = self._lang_response(self._def.outbound.opening_greeting)
            if _ob_greeting:
                greeting = _ob_greeting
            # Platform rule (all agents): a scheduled follow-up call must OPEN
            # by referencing the earlier conversation and the caller's own
            # request — never with the cold outbound pitch. Built from the
            # structured callback_meta the auto-dial executor stamped on the
            # dial record; falls back to the normal greeting when absent.
            _cb_greeting = self._callback_opening()
            if _cb_greeting:
                greeting = _cb_greeting
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
        # Anchor for the early-call "hello?" repair window.
        self._opening_ts = time.monotonic()
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
        # Turn transaction: snapshot BEFORE any mutation so a continuation of
        # the caller's previous utterance can supersede this whole turn.
        self.begin_turn_transaction(text)

        # A new caller utterance cancels a pending agent hangup: "wait, one
        # more question" after the goodbye must be answered, not cut off.
        # Every close path below re-arms it if this turn also ends the call.
        if self.hangup_pending:
            logger.info("[HANGUP] caller spoke again — hangup cancelled")
            self.hangup_pending = False

        # v3 commitment clarification: the suppression window (armed when we
        # ask "what day works?") decays one notch per user turn.
        if self._suppress_time_match_turns > 0:
            self._suppress_time_match_turns -= 1

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
        self._turn_lang_directive = None

        # --- Intent detection ---
        _kb = self._def.knowledge_base
        # objection_patterns_mode 'extend' merges the universal deferral set
        # with the agent's custom patterns; 'replace' (default) keeps today's
        # semantics where any custom list disables the ~28 universal phrases
        # (load-bearing for both Pramukh agents' custom lists).
        _obj_patterns = _kb.objection_patterns
        if (_obj_patterns and str(getattr(
                _kb, "objection_patterns_mode", "replace")) == "extend"):
            _obj_patterns = list(_DEFAULT_OBJECTION_PATTERNS) + list(_obj_patterns)
        intent = detect_intent(
            text,
            self.state.stage,
            location_aliases=frozenset(_kb.location_aliases),
            objection_patterns=_obj_patterns,
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

        # ── DND: "stop calling me" is a compliance event, not an objection ──
        # Detected deterministically (platform-wide, all agents) and STORED:
        # the flag rides collected_data into the intel pipeline, which sets
        # the contact's disposition to 'rejected' (never auto-dialled again),
        # cancels open follow-ups, and forces the follow-up decision to NO.
        # The reply itself stays with the LLM — the prompt already handles
        # polite disengagement; what must never depend on the model is the
        # RECORD of the request.
        if _detect_dnd_request(text):
            if not self.state_mgr.extra_flags.get("dnd_requested"):
                self.state_mgr.extra_flags["dnd_requested"] = True
                self.state_mgr.extra_flags["dnd_quote"] = text[:200]
                logger.info("[DND] caller asked not to be contacted: %r",
                            text[:80])

        # Reset decline counter when the user re-engages with a non-objection
        # intent — we don't want a stale "1 prior decline" to trip the 2-strike
        # farewell after the user has clearly come back into the conversation.
        if intent not in ("objection", "farewell"):
            self.state.decline_count = 0

        # =============================================================
        # STAGE-AWARE ROUTING
        # =============================================================

        # --- Early-call "hello?" — caller didn't hear us; say it again ---
        # (platform rule, all agents). Capped at 2 replays so a caller who
        # actually wants to open with "hello" still gets a conversation.
        # Never replay a greeting that is ITSELF just "hello?" — the rule
        # assumes the caller missed something worth hearing again, and a bare
        # hello carries nothing. Replaying it produces the exact parrot loop
        # users report: agent "Hello?" → caller "hello" → agent "Hello?".
        # A hello answering a hello IS contact established; fall through to the
        # normal stage routing so the agent gets on with its script.
        if (self.last_agent_response and self._hello_replays < 2
                and not self._is_bare_hello_text(self.last_agent_response)
                and self._is_early_hello(text)):
            _cancel_llm()
            self._hello_replays += 1
            resp = self.last_agent_response
            logger.info("[REPAIR] Early 'hello' #%d — replaying last response: %r",
                        self._hello_replays, resp[:60])
            self.memory.add_assistant(resp, language=self.state.language)
            self._store_last_response(resp)
            yield EngineResult(text=resp, is_shortcut=True, intent="repeat")
            return

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
                # LLM sees the updated language in the system prompt; the
                # directive makes the re-render explicit so it never answers
                # with a bare "sure, let's speak X" acknowledgment.
                self._arm_lang_redeliver_directive(self.state.language)
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
        # Platform rule (all agents): the close-permission confirm counts as a
        # farewell — our previous turn asked "anything else I can help with?"
        # and the caller confirmed they're done. The caller's own goodbye and
        # this confirmed wrap-up are the ONLY paths that arm hangup_pending
        # from normal conversation: the agent never cuts a call the caller
        # hasn't agreed to end.
        # The word check backs up the classifier: "okay bye" classifies as
        # affirmation (the leading okay wins), which used to skip this branch
        # entirely and hand the goodbye to the LLM.
        if (intent == "farewell" or self._contains_farewell(text)
                or self._is_close_confirmation(text)):
            _cancel_llm()
            # v3 last-chance commitment checkpoint: if the caller is leaving
            # with a vague promise on the table ("ok bye, call me next week"),
            # ask the ONE clarification instead of the closing line — once.
            _cc_ask = self._commitment_checkpoint(text)
            if _cc_ask is not None:
                self.memory.add_assistant(_cc_ask, language=self.state.language)
                self._store_last_response(_cc_ask)
                yield EngineResult(text=_cc_ask, is_shortcut=True, intent="objection")
                return
            self.state.is_closing = True
            # Closing turns are final: the bridge may hang up right after the
            # farewell line, so never leave this turn open to rollback.
            self.commit_pending_turn()
            # D9: response_bank.closing_responses is the real farewell line
            # when present ({} = today's localized/hardcoded chain).
            _close_lang = self._closing_language()
            _closing = self._lang_response(
                self._def.response_bank.closing_responses, lang=_close_lang)
            resp = _closing or self._localized(
                "closing", "Thank you for your time! We'll be in touch. Have a great day!",
                lang=_close_lang)
            self.memory.add_assistant(resp, language=self.state.language)
            self._store_last_response(resp)
            self.state.stage = STAGE_CLOSING
            self.hangup_pending = True
            yield EngineResult(text=resp, is_shortcut=True, intent="farewell")
            return

        # --- v2 wrong-number close (outbound, gated on configured patterns).
        # Checked BEFORE objections: "wrong number" is also a default
        # objection pattern and must not burn a decline strike instead. ---
        if self.call_direction == "outbound":
            _wn_patterns = [str(p).lower() for p in
                            (self._def.outbound.wrong_number_patterns or [])]
            if _wn_patterns and any(p and p in text.lower() for p in _wn_patterns):
                _cancel_llm()
                self.state.is_closing = True
                self.commit_pending_turn()  # closing turn — never rolled back
                self.state.stage = STAGE_CLOSING
                self.call_outcome = self.call_outcome or "wrong_number"
                self.state_mgr.extra_flags["wrong_number"] = True
                self.hangup_pending = True
                resp = self._lang_response(self._def.outbound.wrong_number_close,
                                           lang=self._closing_language()) \
                    or "I'm so sorry — it seems I have the wrong number. Have a great day!"
                self.memory.add_assistant(resp, language=self.state.language)
                self._store_last_response(resp)
                yield EngineResult(text=resp, is_shortcut=True, intent="farewell")
                return

        # --- Objection at any stage (except GREETING, which has its own handler) ---
        # --- v3 commitment clarification (flag-gated; runs on ANY intent so
        # "call me next week" — classified time_preference, not objection —
        # is captured too). Resolution of a pending clarification answer wins
        # over everything below; fresh detection asks at most one question
        # per call and otherwise records + acknowledges like the callback
        # policy always did.
        if self._clarify_cfg().get("enabled"):
            _cc_resp = None
            if self._pending_clarify is not None:
                _cc_resp = self._resolve_clarification(text)
            elif self._detect_callback_request(text):
                _cc_resp = self._maybe_ask_clarification(text)
            if _cc_resp is not None:
                _cancel_llm()
                self.memory.add_assistant(_cc_resp, language=self.state.language)
                self._store_last_response(_cc_resp)
                yield EngineResult(text=_cc_resp, is_shortcut=True, intent="objection")
                return

        if intent == "objection" and self.state.stage != STAGE_GREETING:
            _cancel_llm()
            self.state.objection_handled = True

            # v2 callback policy (D10): "call me tomorrow"-style deferrals
            # capture a callback time WITHOUT booking a calendar slot.
            cb_policy = self._def.call_flow.callback_policy or {}
            if cb_policy.get("enabled") and self._detect_callback_request(text):
                cb_resp = self._handle_callback_request(text, cb_policy)
                if cb_resp is not None:
                    if cb_policy.get("counts_as_decline", True):
                        self.state.decline_count += 1
                    self.memory.add_assistant(cb_resp, language=self.state.language)
                    self._store_last_response(cb_resp)
                    yield EngineResult(text=cb_resp, is_shortcut=True, intent="objection")
                    return

            self.state.decline_count += 1

            # v2 DND (D10): immediate polite end when the utterance matches a
            # configured dnd_phrase (default [] = no behaviour change).
            _dnd = [str(p).lower() for p in
                    ((self._def.guardrails.compliance or {}).get("dnd_phrases") or [])]
            _is_dnd = any(p and p in text.lower() for p in _dnd)

            # Nth decline (per call_flow.decline_threshold; all live agents
            # carry 2 — wiring changes nothing): stop pushing, end gracefully.
            _threshold = self._def.call_flow.decline_threshold or 2
            if _is_dnd or self.state.decline_count >= _threshold:
                self.state.is_closing = True
                self.commit_pending_turn()  # closing turn — never rolled back
                self.state.stage = STAGE_CLOSING
                # Escalation handoff replaces the farewell only when enabled.
                esc = self._def.guardrails.escalation or {}
                resp = ""
                _close_lang = self._closing_language()
                if esc.get("enabled"):
                    resp = self._lang_response(esc.get("handoff_message") or {},
                                               lang=_close_lang)
                if not resp:
                    resp = self._lang_response(
                        self._def.response_bank.decline_farewell,
                        lang=_close_lang) \
                        or "Thank you for your time. Have a great day!"
                if _is_dnd:
                    self.state_mgr.extra_flags["dnd_requested"] = True
                    self.call_outcome = self.call_outcome or "dnd"
                    logger.info("[DND] caller requested do-not-disturb — ending call")
                self.hangup_pending = True
                self.memory.add_assistant(resp, language=self.state.language)
                self._store_last_response(resp)
                yield EngineResult(text=resp, is_shortcut=True, intent="farewell")
                return

            resp = self._pick_objection_response(text)
            self.memory.add_assistant(resp, language=self.state.language)
            self._store_last_response(resp)
            yield EngineResult(text=resp, is_shortcut=True, intent="objection")
            return

        # --- v2 canned phrases (deterministic, gated on configuration) ---
        for _cp in (self._def.response_bank.canned_phrases or []):
            _pats = [str(p).lower() for p in (_cp.get("patterns") or [])]
            if _pats and any(p and p in text.lower() for p in _pats):
                resp = self._lang_response(_cp.get("responses") or {})
                if resp:
                    _cancel_llm()
                    self.memory.add_assistant(resp, language=self.state.language)
                    self._store_last_response(resp)
                    yield EngineResult(text=resp, is_shortcut=True, intent="canned")
                    return

        # --- v2 greeting shortcut (opt-in: enable_greeting_shortcut) ---
        # Default false = today's behaviour (Pramukh's localized hi/gu
        # "hello" turns keep going through the LLM).
        if intent == "greeting" and self._def.response_bank.enable_greeting_shortcut \
                and self.state.stage != STAGE_GREETING:
            _greet = self._localized("greeting", "")
            if _greet:
                _cancel_llm()
                self.memory.add_assistant(_greet, language=self.state.language)
                self._store_last_response(_greet)
                yield EngineResult(text=_greet, is_shortcut=True, intent="greeting")
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
            # Chain + generated fallback: copilot-built agents ship {} here —
            # the old ["en"] access crashed "who is calling?" on those agents.
            resp = self._lang_response(self._def.response_bank.identity_responses) or (
                f"This is {self._def.identity.agent_name}"
                + (f" from {self._def.identity.company_name}" if self._def.identity.company_name else "")
                + ". How can I help you?")
            self.memory.add_assistant(resp, language=lang)
            self._store_last_response(resp)
            if self.state.stage == STAGE_GREETING:
                self.state.stage = self._post_greeting_stage()
            yield EngineResult(text=resp, is_shortcut=True, intent="identity_question")
            return

        # --- Language switch at any stage ---
        if intent == "language_switch":
            old_lang = self.state.language
            new_lang = self._extract_requested_language(text)
            logger.info(
                "[LANG] Explicit switch request -> %s (was %s, locked=%s)",
                new_lang, old_lang, self.state.language_locked,
            )
            self.state.language = new_lang
            self.state.language_locked = True
            # Start at -7 so the lock persists for 17 turns before decaying.
            # Explicit caller requests should persist but allow natural switching.
            self.state.language_lock_turns = -7
            self.state.language_history = [new_lang] * 5
            logger.info("[LANG-LOCK] explicit_switch lang=%s lock_turns=%d (persists ~17 turns)",
                        new_lang, self.state.language_lock_turns)
            # The canned ack is ONLY for a bare "change language" ask that
            # actually changes the language. When the caller names the language
            # they're already speaking ("हिंदी में बताओ" mid-Hindi-call = "say
            # THAT in Hindi"), or the request carries a real ask ("हिंदी में
            # बताओ लेस कॉल के बारे में"), the ack drops their actual question —
            # a live call answered "जी बिल्कुल, हम हिंदी में बात करते हैं।"
            # three turns straight. Those turns go to the LLM with a one-turn
            # re-deliver directive; the language state above is already set, and
            # the parallel LLM task builds its prompt only after this handler
            # returns, so the new language is what it sees.
            if new_lang == old_lang or self._lang_switch_has_content(text):
                self._arm_lang_redeliver_directive(new_lang)
                logger.info(
                    "[LANG] switch is content-bearing or already-%s — routing "
                    "to LLM for re-delivery instead of canned ack", new_lang)
            else:
                _cancel_llm()
                _ls_bank = self._def.response_bank.lang_switch_responses
                resp = (_ls_bank.get(new_lang) or _ls_bank.get("hi") or _ls_bank.get("en")
                        or next(iter(_ls_bank.values()), "") or "Sure!")
                self.memory.add_assistant(resp, language=new_lang)
                # Deliberately NOT _store_last_response: the ack must never
                # become the repeat/replay target — "repeat that" after a
                # switch should replay the last substantive utterance.
                self.memory.qa_tracker.observe_agent_turn(resp, self.memory.turn_index)
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

        # v2 generic confirmation loop for merge_policy: confirm slots (the
        # literal 'name' slot is hard-excluded — its flow lives above).
        _pending_confirm = [p for p in self.state_mgr.detect_missing_slots()["pending"]
                            if p != "name"]
        for _slot in _pending_confirm:
            _since = self._slot_confirm_turn.setdefault(_slot, self.state.turn_count)
            _val = self.state_mgr.state.get(_slot)
            if intent == "affirmation" and _val is not None:
                self.state_mgr.set_externally(_slot, _val, source="system")
                self._slot_confirm_turn.pop(_slot, None)
                logger.info("[CONFIRM] Slot %s confirmed: %r", _slot, _val)
            elif intent == "negation":
                self.state_mgr.discard_pending(_slot)
                self._slot_confirm_turn.pop(_slot, None)
                logger.info("[CONFIRM] Slot %s rejected — discarded", _slot)
            elif self.state.turn_count - _since >= 2 and _val is not None:
                self.state_mgr.set_externally(_slot, _val, source="system")
                self._slot_confirm_turn.pop(_slot, None)
                logger.info("[CONFIRM] Slot %s auto-accepted after 2 turns: %r", _slot, _val)

        # Advance from GREETING on first user response
        if self.state.stage == STAGE_GREETING:
            self.state.stage = self._post_greeting_stage()
            logger.info("[STAGE] GREETING → %s", self.state.stage)

        # =============================================================
        # SOFT STAGE ADVANCEMENT based on collected data
        # =============================================================
        # v2: declarative branches first (empty = no-op), then advancement.
        if not self._maybe_branch(intent):
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
            # v3: while a commitment clarification is in flight, day/time words
            # refine the commitment — they must never book a calendar slot.
            if self._suppress_time_match_turns > 0:
                logger.info("[COMMITMENT] time-slot matching suppressed this turn")
            else:
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

    def _completed_when_met(self) -> bool:
        """call_flow.completed_when — is the call's completion condition met?"""
        cw = self._def.call_flow.completed_when or {}
        required = [str(x) for x in (cw.get("required_slots") or [])]
        if required and not self.state_mgr.collect_satisfied(required):
            return False
        if cw.get("require_appointment") and not self.memory.booking.appointment_time:
            return False
        return True

    # Recognized AND-side condition keys of advance_when. max_turns is NOT
    # one of them — it is an OR-escape evaluated in _maybe_advance_stage, so
    # {intent: affirmation, max_turns: 3} advances on the affirmation OR on
    # turn 3, and a bare {max_turns: 3} waits for turn 3 (never turn 1 via
    # the vacuous-True path).
    _AW_AND_KEYS = ("min_turns", "all_collect_filled", "slots", "intent",
                    "appointment_captured")

    def _advance_when_met(self, stage_def, aw: dict) -> bool:
        """Declarative advance_when gate (all present conditions AND-ed)."""
        if aw.get("min_turns"):
            try:
                if self.state.discovery_turns < int(aw["min_turns"]):
                    return False
            except (TypeError, ValueError):
                pass
        if aw.get("all_collect_filled"):
            if not self.state_mgr.collect_satisfied(
                    list(getattr(stage_def, "collect", None) or [])):
                return False
        if aw.get("slots"):
            if not self.state_mgr.collect_satisfied([str(s) for s in aw["slots"]]):
                return False
        if aw.get("intent"):
            if self.state.last_intent != str(aw["intent"]):
                return False
        if aw.get("appointment_captured"):
            if not self.memory.booking.appointment_time:
                return False
        return True

    def _exec_branch_action(self, action: str) -> None:
        """Deterministic side effects for branch actions (plan §4.3 item 4).
        Promises are STORED (→ calls.collected_data), not just spoken."""
        sm = self.state_mgr
        if action == "capture_phone":
            phone_slot = next(
                (n for n, s in sm.slots.items() if getattr(s, "type", "") == "phone"),
                "phone" if "phone" in sm.slots else None)
            if phone_slot:
                sm.require_now(phone_slot)
            logger.info("[BRANCH-ACTION] capture_phone (slot=%s)", phone_slot)
        elif action == "promise_whatsapp_media":
            self._whatsapp_promised = True
            sm.extra_flags["whatsapp_promised"] = True
            wp = self._def.follow_up.whatsapp_promise or {}
            if wp.get("assets"):
                sm.extra_flags["whatsapp_assets"] = list(wp["assets"])
            logger.info("[BRANCH-ACTION] promise_whatsapp_media recorded")
        elif action == "book_visit":
            # The goto stage carries the scheduling role; its machinery
            # (calendar block, bare-affirmation slot matching) takes over.
            logger.info("[BRANCH-ACTION] book_visit — scheduling machinery engaged")
        elif action == "schedule_callback":
            self._callback_requested = "requested"
            sm.extra_flags["callback_requested"] = True
            slot_name = str((self._def.call_flow.callback_policy or {})
                            .get("capture_slot") or "callback_time")
            if slot_name in sm.slots:
                sm.require_now(slot_name)
            logger.info("[BRANCH-ACTION] schedule_callback (slot=%s)", slot_name)
        else:
            logger.warning("[BRANCH-ACTION] unknown action %r ignored", action)

    def _maybe_branch(self, intent: str) -> bool:
        """v2 declarative branching — evaluated just before turn-count
        advancement. Empty branch lists = no-op (today's behaviour)."""
        sd = self._stage_def()
        branches = (getattr(sd, "branches", None) or []) if sd else []
        for i, br in enumerate(branches):
            if not isinstance(br, dict):
                continue
            matched = False
            if br.get("when_intent"):
                matched = intent == str(br["when_intent"])
            elif isinstance(br.get("when_slot"), dict):
                ws = br["when_slot"]
                actual = self.state_mgr.state.get(str(ws.get("name") or ""))
                matched = (actual is not None
                           and str(actual).strip().lower()
                           == str(ws.get("equals") or "").strip().lower())
            if not matched:
                continue
            goto = str(br.get("goto") or "")
            if not goto or self._stage_def(goto) is None:
                logger.warning("[STAGE] branch goto %r invalid — skipped", goto)
                continue
            key = (self.state.stage, i)
            try:
                max_takes = max(1, int(br.get("max_takes", 1) or 1))
            except (TypeError, ValueError):
                max_takes = 1
            if self._branch_takes.get(key, 0) >= max_takes:
                continue
            self._branch_takes[key] = self._branch_takes.get(key, 0) + 1
            for action in (br.get("actions") or []):
                try:
                    self._exec_branch_action(str(action))
                except Exception:
                    logger.exception("[BRANCH-ACTION] %r failed (continuing)", action)
            self.state.stage = goto
            self.state.discovery_turns = 0
            logger.info("[STAGE] → %s (branch)", goto)
            return True
        return False

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

        # v2 completed-call gate: advancement INTO the final stage is gated
        # only when completed_when.gate_closing is true (default off = today).
        _cw = self._def.call_flow.completed_when or {}
        _gate_closing = bool(_cw.get("gate_closing"))

        def _blocked_into_closing(target: str) -> bool:
            return _gate_closing and target == last_stage_id and not self._completed_when_met()

        # v2: completion condition met → mark the call complete (gives the
        # previously-dead booking.confirmed path its first intentional writer).
        if _cw and not b.confirmed and (
                _cw.get("required_slots") or _cw.get("require_appointment")) \
                and self._completed_when_met():
            b.confirmed = True
            self.call_outcome = self.call_outcome or "completed"
            logger.info("[STAGE] completed_when met — call marked complete")

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
            if not _blocked_into_closing(next_stage_id):
                self.state.stage = next_stage_id
                self.state.discovery_turns = 0
                logger.info("[STAGE] → %s (appointment_time set)", next_stage_id)
                return

        # v2 declarative gate: a non-empty advance_when REPLACES the legacy
        # pacing for this stage (empty = today's exact behaviour).
        aw = getattr(current_def, "advance_when", None) or {}
        if aw:
            try:
                _mx = int(aw.get("max_turns") or 0)
            except (TypeError, ValueError):
                _mx = 0
            # AND-side conditions met, OR the max_turns time-escape reached.
            # Without the escape, a never-satisfied condition strands the
            # stage forever (the Kaapd failure shape); without the
            # _AW_AND_KEYS guard, a bare {max_turns: N} would advance on
            # turn 1 via _advance_when_met's vacuous True.
            _met = (any(k in aw for k in self._AW_AND_KEYS)
                    and self._advance_when_met(current_def, aw))
            if (_met or (_mx and dt >= _mx)) and \
                    not _blocked_into_closing(next_stage_id):
                self.state.stage = next_stage_id
                self.state.discovery_turns = 0
                logger.info("[STAGE] → %s (advance_when %s)", next_stage_id,
                            "met" if _met else f"max_turns {_mx} reached")
            return

        # Goal-met advance for yes/no confirmation stages — EXPLICIT role
        # only: the derived shim maps legacy OPENING → confirm_person and
        # would silently change existing outbound-skeleton agents. No agent
        # on disk carries an explicit confirm_person role today. Reuses the
        # deterministic [INTENT] classification set earlier this turn; three
        # of the Kaapd call's six turns were affirmations wasted against an
        # already-satisfied confirm goal.
        if (getattr(current_def, "role", "") == "confirm_person"
                and self.state.last_intent == "affirmation"
                and dt >= 1
                and not _blocked_into_closing(next_stage_id)):
            self.state.stage = next_stage_id
            self.state.discovery_turns = 0
            logger.info("[STAGE] → %s (confirm_person affirmation)",
                        next_stage_id)
            return

        # Slot-aware early advancement: when the caller front-loads their
        # requirements ("3BHK in Vapi under 50 lakhs"), don't hold them in
        # DISCOVERY for the scripted turn count — advance as soon as every
        # required discovery slot is filled. Role check (v2 shim), with the
        # legacy id literal kept as fallback.
        if self._stage_role() == "discovery" or self.state.stage == STAGE_DISCOVERY:
            gaps = self.state_mgr.detect_missing_slots()
            _required = sum(1 for s in self.state_mgr.slots.values() if s.required)
            # Guards: pending counts as incomplete, and name-only agents
            # (pitch flows) never early-advance — a captured name is not
            # "discovery complete"; their pacing stays turn-count driven.
            if _required >= 2 and not gaps["missing"] and not gaps.get("pending") \
                    and not gaps.get("blocked"):
                if not _blocked_into_closing(next_stage_id):
                    self.state.stage = next_stage_id
                    self.state.discovery_turns = 0
                    logger.info("[STAGE] → %s (all required slots filled — early advance)",
                                next_stage_id)
                    return

        # Turn-count based advancement
        threshold = current_def.turns_until_advance
        if dt >= threshold and not _blocked_into_closing(next_stage_id):
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
        # v2 phone write-through: CallState.customer_phone and a phone-type
        # slot must never diverge (D5). Sync both directions.
        _phone_slot = next(
            (n for n, s in sm.slots.items() if getattr(s, "type", "") == "phone"),
            None)
        if _phone_slot:
            _slot_val = sm.state.get(_phone_slot)
            if cs.customer_phone and _slot_val is None:
                sm.merge_state({_phone_slot: cs.customer_phone},
                               source="legacy", fill_only=True)
            elif _slot_val and not cs.customer_phone:
                cs.customer_phone = str(_slot_val)
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
                    **self._llm_extra,
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
        # v2: calendar.day_synonyms consulted BEFORE the hardcoded map so
        # 'Monday 4 PM'-style slot labels finally match ({} = today).
        for canon_day, phrases in (self._def.calendar.day_synonyms or {}).items():
            for phrase in phrases:
                p = str(phrase).lower()
                if p and (p in lower or str(phrase) in text):
                    day = canon_day
                    break
            if day:
                break
        if day:
            pass
        elif re.search(r"\b(today|aaj|aaje)\b", lower) or "आज" in text or "આજે" in text:
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
        # v2 role shim: custom-id stages whose role is scheduling/confirmation
        # also arm the bare-affirmation matcher (id literals stay as fallback).
        _role = self._stage_role(stage) if stage is not None else ""
        if stage is None or stage in _scheduling_stages or \
                _role in ("scheduling", "confirmation"):
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

    def _filler_allowed(self) -> bool:
        """Filler frequency governors (llm_config.filler_max_per_call /
        filler_min_gap_turns; 0 = ungoverned = today's behaviour: a filler on
        every turn whose first LLM chunk beats filler_timeout_s — which on
        the Kaapd call was every turn). Suppressed filler = wait silently."""
        # Stage suppression (2026-07-23 filler audit): a filler before the
        # agent has even introduced itself ("One moment please... Hi, I'm
        # Prakhar from...") or glued onto the goodbye ("एक मिनट... [farewell]")
        # is pure awkwardness with no latency worth masking — both were
        # observed on real calls. First LLM turn and closing turns never
        # play a filler.
        if self.state.turn_count <= 1 or self.state.is_closing:
            return False
        cfg = self._def.llm_config
        max_per_call = int(getattr(cfg, "filler_max_per_call", 0) or 0)
        if max_per_call and self._fillers_played >= max_per_call:
            return False
        min_gap = int(getattr(cfg, "filler_min_gap_turns", 0) or 0)
        if min_gap and (self.state.turn_count - self._last_filler_turn) < min_gap:
            return False
        return True

    def _effective_filler_timeout(self) -> float:
        """Per-agent filler_timeout_s, floored platform-wide.

        Production measurement (2026-07-23 latency audit, n=12 turns): LLM
        first-chunk spans 730-1228ms, yet 8 agents configure 0.6s — so their
        fillers fired on essentially EVERY turn and, because the caller must
        sit through ~1.1-1.3s of filler audio before the (usually already
        finished) answer, each one ADDED ~a second of delay instead of
        masking any. At 1.5s, zero of the sampled turns would have played a
        filler; it fires only on genuine outliers, which is the whole point.
        The floor is env-tunable (FILLER_TIMEOUT_FLOOR_S) and never LOWERS an
        agent's own configured value.
        """
        try:
            floor = float(os.environ.get("FILLER_TIMEOUT_FLOOR_S", "1.5"))
        except ValueError:
            floor = 1.5
        return max(float(self._def.llm_config.filler_timeout_s), floor)

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
                llm_queue.get(), timeout=self._effective_filler_timeout(),
            )
        except asyncio.TimeoutError:
            if self._filler_allowed():
                filler = self._pick_filler()
                self.state.filler_used = True
                self._fillers_played += 1
                self._last_filler_turn = self.state.turn_count
                yield EngineResult(text=filler, is_shortcut=True, intent="filler")
            # Post-filler wait is BOUNDED (it used to be a bare get()): a
            # stalled-but-open LLM stream previously meant "one moment
            # please…" followed by up to ~7s of open-ended silence until the
            # httpx timeout. Cap it at stream_timeout_s and fall back to the
            # language-appropriate canned line instead. The fallback replaces
            # the stream entirely — we must not fall through into the
            # chunk loop, which would block on the same stalled queue.
            try:
                first_chunk = await asyncio.wait_for(
                    llm_queue.get(),
                    timeout=float(self._def.llm_config.stream_timeout_s),
                )
            except asyncio.TimeoutError:
                logger.warning("[LLM] Post-filler wait exhausted "
                               "stream_timeout_s — canned fallback")
                fallback = self._def.response_bank.lang_fallbacks.get(
                    self.state.language,
                    self._def.response_bank.response_fallback)
                full_parts.append(fallback)
                yield EngineResult(text=fallback, is_shortcut=False,
                                   intent=intent)
                first_chunk = None
                llm_queue = None  # sentinel: skip the streaming loop

        if first_chunk is not None and llm_queue is not None:
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
            self._llm_failure_streak = 0
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[LLM] Background stream failed")
            # Incident B (2026-07-15): a deterministic prompt-build crash is
            # indistinguishable from a network blip here — three live calls
            # answered every turn with the canned fallback. Streak tracking
            # lets the platform end the loop honestly instead of repeating.
            self._llm_failure_streak += 1
            fallback = self._def.response_bank.lang_fallbacks.get(self.state.language, self._def.response_bank.response_fallback)
            _limit = 0
            try:
                _limit = int(os.environ.get("LLM_FAILURE_STREAK_LIMIT", "0"))
            except ValueError:
                _limit = 0
            if _limit > 0 and self._llm_failure_streak >= _limit:
                logger.error(
                    "[LLM] %d consecutive failures — closing gracefully "
                    "instead of looping the canned fallback",
                    self._llm_failure_streak)
                self.call_outcome = self.call_outcome or "system_error"
                self.state.is_closing = True
                self.hangup_pending = True
                fallback = (self._lang_response(
                    self._def.response_bank.graceful_close)
                    or self._lang_response(
                        self._def.response_bank.decline_farewell)
                    or fallback)
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
        # One-turn language-switch re-delivery directive (armed by the
        # language_switch / repeat+lang handlers; also cleared at turn start).
        if self._turn_lang_directive:
            _final = self._turn_lang_directive + "\n" + _final
            self._turn_lang_directive = None
        # ONE system message, always. Gemini's OpenAI-compat endpoint keeps
        # only a single systemInstruction — with the language trailer sent as
        # a SECOND system message it silently REPLACED the entire agent
        # prompt, so since the Gemini switch every live turn ran prompt-less:
        # no persona, no knowledge, no rules (calls cf6b0e23 / 25dbbabb —
        # the model even parroted the trailer's own 'Ananya/Mr. Ben' example).
        # Merging is provider-agnostic: OpenAI-style backends honor it too.
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system + "\n\n" + _final}]
        messages.extend(self.memory.get_history_for_llm())

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
                    **self._llm_extra,
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
                # Fidelity record: a different model spoke this turn.
                if self._fallback_model not in self.llm_models_used:
                    self.llm_models_used.append(self._fallback_model)
                self.llm_fallback_turns += 1
                stream = await fb.chat.completions.create(
                    model=self._fallback_model,
                    messages=messages,
                    max_tokens=self._def.llm_config.max_tokens,
                    temperature=0.7,
                    stream=True,
                )

            buffer = ""
            timed_out = False
            # Raw model TTFT, distinct from the engine-level first_chunk metric
            # (which also includes the clause-gating below). The latency audit
            # had to reconstruct this split from a single httpx log line —
            # never again. Anchored to the pre-create timestamp (deadline was
            # computed from it) so connection+request time is included.
            _ttft_t0 = deadline - self._def.llm_config.stream_timeout_s
            _ttft_logged = False
            async for chunk in stream:
                if time.monotonic() > deadline:
                    logger.warning("[LLM-TIMEOUT] fallback triggered after %.0fs",
                                   self._def.llm_config.stream_timeout_s)
                    timed_out = True
                    break
                delta = chunk.choices[0].delta
                if delta.content:
                    if not _ttft_logged:
                        _ttft_logged = True
                        logger.info("[LLM] TTFT %.0fms",
                                    (time.monotonic() - _ttft_t0) * 1000)
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
                            yield _validate_response(sentence, self.state.language,
                                                      self._def.llm_config.max_response_length)
                            _first_emitted = True

            if timed_out:
                if buffer.strip():
                    yield _validate_response(buffer.strip(), self.state.language,
                                             self._def.llm_config.max_response_length)
                else:
                    yield (self._lang_response(self._def.response_bank.timeout_fallbacks)
                           or "Let me get back to you on that.")
            elif buffer.strip():
                yield _validate_response(buffer.strip(), self.state.language,
                                             self._def.llm_config.max_response_length)

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
            # sorted(): set order varies per process (hash seed) — pin it so
            # the prompt is deterministic (required by the golden harness).
            other_langs = ", ".join(sorted(c for c in _supported_langs if c != code))
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

        # ── Doctrine bands — ALL owned by prompt_blocks (D13) ────────────────
        from . import prompt_blocks as pb
        _personality_lines = pb.personality_block(self._def)
        _direction_lines = pb.direction_block(
            self._def, self.call_direction,
            self.outbound_contact_name, self.outbound_contact_phone,
            lang=lang, callback_context=self.callback_context)
        _free_slots = [] if b.appointment_time else self._available_calendar_slots()
        _cal_lines = pb.calendar_block(self._def, b.appointment_time, _free_slots)

        parts = [
            # Language lock FIRST — highest priority
            lang_header.get(lang, lang_header["en"]),
            *name_confirm_block,
            *pb.identity_block(self._def),
            *pb.mission_block(self._def),
            *_direction_lines,
            *pb.brand_block(self._def),
            *pb.customer_context_block(self.customer_context),
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
                    # A stage that DECLARES slots to collect gets the strong
                    # ask-next-missing-slot directive at any index. Gate is
                    # collect-only (never role: the live pramukh_agent has
                    # explicit 'discovery' roles at index 2-3 whose prompts
                    # must stay byte-identical). Empty on every stage of both
                    # Pramukh agents and every pre-existing agent.
                    or bool(getattr(self._stage_def(), "collect", None))
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
            *pb.match_rules_block(self._def, dict(self.state_mgr.state),
                                  self._stage_role()),
            *_cal_lines,
            *pb.portfolio_block(self._def, lang),
            *pb.knowledge_grounding_block(self._def),
            *pb.guidelines_block(self._def),
            *_personality_lines,
            *pb.style_block(self._def),
            *pb.anti_repetition_block(self._def),
            *pb.objection_block(self._def, lang),
            *pb.firm_rules_block(self._def, lang),
            *pb.call_closure_block(self._def, lang),
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
            elif self.state.language_history or LANG_LLM_CLASSIFY_FIRST_TURN:
                lang = await self._classify_language_llm(text_lower)
                result = (lang, 0.9, "llm")
            else:
                # First turn: never block the caller's first reply on an
                # extra LLM round-trip. STT's own detection has already been
                # mirrored into state.language by the pipeline; fall back to
                # the agent default. The en/hi/gu hard guard below still runs.
                lang = self.state.language or "hi"
                result = (lang, 0.6, "stt_or_default_first_turn")

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
                **self._llm_extra,
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

    def _closing_language(self) -> str:
        """Language for closing-flavoured lines (closing_responses,
        decline_farewell, wrong_number_close). Policy '' = today: the
        last-turn state.language — the single most misdetection-prone turn
        ('bye bye thank you' flipped the all-Hindi Kaapd call to English one
        turn before the close). 'dominant' = majority language over the
        caller's turns this call, restricted to languages.supported;
        tie-break = most recent, fallback = state.language."""
        policy = str(getattr(self._def.languages, "closing_language_policy",
                             "") or "")
        if policy != "dominant":
            return self.state.language
        supported = set(self._def.languages.supported or [])
        turns = [t for t in (getattr(self.memory, "_turns", None) or [])
                 if getattr(t, "role", "") == "user"]
        counts: dict[str, int] = {}
        for t in turns:
            tl = getattr(t, "language", "") or ""
            if tl and (not supported or tl in supported):
                counts[tl] = counts.get(tl, 0) + 1
        if not counts:
            return self.state.language
        best = max(counts.values())
        winners = {l for l, c in counts.items() if c == best}
        if len(winners) > 1:
            for t in reversed(turns):
                tl = getattr(t, "language", "") or ""
                if tl in winners:
                    return tl
        return next(iter(winners))

    def _lang_response(self, bank: dict, lang: str | None = None) -> str:
        """Per-language response chain: lang → en → first value → ''.
        Fixes the ['en'] KeyError traps for Copilot-built agents."""
        if lang is None:
            lang = self.state.language
        if not isinstance(bank, dict) or not bank:
            return ""
        val = bank.get(lang) or bank.get("en") or next(iter(bank.values()), "")
        if isinstance(val, list):
            if not val:
                return ""
            resp = val[self._response_counter % len(val)]
            self._response_counter += 1
            return str(resp)
        return str(val or "")

    def _match_objection_playbook(self, text: str) -> str | None:
        """D3: deterministic objection playbook match — a configured pattern
        hit returns the playbook answer for the caller's language."""
        playbook = getattr(self._def.response_bank, "objection_playbook", None) or []
        lower = text.lower()
        proofs = "; ".join(getattr(self._def.brand, "trust_proofs", []) or [])
        for item in playbook:
            patterns = [str(p).lower() for p in (item.get("patterns") or [])]
            if any(p and p in lower for p in patterns):
                answer = self._lang_response(item.get("answer") or {})
                if answer:
                    return answer.replace("{trust_proofs}", proofs)
        return None

    def _pick_objection_response(self, text: str = "") -> str:
        # v2: playbook first (empty playbook = today's rotation).
        if text:
            pb = self._match_objection_playbook(text)
            if pb:
                return pb
        resp = self._lang_response(self._def.response_bank.objection_responses)
        return resp or "I understand. Could I share one quick point before you go?"

    # ── v2 callback policy (D10) ────────────────────────────────────────

    _CALLBACK_TIME_RE = re.compile(
        r"\b(kal|tomorrow|parso|evening|morning|afternoon|shaam|subah|dopahar|"
        r"baje|pm|am|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"next week|baad|later|minutes?|mins?|minit|hours?|ghante|ghanta)\b"
        r"|मिनट|घंटे|घंटा|મિનિટ|કલાક", re.IGNORECASE)
    _CALLBACK_ASK_RE = re.compile(
        r"\b(call (?:me )?(?:back|later|tomorrow|after|in)|callback|call karna|"
        r"phone karna|baad mein (?:call|baat)|baad me (?:call|baat)|"
        r"phir (?:call|baat)|dobara call)\b", re.IGNORECASE)

    # "I can't talk NOW" without any callback wording — a brush-off, not an
    # objection. Routed into the callback pin-down flow (ask when to call
    # back, confirm, close) instead of the decline strike counter, which
    # would keep pitching a caller who already said they're busy.
    # Devanagari/Gujarati alternatives sit outside \b groups (matras break
    # word boundaries — the Indic regex gotcha).
    _BUSY_NOW_RE = re.compile(
        r"\b(not free|not right now|busy right now|bit busy|i'?m busy|"
        r"can'?t talk|cannot talk|in a meeting|driving right now|"
        r"abhi busy|abhi free nahi|abhi time nahi|abhi fursat nahi|"
        r"fursat nahi|abhi baat nahi|abhi nahi kar|hamna nahi|"
        r"atyare nahi|atyare busy|atyare time nathi)\b"
        r"|अभी\s*(?:busy|व्यस्त|समय\s*नहीं|बात\s*नहीं)|फ़?ुरसत\s*नहीं"
        r"|અત્યારે\s*(?:નહીં|વ્યસ્ત|સમય\s*નથી)|હમણાં\s*નહીં",
        re.IGNORECASE)

    def _detect_callback_request(self, text: str) -> bool:
        """Deferral WITH a callback flavour (time preference or 'call me
        back'), or a busy-now brush-off — both mean 'reach me later'."""
        return bool(self._CALLBACK_ASK_RE.search(text)) or (
            bool(self._CALLBACK_TIME_RE.search(text))
            and bool(re.search(r"\b(call|baat|phone)\b", text, re.IGNORECASE))
        ) or bool(self._BUSY_NOW_RE.search(text))

    # ── Close-permission confirm (platform rule, all agents) ────────────
    # The prompt doctrine makes every agent ask permission before wrapping
    # up ("Is there anything else I can help you with?"). This pair detects
    # that exchange deterministically: our last response asked the question,
    # the caller's short reply says they're done → close like a farewell.
    _CLOSE_PERMISSION_RE = re.compile(
        r"anything else|any other (?:question|help)|anything more|"
        r"aur (?:kuch|koi)|kuch aur|koi aur|और कुछ|कुछ और|और कोई|"
        r"biju kai|bija koi|kai biju|બીજું કંઈ|કંઈ બીજું|બીજો કોઈ",
        re.IGNORECASE)
    _DONE_REPLY_RE = re.compile(
        r"^(?:no+|nope|nahi+n?|nhi|na|bas|bs|नहीं|ना|बस|ના|બસ|"
        r"that'?s all|nothing(?: else| more)?|no,? than(?:ks|k you)|"
        r"i'?m (?:good|okay|fine)|all good|done|it'?s (?:okay|fine))\b",
        re.IGNORECASE)

    @staticmethod
    def _contains_farewell(text: str) -> bool:
        """Same farewell vocabulary as detect_intent — applied directly so a
        goodbye inside a longer utterance ('okay thank you, bye') closes the
        call even when the classifier scored the turn as something else."""
        lower = (text or "").lower()
        if re.search(r"\b(bye|goodbye|alvida|aavjo)\b", lower):
            return True
        return any(w in lower for w in ("अलविदा", "फिर मिलते", "આવજો", "ફરી મળીશું"))

    def _is_close_confirmation(self, text: str) -> bool:
        """True when the caller just confirmed they're done, in reply to our
        permission-to-close question. Kept deliberately strict: the reply
        must be short, open with a done-word, and ask nothing new — 'no, but
        actually...' keeps the conversation alive."""
        last = (self.last_agent_response or "")
        if not last or not self._CLOSE_PERMISSION_RE.search(last):
            return False
        t = (text or "").strip()
        if not t or len(t) > 45 or "?" in t:
            return False
        if re.search(r"\b(what|how|when|where|why|which|kya|kaise|kab|kaha|"
                     r"kaun|kitna|kitne|su|kem|ketla|but|lekin|par)\b",
                     t, re.IGNORECASE):
            return False
        return bool(self._DONE_REPLY_RE.search(t))

    @staticmethod
    def _parse_day_time(text: str) -> tuple[str, str]:
        """Deterministic (day, time) extraction from a callback phrase —
        shared by the callback policy and the v3 commitment clarifier."""
        lower = text.lower()
        day = ""
        if re.search(r"\b(today|aaj)\b", lower) or "आज" in text:
            day = "Today"
        elif re.search(r"\b(tomorrow|kal)\b", lower) or "कल" in text:
            day = "Tomorrow"
        elif re.search(r"\b(parso|day after)\b", lower):
            day = "Day after tomorrow"
        else:
            m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lower)
            if m:
                day = m.group(1).capitalize()
        tm = ""
        m = re.search(r"\b(\d{1,2})(?::\d{2})?\s*(a\.?m\.?|p\.?m\.?|baje)\b", lower)
        if m:
            tm = m.group(0)
        elif re.search(r"(morning|subah|सुबह)", lower):
            tm = "morning"
        elif re.search(r"(afternoon|dopahar|दोपहर)", lower):
            tm = "afternoon"
        elif re.search(r"(evening|shaam|शाम)", lower):
            tm = "evening"
        return day, tm

    # ── Relative callback offsets ("call me in/after 10 minutes") ──────
    # Word-numbers cover the spoken forms STT actually produces in en/hi/gu
    # code-mix; Devanagari/Gujarati numerals arrive as digits from Sarvam.
    _REL_NUM_WORDS = {
        "one": 1, "two": 2, "five": 5, "ten": 10, "fifteen": 15,
        "twenty": 20, "thirty": 30, "forty five": 45, "sixty": 60,
        "ek": 1, "do": 2, "paanch": 5, "panch": 5, "das": 10, "dus": 10,
        "pandrah": 15, "bees": 20, "tees": 30,
        "दस": 10, "पांच": 5, "पाँच": 5, "पंद्रह": 15, "बीस": 20, "तीस": 30,
        "દસ": 10, "પાંચ": 5, "પંદર": 15, "વીસ": 20, "ત્રીસ": 30,
    }
    _REL_MIN_RE = re.compile(
        r"\b(\d{1,3}|" + "|".join(_REL_NUM_WORDS) + r")\s*"
        r"(?:-|se)?\s*(?:\d{1,3}\s*)?"          # "10-15 minute" ranges → lower bound
        r"(minutes?|mins?|minit|min\b|मिनट|મિનિટ)", re.IGNORECASE)
    _REL_HOUR_RE = re.compile(
        r"\b(\d{1,2}|" + "|".join(_REL_NUM_WORDS) + r")\s*"
        r"(hours?|hrs?|ghante|ghanta|घंटे|घंटा|કલાક)", re.IGNORECASE)
    _REL_HALF_HOUR_RE = re.compile(
        r"\b(half\s+(?:an\s+)?hour|aa?dha\s+ghant|आधा\s*घंट|आधे\s*घंट|"
        r"અડધો\s*કલાક)", re.IGNORECASE)
    _REL_ONE_HOUR_RE = re.compile(
        r"\b(an\s+hour|one\s+hour|ek\s+ghanta|एक\s*घंट)", re.IGNORECASE)
    _REL_VAGUE_RE = re.compile(
        r"\b(baad|later|thodi\s+der|thoda\s+der|abhi\s+nahi|"
        r"in\s+a\s+(?:bit|while)|after\s+some\s?time|kuch\s+der)\b|"
        r"बाद|थोड़ी\s*देर|પછી|થોડી\s*વાર", re.IGNORECASE)

    @classmethod
    def _parse_relative_offset(cls, text: str) -> int | None:
        """Minutes until the requested callback, or None when the phrase has
        no usable relative offset ('call me in 10 minutes' → 10)."""
        def _num(tok: str) -> int:
            tok = tok.strip().lower()
            return int(tok) if tok.isdigit() else cls._REL_NUM_WORDS.get(tok, 0)
        if cls._REL_HALF_HOUR_RE.search(text):
            return 30
        m = cls._REL_MIN_RE.search(text)
        if m and _num(m.group(1)):
            return max(1, min(_num(m.group(1)), 24 * 60))
        m = cls._REL_HOUR_RE.search(text)
        if m and _num(m.group(1)):
            return max(1, min(_num(m.group(1)), 24)) * 60
        if cls._REL_ONE_HOUR_RE.search(text):
            return 60
        return None

    @staticmethod
    def _offset_phrase(offset_min: int, lang: str = "en") -> str:
        """'in 10 minutes' / '10 minute mein' — spoken confirmation form."""
        if offset_min % 60 == 0:
            n = offset_min // 60
            unit = {"en": ("hour", "hours"), "hi": ("ghante", "ghante"),
                    "gu": ("kalak", "kalak")}.get(lang, ("hour", "hours"))
            amount = f"{n} {unit[0] if n == 1 else unit[1]}"
        else:
            amount = f"{offset_min} minute" + ("s" if lang == "en" and offset_min != 1 else "")
        return {"en": f"in about {amount}",
                "hi": f"{amount} mein",
                "gu": f"{amount} ma"}.get(lang, f"in about {amount}")

    def _handle_callback_request(self, text: str, policy: dict) -> str | None:
        """Capture a callback time preference WITHOUT booking a calendar slot
        (never _record_calendar_booking) and reply from the policy bank."""
        day, tm = self._parse_day_time(text)
        offset_min = self._parse_relative_offset(text)
        if offset_min:
            preference = self._offset_phrase(offset_min,
                                             (self.state.language or "en")[:2])
        else:
            preference = " ".join(x for x in (day, tm) if x) or "later"
        slot_name = str(policy.get("capture_slot") or "callback_time")
        if slot_name in self.state_mgr.slots:
            self.state_mgr.set_externally(slot_name, preference, source="system")
        self.state_mgr.extra_flags["callback_requested"] = True
        self.state_mgr.extra_flags["callback_time"] = preference
        if offset_min:
            self.state_mgr.extra_flags["callback_offset_min"] = offset_min
        self.call_outcome = self.call_outcome or "callback_requested"
        logger.info("[CALLBACK] captured preference %r (no calendar booking)", preference)
        self._arm_callback_close()
        resp = self._lang_response(policy.get("responses") or {})
        return resp or (
            f"No problem at all — we'll call you back {preference}. "
            "Thank you, and have a great day!")

    # ── v3 in-call commitment clarification (PLAN-intelligence-layer §2.8) ──

    def _clarify_cfg(self) -> dict:
        try:
            return self._def.follow_up.clarify_commitments or {}
        except Exception:
            return {}

    def _sync_commitments(self) -> None:
        """extra_flags["commitments"] → collected_data → post-call pipeline."""
        self.state_mgr.extra_flags["commitments"] = list(self._commitments)

    def _record_commitment(self, quote: str, day: str, tm: str,
                           clarified: bool, offset_min: int | None = None) -> dict:
        if offset_min:
            described = self._offset_phrase(offset_min, "en")
        else:
            described = " ".join(x for x in (day, tm) if x) or "later"
        c = {
            "kind": "callback", "made_by": "customer",
            "description": f"Customer asked to be called {described}",
            "quote": quote[:300], "day": day, "time": tm,
            "offset_min": offset_min or 0,
            "vague": not (offset_min or (day and tm)), "clarified": clarified,
            "turn": self.state.turn_count,
        }
        self._commitments.append(c)
        self._sync_commitments()
        return c

    def _capture_callback_flags(self, day: str, tm: str,
                                offset_min: int | None = None) -> None:
        """The same side effects _handle_callback_request produces, so the
        outcome taxonomy and dashboard snapshots keep working unchanged."""
        if offset_min:
            preference = self._offset_phrase(offset_min,
                                             (self.state.language or "en")[:2])
        else:
            preference = " ".join(x for x in (day, tm) if x) or "later"
        cb_policy = self._def.call_flow.callback_policy or {}
        slot_name = str(cb_policy.get("capture_slot") or "callback_time")
        if slot_name in self.state_mgr.slots:
            self.state_mgr.set_externally(slot_name, preference, source="system")
        self.state_mgr.extra_flags["callback_requested"] = True
        self.state_mgr.extra_flags["callback_time"] = preference
        if offset_min:
            self.state_mgr.extra_flags["callback_offset_min"] = offset_min
        self.call_outcome = self.call_outcome or "callback_requested"

    def _arm_callback_close(self) -> None:
        """A confirmed callback IS the caller's request to end the call now —
        honouring it (confirm + goodbye, then platform hangup) is the
        consented close the supreme rule requires. The caller speaking again
        still cancels the hangup at the top of process_stream."""
        self.state.is_closing = True
        self.commit_pending_turn()  # closing turn — never rolled back
        self.state.stage = STAGE_CLOSING
        self.hangup_pending = True
        logger.info("[HANGUP] callback confirmed — closing with caller consent")

    def _clarify_question(self, day: str, tm: str, relative: bool = False,
                          busy: bool = False) -> str:
        lang = (self.state.language or "en")[:2]
        if busy:
            # "sorry, not free right now" — apologise for the timing and pin
            # down when to call back; never keep pitching a busy caller.
            bank = {"en": "No problem at all — about how many minutes later "
                          "should I call you back, or is there a time that suits you?",
                    "hi": "Koi baat nahi — main kitne minute baad call karun, "
                          "ya koi time batayein jo aapko theek lage?",
                    "gu": "Kai vandho nahi — ketli minute pachhi call karu, "
                          "ke koi time kaho je tamne thik lage?"}
        elif relative:
            # "call me in a bit / thodi der baad" — pin down the delay, not a day.
            bank = {"en": "Of course — about how many minutes should I wait "
                          "before calling you back, or is there a time that suits you?",
                    "hi": "Zaroor — main kitne minute baad call karun, ya koi "
                          "time batayein jo aapko theek lage?",
                    "gu": "Chokkas — ketli minute pachhi call karu, ke koi "
                          "time kaho je tamne thik lage?"}
        elif day and not tm:
            bank = {"en": f"Sure — what time on {day.lower()} works best for you?",
                    "hi": f"Zaroor — {day.lower()} ko kitne baje call karna theek rahega?",
                    "gu": f"Chokkas — {day.lower()} na roj ketla vage call karvo thik rahese?"}
        elif tm and not day:
            bank = {"en": "Sure — which day works best for you?",
                    "hi": "Zaroor — kis din call karna theek rahega?",
                    "gu": "Chokkas — kya divse call karvo thik rahese?"}
        else:
            bank = {"en": "Sure — which day and what time would work best for us to call you?",
                    "hi": "Zaroor — kis din aur kitne baje call karna theek rahega?",
                    "gu": "Chokkas — kya divse ane ketla vage call karvo thik rahese?"}
        return bank.get(lang, bank["en"])

    def _clarify_confirmation(self, day: str, tm: str,
                              offset_min: int | None = None) -> str:
        lang = (self.state.language or "en")[:2]
        if offset_min:
            preference = self._offset_phrase(offset_min, lang)
        else:
            preference = " ".join(x for x in (day, tm) if x) or "soon"
        # Goodbye tail included: this confirmation is a closing line — the
        # platform hangs up after it plays (_arm_callback_close).
        bank = {"en": f"Perfect — I'll call you back {preference}. "
                      "Thank you, and have a great day!",
                "hi": f"Bilkul — hum aapko {preference} call karenge. "
                      "Dhanyavaad, aapka din shubh rahe!",
                "gu": f"Saras — ame tamne {preference} call karishu. "
                      "Aabhar, tamaro divas saras rahe!"}
        return bank.get(lang, bank["en"])

    def _maybe_ask_clarification(self, text: str) -> str | None:
        """Fresh commitment detected. Vague + not yet asked → ask the ONE
        clarification; otherwise record it and acknowledge politely."""
        day, tm = self._parse_day_time(text)
        offset_min = self._parse_relative_offset(text)
        self._capture_callback_flags(day, tm, offset_min)
        if offset_min:
            self._record_commitment(text, day, tm, clarified=True,
                                    offset_min=offset_min)
            logger.info("[COMMITMENT] relative commitment captured: +%d min",
                        offset_min)
            self._arm_callback_close()
            return self._clarify_confirmation(day, tm, offset_min)
        if day and tm:
            self._record_commitment(text, day, tm, clarified=True)
            logger.info("[COMMITMENT] precise commitment captured: %s %s", day, tm)
            self._arm_callback_close()
            return self._clarify_confirmation(day, tm)
        commitment = self._record_commitment(text, day, tm, clarified=False)
        if self._clarify_asked:
            # One question per call, ever — never interrogate the caller.
            self._arm_callback_close()
            return self._clarify_confirmation(day, tm)
        self._clarify_asked = True
        self._pending_clarify = commitment
        self._suppress_time_match_turns = 2  # this turn + the answer turn
        # Busy flavour only when the brush-off carries NO time hint at all —
        # "busy right now, call me next week" still gets the day question.
        _busy = (not day and not tm
                 and bool(self._BUSY_NOW_RE.search(text))
                 and not self._CALLBACK_TIME_RE.search(text))
        _relative = not day and bool(self._REL_VAGUE_RE.search(text))
        logger.info("[COMMITMENT] vague commitment (%r) — asking clarification",
                    " ".join(x for x in (day, tm) if x) or "later")
        return self._clarify_question(day, tm, relative=_relative, busy=_busy)

    def _resolve_clarification(self, text: str) -> str | None:
        """The turn after our clarification question: fold the answer into the
        pending commitment. Unparseable answers give up gracefully — the
        commitment stays vague and the post-call engine schedules a default."""
        pending = self._pending_clarify
        self._pending_clarify = None
        if pending is None:
            return None
        offset_min = self._parse_relative_offset(text)
        if offset_min:
            pending["offset_min"] = offset_min
            pending["vague"] = False
            pending["clarified"] = True
            pending["description"] = ("Customer asked to be called "
                                      + self._offset_phrase(offset_min, "en"))
            self._sync_commitments()
            self._capture_callback_flags("", "", offset_min)
            self._suppress_time_match_turns = 1
            logger.info("[COMMITMENT] clarified: +%d min", offset_min)
            self._arm_callback_close()
            return self._clarify_confirmation("", "", offset_min)
        day, tm = self._parse_day_time(text)
        day = day or pending.get("day", "")
        tm = tm or pending.get("time", "")
        if not day and not tm:
            logger.info("[COMMITMENT] clarification answer unparseable — keeping vague")
            return None  # let the normal engine handle whatever this turn was
        pending["day"], pending["time"] = day, tm
        pending["vague"] = not (day and tm)
        pending["clarified"] = True
        pending["description"] = ("Customer asked to be called "
                                  + " ".join(x for x in (day, tm) if x))
        self._sync_commitments()
        self._capture_callback_flags(day, tm)
        self._suppress_time_match_turns = 1  # cover this turn's extraction too
        logger.info("[COMMITMENT] clarified: %s %s", day, tm)
        self._arm_callback_close()
        return self._clarify_confirmation(day, tm)

    def _commitment_checkpoint(self, farewell_text: str) -> str | None:
        """Farewell-time last chance: a vague unclarified commitment (either
        detected earlier or embedded in the farewell itself) gets the one
        clarification question instead of the closing line."""
        if not self._clarify_cfg().get("enabled") or self._clarify_asked:
            return None
        if self._detect_callback_request(farewell_text):
            return self._maybe_ask_clarification(farewell_text)
        vague = [c for c in self._commitments
                 if c.get("vague") and not c.get("clarified")]
        if not vague:
            return None
        self._clarify_asked = True
        self._pending_clarify = vague[-1]
        self._suppress_time_match_turns = 2
        logger.info("[COMMITMENT] farewell checkpoint — asking clarification")
        return self._clarify_question(vague[-1].get("day", ""),
                                      vague[-1].get("time", ""))

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
        # filler_ack_extended: recognise Devanagari/Gujarati-script
        # affirmations too — otherwise an all-Devanagari approval always
        # lands in the 'thinking' pool ('एक मिनट…' before every turn of the
        # Kaapd call). Per-call conditional union — never mutate the set
        # shared shape across agents in one process.
        if getattr(self._def.llm_config, "filler_ack_extended", False):
            _ACK_TOKENS = _ACK_TOKENS | {
                "हाँ", "हां", "जी", "हाँजी", "जीहाँ", "बिल्कुल", "बिलकुल",
                "ठीक", "अच्छा", "ज़रूर", "जरूर", "सही",
                "હા", "જી", "બરાબર", "ચોક્કસ", "જરૂર", "સારું",
            }
            # \w drops Devanagari/Gujarati combining marks ('हाँ' → 'ह'),
            # so script text must be tokenized with the marks preserved or
            # the extended tokens can never match.
            clean_words = set(re.findall(
                r"[\wऀ-ॿ઀-૿]+", last_user))
        else:
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
        # Use the YAML bank when the agent has branded thinking phrases IN THE
        # CALLER'S LANGUAGE; otherwise prefer the built-in pool for that
        # language over another language's YAML bank. The old lookup was
        # bank.get(lang, bank.get("en")) — so a Gujarati caller of an agent
        # whose YAML had only en/hi banks got an ENGLISH filler mid-Gujarati
        # conversation, and the built-in Gujarati pool below was unreachable
        # (observed live: "हाँ, एक सेकंड... ચોક્કસ, 12:00 વાગ્યેનો...").
        bank = self._def.response_bank.filler_responses
        yaml_thinking = bank.get(lang, [])
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

    def _lang_switch_has_content(self, text: str) -> bool:
        """True when a language-switch utterance carries a substantive ask
        beyond the switch itself ("हिंदी में बताओ लेस कॉल के बारे में").

        Errs toward True: the cost of a wrong True is one LLM turn instead of
        the instant canned ack; the cost of a wrong False is the ack swallowing
        the caller's actual question.
        """
        # \w alone shatters Indic tokens: Python's \w excludes matras and
        # anusvara (Mn/Mc), so "हिंदी" tokenizes as ['ह','द']. Include the
        # full Devanagari + Gujarati blocks explicitly.
        tokens = re.findall(r"[\w'’ऀ-ॿ઀-૿]+", text.lower())
        residue = [t for t in tokens if t not in _LANG_SWITCH_STOP_TOKENS]
        return sum(len(t) for t in residue) >= 4

    def _arm_lang_redeliver_directive(self, lang: str) -> None:
        """One-turn system-prompt trailer: answer the caller's actual request
        in the requested language — never a bare language acknowledgment."""
        name = {"hi": "Hindi", "gu": "Gujarati", "en": "English"}.get(
            lang, "the requested language")
        self._turn_lang_directive = (
            f"LANGUAGE SWITCH THIS TURN: the caller just asked for {name}. Do NOT "
            f"answer with a bare acknowledgment like 'sure, let's speak {name}'. "
            f"Answer their actual request fully in {name}; if they asked you to "
            f"repeat or re-explain something you already said, restate that "
            f"content in {name}."
        )

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
                **self._llm_extra,
            )
            ConversationRouter._warmup_done_flag = True
            logger.info("[CONV] LLM warmup complete (first time this process)")
        except Exception as exc:
            logger.warning("[CONV] LLM warmup failed: %s", exc)

    # ------------------------------------------------------------------
    # Localization helpers
    # ------------------------------------------------------------------

    def _localized(self, key: str, fallback: str,
                   lang: str | None = None) -> str:
        loc = self._def.response_bank.localized_responses.get(
            lang if lang is not None else self.state.language, {})
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
