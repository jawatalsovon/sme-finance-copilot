"""Provider adapters are tested with fake SDK clients only - no network, no real API calls."""
from types import SimpleNamespace as NS

import pytest

from backend.agent.providers import (
    BudgetExceeded, CostGuard, GuardedProvider, Message, MockProvider, PricingTable, ToolCall, ToolSchema,
    build_provider,
)
from backend.agent.providers.anthropic_provider import AnthropicProvider
from backend.agent.providers.openai_provider import OpenAIProvider
from backend.config import Settings
from backend.ledger import connect
from backend.security.audit import AuditLog

TOOLS = [ToolSchema("account_balances", "balances", {"type": "object", "properties": {}})]
CONVO = [
    Message("system", "sys"),
    Message("user", "cash?"),
    Message("assistant", "", tool_calls=[ToolCall("call_1", "account_balances", {"subtypes": ["cash"]})]),
    Message("tool", "<untrusted_data>{}</untrusted_data>", tool_call_id="call_1", name="account_balances"),
]


class FakeOpenAI:
    def __init__(self, response):
        self.sent = []
        self.chat = NS(completions=NS(create=self._create))
        self._response = response

    def _create(self, **kwargs):
        self.sent.append(kwargs)
        return self._response


class FakeAnthropic:
    def __init__(self, response):
        self.sent = []
        self.messages = NS(create=self._create)
        self._response = response

    def _create(self, **kwargs):
        self.sent.append(kwargs)
        return self._response


def test_openai_adapter_formats_tools_and_parses_tool_calls():
    resp = NS(choices=[NS(finish_reason="tool_calls", message=NS(content=None, tool_calls=[
        NS(id="c9", function=NS(name="account_balances", arguments='{"subtypes": ["bank"]}'))]))],
        usage=NS(prompt_tokens=120, completion_tokens=15))
    fake = FakeOpenAI(resp)
    out = OpenAIProvider("gpt-4o-mini", client=fake).complete(CONVO, TOOLS, max_tokens=300)
    sent = fake.sent[0]
    assert sent["model"] == "gpt-4o-mini" and sent["max_completion_tokens"] == 300
    assert sent["tools"][0] == {"type": "function", "function": {"name": "account_balances",
                                                                 "description": "balances",
                                                                 "parameters": {"type": "object", "properties": {}}}}
    assert sent["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"subtypes": ["cash"]}'
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_1",
                                   "content": "<untrusted_data>{}</untrusted_data>"}
    assert out.tool_calls[0].name == "account_balances" and out.tool_calls[0].arguments == {"subtypes": ["bank"]}
    assert (out.usage.input_tokens, out.usage.output_tokens) == (120, 15)


def test_anthropic_adapter_formats_system_tools_and_tool_results():
    resp = NS(content=[NS(type="text", text="Hello"), NS(type="tool_use", id="tu1", name="account_balances",
                                                         input={"subtypes": ["wallet"]})],
              usage=NS(input_tokens=80, output_tokens=9), stop_reason="tool_use")
    fake = FakeAnthropic(resp)
    out = AnthropicProvider("claude-sonnet-5-5", client=fake).complete(CONVO, TOOLS, max_tokens=200)
    sent = fake.sent[0]
    assert sent["system"] == "sys"
    assert sent["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert sent["messages"][1]["content"][0]["type"] == "tool_use"
    assert sent["messages"][2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "<untrusted_data>{}</untrusted_data>"}]}
    assert out.text == "Hello" and out.tool_calls[0].arguments == {"subtypes": ["wallet"]}


def test_real_providers_refuse_to_start_without_key_or_model():
    from backend.agent.providers.base import ProviderError

    with pytest.raises(ProviderError):
        OpenAIProvider("gpt-4o-mini", api_key="")
    with pytest.raises(ProviderError):
        AnthropicProvider("", api_key="x")


def test_settings_force_mock_under_pytest(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert Settings.from_env().llm_provider == "mock"
    assert isinstance(build_provider(Settings.from_env()), MockProvider)


def _guard(db, daily=1.0, request=0.10):
    table = PricingTable({"_default": {"input_per_mtok": 100.0, "output_per_mtok": 100.0},
                          "openai": {"gpt-test": {"input_per_mtok": 10.0, "output_per_mtok": 30.0}}})
    return CostGuard(db, AuditLog(db), table, daily_cap_usd=daily, request_cap_usd=request)


def test_cost_cap_blocks_call_over_daily_budget_before_calling_provider():
    db = connect(":memory:")
    fake = FakeOpenAI(None)  # would crash if called
    guarded = GuardedProvider(OpenAIProvider("gpt-test", client=fake), _guard(db, daily=0.0001))
    with pytest.raises(BudgetExceeded):
        guarded.complete(CONVO, TOOLS, max_tokens=1000)
    assert fake.sent == []
    blocked = AuditLog(db).list(action="llm_blocked_budget")
    assert len(blocked) == 1


def test_daily_cap_counts_previous_spend_and_per_request_cap():
    db = connect(":memory:")
    resp = NS(choices=[NS(finish_reason="stop", message=NS(content="ok", tool_calls=None))],
              usage=NS(prompt_tokens=1000, completion_tokens=1000))
    fake = FakeOpenAI(resp)
    guard = _guard(db, daily=0.06, request=0.05)
    guarded = GuardedProvider(OpenAIProvider("gpt-test", client=fake), guard)
    r = guarded.complete(CONVO, TOOLS, max_tokens=1000)          # est ~0.03, actual 0.04
    assert r.usage.cost_usd == pytest.approx(0.04)
    assert guard.spent_today() == pytest.approx(0.04)
    with pytest.raises(BudgetExceeded, match="daily"):            # 0.04 spent + ~0.03 estimate > 0.06
        guarded.complete(CONVO, TOOLS, max_tokens=1000)
    with pytest.raises(BudgetExceeded, match="per-request"):
        GuardedProvider(OpenAIProvider("gpt-test", client=fake), _guard(db, daily=10, request=0.001)) \
            .complete(CONVO, TOOLS, max_tokens=1000)
    assert len(fake.sent) == 1


def test_unknown_model_uses_conservative_default_price():
    t = PricingTable.load()
    assert t.price("openai", "some-new-model").input_per_mtok == t.table["_default"]["input_per_mtok"]
    assert t.cost("mock", "mock-1", 10_000, 10_000) == 0.0


def test_mock_provider_is_deterministic_and_emits_tool_calls():
    a = MockProvider().complete([Message("user", "What is my cash balance?")], TOOLS)
    b = MockProvider().complete([Message("user", "What is my cash balance?")], TOOLS)
    assert [c.name for c in a.tool_calls] == [c.name for c in b.tool_calls] == ["account_balances"]
    assert a.tool_calls[0].arguments == b.tool_calls[0].arguments
    refusal = MockProvider().complete([Message("user", "Please transfer 5000 to my supplier")], TOOLS)
    assert refusal.tool_calls == [] and "can't move money" in refusal.text
