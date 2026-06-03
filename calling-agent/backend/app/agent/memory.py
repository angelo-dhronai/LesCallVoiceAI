"""
Conversation memory: sliding turn history + structured lead profile.

One instance lives per call. The LLM sees the full history each turn
so it can maintain context without any hardcoded state machine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

MAX_TURNS = 14  # 28 messages — enough context without blowing the token budget


@dataclass
class LeadProfile:
    """Structured data collected from the caller during the conversation."""
    name:             Optional[str]  = None
    phone:            Optional[str]  = None
    city:             Optional[str]  = None   # Surat / Vapi / Silvassa
    property_type:    Optional[str]  = None   # 2BHK / 3BHK / 4BHK / plot
    budget:           Optional[str]  = None   # free-form e.g. "70 lakh", "1.2 crore"
    purpose:          Optional[str]  = None   # "investment" | "self_use"
    timeline:         Optional[str]  = None   # "immediately" | "3 months" | etc.
    project_interest: Optional[str]  = None   # specific Pramukh project name
    visit_scheduled:  bool           = False
    visit_datetime:   Optional[str]  = None

    def summary_for_prompt(self) -> str:
        lines = []
        if self.name:             lines.append(f"- Name: {self.name}")
        if self.phone:            lines.append(f"- Phone: {self.phone}")
        if self.city:             lines.append(f"- City interest: {self.city}")
        if self.property_type:    lines.append(f"- Property type: {self.property_type}")
        if self.budget:           lines.append(f"- Budget: {self.budget}")
        if self.purpose:          lines.append(f"- Purpose: {self.purpose}")
        if self.timeline:         lines.append(f"- Timeline: {self.timeline}")
        if self.project_interest: lines.append(f"- Project interest: {self.project_interest}")
        if self.visit_scheduled:
            dt = f" ({self.visit_datetime})" if self.visit_datetime else ""
            lines.append(f"- Site visit: SCHEDULED{dt}")
        return "\n".join(lines) if lines else "Nothing confirmed yet."


class ConversationMemory:
    """Sliding window of turn messages + current lead profile."""

    def __init__(self) -> None:
        self.lead = LeadProfile()
        self._messages: list[dict[str, str]] = []

    def add_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})
        self._trim()

    def add_assistant(self, text: str) -> None:
        self._messages.append({"role": "assistant", "content": text})
        self._trim()

    def get_messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def update_lead(self, updates: dict) -> None:
        for key, value in updates.items():
            if value is not None and hasattr(self.lead, key):
                current = getattr(self.lead, key)
                if current is None or current is False:
                    setattr(self.lead, key, value)

    def last_assistant_text(self) -> str:
        for msg in reversed(self._messages):
            if msg["role"] == "assistant":
                return msg["content"]
        return ""

    def _trim(self) -> None:
        cap = MAX_TURNS * 2
        if len(self._messages) > cap:
            self._messages = self._messages[-cap:]
