"""
Conversation State Management — the single write path for qualification slots.

Problem this layer solves: callers volunteer several facts in one breath
("I want a 3BHK apartment in Vapi under 50 lakhs") and the agent must never
re-ask for any of them, must accept corrections ("actually make it a 2BHK"),
and must always know exactly which field to ask for next.

Design constraints inherited from the pipeline:
  * Extraction MUST be synchronous pure Python — the LLM response task is
    fired speculatively in parallel with routing, so any await inside the
    extraction path risks the system prompt being built from stale state.
  * `CallState` (memory.py) is the external contract — the call bridge,
    dashboard, and follow-up automation read it. This manager writes INTO
    CallState/BookingData; it does not replace them.
  * Name, phone, and appointment time keep their existing capture machinery
    (pending-confirmation flow, stage-gated rescheduling) — the manager only
    OBSERVES those slots for the audit trail, it never writes them.

Merge policy per slot:
  * A null/empty extraction never overwrites a captured value.
  * LATEST_WINS slots (location, configuration, budget, timeline, purpose,
    loan_required, …): the caller's newest statement replaces the old one and
    the change is recorded in the audit trail.
  * CORRECTION_GATED slots (project): first confirmed value is locked so a
    passing mention of another project cannot steer the call; only an explicit
    correction cue ("actually…", "nahi, X", …) unlocks it. Rejected updates
    are still recorded in the audit trail for debugging.
  * EXTERNAL slots (name, phone, appointment_time): read-only here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

from .memory import ConversationMemory
from .stt.post_processor import match_location, match_bhk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical Pramukh Group projects — moved here from conversation.py so both
# modules share one source of truth (conversation.py re-imports this).
# Keys are lowercase substrings; values are (canonical_name, location).
# ---------------------------------------------------------------------------
KNOWN_PROJECTS: dict[str, tuple[str, str]] = {
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


# ===========================================================================
# Pure entity extractors — regex/keyword only, no I/O, safe to call per turn.
# All return None when nothing was found (a None NEVER reaches the state).
# ===========================================================================

# Code-mixed callers glue a Latin keyword straight onto a Devanagari/Gujarati
# postposition — "vapiમાં" (= "in Vapi"), "loanજોઈએ", "3bhkનું". Python's \b
# treats the Indic suffix as a word character, so "\bvapi\b" never matches and
# whitespace-splitting yields one unmatchable token. Splitting at the script
# boundary FIRST turns "vapiમાં" into "vapi માં" so every extractor below sees
# a clean word. Same-script text is left untouched (the lookarounds never fire).
_SCRIPT_BOUNDARY_RE = re.compile(
    r"(?<=[A-Za-z0-9])(?=[ऀ-ॿ઀-૿])|(?<=[ऀ-ॿ઀-૿])(?=[A-Za-z0-9])"
)


def segment_mixed_script(text: str) -> str:
    """Insert a space at every Latin/digit ↔ Devanagari/Gujarati boundary."""
    return _SCRIPT_BOUNDARY_RE.sub(" ", text)


def extract_location(text: str) -> str | None:
    """Canonical city ("Surat"/"Vapi"/"Silvassa") from en/hi/gu text.

    Word-boundary matched: "khubsurat"/"खूबसूरत" (= beautiful) contains
    "surat"/"सूरत" as a substring and must never read as the city."""
    text = segment_mixed_script(text)
    loc = match_location(text)
    if loc:
        return loc.capitalize()
    lower = text.lower()
    for city in ("surat", "vapi", "silvassa"):
        if re.search(r"\b" + city + r"\b", lower):
            return city.capitalize()
    for script, en in [
        ("सूरत", "Surat"), ("वापी", "Vapi"), ("सिलवासा", "Silvassa"),
        ("સુરત", "Surat"), ("વાપી", "Vapi"), ("સિલવાસા", "Silvassa"),
    ]:
        if re.search(
            r"(?<![ऀ-ॿ઀-૿])" + script + r"(?![ऀ-ॿ઀-૿])",
            text,
        ):
            return en
    return None


def extract_bhk(text: str) -> str | None:
    """Configuration like "3 BHK" from en/hi/gu text ("3bhk", "तीन बीएचके", …)."""
    bhk = match_bhk(text)
    if not bhk:
        m = re.search(r"(\d)\s*(?:bhk|bed)", text, re.IGNORECASE)
        if m and 1 <= int(m.group(1)) <= 5:
            bhk = f"{m.group(1)} bhk"
    return bhk.upper() if bhk else None


def extract_budget(text: str) -> str | None:
    """Budget amount/range in lakh/crore from en/hi/gu text."""
    lower = text.lower()
    # Range first so "50 to 70 lakh" doesn't collapse to "70 lakh"
    m = re.search(r"(\d+)\s*(?:to|-|se)\s*(\d+)\s*(?:lakhs?|lacs?|लाख|લાખ)", lower)
    if m:
        return f"{m.group(1)}-{m.group(2)} lakh"
    # "cr" needs a boundary — "2 credit card" must not read as "2 crore"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:crores?|cr\.|cr\b|करोड़|કરોડ|karod)", lower)
    if m:
        return f"{m.group(1)} crore"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakhs?|lacs?|लाख|લાખ|l\b)", lower)
    if m:
        return f"{m.group(1)} lakh"
    return None


def extract_timeline(text: str) -> str | None:
    """Caller's stated purchase/move-in timeline."""
    lower = text.lower()
    # NOTE: deliberately no "jaldi" — it almost always modifies the AGENT's
    # action ("details jaldi bhejo"), not the purchase timeline.
    if re.search(r"\b(immediate(?:ly)?|right away|asap|ready now|abhi|turant)\b", lower):
        return "immediate"
    if any(w in text for w in ("अभी", "तुरंत", "હમણાં", "તરત")):
        return "immediate"
    m = re.search(r"(\d+)\s*(?:months?|mahin\w*|maheen\w*|mashin\w*|महीने|महीनों|મહિના)", lower)
    if m:
        return f"{m.group(1)} months"
    m = re.search(r"(\d+)\s*(?:years?|saal|साल|વર્ષ|varsh\w*|वर्ष)", lower)
    if m:
        return f"{m.group(1)} year(s)"
    if re.search(r"\b(end of (?:the )?year|this year|is saal|is varsh|aa varshe|2025|2026)\b", lower):
        return "this year"
    if re.search(r"\b(six months?|chhe mahine|छह महीने)\b", lower):
        return "6 months"
    if re.search(r"\b(three months?|teen mahine|quarter|तीन महीने)\b", lower):
        return "3 months"
    if re.search(r"\bnext year\b|agle saal|अगले साल", lower):
        return "next year"
    return None


