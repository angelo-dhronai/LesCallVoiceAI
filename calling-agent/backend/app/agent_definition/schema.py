"""
AgentDefinition — the complete configuration contract for a voice agent.

Phase 0: Python dataclasses only. Pramukh Group's values live in
pramukh_agent.py. Phase 1 will add JSON/YAML serialization and a loader.

Nothing in this module has any dependency on the voice pipeline or
conversation engine — it is pure data.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

@dataclass
class AgentIdentity:
    """Who the agent is and what company it represents."""
    agent_name: str           # spoken name, e.g. "Vishal Patel"
    company_name: str         # e.g. "Pramukh Group"
    company_tagline: str      # short brand descriptor for the system prompt
    role_description: str     # e.g. "inbound property sales consultant"
    contact: str              # phone / WhatsApp shown to callers, e.g. "+91 87583 70000"
    pronouns: str = "she"     # legacy field — prefer personality.pronouns; kept for backward compat


# ---------------------------------------------------------------------------
# Personality
# ---------------------------------------------------------------------------

@dataclass
class PersonalityConfig:
    """Controls the agent's speaking style, energy, and overall vibe."""
    pronouns: str = "she"              # "she" or "he" — enforced in all languages
    energy: str = "medium"             # "low" | "medium" | "high"
    tone: str = "conversational"       # "professional" | "conversational" | "friendly" | "formal"
    style: str = "natural"             # "concise" | "natural" | "detailed"
    lingo: str = ""                    # vocabulary/jargon preferences
    custom_instructions: str = ""      # extra vibe or persona notes


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------

# The only TTS models Sarvam accepts; anything else 400s every utterance and
# the call goes silent. Platform default is bulbul:v3 unless explicitly set.
VALID_TTS_MODELS = ("bulbul:v2", "bulbul:v3-beta", "bulbul:v3")
DEFAULT_TTS_MODEL = "bulbul:v3"


@dataclass
class TTSVoiceConfig:
    """TTS model + speaker for one language."""
    model: str = DEFAULT_TTS_MODEL   # must be one of VALID_TTS_MODELS
    speaker: str = "shubh"           # e.g. "shubh"
    pace: float = 1.0  # speaking rate; >1.0 = faster (snappier phone delivery)

    def __post_init__(self) -> None:
        model = (self.model or "").strip()
        if model not in VALID_TTS_MODELS:
            # A speaker name sometimes lands in the model field (e.g.
            # model: shubh). Salvage it as the speaker if that slot is empty,
            # then fall back to the platform default model.
            if model and not (self.speaker or "").strip():
                self.speaker = model.lower()
            if model:
                logging.getLogger(__name__).warning(
                    "[TTSVoiceConfig] invalid tts model %r — using %s",
                    self.model, DEFAULT_TTS_MODEL)
            self.model = DEFAULT_TTS_MODEL
        if not (self.speaker or "").strip():
            self.speaker = "shubh"
        try:
            self.pace = float(self.pace)
        except (TypeError, ValueError):
            self.pace = 1.0
        if not (0.5 <= self.pace <= 2.0):
            self.pace = 1.0


@dataclass
class LanguageConfig:
    """Which languages the agent speaks and how."""
    supported: list[str]                    # BCP-47 short codes, e.g. ["hi", "en", "gu"]
    default: str                            # used before caller's language is detected
    tts_voices: dict[str, TTSVoiceConfig]   # lang_code -> TTS config
    # Per-language style guidance injected into the system prompt.
    # Describes register, vocabulary preferences, code-switching norms.
    register_notes: dict[str, str]          # lang_code -> guidance paragraph
    # When set (e.g. "hi-IN"), STT is LOCKED to this language for every turn
    # instead of Sarvam auto-detecting per segment. Use for single-language
    # agents where auto-detect misfires (e.g. Hindi mis-tagged as Punjabi/
    # Tamil/Malayalam). Empty string = auto-detect (default behaviour).
    stt_forced_language: str = ""
    # When True, the LLM is told to reply in natural Hinglish (Hindi freely
    # code-mixed with everyday English words) instead of being forced into a
    # single pure language. Makes phone conversations sound human. Default
    # False preserves the strict single-language behaviour for other agents.
    allow_code_mixing: bool = False


