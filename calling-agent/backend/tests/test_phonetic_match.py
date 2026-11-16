"""Phonetic option matching (RUNBOOK §85.12, call 1232)."""
from __future__ import annotations

import asyncio

import pytest

from app.voice import phonetic_match as pm
from tests import test_call_recovery as base

BHK = {"3 BHK": ["3 BHK"], "4 BHK": ["4 BHK"]}
PURPOSE = {"Self-use": ["Self-use", "self use"],
           "Investment": ["Investment", "इन्वेस्टमेंट"]}


@pytest.mark.parametrize("text,value", [
    ("थ्री बीएचके", "3 BHK"), ("तीन बीएचके", "3 BHK"), ("टी बीएचके", "3 BHK"),
    ("फोर बी एच के चाहिए", "4 BHK"), ("चार bhk", "4 BHK")])
def test_clear_matches_are_accepted(text, value):
    assert pm.best_match(text, BHK)[:2] == (value, "accept")


@pytest.mark.parametrize("text", [
    "पी वी एच के कंसीडर करता हूं मैं तो।",
    "पीडीएचके कंसीडर कर रहा हूं, मैंने आपको बोल दिया है।"])
def test_call_1232_lines_lead_to_3_bhk(text):
    value, tier, _ = pm.best_match(text, BHK)
    assert value == "3 BHK" and tier in ("accept", "confirm")


@pytest.mark.parametrize("text", [
    "हेलो", "मेरा एक करोड़ है बजट", "हाँ जी बोलिए", "फिर ही अच्छे बोल रहा था।",
    "मैं खुद केले देता हूँ।", "कौन बोल रहा है"])
def test_unrelated_speech_matches_nothing(text):
    assert pm.best_match(text, BHK)[1] == ""


@pytest.mark.parametrize("text,value", [
    ("इन वेस्ट मेंट के लिए", "Investment"), ("इंवेस्टमेन", "Investment"),
    ("शेल्फ यूज के लिए", "Self-use"), ("सेल्फ यूज़", "Self-use")])
def test_english_option_words_in_devanagari(text, value):
    got, tier, _ = pm.best_match(text, PURPOSE)
    assert got == value and tier in ("accept", "confirm")


def _router():
    r = base.make_router()
    r._def.languages.phonetic_match = True
    r.state_mgr.note_asked("configuration_required")
    r.last_agent_response = base.SIZE_Q
    r.memory.add_assistant(base.SIZE_Q, language="hi")
    return r


def test_a_likely_match_is_confirmed_and_a_yes_stores_it():
    r = _router()
    out = base.said(base.drive(r, "पी वी एच के कंसीडर करता हूं मैं तो।"))
    if r.state_mgr.state.get("configuration_required") != "3 BHK":
        assert "3 BHK" in out and "सही है" in out
        base.drive(r, "हाँ")
    assert r.state_mgr.state.get("configuration_required") == "3 BHK"


def test_a_no_to_a_two_option_confirmation_is_the_other_option():
    r = _router()
    r._phon_pending = ("configuration_required", "3 BHK", "4 BHK")
    base.drive(r, "नहीं")
    assert r.state_mgr.state.get("configuration_required") == "4 BHK"


def test_off_by_default():
    r = base.make_router()
    assert r._phonetic_option_fix("थ्री बीएचके") == "थ्री बीएचके"


CITIES = ["Vapi", "Surat", "Silvassa", "Umbergaon"]


@pytest.mark.parametrize("word,city", [
    ("भाभी", "Vapi"), ("बापी", "Vapi"), ("पापी", "Vapi"),
    ("सुरत", "Surat"), ("सिलवास", "Silvassa")])
def test_a_city_is_recognised_by_sound(word, city):
    assert pm.name_match(word, CITIES) == city


@pytest.mark.parametrize("word", [
    "वही", "वहीं", "भाई", "दिल्ली", "मुंबई", "हाँ", "बाकी", "आपकी", "शादी"])
def test_everyday_words_and_other_cities_are_not_renamed(word):
    assert pm.name_match(word, CITIES) == ""


def test_the_place_word_is_fixed_only_as_the_answer_to_a_place_question():
    r = base.make_router()
    r._def.languages.phonetic_match = True
    r._def.knowledge_base.locations = list(CITIES)
    r._last_asked_slot = "calling_from"
    assert r._contextual_city_fix("भाभी में ही हूं।").startswith("Vapi में ही")
    assert r._contextual_city_fix("दिल्ली में हूँ") == "दिल्ली में हूँ"
    r._last_asked_slot = "budget_range"
    assert r._contextual_city_fix("भाभी में ही हूं।") == "भाभी में ही हूं।"