# Purpose keyword tables (moved verbatim from ConversationRouter so behavior
# is unchanged; word-boundary regexes prevent "returns my call" false hits).
_INVEST_EXACT = [
    "rental income", "rental return", "return on investment",
    "kiraya", "kiraye",
    "रिटर्न", "निवेश", "ભાડું", "રોકાણ",
]
_SELF_USE_EXACT = [
    "self use", "self-use", "own use",
    "apne liye", "rehna",
    "ઘર", "રહેવા", "ખુદ", "ફેમિલી",
]
_INVEST_BOUND_RE = re.compile(
    r'\b(?:investment|investing|investor|invest|appreciation|'
    r'rental income|rental return|profit|yield)\b'
    r'|\breturn on investment\b',
    re.IGNORECASE,
)
_SELF_USE_BOUND_RE = re.compile(
    r'\b(?:family|families|stay|staying|live|living|reside|residing|'
    r'ghar|makan|rahega|settle|settling)\b', re.IGNORECASE,
)


def extract_purpose(text: str) -> str | None:
    """"investment" / "self_use"."""
    lower = text.lower()
    if (any(kw in lower or kw in text for kw in _INVEST_EXACT)
            or _INVEST_BOUND_RE.search(lower)):
        return "investment"
    if (any(kw in lower or kw in text for kw in _SELF_USE_EXACT)
            or _SELF_USE_BOUND_RE.search(lower)):
        return "self_use"
    return None


# Order matters: negative ("no loan / cash") patterns are checked BEFORE
# positive ones, so "I don't want a loan" never reads as loan_required=yes.
# Negation is often clause-final in Hindi/Gujarati ("loan ki zaroorat nahi
# hai") — allow up to ~25 chars between "loan" and the negation word.
_LOAN_NO_RE = re.compile(
    r"\b(?:no loan|without (?:a )?loan|loan is not|"
    r"loan[\w\s]{0,25}?(?:nahi|nai|nathi|not needed|not required)|"
    r"(?:don'?t|do not|won'?t|not going to|no need (?:of|for))\s*(?:a\s+)?"
    r"(?:need|want|take|require)?[\w\s]{0,12}?loan|"
    r"cash(?: payment| ma| mein| me| deal)?|full payment|one[- ]?shot payment|"
    r"self[- ]?fund(?:ed|ing)?|own funds?)\b",
    re.IGNORECASE,
)
_LOAN_NO_SCRIPT = [
    "बिना लोन", "लोन नहीं", "कैश", "રોકડ", "લોન નહીં", "લોન નથી",
    "लोन की जरूरत नहीं", "जरूरत नहीं", "લોન ની જરૂર નથી", "જરૂર નથી",
]
# Bare "finance" removed — "finance department me job hai" is an occupation,
# not a loan request. Finance counts only with an action verb.
_LOAN_YES_RE = re.compile(
    r"\b(?:home loan|housing loan|bank loan|loan|emi|financing|"
    r"finance\s+(?:kar\w*|le\w*|chahiye|hoga|karva|karwa\w*))\b",
    re.IGNORECASE,
)
_LOAN_YES_SCRIPT = ["लोन", "ईएमआई", "લોન", "होम लोन"]


def extract_loan_required(text: str) -> str | None:
    """"yes" / "no" — whether the caller needs a home loan."""
    lower = text.lower()
    if _LOAN_NO_RE.search(lower) or any(w in text for w in _LOAN_NO_SCRIPT):
        return "no"
    if _LOAN_YES_RE.search(lower) or any(w in text for w in _LOAN_YES_SCRIPT):
        return "yes"
    return None


_RENT_RE = re.compile(
    r"\b(?:on rent|for rent|renting|rent (?:pe|par|pe lena|par lena)|"
    r"kiraye? (?:pe|par) (?:le|chahiye|lena)|bhade)\b",
    re.IGNORECASE,
)
_BUY_RE = re.compile(
    r"\b(?:buy|buying|purchase|purchasing|kharid\w*|लेना है|खरीद\w*|ખરીદ\w*|"
    r"book(?:ing)? (?:karna|karvi|करना))\b",
    re.IGNORECASE,
)


def extract_transaction_type(text: str) -> str | None:
    """"rent" / "buy". Rent patterns are deliberately narrow — "rental income"
    means the caller is an investor (purpose), not a tenant."""
    lower = text.lower()
    if "rental income" in lower or "rental return" in lower:
        return "buy"  # investor buying to let
    if _RENT_RE.search(lower):
        return "rent"
    if _BUY_RE.search(lower) or "खरीद" in text or "ખરીદ" in text:
        return "buy"
    return None


