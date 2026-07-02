"""
Base interface for STT providers.

All providers receive a raw PCM16 mono audio buffer and return a
transcript string. The `pcm_to_wav` helper converts raw bytes into
a valid WAV file in memory — required by most HTTP-based STT APIs.
`normalize_pcm_audio` boosts quiet telephone-line audio before upload.
"""

from __future__ import annotations

import io
import wave
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


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

    gain = min(target_rms / rms, max_gain)
    if gain <= 1.05:
        return pcm_bytes  # already at or above target level

    normalized = np.clip(samples * gain, -32768.0, 32767.0).astype(np.int16)
    logger.debug(
        "[AUDIO-NORM] rms=%.0f gain=%.2fx -> rms≈%.0f", rms, gain, min(rms * gain, 32767)
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
