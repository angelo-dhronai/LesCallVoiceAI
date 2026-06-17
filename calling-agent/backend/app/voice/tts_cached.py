"""
CachedTTS — serves pre-generated WAV files instead of calling Sarvam API.

Inherits from TrackingSarvamTTS so it is a drop-in replacement. When
speak_cached() finds a matching WAV in the manifest, it streams PCM frames
directly from disk (~5ms latency, zero API cost). If no match is found it
returns False and the caller falls through to live SarvamTTS via speak().
"""

from __future__ import annotations

import audioop
import json
import logging
import wave
from pathlib import Path
from typing import TYPE_CHECKING

from .tracking_tts import TrackingSarvamTTS

if TYPE_CHECKING:
    from ..audio_transport.base import AudioSink

logger = logging.getLogger(__name__)

# Default cache dir: <repo>/calling-agent/tts_cache/
_DEFAULT_CACHE_DIR = str(Path(__file__).parent.parent.parent.parent / "tts_cache")

_PCM_CHUNK_BYTES = 4096
_TARGET_SAMPLE_RATE = 16_000  # pipeline and TelephonySink both expect 16kHz


class CachedTTS(TrackingSarvamTTS):
    """
    TrackingSarvamTTS extended with a speak_cached() method.

    speak_cached() streams a pre-generated WAV from disk and calls
    sink.flush() so the echo-mute window in pipeline.py is sized correctly.
    speak() is unchanged — live Sarvam TTS is used as normal fallback.
    """

    def __init__(self, *args, cache_dir: str = _DEFAULT_CACHE_DIR, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cache_dir = Path(cache_dir)
        self._cache_map: dict[str, dict[str, str]] = {}
        self._load_manifest()

    # ── Manifest ──────────────────────────────────────────────────────────────

    def _load_manifest(self) -> None:
        manifest_path = self._cache_dir / "manifest.json"
        if not manifest_path.exists():
            logger.warning(
                "[CachedTTS] manifest.json not found at %s — cache disabled, "
                "all responses will use live TTS",
                manifest_path,
            )
            return
        try:
            with open(manifest_path) as f:
                raw: dict = json.load(f)
            # raw keys are "greeting_en", "faq_rera_hi", etc.
            # Restructure to {key: {lang: path}}
            for composite_key, entry in raw.items():
                key = entry["key"]
                lang = entry["lang"]
                path = entry["path"]
                self._cache_map.setdefault(key, {})[lang] = path
            logger.info(
                "[CachedTTS] Loaded %d cache entries (%d keys)",
                len(raw),
                len(self._cache_map),
            )
        except Exception as exc:
            logger.warning("[CachedTTS] Failed to load manifest: %s — cache disabled", exc)
            self._cache_map = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def cache_has(self, key: str, lang: str) -> bool:
        """Return True if key+lang (or key+en fallback) exists in the cache."""
        langs = self._cache_map.get(key, {})
        return lang in langs or "en" in langs

    async def speak_cached(
        self,
        cache_key: str,
        lang: str,
        sink: "AudioSink | None" = None,
    ) -> bool:
        """
        Stream a pre-generated WAV to sink instead of calling Sarvam TTS.

        Returns True on success, False if the key is not cached (caller should
        fall back to live TTS via speak()).
        """
        effective_sink = sink or self._sink
        if effective_sink is None:
            logger.warning("[CachedTTS] No sink available for cache playback")
            return False

        # Resolve path: exact lang match, then English fallback
        langs = self._cache_map.get(cache_key, {})
        wav_path_str = langs.get(lang) or langs.get("en")
        if not wav_path_str:
            logger.warning(
                "[CachedTTS] Cache miss: key=%s lang=%s (no en fallback either)",
                cache_key, lang,
            )
            return False

        wav_path = Path(wav_path_str)
        if not wav_path.exists():
            logger.warning(
                "[CachedTTS] WAV file missing on disk: %s — falling back to live TTS",
                wav_path,
            )
            return False

        try:
            self._reset()
            self._playing = True

            with wave.open(str(wav_path), "rb") as wf:
                n_frames = wf.getnframes()
                rate = wf.getframerate()
                duration = n_frames / float(rate)
                needs_resample = rate != _TARGET_SAMPLE_RATE
                if needs_resample:
                    logger.debug(
                        "[CachedTTS] Resampling %s: %dHz → %dHz",
                        wav_path.name, rate, _TARGET_SAMPLE_RATE,
                    )
                resample_state = None
                # Each frame is 2 bytes (PCM16 mono)
                frames_per_chunk = _PCM_CHUNK_BYTES // 2

                while True:
                    if self._cancelled:
                        logger.info(
                            "[CachedTTS] Playback cancelled mid-stream: key=%s", cache_key
                        )
                        break
                    pcm = wf.readframes(frames_per_chunk)
                    if not pcm:
                        break
                    if needs_resample:
                        pcm, resample_state = audioop.ratecv(
                            pcm, 2, 1, rate, _TARGET_SAMPLE_RATE, resample_state
                        )
                    await effective_sink.write_frame(pcm)

            if not self._cancelled:
                await effective_sink.flush()
                logger.info(
                    "[CachedTTS] served key=%s lang=%s duration=%.2fs (src_rate=%dHz)",
                    cache_key, lang, duration, rate,
                )

            return True

        except Exception as exc:
            logger.error(
                "[CachedTTS] Error streaming cached WAV key=%s: %s", cache_key, exc
            )
            return False
        finally:
            self._playing = False
            self._reset()

    # speak() is inherited unchanged from TrackingSarvamTTS
