"""
F2 — guardrails.builtin_rules_pack ('real_estate' default vs 'generic') and
the strict KNOWLEDGE GROUNDING band.

Pins today's behaviour:
- default agent (no guardrails section) renders the real-estate FIRM RULES
  band ('site visit', 'property pitches');
- builtin_rules_pack='generic' renders the industry-neutral band: no
  site-visit/property/portfolio/builders wording, mission/catalogue lines
  instead, universal hygiene rules unchanged;
- honesty.forbidden_fabrications overrides the generic fabrication default;
- knowledge_grounding_block gates on honesty.knowledge_grounding == 'strict'
  (absent/other → [] exactly) and names the portfolio_section_label;
- a default agent's FULL system prompt carries no KNOWLEDGE GROUNDING band.

No network, no LLM — prompt_blocks functions + one offline prompt build.
"""
from __future__ import annotations

from app.agent_definition.schema import AgentDefinition
from app.voice import prompt_blocks as pb
from app.voice.conversation import ConversationRouter


def make_agent(**over) -> AgentDefinition:
    d = {
        "agent_id": "t_rules",
        "version": "1.0.0",
        "identity": {"agent_name": "Asha", "company_name": "TestCo"},
        "languages": {"supported": ["en", "hi"], "default": "en",
                      "tts_voices": {}},
        "call_flow": {"stages": [
            {"id": "S1", "order": 0, "goal": "one", "turns_until_advance": 8},
            {"id": "S2", "order": 1, "goal": "two", "turns_until_advance": 99},
        ]},
        "response_bank": {"opening_greeting": "hi"},
    }
    d.update(over)
    return AgentDefinition.from_dict(d)


UNIVERSAL_LINES = [
    "✓ Use contractions: I'll, I'm, we've, it's, don't, can't — always",
    "✓ Open each turn with a varied natural acknowledgment "
    "(Got it / Sure / Right / Absolutely — never twice in a row)",
    "✗ NEVER ask for information that is already marked ✓ in CONVERSATION STATE",
]


# ── (a) default pack = real_estate ──────────────────────────────────────────

def test_default_agent_renders_real_estate_band():
    agent = make_agent()  # no guardrails section at all
    band = pb.firm_rules_block(agent, lang="en")
    joined = "\n".join(band)
    assert band[1] == "━━ FIRM RULES ━━"
    assert "site visit" in joined
    assert "property pitches" in joined
    for line in UNIVERSAL_LINES:
        assert line in band


# ── (b) generic pack ────────────────────────────────────────────────────────

def test_generic_pack_is_industry_neutral():
    agent = make_agent(guardrails={"builtin_rules_pack": "generic"})
    band = pb.firm_rules_block(agent, lang="en")
    joined = "\n".join(band)
    for banned in ("site visit", "property", "portfolio", "builders"):
        assert banned not in joined.lower(), banned
    assert "CALL MISSION" in joined
    assert "catalogue" in joined
    assert "NEVER make up products, prices or dates" in joined
    for line in UNIVERSAL_LINES:
        assert line in band


# ── (c) custom forbidden_fabrications wins over the generic default ─────────

def test_generic_pack_custom_fabrication_line_wins():
    agent = make_agent(guardrails={
        "builtin_rules_pack": "generic",
        "honesty": {"forbidden_fabrications": ["inventory"]},
    })
    band = pb.firm_rules_block(agent, lang="en")
    joined = "\n".join(band)
    assert "✗ NEVER make up inventory not listed above" in band
    assert "NEVER make up products, prices or dates" not in joined


# ── (d) knowledge_grounding_block gating ────────────────────────────────────

def test_knowledge_grounding_strict_renders_band_with_label():
    agent = make_agent(
        guardrails={"honesty": {"knowledge_grounding": "strict"}},
        knowledge_base={"portfolio_section_label": "FABRIC CATALOGUE"},
    )
    band = pb.knowledge_grounding_block(agent)
    assert band[1] == "━━ KNOWLEDGE GROUNDING (STRICT) ━━"
    joined = "\n".join(band)
    assert "ONLY source of truth" in joined
    assert "FABRIC CATALOGUE" in joined


def test_knowledge_grounding_absent_key_renders_nothing():
    agent = make_agent()
    assert pb.knowledge_grounding_block(agent) == []


def test_knowledge_grounding_other_value_renders_nothing():
    agent = make_agent(guardrails={"honesty": {"knowledge_grounding": "loose"}})
    assert pb.knowledge_grounding_block(agent) == []


# ── (e) full prompt of a legacy-shaped agent has no grounding band ──────────

