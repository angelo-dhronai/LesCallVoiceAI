"""
Unit tests for the Conversation State Management layer (state_manager.py).

Tests do NOT require API keys or network access.  They cover:
  - Multi-entity extraction from a single utterance (en / hi / gu)
  - Merge policies: null-never-overwrites, latest-wins, correction-gated
    project lock, external observe-only slots
  - Audit trail (accepted + rejected changes, corrections)
  - Missing-slot detection, ask-fatigue, suppression
  - Priority-ordered next-question generation
  - Running summary + prompt-context construction
  - Router integration: shortcut turns no longer lose volunteered slots
  - Snapshot serialization (phone masking, JSON-safe)

Run with:
    cd calling-agent
    source venv/bin/activate
    python -m pytest backend/tests/ -v
"""

from __future__ import annotations

import sys
import os

# Make sure the backend package is importable without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import asyncio

import pytest

from app.voice import state_manager as sm
from app.voice.state_manager import (
    ConversationStateManager,
    SLOT_SPECS,
    extract_entities,
    extract_budget,
    extract_bhk,
    extract_location,
    extract_timeline,
    extract_purpose,
    extract_loan_required,
    extract_transaction_type,
    extract_occupation,
    extract_property_category,
    extract_project,
    detect_correction,
)
from app.voice.memory import ConversationMemory, CallState
from app.voice.conversation import (
    ConversationRouter,
    ConversationState,
    STAGE_INVITE_VISIT,
)

os.environ.setdefault("OPENAI_API_KEY", "dummy")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_manager() -> ConversationStateManager:
    return ConversationStateManager(ConversationMemory(), ConversationState())


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Entity extraction — pure functions
# ---------------------------------------------------------------------------

class TestEntityExtraction:
    def test_multi_entity_single_utterance(self):
        """The headline scenario: several facts volunteered in one breath."""
        e = extract_entities("I want a 3bhk apartment in Vapi under 50 lakhs")
        assert e["location"] == "Vapi"
        assert e["configuration"] == "3 BHK"
        assert e["budget"] == "50 lakh"
        assert e["property_category"] == "apartment"

    def test_hindi_utterance(self):
        e = extract_entities("मुझे वापी में 2 बीएचके चाहिए")
        assert e["location"] == "Vapi"
        assert e["configuration"] == "2 BHK"

    def test_gujarati_utterance(self):
        e = extract_entities("સુરત માં 3 બીએચકે જોઈએ છે")
        assert e["location"] == "Surat"
        assert e["configuration"] == "3 BHK"

    def test_budget_range(self):
        assert extract_budget("50 to 70 lakh tak") == "50-70 lakh"

    def test_budget_crore(self):
        assert extract_budget("1.5 crore mein") == "1.5 crore"

    def test_budget_none_without_digits(self):
        assert extract_budget("mujhe flat chahiye") is None

    def test_timeline_variants(self):
        assert extract_timeline("abhi chahiye") == "immediate"
        assert extract_timeline("3 mahine mein") == "3 months"
        assert extract_timeline("2 saal mein plan hai") == "2 year(s)"

    def test_purpose(self):
        assert extract_purpose("investment ke liye") == "investment"
        assert extract_purpose("family ke saath rehna hai") == "self_use"

    def test_loan_yes(self):
        assert extract_loan_required("home loan lena hai") == "yes"
        assert extract_loan_required("EMI se lunga") == "yes"

    def test_loan_no_beats_yes(self):
        # Negative patterns must win even though "loan" also appears
        assert extract_loan_required("loan nahi chahiye, cash payment karunga") == "no"
        assert extract_loan_required("no loan, self funded") == "no"

    def test_transaction_type(self):
        assert extract_transaction_type("flat kharidna hai") == "buy"
        assert extract_transaction_type("rent pe lena hai") == "rent"
        # "rental income" means an investor BUYING to let — not a tenant
        assert extract_transaction_type("rental income ke liye") == "buy"

    def test_occupation(self):
        assert extract_occupation("main business karta hu") == "business"
        assert extract_occupation("i am a doctor") == "doctor"

    def test_property_category_commercial(self):
        assert extract_property_category("orbit me shop chahiye") == "commercial"

    def test_project_alias(self):
        assert extract_project("aranya 2 dekhna hai") == ("Aranya II", "Surat")

    def test_project_word_boundary(self):
        # "aura" must not match inside unrelated words
        assert extract_project("restaurant chahiye") is None

    def test_empty_input(self):
        assert extract_entities("") == {}
        assert extract_entities("   ") == {}

    def test_noisy_asr_fragment(self):
        e = extract_entities("umm haan 3 bhk vapi")
        assert e["configuration"] == "3 BHK"
        assert e["location"] == "Vapi"

    def test_code_mixed_latin_city_glued_to_gujarati_suffix(self):
        # Real call: caller wrote the city as a Latin word fused to the Gujarati
        # postposition "માં" (= "in"). "\bvapi\b" never matched (the Indic char
        # is a word char) and the fuzzy path saw one unmatchable token, so the
        # agent lost the location and re-asked for it.
        assert extract_location("અને એ શું vapiમાં આવેલો છે પ્રોજેક્ટ?") == "Vapi"
        e = extract_entities("suratમાં 3bhkનું ઘર 50 લાખ")
        assert e["location"] == "Surat"
        assert e["configuration"] == "3 BHK"
        assert e["budget"] == "50 lakh"

    def test_khubsurat_still_not_a_city_after_segmentation(self):
        # The script-splitting fix must not weaken the "beautiful" guard.
        assert extract_location("khubsurat ghar chahiye") is None
        assert extract_location("ખૂબસૂરત ઘર") is None

    def test_correction_cues(self):
        assert detect_correction("actually make it a 2bhk")
        assert detect_correction("nahi nahi surat me")
        assert detect_correction("ના ના બદલો")
        assert not detect_correction("3 bhk in vapi")


