"""Anthropic adapter (Messages API with tool use). Optional; OpenAI is the default real provider.

The client is injectable so unit tests use a fake and make no network calls.
"""
from __future__ import annotations

from typing import Any

from backend.agent.providers.base import LLMProvider, LLMResponse, Message, ProviderError, ToolCall, ToolSchema, Usage


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str = "", client: Any = None, timeout: float = 60.0):
        if not model:
            raise ProviderError("LLM_MODEL must be set for the anthropic provider")
        super().__init__(model)
        if client is None:
            if not api_key:
                raise ProviderError("ANTHROPIC_API_KEY is not set (put it in backend/.env)")
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self.client = client

    @staticmethod
    def to_anthropic(messages: list[Message]) -> tuple[str, list[dict]]:
        """Returns (system, messages). Consecutive tool results merge into one user turn."""
        system_parts: list[str] = []
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            elif m.role == "assistant":
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": m.content})
        return "\n\n".join(system_parts), out

    def complete(self, messages: list[Message], tools: list[ToolSchema], *, max_tokens: int = 1024) -> LLMResponse:
        system, msgs = self.to_anthropic(messages)
        kwargs: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "messages": msgs}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                               for t in tools]
        try:
            resp = self.client.messages.create(**kwargs)
        except Exception as e:
            raise ProviderError(f"anthropic call failed: {type(e).__name__}") from e
        texts, calls = [], []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                texts.append(block.text)
            elif btype == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text="\n".join(texts) if texts else None,
            tool_calls=calls,
            usage=Usage(input_tokens=getattr(usage, "input_tokens", 0) or 0,
                        output_tokens=getattr(usage, "output_tokens", 0) or 0),
            provider=self.name,
            model=self.model,
            stop_reason=getattr(resp, "stop_reason", "") or "",
        )