def test_full_prompt_default_agent_has_no_grounding_band():
    agent = make_agent()
    router = ConversationRouter(agent_def=agent, default_language="en")
    router._available_calendar_slots = lambda: []  # deterministic, no I/O
    prompt = router._build_system_prompt()
    assert "KNOWLEDGE GROUNDING" not in prompt


# ── (f) slot-aware scheduling rules (2026-07-28) ────────────────────────────
# exact_n + empty calendar used to ORDER the model to offer "exactly TWO
# specific time options" it did not have — live call invented "Tuesday at
# 10 AM or Wednesday at 2 PM". Slotless agents now get a no-invention rule.

GENERIC_EXACT_N = [
    "✗ NEVER give more than 2 time options when offering an appointment or callback",
    "✗ NEVER ask an open 'when suits you?' — always give exactly TWO specific time options",
]
RE_EXACT_N = [
    "✗ NEVER give more than 2 time options for site visit",
    "✗ NEVER say 'When can you come?' — always give exactly TWO specific time options",
]
SLOTS = {"type": "mock", "mock_slots": [{"day": "Tomorrow", "time": "11:00 AM"}]}


def test_exact_n_without_slots_emits_no_invention_rule():
    agent = make_agent(guardrails={"builtin_rules_pack": "generic"})
    joined = "\n".join(pb.firm_rules_block(agent, lang="en"))
    for line in GENERIC_EXACT_N:
        assert line not in joined
    assert "NEVER invent or state specific times, dates or slots" in joined
    assert "ask what day and time suits the caller" in joined


def test_exact_n_with_slots_is_byte_identical():
    agent = make_agent(guardrails={"builtin_rules_pack": "generic"},
                       calendar=SLOTS)
    band = pb.firm_rules_block(agent, lang="en")
    for line in GENERIC_EXACT_N:
        assert line in band          # today's exact strings, pinned verbatim
    assert "NEVER invent or state specific times" not in "\n".join(band)


def test_real_estate_pack_slot_awareness():
    slotless = "\n".join(pb.firm_rules_block(make_agent(), lang="en"))
    assert RE_EXACT_N[0] not in slotless
    assert "for the site visit — no calendar is configured" in slotless
    with_slots = pb.firm_rules_block(make_agent(calendar=SLOTS), lang="en")
    for line in RE_EXACT_N:
        assert line in with_slots


def test_scheduling_enabled_false_suppresses_sched_rules():
    agent = make_agent(guardrails={"builtin_rules_pack": "generic"},
                       call_flow={"stages": [
                           {"id": "S1", "order": 0, "goal": "one"}],
                           "scheduling": {"enabled": False}})
    joined = "\n".join(pb.firm_rules_block(agent, lang="en"))
    assert "time options" not in joined
    assert "NEVER invent or state specific times" not in joined
    # rest of the band intact
    assert "✗ NEVER dump the whole catalogue on one caller" in joined


def test_flexible_style_with_slots_unchanged_without_gets_no_invention():
    line = "✓ Offer available time slots flexibly and accept a caller-proposed time when it matches a free slot"
    flex_flow = {"stages": [{"id": "S1", "order": 0, "goal": "one"}],
                 "scheduling": {"style": "flexible"}}
    with_slots = pb.firm_rules_block(
        make_agent(call_flow=flex_flow, calendar=SLOTS), lang="en")
    assert line in with_slots
    # flexible + empty calendar would reference free slots that don't exist —
    # slotless agents get the no-invention rule regardless of style.
    slotless = "\n".join(pb.firm_rules_block(
        make_agent(call_flow=flex_flow), lang="en"))
    assert line not in slotless
    assert "NEVER invent or state specific times" in slotless


# ── (g) llm_config.extra_never/always_rules now render (2026-07-28) ─────────

def test_extra_rules_render_after_guardrail_rules():
    agent = make_agent(
        guardrails={"always_rules": ["Always be brief"]},
        llm_config={"extra_never_rules": ["Never quote prices"],
                    "extra_always_rules": ["Always keep replies to 1-2 short spoken sentences."]},
    )
    band = pb.firm_rules_block(agent, lang="en")
    assert "✓ be brief" in band
    assert "✗ NEVER quote prices" in band
    assert "✓ keep replies to 1-2 short spoken sentences." in band
    # ordering: guardrail rule before the llm_config extras
    assert band.index("✓ be brief") < band.index("✗ NEVER quote prices")


def test_extra_rules_empty_band_unchanged():
    with_empty = pb.firm_rules_block(
        make_agent(llm_config={"extra_never_rules": [],
                               "extra_always_rules": []}), lang="en")
    without = pb.firm_rules_block(make_agent(), lang="en")
    assert with_empty == without