# ---------------------------------------------------------------------------
# Calendar / Appointment Slots
# ---------------------------------------------------------------------------

@dataclass
class AppointmentSlot:
    """A single bookable time window."""
    day: str    # e.g. "Tomorrow", "This Saturday"
    time: str   # e.g. "10:00 AM"


@dataclass
class CalendarConfig:
    """Appointment availability configuration."""
    type: str                           # "mock" | "webhook" (Phase 2+)
    mock_slots: list[AppointmentSlot]   # used when type == "mock"
    # Localised slot-list strings for injection into the system prompt.
    # Keyed by the same short lang codes used in LanguageConfig.supported.
    # Phase 2 will auto-generate these from mock_slots.
    slots_text: dict[str, str]


# ---------------------------------------------------------------------------
# Call Flow / Stage Machine
# ---------------------------------------------------------------------------

@dataclass
class StageDefinition:
    """One stage in the linear conversation flow."""
    id: str     # matches the STAGE_* constants in conversation.py
    order: int  # position in the linear sequence (0-indexed)
    # LLM stage goal — what the agent must accomplish before advancing.
    # Written in English; the LLM responds in the detected caller language.
    goal: str
    # After this many turns in this stage, auto-advance to the next stage.
    # Override 99 to block automatic advancement (use booking events only).
    turns_until_advance: int = 8


@dataclass
class SlotDefinition:
    """One piece of information the agent must collect during the call.

    Consumed by app/voice/state_manager.py: slots drive entity extraction,
    missing-field detection, and dynamic question generation. Optional per
    agent — agents without a `slots:` section get a sensible default set
    derived from their qa_field_signals.
    """
    name: str                                   # canonical key, e.g. "location"
    priority: int = 100                         # lower = asked earlier
    required: bool = True                       # counts toward missing-slot list
    label: str = ""                             # display label ("Configuration")
    question: dict[str, str] = field(default_factory=dict)  # lang -> question


@dataclass
class CallFlowConfig:
    """Ordered list of stages and global flow parameters."""
    stages: list[StageDefinition]
    # Number of objections / deferrals before ending the call graciously.
    decline_threshold: int = 2

    # Convenience accessors -------------------------------------------------

    @property
    def stage_goals(self) -> dict[str, str]:
        return {s.id: s.goal for s in self.stages}

    @property
    def stage_order(self) -> dict[str, int]:
        return {s.id: s.order for s in self.stages}


# ---------------------------------------------------------------------------
# Response Bank — all multilingual canned responses
# ---------------------------------------------------------------------------

