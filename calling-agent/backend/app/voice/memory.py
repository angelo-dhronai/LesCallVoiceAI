"""
Conversation memory system — short-term + context + booking data.

Short-term: sliding window of recent turns.
Context: extracted structured info (user name, intent, topic).
Booking: Pramukh Group appointment booking state.
All are injected into LLM prompts for context-aware responses.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_SHORT_TERM_TURNS = 12  # covers a full 20-turn call without dropping early answers

# ---------------------------------------------------------------------------
# Phone number extraction — Indian mobile numbers (10 digits, starts 6-9)
# ---------------------------------------------------------------------------
_PHONE_RE = re.compile(
    r'(?<!\d)'
    r'(?:(?:\+91|91)[-.\s]?)?'      # optional country code
    r'([6-9]\d{4}[-.\s]?\d{5})'     # 10-digit Indian mobile
    r'(?!\d)',
)


@dataclass
class Turn:
    role: str       # "user" | "assistant"
    text: str
    language: str = ""  # "en", "hi", "gu" — language at time of turn


@dataclass
class BookingData:
    """Structured booking state for Pramukh Group appointments."""
    location: str | None = None       # "Surat", "Vapi", "Silvassa"
    property_type: str | None = None  # "2 BHK", "3 BHK", etc.
    appointment_date: str | None = None
    appointment_time: str | None = None
    user_name: str | None = None
    confirmed: bool = False
    # New fields driven by the project / status / rent-or-buy flow
    project: str | None = None              # caller-named Pramukh project
    property_status: str | None = None      # "ready_made" / "under_construction"
    transaction_type: str | None = None     # "rent" / "buy"
    budget: str | None = None              # caller's stated budget (e.g. "70 lakh")

    def summary(self) -> str:
        parts = []
        if self.user_name:
            parts.append(f"Name: {self.user_name}")
        if self.project:
            parts.append(f"Project: {self.project}")
        if self.property_status:
            parts.append(f"Status: {self.property_status}")
        if self.transaction_type:
            parts.append(f"Intent: {self.transaction_type}")
        if self.location:
            parts.append(f"Location: {self.location}")
        if self.property_type:
            parts.append(f"Type: {self.property_type}")
        if self.budget:
            parts.append(f"Budget: {self.budget}")
        if self.appointment_date:
            parts.append(f"Date: {self.appointment_date}")
        if self.appointment_time:
            parts.append(f"Time: {self.appointment_time}")
        return ", ".join(parts) if parts else "No details yet"

    def next_missing_field(self) -> str | None:
        if not self.location:
            return "location"
        if not self.property_type:
            return "property_type"
        if not self.appointment_date and not self.appointment_time:
            return "appointment"
        return None


@dataclass
class CallState:
    """
    Persistent per-call state — accumulated for the entire conversation.

    This is the canonical state object injected into every LLM prompt so
    the agent never re-asks for information already collected.

    Fields map directly to the requirements:
      customer_name, customer_phone, selected_project, selected_location,
      site_visit_requested, appointment_date, appointment_time,
      language, conversation_stage.
    """
    customer_name: str | None = None
    customer_phone: str | None = None       # stored internally; never read back verbatim
    selected_project: str | None = None     # first confirmed project; locked for session
    selected_location: str | None = None    # Surat / Vapi / Silvassa
    property_type: str | None = None        # "2 BHK", "3 BHK", etc.
    purpose: str | None = None              # "self_use" / "investment"
    budget: str | None = None              # caller's stated budget
    site_visit_requested: bool = False
    appointment_date: str | None = None
    appointment_time: str | None = None
    language: str = "hi"
    conversation_stage: str = "GREETING"
    booking_validated: bool = False         # True once all mandatory fields pass
    timeline: str | None = None             # caller's stated timeline (e.g. "3 months", "immediate")
    preferred_visit_date: str | None = None # caller's stated preferred visit date
    # Caller-requested callback ("call me in 10 minutes" / "at 12 today"). When
    # set, a follow-up automation entry is created at call end so the system
    # auto-dials them back at the requested time.
    callback_requested: bool = False
    callback_iso: str | None = None          # UTC ISO8601 — when to dial back
    callback_raw: str | None = None          # original phrase, for context/debug
    # Qualification slots added with the ConversationStateManager layer
    loan_required: str | None = None         # "yes" / "no" — home-loan needed
    transaction_type: str | None = None      # "buy" / "rent"
    occupation: str | None = None            # e.g. "business" / "service"
    property_category: str | None = None     # "apartment" / "villa" / "plot" / "commercial"

    def to_dict(self) -> dict:
        """Plain-dict snapshot of every field (raw values, phone unmasked —
        mask at the presentation layer, e.g. StateManager.snapshot())."""
        from dataclasses import asdict
        return asdict(self)


@dataclass
class ContextMemory:
    """Structured info extracted from conversation."""
    user_name: str | None = None
    intent: str | None = None
    topic: str | None = None
    sentiment: str = "neutral"
    extra: dict = field(default_factory=dict)

    def to_prompt_string(self) -> str:
        parts = []
        if self.user_name:
            parts.append(f"User's name: {self.user_name}")
        if self.intent:
            parts.append(f"Current intent: {self.intent}")
        if self.topic:
            parts.append(f"Topic: {self.topic}")
        if self.sentiment != "neutral":
            parts.append(f"User sentiment: {self.sentiment}")
        return "\n".join(parts)


class QATracker:
    """
    Tracks which discovery fields have been CAPTURED and how many times each
    was asked.

    A field counts as answered ONLY when an extractor captures a real value for
    it — never merely because a turn followed the question. This prevents a
    trailing speech fragment (e.g. the tail of the caller's name) from being
    mis-classified as the answer to a *different* question and causing that
    question to be skipped and its data lost. A per-field ask counter lets the
    agent move on after MAX_ASKS attempts so it never nags, while a captured
    value suppresses re-asking permanently.
    """

    # Signals that indicate the agent is asking about a specific field.
    _FIELD_SIGNALS: dict[str, list[str]] = {
        "name": [
            "your name", "naam", "aapka naam", "naam kya", "naam batao",
            "naam bataiye", "आपका नाम", "नाम बताइए", "नाम क्या",
            "tamaru naam", "tamaro naam", "tamaru", "aapnu naam",
            "what should i call", "may i know your name", "introduce yourself",
            "kon che aap", "tame kon", "tamaru naam shu che",
        ],
        "location": [
            "which city", "kahan", "kahan se", "location", "kis jagah",
            "which location", "city", "कहाँ", "किस शहर", "क्या लोकेशन",
            "ક્યાં", "ક્યા શહેर",
        ],
        "bhk": [
            "bhk", "how many bedroom", "configuration", "config",
            "kitne bhk", "kitne bedroom", "कितने bhk", "BHK chahiye",
            "property size", "flat size", "किस साइज",
        ],
        "budget": [
            "budget", "price range", "kitna budget", "how much",
            "bajat", "बजट", "कितना बजट", "budget kya", "comfortable range",
            "range", "how much are you looking", "investment budget",
            " બજેટ", "ketlu budget",
        ],
        "purpose": [
            "purpose", "self use", "investment", "apne liye",
            "kiraye ke liye", "किस लिए", "invest ke liye",
            "for yourself", "for living", "for renting",
        ],
        "timeline": [
            "when", "how soon", "timeline", "kab", "कब", "ક્યારे",
            "planning to buy", "when are you looking",
        ],
        "loan": [
            "loan", "home loan", "emi", "finance", "funding",
            "लोन", "ईएमआई", "લોન", "loan loge", "loan lenge",
            "self funded", "cash payment",
        ],
    }

    # After asking a field this many times without capturing a usable value,
    # stop asking it — the caller isn't giving a parseable answer and nagging
    # hurts the call. A CAPTURED value suppresses re-asking permanently. Note
    # this never skips a field on its first ask (which was the original bug).
    MAX_ASKS = 2

    def __init__(self) -> None:
        self._ask_count: dict[str, int] = {}   # field -> times the agent has asked
        self._captured: dict[str, str] = {}     # field -> canonical extracted value

    def observe_agent_turn(self, agent_text: str, turn_index: int) -> None:
        """Count which field the agent just asked about (fatigue tracking)."""
        lower = agent_text.lower()
        has_question = "?" in agent_text or any(
            w in lower for w in [
                "what", "which", "how", "when", "where",
                "kya", "kab", "kahan", "kaun", "kitna",
                "क्या", "कब", "कहाँ", "कौन", "कितना",
                "shu", "kem", "kyare", "kon", "keta",
            ]
        )
        if not has_question:
            return
        for field, signals in self._FIELD_SIGNALS.items():
            if field in self._captured:
                continue
            if any(s in lower for s in signals):
                self._ask_count[field] = self._ask_count.get(field, 0) + 1
                logger.info("[QA] Agent asked about %r (ask #%d)", field, self._ask_count[field])

    def observe_user_turn(self, user_text: str, turn_index: int) -> None:
        """No-op by design.

        Attribution is intentionally NOT inferred from turn arrival: a trailing
        speech fragment (e.g. the tail of the caller's name) must never be
        counted as the answer to whatever the agent last asked. A field becomes
        answered only when an extractor captures a real value for it
        (`mark_extracted`) — that is what keeps one question's stray fragment
        from being mis-classified as the answer to a different question.
        """
        return

    def mark_extracted(self, field: str, value: str) -> None:
        """Record the canonical extracted value — the ONLY way a field becomes
        'answered'. Permanently suppresses re-asking for that field."""
        self._captured[field] = value
        logger.info("[QA] Field %r captured: %r", field, value)

    def is_answered(self, field: str) -> bool:
        """True only when a real value was captured for the field."""
        return field in self._captured

    def should_stop_asking(self, field: str) -> bool:
        """True when the agent should no longer ask for a field — either it was
        captured, or it has already been asked MAX_ASKS times without a usable
        answer (move on rather than nag). Never true on a field's first ask."""
        return field in self._captured or self._ask_count.get(field, 0) >= self.MAX_ASKS

    def to_prompt_block(self) -> str:
        captured = self._captured
        asked_out = [
            f for f, c in self._ask_count.items()
            if c >= self.MAX_ASKS and f not in captured
        ]
        if not captured and not asked_out:
            return ""
        lines = ["━━ ALREADY COLLECTED — NEVER RE-ASK ━━"]
        for field, value in captured.items():
            lines.append(f"  · {field}: {value!r} — confirmed; do NOT ask again")
        if asked_out:
            lines.append(
                "  · Already asked without a clear answer (do NOT keep asking — "
                "move the call forward): " + ", ".join(asked_out)
            )
        return "\n".join(lines)


class ConversationMemory:
    """
    Manages short-term turn history, structured context, booking state,
    and the question-answer tracker.
    """

    def __init__(self, max_turns: int = MAX_SHORT_TERM_TURNS) -> None:
        self._max_turns = max_turns
        self._turns: list[Turn] = []
        self.context = ContextMemory()
        self.booking = BookingData()
        self.call_state = CallState()
        self.qa_tracker = QATracker()

    @property
    def turns(self) -> list[Turn]:
        return self._turns

    @property
    def turn_count(self) -> int:
        return len(self._turns) // 2

    def add_user(self, text: str, language: str = "") -> None:
        self._turns.append(Turn(role="user", text=text, language=language))
        self._extract_context(text)
        self._trim()

    def add_assistant(self, text: str, language: str = "") -> None:
        self._turns.append(Turn(role="assistant", text=text, language=language))
        self._trim()

    def get_history_for_llm(self) -> list[dict[str, str]]:
        return [{"role": t.role, "content": t.text} for t in self._turns]

    @property
    def turn_index(self) -> int:
        """Current turn count (number of complete user+assistant pairs)."""
        return len([t for t in self._turns if t.role == "user"])

    def _trim(self) -> None:
        max_messages = self._max_turns * 2
        if len(self._turns) > max_messages:
            self._turns = self._turns[-max_messages:]

    def _extract_context(self, text: str) -> None:
        lower = text.lower()

        # Phone number — capture once; stored internally and never echoed back
        if not self.call_state.customer_phone:
            m = _PHONE_RE.search(text)
            if m:
                raw = re.sub(r'[-.\s]', '', m.group(0))
                # Normalise: strip country code prefix, keep 10-digit number
                if raw.startswith('+91'):
                    raw = raw[3:]
                elif raw.startswith('91') and len(raw) > 10:
                    raw = raw[2:]
                if len(raw) == 10 and raw[0] in '6789':
                    self.call_state.customer_phone = raw
                    logger.info("[MEMORY] Phone captured (stored internally, not echoed)")

        # STT transcripts arrive fully lowercased (validate_transcript lowercases all text).
        # Patterns use case-insensitive capture groups; results are capitalized on store.
        name_patterns = [
            # English explicit: "my name is rahul", "i am priya"
            r"(?:my name is|i'm|i am|this is|call me)\s+([a-z]{2,}(?:\s+[a-z]{2,})?)",
            # Hindi romanized: "mera naam rahul", "naam hai priya"
            r"(?:mera naam|naam hai|naam mera|aapka naam)\s+([a-z]{2,}(?:\s+[a-z]{2,})?)",
            r"\bnaam\s+([a-z]{3,})",
            # Gujarati romanized: "maru naam", "maro naam"
            r"(?:maru naam|maro naam|mari name)\s+([a-z]{2,}(?:\s+[a-z]{2,})?)",
            # Hindi informal: "main rahul bol raha", captures the name between main and bol/hu
            r"(?:main|mai|mein)\s+([a-z]{3,})\s+(?:bol|baat|calling|speaking|hu|hoon)",
        ]
        _name_blocklist = {
            "a", "the", "just", "not", "is", "interested", "busy",
            "calling", "looking", "available", "free", "here", "there",
            "good", "fine", "okay", "sure", "ready", "happy", "sorry",
            "glad", "trying", "going", "coming", "waiting", "talking",
            "asking", "wondering", "thinking", "planning", "searching",
            # Feeling/state/condition words — caller answering "how are you?"
            # with "i'm <state>" must never be read as a name. These are the
            # words that follow "i'm"/"i am" in small talk, not introductions.
            "even", "better", "best", "well", "doing", "great", "alright",
            "blessed", "grateful", "thankful", "tired", "bored", "stressed",
            "exhausted", "sleepy", "hungry", "sick", "ill", "unwell",
            "nervous", "anxious", "worried", "scared", "afraid", "confused",
            "fantastic", "wonderful", "excellent", "perfect", "amazing",
            "awesome", "fabulous", "fabulous", "cool", "nice", "bit",
            # Intensifiers / adverbs that precede a state word
            "very", "really", "so", "too", "quite", "pretty", "more", "much",
            "still", "all", "right", "kind", "sort", "of", "feeling",
            "absolutely", "totally", "super", "completely", "actually",
            "honestly", "literally", "fine", "good", "okay", "ok",
            # Courtesy / address words that trail a state reply
            "thanks", "thank", "thankyou", "thanku", "thx", "welcome",
            "please", "yaar", "bhai", "sir", "madam", "you", "yourself",
            "today", "now", "also", "anyway", "though",
            "surat", "vapi", "silvassa", "pramukh", "vishal", "patel",
            "mera", "meri", "mere", "naam", "my", "name",
            "maro", "mari", "maru", "apnu", "apna", "aapka",
            "tamaru", "tamaro", "tamari", "ame", "tame",
            # Hindi/Gujarati trailing words that follow a name
            "hai", "hain", "ho", "hun", "hoon", "hu", "hoon",
            "che", "chhe", "cho", "chu",
            "bol", "baat", "raha", "rahi", "rahe",
        }
        if not self.booking.user_name:  # only try if not already captured
            for pattern in name_patterns:
                match = re.search(pattern, lower)
                if match:
                    name = match.group(1).strip()
                    # Filter tokens against blocklist; capitalize each valid token
                    name_tokens = []
                    for tok in name.split():
                        clean = re.sub(r"[^a-z]", "", tok)
                        if clean and len(clean) >= 2 and clean not in _name_blocklist:
                            name_tokens.append(clean.capitalize())
                    if name_tokens:
                        candidate = " ".join(name_tokens)
                        if len(candidate) > 2 and candidate.lower() not in _name_blocklist:
                            self.context.user_name = candidate
                            self.booking.user_name = candidate
                            logger.info("[MEMORY] Extracted name: %s", candidate)
                            break

        topic_keywords = {
            "appointment": "scheduling", "schedule": "scheduling",
            "book": "scheduling", "visit": "scheduling",
            "cancel": "cancellation",
            "price": "pricing", "cost": "pricing",
            "how much": "pricing", "rate": "pricing", "kitna": "pricing",
            "problem": "support", "issue": "support",
            "flat": "property", "apartment": "property",
            "bhk": "property", "property": "property",
            "makan": "property", "ghar": "property",
        }
        for keyword, topic in topic_keywords.items():
            if keyword in lower:
                self.context.topic = topic
                break

        negative_words = {"angry", "frustrated", "annoyed", "terrible", "awful", "worst", "hate"}
        positive_words = {"great", "thanks", "awesome", "love", "perfect", "excellent", "wonderful",
                          "accha", "badhiya", "saru", "maja"}
        words = set(lower.split())
        if words & negative_words:
            self.context.sentiment = "negative"
        elif words & positive_words:
            self.context.sentiment = "positive"

    def reset(self) -> None:
        self._turns.clear()
        self.context = ContextMemory()
        self.booking = BookingData()
        self.call_state = CallState()
        self.qa_tracker = QATracker()
