"""Conversation and message entities for the AI assistant.

These are plain data holders with an explicit role enum. The assistant is
modelled as a *transcript* rather than a live object graph because that is what
it is: the model's only memory between turns is the message list that is
replayed to it, and keeping that list as durable, ordered rows is what makes a
conversation resumable across devices and restarts.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class MessageRole(StrEnum):
    """Who produced a transcript entry.

    Matches the three roles the provider wire format actually uses; ``system``
    is deliberately absent because the system prompt is constructed per request
    and is never part of the stored transcript.
    """

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Conversation:
    """One assistant chat thread owned by a user."""

    id: str
    user_id: int
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass
class Message:
    """One transcript entry.

    ``position`` is the ordering key (gaps in it are meaningless; it exists so
    ordering survives equal timestamps, which happen on fast writes). ``meta``
    carries role-specific extras as JSON-safe values — for an assistant turn
    that requested tools, the tool-call ids/names/arguments; for a tool result,
    the ``tool_call_id`` it answers.
    """

    id: str
    conversation_id: str
    position: int
    role: MessageRole
    content: str
    tool_name: str | None = None
    meta: dict[str, Any] | None = field(default=None)
    created_at: datetime | None = None
