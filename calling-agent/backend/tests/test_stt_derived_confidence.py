"""Derived STT confidence: the wps hallucination guard must score against
the audio the transcript actually covers, not just the local segment.
Regression for the live barge-in-stitch rejection (2026-07-29): streaming
STT returned 7 words for a 464ms local buffer (the rest of the speech was
server-side, dropped locally by the barge-in flush) → 15 wps → falsely
rejected as over-generation, and the booking turn never reached the LLM."""

from app.voice.stt.router import STTRouter

SR = 16000


def _audio(sec: float) -> bytes:
    return b"\x00" * int(2 * SR * sec)


TEXT = "शाम चार बजे साइट विजिट बुक कीजिए"  # 7 words


def test_short_local_buffer_alone_reads_as_overgeneration():
    score = STTRouter._derive_confidence(TEXT, _audio(0.46), SR, 800, None)
    assert score < 0.4  # the pre-fix behavior: would be rejected


def test_covered_seconds_rescues_barge_in_stitch():
    score = STTRouter._derive_confidence(
        TEXT, _audio(0.46), SR, 800, None, covered_seconds=4.0)
    assert score >= 0.9  # 7 words over 4s of real speech is normal


def test_covered_seconds_never_shrinks_duration():
    # covered smaller than the local buffer must not inflate wps.
    plain = STTRouter._derive_confidence(TEXT, _audio(3.0), SR, 800, None)
    with_small = STTRouter._derive_confidence(
        TEXT, _audio(3.0), SR, 800, None, covered_seconds=0.2)
    assert with_small == plain