# ---------------------------------------------------------------------------
# Merge policies
# ---------------------------------------------------------------------------

class TestMergePolicies:
    def test_null_never_overwrites(self):
        mgr = make_manager()
        mgr.merge_slot("location", "Vapi")
        assert mgr.merge_slot("location", None) is None
        assert mgr.merge_slot("location", "  ") is None
        assert mgr.read_slot("location") == "Vapi"

    def test_restatement_is_noop(self):
        mgr = make_manager()
        mgr.merge_slot("location", "Vapi")
        n_audit = len(mgr.audit_log)
        assert mgr.merge_slot("location", "Vapi") is None
        assert len(mgr.audit_log) == n_audit

    def test_latest_wins_with_audit(self):
        mgr = make_manager()
        mgr.ingest_user_turn("i need a 3bhk")
        mgr.ingest_user_turn("actually make it a 2bhk")
        assert mgr.read_slot("configuration") == "2 BHK"
        entry = [c for c in mgr.audit_log if c.field == "configuration" and c.old][-1]
        assert (entry.old, entry.new, entry.accepted) == ("3 BHK", "2 BHK", True)
        assert entry.correction

    def test_latest_wins_without_explicit_cue(self):
        # LATEST_WINS slots update even without a correction phrase
        mgr = make_manager()
        mgr.ingest_user_turn("budget 50 lakh hai")
        mgr.ingest_user_turn("70 lakh tak chala jayega")
        assert mgr.read_slot("budget") == "70 lakh"

    def test_project_lock_rejects_passing_mention(self):
        mgr = make_manager()
        mgr.ingest_user_turn("aura project ke bare me batao")
        assert mgr.read_slot("project") == "Aura"
        changes = mgr.ingest_user_turn("sahaj kaisa hai")
        rejected = [c for c in changes if c.field == "project"]
        assert rejected and not rejected[0].accepted
        assert mgr.read_slot("project") == "Aura"

    def test_project_lock_accepts_explicit_correction(self):
        mgr = make_manager()
        mgr.ingest_user_turn("aura project ke bare me batao")
        mgr.ingest_user_turn("nahi nahi, sahaj project chahiye")
        assert mgr.read_slot("project") == "Sahaj"

    def test_project_implies_location(self):
        mgr = make_manager()
        mgr.ingest_user_turn("yogi woods dekhna hai")
        assert mgr.read_slot("project") == "Yogi Woods"
        assert mgr.read_slot("location") == "Silvassa"

    def test_project_does_not_override_stated_location(self):
        mgr = make_manager()
        mgr.ingest_user_turn("vapi me dekh raha hu")
        mgr.ingest_user_turn("yogi woods kaisa hai")
        assert mgr.read_slot("location") == "Vapi"

    def test_external_slots_are_observe_only(self):
        mgr = make_manager()
        change = mgr.merge_slot("name", "Rahul")
        assert change is not None and not change.accepted
        assert mgr.read_slot("name") is None

    def test_write_through_both_stores(self):
        # `b.X or cs.X` coalescing readers all over conversation.py must see
        # the same value from either store.
        mgr = make_manager()
        mgr.ingest_user_turn("2 bhk in surat, 40 lakh budget")
        b, cs = mgr.memory.booking, mgr.memory.call_state
        assert b.location == cs.selected_location == "Surat"
        assert b.property_type == cs.property_type == "2 BHK"
        assert b.budget == cs.budget == "40 lakh"

    def test_qa_tracker_marked_on_capture(self):
        mgr = make_manager()
        mgr.ingest_user_turn("3 bhk in vapi, loan lena hai")
        qa = mgr.memory.qa_tracker
        assert qa.is_answered("location")
        assert qa.is_answered("bhk")
        assert qa.is_answered("loan")

    def test_ingest_never_raises(self):
        mgr = make_manager()
        mgr.memory = None  # sabotage — ingest must swallow, not break the call
        assert mgr.ingest_user_turn("3 bhk vapi") == []


