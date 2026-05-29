import asyncio
import logging
import queue as _queue
import threading
import numpy as np
from .base import AudioSink

logger = logging.getLogger(__name__)

class LocalSpeakerSink(AudioSink):
    """
    Real-time speaker output using sounddevice.
    Wraps the existing logic to fit the new AudioSink interface.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        output_device: int | str | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._output_device = output_device
        self._audio_q: _queue.Queue[np.ndarray | None] = _queue.Queue(maxsize=200)
        self._playback_thread: threading.Thread | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._playback_thread = threading.Thread(target=self._play_loop, daemon=True)
        self._playback_thread.start()
        logger.info("[LocalSpeakerSink] Playback thread started")

    def _play_loop(self) -> None:
        import sounddevice as sd
        try:
            with sd.OutputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                device=self._output_device,
            ) as stream:
                while self._running:
                    try:
                        data = self._audio_q.get(timeout=0.5)
                    except _queue.Empty:
                        continue
                    if data is None:
                        break
                    try:
                        stream.write(data.reshape(-1, 1))
                    except sd.PortAudioError as exc:
                        logger.error("[LocalSpeakerSink] PortAudio error: %s", exc)
                        break
        except Exception as exc:
            logger.error("[LocalSpeakerSink] Playback error: %s", exc)
        finally:
            self._running = False
            logger.info("[LocalSpeakerSink] Playback loop stopped")

    async def write_frame(self, pcm: bytes):
        if not self._running:
            return

        # Convert PCM16 bytes to float32 for sounddevice
        samples = np.frombuffer(pcm, dtype=np.int16)
        audio_float = samples.astype(np.float32) / 32768.0
        
        try:
            self._audio_q.put(audio_float, timeout=0.1)
        except _queue.Full:
            logger.warning("[LocalSpeakerSink] Audio queue full — dropping chunk")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._audio_q:
            try:
                self._audio_q.put_nowait(None)
            except _queue.Full:
                pass
        if self._playback_thread:
            self._playback_thread.join(timeout=2.0)
            self._playback_thread = None
        
        import sounddevice as sd
        try:
            sd.stop()
        except Exception:
            pass
        logger.info("[LocalSpeakerSink] Stopped")
