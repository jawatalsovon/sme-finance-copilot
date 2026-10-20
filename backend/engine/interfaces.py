"""Finance engine interface (drafted by A1, owned by M1 from wave 3).

How M1 plugs in
---------------
Create `backend/engine/impl.py` with a class `Engine` that implements `FinanceEngine`
(subclass `BaseEngine` to inherit the constructor and default stubs). Nothing else
needs to change: `get_engine(db)` imports it automatically, the API report endpoints
and the agent tools start returning real results instead of HTTP 501.

Rules
  * Deterministic: same database + same arguments -> same result. No LLM calls here.
  * Every number is traceable: fill `evidence` with "table:id" refs (backend.ledger.lineage).
  * Read-only: the engine never writes to the ledger. Corrections are returned as
    `DraftJournalEntry` proposals; the agent's T2 tool writes them after owner approval.
  * Use base-currency amounts (`amount_base`, `total_base`) unless a field says otherwise.
  * Raise `EngineError` with a plain message for bad input (unknown month, no data).
"""
from __future__ import annotations

import datetime as dt
import importlib
from typing import Literal, Protocol, runtime_checkable

from backend.engine.types import (
    AlertList,
    CashflowForecast,
    CashTruthResult,
    FinancingMatches,
    KPISet,
    MonthlyPnL,
    OwnerBrief,
    ScenarioChange,
    ScenarioResult,
)
from backend.ledger.db import Database


class EngineError(ValueError):
    """Bad input or missing data; message is shown to the owner."""


class EngineNotImplemented(NotImplementedError):
    """The function exists in the contract but M1 has not implemented it yet (API -> 501)."""


@runtime_checkable
class FinanceEngine(Protocol):
    db: Database

    def cash_truth(self, company_id: str, as_of: dt.date) -> CashTruthResult:
        """Books vs. reality.

        Compare book cash (posted journal lines on accounts with subtype cash/bank/wallet)
        with verified cash (external_statement_lines balances / cash counts) up to `as_of`.
        Returns the gap (verified - book), per-channel balances, and findings - each with
        evidence row ids and, where a correction is warranted, proposed draft entries.
        Must detect at least: unrecorded expenses, unrecorded receipts, fees not recorded,
        duplicates, stale receivables (> 90 days past due), timing differences.
        """
        ...

    def monthly_pnl(self, company_id: str, month: str, basis: Literal["books", "corrected"] = "books") -> MonthlyPnL:
        """One-page P&L for `month` ("YYYY-MM"): revenue, COGS, gross margin, opex,
        operating and net profit, cash in/out. basis="corrected" applies the Cash Truth
        proposals as if approved (never writes them)."""
        ...

    def kpis(self, company_id: str, as_of: dt.date) -> KPISet:
        """Plain-language KPIs: at least runway_months, gross_margin_pct, dso_days,
        dpo_days, cash_conversion, revenue_growth_pct, top_customer_share_pct."""
        ...

    def cashflow_forecast(
        self,
        company_id: str,
        horizon: int = 13,
        granularity: Literal["week", "month"] = "week",
        as_of: dt.date | None = None,
    ) -> CashflowForecast:
        """Cash forecast from the corrected position: 13 weeks or 12 months, with
        prediction intervals and a rolling-origin backtest error (BacktestResult)."""
        ...

    def alerts(self, company_id: str, as_of: dt.date) -> AlertList:
        """Actionable alerts: cash below 1 month of costs in week N, customer X days late,
        supplier bill due, unusual expense, large Cash Truth gap."""
        ...

    def financing_matches(self, company_id: str, as_of: dt.date | None = None) -> FinancingMatches:
        """Match the company against the product catalogue for its country: eligibility
        checks with reasons + affordability (DSCR from the forecast). Never applies."""
        ...

    def scenario(self, company_id: str, changes: list[ScenarioChange], as_of: dt.date | None = None) -> ScenarioResult:
        """What-if: re-run the forecast with `changes` and return baseline vs. scenario.
        Supported presets: input cost +15% (cogs_pct), lose the biggest customer (lose_customer),
        FX move of foreign-currency receipts (fx_pct, e.g. -10 or +10)."""
        ...

    def owner_brief(self, company_id: str, as_of: dt.date | None = None) -> OwnerBrief:
        """Weekly Owner Brief produced without being asked: Cash Truth gap, top 3 alerts and
        recommended actions, each linked to its draft entry (queued for approval by the agent)."""
        ...


class BaseEngine:
    """Constructor + stubs. M1 subclasses this in backend/engine/impl.py."""

    def __init__(self, db: Database):
        self.db = db

    def _todo(self, name: str):
        raise EngineNotImplemented(f"engine.{name} is not implemented yet (owner: M1)")

    def cash_truth(self, company_id, as_of):
        self._todo("cash_truth")

    def monthly_pnl(self, company_id, month, basis="books"):
        self._todo("monthly_pnl")

    def kpis(self, company_id, as_of):
        self._todo("kpis")

    def cashflow_forecast(self, company_id, horizon=13, granularity="week", as_of=None):
        self._todo("cashflow_forecast")

    def alerts(self, company_id, as_of):
        self._todo("alerts")

    def financing_matches(self, company_id, as_of=None):
        self._todo("financing_matches")

    def scenario(self, company_id, changes, as_of=None):
        self._todo("scenario")

    def owner_brief(self, company_id, as_of=None):
        self._todo("owner_brief")


IMPL_MODULE = "backend.engine.impl"


def get_engine(db: Database) -> FinanceEngine:
    """Return M1's Engine if backend/engine/impl.py exists, else the stub engine."""
    try:
        mod = importlib.import_module(IMPL_MODULE)
    except ModuleNotFoundError as e:
        if e.name != IMPL_MODULE:
            raise  # impl exists but one of its imports is missing: surface it
        return BaseEngine(db)  # type: ignore[return-value]
    return mod.Engine(db)