@dataclass
class ResponseBank:
    """
    Every canned response the agent can emit without calling the LLM.

    Keys for per-language dicts are the same short codes used in
    LanguageConfig.supported ("hi", "en", "gu", …).
    """
    # The opening utterance the agent delivers when the call connects.
    # Usually bilingual so the caller can reply in their preferred language.
    opening_greeting: str = ""

    # Played when the agent is waiting >FILLER_TIMEOUT_S for the LLM.
    filler_responses: dict[str, list[str]] = field(default_factory=dict)

    # "Who is calling?" — fast deterministic answer
    identity_responses: dict[str, str] = field(default_factory=dict)

    # First objection / deferral ("call me later", "not interested")
    objection_responses: dict[str, list[str]] = field(default_factory=dict)

    # Second objection — warm end of call
    decline_farewell: dict[str, str] = field(default_factory=dict)

    # Language-switch confirmation ("Sure, let's speak in Hindi")
    lang_switch_responses: dict[str, str] = field(default_factory=dict)

    # "I couldn't hear you / please repeat"
    lang_fallbacks: dict[str, str] = field(default_factory=dict)

    # LLM timed out mid-stream — spoken while retrying silently
    timeout_fallbacks: dict[str, str] = field(default_factory=dict)

    # Absolute last resort — single universal string, no LLM
    response_fallback: str = ""

    # Warm close when all mandatory fields are collected and confirmed
    graceful_close: dict[str, str] = field(default_factory=dict)

    # Intent-keyed responses for common shortcut intents (greeting, farewell,
    # gratitude, closing). Values can be str or list[str] (rotated).
    localized_responses: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Stage-specific proactive nudges when the LLM hasn't naturally advanced.
    # Keyed by stage ID then by language.
    proactive_prompts: dict[str, dict[str, str]] = field(default_factory=dict)

    # Optional: alternate decline/end-of-call responses (override decline_farewell)
    decline_responses: dict[str, list[str]] = field(default_factory=dict)  # lang -> [resp, ...]

    # Optional: template for presenting appointment slots ("Available: {slots}")
    slots_prompt: dict[str, str] = field(default_factory=dict)  # lang -> template


# ---------------------------------------------------------------------------
# Knowledge Base
# ---------------------------------------------------------------------------

@dataclass
class STTCorrections:
    """
    Domain-specific text substitutions applied to raw STT output.

    Each dict maps a lowercase source substring to its corrected form.
    All corrections are applied case-insensitively before intent detection.
    Agents that need no correction may omit any or all fields.
    """
    city_corrections: dict[str, str] = field(default_factory=dict)
    phonetic_corrections: dict[str, str] = field(default_factory=dict)
    real_estate_corrections: dict[str, str] = field(default_factory=dict)
    project_name_corrections: dict[str, str] = field(default_factory=dict)


@dataclass
class KnowledgeBase:
    """
    Domain knowledge used for project/product matching and STT correction.

    known_projects:
        Maps lowercase substring keys to (canonical_name, location) tuples.
        Keys are sorted longest-first at lookup time so multi-word names
        (e.g. "aranya ii") match before shorter substrings (e.g. "aranya").

    locations:
        Canonical location names the agent serves. Used for STT correction
        (fuzzy-matched against caller speech) and for building the system
        prompt geography section.

    portfolio_prompt_text:
        The full plain-English paragraph injected into the system prompt's
        PROJECT PORTFOLIO section.

    location_aliases:
        All script/language variants of location names (Latin, Devanagari,
        Gujarati, etc.) used by detect_intent() for location_selection intent.

    objection_patterns / time_patterns:
        Substring lists for deterministic intent classification. If empty,
        the corresponding intent type is never triggered by pattern matching.

    qa_field_signals:
        Maps qualification field names (e.g. "name", "budget") to lists of
        trigger phrases. Used by QATracker to prevent re-asking answered fields.

    stt_corrections:
        Domain-specific STT post-processing substitution tables.
    """
    known_projects: dict[str, tuple[str, str]]  # key -> (canonical_name, location)
    locations: list[str]                         # e.g. ["Surat", "Vapi", "Silvassa"]
    portfolio_prompt_text: str                   # injected verbatim into system prompt
    # Label for the knowledge section injected into the LLM system prompt.
    # Override per-agent: "DOCTOR DIRECTORY", "PRODUCT CATALOGUE", etc.
    portfolio_section_label: str = "KNOWLEDGE BASE"
    location_aliases: list[str] = field(default_factory=list)
    objection_patterns: list[str] = field(default_factory=list)
    time_patterns: list[str] = field(default_factory=list)
    qa_field_signals: dict[str, list[str]] = field(default_factory=dict)
    stt_corrections: STTCorrections = field(default_factory=STTCorrections)