# ---------------------------------------------------------------------------
# External-slot observation (name / phone / appointment audit)
# ---------------------------------------------------------------------------

class TestObserveExternal:
    def test_name_confirmed_is_audited(self):
        mgr = make_manager()
        mgr.memory.booking.user_name = "Rahul"
        changes = mgr.observe_external(utterance="mera naam rahul hai")
        assert [(c.field, c.new) for c in changes] == [("name", "Rahul")]
        assert mgr.memory.qa_tracker.is_answered("name")

    def test_transition_to_none_ignored(self):
        # The pending-confirmation flow parks values as None on purpose;
        # that must not be recorded as data loss.
        mgr = make_manager()
        mgr.memory.booking.user_name = "Rahul"
        mgr.observe_external()
        mgr.memory.booking.user_name = None
        assert mgr.observe_external() == []


# ---------------------------------------------------------------------------
# Missing slots, fatigue, next question
# ---------------------------------------------------------------------------

class TestMissingSlots:
    def test_initial_state_all_required_missing(self):
        mgr = make_manager()
        assert mgr.filled_slots() == []
        assert mgr.missing_slots() == [
            "name", "location", "configuration", "budget",
            "purpose", "timeline", "loan_required", "phone",
        ]

    def test_filled_vs_missing_after_utterance(self):
        mgr = make_manager()
        mgr.ingest_user_turn("i need a 3bhk in vapi under 50 lakhs")
        assert mgr.filled_slots() == ["location", "configuration", "budget"]
        assert mgr.missing_slots() == ["name", "purpose", "timeline", "loan_required", "phone"]

    def test_suppress_removes_from_askable(self):
        mgr = make_manager()
        askable = mgr.askable_missing_slots(suppress={"name", "phone"})
        assert "name" not in askable and "phone" not in askable

    def test_ask_fatigue_drops_slot_from_askable(self):
        mgr = make_manager()
        qa = mgr.memory.qa_tracker
        qa.observe_agent_turn("Which city are you looking in?", 1)
        qa.observe_agent_turn("Which city would you prefer?", 2)
        assert "location" not in mgr.askable_missing_slots()
        assert "location" in mgr.missing_slots()  # still genuinely missing


class TestNextQuestion:
    def test_priority_order(self):
        mgr = make_manager()
        key, label, _ = mgr.next_question("en")
        assert key == "name"
        mgr.memory.booking.user_name = "Rahul"
        key, label, _ = mgr.next_question("en")
        assert key == "location"
        mgr.ingest_user_turn("vapi me")
        key, label, _ = mgr.next_question("en")
        assert key == "configuration"

    def test_language_templates(self):
        mgr = make_manager()
        mgr.memory.booking.user_name = "Rahul"
        _, _, q_hi = mgr.next_question("hi")
        _, _, q_gu = mgr.next_question("gu")
        assert "शहर" in q_hi
        assert "શહેર" in q_gu

    def test_all_filled_returns_none(self):
        mgr = make_manager()
        mgr.memory.booking.user_name = "Rahul"
        mgr.memory.call_state.customer_phone = "9876543210"
        mgr.ingest_user_turn(
            "3bhk in vapi, 50 lakh, investment, 3 months me, home loan lunga"
        )
        assert mgr.next_question("en") is None


