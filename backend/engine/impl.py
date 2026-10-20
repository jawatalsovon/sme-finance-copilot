"""M1's finance engine: the deterministic brain behind every number the copilot shows.

Picked up automatically by backend.engine.interfaces.get_engine(). Read-only; deterministic;
every figure carries evidence refs. Algorithms are described in backend/engine/README.md.
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Literal

import pandas as pd

from backend.core.util import format_money
from backend.engine import alerts as alerts_mod
from backend.engine import brief as brief_mod
from backend.engine import financing as fin_mod
from backend.engine import forecast as fc_mod
from backend.engine import pnl as pnl_mod
from backend.engine import scenario as sc_mod
from backend.engine.cash_truth import Raw, run_cash_truth
from backend.engine.data import Ledger, dec, month_bounds
from backend.engine.interfaces import BaseEngine, EngineError
from backend.engine.types import (
    AlertList, CashflowForecast, CashTruthResult, FinancingMatches, KPISet, MonthlyPnL, OwnerBrief, ScenarioChange,
    ScenarioResult,
)

# The three supported what-if presets (plan revision 2). `value` may be changed within the bounds.
SCENARIO_PRESETS = {
    "input_cost_up": {"kind": "cogs_pct", "value": 15, "bounds": (-50, 100),
                      "label": "Input (raw material / goods) cost +15%"},
    "lose_biggest_customer": {"kind": "lose_customer", "value": 0, "bounds": (0, 0),
                              "label": "Lose the biggest customer"},
    "fx_move": {"kind": "fx_pct", "value": -10, "bounds": (-30, 30),
                "label": "Exchange-rate move on foreign-currency receipts (+/-10%)"},
}
ALLOWED_SCENARIO_KINDS = {v["kind"]: k for k, v in SCENARIO_PRESETS.items()}
from backend.ledger.db import Database

_CACHE_LOCK = threading.RLock()
_CACHE: dict[tuple, dict] = {}   # (db id, company, data signature) -> memo dict


class Engine(BaseEngine):
    def __init__(self, db: Database):
        super().__init__(db)

    # ------------------------------------------------------------------ plumbing
    def _signature(self, company_id: str) -> tuple:
        row = self.db.query_one(
            """SELECT (SELECT COUNT(*) FROM journal_entries WHERE company_id = ?) AS je,
                      (SELECT COUNT(*) FROM journal_entries WHERE company_id = ? AND status = 'posted') AS jp,
                      (SELECT COUNT(*) FROM journal_lines WHERE company_id = ?) AS jl,
                      (SELECT COUNT(*) FROM external_statement_lines WHERE company_id = ?) AS st,
                      (SELECT COUNT(*) FROM invoices WHERE company_id = ?) AS inv,
                      (SELECT COUNT(*) FROM invoice_payments WHERE company_id = ?) AS ip,
                      (SELECT COUNT(*) FROM bills WHERE company_id = ?) AS bi,
                      (SELECT MAX(finished_at) FROM ingest_batches WHERE company_id = ?) AS last""",
            [company_id] * 8)
        return tuple(row.values()) if row else ()

    def _memo(self, company_id: str) -> dict:
        key = (id(self.db), company_id)
        sig = self._signature(company_id)
        with _CACHE_LOCK:
            m = _CACHE.get(key)
            if m is None or m["sig"] != sig:
                m = {"sig": sig, "ledger": Ledger(self.db, company_id), "lock": threading.RLock()}
                _CACHE[key] = m
            return m

    def _ledger(self, company_id: str) -> Ledger:
        return self._memo(company_id)["ledger"]

    def _as_of(self, L: Ledger, as_of: dt.date | None) -> dt.date:
        last = L.last_date
        if last is None:
            raise EngineError(f"no transactions loaded for {L.company_id}")
        if as_of is None:
            return last
        if isinstance(as_of, str):
            as_of = dt.date.fromisoformat(as_of)
        if L.first_date and as_of < L.first_date:
            raise EngineError(f"{as_of} is before the first transaction ({L.first_date})")
        return min(as_of, last)

    def _cached(self, company_id: str, key: tuple, fn):
        m = self._memo(company_id)
        with m["lock"]:
            if key not in m:
                m[key] = fn()
            return m[key]

    def _ct(self, company_id: str, as_of: dt.date) -> tuple[CashTruthResult, list[Raw]]:
        L = self._ledger(company_id)
        return self._cached(company_id, ("ct", as_of), lambda: run_cash_truth(L, as_of))

    def _corrections(self, company_id: str) -> pd.DataFrame:
        """All Cash Truth drafts at the latest data date, as journal-line rows (the corrected basis)."""
        L = self._ledger(company_id)
        last = self._as_of(L, None)

        def build():
            ct, _ = self._ct(company_id, last)
            return pnl_mod.draft_lines_frame(L, ct.proposed_entries)
        return self._cached(company_id, ("corr",), build)

    def _monthly(self, company_id: str, as_of: dt.date, basis: str = "corrected") -> pd.DataFrame:
        L = self._ledger(company_id)
        return self._cached(company_id, ("monthly", basis, as_of),
                            lambda: pnl_mod.monthly_table(L, self._corrections(company_id), basis, as_of))

    # ------------------------------------------------------------------ contract
    def cash_truth(self, company_id: str, as_of: dt.date) -> CashTruthResult:
        L = self._ledger(company_id)
        return self._ct(company_id, self._as_of(L, as_of))[0]

    def monthly_pnl(self, company_id: str, month: str, basis: Literal["books", "corrected"] = "books") -> MonthlyPnL:
        if basis not in ("books", "corrected"):
            raise EngineError("basis must be 'books' or 'corrected'")
        L = self._ledger(company_id)
        month_bounds(month)
        corr = self._corrections(company_id) if basis == "corrected" else None
        return self._cached(company_id, ("pnl", month, basis),
                            lambda: pnl_mod.PnLBuilder(L, corr).monthly_pnl(month, basis))

    def kpis(self, company_id: str, as_of: dt.date) -> KPISet:
        L = self._ledger(company_id)
        a = self._as_of(L, as_of)
        return self._cached(company_id, ("kpis", a), lambda: pnl_mod.kpis(L, self._ct(company_id, a)[0],
                                                                          self._corrections(company_id), a))

    def _forecast(self, company_id: str, horizon: int, granularity: str, as_of: dt.date, adjust=None):
        L = self._ledger(company_id)
        ct = self._ct(company_id, as_of)[0]
        opening = ct.verified_cash.model_copy(update={"key": "opening_cash", "label": "Opening cash (verified)"})
        return fc_mod.cashflow_forecast(L, self._corrections(company_id), opening, as_of, horizon, granularity, adjust)

    def cashflow_forecast(self, company_id: str, horizon: int = 13, granularity: Literal["week", "month"] = "week",
                          as_of: dt.date | None = None) -> CashflowForecast:
        L = self._ledger(company_id)
        a = self._as_of(L, as_of)
        return self._cached(company_id, ("fc", horizon, granularity, a),
                            lambda: self._forecast(company_id, horizon, granularity, a)[0])

    def alerts(self, company_id: str, as_of: dt.date) -> AlertList:
        L = self._ledger(company_id)
        a = self._as_of(L, as_of)

        def build():
            ct = self._ct(company_id, a)[0]
            try:
                fc = self.cashflow_forecast(company_id, 13, "week", a)
            except EngineError:            # too little history to forecast: alerts without the cash outlook
                fc = None
            raws = self._ct(company_id, a)[1]
            found: dict[str, float] = {}
            for r in raws:
                credit = getattr(r, "ar_credit", None)
                if credit and credit[1]:
                    found[credit[1]] = found.get(credit[1], 0.0) + r.impact_c / 100 * (L.fx_rate(r.currency, r.date) or 1.0)
            try:
                kp = self.kpis(company_id, a)
            except EngineError:
                kp = None
            return alerts_mod.alerts(L, a, ct, fc, self._monthly(company_id, a), kp, found)
        return self._cached(company_id, ("alerts", a), build)

    def financing_matches(self, company_id: str, as_of: dt.date | None = None) -> FinancingMatches:
        L = self._ledger(company_id)
        a = self._as_of(L, as_of)

        def build():
            try:
                fc = self.cashflow_forecast(company_id, 12, "month", a)
            except EngineError:            # too little history: eligibility only, affordability from history
                fc = None
            monthly = self._monthly(company_id, a)
            full = monthly[monthly.index <= pnl_mod._last_full_month(a)]
            debt = self._existing_debt_service(L, a)
            return fin_mod.financing_matches(L, full, fc, a, debt)
        return self._cached(company_id, ("fin", a), build)

    def _existing_debt_service(self, L: Ledger, as_of: dt.date) -> float:
        """Average monthly debt service over the last 12 months (base units): interest paid plus the net
        reduction of loan balances. A revolving line that is repaid and drawn again within the year
        (draws = repayments) therefore counts only its interest."""
        all_lines = L.lines_upto(as_of)
        outstanding = -float(all_lines[all_lines["acc_subtype"] == "loan"]["base_c"].sum())
        if outstanding <= 0:          # no loan left on the as-of date: nothing to service going forward
            return 0.0
        lines = all_lines[all_lines["entry_date"] > as_of - dt.timedelta(days=365)]
        loan = lines[lines["acc_subtype"] == "loan"]["base_c"]
        repaid, drawn = float(loan[loan > 0].sum()), float(-loan[loan < 0].sum())
        interest = float(lines[lines["acc_subtype"] == "interest"]["base_c"].sum())
        return (interest + min(max(0.0, repaid - drawn), outstanding)) / 100 / 12

    def scenario(self, company_id: str, changes: list[ScenarioChange], as_of: dt.date | None = None) -> ScenarioResult:
        if not changes:
            raise EngineError("give at least one change, e.g. {'kind': 'cogs_pct', 'value': 15}")
        L = self._ledger(company_id)
        a = self._as_of(L, as_of)
        changes = [c if isinstance(c, ScenarioChange) else ScenarioChange(**c) for c in changes]
        for c in changes:
            if c.kind not in ALLOWED_SCENARIO_KINDS:
                raise EngineError("supported scenarios: input cost change (cogs_pct, e.g. 15), lose the biggest "
                                  "customer (lose_customer) and an FX move (fx_pct, e.g. -10 or 10)")
            lo, hi = SCENARIO_PRESETS[ALLOWED_SCENARIO_KINDS[c.kind]]["bounds"]
            if c.kind != "lose_customer" and not lo <= c.value <= hi:
                raise EngineError(f"{c.kind} must be between {lo} and {hi}")
        base = self.cashflow_forecast(company_id, 13, "week", a)
        monthly = self._monthly(company_id, a)
        full = monthly[monthly.index <= pnl_mod._last_full_month(a)]
        sh = sc_mod.shares(L, full, a)
        adjust, notes = sc_mod.make_adjust(L, changes, sh, "week")
        scen, _ = self._forecast(company_id, 13, "week", a, adjust)
        cur = L.base
        what = "; ".join(sc_mod.describe(c, cur) for c in changes)
        scen = scen.model_copy(update={"assumptions": scen.assumptions + [f"Scenario: {what}."] + notes,
                                       "summary": f"Scenario ({what}): " + scen.summary})
        figs, runway = sc_mod.deltas(base, scen, cur)
        expl = (f"Scenario: {what}. Shares used (last 12 months): cost of goods {sh['cogs_out']:.0%} of money out, "
                f"running costs {sh['opex_out']:.0%}, foreign-currency sales {sh['fx_in']:.0%} of revenue, invoiced "
                f"(credit) receipts {sh['credit_in']:.0%} of money in. " + " ".join(notes))
        summary = (f"With {what}, cash after 13 weeks would be {format_money(figs[2].value, cur)} instead of "
                   f"{format_money(base.points[-1].closing_cash, cur)} ({format_money(figs[0].value, cur)}); "
                   f"lowest point {format_money(figs[3].value, cur)}. {runway}")
        return ScenarioResult(company_id=company_id, changes=changes, baseline=base, scenario=scen, deltas=figs,
                              explanation=expl, summary=summary)

    def owner_brief(self, company_id: str, as_of: dt.date | None = None) -> OwnerBrief:
        L = self._ledger(company_id)
        a = self._as_of(L, as_of)

        def build():
            ct = self._ct(company_id, a)[0]
            al = self.alerts(company_id, a)
            fc = self.cashflow_forecast(company_id, 13, "week", a)
            try:
                fin = self.financing_matches(company_id, a)
            except EngineError:
                fin = None
            return brief_mod.owner_brief(company_id, a, L.base, ct, al, fc, fin, self.kpis(company_id, a))
        return self._cached(company_id, ("brief", a), build)

    @staticmethod
    def scenario_presets() -> dict:
        return {k: {"kind": v["kind"], "value": v["value"], "label": v["label"]} for k, v in SCENARIO_PRESETS.items()}
