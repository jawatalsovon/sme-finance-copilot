"""What-if scenarios: re-run the forecast with changes applied to the future cash flows.

Each change is applied to the forecast money in / money out, using shares measured from the last
12 months (for example: cost of goods is X% of money out, customer Y paid Z% of money in):
  revenue_pct / price_pct   money in x (1 + v%)
  cogs_pct                  money out + (COGS share of money out) x v%      e.g. raw material +15%
  opex_pct                  money out + (running-cost share of money out) x v%
  fx_pct                    money in from foreign-currency sales x (1 + v%)  e.g. USD -5% vs base
  lose_customer             money in - that customer's share (default: the biggest customer)
  delay_receivables_days    invoiced (credit) receipts arrive v days later
  new_loan                  + v in the first period, then monthly repayments (12 months, country rate)
  one_off_expense           + v money out in the period containing `start` (default: first period)
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from backend.core.util import format_money
from backend.engine.data import Ledger, dec
from backend.engine.financing import DEFAULT_APR, annuity
from backend.engine.interfaces import EngineError
from backend.engine.types import CashflowForecast, Figure, ScenarioChange


def shares(L: Ledger, monthly: pd.DataFrame, as_of: dt.date) -> dict:
    ttm = monthly.tail(12)
    cin, cout = float(ttm["cash_in"].sum()), float(ttm["cash_out"].sum())
    cogs = float(ttm["cogs"].sum())
    opex = float(ttm["operating_expenses"].sum())
    lines = L.lines_upto(as_of)
    lines = lines[lines["entry_date"] > as_of - dt.timedelta(days=365)]
    rl = lines[lines["acc_subtype"] == "revenue"]
    rev = -float(rl["base_c"].sum())
    fx_rev = -float(rl[rl["currency"] != L.base]["base_c"].sum())
    inv = L.invoices
    cust = {}
    b2b = 0.0
    if len(inv):
        w = inv[(inv["issue_date"] > as_of - dt.timedelta(days=365)) & (inv["issue_date"] <= as_of)]
        tot = w.groupby(["counterparty_id", "customer_name"])["total_base"].apply(lambda s: pd.to_numeric(s).sum() * 100)
        for (cid, name), v in tot.sort_values(ascending=False).items():
            cust[cid] = (name, float(v))
        b2b = float(sum(v for _, v in cust.values()))
    return {"cogs_out": min(1.0, cogs / cout) if cout else 0.0, "opex_out": min(1.0, opex / cout) if cout else 0.0,
            "fx_in": min(1.0, fx_rev / rev) if rev else 0.0, "cust": cust, "cash_in": cin,
            "credit_in": min(1.0, b2b / cin) if cin else 0.0}


def make_adjust(L: Ledger, changes: list[ScenarioChange], sh: dict, granularity: str) -> tuple:
    notes: list[str] = []
    per_month = 1.0 if granularity == "month" else 12 / 52

    def adjust(pts: pd.DataFrame) -> pd.DataFrame:
        pts = pts.copy()
        n = len(pts)
        starts = [pd.Timestamp(s).date() for s in pts["start"]]

        length = dt.timedelta(days=7 if granularity == "week" else 31)

        def active(ch: ScenarioChange) -> np.ndarray:
            """1 for periods that end on or after the change's start date (all periods if no start)."""
            if ch.start is None:
                return np.ones(n)
            return np.array([1.0 if s + length > ch.start else 0.0 for s in starts])

        base_in = pts["inflow"].to_numpy().copy()
        base_out = pts["outflow"].to_numpy().copy()
        for ch in changes:
            a = active(ch)
            v = ch.value
            if ch.kind in ("revenue_pct", "price_pct"):
                pts["inflow"] += base_in * v / 100 * a
            elif ch.kind == "cogs_pct":
                pts["outflow"] += base_out * sh["cogs_out"] * v / 100 * a
            elif ch.kind == "opex_pct":
                pts["outflow"] += base_out * sh["opex_out"] * v / 100 * a
            elif ch.kind == "fx_pct":
                pts["inflow"] += base_in * sh["fx_in"] * v / 100 * a
            elif ch.kind == "lose_customer":
                if not sh["cust"]:
                    raise EngineError("no invoiced customers found, so 'lose a customer' cannot be measured")
                cid = ch.target if ch.target in sh["cust"] else next(iter(sh["cust"]))
                name, val = sh["cust"][cid]
                share = val / sh["cash_in"] if sh["cash_in"] else 0.0
                pts["inflow"] -= base_in * share * a
                notes.append(f"Lost customer: {name} ({share:.1%} of money in over the last 12 months).")
            elif ch.kind == "delay_receivables_days":
                periods = v / (7 if granularity == "week" else 30.4)
                credit = base_in * sh["credit_in"]
                shifted = np.interp(np.arange(n) - periods, np.arange(n), credit, left=0.0)
                pts["inflow"] += (shifted - credit) * a
            elif ch.kind == "new_loan":
                apr = DEFAULT_APR.get(L.country, 12.0)
                pay = annuity(v, apr, 12)
                first = int(np.argmax(a)) if a.any() else 0
                pts.loc[pts.index[first], "inflow"] += v
                pts["outflow"] += np.where(np.arange(n) > first, pay * per_month, 0.0)
                notes.append(f"New loan {format_money(dec(round(v * 100)), L.base)}: about "
                             f"{format_money(dec(round(pay * 100)), L.base)} a month over 12 months at {apr:.1f}%.")
            elif ch.kind == "one_off_expense":
                first = int(np.argmax(a)) if a.any() else 0
                pts.loc[pts.index[first], "outflow"] += v
            else:
                raise EngineError(f"unknown scenario change: {ch.kind}")
        pts["inflow"] = pts["inflow"].clip(lower=0)
        pts["outflow"] = pts["outflow"].clip(lower=0)
        return pts

    return adjust, notes


