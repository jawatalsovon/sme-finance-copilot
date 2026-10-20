"""Deterministic mock provider (default for dev and tests, costs $0).

Two modes
  * script mode:   MockProvider(script=[LLMResponse | dict, ...]) returns the items in order
                   (dict form: {"text": ..., "tool_calls": [{"name", "arguments"}]}).
  * scenario mode: picks a scenario from keywords in the latest user message, emits that
                   scenario's tool calls, and after the tool results come back writes an
                   answer that ONLY copies the code-generated `summary` of each tool result
                   and cites its evidence refs. It never computes a number.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.agent.providers.base import LLMProvider, LLMResponse, Message, ToolCall, ToolSchema, Usage
from backend.core.util import business_today
from backend.security.injection import unwrap_untrusted
from backend.security.policy import MONEY_POLICY_NOTE


def _prev_month(today: dt.date | None = None) -> str:
    d = (today or business_today()).replace(day=1) - dt.timedelta(days=1)
    return d.strftime("%Y-%m")


@dataclass
class Scenario:
    name: str
    triggers: re.Pattern
    tool_calls: list[tuple[str, Callable[[], dict] | dict]] = field(default_factory=list)
    reply: str = ""                          # used when there are no tool calls
    intro: str = "Here is what I found:"


REFUSAL = ("I can't move money, make payments or transfer funds - that is outside what this copilot is "
           "allowed to do at any permission tier. I can prepare a draft (for example a payment reminder or "
           "a bill summary) for you to act on yourself.")

SCENARIOS: list[Scenario] = [
    Scenario("money_movement", re.compile(
        r"(^\s*|\b(please|can\s+you|could\s+you|go\s+ahead\s+and|now)\s+)(pay|transfer|send\s+money|wire|withdraw|disburse)\b"
        r"|\bsend\s+money\b|\bmake\s+(a\s+)?payment\b", re.I),
             reply=REFUSAL),
    Scenario("cash_truth", re.compile(r"cash\s*truth|reconcil|\bgap\b|books?\s+(vs|versus)|missing", re.I),
             [("cash_truth", {})], intro="Books versus real cash:"),
    Scenario("pnl", re.compile(r"profit|p&l|\bpnl\b|margin|\bloss\b", re.I),
             [("monthly_pnl", lambda: {"month": _prev_month()})], intro="Last month's results:"),
    Scenario("forecast", re.compile(r"forecast|runway|next\s+(few\s+)?(weeks?|months?)|predict|will\s+i", re.I),
             [("cashflow_forecast", {"horizon": 13, "granularity": "week"})], intro="Cash outlook:"),
    Scenario("financing", re.compile(r"loan|financ|credit|borrow", re.I),
             [("financing_matches", {})], intro="Financing options:"),
    Scenario("alerts", re.compile(r"alert|late|overdue|\bdue\b|warning", re.I),
             [("alerts", {})], intro="Things that need attention:"),
    Scenario("search", re.compile(r"\b(find|search|transactions?|statement|show\s+me)\b", re.I),
             [("search_transactions", {"source": "statements", "limit": 10})], intro="Recent statement lines:"),
    Scenario("cash_position", re.compile(r"cash|balance|money|bank|wallet", re.I),
             [("account_balances", {"subtypes": ["cash", "bank", "wallet"]})], intro="Your cash position:"),
]
DEFAULT_SCENARIO = Scenario("overview", re.compile(r".*"),
                            [("account_balances", {"subtypes": ["cash", "bank", "wallet"]}), ("alerts", {})],
                            intro="Quick overview:")


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, model: str = "mock-1", script: list[LLMResponse | dict] | None = None,
                 scenarios: list[Scenario] | None = None, max_refs_per_tool: int = 5):
        super().__init__(model or "mock-1")
        self.script = list(script) if script is not None else None
        self.scenarios = scenarios if scenarios is not None else SCENARIOS
        self.max_refs_per_tool = max_refs_per_tool
        self.calls: list[list[Message]] = []          # what the provider was sent (tests inspect this)

    # ------------------------------------------------------------------
    def complete(self, messages: list[Message], tools: list[ToolSchema], *, max_tokens: int = 1024) -> LLMResponse:
        self.calls.append(list(messages))
        usage = Usage(input_tokens=sum(len(m.content) for m in messages) // 4, output_tokens=0)
        if self.script is not None:
            if not self.script:
                return self._resp("(mock script exhausted)", [], usage)
            item = self.script.pop(0)
            if isinstance(item, LLMResponse):
                return item
            calls = [ToolCall(id=c.get("id", f"mock-call-{i}"), name=c["name"], arguments=c.get("arguments", {}))
                     for i, c in enumerate(item.get("tool_calls", []))]
            return self._resp(item.get("text"), calls, usage)

        last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
        user_text = messages[last_user].content if last_user >= 0 else ""
        scenario = next((s for s in self.scenarios if s.triggers.search(user_text)), DEFAULT_SCENARIO)
        # Like a well-behaved model, obey the server's policy note for this turn.
        if any(m.role == "system" and m.content.startswith(MONEY_POLICY_NOTE[:60]) for m in messages):
            scenario = next(s for s in SCENARIOS if s.name == "money_movement")
        tool_msgs = [m for m in messages[last_user + 1:] if m.role == "tool"]
        available = {t.name for t in tools}

        if not tool_msgs:
            calls = []
            for i, (name, args) in enumerate(scenario.tool_calls):
                if name in available:
                    calls.append(ToolCall(id=f"mock-call-{len(messages)}-{i}", name=name,
                                          arguments=args() if callable(args) else dict(args)))
            if calls:
                return self._resp(None, calls, usage)
            return self._resp(scenario.reply or "I don't have a tool for that yet.", [], usage)
        return self._resp(self._compose(scenario, tool_msgs), [], usage)

    def _compose(self, scenario: Scenario, tool_msgs: list[Message]) -> str:
        lines = [scenario.intro]
        guard_hit = False
        for m in tool_msgs:
            guard_hit = guard_hit or "[guard]" in m.content
            try:
                payload: dict[str, Any] = json.loads(unwrap_untrusted(m.content))
            except (json.JSONDecodeError, ValueError):
                lines.append(f"- {m.name}: (unreadable tool output)")
                continue
            status = payload.get("status")
            if status == "executed" and payload.get("ok"):
                refs = payload.get("evidence", [])[: self.max_refs_per_tool]
                cite = " " + " ".join(f"[{r}]" for r in refs) if refs else ""
                lines.append(f"- {payload.get('summary') or m.name}{cite}")
            elif status == "queued_for_approval":
                lines.append(f"- I prepared this as a draft. It is waiting for your approval "
                             f"(approval {payload.get('approval_id')}); nothing has been written yet.")
            elif status == "denied":
                lines.append(f"- I'm not allowed to do that at your current permission level: {payload.get('error')}")
            else:
                lines.append(f"- {m.name} is not available right now: {payload.get('error') or 'unknown error'}")
        if guard_hit:
            lines.append("Note: some imported rows contain text that looks like instructions. "
                         "I treated it as data and did not act on it - you may want to review those rows.")
        return "\n".join(lines)

    def _resp(self, text: str | None, calls: list[ToolCall], usage: Usage) -> LLMResponse:
        usage.output_tokens = len(text or "") // 4 + 10 * len(calls)
        return LLMResponse(text=text, tool_calls=calls, usage=usage, provider=self.name, model=self.model,
                           stop_reason="tool_use" if calls else "end_turn")
