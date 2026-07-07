"""
Voice Activity Detection.

Energy-based VAD using numpy-vectorized mean-absolute computation.
"""

from __future__ import annotations

import logging
import os

import numpy as np

logger = logging.getLogger(__name__)

# Master switch (shared intent with stt.base.ADAPTIVE_AUDIO). Default ON.
# ADAPTIVE_AUDIO=0 freezes the VAD to its exact legacy one-shot-calibrated
# threshold behaviour.
ADAPTIVE_AUDIO = os.environ.get("ADAPTIVE_AUDIO", "1") != "0"


class VoiceActivityDetector:
    def __init__(
        self,
        sample_rate: int = 16000,
        energy_threshold: float | None = None,
        calibration_frames: int = 25,
        calibration_multiplier: float = 2.0,
        minimum_threshold: float = 250.0,
        maximum_threshold: float = 1200.0,
        barge_in_multiplier: float = 2.0,
        barge_in_minimum: float = 500.0,
        minimum_rms_floor: float = 40.0,
    ) -> None:

        self.sample_rate = sample_rate

        self._threshold: float = energy_threshold or 0.0

        self._minimum_threshold = minimum_threshold
        self._maximum_threshold = maximum_threshold

        self._calibration_multiplier = calibration_multiplier

        self._barge_in_multiplier = barge_in_multiplier
        self._barge_in_minimum = barge_in_minimum

        self._minimum_rms_floor = minimum_rms_floor

        self._calibrating = energy_threshold is None
        self._calibration_energies: list[float] = []
        self._calibration_target = calibration_frames
        self._calibrated = not self._calibrating

        self._last_energy: float = 0.0
        self._log_counter = 0

        # --- Adaptive noise tracking (dynamic per-line/per-moment threshold) ---
        # The legacy VAD freezes its threshold once at calibration and clamps it
        # up to `minimum_threshold` (260 in telephony). On a quiet line whose
        # real noise floor is ~40, that 260 clamp silently drops soft/weak/distant
        # voices whose speech energy lands in the 108-260 band. The adaptive
        # tracker follows the true noise floor and sets the turn-detection
        # threshold to a fixed SNR margin above it, so soft speech is captured
        # while a genuinely noisy line still raises the bar.
        self._adaptive = ADAPTIVE_AUDIO
        # Frozen baseline captured at calibration — barge-in ALWAYS uses this, so
        # the TTS-interrupt / echo path is unaffected by adaptive turn-detection.
        self._calibrated_threshold: float = self._threshold
        self._noise_ema: float = 0.0
        self._noise_alpha: float = 0.05          # slow tracker (~1s to converge)
        self._adaptive_multiplier: float = 2.5   # SNR margin over the noise floor
        # Adaptive threshold may drop BELOW the (deliberately high) fixed
        # minimum_threshold to catch soft speech, but never below a safe margin
        # over the absolute rms floor — so comfort-noise never reads as speech.
        self._adaptive_floor: float = max(minimum_rms_floor * 1.8, 90.0)

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def calibrated(self) -> bool:
        return self._calibrated

    @property
    def last_energy(self) -> float:
        return self._last_energy

    @property
    def barge_in_threshold(self) -> float:
        # Barge-in is pinned to the frozen calibration baseline (identical to the
        # calibrated threshold when adaptive is off), so adaptive turn-detection
        # changes can never make barge-in more or less sensitive than before.
        base = self._calibrated_threshold if self._adaptive else self._threshold
        return max(
            base * self._barge_in_multiplier,
            self._barge_in_minimum,
        )

    def _compute_energy(self, frame: bytes) -> float:
        audio = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        return float(np.abs(audio).mean())

    def is_speech(self, frame: bytes) -> bool:
        energy = self._compute_energy(frame)

        self._last_energy = energy

        self._log_counter += 1

        # Periodic diagnostic: energy vs thresholds every ~1 second (50 frames * 20ms)
        if self._log_counter % 50 == 1:
            if self._calibrated:
                logger.info(
                    "[VAD] energy=%.1f threshold=%.1f barge_in=%.1f "
                    "rms_floor=%.1f decision=%s",
                    energy,
                    self._threshold,
                    self.barge_in_threshold,
                    self._minimum_rms_floor,
                    "SPEECH" if (energy > self._minimum_rms_floor and energy > self._threshold) else "silence",
                )
            else:
                logger.info(
                    "[VAD] Calibrating: energy=%.1f frames=%d/%d",
                    energy,
                    len(self._calibration_energies),
                    self._calibration_target,
                )

        # Calibration phase: sample ALL frames including silence so calibration
        # always completes in 25 frames (500ms). The rms_floor guard must not
        # run here — PCMU comfort-noise (0xFF → energy=0) would block every frame
        # and leave the VAD permanently uncalibrated, preventing all STT.
        # _finish_calibration() clamps threshold to minimum_threshold (150/260)
        # when the sampled frames are all silence, which is the correct baseline
        # for a telephony channel before the caller first speaks.
        if not self._calibrated:
            self._calibration_energies.append(energy)

            if len(self._calibration_energies) >= self._calibration_target:
                self._finish_calibration()

            return False

        # Adaptive noise tracking: update the noise floor estimate using ONLY
        # frames close to the current estimate (well below speech). Loud frames
        # and mid-word pauses stay above the noise gate and never update it, so
        # the tracker cannot chase the speaker or inflate mid-utterance. The
        # turn-detection threshold is then set to a fixed SNR margin above the
        # tracked floor, bounded by [adaptive_floor, maximum_threshold].
        if self._adaptive:
            noise_gate = max(self._noise_ema * 3.0, self._adaptive_floor)
            if energy < noise_gate:
                self._noise_ema = (
                    (1.0 - self._noise_alpha) * self._noise_ema
                    + self._noise_alpha * energy
                )
                raw = self._noise_ema * self._adaptive_multiplier
                self._threshold = float(
                    min(max(raw, self._adaptive_floor), self._maximum_threshold)
                )

        # Post-calibration: ignore absolute silence (below the noise floor).
        if energy < self._minimum_rms_floor:
            return False

        return energy > self._threshold

    def _finish_calibration(self) -> None:
        arr = np.array(self._calibration_energies)

        noise_mean = float(arr.mean())
        noise_max = float(arr.max())

        sorted_arr = np.sort(arr)

        quiet_half = sorted_arr[: max(1, len(sorted_arr) // 2)]

        noise_p95 = float(np.percentile(quiet_half, 95))

        raw = noise_p95 * self._calibration_multiplier

        capped = min(raw, self._maximum_threshold)

        self._threshold = max(capped, self._minimum_threshold)

        # Freeze the calibrated value as the barge-in baseline, then seed the
        # adaptive noise tracker from the measured quiet-frame noise and set the
        # initial adaptive turn-detection threshold. This lets a quiet line drop
        # below the fixed minimum_threshold (to catch soft voices) immediately,
        # while a noisy line keeps a high bar.
        self._calibrated_threshold = self._threshold
        if self._adaptive:
            self._noise_ema = max(noise_p95, 1.0)
            raw_adaptive = self._noise_ema * self._adaptive_multiplier
            self._threshold = float(
                min(max(raw_adaptive, self._adaptive_floor), self._maximum_threshold)
            )

        if raw > self._maximum_threshold:
            logger.warning(
                "[VAD] Calibration noise too high "
                "(p95=%.1f * %.1f = %.1f) capped at %.1f",
                noise_p95,
                self._calibration_multiplier,
                raw,
                self._maximum_threshold,
            )

        self._calibrated = True

        logger.info(
            "[VAD] Calibrated in %d frames "
            "(%.0fms) mean=%.2f p95=%.2f max=%.2f "
            "threshold=%.1f barge_in=%.1f",
            len(self._calibration_energies),
            len(self._calibration_energies) * 20,
            noise_mean,
            noise_p95,
            noise_max,
            self._threshold,
            self.barge_in_threshold,
        )
