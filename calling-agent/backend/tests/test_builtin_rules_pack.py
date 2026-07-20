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
