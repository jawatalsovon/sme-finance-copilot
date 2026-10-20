"""Provider-neutral LLM interface.

    provider.complete(messages, tools, max_tokens=...) -> LLMResponse

Messages use four roles: system, user, assistant (may carry tool_calls), tool (result of
one tool call, linked by tool_call_id). Each adapter converts to its SDK's format.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None          # tool name for role="tool"


@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]       # JSON Schema (type=object)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]
    usage: Usage
    provider: str
    model: str
    stop_reason: str = ""


class ProviderError(RuntimeError):
    pass


class LLMProvider(abc.ABC):
    name: str = "base"

    def __init__(self, model: str):
        self.model = model

    @abc.abstractmethod
    def complete(self, messages: list[Message], tools: list[ToolSchema], *, max_tokens: int = 1024) -> LLMResponse:
        """One model turn. Must not retry on its own in a way that bypasses the cost guard."""
