"""Monthly P&L, cash in/out and KPIs, on the books basis or the corrected basis.

Corrected basis = the books plus every Cash Truth draft entry, as if the owner had approved
them all (nothing is written). P&L buckets come from the account subtypes:
  revenue            income/revenue            (credit positive)
  other income       income/other_income
  COGS               expense/cogs
  operating expenses every other expense subtype except interest and tax
  net profit         gross profit + other income - all non-COGS expenses
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

import numpy as np
import pandas as pd

from backend.core.util import format_money
from backend.engine.data import CASH_SUBTYPES, Ledger, cents, dec, month_bounds
from backend.engine.interfaces import EngineError
from backend.engine.types import (
    CashTruthResult, DraftJournalEntry, Figure, KPI, KPISet, MonthlyPnL, PnLLine,
)
from backend.ledger.lineage import make_ref

BELOW_OPERATING = ("interest", "tax")
FINANCING_SUBTYPES = ("loan", "owner_equity", "drawings", "retained_earnings")
OPEX_LABELS = {
    "opex": "Running costs", "payroll": "Staff costs", "rent": "Rent", "utilities": "Utilities",
    "marketing": "Marketing and advertising", "interest": "Loan interest", "tax": "Tax",
    "fx_loss": "Exchange differences", "other_expense": "Other expenses",
}
HINTS = {
    "revenue": "Money earned from sales in the month (before costs).",
    "cogs": "Cost of the goods you sold: stock, freight, duties, stock losses.",
    "gross_profit": "Revenue minus the cost of the goods sold.",
    "operating_expenses": "Running costs: staff, rent, utilities, fees, marketing and similar.",
    "net_profit": "What is left after all costs, interest and other income.",
}


def draft_lines_frame(L: Ledger, entries: list[DraftJournalEntry]) -> pd.DataFrame:
    """Draft entries as journal-line-like rows (base cents, debit positive)."""
    rows = []
    acc = L.accounts
    for i, e in enumerate(entries):
        for j, ln in enumerate(e.lines):
            base_c = (int(round(float(ln.amount_base) * 100)) if ln.amount_base is not None
                      else int(round(float(ln.debit - ln.credit) * float(ln.fx_rate) * 100)))
            rows.append({
                "line_id": f"draft-{i}-{j}", "entry_id": f"draft-{i}", "entry_date": e.entry_date,
                "account_id": ln.account_id, "base_c": base_c,
                "native_c": int(round(float(ln.debit - ln.credit) * 100)), "entry_memo": e.memo,
                "evidence": e.evidence,
            })
    df = pd.DataFrame(rows, columns=["line_id", "entry_id", "entry_date", "account_id", "base_c", "native_c",
                                     "entry_memo", "evidence"])
    df["acc_type"] = df["account_id"].map(acc["type"]).fillna("")
    df["acc_subtype"] = df["account_id"].map(acc["subtype"]).fillna("")
    df["month"] = [f"{d.year:04d}-{d.month:02d}" for d in df["entry_date"]]
    return df


def pnl_buckets(lines: pd.DataFrame) -> dict[str, int]:
    """Sum P&L lines into buckets (positive numbers = income for income buckets, cost for cost buckets)."""
    out: dict[str, int] = defaultdict(int)
    for (t, st), v in lines.groupby(["acc_type", "acc_subtype"])["base_c"].sum().items():
        if t == "income":
            out["revenue" if st == "revenue" else "other_income"] += -int(v)
        elif t == "expense":
            out["cogs" if st == "cogs" else f"opex:{st}"] += int(v)
    return dict(out)


def totals(b: dict[str, int]) -> dict[str, int]:
    rev, oi, cogs = b.get("revenue", 0), b.get("other_income", 0), b.get("cogs", 0)
    opx = {k: v for k, v in b.items() if k.startswith("opex:")}
    operating = sum(v for k, v in opx.items() if k.split(":", 1)[1] not in BELOW_OPERATING)
    below = sum(v for k, v in opx.items() if k.split(":", 1)[1] in BELOW_OPERATING)
    gross = rev - cogs
    op_profit = gross - operating
    net = op_profit + oi - below
    return {"revenue": rev, "other_income": oi, "cogs": cogs, "gross_profit": gross,
            "operating_expenses": operating, "operating_profit": op_profit, "below": below, "net_profit": net}


class PnLBuilder:
    def __init__(self, L: Ledger, corrections: pd.DataFrame | None):
        self.L = L
        self.corr = corrections  # draft lines frame or None

    def month_lines(self, month: str, basis: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        s, e = month_bounds(month)
        lines = self.L.lines
        book = lines[(lines["entry_date"] >= s) & (lines["entry_date"] <= e)]
        corr = pd.DataFrame(columns=lines.columns)
        if basis == "corrected" and self.corr is not None and len(self.corr):
            corr = self.corr[(self.corr["entry_date"] >= s) & (self.corr["entry_date"] <= e)]
        return book, corr

    def buckets(self, month: str, basis: str) -> dict[str, int]:
        book, corr = self.month_lines(month, basis)
        b = pnl_buckets(book[book["acc_type"].isin(["income", "expense"])])
        if len(corr):
            for k, v in pnl_buckets(corr[corr["acc_type"].isin(["income", "expense"])]).items():
                b[k] = b.get(k, 0) + v
        return b

    def cash_flows(self, month: str, basis: str) -> tuple[int, int, list[str], list[str]]:
        """Money in / out of cash, bank and wallet accounts (transfers between them excluded)."""
        book, corr = self.month_lines(month, basis)
        frames = [book[["entry_id", "account_id", "acc_subtype", "base_c", "line_id"]]]
        if len(corr):
            frames.append(corr[["entry_id", "account_id", "acc_subtype", "base_c", "line_id"]])
        df = pd.concat(frames, ignore_index=True)
        cash = df[df["acc_subtype"].isin(CASH_SUBTYPES)]
        non_cash_entries = set(df[~df["acc_subtype"].isin(CASH_SUBTYPES)]["entry_id"])
        cash = cash[cash["entry_id"].isin(non_cash_entries)]   # drop pure cash-to-cash transfers
        net = cash.groupby("entry_id")["base_c"].sum()
        cin = int(net[net > 0].sum())
        cout = int(-net[net < 0].sum())
        ids_in = cash[cash["entry_id"].isin(net[net > 0].index)]["line_id"].astype(str).tolist()
        ids_out = cash[cash["entry_id"].isin(net[net < 0].index)]["line_id"].astype(str).tolist()
        return cin, cout, ids_in, ids_out

    def monthly_pnl(self, month: str, basis: str) -> MonthlyPnL:
        L = self.L
        b = self.buckets(month, basis)
        book, corr = self.month_lines(month, basis)
        if book.empty and corr.empty:
            raise EngineError(f"no transactions in {month} for {L.company_id}")
        t = totals(b)
        cur = L.base
        pl = book[book["acc_type"].isin(["income", "expense"])]

        def ev(mask_fn):
            ids = pl[mask_fn(pl)]["line_id"].astype(str)
            refs = [make_ref("journal_lines", i) for i in ids.head(200)]
            if len(corr):
                c = corr[mask_fn(corr)]
                for evs in c["evidence"].head(50):
                    refs.extend(evs[:3])
            return refs, int(len(ids))

        def fig(key, label, value, mask_fn=None, formula=""):
            refs, n = ev(mask_fn) if mask_fn else ([], 0)
            return Figure(key=key, label=label, value=dec(value), currency=cur, evidence=refs[:200],
                          evidence_count=n, formula=formula, hint=HINTS.get(key, ""))

        is_rev = lambda d: (d["acc_type"] == "income") & (d["acc_subtype"] == "revenue")  # noqa: E731
        is_cogs = lambda d: (d["acc_type"] == "expense") & (d["acc_subtype"] == "cogs")  # noqa: E731
        is_opex = lambda d: (d["acc_type"] == "expense") & ~d["acc_subtype"].isin(("cogs",) + BELOW_OPERATING)  # noqa: E731
        lines_out: list[PnLLine] = []
        for k in sorted(b):
            if not k.startswith("opex:"):
                continue
            st = k.split(":", 1)[1]
            refs, n = ev(lambda d, st=st: (d["acc_type"] == "expense") & (d["acc_subtype"] == st))
            lines_out.append(PnLLine(key=k, label=OPEX_LABELS.get(st, st.replace("_", " ").title()),
                                     amount=dec(b[k]), evidence=refs[:50], evidence_count=n))
        # account-level detail
        detail = pl.groupby("account_id")["base_c"].sum()
        if len(corr):
            cp = corr[corr["acc_type"].isin(["income", "expense"])].groupby("account_id")["base_c"].sum()
            detail = detail.add(cp, fill_value=0)
        for a, v in detail.items():
            if a not in L.accounts.index or int(v) == 0:
                continue
            sign = -1 if L.accounts.loc[a, "type"] == "income" else 1
            refs = [make_ref("journal_lines", i) for i in pl[pl["account_id"] == a]["line_id"].astype(str).head(20)]
            lines_out.append(PnLLine(key=f"account:{a}", label=L.account_name(a), amount=dec(sign * int(v)),
                                     evidence=refs, evidence_count=int((pl["account_id"] == a).sum())))
        cin, cout, ids_in, ids_out = self.cash_flows(month, basis)
        gm = (t["gross_profit"] / t["revenue"] * 100) if t["revenue"] else None
        prev = _prev_month(month)
        comp = None
        try:
            pb = totals(self.buckets(prev, basis))
            comp = {k: round((t[k] - pb[k]) / abs(pb[k]) * 100, 1) for k in ("revenue", "gross_profit", "net_profit",
                                                                            "operating_expenses") if pb.get(k)}
        except EngineError:
            pass
        notes = []
        if basis == "corrected":
            n_corr = corr["entry_id"].nunique() if len(corr) else 0
            notes.append(f"Corrected basis: includes {n_corr} proposed correction(s) from Cash Truth dated in "
                         f"{month}, as if approved. Nothing has been written to your books.")
        if t["other_income"]:
            notes.append(f"Other income of {format_money(dec(t['other_income']), cur)} is shown below operating profit.")
        summary = (f"{month} ({basis}): revenue {format_money(dec(t['revenue']), cur)}, cost of goods "
                   f"{format_money(dec(t['cogs']), cur)}, gross profit {format_money(dec(t['gross_profit']), cur)}"
                   f"{f' ({gm:.1f}% margin)' if gm is not None else ''}, running costs "
                   f"{format_money(dec(t['operating_expenses']), cur)}, net profit {format_money(dec(t['net_profit']), cur)}. "
                   f"Cash in {format_money(dec(cin), cur)}, cash out {format_money(dec(cout), cur)}.")
        return MonthlyPnL(
            company_id=L.company_id, month=month, currency=cur, basis=basis,  # type: ignore[arg-type]
            revenue=fig("revenue", "Revenue (sales)", t["revenue"], is_rev, "Credits to revenue accounts in the month."),
            cogs=fig("cogs", "Cost of goods sold", t["cogs"], is_cogs, "Debits to cost-of-goods accounts."),
            gross_profit=fig("gross_profit", "Gross profit", t["gross_profit"], None, "revenue - cost of goods sold"),
            gross_margin_pct=round(gm, 2) if gm is not None else None,
            operating_expenses=fig("operating_expenses", "Running costs", t["operating_expenses"], is_opex,
                                   "All other expenses except interest and tax."),
            operating_profit=fig("operating_profit", "Operating profit", t["operating_profit"], None,
                                 "gross profit - running costs"),
            net_profit=fig("net_profit", "Net profit", t["net_profit"], None,
                           "operating profit + other income - interest - tax"),
            cash_in=Figure(key="cash_in", label="Money in", value=dec(cin), currency=cur,
                           evidence=[make_ref("journal_lines", i) for i in ids_in[:200] if not i.startswith("draft")],
                           evidence_count=len(ids_in), formula="Receipts into cash, bank and wallet accounts (transfers between them excluded)."),
            cash_out=Figure(key="cash_out", label="Money out", value=dec(cout), currency=cur,
                            evidence=[make_ref("journal_lines", i) for i in ids_out[:200] if not i.startswith("draft")],
                            evidence_count=len(ids_out), formula="Payments out of cash, bank and wallet accounts (transfers excluded)."),
            lines=lines_out, comparison_prev_month=comp, notes=notes, summary=summary)


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:])
    return f"{y - (m == 1):04d}-{(m - 2) % 12 + 1:02d}"


def months_between(first: dt.date, last: dt.date) -> list[str]:
    out = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def monthly_table(L: Ledger, corrections: pd.DataFrame | None, basis: str, upto: dt.date) -> pd.DataFrame:
    """One row per month: revenue, cogs, opex, net profit, cash in, cash out (base cents). Fast path for KPIs/forecast."""
    lines = L.lines[L.lines["entry_date"] <= upto]
    frames = [lines[["entry_id", "entry_date", "account_id", "acc_type", "acc_subtype", "base_c", "month"]]]
    if basis == "corrected" and corrections is not None and len(corrections):
        c = corrections[corrections["entry_date"] <= upto]
        frames.append(c[["entry_id", "entry_date", "account_id", "acc_type", "acc_subtype", "base_c", "month"]])
    df = pd.concat(frames, ignore_index=True)
    rows = {}
    pl = df[df["acc_type"].isin(["income", "expense"])]
    for month, g in pl.groupby("month"):
        rows[month] = totals(pnl_buckets(g))
    out = pd.DataFrame.from_dict(rows, orient="index").fillna(0).astype("int64")
    # cash flows
    is_cash = df["acc_subtype"].isin(CASH_SUBTYPES)
    non_cash_entries = set(df.loc[~is_cash, "entry_id"])
    cash = df[is_cash & df["entry_id"].isin(non_cash_entries)]
    net = cash.groupby(["month", "entry_id"])["base_c"].sum().reset_index()
    cin = net[net["base_c"] > 0].groupby("month")["base_c"].sum()
    cout = -net[net["base_c"] < 0].groupby("month")["base_c"].sum()
    out["cash_in"] = cin.reindex(out.index).fillna(0).astype("int64")
    out["cash_out"] = cout.reindex(out.index).fillna(0).astype("int64")
    # operating cash flow: without loan principal and owner money (capital, drawings, dividends)
    fin_entries = set(df.loc[df["acc_subtype"].isin(FINANCING_SUBTYPES), "entry_id"])
    op = net[~net["entry_id"].isin(fin_entries)]
    out["op_in"] = op[op["base_c"] > 0].groupby("month")["base_c"].sum().reindex(out.index).fillna(0).astype("int64")
    out["op_out"] = (-op[op["base_c"] < 0].groupby("month")["base_c"].sum()).reindex(out.index).fillna(0).astype("int64")
    out.index.name = "month"
    return out.sort_index()


# ---------------------------------------------------------------------------- KPIs
def balance_by_subtype(L: Ledger, corrections: pd.DataFrame | None, as_of: dt.date, basis: str) -> dict[str, int]:
    lines = L.lines_upto(as_of)
    b = lines.groupby("acc_subtype")["base_c"].sum()
    if basis == "corrected" and corrections is not None and len(corrections):
        c = corrections[corrections["entry_date"] <= as_of]
        b = b.add(c.groupby("acc_subtype")["base_c"].sum(), fill_value=0)
    return {k: int(v) for k, v in b.items()}


def ar_aging(L: Ledger, as_of: dt.date) -> dict[str, int]:
    inv = L.invoices
    if inv.empty:
        return {}
    pays = L.invoice_payments
    pays = pays[pays["payment_date"] <= as_of]
    paid_base = pays.groupby("invoice_id")["amount_base"].sum()
    inv = inv[inv["issue_date"] <= as_of].copy()
    inv["out"] = (pd.to_numeric(inv["total_base"]) - inv["id"].map(paid_base).fillna(0).astype(float))
    inv = inv[inv["out"] > 0.005]
    buckets = {"current": 0, "1-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    for due, out in zip(inv["due_date"], inv["out"]):
        days = (as_of - due).days if due else 0
        k = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
        buckets[k] += int(round(out * 100))
    return buckets


def ap_aging(L: Ledger, as_of: dt.date) -> dict[str, int]:
    b = L.bills
    if b.empty:
        return {}
    pays = L.db.query_df("SELECT bill_id, payment_date, amount_base FROM bill_payments WHERE company_id = ?",
                         [L.company_id])
    pays["payment_date"] = pd.to_datetime(pays["payment_date"]).dt.date
    pays = pays[pays["payment_date"] <= as_of]
    paid = pays.groupby("bill_id")["amount_base"].sum()
    b = b[b["issue_date"] <= as_of].copy()
    b["out"] = pd.to_numeric(b["total_base"]) - b["id"].map(paid).fillna(0).astype(float)
    b = b[b["out"] > 0.005]
    buckets = {"not_due": 0, "1-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    for due, out in zip(b["due_date"], b["out"]):
        days = (as_of - due).days if due else 0
        k = "not_due" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
        buckets[k] += int(round(out * 100))
    return buckets


def kpis(L: Ledger, ct: CashTruthResult, corrections: pd.DataFrame | None, as_of: dt.date) -> KPISet:
    cur = L.base
    tbl = monthly_table(L, corrections, "corrected", as_of)
    last_full = _last_full_month(as_of)
    tbl_full = tbl[tbl.index <= last_full]
    if tbl_full.empty:
        raise EngineError(f"not enough history before {as_of} for KPIs")
    ttm = tbl_full.tail(12)
    prev = tbl_full.iloc[-24:-12] if len(tbl_full) >= 24 else None
    bal = balance_by_subtype(L, corrections, as_of, "corrected")
    out: list[KPI] = []
    cash_ver = int(round(float(ct.verified_cash.value) * 100))
    avg_out = int(ttm.tail(3)["cash_out"].mean()) if len(ttm) else 0
    avg_in = int(ttm.tail(3)["cash_in"].mean()) if len(ttm) else 0
    burn = avg_out - avg_in
    runway = cash_ver / avg_out if avg_out > 0 else None
    out.append(KPI(key="runway_months", label="Months of costs your cash covers", value=_r(runway), unit="months",
                   period="cash as of " + str(as_of) + ", costs = average of last 3 months",
                   status=_status(runway, 3, 1), evidence=ct.verified_cash.evidence[:20],
                   explanation=(f"Verified cash {format_money(dec(cash_ver), cur)} divided by average monthly money out "
                                f"{format_money(dec(avg_out), cur)}." + (
                                    f" Money out exceeds money in by {format_money(dec(burn), cur)} a month, so at this pace "
                                    f"cash lasts about {cash_ver / burn:.1f} months." if burn > 0 else
                                    " Money in currently covers money out.")),
                   hint="How long your verified cash would pay your normal monthly outgoings with no new income."))
    rev, gp, net = int(ttm["revenue"].sum()), int(ttm["gross_profit"].sum()), int(ttm["net_profit"].sum())
    gm = gp / rev * 100 if rev else None
    out.append(KPI(key="gross_margin_pct", label="Gross margin (last 12 months)", value=_r(gm), unit="pct",
                   period=f"{ttm.index[0]} to {ttm.index[-1]}", status=_status(gm, 25, 10),
                   explanation=f"Gross profit {format_money(dec(gp), cur)} / revenue {format_money(dec(rev), cur)}.",
                   hint="Of every 100 you sell, how much is left after paying for the goods."))
    nm = net / rev * 100 if rev else None
    out.append(KPI(key="net_margin_pct", label="Net profit margin (last 12 months)", value=_r(nm), unit="pct",
                   period=f"{ttm.index[0]} to {ttm.index[-1]}", status=_status(nm, 5, 0),
                   explanation=f"Net profit {format_money(dec(net), cur)} / revenue {format_money(dec(rev), cur)} (corrected basis).",
                   hint="Of every 100 you sell, how much is profit after all costs."))
    ar = bal.get("ar", 0)
    ap = -bal.get("ap", 0)
    inv_bal = bal.get("inventory", 0)
    cogs = int(ttm["cogs"].sum())
    dso = ar / rev * 365 if rev else None
    dpo = ap / cogs * 365 if cogs else None
    dio = inv_bal / cogs * 365 if cogs else None
    out.append(KPI(key="dso_days", label="Days to get paid (DSO)", value=_r(dso), unit="days", period="last 12 months",
                   status=_status_low(dso, 30, 60),
                   explanation=f"Receivables {format_money(dec(ar), cur)} / 12-month revenue x 365.",
                   hint="On average, how many days of sales are still owed to you."))
    out.append(KPI(key="dpo_days", label="Days you take to pay suppliers (DPO)", value=_r(dpo), unit="days",
                   period="last 12 months", status="unknown",
                   explanation=f"Supplier payables {format_money(dec(ap), cur)} / 12-month cost of goods x 365.",
                   hint="On average, how many days of purchases you still owe."))
    out.append(KPI(key="dio_days", label="Days of stock (DIO)", value=_r(dio), unit="days", period="last 12 months",
                   status="unknown", explanation=f"Stock {format_money(dec(inv_bal), cur)} / 12-month cost of goods x 365.",
                   hint="How many days of sales your stock would last."))
    ccc = (dso or 0) + (dio or 0) - (dpo or 0) if rev and cogs else None
    out.append(KPI(key="cash_conversion", label="Cash conversion cycle", value=_r(ccc), unit="days",
                   period="last 12 months", status=_status_low(ccc, 30, 90),
                   explanation="Days to get paid + days of stock - days you take to pay suppliers.",
                   hint="How long your money is tied up between paying suppliers and getting paid."))
    growth = None
    if prev is not None and int(prev["revenue"].sum()):
        growth = (rev / int(prev["revenue"].sum()) - 1) * 100
    out.append(KPI(key="revenue_growth_pct", label="Revenue growth (12 months vs previous 12)", value=_r(growth),
                   unit="pct", period=f"to {ttm.index[-1]}", status=_status(growth, 5, 0),
                   explanation="Revenue of the last 12 full months compared with the 12 months before.",
                   hint="Are sales growing or shrinking compared with a year earlier."))
    share, top_name, top_ev = _top_customer_share(L, as_of, rev)
    out.append(KPI(key="top_customer_share_pct", label="Largest customer's share of sales", value=_r(share), unit="pct",
                   period="last 12 months", status=_status_low(share, 20, 40), evidence=top_ev,
                   explanation=(f"{top_name} accounts for this share of 12-month revenue (invoiced sales)."
                                if top_name else "No invoiced customers in the last 12 months."),
                   hint="How dependent you are on one customer."))
    gap = float(ct.gap.value)
    gp_rev = abs(gap) / (rev / 100) * 100 if rev else None
    out.append(KPI(key="cash_truth_gap_pct", label="Cash Truth gap as % of yearly revenue", value=_r(gp_rev), unit="pct",
                   period=str(as_of), status=_status_low(gp_rev, 0.5, 2), evidence=ct.gap.evidence[:10],
                   explanation=f"Gap between book and verified cash {format_money(ct.gap.value, cur)}.",
                   hint="How far your books are from the cash you can prove."))
    out.append(KPI(key="verified_cash", label="Verified cash", value=float(ct.verified_cash.value), unit="currency",
                   currency=cur, period=str(as_of), status="unknown", evidence=ct.verified_cash.evidence[:20],
                   explanation="Bank and wallet statement balances plus the latest cash count."))
    aging = ar_aging(L, as_of)
    if aging:
        tot = sum(aging.values())
        out.append(KPI(key="ar_overdue_90_pct", label="Receivables more than 90 days overdue", value=_r(aging["90+"] / tot * 100 if tot else 0),
                       unit="pct", period=str(as_of), status=_status_low(aging["90+"] / tot * 100 if tot else 0, 5, 20),
                       explanation="Aging of what customers owe: " + ", ".join(f"{k}: {format_money(dec(v), cur)}" for k, v in aging.items()),
                       hint="Share of what you are owed that is very late."))
    ap_ag = ap_aging(L, as_of)
    if ap_ag:
        out.append(KPI(key="ap_due_30", label="Supplier bills overdue", value=float(dec(sum(v for k, v in ap_ag.items() if k != "not_due"))),
                       unit="currency", currency=cur, period=str(as_of), status="unknown",
                       explanation="Aging of what you owe suppliers: " + ", ".join(f"{k}: {format_money(dec(v), cur)}" for k, v in ap_ag.items())))
    summary = (f"As of {as_of}: verified cash covers {runway:.1f} months of costs; " if runway is not None else f"As of {as_of}: ") + \
        (f"gross margin {gm:.1f}%, " if gm is not None else "") + \
        (f"net margin {nm:.1f}%, " if nm is not None else "") + \
        (f"customers take {dso:.0f} days to pay, " if dso is not None else "") + \
        (f"revenue {'up' if growth >= 0 else 'down'} {abs(growth):.1f}% on the previous year." if growth is not None else "")
    return KPISet(company_id=L.company_id, as_of=as_of, currency=cur, kpis=out, summary=summary.strip())


def _top_customer_share(L: Ledger, as_of: dt.date, rev_ttm: int):
    inv = L.invoices
    if inv.empty or not rev_ttm:
        return None, None, []
    start = as_of - dt.timedelta(days=365)
    w = inv[(inv["issue_date"] > start) & (inv["issue_date"] <= as_of)]
    if w.empty:
        return None, None, []
    by = w.groupby("customer_name")["total_base"].apply(lambda s: pd.to_numeric(s).sum()).sort_values(ascending=False)
    top = by.index[0]
    share = float(by.iloc[0]) * 100 / (rev_ttm / 100) if rev_ttm else None
    ev = [make_ref("invoices", i) for i in w[w["customer_name"] == top]["id"].head(20)]
    return share, top, ev


def _last_full_month(as_of: dt.date) -> str:
    nxt = as_of + dt.timedelta(days=1)
    if nxt.month != as_of.month:
        return f"{as_of.year:04d}-{as_of.month:02d}"
    first = as_of.replace(day=1) - dt.timedelta(days=1)
    return f"{first.year:04d}-{first.month:02d}"


def _r(v):
    return None if v is None else round(float(v), 2)


def _status(v, good, bad):
    if v is None:
        return "unknown"
    return "good" if v >= good else "bad" if v < bad else "watch"


def _status_low(v, good, bad):
    if v is None:
        return "unknown"
    return "good" if v <= good else "bad" if v > bad else "watch"
