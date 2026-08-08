"""
Voice-provider selection — the ONE place that decides which STT/TTS
provider serves a call, and the HybridTTS wrapper that routes per-language.

Selection order:
  1. agent.voice_provider ("sarvam" | "elevenlabs") when set — canary knob.
  2. VOICE_PROVIDER env (default "sarvam" — flipping it is the rollback).
  3. Per-language capability routing on top (voice_catalog.tts_route /
     stt_primary_provider): a preference that cannot serve a language is
     overruled, so a Gujarati agent under ElevenLabs still speaks bulbul for
     gu, and a Spanish agent under Sarvam still speaks flash for es.

Rollback doctrine: with VOICE_PROVIDER=sarvam and no agent overrides, this
module builds byte-identical objects to the pre-switch code (a single
TrackingSarvamTTS + TrackingSarvamSTT with the per-call Sarvam key lease) —
no HybridTTS wrapper, no ElevenLabs client, nothing new on the wire.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..agent_definition.voice_catalog import (
    DEFAULT_TTS_MODEL,
    EL_DEFAULT_TTS_MODEL,
    SARVAM_LANGUAGES,
    el_voice_for_speaker,
    el_voice_gender,
    model_provider,
    stt_primary_provider,
    tts_route,
)
from .tracking_stt import TrackingSarvamSTT, TrackingScribeSTT
from .tracking_tts import TrackingSarvamTTS, TrackingElevenLabsTTS

logger = logging.getLogger(__name__)


# Same live-read doctrine as key_rotator.current_key(): the runtime process
# loads .env ONCE at boot, so an os.environ-only read serves a flipped flag
# only after the next container restart. That exact lag put the 11:10 call
# on Sarvam eleven minutes after the owner's strict-EL flip (2026-08-07) —
# the flag now re-reads .env per call, so a provider flip is hot.
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def _live_env(name: str, default: str = "") -> str:
    """Value from .env on disk (updating os.environ), else os.environ."""
    try:
        if _ENV_FILE.exists():
            m = re.search(rf"^{name}=(.*)$",
                          _ENV_FILE.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                val = m.group(1).strip()
                if val:
                    os.environ[name] = val
                    return val
    except Exception:  # pragma: no cover — never break a call over an env read
        pass
    return os.environ.get(name, default)


def platform_voice_provider() -> str:
    """Platform-wide default provider (live .env re-read — flips are hot)."""
    vp = _live_env("VOICE_PROVIDER", "sarvam").strip().lower()
    return vp if vp in ("sarvam", "elevenlabs") else "sarvam"


def strict_mode() -> bool:
    """VOICE_PROVIDER_STRICT: single-provider test mode (owner directive
    2026-08-07 — "remove the sarvam fallback"). No cross-provider routing:
    languages the preferred provider's realtime model can't speak ride its
    fallback model (eleven_v3) instead of the other vendor. Agent-level
    voice_provider overrides are ALSO ignored — strict means the whole
    platform exercises one API."""
    return _live_env(
        "VOICE_PROVIDER_STRICT", "false").strip().lower() in ("1", "true", "yes")


def provider_pref(agent_def=None) -> str:
    """Effective provider preference for an agent (override > platform;
    strict mode pins everything to the platform provider)."""
    if not strict_mode() and agent_def is not None:
        vp = (getattr(agent_def, "voice_provider", "") or "").strip().lower()
        if vp in ("sarvam", "elevenlabs"):
            return vp
    return platform_voice_provider()


def _translate_voice(provider: str, model: str, speaker: str) -> tuple[str, str]:
    """(model, speaker) as `provider` needs them.

    Agent YAMLs were authored for bulbul and are NEVER rewritten for the
    provider switch — when a bulbul-configured language routes through
    ElevenLabs the speaker name is shim-mapped to an EL voice_id (and vice
    versa if an EL-native config ever routes through Sarvam).
    """
    cfg_provider = model_provider(model)
    if provider == cfg_provider:
        return model, speaker
    if provider == "elevenlabs":
        return EL_DEFAULT_TTS_MODEL, el_voice_for_speaker(speaker)
    # EL-native config routed through Sarvam (e.g. gu on an agent authored
    # for ElevenLabs): same-gender bulbul default.
    fb = "simran" if el_voice_gender(speaker) == "female" else "shubh"
    return DEFAULT_TTS_MODEL, fb


class HybridTTS:
    """Per-language router over one-or-more provider TTS instances.

    Built only when an agent's language set actually spans providers; the
    single-provider case returns the bare provider class from the factory
    (zero wrapper overhead, exact legacy behaviour).

    Exposes the full surface the pipeline touches: warmup / set_language /
    set_voice / speak / speak_cached / stop / close, plus `_sink` (echo-mute
    sizing) and `_speaker` (logging). All delegates share ONE AudioSink, so
    flush semantics and playback_busy_until behave exactly as before.
    """

    _warmup_done_flag = False   # pipeline checks this on the CLASS

    def __init__(self, delegates: dict, initial: str, pref: str,
                 tts_voices: dict | None = None) -> None:
        self._delegates = delegates          # provider name -> TTS instance
        self._pref = pref
        self._tts_voices = tts_voices or {}  # lang -> TTSVoiceConfig
        self._active = delegates[initial]
        self._active_name = initial
        self._language = "en"
        # Set on the first dead-air ElevenLabs failure; routing then prefers
        # Sarvam for every language it can speak, for the REST OF THIS CALL
        # (owner 2026-08-07: "prefer elevenlabs, sarvam as last-case
        # fallback"). Per-call object, so the degrade never outlives the call.
        self._el_failed = False

    # -- routing ---------------------------------------------------------

    def _delegate_for(self, language: str):
        name, _model = tts_route(language, self._pref)
        if (self._el_failed and name == "elevenlabs"
                and "sarvam" in self._delegates
                and (language or "en").split("-")[0].lower()
                in SARVAM_LANGUAGES):
            name = "sarvam"
        if name not in self._delegates:
            # Routing wants a provider this agent wasn't built with (should
            # not happen — the factory builds every reachable provider).
            name = self._active_name
        return name, self._delegates[name]

    def set_language(self, lang: str) -> None:
        self._language = lang
        name, delegate = self._delegate_for(lang)
        if delegate is not self._active:
            logger.info("[TTS:Hybrid] language %r → provider %s", lang, name)
            self._active = delegate
            self._active_name = name
        self._active.set_language(lang)
        # Re-apply this language's configured voice through the translation
        # shim so a provider switch always lands on the right voice even if
        # the pipeline skips its own set_voice call.
        vcfg = self._tts_voices.get((lang or "").split("-")[0])
        if vcfg is not None:
            self.set_voice(model=getattr(vcfg, "model", None),
                           speaker=getattr(vcfg, "speaker", None),
                           pace=getattr(vcfg, "pace", None))

    def set_voice(self, model: str | None = None, speaker: str | None = None,
                  pace: float | None = None) -> None:
        if model or speaker:
            model2, speaker2 = _translate_voice(
                self._active_name, model or "", speaker or "")
            self._active.set_voice(model=model2 or None,
                                   speaker=speaker2 or None, pace=pace)
        else:
            self._active.set_voice(pace=pace)

    # -- passthrough surface --------------------------------------------

    async def warmup(self) -> None:
        for d in self._delegates.values():
            if hasattr(d, "warmup"):
                try:
                    await d.warmup()
                except Exception:  # noqa: BLE001 — warmup is best-effort
                    pass
        HybridTTS._warmup_done_flag = True

    async def speak(self, text: str) -> None:
        await self._active.speak(text)
        await self._maybe_failover(text, cached=False)

    async def speak_cached(self, text: str) -> None:
        if hasattr(self._active, "speak_cached"):
            await self._active.speak_cached(text)
        else:
            await self._active.speak(text)
        await self._maybe_failover(text, cached=True)

    async def _maybe_failover(self, text: str, cached: bool) -> None:
        """Dead-air rescue: the utterance went to ElevenLabs and ZERO audio
        reached the sink (quota 401, 429 storm, timeout, missing key — the
        provider boundary swallows all of these and returns silently). Speak
        it through the Sarvam reserve so the caller never sits in silence,
        and pin Sarvam for the rest of the call — every later utterance
        would otherwise pay the same EL timeout before its rescue.

        Zero-chunk only: a stream that died mid-utterance already played
        its head, and re-speaking the whole line is the double-speak defect.
        """
        active = self._active
        # _cancelled is unreliable here — speak()'s trailing _reset() already
        # cleared it — so barge-in is read from _stop_seen, which survives.
        if (self._active_name != "elevenlabs"
                or "sarvam" not in self._delegates
                or getattr(active, "_produced_audio", True)
                or getattr(active, "_cancelled", False)
                or getattr(active, "_stop_seen", False)
                or not text or len(text.strip()) < 2):
            return
        lang = (self._language or "en").split("-")[0].lower()
        if lang not in SARVAM_LANGUAGES:
            return
        logger.warning(
            "[TTS:Hybrid] ElevenLabs produced no audio for %r — failing "
            "over to Sarvam for the rest of the call", text[:60])
        self._el_failed = True
        # Re-route through the normal path so the Sarvam delegate gets this
        # language's voice via the translation shim, same as any switch.
        self.set_language(self._language)
        if self._active_name != "sarvam":
            return
        if cached and hasattr(self._active, "speak_cached"):
            await self._active.speak_cached(text)
        else:
            await self._active.speak(text)

    async def stop(self) -> None:
        # Stop every delegate — stop() on an idle one is a harmless flag set,
        # and barge-in must never race a delegate swap.
        for d in self._delegates.values():
            await d.stop()

    async def close(self) -> None:
        for d in self._delegates.values():
            try:
                await d.close()
            except Exception:  # noqa: BLE001 — teardown best-effort
                pass

    # -- attributes the pipeline reads ----------------------------------

    def set_sink(self, sink) -> None:
        """Rebind every delegate to one transport sink (the pipeline calls
        this with the TelephonySink right after construction)."""
        for d in self._delegates.values():
            if hasattr(d, "set_sink"):
                d.set_sink(sink)

    @property
    def _sink(self):
        return self._active._sink

    @property
    def _speaker(self):
        return getattr(self._active, "_speaker", "?")

    @property
    def _playing(self):
        return any(getattr(d, "_playing", False)
                   for d in self._delegates.values())

    @property
    def is_playing(self):
        return self._playing


def build_tts(agent_def, call_id_provider, audio_sink, sample_rate: int,
              sarvam_key_provider=None):
    """Build the TTS stack for one call.

    Returns a single provider instance when every supported language routes
    to one provider (the common case — and ALWAYS the case under
    VOICE_PROVIDER=sarvam with no overrides), or a HybridTTS over shared-sink
    delegates when the language set spans providers.
    """
    pref = provider_pref(agent_def)
    strict = strict_mode()

    # Default-language voice config (what the bridge used to inline).
    model, speaker, pace = DEFAULT_TTS_MODEL, "shubh", 1.0
    supported = ["hi", "en"]
    tts_voices = {}
    if agent_def:
        supported = list(agent_def.languages.supported or ["hi", "en"])
        dlang = agent_def.languages.default
        tts_voices = agent_def.languages.tts_voices or {}
        vcfg = tts_voices.get(dlang)
        if vcfg:
            model, speaker = vcfg.model, vcfg.speaker
            pace = getattr(vcfg, "pace", 1.0) or 1.0
        dlang = dlang or (supported[0] if supported else "hi")
    else:
        dlang = "hi"

    needed = {tts_route(l, pref, strict)[0] for l in supported}
    needed.add(tts_route(dlang, pref, strict)[0])
    # Sarvam reserve for the dead-air failover (owner 2026-08-07: "prefer
    # elevenlabs, sarvam as last-case fallback"): under an EL preference
    # every call carries a Sarvam delegate even when language routing never
    # picks it, so HybridTTS._maybe_failover has somewhere to go. Strict
    # mode stays pure-EL — it exists to exercise exactly one API.
    if not strict and pref == "elevenlabs" and "elevenlabs" in needed:
        if any((l or "").split("-")[0].lower() in SARVAM_LANGUAGES
               for l in [*supported, dlang]):
            needed.add("sarvam")

    def _make(provider_name: str):
        m, s = _translate_voice(provider_name, model, speaker)
        if provider_name == "elevenlabs":
            return TrackingElevenLabsTTS(
                call_id_provider=call_id_provider,
                model=m, speaker=s, pace=pace,
                sample_rate=sample_rate, audio_sink=audio_sink,
            )
        tts = TrackingSarvamTTS(
            call_id_provider=call_id_provider,
            model=m, speaker=s, pace=pace,
            sample_rate=sample_rate, audio_sink=audio_sink,
        )
        if sarvam_key_provider is not None:
            tts.key_provider = sarvam_key_provider
        return tts

    if len(needed) == 1:
        only = next(iter(needed))
        logger.info("[voice-provider] TTS=%s (pref=%s, langs=%s)",
                    only, pref, supported)
        return _make(only)

    delegates = {name: _make(name) for name in sorted(needed)}
    initial = tts_route(dlang, pref)[0]
    logger.info("[voice-provider] TTS=hybrid %s (pref=%s, langs=%s, default=%s→%s)",
                sorted(needed), pref, supported, dlang, initial)
    hybrid = HybridTTS(delegates, initial, pref, tts_voices=tts_voices)
    hybrid._language = dlang
    return hybrid


def _agent_supported_langs(agent_def) -> list[str]:
    return (list(agent_def.languages.supported)
            if agent_def else ["hi", "en"])


def _stt_primary_choice(agent_def) -> str:
    """Provider the STT primary resolves to for this agent (env overrides
    applied) — shared by build_stt_primary and build_stt_fallback so the
    fallback decision can never disagree with what the primary actually is."""
    supported = _agent_supported_langs(agent_def)
    choice = stt_primary_provider(supported, provider_pref(agent_def),
                                  strict_mode())
    # Env escape hatch: STT_PROVIDER overrides the computed choice (e.g.
    # keep saarika primary fleet-wide while TTS rides ElevenLabs).
    # Ignored under strict mode — strict means ONE provider on the wire.
    forced = ("" if strict_mode()
              else _live_env("STT_PROVIDER", "").strip().lower())
    if forced in ("sarvam", "elevenlabs"):
        # Never force Sarvam onto a language set it cannot hear.
        if not (forced == "sarvam" and choice == "elevenlabs"
                and stt_primary_provider(supported, "sarvam") == "elevenlabs"):
            choice = forced
    return choice


def build_stt_fallback(agent_def, call_id_provider, sarvam_key_provider=None):
    """Sarvam saarika as the STT router's fallback/hedge provider (owner
    2026-08-07: "prefer elevenlabs, sarvam as last-case fallback") — only
    when the primary is Scribe AND saarika hears every agent language.
    Returns None when Whisper should stay the fallback: Sarvam primary
    (same-provider fallback is pointless — the retry hedge already
    re-requests the primary), strict mode, or a language set outside the
    Indic roster.
    """
    if strict_mode():
        return None
    if _stt_primary_choice(agent_def) != "elevenlabs":
        return None
    langs = [(l or "").split("-")[0].lower()
             for l in _agent_supported_langs(agent_def)]
    if any(l not in SARVAM_LANGUAGES for l in langs if l):
        return None
    stt = TrackingSarvamSTT(call_id_provider=call_id_provider)
    if sarvam_key_provider is not None:
        stt.key_provider = sarvam_key_provider
    logger.info("[voice-provider] STT fallback=sarvam (primary=elevenlabs)")
    return stt


def build_stt_primary(agent_def, call_id_provider, sarvam_key_provider=None):
    """Build the STT primary for one call (router + Whisper stay outside)."""
    pref = provider_pref(agent_def)
    supported = _agent_supported_langs(agent_def)
    choice = _stt_primary_choice(agent_def)
    if choice == "elevenlabs":
        logger.info("[voice-provider] STT=elevenlabs (pref=%s, langs=%s)",
                    pref, supported)
        return TrackingScribeSTT(call_id_provider=call_id_provider)
    stt = TrackingSarvamSTT(call_id_provider=call_id_provider)
    if sarvam_key_provider is not None:
        stt.key_provider = sarvam_key_provider
    logger.info("[voice-provider] STT=sarvam (pref=%s, langs=%s)",
                pref, supported)
    return stt