# Occupation needs FIRST-PERSON context: "doctor ke paas jana hai" (visiting
# a doctor) or "service road ke paas" must never read as the caller's job.
_PROFESSIONS = r"(?:doctor|engineer|teacher|professor|advocate|lawyer|architect|farmer|ca)"
_OCCUPATION_PATTERNS: list[tuple[re.Pattern, str | None]] = [
    (re.compile(
        r"\b(?:businessman|(?:mera|apna|my own?)\s+\w{0,12}\s*business|"
        r"business\s+(?:karta|karti|kar(?:u|un)|hu|hoon|chu|che)|"
        r"i\s+(?:run|have|own|do)\s+(?:a\s+)?business|vyapar|dhandho|karobar)\b",
        re.IGNORECASE), "business"),
    (re.compile(
        r"\b(?:(?:job|service|naukri)\s+(?:karta|karti|hu|hoon|chu|me hu)|"
        r"(?:me|mein|me)\s+(?:job|naukri)|salaried|private job|government job|"
        r"i\s+work\s+(?:at|in|for))\b",
        re.IGNORECASE), "service"),
    (re.compile(
        r"\b(?:i\s*(?:am|'m)\s+a?\s*" + _PROFESSIONS + r"|"
        + _PROFESSIONS + r"\s+(?:hu|hoon|chu))\b",
        re.IGNORECASE), None),  # capture the profession word itself
]
_OCCUPATION_SCRIPT = {"व्यापार": "business", "नौकरी": "service", "ધંધો": "business", "નોકરી": "service"}


def extract_occupation(text: str) -> str | None:
    for script, occ in _OCCUPATION_SCRIPT.items():
        if script in text:
            return occ
    for pattern, canonical in _OCCUPATION_PATTERNS:
        m = pattern.search(text)
        if m:
            if canonical:
                return canonical
            pm = re.search(_PROFESSIONS, m.group(0), re.IGNORECASE)
            return pm.group(0).lower() if pm else m.group(0).lower()
    return None


_PROPERTY_CATEGORY_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Bare "office" excluded — "office se aa raha hu" is commuting small talk;
    # commercial intent needs shop/showroom or an office-acquisition phrase.
    (re.compile(
        r"\b(?:shop|dukaan|showroom|commercial|"
        r"office\s+(?:space|chahiye|lena|kharid\w*|joie|jo(?:it|ie)e))\b|દુકાન|दुकान",
        re.IGNORECASE), "commercial"),
    (re.compile(r"\b(?:villa|bungalow|row ?house|kothi)\b", re.IGNORECASE), "villa"),
    (re.compile(r"\b(?:plot|jameen|jamin)\b|जमीन|જમીન", re.IGNORECASE), "plot"),
    (re.compile(r"\b(?:flat|apartment)\b|फ्लैट|ફ્લેટ", re.IGNORECASE), "apartment"),
]


def extract_property_category(text: str) -> str | None:
    for pattern, category in _PROPERTY_CATEGORY_PATTERNS:
        if pattern.search(text):
            return category
    return None


def extract_project(text: str) -> tuple[str, str] | None:
    """(canonical_name, location) if a known Pramukh project is mentioned.
    Longest keys first so 'aranya ii' beats 'aranya'; word boundaries stop
    short names (aura, orbit) matching inside unrelated words."""
    lower = text.lower()
    for key in sorted(KNOWN_PROJECTS, key=len, reverse=True):
        if re.search(r'\b' + re.escape(key) + r'\b', lower):
            return KNOWN_PROJECTS[key]
    return None


# Correction cues — a caller explicitly revising earlier information.
# Used to (a) unlock CORRECTION_GATED slots and (b) annotate the audit trail
# so the prompt can tell the LLM to acknowledge the change.
# Deliberately EXCLUDED: bare "matlab"/"मतलब"/"etle ke" — ubiquitous Hindi/
# Gujarati fillers ("iska matlab kya hai?") that would unlock the project
# lock on a large fraction of ordinary turns. "mera matlab" (= "I meant")
# stays, since that phrasing is a genuine self-correction.
_CORRECTION_CUES = [
    # English
    "actually", "instead", "i meant", "i mean", "make it", "change it",
    "change that", "not that", "correction", "rather", "sorry i said",
    "no no", "wait no",
    # Hindi (romanized + Devanagari)
    "nahi nahi", "nai nai", "mera matlab", "badal do",
    "badalna", "ki jagah", "नहीं नहीं", "मेरा मतलब", "बदल", "की जगह", "गलत बताया",
    # Gujarati (romanized + script)
    "na na", "badli", "badlo", "ના ના", "બદલો", "બદલી",
]


def detect_correction(text: str) -> bool:
    lower = text.lower()
    return any(cue in lower or cue in text for cue in _CORRECTION_CUES)


# Rejection context — the caller is declining/dismissing something mentioned
# in the same breath. A project named inside a rejection ("no no, Aranya is
# too far") must never be SELECTED, correction cue or not.
_REJECTION_MARKERS = [
    "too far", "too expensive", "costly", "expensive", "don't want",
    "dont want", "don't like", "dont like", "not interested", "avoid",
    "nahi chahiye", "nai chahiye", "pasand nahi", "door hai", "bahut door",
    "mehenga", "mehnga", "rehne do", "chhodo", "chodo", "cancel",
    "नहीं चाहिए", "पसंद नहीं", "दूर है", "महंगा", "रहने दो", "छोड़ो",
    "nathi joitu", "nathi gamtu", "dur che", "monghu", "નથી જોઈતું",
    "પસંદ નથી", "દૂર છે", "મોંઘું",
]