def describe(ch: ScenarioChange, cur: str) -> str:
    v = ch.value
    return {
        "revenue_pct": f"sales {v:+.0f}%", "price_pct": f"prices {v:+.0f}%", "cogs_pct": f"cost of goods {v:+.0f}%",
        "opex_pct": f"running costs {v:+.0f}%", "fx_pct": f"foreign-currency receipts {v:+.0f}% in {cur}",
        "lose_customer": "lose the biggest customer" if not ch.target else f"lose customer {ch.target}",
        "delay_receivables_days": f"customers pay {v:.0f} days later",
        "new_loan": f"new loan of {format_money(dec(round(v * 100)), cur)}",
        "one_off_expense": f"one-off payment of {format_money(dec(round(v * 100)), cur)}",
    }.get(ch.kind, ch.kind)


def deltas(base: CashflowForecast, scen: CashflowForecast, cur: str) -> tuple[list[Figure], str]:
    b_end, s_end = float(base.points[-1].closing_cash), float(scen.points[-1].closing_cash)
    b_min = min(float(p.closing_cash) for p in base.points)
    s_min = min(float(p.closing_cash) for p in scen.points)

    def first_neg(fc):
        for i, p in enumerate(fc.points, start=1):
            if float(p.closing_cash) < 0:
                return i
        return None

    bn, sn = first_neg(base), first_neg(scen)
    unit = "week" if base.granularity == "week" else "month"
    figs = [
        Figure(key="closing_cash_delta", label=f"Change in cash at the end of the {base.horizon} {unit}s",
               value=dec(round((s_end - b_end) * 100)), currency=cur, formula="scenario closing cash - baseline closing cash"),
        Figure(key="lowest_cash_delta", label="Change in the lowest cash point", value=dec(round((s_min - b_min) * 100)),
               currency=cur, formula="scenario minimum - baseline minimum"),
        Figure(key="scenario_closing_cash", label="Cash at the end (scenario)", value=dec(round(s_end * 100)), currency=cur),
        Figure(key="scenario_lowest_cash", label="Lowest cash point (scenario)", value=dec(round(s_min * 100)), currency=cur),
    ]
    runway = (f"Cash stays positive for all {base.horizon} {unit}s in both cases." if bn is None and sn is None else
              f"Cash goes below zero in {unit} {sn} (baseline: {'never' if bn is None else unit + ' ' + str(bn)}).")
    return figs, runway
