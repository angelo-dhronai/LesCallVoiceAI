"""
Unit tests for the Conversation State Management layer.

Run:  cd calling-agent/backend && ../venv/bin/python -m pytest tests/test_state_manager.py -v
"""
from __future__ import annotations

import json
import time

import pytest

from app.voice.state_manager import (
    ConversationStateManager,
    SlotSpec,
    _REAL_ESTATE_SLOTS,
)


def make_mgr(**kw) -> ConversationStateManager:
    return ConversationStateManager(
        slots=list(_REAL_ESTATE_SLOTS),
        locations=["Surat", "Vapi", "Silvassa"],
        location_aliases=["surat", "vapi", "silvassa", "सूरत", "वापी", "सिलवासा",
                          "સુરત", "વાપી", "સિલવાસા"],
        **kw,
    )


# ---------------------------------------------------------------------------
# 1. Multi-slot extraction from one utterance (the assignment's core example)
# ---------------------------------------------------------------------------

class TestExtraction:
    def test_assignment_example(self):
        m = make_mgr()
        m.observe_user("I want a 3BHK apartment in Vapi under 50 lakhs")
        assert m.state["bhk"] == "3BHK"
        assert m.state["property_type"] == "Apartment"
        assert m.state["location"] == "Vapi"
        assert m.state["budget"] == "50 Lakhs"

    def test_hinglish(self):
        m = make_mgr()
        m.observe_user("vapi mein teen bhk chahiye pachas lakh tak ka")
        assert m.state["bhk"] == "3BHK"
        assert m.state["location"] == "Vapi"
        assert m.state["budget"] == "50 Lakhs"

    def test_devanagari(self):
        m = make_mgr()
        m.observe_user("मुझे वापी में तीन बीएचके चाहिए पचास लाख तक")
        assert m.state["bhk"] == "3BHK"
        assert m.state["location"] == "Vapi"
        assert m.state["budget"] == "50 Lakhs"

    def test_crore_budget(self):
        m = make_mgr()
        m.observe_user("budget around 1.2 crore hai")
        assert m.state["budget"] == "1.2 Crore"

    def test_budget_range(self):
        m = make_mgr()
        m.observe_user("40-50 lakh ke beech mein")
        assert m.state["budget"] == "40-50 Lakhs"

    def test_timeline_and_loan(self):
        m = make_mgr()
        m.observe_user("next month lena hai aur home loan chahiye")
        assert m.state["timeline"] == "Next month"
        assert m.state["loan_required"] == "Yes"

    def test_loan_no_cash(self):
        m = make_mgr()
        m.observe_user("no loan, full cash payment karunga")
        assert m.state["loan_required"] == "No"

    def test_purpose_investment(self):
        m = make_mgr()
        m.observe_user("investment ke liye dekh raha hoon, rental income ke liye")
        assert m.state["purpose"] == "Investment"

    def test_noisy_fragment_extracts_what_parses(self):
        m = make_mgr()
        # Simulated garbled ASR — only the intelligible parts should extract
        m.observe_user("uhh kkr vapi mein something xyzw 2 bhk")
        assert m.state["location"] == "Vapi"
        assert m.state["bhk"] == "2BHK"
        assert m.state["budget"] is None

    def test_empty_and_garbage_safe(self):
        m = make_mgr()
        assert m.observe_user("") == []
        assert m.observe_user("   ") == []
        assert m.observe_user("hello haan ji") == []


# ---------------------------------------------------------------------------
# 2. Merge semantics: null-protection, latest-wins, fill_only
# ---------------------------------------------------------------------------

class TestMerge:
    def test_never_overwrite_with_null(self):
        m = make_mgr()
        m.observe_user("3BHK in Vapi")
        m.merge_state({"bhk": None, "location": ""}, source="llm")
        assert m.state["bhk"] == "3BHK"
        assert m.state["location"] == "Vapi"

    def test_latest_wins_with_audit(self):
        m = make_mgr()
        m.observe_user("I need a 3BHK")
        m.observe_user("Actually make it a 2BHK")
        assert m.state["bhk"] == "2BHK"
        changes = [a for a in m.audit_log if a.field == "bhk"]
        assert len(changes) == 2
        assert changes[-1].old == "3BHK" and changes[-1].new == "2BHK"

    def test_fill_only_never_clobbers(self):
        m = make_mgr()
        m.observe_user("2BHK chahiye")
        m.merge_state({"bhk": "4BHK"}, source="llm", fill_only=True)
        assert m.state["bhk"] == "2BHK"          # stale LLM result rejected
        m.merge_state({"budget": "60 Lakhs"}, source="llm", fill_only=True)
        assert m.state["budget"] == "60 Lakhs"   # gap-filling allowed

    def test_unknown_slot_ignored(self):
        m = make_mgr()
        m.merge_state({"favourite_color": "blue"})
        assert "favourite_color" not in m.state

    def test_negation_correction_same_utterance(self):
        m = make_mgr()
        m.observe_user("3 bhk nahi, 2 bhk chahiye")
        assert m.state["bhk"] == "2BHK"

    def test_fragmented_across_turns(self):
        m = make_mgr()
        m.observe_user("vapi mein dekh raha hoon")
        m.observe_user("2 bhk chahiye")
        m.observe_user("budget 30 lakh hai")
        assert m.state["location"] == "Vapi"
        assert m.state["bhk"] == "2BHK"
        assert m.state["budget"] == "30 Lakhs"


