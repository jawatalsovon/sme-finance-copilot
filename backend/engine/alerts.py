"""Alerts: things the owner should act on, each with severity, a plain message and evidence.

  low_cash        the 13-week forecast drops below one month of running costs (warning) or below zero (critical)
  late_customer   a customer owes money past the due date (warning > 30 days, critical > 90 days; debts more
                  than a year overdue are left to Cash Truth's write-off findings)
  bill_due        a large supplier bill is overdue or due in the next 14 days
  margin_drop     last month's gross margin is well below the 12-month average
  unusual_expense an expense account spent far more last month than usual (mean + 3 standard deviations)
  cash_truth_gap  the books are further from verifiable cash than a materiality threshold
  concentration   one customer is a large share of sales
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from backend.core.util import format_money
from backend.engine.data import Ledger, dec
from backend.engine.types import Alert, AlertList, CashflowForecast, CashTruthResult, KPISet
from backend.ledger.lineage import make_ref

GAP_MATERIALITY_PCT = 0.5     # of 12-month revenue
MARGIN_DROP_PP = 3.0
BILL_WINDOW_DAYS = 14


def alerts(L: Ledger, as_of: dt.date, ct: CashTruthResult, fc: CashflowForecast | None, monthly: pd.DataFrame,
           kp: KPISet | None, found_payments: dict[str, float] | None = None) -> AlertList:
    """found_payments: customer name -> base amount of payments Cash Truth found on statements but the
    books never recorded (so the customer is not as late as the books say)."""
    found_payments = dict(found_payments or {})
    cur = L.base
    out: list[Alert] = []
    full = monthly[monthly.index <= _last_full_month(as_of)]
    ttm = full.tail(12)
    # one month of running costs (staff, rent, utilities, fees, interest; not stock purchases)
    avg_out = float((ttm["operating_expenses"] + ttm["below"]).tail(3).mean()) / 100 if len(ttm) else 0.0
    rev12 = float(ttm["revenue"].sum()) / 100 if len(ttm) else 0.0

    # --- low cash within the forecast horizon
    for i, p in enumerate(fc.points if fc else [], start=1):
        close, lower = float(p.closing_cash), float(p.closing_cash_lower)
        if close < 0 or close < avg_out:
            crit = close < 0
            out.append(Alert(
                id="", severity="critical" if crit else "warning", kind="low_cash",
                title=f"Cash {'runs out' if crit else 'falls below one month of running costs'} in week {i}",
                message=(f"The forecast puts cash at {format_money(p.closing_cash, cur)} in the week starting "
                         f"{p.period_start} (week {i} of {len(fc.points)}), "
                         + ("below zero." if crit else f"below one month of running costs ({format_money(dec(round(avg_out * 100)), cur)}).")
                         + f" 80% range: {format_money(p.closing_cash_lower, cur)} to {format_money(p.closing_cash_upper, cur)}."),
                due_date=p.period_start, amount=p.closing_cash, currency=cur, evidence=fc.evidence[:10],
                suggested_action="Delay non-urgent purchases, chase overdue customers, or look at the financing matches."))
            break
        if lower < 0:
            out.append(Alert(
                id="", severity="info", kind="low_cash", title=f"Cash could dip below zero by week {i} in a bad case",
                message=(f"The expected path stays positive, but the cautious end of the 80% range reaches "
                         f"{format_money(p.closing_cash_lower, cur)} in the week starting {p.period_start}."),
                due_date=p.period_start, amount=p.closing_cash_lower, currency=cur, evidence=fc.evidence[:10],
                suggested_action="Keep a buffer; avoid large one-off payments in this period."))
            break

    # --- late customers
    inv = L.invoices
    if len(inv):
        pays = L.invoice_payments
        pays = pays[pays["payment_date"] <= as_of]
        paid = pays.groupby("invoice_id")["amount_base"].sum()
        w = inv[inv["issue_date"] <= as_of].copy()
        w["out"] = pd.to_numeric(w["total_base"]) - w["id"].map(paid).fillna(0).astype(float)
        # apply payments found on statements to the customer's oldest invoices first
        w = w.sort_values(["issue_date", "id"])
        for i, r in w.iterrows():
            left = found_payments.get(r["customer_name"], 0.0)
            if left > 0 and r["out"] > 0:
                use = min(left, r["out"])
                w.at[i, "out"] = r["out"] - use
                found_payments[r["customer_name"]] = left - use
        w = w[(w["out"] > 0.005) & w["due_date"].notna()]
        w["late"] = [(as_of - d).days for d in w["due_date"]]
        older = w[w["late"] > 365]          # very old debts are Cash Truth write-off findings, not reminders
        w = w[(w["late"] > 30) & (w["late"] <= 365)]
        if len(w):
            by = w.groupby("customer_name").agg(out=("out", "sum"), late=("late", "max"), ids=("id", list),
                                                cp=("counterparty_id", "first"))
            thresh = max(rev12 * 0.0005, 1.0)
            by = by[by["out"] >= thresh].sort_values("out", ascending=False).head(10)
            for name, r in by.iterrows():
                out.append(Alert(
                    id="", severity="critical" if r["late"] > 90 else "warning", kind="late_customer",
                    title=f"{name} is {int(r['late'])} days late",
                    message=(f"{name} owes {format_money(dec(round(r['out'] * 100)), cur)} on {len(r['ids'])} invoice(s); "
                             f"the oldest is {int(r['late'])} days past its due date."),
                    amount=dec(round(r["out"] * 100)), currency=cur, counterparty_id=r["cp"],
                    evidence=[make_ref("invoices", i) for i in r["ids"][:20]],
                    suggested_action="Send a friendly reminder (the copilot can draft it for your approval)."))

    # --- bills due soon or overdue
    b = L.bills
    if len(b):
        bp = L.db.query_df("SELECT bill_id, payment_date, amount_base FROM bill_payments WHERE company_id = ?", [L.company_id])
        bp["payment_date"] = pd.to_datetime(bp["payment_date"]).dt.date
        paidb = bp[bp["payment_date"] <= as_of].groupby("bill_id")["amount_base"].sum()
        w = b[b["issue_date"] <= as_of].copy()
        w["out"] = pd.to_numeric(w["total_base"]) - w["id"].map(paidb).fillna(0).astype(float)
        w = w[(w["out"] > 0.005) & w["due_date"].notna()]
        w = w.loc[np.array([d <= as_of + dt.timedelta(days=BILL_WINDOW_DAYS) for d in w["due_date"]], dtype=bool)]
        cash_out_m = float(ttm["cash_out"].tail(3).mean()) / 100 if len(ttm) else 0.0
        big = max(cash_out_m * 0.05, 1.0)        # a bill is "large" at 5% or more of a month's money out
        w = w[w["out"] >= big].sort_values("out", ascending=False).head(5).sort_values("due_date")
        for r in w.itertuples():
            overdue = r.due_date < as_of
            out.append(Alert(
                id="", severity="warning" if overdue else "info", kind="bill_due",
                title=f"Bill {r.bill_no} from {r.supplier_name} {'overdue' if overdue else 'due ' + str(r.due_date)}",
                message=(f"{format_money(dec(round(r.out * 100)), cur)} to {r.supplier_name} "
                         + (f"was due on {r.due_date} ({(as_of - r.due_date).days} days ago)." if overdue
                            else f"is due on {r.due_date}.")),
                due_date=r.due_date, amount=dec(round(r.out * 100)), currency=cur, counterparty_id=r.counterparty_id,
                evidence=[make_ref("bills", r.id)], suggested_action="Plan the payment or agree new terms with the supplier."))

    # --- margin squeeze
    if len(ttm) >= 6 and ttm["revenue"].iloc[-1] > 0:
        gm_last = ttm["gross_profit"].iloc[-1] / ttm["revenue"].iloc[-1] * 100
        gm_avg = ttm["gross_profit"].sum() / max(1, ttm["revenue"].sum()) * 100
        if gm_avg - gm_last >= MARGIN_DROP_PP:
            out.append(Alert(
                id="", severity="warning", kind="margin_drop",
                title=f"Gross margin fell to {gm_last:.1f}% in {ttm.index[-1]}",
                message=(f"Gross margin in {ttm.index[-1]} was {gm_last:.1f}%, against {gm_avg:.1f}% over the last "
                         f"12 months: costs of goods are rising faster than prices."),
                amount=None, currency=cur, evidence=[],
                suggested_action="Check supplier prices and your own selling prices; run the 'raw material +15%' scenario."))

    # --- unusual spend spikes per expense account (last full month vs previous 12)
    lines = L.lines_upto(as_of)
    exp = lines[lines["acc_type"] == "expense"]
    if len(exp) and len(full) >= 13:
        last_m = full.index[-1]
        hist_months = list(full.index[-13:-1])
        pv = exp[exp["month"].isin(hist_months + [last_m])].pivot_table(
            index="month", columns="account_id", values="base_c", aggfunc="sum").reindex(hist_months + [last_m]).fillna(0)
        for a in pv.columns:
            h = pv.loc[hist_months, a].to_numpy() / 100
            v = pv.loc[last_m, a] / 100
            mu, sd = float(np.mean(h)), float(np.std(h))
            if v > mu + 3 * max(sd, 0.05 * abs(mu)) and v - mu > max(avg_out * 0.02, 1.0):
                ids = exp[(exp["month"] == last_m) & (exp["account_id"] == a)].sort_values("base_c", ascending=False)
                out.append(Alert(
                    id="", severity="warning", kind="unusual_expense",
                    title=f"{L.account_name(a)}: unusually high in {last_m}",
                    message=(f"{L.account_name(a)} was {format_money(dec(round(v * 100)), cur)} in {last_m}, against a "
                             f"usual {format_money(dec(round(mu * 100)), cur)} a month."),
                    amount=dec(round(v * 100)), currency=cur,
                    evidence=[make_ref("journal_lines", i) for i in ids["line_id"].head(20)],
                    suggested_action="Check the largest entries listed as evidence."))

    # --- Cash Truth gap above materiality
    gap = float(ct.gap.value)
    if rev12 and abs(gap) / rev12 * 100 >= GAP_MATERIALITY_PCT:
        pct = abs(gap) / rev12 * 100
        out.append(Alert(
            id="", severity="critical" if pct >= 2 else "warning", kind="cash_truth_gap",
            title=f"Books and real cash differ by {format_money(ct.gap.value, cur)}",
            message=(f"Your books show {format_money(dec(round(abs(gap) * 100)), cur)} "
                     f"{'more' if gap < 0 else 'less'} cash than statements and counts confirm ({pct:.1f}% of yearly "
                     f"revenue). {len(ct.findings)} finding(s) explain {format_money(ct.explained.value, cur)}."),
            amount=ct.gap.value, currency=cur, evidence=(ct.verified_cash.evidence + ct.book_cash.evidence)[:20],
            suggested_action="Review the Cash Truth findings and approve the correcting drafts."))

    # --- customer concentration
    for k in (kp.kpis if kp else []):
        if k.key == "top_customer_share_pct" and k.value is not None and k.value >= 30:
            out.append(Alert(id="", severity="warning" if k.value < 50 else "critical", kind="concentration",
                             title=f"One customer is {k.value:.0f}% of sales", message=k.explanation,
                             evidence=k.evidence[:20], suggested_action="Run the 'lose the biggest customer' scenario."))

    order = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda a: (order[a.severity], a.kind, a.title))
    for i, a in enumerate(out, start=1):
        a.id = f"a-{i:03d}"
    n = {s: sum(1 for a in out if a.severity == s) for s in order}
    summary = (f"{len(out)} alert(s) as of {as_of}: {n['critical']} critical, {n['warning']} warning, {n['info']} info."
               + (f" Most urgent: {out[0].title}." if out else ""))
    return AlertList(company_id=L.company_id, as_of=as_of, alerts=out, summary=summary)


def _last_full_month(as_of: dt.date) -> str:
    nxt = as_of + dt.timedelta(days=1)
    if nxt.month != as_of.month:
        return f"{as_of.year:04d}-{as_of.month:02d}"
    prev = as_of.replace(day=1) - dt.timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"