# ---------------------------------------------------------------------------
# LLM Configuration
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    """
    All LLM-related tunables that were previously scattered as module-level
    constants in conversation.py.
    """
    model: str                      # e.g. "gpt-4o-mini"
    max_tokens: int                 # generation cap per response
    timeout_s: float                # per-turn LLM call timeout
    stream_timeout_s: float         # hard deadline for streaming response
    filler_timeout_s: float         # how long to wait before playing a filler
    intent_classify_timeout_s: float
    max_response_length: int        # character cap before truncation
    # Additional domain-specific NEVER/ALWAYS rules appended to the
    # platform's built-in rules in the system prompt.
    extra_never_rules: list[str] = field(default_factory=list)
    extra_always_rules: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level AgentDefinition
# ---------------------------------------------------------------------------

@dataclass
class AgentDefinition:
    """
    Complete, self-contained description of one voice agent.

    The platform runtime (ConversationRouter, VoicePipeline) reads this
    object at startup and operates entirely from it — no domain knowledge
    is embedded in the runtime code itself.

    Phase 0: instantiated as a Python object (see pramukh_agent.py).
    Phase 1: loaded from a YAML/JSON file via AgentDefinition.from_file().
    """
    agent_id: str       # unique slug, e.g. "pramukh-v1"
    version: str        # semver string, e.g. "1.0.0"

    identity: AgentIdentity
    languages: LanguageConfig
    call_flow: CallFlowConfig
    knowledge_base: KnowledgeBase
    response_bank: ResponseBank
    calendar: CalendarConfig
    llm_config: LLMConfig
    personality: PersonalityConfig = field(default_factory=PersonalityConfig)
    # Optional conversation-state slot definitions (see SlotDefinition).
    slots: list[SlotDefinition] = field(default_factory=list)
    # "inbound"  — the agent answers calls: welcome the caller, listen first,
    #              resolve issues/requests/queries, book appointments and visits.
    # "outbound" — the agent places calls: confirm who answered, state the
    #              purpose, pitch products/services, and lead the conversation.
    call_direction: str = "inbound"

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict) -> "AgentDefinition":
        """Reconstruct an AgentDefinition from a plain nested dict (e.g. from YAML)."""
        identity = AgentIdentity(**d["identity"])

        lang_d = d["languages"]
        tts_voices = {k: TTSVoiceConfig(**v) for k, v in lang_d["tts_voices"].items()}
        languages = LanguageConfig(
            supported=lang_d["supported"],
            default=lang_d["default"],
            tts_voices=tts_voices,
            register_notes=lang_d["register_notes"],
            stt_forced_language=lang_d.get("stt_forced_language", ""),
            allow_code_mixing=lang_d.get("allow_code_mixing", False),
        )

        cal_d = d["calendar"]
        mock_slots = [AppointmentSlot(**s) for s in cal_d["mock_slots"]]
        calendar = CalendarConfig(
            type=cal_d["type"],
            mock_slots=mock_slots,
            slots_text=cal_d["slots_text"],
        )

        cf_d = d["call_flow"]
        stages = [StageDefinition(**s) for s in cf_d["stages"]]
        call_flow = CallFlowConfig(
            stages=stages,
            decline_threshold=cf_d.get("decline_threshold", 2),
        )

        kb_d = d["knowledge_base"]
        # YAML loads tuple values as lists — convert back to tuples.
        known_projects = {k: tuple(v) for k, v in kb_d["known_projects"].items()}
        stt_d = kb_d.get("stt_corrections", {})
        stt_corrections = STTCorrections(
            city_corrections=stt_d.get("city_corrections", {}),
            phonetic_corrections=stt_d.get("phonetic_corrections", {}),
            real_estate_corrections=stt_d.get("real_estate_corrections", {}),
            project_name_corrections=stt_d.get("project_name_corrections", {}),
        )
        knowledge_base = KnowledgeBase(
            known_projects=known_projects,
            locations=kb_d["locations"],
            portfolio_prompt_text=kb_d["portfolio_prompt_text"],
            portfolio_section_label=kb_d.get("portfolio_section_label", "KNOWLEDGE BASE"),
            location_aliases=kb_d.get("location_aliases", []),
            objection_patterns=kb_d.get("objection_patterns", []),
            time_patterns=kb_d.get("time_patterns", []),
            qa_field_signals=kb_d.get("qa_field_signals", {}),
            stt_corrections=stt_corrections,
        )

        response_bank = ResponseBank(**d["response_bank"])

        llm_d = d.get("llm_config", {})
        llm_config = LLMConfig(
            model=llm_d.get("model", "gpt-4o-mini"),
            max_tokens=llm_d.get("max_tokens", 120),
            timeout_s=llm_d.get("timeout_s", llm_d.get("timeout_seconds", 8.0)),
            stream_timeout_s=llm_d.get("stream_timeout_s", 10.0),
            filler_timeout_s=llm_d.get("filler_timeout_s", 1.5),
            intent_classify_timeout_s=llm_d.get("intent_classify_timeout_s", 1.0),
            max_response_length=llm_d.get("max_response_length", 150),
            extra_never_rules=llm_d.get("extra_never_rules", []),
            extra_always_rules=llm_d.get("extra_always_rules", []),
        )

        slots = [
            SlotDefinition(
                name=s["name"],
                priority=s.get("priority", 100),
                required=s.get("required", True),
                label=s.get("label", ""),
                question=s.get("question", {}) or {},
            )
            for s in (d.get("slots") or [])
        ]

        pers_d = d.get("personality", {})
        personality = PersonalityConfig(
            pronouns=pers_d.get("pronouns", d.get("identity", {}).get("pronouns", "she")),
            energy=pers_d.get("energy", "medium"),
            tone=pers_d.get("tone", "conversational"),
            style=pers_d.get("style", "natural"),
            lingo=pers_d.get("lingo", ""),
            custom_instructions=pers_d.get("custom_instructions", ""),
        )

        return cls(
            agent_id=d["agent_id"],
            version=d["version"],
            identity=identity,
            languages=languages,
            call_flow=call_flow,
            knowledge_base=knowledge_base,
            response_bank=response_bank,
            calendar=calendar,
            llm_config=llm_config,
            personality=personality,
            slots=slots,
            call_direction=(d.get("call_direction") or "inbound").strip().lower(),
        )

    @classmethod
    def from_yaml(cls, path: "str | Path") -> "AgentDefinition":
        """Load an AgentDefinition from a YAML file."""
        import yaml
        from pathlib import Path as _Path
        with open(_Path(path), encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    def to_yaml(self, path: "str | Path") -> None:
        """Persist this AgentDefinition to a YAML file."""
        import dataclasses
        import yaml
        from pathlib import Path as _Path

        def _coerce(obj: Any) -> Any:
            """Convert dataclasses and tuples to YAML-serialisable primitives."""
            if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                return {k: _coerce(v) for k, v in dataclasses.asdict(obj).items()}
            if isinstance(obj, tuple):
                return list(obj)
            if isinstance(obj, list):
                return [_coerce(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _coerce(v) for k, v in obj.items()}
            return obj

        data = _coerce(self)
        out = _Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: dump to a temp file in the same directory, then
        # os.replace() over the target. A call arriving mid-write can now only
        # ever see the complete old file or the complete new file — a torn
        # half-written YAML used to parse-fail and drop the caller onto the
        # fallback agent. watchfiles treats the replace as a change, so the
        # registry hot-reload keeps working.
        import os as _os
        import tempfile as _tempfile
        fd, tmp_path = _tempfile.mkstemp(
            prefix=out.name + ".", suffix=".tmp", dir=str(out.parent))
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                f.flush()
                _os.fsync(f.fileno())
            _os.replace(tmp_path, out)
        except BaseException:
            try:
                _os.unlink(tmp_path)
            except OSError:
                pass
            raise
