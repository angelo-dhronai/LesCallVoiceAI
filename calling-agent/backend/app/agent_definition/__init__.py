"""
agent_definition — platform-level agent configuration layer.

Public API for the rest of the codebase:

    from app.agent_definition import AgentDefinition, get_pramukh_agent
"""

from .schema import (
    AgentDefinition,
    AgentIdentity,
    AppointmentSlot,
    CalendarConfig,
    CallFlowConfig,
    KnowledgeBase,
    LanguageConfig,
    LLMConfig,
    ResponseBank,
    STTCorrections,
    StageDefinition,
    TTSVoiceConfig,
)
from .pramukh_agent import get_pramukh_agent

__all__ = [
    "AgentDefinition",
    "AgentIdentity",
    "AppointmentSlot",
    "CalendarConfig",
    "CallFlowConfig",
    "KnowledgeBase",
    "LanguageConfig",
    "LLMConfig",
    "ResponseBank",
    "STTCorrections",
    "StageDefinition",
    "TTSVoiceConfig",
    "get_pramukh_agent",
]
