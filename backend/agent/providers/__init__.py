"""LLM providers: mock (default), openai, anthropic - one interface, always behind the cost guard."""
from __future__ import annotations

from backend.agent.providers.base import (
    LLMProvider, LLMResponse, Message, ProviderError, ToolCall, ToolSchema, Usage,
)
from backend.agent.providers.cost import BudgetExceeded, CostGuard, GuardedProvider, PricingTable
from backend.agent.providers.mock import MockProvider


def build_provider(settings) -> LLMProvider:
    """Provider from settings. Real SDK clients are only created here, never in tests."""
    name = settings.llm_provider
    if name == "mock":
        return MockProvider(settings.llm_model or "mock-1")
    if name == "openai":
        from backend.agent.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(settings.llm_model, api_key=settings.openai_api_key)
    if name == "anthropic":
        from backend.agent.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings.llm_model, api_key=settings.anthropic_api_key)
    raise ProviderError(f"unknown LLM_PROVIDER {name!r} (use mock | openai | anthropic)")


__all__ = [
    "LLMProvider", "LLMResponse", "Message", "ProviderError", "ToolCall", "ToolSchema", "Usage",
    "BudgetExceeded", "CostGuard", "GuardedProvider", "PricingTable", "MockProvider", "build_provider",
]