# ---------------------------------------------------------------------------
# Running summary + prompt context
# ---------------------------------------------------------------------------

class TestRunningSummary:
    def test_mentions_undiscussed_fields(self):
        mgr = make_manager()
        mgr.ingest_user_turn("3bhk in vapi under 50 lakh")
        s = mgr.running_summary()
        assert "3 BHK" in s and "Vapi" in s and "50 lakh" in s
        assert "timeline not yet discussed" in s.lower()
        assert "loan requirement not yet discussed" in s.lower()

    def test_correction_noted(self):
        mgr = make_manager()
        mgr.ingest_user_turn("3bhk chahiye")
        mgr.ingest_user_turn("actually 2bhk kar do")
        assert "changed configuration from 3 BHK to 2 BHK" in mgr.running_summary()


class TestPromptContext:
    def test_sections_present(self):
        mgr = make_manager()
        mgr.ingest_user_turn("3bhk in vapi under 50 lakh")
        ctx = mgr.build_prompt_context("en")
        for section in (
            "PERSISTENT CALL STATE", "RUNNING SUMMARY",
            "ALREADY COLLECTED — DO NOT RE-ASK", "QUALIFICATION STATUS",
            "NEXT FIELD TO ASK",
        ):
            assert section in ctx, f"missing section: {section}"

    def test_filled_slot_not_asked_again(self):
        mgr = make_manager()
        mgr.ingest_user_turn("3bhk in vapi under 50 lakh")
        ctx = mgr.build_prompt_context("en")
        needed = ctx.split("QUALIFICATION STATUS")[1]
        assert "City (Surat / Vapi / Silvassa)" not in needed
        assert "Configuration" not in needed
        assert "city/location (Vapi)" in ctx  # listed as already collected

    def test_recent_correction_block(self):
        mgr = make_manager()
        mgr.ingest_user_turn("3bhk chahiye")
        mgr.ingest_user_turn("actually 2bhk")
        ctx = mgr.build_prompt_context("en")
        assert "RECENT CORRECTIONS" in ctx
        assert "'3 BHK' → '2 BHK'" in ctx

    def test_correction_block_expires(self):
        mgr = make_manager()
        mgr.ingest_user_turn("3bhk chahiye")
        mgr.ingest_user_turn("actually 2bhk")
        mgr.router_state.turn_count = 10  # correction is now 10 turns old
        assert "RECENT CORRECTIONS" not in mgr.build_prompt_context("en")

    def test_all_captured_variant(self):
        mgr = make_manager()
        mgr.memory.booking.user_name = "Rahul"
        mgr.memory.call_state.customer_phone = "9876543210"
        mgr.ingest_user_turn(
            "3bhk in vapi, 50 lakh, investment, 3 months me, home loan lunga"
        )
        assert "All core qualification fields captured" in mgr.build_prompt_context("en")


# ---------------------------------------------------------------------------
# Snapshot / serialization
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_snapshot_json_safe_and_phone_masked(self):
        mgr = make_manager()
        mgr.memory.call_state.customer_phone = "9876543210"
        mgr.ingest_user_turn("3bhk in vapi under 50 lakh")
        snap = mgr.snapshot()
        json.dumps(snap)  # must not raise
        assert snap["slots"]["phone"] == "***3210"
        assert snap["slots"]["location"] == "Vapi"
        assert "location" in snap["filled_slots"]
        assert "timeline" in snap["missing_slots"]
        assert snap["audit_log"]

    def test_call_state_to_dict_has_new_fields(self):
        d = CallState().to_dict()
        for f in ("loan_required", "transaction_type", "occupation", "property_category"):
            assert f in d


# ---------------------------------------------------------------------------
# Regressions from the adversarial review — every case below was a confirmed
# real-call failure before the fix.
# ---------------------------------------------------------------------------

