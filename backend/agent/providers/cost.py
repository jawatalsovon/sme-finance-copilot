"""Cost logging and hard USD caps, enforced BEFORE every LLM call.

GuardedProvider wraps any provider:
  1. estimate worst-case cost = (estimated input tokens * input price
                                 + max_tokens * output price) / 1e6
  2. refuse (BudgetExceeded) if estimate > per-request cap, or
     spent today (UTC, from llm_usage) + estimate > daily cap
  3. call the provider, price the actual usage, write llm_usage + audit row.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from backend.agent.providers.base import LLMProvider, LLMResponse, Message, ToolSchema
from backend.core.ids import new_id
from backend.core.util import utc_now, utc_now_iso
from backend.ledger.db import Database
from backend.security.audit import AuditLog

DEFAULT_PRICING_FILE = Path(__file__).resolve().parent / "pricing.json"


class BudgetExceeded(RuntimeError):
    def __init__(self, message: str, *, estimate: float, spent_today: float, cap: float):
        super().__init__(message)
        self.estimate = estimate
        self.spent_today = spent_today
        self.cap = cap


@dataclass
class Price:
    input_per_mtok: float
    output_per_mtok: float


class PricingTable:
    def __init__(self, table: dict):
        self.table = table

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PricingTable":
        p = Path(path) if path else DEFAULT_PRICING_FILE
        return cls(json.loads(p.read_text(encoding="utf-8")))

    def price(self, provider: str, model: str) -> Price:
        entry = self.table.get(provider, {}).get(model)
        if entry is None:
            # Longest prefix match (e.g. dated model snapshots), else the conservative default.
            candidates = [m for m in self.table.get(provider, {}) if model.startswith(m)]
            entry = self.table[provider][max(candidates, key=len)] if candidates else self.table["_default"]
        return Price(float(entry["input_per_mtok"]), float(entry["output_per_mtok"]))

    def cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.price(provider, model)
        return (input_tokens * p.input_per_mtok + output_tokens * p.output_per_mtok) / 1_000_000


def estimate_input_tokens(messages: list[Message], tools: list[ToolSchema]) -> int:
    """Rough upper estimate: ~3.5 characters per token, plus per-message overhead."""
    chars = sum(len(m.content or "") + sum(len(json.dumps(tc.arguments)) for tc in m.tool_calls) for m in messages)
    chars += sum(len(t.name) + len(t.description) + len(json.dumps(t.parameters)) for t in tools)
    return int(chars / 3.5) + 8 * len(messages) + 50


class CostGuard:
    def __init__(self, db: Database, audit: AuditLog, pricing: PricingTable,
                 daily_cap_usd: float, request_cap_usd: float):
        self.db = db
        self.audit = audit
        self.pricing = pricing
        self.daily_cap_usd = daily_cap_usd
        self.request_cap_usd = request_cap_usd

    def spent_today(self, day: dt.date | None = None) -> float:
        day = day or utc_now().date()
        return float(self.db.scalar("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE day = ?", [day]) or 0.0)

    def check(self, provider: str, model: str, messages: list[Message], tools: list[ToolSchema],
              max_tokens: int, company_id: str | None = None) -> float:
        est_in = estimate_input_tokens(messages, tools)
        estimate = self.pricing.cost(provider, model, est_in, max_tokens)
        spent = self.spent_today()
        reason = None
        if estimate > self.request_cap_usd:
            reason = f"estimated cost ${estimate:.4f} exceeds the per-request cap ${self.request_cap_usd:.4f}"
        elif spent + estimate > self.daily_cap_usd:
            reason = (f"daily LLM budget reached: spent ${spent:.4f} + estimated ${estimate:.4f} "
                      f"> cap ${self.daily_cap_usd:.4f}")
        if reason:
            self.audit.append("llm_blocked_budget", actor="system", company_id=company_id,
                              payload={"provider": provider, "model": model, "estimate_usd": round(estimate, 6),
                                       "spent_today_usd": round(spent, 6), "daily_cap_usd": self.daily_cap_usd,
                                       "request_cap_usd": self.request_cap_usd})
            raise BudgetExceeded(reason, estimate=estimate, spent_today=spent, cap=self.daily_cap_usd)
        return estimate

    def record(self, resp: LLMResponse, company_id: str | None, session_id: str | None) -> None:
        resp.usage.cost_usd = self.pricing.cost(resp.provider, resp.model, resp.usage.input_tokens,
                                                resp.usage.output_tokens)
        now = utc_now()
        self.db.execute(
            "INSERT INTO llm_usage (id, ts, day, provider, model, input_tokens, output_tokens, cost_usd, "
            "company_id, session_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [new_id("llm"), utc_now_iso(), now.date(), resp.provider, resp.model, resp.usage.input_tokens,
             resp.usage.output_tokens, resp.usage.cost_usd, company_id, session_id],
        )
        self.audit.append("llm_call", actor="agent", company_id=company_id,
                          payload={"provider": resp.provider, "model": resp.model,
                                   "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens,
                                   "cost_usd": round(resp.usage.cost_usd, 6), "tool_calls": [t.name for t in resp.tool_calls],
                                   "session_id": session_id})

    def status(self) -> dict:
        spent = self.spent_today()
        return {"day": utc_now().date().isoformat(), "spent_usd": round(spent, 6),
                "daily_cap_usd": self.daily_cap_usd, "request_cap_usd": self.request_cap_usd,
                "remaining_usd": round(max(self.daily_cap_usd - spent, 0.0), 6)}


class GuardedProvider:
    """The only way the agent talks to a provider."""

    def __init__(self, inner: LLMProvider, guard: CostGuard):
        self.inner = inner
        self.guard = guard

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    def complete(self, messages: list[Message], tools: list[ToolSchema], *, max_tokens: int,
                 company_id: str | None = None, session_id: str | None = None) -> LLMResponse:
        self.guard.check(self.inner.name, self.inner.model, messages, tools, max_tokens, company_id)
        resp = self.inner.complete(messages, tools, max_tokens=max_tokens)
        self.guard.record(resp, company_id, session_id)
        return resp