# ---------------------------------------------------------------------------
# 3. Missing-slot detection + priority ordering
# ---------------------------------------------------------------------------

class TestMissingSlots:
    def test_filled_and_missing(self):
        m = make_mgr()
        m.observe_user("I want a 3BHK in Vapi under 50 lakhs")
        gaps = m.detect_missing_slots()
        assert set(gaps["filled"]) >= {"location", "bhk", "budget"}
        assert "timeline" in gaps["missing"]
        assert "loan_required" in gaps["missing"]
        assert "location" not in gaps["missing"]

    def test_priority_order(self):
        m = make_mgr()
        gaps = m.detect_missing_slots()
        # name(10) < location(20) < bhk(30) < budget(40) < timeline(60) < loan(70)
        assert gaps["missing"].index("name") < gaps["missing"].index("location")
        assert gaps["missing"].index("budget") < gaps["missing"].index("timeline")

    def test_pending_field_not_asked(self):
        m = make_mgr()
        m.mark_pending("name")
        assert "name" not in m.detect_missing_slots()["missing"]
        m.clear_pending("name")
        assert "name" in m.detect_missing_slots()["missing"]


# ---------------------------------------------------------------------------
# 4. Dynamic question generation
# ---------------------------------------------------------------------------

class TestNextQuestion:
    def test_highest_priority_missing(self):
        m = make_mgr()
        m.set_externally("name", "Rahul")
        m.observe_user("3BHK in Vapi under 50 lakhs")
        nxt = m.next_missing_slot()
        assert nxt.name == "timeline"
        q = m.generate_next_question("en")
        assert "planning to purchase" in q.lower() or "when" in q.lower()

    def test_all_filled_returns_none(self):
        m = make_mgr()
        for s in m.slots.values():
            m.set_externally(s.name, "x")
        assert m.generate_next_question() is None


# ---------------------------------------------------------------------------
# 5. Prompt context + running summary
# ---------------------------------------------------------------------------

class TestPromptContext:
    def test_block_contents(self):
        m = make_mgr()
        m.set_externally("name", "Rahul")
        m.observe_user("I want a 3BHK apartment in Vapi under 50 lakhs")
        block = m.build_prompt_context("en")
        assert "CONVERSATION STATE" in block
        assert "✓ Location: Vapi" in block
        assert "✓ Configuration: 3BHK" in block
        assert "✓ Budget: 50 Lakhs" in block
        assert "✗ Timeline: Unknown" in block
        assert "NEVER ask about any ✓ field" in block
        assert "Ask ONLY ONE question next, about: Timeline" in block

    def test_running_summary(self):
        m = make_mgr()
        m.observe_user("I want a 3BHK apartment in Vapi under 50 lakhs")
        s = m.running_summary()
        assert "3BHK" in s and "Vapi" in s and "50 Lakhs" in s
        assert "timeline not yet discussed" in s.lower()
        assert "loan requirement not yet discussed" in s.lower()

    def test_all_filled_moves_to_closing(self):
        m = make_mgr()
        for s in m.slots.values():
            m.set_externally(s.name, "filled")
        block = m.build_prompt_context()
        assert "All required information is collected" in block


# ---------------------------------------------------------------------------
# 6. Audit trail + serialization
# ---------------------------------------------------------------------------

class TestAuditAndSerialization:
    def test_audit_shape(self):
        m = make_mgr()
        m.observe_user("I need a 3BHK")
        m.observe_user("Actually make it a 2BHK")
        e = m.audit_log[-1].to_dict()
        assert e["field"] == "bhk" and e["old"] == "3BHK" and e["new"] == "2BHK"
        assert e["source"] == "regex" and e["turn"] == 2
        assert e["timestamp"]

    def test_to_json_roundtrip(self):
        m = make_mgr()
        m.observe_user("3BHK in Vapi under 50 lakhs")
        d = json.loads(m.to_json())
        assert d["state"]["location"] == "Vapi"
        assert "timeline" in d["missing"]
        assert d["audit_log"]


