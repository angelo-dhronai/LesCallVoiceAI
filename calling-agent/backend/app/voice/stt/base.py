"""
Base interface for STT providers.

All providers receive a raw PCM16 mono audio buffer and return a
transcript string. The `pcm_to_wav` helper converts raw bytes into
a valid WAV file in memory — required by most HTTP-based STT APIs.
`normalize_pcm_audio` boosts quiet telephone-line audio before upload.
"""

from __future__ import annotations

import io
import os
import wave
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# Master switch for the adaptive audio-handling layer. Default ON (improves
# capture of soft/weak/deep voices). Set ADAPTIVE_AUDIO=0 to fall back to the
# exact legacy fixed-gain behaviour instantly, with zero code change.
ADAPTIVE_AUDIO = os.environ.get("ADAPTIVE_AUDIO", "1") != "0"

# 20 ms frame at 16 kHz = 320 samples. Used to measure the *voiced* level of a
# segment rather than a silence-diluted whole-segment mean.
_FRAME_SAMPLES = 320


def _frame_rms(samples: np.ndarray, frame_len: int = _FRAME_SAMPLES) -> np.ndarray:
    """Per-frame RMS over 20 ms windows (float64 to avoid int16² overflow)."""
    n = len(samples) // frame_len
    if n == 0:
        return np.array([float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))])
    trimmed = samples[: n * frame_len].reshape(n, frame_len).astype(np.float64)
    return np.sqrt(np.mean(trimmed ** 2, axis=1))


def classify_segment(pcm_bytes: bytes) -> dict:
    """
    Stage-0 measurement: cheap per-utterance signal fingerprint used to pick the
    right handling and to make the dynamic behaviour observable in logs. Pure
    measurement — never mutates audio.
    """
    if len(pcm_bytes) < 2:
        return {"rms": 0.0, "peak": 0.0, "speech": 0.0, "crest": 0.0,
                "clip_ratio": 0.0, "klass": "empty"}
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples ** 2)))
    peak = float(np.max(np.abs(samples)))
    frames_rms = _frame_rms(samples)
    speech = float(np.percentile(frames_rms, 90)) if len(frames_rms) >= 3 else rms
    crest = (peak / rms) if rms > 0 else 0.0
    clip_ratio = float(np.mean(np.abs(samples) >= 32767.0))
    if clip_ratio > 0.005:
        klass = "clipped"          # already hot / distorted at source
    elif speech < 600.0:
        klass = "soft"             # soft / weak / distant voice
    elif speech > 6000.0:
        klass = "loud"             # deep / loud / close voice
    else:
        klass = "normal"
    return {"rms": rms, "peak": peak, "speech": speech, "crest": crest,
            "clip_ratio": clip_ratio, "klass": klass}


@dataclass
class STTResult:
    """STT transcription result with confidence score."""
    text: str
    confidence: float = 1.0
    detected_language: str | None = None
    # True when an empty result is due to a foreign / unsupported language being
    # detected (Tamil, Punjabi, etc.) — i.e. almost certainly noise or cross-talk
    # rather than genuine unclear Hindi/Gujarati/English. The pipeline uses this
    # to suppress the "माफ़ कीजिए, आवाज़ स्पष्ट नहीं आई" fallback in that case.
    noise_rejected: bool = False


def normalize_pcm_audio(
    pcm_bytes: bytes,
    target_rms: float = 3000.0,
    max_gain: float = 8.0,
) -> bytes:
    """
    Normalize PCM16 audio amplitude before STT submission.

    Telephone-line audio is often quiet (low RMS). Boosting it up to
    target_rms improves transcription accuracy without risking clipping
    because gain is capped at max_gain and the result is always clipped
    to int16 range. Near-silence frames are returned unchanged to avoid
    amplifying pure noise.

    Parameters
    ----------
    pcm_bytes : bytes
        Raw PCM16 mono audio.
    target_rms : float
        Desired RMS level after normalization.
    max_gain : float
        Maximum amplification factor (prevents noise blowup).
    """
    if len(pcm_bytes) < 320:
        return pcm_bytes  # too short to normalize reliably

    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples ** 2)))

    if rms < 10.0:
        return pcm_bytes  # near-silence — amplifying would only boost noise

    if not ADAPTIVE_AUDIO:
        # ---- legacy fixed-gain path (bytes unchanged from prior behaviour) ----
        gain = min(target_rms / rms, max_gain)
        if gain <= 1.05:
            return pcm_bytes  # already at or above target level
        normalized = np.clip(samples * gain, -32768.0, 32767.0).astype(np.int16)
        logger.debug(
            "[AUDIO-NORM] rms=%.0f gain=%.2fx -> rms≈%.0f", rms, gain, min(rms * gain, 32767)
        )
        return normalized.tobytes()

    # ---- adaptive path: gain the actual VOICED level to target, never clip ----
    # 1) Drive gain from the speech level (90th-pct of per-frame RMS), NOT the
    #    silence-diluted whole-segment RMS. This lifts a soft/weak voice to a
    #    usable level while amplifying the noise floor far less than the legacy
    #    whole-segment gain did (SNR-preserving).
    # 2) Cap gain by peak headroom so a deep/hot voice with near-full-scale peaks
    #    is never driven into hard clipping (which distorts and hurts STT).
    # 3) Never attenuate — this stage only boosts. Already-loud/normal segments
    #    resolve to gain≈1 and return byte-identical to the legacy path.
    frames_rms = _frame_rms(samples)
    speech_level = float(np.percentile(frames_rms, 90)) if len(frames_rms) >= 3 else rms
    speech_level = max(speech_level, rms * 0.5, 1.0)

    peak = float(np.max(np.abs(samples)))
    gain_target = target_rms / speech_level
    gain_peak = (32767.0 * 0.95) / peak if peak > 0 else max_gain
    gain = min(gain_target, gain_peak, max_gain)
    gain = max(gain, 1.0)

    if gain <= 1.05:
        return pcm_bytes  # already adequate / no headroom — preserve exact bytes

    normalized = np.clip(samples * gain, -32768.0, 32767.0).astype(np.int16)
    logger.debug(
        "[AUDIO-NORM] adaptive rms=%.0f speech=%.0f peak=%.0f "
        "g_tgt=%.2f g_peak=%.2f gain=%.2fx",
        rms, speech_level, peak, gain_target, gain_peak, gain,
    )
    return normalized.tobytes()


def pcm_to_wav(
    pcm_bytes: bytes,
    sample_rate: int = 16_000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Convert raw PCM16 bytes into a complete WAV file in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


class STTProvider(ABC):
    """
    Abstract STT provider.

    Subclasses implement `transcribe()` which takes raw PCM16 mono audio
    bytes and returns the transcript as a plain string. Implementations
    handle WAV conversion, HTTP transport, and response parsing internally.
    """

    @abstractmethod
    async def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16_000,
        language: str = "en",
    ) -> STTResult:
        """
        Transcribe raw PCM16 audio to text.

        Parameters
        ----------
        audio_bytes : bytes
            Raw PCM16 mono audio.
        sample_rate : int
            Sample rate of the audio.
        language : str
            BCP-47 language code hint.

        Returns
        -------
        STTResult
            Transcript text and confidence score.
        """
        ...
