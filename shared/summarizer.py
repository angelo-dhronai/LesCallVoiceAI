"""AI call summaries over transcripts, via OpenAI.

Produces a structured summary: a short paragraph plus sentiment and outcome,
suitable for the dashboard. Uses the OpenAI client already in the stack.

Env:
    OPENAI_API_KEY   required
    SUMMARY_MODEL    optional (default gpt-4o-mini)
"""
from __future__ import annotations

import json
import os

from shared.transcript import transcript_to_plain

_SYSTEM = (
    "You summarize sales phone calls for a real-estate company. Given a transcript, "
    "return STRICT JSON with keys: summary (2-3 sentence plain-English recap), "
    "sentiment (one of: positive, neutral, negative), outcome (one of: booked, "
    "interested, callback, not_interested, no_answer, other), key_points (array of "
    "short strings). No prose outside the JSON."
)


def summarize_turns(turns: list[dict]) -> dict:
    """Summarize parsed transcript turns. Returns the structured dict.

    Raises if OPENAI_API_KEY is missing or the API call fails — callers decide
    how to surface that (the dashboard stores the error and shows a retry).
    """
    if not turns:
        return {"summary": "", "sentiment": "neutral", "outcome": "no_answer", "key_points": []}

    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)
    model = os.environ.get("SUMMARY_MODEL", "gpt-4o-mini")
    plain = transcript_to_plain(turns)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Transcript:\n{plain}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)
    data.setdefault("summary", "")
    data.setdefault("sentiment", "neutral")
    data.setdefault("outcome", "other")
    data.setdefault("key_points", [])
    return data