def detect_rejection(text: str) -> bool:
    lower = text.lower()
    return any(m in lower or m in text for m in _REJECTION_MARKERS)


# Overwrite guards for LATEST_WINS slots that false-positive easily.
# A NEW value may always FILL an empty slot; REPLACING an existing value
# additionally requires the utterance to actually be about that topic
# (or an explicit correction cue).
_BUDGET_CONTEXT_RE = re.compile(
    r"\b(?:budget|bajat|range|under|upto|up to|within|max|maximum|tak|"
    r"ke andar|andar|comfortable|around|se kam|kharch)\b|बजट|બજેટ|तक|સુધી",
    re.IGNORECASE,
)
_PURCHASE_CONTEXT_RE = re.compile(
    r"\b(?:buy|purchase|lena|lene|kharid\w*|book|plan|chahiye|joie|"
    r"jo(?:it|ie)e|shift|possession|move)\b|खरीद|लेना|ખરીદ|લેવ",
    re.IGNORECASE,
)


def extract_entities(text: str, intent: str = "general") -> dict[str, str]:
    """Run every slot extractor over one utterance. Returns ONLY captured
    (non-null) values — a slot the utterance didn't mention is simply absent,
    so merging can never overwrite good data with null."""
    if not text or not text.strip():
        return {}
    # Split code-mixed tokens ("vapiમાં" → "vapi માં") once so every extractor
    # below sees clean word boundaries regardless of script mixing.
    text = segment_mixed_script(text)
    entities: dict[str, str] = {}
    if (loc := extract_location(text)):
        entities["location"] = loc
    if (bhk := extract_bhk(text)):
        entities["configuration"] = bhk
    if (budget := extract_budget(text)):
        entities["budget"] = budget
    if (timeline := extract_timeline(text)):
        entities["timeline"] = timeline
    if (purpose := extract_purpose(text)):
        entities["purpose"] = purpose
    if (loan := extract_loan_required(text)):
        entities["loan_required"] = loan
    if (txn := extract_transaction_type(text)):
        entities["transaction_type"] = txn
    if (occ := extract_occupation(text)):
        entities["occupation"] = occ
    if (cat := extract_property_category(text)):
        entities["property_category"] = cat
    if (proj := extract_project(text)):
        entities["project"] = proj[0]
    return entities


# ===========================================================================
# Slot registry
# ===========================================================================

FILL_ONLY = "fill_only"                # first captured value wins
LATEST_WINS = "latest_wins"            # newest caller statement wins (audited)
CORRECTION_GATED = "correction_gated"  # locked unless an explicit correction cue
EXTERNAL = "external"                  # owned by other machinery; observe only


@dataclass(frozen=True)
class SlotSpec:
    key: str
    label: str                       # how the prompt refers to it
    policy: str
    required: bool = False
    priority: int = 99               # ask order — lower asks first
    qa_field: str | None = None      # QATracker field name (ask-fatigue)
    # Suggested question per language — guidance for the LLM, and a usable
    # deterministic fallback. {name} is filled when the caller's name is known.
    questions: dict[str, str] = field(default_factory=dict)


# Priority mirrors the tuned sales flow that already lived in the prompt:
# name → city → configuration → budget → purpose → timeline → loan → phone.
SLOT_SPECS: dict[str, SlotSpec] = {s.key: s for s in [
    SlotSpec(
        key="name", label="Name", policy=EXTERNAL, required=True, priority=1,
        qa_field="name",
        questions={
            "en": "By the way, may I know your name?",
            "hi": "वैसे, आपका नाम जान सकता हूँ?",
            "gu": "તમારું નામ જાણી શકું?",
        },
    ),
    SlotSpec(
        key="location", label="City (Surat / Vapi / Silvassa)",
        policy=LATEST_WINS, required=True, priority=2, qa_field="location",
        questions={
            "en": "Which city are you looking in — Surat, Vapi, or Silvassa?",
            "hi": "आप किस शहर में देख रहे हैं — सूरत, वापी या सिलवासा?",
            "gu": "તમે કયા શહેરમાં જોઈ રહ્યા છો — સુરત, વાપી કે સિલવાસા?",
        },
    ),
    SlotSpec(
        key="configuration", label="Configuration (1/2/3 BHK)",
        policy=LATEST_WINS, required=True, priority=3, qa_field="bhk",
        questions={
            "en": "What configuration would suit you — 2 BHK or 3 BHK?",
            "hi": "आपको कौन सा configuration चाहिए — 2 BHK या 3 BHK?",
            "gu": "તમને કયું configuration જોઈએ — 2 BHK કે 3 BHK?",
        },
    ),
    SlotSpec(
        key="budget", label="Budget (comfortable range — ask warmly)",
        policy=LATEST_WINS, required=True, priority=4, qa_field="budget",
        questions={
            "en": "And what budget range are you comfortable with?",
            "hi": "और आपका budget किस range में है?",
            "gu": "અને તમારું budget કઈ range માં છે?",
        },
    ),
    SlotSpec(
        # CORRECTION_GATED: the self-use keywords (family/live/ghar/rehna)
        # appear constantly in small talk ("family ke saath rehta hu"), so a
        # confirmed purpose only changes on an explicit correction.
        key="purpose", label="Purpose (self-use or investment)",
        policy=CORRECTION_GATED, required=True, priority=5, qa_field="purpose",
        questions={
            "en": "Is this for your own family, or as an investment?",
            "hi": "यह अपने रहने के लिए है या investment के लिए?",
            "gu": "આ પોતાના રહેવા માટે છે કે investment માટે?",
        },
    ),
    SlotSpec(
        key="timeline", label="Timeline (when planning to buy)",
        policy=LATEST_WINS, required=True, priority=6, qa_field="timeline",
        questions={
            "en": "When are you planning to purchase?",
            "hi": "आप कब तक लेने का सोच रहे हैं?",
            "gu": "તમે ક્યાં સુધીમાં લેવાનું વિચારો છો?",
        },
    ),
    SlotSpec(
        key="loan_required", label="Loan requirement (loan or self-funded — ask softly)",
        policy=LATEST_WINS, required=True, priority=7, qa_field="loan",
        questions={
            "en": "Will you be taking a home loan, or is it self-funded?",
            "hi": "आप home loan लेंगे या self-funded रहेगा?",
            "gu": "તમે home loan લેશો કે self-funded રહેશે?",
        },
    ),
    SlotSpec(
        key="phone", label="Phone / WhatsApp", policy=EXTERNAL,
        required=True, priority=8,
        questions={
            "en": "Could you share your WhatsApp number so I can send you the details?",
            "hi": "आपका WhatsApp नंबर मिल सकता है? Details भेज दूँगा।",
            "gu": "તમારો WhatsApp નંબર મળી શકે? Details મોકલી દઈશ.",
        },
    ),
    # Optional slots — captured when volunteered, never asked for explicitly.
    SlotSpec(key="transaction_type", label="Buy or rent", policy=LATEST_WINS),
    SlotSpec(key="occupation", label="Occupation", policy=LATEST_WINS),
    SlotSpec(key="property_category", label="Property category", policy=LATEST_WINS),
    SlotSpec(key="project", label="Project", policy=CORRECTION_GATED),
    SlotSpec(key="appointment_time", label="Visit time", policy=EXTERNAL),
]}

