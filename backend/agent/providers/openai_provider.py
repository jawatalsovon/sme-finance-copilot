"""OpenAI adapter (Chat Completions with function/tool calling).

The client is injectable so unit tests use a fake and make no network calls.
"""
from __future__ import annotations

import json
from typing import Any

from backend.agent.providers.base import LLMProvider, LLMResponse, Message, ProviderError, ToolCall, ToolSchema, Usage


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str, api_key: str = "", client: Any = None, timeout: float = 60.0):
        if not model:
            raise ProviderError("LLM_MODEL must be set for the openai provider")
        super().__init__(model)
        if client is None:
            if not api_key:
                raise ProviderError("OPENAI_API_KEY is not set (put it in backend/.env)")
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=timeout, max_retries=1)
        self.client = client

    @staticmethod
    def to_openai_messages(messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                out.append({
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                        for tc in m.tool_calls
                    ],
                })
            elif m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    @staticmethod
    def to_openai_tools(tools: list[ToolSchema]) -> list[dict]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.parameters}} for t in tools]

    def complete(self, messages: list[Message], tools: list[ToolSchema], *, max_tokens: int = 1024) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.to_openai_messages(messages),
            "max_completion_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self.to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:  # SDK errors -> one type; never include request content
            raise ProviderError(f"openai call failed: {type(e).__name__}") from e
        choice = resp.choices[0]
        msg = choice.message
        calls = []
        for tc in getattr(msg, "tool_calls", None) or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args if isinstance(args, dict) else {}))
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=msg.content,
            tool_calls=calls,
            usage=Usage(input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        output_tokens=getattr(usage, "completion_tokens", 0) or 0),
            provider=self.name,
            model=self.model,
            stop_reason=getattr(choice, "finish_reason", "") or "",
        )
