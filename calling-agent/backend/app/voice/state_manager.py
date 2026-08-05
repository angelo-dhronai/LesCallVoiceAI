"""
ConversationStateManager — the conversation state management layer.

Solves the "user gave three answers in one sentence, agent asks all three
again" problem. One caller utterance like

    "I want a 3BHK apartment in Vapi under 50 lakhs"

fills location / configuration / budget in a single pass; the agent then
acknowledges what it knows and asks ONLY the highest-priority missing field.

Design (latency-first, telephony-first)
---------------------------------------
* extract_entities() is pure precompiled-regex + knowledge-base matching —
  well under 1 ms per utterance. It runs synchronously on the hot path so the
  CURRENT turn's system prompt already contains the CURRENT turn's facts.
* An optional async LLM extractor (see conversation.py) back-fills slots the
  regexes miss; it merges ONLY into still-empty slots so a late/stale result
  can never clobber newer information (race-safe by construction).
* merge_state() never overwrites a value with null, and logs every change to
  an audit trail. Latest user information always wins for real values —
  "Actually make it a 2BHK" flips configuration and records old→new.
* running_summary() and build_prompt_context() are template-built strings —
  no LLM calls, no I/O, nothing on the critical path but string formatting.
* Slot vocabulary is per-agent: defined in agent YAML (`slots:`) or derived
  from the agent's qa_field_signals as a fallback, so real-estate agents get
  location/bhk/budget/... while a meeting-booking EA tracks name/company/....

Telephony realities handled: Hinglish + Devanagari + Gujarati phrasing,
noisy ASR fragments (extract what parses, ignore the rest), corrections,
multi-intent utterances, information fragmented across turns (merge), and
barge-in (extraction is synchronous, so an interrupted turn still counts).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SlotSpec:
    """One trackable piece of information."""
    name: str                                   # canonical key, e.g. "location"
    priority: int = 100                         # lower = asked earlier
    required: bool = True                       # counts toward missing_slots
    label: str = ""                             # human label for prompts ("Configuration")
    question: dict[str, str] = field(default_factory=dict)   # lang -> question to ask
    summary_known: str = ""                     # template, {value} substituted
    summary_unknown: str = ""                   # sentence used while missing
    # v2 (mirrors SlotDefinition — all defaults keep legacy behaviour):
    type: str = ""                              # ''|text|number|money|enum|boolean|phone|date|time|location
    enum_values: list[str] = field(default_factory=list)
    enum_synonyms: dict[str, list[str]] = field(default_factory=dict)
    extract_patterns: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    merge_policy: str = "free"                  # free|correction_gated|confirm
    confirm_readback: str = ""                  # ''|verbatim|digit_groups_2
    max_asks: int = 0                           # 0 = unlimited
    reask_question: dict[str, str] = field(default_factory=dict)
    # Precompiled at manager init (hot path stays <1 ms):
    _compiled_patterns: list = field(default_factory=list, repr=False, compare=False)
    _synonym_lookup: dict = field(default_factory=dict, repr=False, compare=False)

    def display(self) -> str:
        return self.label or self.name.replace("_", " ").title()


# type: phone — Indian mobile (mirrors memory.py's extraction pattern)
_SLOT_PHONE_RE = re.compile(
    r'(?<!\d)'
    r'(?:(?:\+91|91)[-.\s]?)?'
    r'([6-9]\d{4}[-.\s]?\d{5})'
    r'(?!\d)',
)

# Generic number for type: number
_SLOT_NUMBER_RE = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])')

# Correction markers for merge_policy: correction_gated — the caller is
# explicitly revising an earlier value, not just mentioning a new one.
_CORRECTION_MARKER_RE = re.compile(
    r"\b(actually|nahi|नहीं|nahin|galat|wrong|change|correct|sorry|"
    r"i meant|make it|instead|update)\b", re.IGNORECASE)


def format_readback(value: Any, mode: str) -> str:
    """Format a captured value for spoken read-back confirmation (D5)."""
    s = str(value or "")
    if mode == "digit_groups_2":
        digits = re.sub(r"\D", "", s)
        if digits:
            return " ".join(digits[i:i + 2] for i in range(0, len(digits), 2))
    if mode == "spelled":
        # B3: local part letter-by-letter, domain spoken naturally —
        # 'ben@gmail.com' → 'b. e. n. — at — gmail dot com'.
        from .contact_capture import spelled_readback
        return spelled_readback(s)
    return s


@dataclass
class AuditEntry:
    """One state mutation — the audit trail the spec requires."""
    field: str
    old: Any
    new: Any
    source: str          # "regex" | "llm" | "legacy" | "system"
    turn: int
    timestamp: str       # ISO-8601
    utterance: str = ""  # snippet that caused the change

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Precompiled extractors (module-level: compiled once per process)
# ---------------------------------------------------------------------------

# Word-numbers common in Indian budget/config speech (romanized + Devanagari).
_WORD_NUM = {
    "ek": 1, "एक": 1, "one": 1, "do": 2, "दो": 2, "two": 2,
    "teen": 3, "tin": 3, "तीन": 3, "three": 3,
    "char": 4, "chaar": 4, "चार": 4, "four": 4, "paanch": 5, "panch": 5, "पांच": 5, "five": 5,
    "das": 10, "dus": 10, "दस": 10, "ten": 10, "bees": 20, "बीस": 20, "twenty": 20,
    "tees": 30, "तीस": 30, "thirty": 30, "chalis": 40, "chaalis": 40, "चालीस": 40, "forty": 40,
    "pachas": 50, "पचास": 50, "fifty": 50, "saath": 60, "साठ": 60, "sixty": 60,
    "sattar": 70, "सत्तर": 70, "seventy": 70, "assi": 80, "अस्सी": 80, "eighty": 80,
    "nabbe": 90, "नब्बे": 90, "ninety": 90, "sau": 100, "सौ": 100, "hundred": 100,
    "dedh": 1.5, "डेढ़": 1.5, "dhai": 2.5, "ढाई": 2.5,
}
_NUM_WORDS_ALT = "|".join(sorted(map(re.escape, _WORD_NUM), key=len, reverse=True))

# 1-6 BHK: "3bhk", "3 bhk", "teen bhk", "३ बीएचके", "3 bedroom", "2-3 bhk" (→ last)
_BHK_RE = re.compile(
    rf"(\d|[१-६]|{_NUM_WORDS_ALT})\s*[-–]?\s*(?:bhk|b\.h\.k|बीएचके|bedroom|बेडरूम|bed\b)",
    re.IGNORECASE,
)
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# Budget: "50 lakh(s)", "पचास लाख", "1.2 crore", "50-60 lakh", "under 50L"
_BUDGET_RE = re.compile(
    rf"(\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?|{_NUM_WORDS_ALT})"
    r"\s*(lakh|lac|lakhs|लाख|l\b|crore|crores|cr\b|करोड़|करोड)",
    re.IGNORECASE,
)
_BUDGET_QUALIFIER_RE = re.compile(
    r"\b(under|below|upto|up to|tak|se kam|max|maximum|के अंदर|तक|around|approx|budget)\b",
    re.IGNORECASE,
)

_PROPERTY_TYPES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(apartment|flat|फ्लैट|अपार्टमेंट)\b", re.I), "Apartment"),
    (re.compile(r"\b(villa|bungalow|बंगला|विला|row ?house|kothi|कोठी)\b", re.I), "Villa/Bungalow"),
    (re.compile(r"\b(plot|zameen|jameen|ज़मीन|जमीन|प्लॉट|land)\b", re.I), "Plot/Land"),
    (re.compile(r"\b(shop|dukaan|dukan|दुकान|showroom|office|ऑफिस|commercial)\b", re.I), "Commercial"),
]

# Order matters: "Just exploring" MUST outrank the immediacy patterns so
# "koi jaldi nahi" / "bas dekh raha" never read as "Immediately". Bare
# "abhi"/"jaldi" are NOT immediacy — they need purchase context nearby
# ("abhi chahiye", "turant lena hai"); "abhi nahi" never matches at all.
_TIMELINE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(just (?:looking|exploring|enquiring)|bas dekh|सिर्फ देख|no hurry"
                r"|koi jaldi nahi|कोई जल्दी नहीं)\b", re.I), "Just exploring"),
    (re.compile(r"\b(immediately|asap|ready to (?:buy|book))\b", re.I), "Immediately"),
    (re.compile(r"\b(?:abhi|turant|अभी|तुरंत)(?!\s*(?:nahi|नहीं))\b.{0,16}?"
                r"\b(?:chahiye|chaahiye|lena|le\s*l|book|kharid|buy|चाहिए|लेना|ख़रीद|खरीद)", re.I), "Immediately"),
    (re.compile(r"\b(?:chahiye|lena|book|kharid|buy)\b.{0,12}?\b(?:abhi|turant)(?!\s*nahi)\b", re.I), "Immediately"),
    (re.compile(r"\b(this|is)\s*(month|महीने|mahine)\b", re.I), "This month"),
    (re.compile(r"\b(next|agle|अगले)\s*(month|महीने|mahine)\b", re.I), "Next month"),
    (re.compile(r"\b([1-9]|[१-९]|do|teen|char|छह|che)\s*[-–]?\s*(months?|महीने|mahino)\b", re.I), "{0} months"),
    (re.compile(r"\b(this|is)\s*(year|साल|saal)\b", re.I), "This year"),
    (re.compile(r"\b(next|agle|अगले)\s*(year|साल|saal)\b", re.I), "Next year"),
    (re.compile(r"\b(diwali|दिवाली|dussehra|दशहरा|holi|होली|navratri|नवरात्रि)\b", re.I), "Around {0}"),
]

_LOAN_YES_RE = re.compile(
    # Bare "hai/hoga" removed from the verb list: "loan available hai kya?"
    # is a QUESTION about loans, not a statement of needing one.
    r"\b(loan|लोन|emi|finance|financing|home ?loan|karza|कर्ज)\b.{0,24}?"
    r"\b(chahiye|lena|lunga|lungi|karna|karwana|required|need|want|चाहिए|लेना)\b"
    r"|\bwith\s+(?:a\s+)?loan\b|\bloan\s*(?:pe|par|पे|पर|se|से)\b",
    re.IGNORECASE,
)
# Question markers near a loan mention → the caller is ASKING about loans,
# not answering whether they need one. Don't fill the slot from questions.
_LOAN_QUESTION_RE = re.compile(
    r"\?|\b(kya|kitna|kitne|kaise|kab|milega|milta|interest|byaj|rate|क्या|कितना|कैसे|मिलेगा|ब्याज)\b",
    re.IGNORECASE,
)
_LOAN_NO_RE = re.compile(
    r"\b(cash|कैश|full payment|no loan|without loan|bina loan|बिना लोन|self fund|own fund|loan nahi|लोन नहीं)\b",
    re.IGNORECASE,
)

_PURPOSE_INVEST_RE = re.compile(
    r"\b(invest|investment|rental|kiraya|kiraye|किराया|किराए|rent (?:out|pe)|resale|returns?)\b", re.I)
_PURPOSE_SELF_RE = re.compile(
    r"\b(self ?use|khud|खुद|rehne|रहने|apne (?:liye|rehne)|family ke liye|own use|live|shift)\b", re.I)

_OCCUPATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(business|vyapar|व्यापार|businessman|self ?employed|entrepreneur|dukandar)\b", re.I), "Business"),
    (re.compile(r"\b(job|naukri|नौकरी|service|salaried|employee|company me|kaam karta)\b", re.I), "Salaried"),
    (re.compile(r"\b(doctor|डॉक्टर|engineer|इंजीनियर|ca\b|advocate|lawyer|teacher|professor)\b", re.I), "Professional"),
]

# Correction markers — "actually", "nahi X", "change kar do", "make it"
_NEGATED_VALUE_RE = re.compile(rf"(\d|{_NUM_WORDS_ALT})\s*(?:bhk|बीएचके)\s*(?:nahi|नहीं|nahin|not|मत)", re.I)


def _word_or_digit_to_num(tok: str) -> float | None:
    tok = tok.strip().lower().translate(_DEVANAGARI_DIGITS)
    if re.fullmatch(r"\d+(?:\.\d+)?", tok):
        return float(tok)
    return _WORD_NUM.get(tok)


# ---------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------

class ConversationStateManager:
    """Per-call structured memory: extract → merge → detect gaps → drive prompts."""

    def __init__(
        self,
        slots: list[SlotSpec],
        locations: list[str] | None = None,
        location_aliases: list[str] | None = None,
        default_intent: str = "",
        language: str = "hi",
    ) -> None:
        # Deep-copy: the fallback slot sets are shared module-level objects,
        # and v2 hooks (require_now, precompiled patterns) mutate specs —
        # per-call managers must never leak mutations into each other.
        import copy as _copy
        self.slots: dict[str, SlotSpec] = {s.name: _copy.deepcopy(s) for s in slots}
        self.state: dict[str, Any] = {s.name: None for s in slots}
        if default_intent:
            self.state.setdefault("intent", default_intent)
        self.audit_log: list[AuditEntry] = []
        # Log an unknown collect-name once per manager, not once per turn.
        self._warned_unknown_collect: dict[str, bool] = {}
        self.language = language
        self.turn_index = 0
        self._pending_fields: set[str] = set()   # e.g. name awaiting confirmation
        # v2 bookkeeping — all additive:
        self.asked_count: dict[str, int] = {}    # slot -> times asked
        self._asked_turn: dict[str, int] = {}    # slot -> turn last counted
        self.skipped: set[str] = set()           # max_asks exhausted
        self.readback_done: set[str] = set()     # read-back confirmed slots
        self.extra_flags: dict[str, Any] = {}    # e.g. whatsapp_promised (→ collected_data)
        # Precompile per-slot patterns/synonyms (hot path stays <1 ms).
        for s in self.slots.values():
            s._compiled_patterns = []
            for p in (s.extract_patterns or [])[:8]:
                try:
                    s._compiled_patterns.append(re.compile(str(p)[:200], re.IGNORECASE))
                except re.error:
                    logger.warning("[STATE] slot %s pattern %r does not compile — skipped",
                                   s.name, p)
            lookup: dict[str, str] = {}
            for canon in (s.enum_values or []):
                lookup[str(canon).lower()] = str(canon)
            for canon, alts in (s.enum_synonyms or {}).items():
                canon_val = lookup.get(str(canon).lower(), str(canon))
                for alt in (alts if isinstance(alts, (list, tuple)) else [alts]):
                    lookup[str(alt).lower()] = canon_val
            s._synonym_lookup = lookup
        # Location matching material from the agent's knowledge base.
        self._locations = [l for l in (locations or []) if l]
        self._loc_lookup: dict[str, str] = {}
        for canon in self._locations:
            self._loc_lookup[canon.lower()] = canon
        aliases = [a.strip() for a in (location_aliases or []) if a and a.strip()]
        n_loc = len(self._locations)
        # Agent YAMLs list aliases in script-groups mirroring the locations
        # order (e.g. [surat, vapi, silvassa, સુરત, વાપી, સિલવાસા, सूरत, ...]),
        # so position-in-group canonicalizes non-Latin scripts too.
        grouped = n_loc > 0 and len(aliases) % n_loc == 0
        for i, alias in enumerate(aliases):
            a = alias.lower()
            best = self._loc_lookup.get(a) \
                or next((c for c in self._locations if c.lower().startswith(a[:3])), None) \
                or (self._locations[i % n_loc] if grouped else None)
            self._loc_lookup[a] = best or alias.title()

    # ------------------------------------------------------------------
    # 1. extract_entities — pure sync, <1 ms
    # ------------------------------------------------------------------
    def _untyped(self, name: str) -> bool:
        spec = self.slots.get(name)
        return spec is not None and not getattr(spec, "type", "")

    def _extract_typed(self, spec: SlotSpec, text: str, lower: str) -> Any:
        """v2 type-driven extraction for one slot. None = nothing found."""
        # Custom regexes win (first capture group, else whole match).
        for pat in spec._compiled_patterns:
            m = pat.search(text)
            if m:
                return (m.group(1) if m.groups() else m.group(0)).strip()
        t = spec.type
        if t in ("enum", "location"):
            # Latest mention wins (mirrors _match_location correction handling)
            hit, best_idx = None, -1
            for alias, canon in spec._synonym_lookup.items():
                start = 0
                while True:
                    idx = lower.find(alias, start)
                    if idx < 0:
                        break
                    before_ok = idx == 0 or not lower[idx - 1].isalnum()
                    after = idx + len(alias)
                    after_ok = after >= len(lower) or not lower[after].isalnum()
                    if before_ok and after_ok and idx > best_idx:
                        best_idx, hit = idx, canon
                    start = idx + 1
            if hit is None and t == "location":
                return self._match_location(lower)
            return hit
        if t == "money":
            return self._extract_budget(text)
        if t == "number":
            matches = _SLOT_NUMBER_RE.findall(lower)
            return matches[-1] if matches else None
        if t == "phone":
            m = _SLOT_PHONE_RE.search(text)
            if m:
                raw = re.sub(r"[-.\s]", "", m.group(0))
                if raw.startswith("+91"):
                    raw = raw[3:]
                elif raw.startswith("91") and len(raw) > 10:
                    raw = raw[2:]
                if len(raw) == 10 and raw[0] in "6789":
                    return raw
            return None
        if t == "boolean":
            # Only when this slot is the one being asked — a stray "yes"
            # anywhere else must not fill it.
            gaps = self.detect_missing_slots()
            if gaps["missing"] and gaps["missing"][0] == spec.name:
                if re.search(r"\b(yes|yeah|haan|ha|ji|bilkul|sure|ok|okay|હા|हां|हाँ)\b", lower):
                    return "Yes"
                if re.search(r"\b(no|nahi|nope|नहीं|ના|નહીં)\b", lower):
                    return "No"
            return None
        if t == "email":
            # B1 (2026-07-30): deterministic spoken-form assembly — the ONLY
            # writer for email slots (merge_state rejects source="llm").
            from .contact_capture import normalize_spoken_email
            return normalize_spoken_email(text)
        # text/date/time: deferred to the LLM gap-filler.
        return None

    def extract_entities(self, text: str) -> dict[str, Any]:
        """Extract every recognizable slot value from one utterance."""
        if not text or not text.strip():
            return {}
        out: dict[str, Any] = {}
        lower = text.lower()

        # v2: type-driven extraction runs FIRST for typed slots; slots with
        # an empty type keep the legacy name-keyed dispatch byte-identically.
        for name, spec in self.slots.items():
            if not getattr(spec, "type", ""):
                continue
            try:
                val = self._extract_typed(spec, text, lower)
            except Exception:
                logger.exception("[STATE] typed extraction failed for %s", name)
                val = None
            if val is not None:
                out[name] = val

        if "location" in self.slots and self._untyped("location"):
            loc = self._match_location(lower)
            if loc:
                out["location"] = loc

        if (("bhk" in self.slots and self._untyped("bhk"))
                or ("configuration" in self.slots and self._untyped("configuration"))):
            bhk = self._extract_bhk(text)
            if bhk:
                out["bhk" if "bhk" in self.slots else "configuration"] = bhk

        if "property_type" in self.slots and self._untyped("property_type"):
            for pat, label in _PROPERTY_TYPES:
                if pat.search(text):
                    out["property_type"] = label
                    break

        if "budget" in self.slots and self._untyped("budget"):
            budget = self._extract_budget(text)
            if budget:
                out["budget"] = budget

        if "timeline" in self.slots and self._untyped("timeline"):
            tl = self._extract_timeline(text)
            if tl:
                out["timeline"] = tl

        if ("loan_required" in self.slots and self._untyped("loan_required")
                and not _LOAN_QUESTION_RE.search(text)):
            if _LOAN_NO_RE.search(text):
                out["loan_required"] = "No"
            elif _LOAN_YES_RE.search(text):
                out["loan_required"] = "Yes"

        if "purpose" in self.slots and self._untyped("purpose"):
            if _PURPOSE_INVEST_RE.search(text):
                out["purpose"] = "Investment"
            elif _PURPOSE_SELF_RE.search(text):
                out["purpose"] = "Self-use"

        if "occupation" in self.slots and self._untyped("occupation"):
            for pat, label in _OCCUPATION_PATTERNS:
                if pat.search(text):
                    out["occupation"] = label
                    break
        return out

    # -- individual extractors ----------------------------------------
    def _match_location(self, lower: str) -> str | None:
        """Latest MENTION in the utterance wins (corrections: 'Vapi nahi, Surat').

        Tracks the highest start index across ALL occurrences of ALL aliases —
        not dict order, which previously made 'Vapi nahi, Surat' resolve to
        the rejected city.
        """
        hit: str | None = None
        best_idx = -1
        for alias, canon in self._loc_lookup.items():
            start = 0
            while True:
                idx = lower.find(alias, start)
                if idx < 0:
                    break
                # word-boundary-ish check that also works for Devanagari (no \b)
                before_ok = idx == 0 or not lower[idx - 1].isalnum()
                after = idx + len(alias)
                after_ok = after >= len(lower) or not lower[after].isalnum()
                if before_ok and after_ok and idx > best_idx:
                    best_idx = idx
                    hit = canon
                start = idx + 1
        return hit

    @staticmethod
    def _extract_bhk(text: str) -> str | None:
        negated: set[float] = set()
        for m in _NEGATED_VALUE_RE.finditer(text):
            n = _word_or_digit_to_num(m.group(1))
            if n:
                negated.add(n)
        value: float | None = None
        for m in _BHK_RE.finditer(text):
            n = _word_or_digit_to_num(m.group(1))
            if n and 1 <= n <= 6 and n not in negated:
                value = n  # last non-negated mention wins
        if value is None:
            return None
        return f"{int(value)}BHK"

    @staticmethod
    def _extract_budget(text: str) -> str | None:
        result: str | None = None
        for m in _BUDGET_RE.finditer(text):
            raw_num, unit = m.group(1), m.group(2).lower()
            unit_label = "Crore" if unit.startswith(("cr", "कर")) or unit == "करोड़" else "Lakhs"
            num_part = raw_num.strip().translate(_DEVANAGARI_DIGITS)
            if re.search(r"[-–]", num_part):
                lo, hi = re.split(r"\s*[-–]\s*", num_part)[:2]
                result = f"{lo.strip()}-{hi.strip()} {unit_label}"
            else:
                n = _word_or_digit_to_num(num_part)
                if n is None:
                    continue
                n_str = str(int(n)) if float(n).is_integer() else str(n)
                result = f"{n_str} {unit_label}"
        return result

    @staticmethod
    def _extract_timeline(text: str) -> str | None:
        for pat, template in _TIMELINE_PATTERNS:
            m = pat.search(text)
            if m:
                if "{0}" in template:
                    tok = m.group(1)
                    n = _word_or_digit_to_num(tok)
                    return template.format(int(n) if n else tok.title())
                return template
        return None

    # ------------------------------------------------------------------
    # 2. merge_state — null-protected, latest-wins, audited
    # ------------------------------------------------------------------
    def merge_state(
        self,
        extracted: dict[str, Any],
        source: str = "regex",
        utterance: str = "",
        fill_only: bool = False,
    ) -> list[AuditEntry]:
        """Merge extracted values into state.

        * None/empty extracted values are ignored (never overwrite with null).
        * Real values overwrite (latest user info wins) with an audit entry —
          unless fill_only=True (used by the async LLM extractor so a slow,
          possibly stale result can only fill gaps, never clobber).
        """
        changes: list[AuditEntry] = []
        for key, new in (extracted or {}).items():
            if new is None or (isinstance(new, str) and not new.strip()):
                continue
            if key not in self.state:
                continue  # unknown slot for this agent — ignore
            if key in self._pending_fields:
                # Field is awaiting the caller's explicit confirmation (e.g.
                # name). No extractor — regex OR async LLM — may write it;
                # only set_externally (the confirmation path) can, which
                # clears the pending flag first. Prevents a rejected value
                # from resurrecting as confirmed ground truth.
                continue
            old = self.state.get(key)
            if old == new:
                continue
            if fill_only and old is not None:
                continue
            # v2 merge policies (default 'free' = current latest-wins). The
            # literal slot 'name' is hard-excluded — its confirmation flow
            # (_name_pending in conversation.py) stays untouched.
            spec = self.slots.get(key)
            policy = getattr(spec, "merge_policy", "free") if spec else "free"
            slot_type = getattr(spec, "type", "") if spec else ""
            # B1/B4 (2026-07-30): contact-typed slots have exactly one
            # writer — the deterministic capture path. A live call stored
            # the LLM's guess 'bell@gmail.com' (assembled from ASR "bell
            # www.gmail.com") as ground truth; fill_only then made it
            # immortal. LLM extraction may not touch these slots at all,
            # and no source may store a value that fails the validator.
            if slot_type in ("email", "phone") and source in ("llm", "answer"):
                self.update_audit_log(
                    key, old, old, source,
                    f"REJECTED {source} write {new!r} (contact slots are "
                    f"deterministic-only)")
                continue
            if slot_type == "email":
                from .contact_capture import validate_email
                if not validate_email(str(new)):
                    self.update_audit_log(
                        key, old, old, source,
                        f"REJECTED invalid email {new!r}")
                    continue
                new = str(new).strip().lower()
            elif slot_type in ("date", "time") and source in ("llm", "answer"):
                # A gap-filled date/time must at least parse-ish: carry a
                # digit or a recognizable day/time word ('after office' was
                # stored as preferred_time from a discovery answer).
                _sv = str(new).lower()
                if not (any(ch.isdigit() for ch in _sv)
                        or re.search(
                            r"\b(morning|afternoon|evening|noon|night|today|"
                            r"tomorrow|tonight|monday|tuesday|wednesday|"
                            r"thursday|friday|saturday|sunday|subah|dopahar|"
                            r"shaam|raat|aaj|kal|parso|savare|bapore|saanje)\b",
                            _sv)):
                    self.update_audit_log(
                        key, old, old, source,
                        f"REJECTED unparseable {slot_type} {new!r}")
                    continue
            if key != "name" and policy == "correction_gated" and old is not None:
                if not (_NEGATED_VALUE_RE.search(utterance or "")
                        or _CORRECTION_MARKER_RE.search(utterance or "")):
                    # Overwrite rejected — keep the confirmed value, audit it.
                    self.update_audit_log(
                        key, old, old, source,
                        f"REJECTED overwrite to {new!r} (no correction marker): "
                        f"{(utterance or '')[:80]}")
                    continue
            if key != "name" and policy == "confirm" and source != "system":
                # Captured but held for the caller's explicit confirmation;
                # conversation.py's generic confirm loop resolves it.
                self.state[key] = new
                self._pending_fields.add(key)
                entry = self.update_audit_log(
                    key, old, new, source, (utterance or "")[:120])
                changes.append(entry)
                continue
            self.state[key] = new
            if spec is not None and getattr(spec, "confirm_readback", ""):
                self.readback_done.discard(key)  # new value → read back again
            entry = self.update_audit_log(key, old, new, source, utterance)
            changes.append(entry)
        return changes

    # ------------------------------------------------------------------
    # 3. missing-slot detection
    # ------------------------------------------------------------------
    def _deps_met(self, name: str) -> bool:
        """All depends_on slots filled (or the slot has no dependencies)."""
        spec = self.slots.get(name)
        deps = getattr(spec, "depends_on", None) or [] if spec else []
        return all(self.state.get(d) is not None for d in deps if d in self.slots)

    def detect_missing_slots(self) -> dict[str, list[str]]:
        filled = [n for n, v in self.state.items()
                  if n in self.slots and v is not None]
        # v2 dependency gates: a required, unfilled slot whose depends_on are
        # not yet met is BLOCKED — never asked early, still filled
        # opportunistically when volunteered (extraction is ungated).
        # v2 ask fatigue: a slot in `skipped` (max_asks hit) leaves `missing`.
        unfilled = [n for n, s in self.slots.items()
                    if s.required and self.state.get(n) is None
                    and n not in self._pending_fields
                    and n not in self.skipped]
        blocked = sorted((n for n in unfilled if not self._deps_met(n)),
                         key=lambda n: self.slots[n].priority)
        missing = sorted((n for n in unfilled if self._deps_met(n)),
                         key=lambda n: self.slots[n].priority)
        # Pending = captured but unconfirmed. Excluded from `missing` (don't
        # re-ask mid-confirmation) but NOT filled — "all collected" logic and
        # stage advancement must treat pending as incomplete.
        pending = sorted(
            (n for n in self._pending_fields if n in self.slots),
            key=lambda n: self.slots[n].priority,
        )
        return {"filled": filled, "missing": missing, "pending": pending,
                "blocked": blocked, "skipped": sorted(self.skipped)}

    def collect_satisfied(self, names: list[str]) -> bool:
        """Stage-advancement check: every named slot is filled (pending
        excluded) or skipped via max_asks (counts as satisfied).

        FAIL-CLOSED on a name that is not a defined slot. This used to
        `continue` — treating the unknown name as already satisfied — so a
        stage whose `collect` referenced slots that no longer existed
        returned vacuously True and advanced on its FIRST evaluation. That is
        not a hypothetical: a whole-list `slots` replacement orphaned the
        `collect` of hardik's EXISTING_COVERAGE stage and the entire
        existing-coverage qualification silently evaporated from live calls,
        with no error and no lint (2026-08-04 audit).

        A config mistake must degrade VISIBLY, not delete a stage. Holding is
        safe: `advance_when.max_turns` is an OR-fallback (conversation.py),
        so the stage still advances on its own turn budget.
        """
        for n in names or []:
            if n not in self.slots:
                if not self._warned_unknown_collect.get(n):
                    self._warned_unknown_collect[n] = True
                    logger.warning(
                        "[STATE] stage collects '%s' which is not a defined "
                        "slot — gate held (was silently satisfied before). "
                        "Fix the agent's slots or the stage's collect list.", n)
                return False
            if n in self._pending_fields:
                return False
            if self.state.get(n) is None and n not in self.skipped:
                return False
        return True

    def require_now(self, name: str) -> None:
        """Branch action hook (capture_phone): make an optional slot required
        so it becomes the next ask."""
        spec = self.slots.get(name)
        if spec is not None:
            spec.required = True
            self.skipped.discard(name)

    def next_missing_slot(self) -> SlotSpec | None:
        missing = self.detect_missing_slots()["missing"]
        return self.slots[missing[0]] if missing else None

    # ------------------------------------------------------------------
    # 4. dynamic question generation
    # ------------------------------------------------------------------
    def generate_next_question(self, language: str | None = None) -> str | None:
        slot = self.next_missing_slot()
        if slot is None:
            return None
        lang = language or self.language
        # v2: second-attempt phrasing after the first ask, when provided.
        asked = self.asked_count.get(slot.name, 0)
        if asked >= 1 and slot.reask_question:
            q = (slot.reask_question.get(lang) or slot.reask_question.get("en")
                 or next(iter(slot.reask_question.values()), ""))
            if q:
                return q
        q = slot.question.get(lang) or slot.question.get("en") or ""
        return q or f"Ask the caller about their {slot.display().lower()}."

    def note_asked(self, name: str) -> None:
        """Ask-fatigue bookkeeping (v2): called at question-selection time.
        At max_asks (when >0) the slot moves to `skipped` — excluded from
        `missing`, satisfied for stage advancement.

        Counted at most ONCE per turn_index: this runs on every prompt BUILD
        (retries/rebuilds included), and per-build counting would burn a
        slot's max_asks budget without the agent ever voicing a question."""
        spec = self.slots.get(name)
        if spec is None:
            return
        if self._asked_turn.get(name) == self.turn_index:
            return
        self._asked_turn[name] = self.turn_index
        self.asked_count[name] = self.asked_count.get(name, 0) + 1
        max_asks = getattr(spec, "max_asks", 0) or 0
        if max_asks > 0 and self.asked_count[name] >= max_asks and \
                self.state.get(name) is None:
            self.skipped.add(name)
            logger.info("[STATE] slot %s hit max_asks=%d — skipped", name, max_asks)

    # ------------------------------------------------------------------
    # 5. prompt context — THE single authoritative state block
    # ------------------------------------------------------------------
    def build_prompt_context(self, language: str | None = None,
                             stage_discovery: bool = True) -> str:
        """The single authoritative state block for the system prompt.

        Ground truth for FACTS always; the next-ACTION directive is
        stage-aware — during discovery it demands the one next question,
        after discovery it defers to the stage goal so booking/closing flows
        are never deadlocked by an unfilled optional-ish slot.
        """
        gaps = self.detect_missing_slots()
        lines = ["━━ CONVERSATION STATE (ground truth for FACTS — never re-ask ✓ fields) ━━"]
        # Compliance outranks the sales goal. _detect_dnd_request already
        # RECORDS the opt-out, but the spoken reply was left entirely to the
        # model on the assumption that "the prompt already handles polite
        # disengagement". Adversarial testing on 2026-08-04 disproved that:
        # after "stop calling me" the agent answered "We work with all major
        # providers... it's always worth a quick comparison." Once the caller
        # has opted out there is no pitch left to make.
        if self.extra_flags.get("dnd_requested"):
            lines.append(
                "⛔ THIS CALLER HAS ASKED NOT TO BE CONTACTED. Acknowledge it, "
                "confirm they will be removed, apologise briefly and close the "
                "call. Do NOT pitch, do NOT ask another qualifying question, do "
                "NOT offer a callback or 'one quick point' — no matter what the "
                "stage goal below says.")
        for name, spec in sorted(self.slots.items(), key=lambda kv: kv[1].priority):
            val = self.state.get(name)
            if name in self._pending_fields:
                lines.append(f"  ? {spec.display()}: awaiting caller confirmation")
            elif val is not None:
                lines.append(f"  ✓ {spec.display()}: {val}")
            elif spec.required:
                lines.append(f"  ✗ {spec.display()}: Unknown")
        if gaps["missing"]:
            pretty = ", ".join(self.slots[m].display() for m in gaps["missing"])
            lines.append(f"MISSING (priority order): {pretty}")
        lines.append(f"SUMMARY: {self.running_summary()}")
        lines.append("RULES:")
        lines.append("  • NEVER ask about any ✓ field — the caller already told you. "
                      "Acknowledge ✓ facts naturally when relevant.")
        # v2: read-back instruction for filled-but-unconfirmed readback slots.
        readback_due = [
            n for n, spec in self.slots.items()
            if getattr(spec, "confirm_readback", "")
            and self.state.get(n) is not None
            and n not in self.readback_done
            and n not in self._pending_fields
        ]
        for n in sorted(readback_due, key=lambda x: self.slots[x].priority):
            spec = self.slots[n]
            spoken = format_readback(self.state.get(n), spec.confirm_readback)
            lines.append(f"  • READ BACK the {spec.display().lower()} ONCE to confirm it — "
                         f"say it as: \"{spoken}\" — then continue.")
            self.readback_done.add(n)  # instruction is emitted exactly once
        if gaps["pending"]:
            # A confirmation is in flight (e.g. name). That confirmation IS
            # the next action — never stack a second question on top of it.
            pretty_p = ", ".join(self.slots[p].display() for p in gaps["pending"])
            for p in gaps["pending"]:
                spec = self.slots[p]
                if p != "name" and getattr(spec, "merge_policy", "") == "confirm":
                    spoken = format_readback(self.state.get(p),
                                             getattr(spec, "confirm_readback", ""))
                    lines.append(f"  • CONFIRM the {spec.display().lower()} the caller gave "
                                 f"(\"{spoken}\") — ask if it's correct, nothing else first.")
            lines.append(f"  • Awaiting confirmation of: {pretty_p}. Confirm that FIRST — "
                         "do not ask any other question this turn.")
        elif gaps["missing"] and stage_discovery:
            nq = self.generate_next_question(language)
            first_name = gaps["missing"][0]
            first = self.slots[first_name].display()
            self.note_asked(first_name)
            lines.append(f"  • Ask ONLY ONE question next, about: {first}."
                         + (f" Suggested phrasing: {nq}" if nq else ""))
        elif gaps["missing"]:
            pretty = ", ".join(self.slots[m].display() for m in gaps["missing"])
            lines.append(f"  • Still unknown: {pretty}. Weave in AT MOST one of these "
                         "only if it fits naturally — the CURRENT GOAL comes first.")
        else:
            lines.append("  • All required information is collected — do not re-ask "
                         "anything; progress the conversation per the CURRENT GOAL.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 6. audit log
    # ------------------------------------------------------------------
    def update_audit_log(
        self, field_name: str, old: Any, new: Any, source: str, utterance: str = "",
    ) -> AuditEntry:
        entry = AuditEntry(
            field=field_name, old=old, new=new, source=source,
            turn=self.turn_index,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            utterance=(utterance or "")[:120],
        )
        self.audit_log.append(entry)
        logger.info("[STATE] %s: %r → %r (source=%s turn=%d)",
                    field_name, old, new, source, self.turn_index)
        return entry

    # ------------------------------------------------------------------
    # Convenience / lifecycle
    # ------------------------------------------------------------------
    def observe_user(self, text: str) -> list[AuditEntry]:
        """The per-turn hot-path entry point: extract + merge in one call."""
        self.turn_index += 1
        extracted = self.extract_entities(text)
        return self.merge_state(extracted, source="regex", utterance=text)

    def set_externally(self, field_name: str, value: Any, source: str = "system") -> None:
        """Write-through for values captured by other subsystems (name flow,
        appointment slot matcher, legacy extractors)."""
        if field_name not in self.state:
            return
        self._pending_fields.discard(field_name)
        if value is None or self.state.get(field_name) == value:
            return
        old = self.state.get(field_name)
        self.state[field_name] = value
        self.update_audit_log(field_name, old, value, source)

    def mark_pending(self, field_name: str) -> None:
        """Field captured but awaiting caller confirmation (e.g. name)."""
        if field_name in self.state:
            self._pending_fields.add(field_name)

    def clear_pending(self, field_name: str) -> None:
        self._pending_fields.discard(field_name)

    def discard_pending(self, field_name: str) -> None:
        """Caller REJECTED the pending value: clear the flag AND null any
        value that leaked into the slot, with an audit entry."""
        self._pending_fields.discard(field_name)
        old = self.state.get(field_name)
        if field_name in self.state and old is not None:
            self.state[field_name] = None
            self.update_audit_log(field_name, old, None, "system", "caller rejected value")

    def running_summary(self) -> str:
        """Rolling template summary — rebuilt each turn from state, no LLM."""
        parts: list[str] = []
        known: list[str] = []
        cfg = self.state.get("bhk") or self.state.get("configuration")
        ptype = self.state.get("property_type")
        loc = self.state.get("location")
        budget = self.state.get("budget")
        if cfg or ptype or loc or budget:
            frag = "Customer wants"
            frag += f" a {cfg}" if cfg else " a property"
            if ptype:
                frag += f" {ptype.lower()}"
            if loc:
                frag += f" in {loc}"
            if budget:
                frag += f" under {budget}" if "-" not in str(budget) else f" in the {budget} range"
            parts.append(frag + ".")
        for name, spec in sorted(self.slots.items(), key=lambda kv: kv[1].priority):
            if name in ("bhk", "configuration", "property_type", "location", "budget"):
                continue
            if name in self._pending_fields:
                known.append(f"{spec.display()} awaiting caller confirmation.")
                continue
            val = self.state.get(name)
            if val is not None:
                known.append(spec.summary_known.format(value=val) if spec.summary_known
                             else f"{spec.display()}: {val}.")
            elif spec.required:
                parts_unknown = spec.summary_unknown or f"{spec.display()} not yet discussed."
                known.append(parts_unknown)
        parts.extend(known)
        return " ".join(parts) if parts else "No details collected yet."

    def to_dict(self) -> dict:
        gaps = self.detect_missing_slots()
        out = {
            "state": dict(self.state),
            "filled": gaps["filled"],
            "missing": gaps["missing"],
            "summary": self.running_summary(),
            "audit_log": [a.to_dict() for a in self.audit_log],
            "turns_observed": self.turn_index,
            # v2 — additive keys only (snapshot consumers rely on the rest):
            "skipped": gaps["skipped"],
            "blocked": gaps["blocked"],
        }
        if self.extra_flags:
            out["flags"] = dict(self.extra_flags)
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Per-agent slot sets
# ---------------------------------------------------------------------------

_Q = dict  # brevity below

_REAL_ESTATE_SLOTS: list[SlotSpec] = [
    SlotSpec("name", 10, True, "Name",
             _Q(hi="क्या मैं आपका नाम जान सकती हूँ?", en="May I know your name?"),
             summary_unknown="Name not yet captured."),
    SlotSpec("location", 20, True, "Location",
             _Q(hi="आप किस city में property देख रहे हैं?", en="Which city are you looking in?")),
    SlotSpec("bhk", 30, True, "Configuration",
             _Q(hi="आपको कितने BHK चाहिए?", en="What configuration are you looking for — 2BHK, 3BHK?")),
    SlotSpec("property_type", 35, False, "Property Type",
             _Q(hi="Flat चाहिए या villa/plot?", en="Apartment, villa or plot?")),
    SlotSpec("budget", 40, True, "Budget",
             _Q(hi="आपका comfortable budget क्या है?", en="What budget range are you comfortable with?")),
    SlotSpec("purpose", 50, False, "Purpose",
             _Q(hi="खुद रहने के लिए या investment के लिए?", en="Is this for self-use or investment?")),
    SlotSpec("timeline", 60, True, "Timeline",
             _Q(hi="आप कब तक purchase करने का सोच रहे हैं?", en="When are you planning to purchase?"),
             summary_unknown="Purchase timeline not yet discussed."),
    SlotSpec("loan_required", 70, True, "Loan Required",
             _Q(hi="क्या आप home loan लेने का सोच रहे हैं?", en="Will you be taking a home loan?"),
             summary_unknown="Loan requirement not yet discussed."),
    SlotSpec("occupation", 80, False, "Occupation",
             _Q(hi="आप क्या काम करते हैं?", en="What do you do professionally?")),
]

_MEETING_SLOTS: list[SlotSpec] = [
    SlotSpec("name", 10, True, "Name",
             _Q(hi="क्या मैं आपका नाम जान सकती हूँ?", en="May I know your name?"),
             summary_unknown="Name not yet captured."),
    # Deliberately NOT named "timeline": that would reuse the purchase-timeline
    # regexes and let small talk ("next month India aa raha hoon") permanently
    # fill the meeting time. This slot is filled from the appointment-booking
    # write-through (_sync_state_manager) or the LLM gap-filler only.
    SlotSpec("meeting_time", 20, True, "Meeting Time",
             _Q(hi="कौन सा दिन और time आपके लिए ठीक रहेगा?", en="What day and time works for you?"),
             summary_unknown="Meeting time not yet agreed."),
]


def build_state_manager(agent_def, language: str = "hi") -> ConversationStateManager:
    """Factory: build a per-call manager from the agent definition.

    Priority: explicit agent YAML `slots:` → derived default by agent type
    (real-estate agents get the full property slot set; others get the
    meeting/name set). qa_field_signals keys decide the agent type.
    """
    slots: list[SlotSpec] = []
    for s in getattr(agent_def, "slots", None) or []:
        # Copy ALL SlotDefinition fields (summary_known/unknown used to be
        # dropped here — the plan's L4 finding).
        _type = getattr(s, "type", "") or ""
        _policy = getattr(s, "merge_policy", "free") or "free"
        _readback = getattr(s, "confirm_readback", "") or ""
        _max_asks = int(getattr(s, "max_asks", 0) or 0)
        # B6 engine-level contact provisioning (2026-07-30, platform-wide —
        # every existing and future agent): a slot NAMED email is email-typed
        # even without YAML migration (no legacy name-keyed email extractor
        # exists, so typing it costs nothing), and contact-typed slots get
        # the confirm/read-back/cap machinery unless the YAML says otherwise.
        # This is what makes captured emails pending-until-confirmed and
        # correctable instead of first-guess-immortal.
        # "text" counts as untyped here: it is the generic default the
        # Copilot used to stamp on every slot (the live LesCall agent's
        # email slot is `type: text`), not a deliberate opt-out.
        if _type in ("", "text") and s.name.strip().lower() in (
                "email", "email_id", "email_address"):
            _type = "email"
        if _type == "email":
            # Email is the incident class: pending-until-confirmed, spelled
            # read-back, capped asks. Phone deliberately keeps its authored
            # policy — live outbound flows capture numbers passively and a
            # forced confirm gate could stall their stage advancement; it
            # only gains the digit read-back hint when none is set.
            if _policy == "free":
                _policy = "confirm"
            if not _readback:
                _readback = "spelled"
            if _max_asks <= 0:
                _max_asks = 2
        elif _type == "phone" and not _readback:
            _readback = "digit_groups_2"
        slots.append(SlotSpec(
            name=s.name, priority=s.priority, required=s.required,
            label=getattr(s, "label", "") or "",
            question=dict(getattr(s, "question", {}) or {}),
            summary_known=getattr(s, "summary_known", "") or "",
            summary_unknown=getattr(s, "summary_unknown", "") or "",
            type=_type,
            enum_values=list(getattr(s, "enum_values", None) or []),
            enum_synonyms=dict(getattr(s, "enum_synonyms", None) or {}),
            extract_patterns=list(getattr(s, "extract_patterns", None) or []),
            depends_on=list(getattr(s, "depends_on", None) or []),
            merge_policy=_policy,
            confirm_readback=_readback,
            max_asks=_max_asks,
            reask_question=dict(getattr(s, "reask_question", None) or {}),
        ))
    if not slots:
        qa_keys = set((agent_def.knowledge_base.qa_field_signals or {}).keys())
        stage_ids = {s.id for s in agent_def.call_flow.stages}
        # v2 role shim: custom-id stages with an explicit scheduling/
        # confirmation role also count as booking flows (the id-literal set
        # stays as the fallback for legacy agents).
        from .stage_roles import stage_role as _stage_role_of
        stage_roles = {_stage_role_of(s, s.id) for s in agent_def.call_flow.stages}
        if "bhk" in qa_keys:
            # BHK is the real-estate discriminator; location/budget alone
            # appear in clinics and product-sales agents too.
            slots = list(_REAL_ESTATE_SLOTS)
        elif (stage_ids & {"INVITE_VISIT", "SLOT_BOOKING", "CONFIRMATION"}
              or stage_roles & {"scheduling", "confirmation"}):
            # Books meetings/appointments → track name + agreed time.
            slots = list(_MEETING_SLOTS)
        else:
            # Pure pitch agents (no booking stage): only track the name —
            # never pressure the LLM to ask for a meeting time that the
            # call flow has no stage to handle.
            slots = [s for s in _MEETING_SLOTS if s.name == "name"]
    kb = agent_def.knowledge_base
    slot_names = {s.name for s in slots}
    default_intent = "buy_property" if {"location", "bhk"} & slot_names else ""
    return ConversationStateManager(
        slots=slots,
        locations=list(kb.locations or []),
        location_aliases=list(kb.location_aliases or []),
        default_intent=default_intent,
        language=language,
    )