_ASKABLE_KEYS = sorted(
    (s.key for s in SLOT_SPECS.values() if s.required),
    key=lambda k: SLOT_SPECS[k].priority,
)


def _mask_value(key: str, value: str | None) -> str | None:
    """Phone numbers must never leave the process in full — the audit trail
    is exported via snapshot() (web console, conversation_state.json)."""
    if value and key == "phone":
        return "***" + str(value)[-4:]
    return value


def _redact_digits(text: str) -> str:
    """Strip long digit runs (spoken phone numbers, often chunked like
    '98765 43210') from utterance snippets stored in the audit trail."""
    return re.sub(r"\d[\d\s\-]{5,}\d", "…", text)


@dataclass
class SlotChange:
    """One entry in the audit trail."""
    field: str
    old: str | None
    new: str | None
    turn: int
    source: str                  # "regex" / "external" / "llm"
    timestamp: float
    accepted: bool = True        # False = blocked by merge policy (e.g. project lock)
    correction: bool = False     # caller used an explicit correction cue
    utterance: str = ""

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "old": self.old,
            "new": self.new,
            "turn": self.turn,
            "source": self.source,
            "timestamp": self.timestamp,
            "accepted": self.accepted,
            "correction": self.correction,
            "utterance": self.utterance,
        }


# ===========================================================================
# The manager
# ===========================================================================

