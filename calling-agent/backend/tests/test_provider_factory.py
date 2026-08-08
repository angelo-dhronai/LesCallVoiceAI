"""Provider factory + HybridTTS routing semantics (2026-08-07 EL switch).

Pins the invariants the rollback doctrine depends on:
  * VOICE_PROVIDER=sarvam + no override → exactly the legacy objects
    (TrackingSarvamTTS / TrackingSarvamSTT), no wrapper, no EL client.
  * elevenlabs pref on an hi/en agent → HybridTTS with the EL delegate
    active (bulbul speaker shim applied) and a Sarvam reserve delegate for
    the dead-air failover (owner 2026-08-07: Sarvam is the last-case
    fallback).
  * elevenlabs pref on an hi/en/gu agent → HybridTTS; gu routes to the
    Sarvam delegate, hi/en to the EL delegate; barge-in stops both.
  * An EL utterance that delivers ZERO audio fails over to the Sarvam
    reserve and pins it for the rest of the call; cancelled (barge-in)
    utterances and Sarvam-incapable languages never fail over.
  * Sarvam-only language sets keep Sarvam STT under sarvam pref; any
    EL-only language forces Scribe STT regardless of pref. Scribe primary
    on a Sarvam-capable language set gets saarika as the router fallback.

No network: everything here is construction + routing, never speak().
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent_definition.schema import (  # noqa: E402
    AgentDefinition, LanguageConfig, TTSVoiceConfig,
)
from app.voice.provider_factory import (  # noqa: E402
    HybridTTS, build_stt_primary, build_tts, provider_pref,
)
from app.voice.tracking_stt import (  # noqa: E402
    TrackingSarvamSTT, TrackingScribeSTT,
)
from app.voice.tracking_tts import (  # noqa: E402
    TrackingSarvamTTS, TrackingElevenLabsTTS,
)

import app.voice.provider_factory as _pf  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_env_file(monkeypatch):
    """provider_factory re-reads calling-agent/.env per call (hot flips) —
    point it at nowhere so these tests are driven purely by monkeypatched
    process env, not by whatever the live deployment currently has set."""
    monkeypatch.setattr(_pf, "_ENV_FILE", Path("/nonexistent/.env"))
    monkeypatch.delenv("VOICE_PROVIDER_STRICT", raising=False)


class _Sink:
    sample_rate = 8000

    async def start(self):
        pass

    async def write_frame(self, b):
        pass

    async def flush(self):
        pass

    async def stop(self):
        pass


def _agent(langs, default, voices=None, voice_provider=""):
    d = AgentDefinition.__new__(AgentDefinition)
    d.voice_provider = voice_provider
    d.languages = LanguageConfig(
        supported=list(langs),
        default=default,
        tts_voices=voices or {
            lang: TTSVoiceConfig(model="bulbul:v3", speaker="shubh", pace=1.15)
            for lang in langs
        },
        register_notes={},
    )
    return d


_CIP = staticmethod(lambda: "test-call")


def test_sarvam_default_builds_legacy_objects(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "sarvam")
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    a = _agent(["hi", "en", "gu"], "hi")
    tts = build_tts(a, lambda: "c", audio_sink=_Sink(), sample_rate=8000)
    assert type(tts) is TrackingSarvamTTS
    assert tts._model == "bulbul:v3" and tts._speaker == "shubh"
    stt = build_stt_primary(a, lambda: "c")
    assert type(stt) is TrackingSarvamSTT


def test_elevenlabs_pref_hi_en_hybrid_with_sarvam_reserve(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")
    a = _agent(["hi", "en"], "hi")
    tts = build_tts(a, lambda: "c", audio_sink=_Sink(), sample_rate=8000)
    # Even a fully-EL language set carries the Sarvam reserve delegate now
    # (dead-air failover target) — so the factory returns the hybrid.
    assert isinstance(tts, HybridTTS)
    assert set(tts._delegates) == {"elevenlabs", "sarvam"}
    assert tts._active_name == "elevenlabs"
    el = tts._delegates["elevenlabs"]
    assert type(el) is TrackingElevenLabsTTS
    # shubh must have been shim-mapped to an EL voice_id, never sent verbatim
    assert el._speaker != "shubh" and len(el._speaker) == 20
    assert el._model == "eleven_flash_v2_5"
    assert type(tts._delegates["sarvam"]) is TrackingSarvamTTS


def test_gujarati_forces_hybrid_and_routes_correctly(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")
    a = _agent(["hi", "en", "gu"], "hi")
    tts = build_tts(a, lambda: "c", audio_sink=_Sink(), sample_rate=8000)
    assert isinstance(tts, HybridTTS)
    assert set(tts._delegates) == {"sarvam", "elevenlabs"}
    # default hi → EL active
    assert tts._active_name == "elevenlabs"
    tts.set_language("gu")
    assert tts._active_name == "sarvam"
    # gu voice re-applied through the shim: bulbul speaker stays a bulbul name
    assert tts._active._speaker == "shubh"
    tts.set_language("en")
    assert tts._active_name == "elevenlabs"
    assert len(tts._active._speaker) == 20  # EL voice_id, not "shubh"


def test_agent_override_beats_platform_env(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "sarvam")
    a = _agent(["hi", "en"], "hi", voice_provider="elevenlabs")
    assert provider_pref(a) == "elevenlabs"
    tts = build_tts(a, lambda: "c", audio_sink=_Sink(), sample_rate=8000)
    assert isinstance(tts, HybridTTS)
    assert tts._active_name == "elevenlabs"
    assert type(tts._active) is TrackingElevenLabsTTS


def test_el_only_language_forces_scribe_stt(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "sarvam")
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    a = _agent(["en", "es"], "en")
    stt = build_stt_primary(a, lambda: "c")
    assert type(stt) is TrackingScribeSTT
    # …and a Spanish turn under sarvam pref speaks through EL
    tts = build_tts(a, lambda: "c", audio_sink=_Sink(), sample_rate=8000)
    assert isinstance(tts, HybridTTS)
    tts.set_language("es")
    assert tts._active_name == "elevenlabs"


def test_stt_provider_env_escape_hatch(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")
    monkeypatch.setenv("STT_PROVIDER", "sarvam")
    a = _agent(["hi", "en"], "hi")
    stt = build_stt_primary(a, lambda: "c")
    # forced back to saarika even though the platform pref is EL
    assert type(stt) is TrackingSarvamSTT


def test_hybrid_stop_reaches_every_delegate(monkeypatch):
    import asyncio

    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")
    a = _agent(["hi", "gu"], "hi")
    tts = build_tts(a, lambda: "c", audio_sink=_Sink(), sample_rate=8000)
    assert isinstance(tts, HybridTTS)
    for d in tts._delegates.values():
        d._playing = True
    asyncio.run(tts.stop())
    for d in tts._delegates.values():
        assert d._cancelled or not d._playing


def test_strict_mode_never_builds_sarvam(monkeypatch):
    """VOICE_PROVIDER_STRICT (owner 2026-08-07): Sarvam fully out of the
    call path — a gu-supporting agent gets ONE EL instance (no hybrid), gu
    turns swap the model to eleven_v3, and agent-level sarvam overrides are
    ignored."""
    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")
    monkeypatch.setenv("VOICE_PROVIDER_STRICT", "true")
    monkeypatch.setenv("STT_PROVIDER", "sarvam")  # must be ignored under strict
    a = _agent(["hi", "en", "gu"], "hi", voice_provider="sarvam")
    tts = build_tts(a, lambda: "c", audio_sink=_Sink(), sample_rate=8000)
    assert type(tts) is TrackingElevenLabsTTS          # no hybrid, no sarvam
    stt = build_stt_primary(a, lambda: "c")
    assert type(stt) is TrackingScribeSTT              # escape hatch ignored
    # gu → eleven_v3, back to flash for hi (single-instance model routing)
    tts.set_language("gu")
    assert tts._model == "eleven_v3"
    tts.set_language("hi")
    assert tts._model == "eleven_flash_v2_5"
    # a foreign (bulbul) set_voice during a gu turn must not undo the swap
    tts.set_language("gu")
    tts.set_voice(model="bulbul:v3", speaker="shubh", pace=1.15)
    assert tts._model == "eleven_v3"
    assert len(tts._speaker) == 20                     # shim kept the EL voice


def test_strict_sarvam_side_is_symmetric(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "sarvam")
    monkeypatch.setenv("VOICE_PROVIDER_STRICT", "true")
    a = _agent(["en", "es"], "en")   # es is EL-only in hybrid mode
    tts = build_tts(a, lambda: "c", audio_sink=_Sink(), sample_rate=8000)
    assert type(tts) is TrackingSarvamTTS


def test_sink_rebind_reaches_every_delegate(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")
    a = _agent(["hi", "gu"], "hi")
    tts = build_tts(a, lambda: "c", audio_sink=_Sink(), sample_rate=8000)
    new_sink = _Sink()
    tts.set_sink(new_sink)
    for d in tts._delegates.values():
        assert d._sink is new_sink
    assert tts._sink is new_sink


# ── Dead-air failover (owner 2026-08-07: Sarvam as last-case fallback) ──

class _FakeDelegate:
    """speak()-level fake: records utterances, simulates audio delivery."""

    def __init__(self, produces_audio: bool):
        self._produces = produces_audio
        self.spoken = []
        self._cancelled = False
        self._produced_audio = False

    def set_language(self, lang):
        pass

    def set_voice(self, model=None, speaker=None, pace=None):
        pass

    async def speak(self, text):
        self._produced_audio = self._produces
        self.spoken.append(text)

    async def speak_cached(self, text):
        await self.speak(text)

    async def stop(self):
        self._cancelled = True


def _fake_hybrid(el_produces: bool):
    el = _FakeDelegate(el_produces)
    sv = _FakeDelegate(True)
    h = HybridTTS({"elevenlabs": el, "sarvam": sv},
                  "elevenlabs", "elevenlabs")
    h._language = "en-IN"
    return h, el, sv


def test_dead_air_failover_pins_sarvam():
    import asyncio
    h, el, sv = _fake_hybrid(el_produces=False)
    asyncio.run(h.speak("Hello caller"))
    assert el.spoken == ["Hello caller"]
    assert sv.spoken == ["Hello caller"]        # rescued utterance
    assert h._el_failed and h._active_name == "sarvam"
    asyncio.run(h.speak("Second line"))         # rest of call rides Sarvam
    assert sv.spoken[-1] == "Second line" and len(el.spoken) == 1


def test_healthy_el_never_fails_over():
    import asyncio
    h, el, sv = _fake_hybrid(el_produces=True)
    asyncio.run(h.speak("Hello caller"))
    assert sv.spoken == [] and not h._el_failed


def test_cancelled_utterance_skips_failover():
    import asyncio
    h, el, sv = _fake_hybrid(el_produces=False)
    el._cancelled = True                        # barge-in mid-utterance
    asyncio.run(h.speak("Interrupted"))
    assert sv.spoken == [] and not h._el_failed


def test_sarvam_incapable_language_skips_failover():
    import asyncio
    h, el, sv = _fake_hybrid(el_produces=False)
    h._language = "es"                          # bulbul has no Spanish
    asyncio.run(h.speak("Hola"))
    assert sv.spoken == [] and not h._el_failed


def test_stt_fallback_saarika_behind_scribe(monkeypatch):
    from app.voice.provider_factory import build_stt_fallback
    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    # Scribe primary + Indic-only set → saarika fallback
    fb = build_stt_fallback(_agent(["hi", "en"], "hi"), lambda: "c")
    assert type(fb) is TrackingSarvamSTT
    # A language saarika cannot hear → keep Whisper (None)
    assert build_stt_fallback(_agent(["en", "es"], "en"), lambda: "c") is None
    # Sarvam primary → same-provider fallback is pointless (None)
    monkeypatch.setenv("VOICE_PROVIDER", "sarvam")
    assert build_stt_fallback(_agent(["hi", "en"], "hi"), lambda: "c") is None


def test_failover_flag_survives_real_speak_lifecycle(monkeypatch):
    """Drive the REAL speak() machinery (only the network generator mocked).

    speak() ends its finally block with _reset(); the dead-air flag must
    survive that or every healthy EL utterance looks like dead air and gets
    re-spoken by Sarvam (live false-failover + double greeting, 2026-08-07 —
    the fake-delegate tests above model the post-fix contract and could not
    see it)."""
    import asyncio

    from app.voice.tts_elevenlabs import ElevenLabsTTS
    from app.voice.tts_sarvam import SarvamTTS
    from app.voice.tts_stream import TTSChunk

    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")

    async def el_healthy(self, text):
        yield TTSChunk(audio=b"\x00" * 3200, text_segment=text, is_last=False)

    async def el_dead(self, text):
        return
        yield  # pragma: no cover — makes this an (empty) async generator

    async def sv_stream(self, text):
        yield TTSChunk(audio=b"\x00" * 3200, text_segment=text, is_last=False)

    def make():
        tts = build_tts(_agent(["hi", "en"], "en"), lambda: "c",
                        audio_sink=_Sink(), sample_rate=8000)
        assert isinstance(tts, HybridTTS)
        for d in tts._delegates.values():
            d._api_key = "test-key"        # bypass the no-key early return
        tts.set_language("en")
        monkeypatch.setattr(SarvamTTS, "_stream_audio", sv_stream)
        return tts

    # Healthy EL stream → flag survives the trailing _reset(), no failover.
    tts = make()
    monkeypatch.setattr(ElevenLabsTTS, "_stream_audio", el_healthy)
    asyncio.run(tts.speak("Hello caller"))
    assert not tts._el_failed
    assert tts._active_name == "elevenlabs"

    # Zero-chunk EL stream → genuine dead air → Sarvam rescues and pins.
    tts = make()
    monkeypatch.setattr(ElevenLabsTTS, "_stream_audio", el_dead)
    asyncio.run(tts.speak("Hello caller"))
    assert tts._el_failed
    assert tts._active_name == "sarvam"
    assert tts._active._produced_audio     # Sarvam actually spoke the rescue