class TestReviewRegressions:
    # ── Privacy: phone must never leave the process in full ──────────────
    def test_phone_masked_in_audit_and_snapshot(self):
        mgr = make_manager()
        mgr.memory.call_state.customer_phone = "9876543210"
        mgr.observe_external(utterance="my number is 9876543210 and budget 50 lakh")
        entry = [c for c in mgr.audit_log if c.field == "phone"][-1]
        assert entry.new == "***3210"
        assert "9876543210" not in entry.utterance
        assert "9876543210" not in json.dumps(mgr.snapshot())

    def test_phone_change_never_reaches_summary_or_prompt(self):
        mgr = make_manager()
        mgr.memory.call_state.customer_phone = "9876543210"
        mgr.observe_external()
        mgr.memory.call_state.customer_phone = "9123456789"
        mgr.observe_external()
        blob = mgr.running_summary() + mgr.build_prompt_context("en")
        assert "9876543210" not in blob and "9123456789" not in blob

    def test_digit_runs_redacted_from_all_audit_utterances(self):
        mgr = make_manager()
        mgr.ingest_user_turn("number 98765 43210 hai aur budget 50 lakh")
        for c in mgr.audit_log:
            assert "98765" not in c.utterance

    # ── Polarity: a rejected project must never be selected ──────────────
    def test_rejected_project_does_not_flip_lock(self):
        mgr = make_manager()
        mgr.ingest_user_turn("central park ke bare me batao")
        mgr.ingest_user_turn("no no, aranya is too far for me")
        assert mgr.read_slot("project") == "Central Park"

    def test_rejected_project_does_not_fill_empty_slot(self):
        mgr = make_manager()
        mgr.ingest_user_turn("aranya nahi chahiye")
        assert mgr.read_slot("project") is None

    def test_matlab_filler_does_not_unlock_project(self):
        mgr = make_manager()
        mgr.ingest_user_turn("vivanta ke baare me batao")
        mgr.ingest_user_turn("aranya ka matlab kya hai?")
        assert mgr.read_slot("project") == "Vivanta"

    # ── LATEST_WINS pollution guards ──────────────────────────────────────
    def test_khubsurat_is_not_surat(self):
        assert extract_location("bahut khubsurat project hai") is None
        assert extract_location("बहुत खूबसूरत है") is None
        assert extract_location("surat me chahiye") == "Surat"  # still works

    def test_price_question_does_not_overwrite_budget(self):
        mgr = make_manager()
        mgr.ingest_user_turn("mera budget 50 lakh hai")
        mgr.ingest_user_turn("3 bhk 2 crore ka hai kya?")
        assert mgr.read_slot("budget") == "50 lakh"

    def test_budget_updates_with_context_or_correction(self):
        mgr = make_manager()
        mgr.ingest_user_turn("mera budget 50 lakh hai")
        mgr.ingest_user_turn("actually 70 lakh kar sakta hu")
        assert mgr.read_slot("budget") == "70 lakh"

    def test_credit_card_is_not_crore(self):
        assert extract_budget("mere paas 2 credit card hai") is None

    def test_smalltalk_does_not_flip_purpose(self):
        mgr = make_manager()
        mgr.ingest_user_turn("investment ke liye dekh raha hu")
        mgr.ingest_user_turn("main apni family ke saath surat me rehta hu")
        assert mgr.read_slot("purpose") == "investment"

    def test_purpose_changes_on_explicit_correction(self):
        mgr = make_manager()
        mgr.ingest_user_turn("investment ke liye dekh raha hu")
        mgr.ingest_user_turn("actually apne liye chahiye, rehna hai wahan")
        assert mgr.read_slot("purpose") == "self_use"

    def test_jaldi_is_not_a_timeline(self):
        mgr = make_manager()
        mgr.ingest_user_turn("3 mahine me lena hai")
        mgr.ingest_user_turn("details jaldi whatsapp kar do")
        assert mgr.read_slot("timeline") == "3 months"

    def test_abhi_smalltalk_does_not_overwrite_timeline(self):
        mgr = make_manager()
        mgr.ingest_user_turn("3 mahine me lena hai")
        mgr.ingest_user_turn("abhi office se aa raha hu")
        assert mgr.read_slot("timeline") == "3 months"

    def test_abhi_chahiye_still_fills_and_overwrites_with_context(self):
        mgr = make_manager()
        assert extract_timeline("abhi chahiye") == "immediate"
        mgr.ingest_user_turn("3 mahine ka soch raha tha")
        mgr.ingest_user_turn("nahi nahi, abhi hi lena hai")
        assert mgr.read_slot("timeline") == "immediate"

    def test_office_commute_is_not_commercial(self):
        mgr = make_manager()
        mgr.ingest_user_turn("mujhe flat chahiye")
        mgr.ingest_user_turn("office ke baad 6 baje aa sakta hu")
        assert mgr.read_slot("property_category") == "apartment"
        assert extract_property_category("office space chahiye") == "commercial"

    # ── Loan negation coverage ────────────────────────────────────────────
    @pytest.mark.parametrize("text", [
        "i don't need a loan",
        "i do not want a loan",
        "loan ki zaroorat nahi hai",
        "no need for a loan",
        "લોન ની જરૂર નથી",
    ])
    def test_loan_refusals_read_as_no(self, text):
        assert extract_loan_required(text) == "no"

    def test_finance_job_is_not_a_loan(self):
        assert extract_loan_required("finance department me job hai") is None

    # ── Occupation first-person gating ────────────────────────────────────
    def test_occupation_third_party_mentions_ignored(self):
        assert extract_occupation("doctor ke paas jana hai") is None
        assert extract_occupation("mera beta doctor hai") is None
        assert extract_occupation("service road ke paas hai kya") is None
        assert extract_occupation("aapki service bahut achhi hai") is None


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------

