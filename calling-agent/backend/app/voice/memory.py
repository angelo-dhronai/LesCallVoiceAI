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
    Tracks which discovery questions have been asked and whether the caller
    answered them — even when data extraction failed to parse the value.

    This prevents the LLM from re-asking questions the caller already responded
    to in natural speech, such as a mangled budget number or an informal name.
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
    }

    def __init__(self) -> None:
        self._asked: dict[str, int] = {}    # field -> turn_index when asked
        self._answered: dict[str, str] = {} # field -> user answer text (may be raw STT)

    def observe_agent_turn(self, agent_text: str, turn_index: int) -> None:
        """Detect which field the agent just asked about."""
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
            if field not in self._answered and any(s in lower for s in signals):
                if field not in self._asked:
                    self._asked[field] = turn_index
                    logger.info("[QA] Agent asked about %r at turn %d", field, turn_index)

    def observe_user_turn(self, user_text: str, turn_index: int) -> None:
        """Mark pending asked-fields as answered by this user turn."""
        for field, asked_at in list(self._asked.items()):
            if field not in self._answered and turn_index > asked_at:
                self._answered[field] = user_text[:100]
                logger.info("[QA] Field %r answered: %r", field, user_text[:50])

    def mark_extracted(self, field: str, value: str) -> None:
        """Override answer with the successfully extracted canonical value."""
        self._answered[field] = value
        if field not in self._asked:
            self._asked[field] = 0

    def is_answered(self, field: str) -> bool:
        return field in self._answered

    def to_prompt_block(self) -> str:
        if not self._answered:
            return ""
        lines = ["━━ QUESTIONS ALREADY ANSWERED — NEVER RE-ASK ━━"]
        for field, answer in self._answered.items():
            lines.append(f"  · {field}: caller already responded ({answer!r})")
        lines.append(
            "Even if extraction was imperfect, the caller HAS answered these. "
            "Move on — do NOT ask again."
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