class ConversationStateManager:
    """Per-call state authority. One instance per ConversationRouter.

    Wraps the existing ConversationMemory (BookingData + CallState) and the
    router's ConversationState so every slot has exactly one write path with
    a merge policy, and every mutation lands in the audit trail.
    """

    # Slots whose canonical value lives in more than one legacy store; the
    # manager writes ALL locations so `b.X or cs.X` readers stay consistent.
    def __init__(self, memory: ConversationMemory, router_state=None) -> None:
        self.memory = memory
        self.router_state = router_state       # ConversationState (duck-typed)
        self.audit_log: list[SlotChange] = []
        self._summary: str = ""
        self._last_external: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    # Slot access — the ONLY place that knows where each slot is stored
    # ------------------------------------------------------------------

    def read_slot(self, key: str) -> str | None:
        b = self.memory.booking
        cs = self.memory.call_state
        rs = self.router_state
        if key == "name":
            return b.user_name or cs.customer_name
        if key == "phone":
            return cs.customer_phone
        if key == "location":
            return b.location or cs.selected_location
        if key == "configuration":
            return b.property_type or cs.property_type
        if key == "budget":
            return b.budget or cs.budget
        if key == "timeline":
            return cs.timeline
        if key == "purpose":
            return (rs.purpose if rs is not None else None) or cs.purpose
        if key == "loan_required":
            return cs.loan_required
        if key == "transaction_type":
            return cs.transaction_type or b.transaction_type
        if key == "occupation":
            return cs.occupation
        if key == "property_category":
            return cs.property_category
        if key == "project":
            return cs.selected_project or b.project
        if key == "appointment_time":
            return b.appointment_time or cs.appointment_time
        return None

    def _write_slot(self, key: str, value: str) -> None:
        """Write-through to every legacy store that mirrors this slot."""
        b = self.memory.booking
        cs = self.memory.call_state
        rs = self.router_state
        if key == "location":
            b.location = value
            cs.selected_location = value
        elif key == "configuration":
            b.property_type = value
            cs.property_type = value
        elif key == "budget":
            b.budget = value
            cs.budget = value
        elif key == "timeline":
            cs.timeline = value
        elif key == "purpose":
            cs.purpose = value
            if rs is not None:
                rs.purpose = value
        elif key == "loan_required":
            cs.loan_required = value
        elif key == "transaction_type":
            cs.transaction_type = value
            b.transaction_type = value
        elif key == "occupation":
            cs.occupation = value
        elif key == "property_category":
            cs.property_category = value
        elif key == "project":
            cs.selected_project = value
            b.project = value
            if rs is not None:
                rs.project_recommended = value

    # ------------------------------------------------------------------
    # Ingest + merge
    # ------------------------------------------------------------------

    def _turn(self) -> int:
        if self.router_state is not None:
            return getattr(self.router_state, "turn_count", 0)
        return self.memory.turn_index

    def ingest_user_turn(self, text: str, intent: str = "general") -> list[SlotChange]:
        """Extract → merge → audit for one user utterance. Synchronous and
        exception-safe: a failure here must never break the call."""
        try:
            changes: list[SlotChange] = []
            changes += self.observe_external(utterance=text)
            entities = extract_entities(text, intent)
            correction = detect_correction(text)
            rejection = detect_rejection(text)
            for key, value in entities.items():
                # A project named inside a rejection ("no no, Aranya is too
                # far") must never be selected — extraction sees the mention,
                # not the polarity, so the polarity is checked here.
                veto = key == "project" and rejection
                # Easily-false-positive slots may always FILL, but REPLACING
                # a captured value needs the turn to actually be on-topic
                # (price questions must not rewrite the caller's budget; a
                # "jaldi bhejo"-style hurry-up must not rewrite the timeline).
                allow_overwrite = True
                if (key == "budget" and not correction
                        and not _BUDGET_CONTEXT_RE.search(text)):
                    allow_overwrite = False
                if (key == "timeline" and value == "immediate" and not correction
                        and not _PURCHASE_CONTEXT_RE.search(text)):
                    allow_overwrite = False
                change = self.merge_slot(
                    key, value, source="regex",
                    correction=correction, utterance=text,
                    allow_overwrite=allow_overwrite, veto=veto,
                )
                if change is not None:
                    changes.append(change)
                    # A confirmed project implies its city when none is known.
                    if key == "project" and change.accepted and not self.read_slot("location"):
                        proj = extract_project(text)
                        if proj:
                            loc_change = self.merge_slot(
                                "location", proj[1], source="regex",
                                correction=False, utterance=text,
                            )
                            if loc_change is not None:
                                changes.append(loc_change)
            if changes:
                self._refresh_summary()
            return changes
        except Exception:  # pragma: no cover — defensive: never break a call
            logger.exception("[STATE-MGR] ingest_user_turn failed")
            return []

    def merge_slot(
        self,
        key: str,
        new_value: str | None,
        source: str = "regex",
        correction: bool = False,
        utterance: str = "",
        allow_overwrite: bool = True,
        veto: bool = False,
    ) -> SlotChange | None:
        """Apply one extracted value under the slot's merge policy.
        Returns the audit entry (accepted or rejected), or None for a no-op."""
        spec = SLOT_SPECS.get(key)
        if spec is None:
            return None
        if new_value is None or not str(new_value).strip():
            return None                      # null never overwrites data
        new_value = str(new_value).strip()
        old = self.read_slot(key)
        if old == new_value:
            return None                      # restatement — nothing to do

        accepted = True
        if veto or spec.policy == EXTERNAL:
            accepted = False                 # rejected mention / observe-only slot
        elif old is not None:
            if not allow_overwrite or spec.policy == FILL_ONLY:
                accepted = False
            elif spec.policy == CORRECTION_GATED and not correction:
                accepted = False             # e.g. passing project mention while locked
            # LATEST_WINS: accepted stays True — newest caller info wins

        change = SlotChange(
            field=key, old=_mask_value(key, old), new=_mask_value(key, new_value),
            turn=self._turn(),
            source=source, timestamp=time.time(),
            accepted=accepted, correction=correction,
            utterance=_redact_digits(utterance.strip())[:120],
        )
        self.audit_log.append(change)

        if accepted:
            self._write_slot(key, new_value)
            if spec.qa_field:
                self.memory.qa_tracker.mark_extracted(spec.qa_field, new_value)
            logger.info(
                "[STATE-MGR] %s: %r -> %r (source=%s%s)",
                key, old, new_value, source,
                ", CORRECTION" if (correction and old is not None) else "",
            )
        else:
            logger.info(
                "[STATE-MGR] %s update REJECTED by %s policy: %r -> %r kept=%r",
                key, spec.policy, old, new_value, old,
            )
        return change

    def observe_external(self, utterance: str = "") -> list[SlotChange]:
        """Diff-audit slots owned by other machinery (name/phone/appointment).
        Transitions to None are ignored — the pending-confirmation flow parks
        values as None on purpose and that must not read as data loss."""
        changes: list[SlotChange] = []
        for key in ("name", "phone", "appointment_time"):
            current = self.read_slot(key)
            previous = self._last_external.get(key)
            if current and current != previous:
                change = SlotChange(
                    field=key,
                    old=_mask_value(key, previous),
                    new=_mask_value(key, current),
                    turn=self._turn(),
                    source="external", timestamp=time.time(),
                    accepted=True, correction=False,
                    utterance=_redact_digits(utterance.strip())[:120],
                )
                self.audit_log.append(change)
                changes.append(change)
                self._last_external[key] = current
                spec = SLOT_SPECS.get(key)
                if spec and spec.qa_field:
                    self.memory.qa_tracker.mark_extracted(spec.qa_field, current)
        if changes:
            self._refresh_summary()
        return changes

    # ------------------------------------------------------------------
    # Missing-slot detection + next question
    # ------------------------------------------------------------------

    def filled_slots(self) -> list[str]:
        return [k for k in _ASKABLE_KEYS if self.read_slot(k)]

    def missing_slots(self) -> list[str]:
        """Required slots with no value, in ask-priority order."""
        return [k for k in _ASKABLE_KEYS if not self.read_slot(k)]

    def askable_missing_slots(self, suppress: set[str] | None = None) -> list[str]:
        """Missing slots the agent should actually ask about: excludes slots
        under a pending confirmation (`suppress`) and slots the caller has
        already been asked about MAX_ASKS times without a usable answer."""
        suppress = suppress or set()
        qa = self.memory.qa_tracker
        result = []
        for key in self.missing_slots():
            if key in suppress:
                continue
            qa_field = SLOT_SPECS[key].qa_field
            if qa_field and qa.should_stop_asking(qa_field):
                continue
            result.append(key)
        return result

    def next_question(
        self, language: str = "en", suppress: set[str] | None = None,
    ) -> tuple[str, str, str] | None:
        """(slot_key, label, suggested_question) for the highest-priority
        missing slot, or None when qualification is complete."""
        askable = self.askable_missing_slots(suppress)
        if not askable:
            return None
        key = askable[0]
        spec = SLOT_SPECS[key]
        question = spec.questions.get(language) or spec.questions.get("en", "")
        return key, spec.label, question

    # ------------------------------------------------------------------
    # Running summary
    # ------------------------------------------------------------------

    def _refresh_summary(self) -> None:
        self._summary = self._build_summary()

    def _build_summary(self) -> str:
        get = self.read_slot
        name = get("name")
        subject = f"Customer {name}" if name else "Customer"
        wants: list[str] = []
        if get("configuration"):
            wants.append(get("configuration"))
        wants.append(get("property_category") or "property")
        core = f"{subject} wants a {' '.join(wants)}"
        if get("location"):
            core += f" in {get('location')}"
        if get("budget"):
            core += f", budget {get('budget')}"
        if get("purpose"):
            core += f", for {'investment' if get('purpose') == 'investment' else 'their own use'}"
        sentences = [core + "."]
        if get("project"):
            sentences.append(f"Project under discussion: {get('project')}.")
        if get("appointment_time"):
            sentences.append(f"Site visit planned: {get('appointment_time')}.")
        if get("timeline"):
            sentences.append(f"Purchase timeline: {get('timeline')}.")
        else:
            sentences.append("Purchase timeline not yet discussed.")
        if get("loan_required"):
            sentences.append(
                "Needs a home loan." if get("loan_required") == "yes"
                else "Self-funded, no loan needed."
            )
        else:
            sentences.append("Loan requirement not yet discussed.")
        # Externally-owned slots (name/phone/appointment) are excluded: their
        # old→new transitions come from the confirmation machinery, not from
        # a caller correction — and phone digits must not enter the prompt.
        recent_corrections = [
            c for c in self.audit_log
            if c.accepted and c.old is not None and c.source != "external"
        ]
        if recent_corrections:
            last = recent_corrections[-1]
            sentences.append(
                f"Note: caller changed {last.field} from {last.old} to {last.new}."
            )
        return " ".join(sentences)

    def running_summary(self) -> str:
        if not self._summary:
            self._refresh_summary()
        return self._summary

    # ------------------------------------------------------------------
    # Prompt context — injected into every LLM system prompt
    # ------------------------------------------------------------------

    _STATE_DUMP_KEYS = [
        "name", "phone", "project", "location", "configuration",
        "property_category", "purpose", "budget", "timeline",
        "loan_required", "transaction_type", "occupation", "appointment_time",
    ]

    _DONT_REASK_PHRASING = {
        "name": "caller's name ({v})",
        "phone": "caller's phone number (stored; do not repeat back)",
        "project": "which property ({v})",
        "location": "city/location ({v})",
        "configuration": "property size/configuration ({v})",
        "purpose": "purpose ({v})",
        "budget": "budget ({v})",
        "timeline": "timeline ({v})",
        "loan_required": "loan requirement ({v})",
        "appointment_time": "visit time ({v})",
    }

    def build_prompt_context(
        self, language: str = "en", suppress: set[str] | None = None,
    ) -> str:
        """The full state block for the system prompt: structured state dump,
        running summary, recent corrections, do-not-re-ask list, and the
        single next field to ask. Section headers match the wording the
        prompt was tuned around."""
        cs = self.memory.call_state
        lines: list[str] = ["━━ PERSISTENT CALL STATE ━━"]
        for key in self._STATE_DUMP_KEYS:
            value = self.read_slot(key)
            if key == "phone":
                shown = "captured (stored internally)" if value else "(not yet captured)"
            else:
                shown = value or "(not yet captured)"
            lines.append(f"  {key:<21}: {shown}")
        lines.append(f"  {'site_visit_requested':<21}: {cs.site_visit_requested}")
        lines.append(f"  {'booking_validated':<21}: {cs.booking_validated}")

        lines += ["", "━━ RUNNING SUMMARY ━━", f"  {self.running_summary()}"]

        turn_now = self._turn()
        corrections = [
            c for c in self.audit_log
            if c.accepted and c.old is not None
            and c.source != "external" and turn_now - c.turn <= 3
        ]
        if corrections:
            lines += ["", "━━ RECENT CORRECTIONS (latest info wins) ━━"]
            for c in corrections[-3:]:
                lines.append(
                    f"  · {c.field}: {c.old!r} → {c.new!r}. The caller CHANGED this — "
                    f"acknowledge the update naturally and use ONLY the new value."
                )

        dont_reask = []
        for key, phrasing in self._DONT_REASK_PHRASING.items():
            value = self.read_slot(key)
            if value:
                dont_reask.append(phrasing.format(v=value))
        if dont_reask:
            lines += [
                "",
                "━━ ALREADY COLLECTED — DO NOT RE-ASK ━━",
                f"You already have: {'; '.join(dont_reask)}.",
                "NEVER ask for any of these again. Use them naturally in your responses.",
            ]

        askable = self.askable_missing_slots(suppress)
        if askable:
            labels = [SLOT_SPECS[k].label for k in askable]
            nq = self.next_question(language, suppress)
            lines += [
                "",
                "━━ QUALIFICATION STATUS ━━",
                f"  Still needed (in priority order): {' → '.join(labels)}",
                f"  ⚡ NEXT FIELD TO ASK: {labels[0]}",
            ]
            if nq and nq[2]:
                lines.append(
                    f"  Suggested phrasing (adapt naturally, stay in the caller's language): \"{nq[2]}\""
                )
            lines += [
                "  After acknowledging what the caller said, ask for this field — warmly, naturally.",
                "  Do NOT skip it. Do NOT ask two questions at once.",
            ]
        else:
            lines += [
                "",
                "━━ QUALIFICATION STATUS ━━",
                "  ✅ All core qualification fields captured.",
                "  Your priority now: recommend a matching project and book the site visit.",
            ]
        return "\n".join(lines)

    def known_summary_lines(self) -> list[str]:
        """Short 'Known so far' lines for the compact CALL STATE header."""
        rows = [
            ("name", "Caller name"),
            ("purpose", "Purpose"),
            ("location", "Location interest"),
            ("configuration", "Configuration"),
            ("budget", "Budget"),
            ("timeline", "Timeline"),
            ("loan_required", "Loan required"),
            ("project", "Project discussed"),
            ("appointment_time", "Visit scheduled"),
        ]
        return [f"{label}: {self.read_slot(key)}" for key, label in rows if self.read_slot(key)]

    # ------------------------------------------------------------------
    # Serialization — event hub, dashboard, end-of-call persistence
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """JSON-safe snapshot. The phone number is masked — it must never
        leave the process in full (web console / dashboards read this)."""
        slots = {key: self.read_slot(key) for key in self._STATE_DUMP_KEYS}
        phone = slots.get("phone")
        if phone:
            slots["phone"] = "***" + phone[-4:]
        cs = self.memory.call_state
        rs = self.router_state
        return {
            "slots": slots,
            "filled_slots": self.filled_slots(),
            "missing_slots": self.missing_slots(),
            "site_visit_requested": cs.site_visit_requested,
            "booking_validated": cs.booking_validated,
            "stage": getattr(rs, "stage", cs.conversation_stage) if rs is not None else cs.conversation_stage,
            "language": getattr(rs, "language", cs.language) if rs is not None else cs.language,
            "turn": self._turn(),
            "summary": self.running_summary(),
            "audit_log": [c.to_dict() for c in self.audit_log[-25:]],
        }

    # ------------------------------------------------------------------
    # Optional LLM-assisted extraction (fills gaps the regexes miss)
    # ------------------------------------------------------------------

    _LLM_EXTRACT_SLOTS = [
        "location", "configuration", "budget", "timeline", "purpose",
        "loan_required", "transaction_type", "occupation",
    ]

    @staticmethod
    def llm_extraction_enabled() -> bool:
        return os.environ.get("ENABLE_LLM_SLOT_EXTRACTION", "false").lower() in ("1", "true", "yes")

    async def llm_enrich(self, client, model: str, text: str) -> list[SlotChange]:
        """Ask the LLM to extract slots the regexes missed. Fill-only: an LLM
        value NEVER overwrites a deterministically captured one, so this can
        run after the response without racing the main merge path. Gated by
        ENABLE_LLM_SLOT_EXTRACTION; silent on any failure."""
        empty = [k for k in self._LLM_EXTRACT_SLOTS if not self.read_slot(k)]
        if not empty:
            return []
        schema_hint = {
            "location": "Surat | Vapi | Silvassa",
            "configuration": "like '2 BHK'",
            "budget": "like '50 lakh' or '1.2 crore'",
            "timeline": "like 'immediate', '3 months', 'this year'",
            "purpose": "self_use | investment",
            "loan_required": "yes | no",
            "transaction_type": "buy | rent",
            "occupation": "like 'business', 'service', 'doctor'",
        }
        wanted = {k: schema_hint[k] for k in empty}
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract real-estate qualification fields from one caller "
                            "utterance (may be Hindi/Gujarati/English, noisy phone "
                            "transcript). Return STRICT JSON with ONLY these keys, "
                            "value null when the utterance does not state it: "
                            + json.dumps(wanted)
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=120,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw = (resp.choices[0].message.content or "").strip()
            data = json.loads(raw)
        except Exception as exc:
            logger.info("[STATE-MGR] llm_enrich skipped: %r", exc)
            return []
        changes = []
        for key in empty:
            value = data.get(key)
            if isinstance(value, str) and value.strip() and value.lower() != "null":
                if self.read_slot(key) is None:   # fill-only, re-checked post-await
                    change = self.merge_slot(key, value, source="llm", utterance=text)
                    if change is not None:
                        changes.append(change)
        if changes:
            logger.info("[STATE-MGR] llm_enrich filled: %s",
                        ", ".join(f"{c.field}={c.new!r}" for c in changes))
        return changes