class TestRouterIntegration:
    def _router(self) -> ConversationRouter:
        return ConversationRouter(api_key="dummy")

    def test_router_owns_manager(self):
        r = self._router()
        assert r.state_mgr.memory is r.memory
        assert r.state_mgr.router_state is r.state

    def test_shortcut_turn_no_longer_loses_slots(self):
        """A turn classified as a shortcut (gratitude) used to skip extraction
        entirely; volunteered slots in it were lost. Now ingest runs first."""
        r = self._router()

        async def _run():
            results = []
            async for res in r.process_stream("thank you, 3 bhk in vapi under 50 lakh"):
                results.append(res)
            return results

        results = run_async(_run())
        assert results and results[0].is_shortcut          # gratitude canned reply
        assert r.memory.booking.property_type == "3 BHK"   # …but slots captured
        assert r.memory.booking.location == "Vapi"
        assert r.memory.booking.budget == "50 lakh"

    def test_prompt_contains_state_context(self):
        r = self._router()
        r.state_mgr.ingest_user_turn("2bhk in surat, loan chahiye")
        prompt = r._build_system_prompt()
        assert "PERSISTENT CALL STATE" in prompt
        assert "RUNNING SUMMARY" in prompt
        assert "loan_required" in prompt
        assert "NEXT FIELD TO ASK" in prompt

    def test_prompt_suppresses_pending_phone(self):
        r = self._router()
        r._phone_pending = "9876543210"
        prompt = r._build_system_prompt()
        qual = prompt.split("QUALIFICATION STATUS")[1].split("━━ CURRENT GOAL")[0]
        assert "Phone / WhatsApp" not in qual

    def test_reschedule_still_works(self):
        r = self._router()
        r.state.stage = STAGE_INVITE_VISIT
        r.memory.booking.appointment_time = "Tomorrow at 10:00 AM"
        r.memory.call_state.appointment_time = "Tomorrow at 10:00 AM"
        r._extract_data_from_input("actually saturday 3 pm karo", "time_preference")
        assert "3" in (r.memory.booking.appointment_time or "")
        assert r.memory.call_state.appointment_time == r.memory.booking.appointment_time

    def test_legacy_extractor_delegates(self):
        r = self._router()
        assert r._extract_budget("70 lakh budget hai") == "70 lakh"
        assert r._extract_bhk("3 bhk chahiye") == "3 bhk"      # historical lowercase
        assert r._extract_location("vapi me") == "Vapi"
        assert r._extract_timeline("3 mahine mein") == "3 months"
        assert r._extract_project_from_text("aura dekhna hai") == ("Aura", "Vapi")

    def test_fragmented_info_across_turns(self):
        """Callers rarely answer in full sentences; fragments must accumulate."""
        r = self._router()
        r.state_mgr.ingest_user_turn("3 bhk")
        r.state_mgr.ingest_user_turn("vapi")
        r.state_mgr.ingest_user_turn("50 lakh ke andar")
        r.state_mgr.ingest_user_turn("loan nahi, cash")
        cs = r.memory.call_state
        assert cs.property_type == "3 BHK"
        assert cs.selected_location == "Vapi"
        assert cs.budget == "50 lakh"
        assert cs.loan_required == "no"