# ---------------------------------------------------------------------------
# 7. Latency budget — the layer must be effectively free
# ---------------------------------------------------------------------------

class TestReviewRegressions:
    """Pins the confirmed findings from the adversarial review."""

    def test_location_last_mention_wins_not_dict_order(self):
        m = make_mgr()
        m.observe_user("Vapi nahi, Surat me chahiye")
        assert m.state["location"] == "Surat"
        m2 = make_mgr()
        m2.observe_user("main Surat me rehta hoon lekin Vapi me flat chahiye")
        assert m2.state["location"] == "Vapi"

    def test_abhi_nahi_is_not_immediately(self):
        m = make_mgr()
        m.observe_user("abhi nahi lena hai")
        assert m.state["timeline"] != "Immediately"

    def test_bare_abhi_without_purchase_context_no_timeline(self):
        m = make_mgr()
        m.observe_user("abhi main office me hoon")
        assert m.state["timeline"] is None

    def test_abhi_chahiye_is_immediately(self):
        m = make_mgr()
        m.observe_user("mujhe abhi chahiye flat")
        assert m.state["timeline"] == "Immediately"

    def test_no_hurry_outranks_immediacy(self):
        m = make_mgr()
        m.observe_user("koi jaldi nahi hai, bas dekh raha hoon")
        assert m.state["timeline"] == "Just exploring"

    def test_loan_question_does_not_fill_slot(self):
        m = make_mgr()
        m.observe_user("kya loan milega ispe?")
        assert m.state["loan_required"] is None
        m.observe_user("loan kitna milta hai")
        assert m.state["loan_required"] is None

    def test_loan_statement_still_fills(self):
        m = make_mgr()
        m.observe_user("haan mujhe home loan lena hai")
        assert m.state["loan_required"] == "Yes"

    def test_pending_field_blocked_from_all_merges(self):
        m = make_mgr()
        m.mark_pending("name")
        m.merge_state({"name": "Rakesh"}, source="llm", fill_only=True)
        assert m.state["name"] is None            # LLM race blocked
        m.merge_state({"name": "Rakesh"}, source="regex")
        assert m.state["name"] is None            # regex blocked too
        m.set_externally("name", "Rakesh")        # confirmation path works
        assert m.state["name"] == "Rakesh"

    def test_discard_pending_nulls_leaked_value(self):
        m = make_mgr()
        m.state["name"] = "Rakesh"                # simulate a pre-fix leak
        m.mark_pending("name")
        m.discard_pending("name")
        assert m.state["name"] is None
        assert m.audit_log[-1].new is None        # audited rejection

    def test_pending_counts_as_incomplete(self):
        m = make_mgr()
        for s in m.slots.values():
            m.set_externally(s.name, "x")
        m.state["name"] = None
        m.mark_pending("name")
        gaps = m.detect_missing_slots()
        assert gaps["missing"] == [] and gaps["pending"] == ["name"]
        block = m.build_prompt_context()
        assert "Awaiting confirmation" in block
        assert "All required information is collected" not in block

    def test_post_discovery_directive_softens(self):
        m = make_mgr()
        m.observe_user("3BHK in Vapi")
        block = m.build_prompt_context(stage_discovery=False)
        assert "Ask ONLY ONE question" not in block
        assert "CURRENT GOAL comes first" in block

    def test_meeting_slots_immune_to_purchase_timeline(self):
        from app.voice.state_manager import _MEETING_SLOTS
        m = ConversationStateManager(slots=list(_MEETING_SLOTS))
        m.observe_user("next month India aa raha hoon")
        assert m.state["meeting_time"] is None


class TestLatency:
    def test_observe_user_under_2ms(self):
        m = make_mgr()
        utterance = "haan ji main Vapi mein teen BHK flat dekh raha hoon pachas lakh tak, next month lena hai, loan bhi chahiye"
        # warm-up
        m.observe_user(utterance)
        t0 = time.perf_counter()
        n = 200
        for _ in range(n):
            m.extract_entities(utterance)
        per_call_ms = (time.perf_counter() - t0) * 1000 / n
        assert per_call_ms < 2.0, f"extraction too slow: {per_call_ms:.3f} ms"

    def test_prompt_context_under_1ms(self):
        m = make_mgr()
        m.observe_user("3BHK in Vapi under 50 lakhs")
        t0 = time.perf_counter()
        for _ in range(200):
            m.build_prompt_context()
        per_call_ms = (time.perf_counter() - t0) * 1000 / 200
        assert per_call_ms < 1.0, f"prompt build too slow: {per_call_ms:.3f} ms"
