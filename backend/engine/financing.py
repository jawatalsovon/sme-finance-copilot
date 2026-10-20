"""Financing matches: catalogue rules checked against computed facts + affordability.

Plain words: we compute facts about the business from its own numbers (years trading, last-12-month
revenue, export revenue, invoiced share, marketplace and card history, forecast cash), check each
product's published eligibility rules, size a loan to the gap the forecast shows, and test whether
the forecast cash flow can carry the repayment (DSCR = cash available for debt service / debt
service). Facts we cannot know from the books (credit-bureau status, owner age, tax grade) are
shown as "to confirm" and never counted as passed. Illustrative only; the copilot never applies.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path

import pandas as pd

from backend.core.util import format_money
from backend.engine.data import Ledger, dec
from backend.engine.types import (
    Affordability, CashflowForecast, EligibilityCheck, FinancingMatch, FinancingMatches, FinancingProduct,
)

CATALOGUE = Path(__file__).resolve().parents[2] / "docs" / "research" / "financing_catalogue.json"
DEFAULT_APR = {"BD": 13.0, "CN": 5.0, "GB": 11.0}
MIN_DSCR = 1.25

TYPE_MAP = {
    "bank_term_loan_unsecured": "term_loan", "bank_term_loan": "term_loan", "government_guaranteed_term_loan": "govt_scheme",
    "central_bank_refinance_working_capital": "working_capital", "bank_online_unsecured_working_capital": "working_capital",
    "bank_overdraft_secured": "overdraft", "bank_overdraft": "overdraft", "microfinance_group_or_individual_loan": "microfinance",
    "digital_nano_loan": "microfinance", "bank_unsecured_tax_credit_loan": "working_capital",
    "guaranteed_bank_loan": "govt_scheme", "export_credit_insurance": "trade_finance",
    "inventory_finance_platform_data": "working_capital", "asset_finance": "lease", "invoice_finance": "invoice_financing",
    "revenue_based_finance": "working_capital", "tax_payment_plan": "other",
}
NOT_LOANS = {"export_credit_insurance", "tax_payment_plan"}


def load_catalogue(path: Path = CATALOGUE) -> list[dict]:
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("products", [])


def parse_apr(text: str, country: str) -> float:
    nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", text or "")]
    nums = [n for n in nums if 1.0 <= n <= 40.0]
    return max(nums) if nums else DEFAULT_APR.get(country, 12.0)   # conservative: highest quoted rate


def annuity(principal: float, apr_pct: float, months: int) -> float:
    r = apr_pct / 100 / 12
    if r <= 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** -months)


def company_facts(L: Ledger, monthly: pd.DataFrame, as_of: dt.date, fc: CashflowForecast | None) -> dict:
    """Facts from the data. Values are (value, evidence_text); None value = unknown."""
    first = L.first_date
    founded = L.company.get("founded_on")
    start = pd.Timestamp(founded).date() if founded is not None and not pd.isna(founded) else first
    years = (as_of - start).days / 365.25 if start else None
    ttm = monthly.tail(12)
    rev = float(ttm["revenue"].sum()) / 100 if len(ttm) else 0.0
    # export revenue: revenue lines booked in a foreign currency, valued in USD
    lines = L.lines_upto(as_of)
    lines = lines[lines["entry_date"] > as_of - dt.timedelta(days=365)]
    rl = lines[(lines["acc_subtype"] == "revenue") & (lines["currency"] != L.base)]
    usd = L.fx_rate("USD", as_of) if L.base != "USD" else 1.0
    export_usd = (-rl["base_c"].sum() / 100) / usd if usd and len(rl) else 0.0
    inv = L.invoices
    b2b = 0.0
    if len(inv) and rev:
        w = inv[(inv["issue_date"] > as_of - dt.timedelta(days=365)) & (inv["issue_date"] <= as_of)]
        b2b = float(pd.to_numeric(w["total_base"]).sum()) / rev if len(w) else 0.0
    st = L.statements
    st = st[st["txn_date"] <= as_of]
    mkt = st[(st["channel"] == "pos_settlement") & ~st["account_id"].isin(L.accounts_of("cash", "bank", "wallet").index)]
    mkt_months = mkt["month"].nunique() if len(mkt) else 0
    card_months = st[st["channel"] == "pos_settlement"]["month"].nunique() if len(st) else 0
    try:
        pos = L.db.query_df("SELECT COUNT(DISTINCT strftime(sales_date, '%Y-%m')) n FROM pos_sales_daily "
                            "WHERE company_id = ? AND card_amount > 0", [L.company_id])
        card_months = max(card_months, int(pos["n"].iloc[0] or 0))
    except Exception:  # noqa: BLE001
        pass
    bal = L.lines_upto(as_of).groupby("acc_type")["base_c"].sum()
    net_assets = (bal.get("asset", 0) + bal.get("liability", 0)) / 100
    pts = fc.points if fc else []
    min_close = min(float(p.closing_cash) for p in pts) if pts else None
    avg_out = float(ttm["cash_out"].mean()) / 100 if len(ttm) else 0.0
    needs_wc = min_close is not None and min_close < avg_out   # cash dips below one month of outgoings
    return {
        "years_trading": round(years, 1) if years is not None else None,
        "annual_revenue_local": round(rev, 2),
        "annual_export_usd": round(float(export_usd), 2),
        "sector": L.company.get("industry"),
        "country_registered": L.country,
        "b2b_invoice_share": round(b2b, 3),
        "has_ecommerce_platform_history_months": int(mkt_months),
        "months_card_sales": int(card_months),
        "insolvent": bool(net_assets < 0),
        "needs_working_capital": bool(needs_wc),
        # unknowable from the books: ask the owner
        "cib_clear": None, "foreign_invested": None, "tax_credit_grade": None, "owner_age": None,
        "tax_arrears": None, "has_bkash_ekyc": None,
        "_min_forecast_cash": min_close, "_avg_monthly_out": avg_out,
    }


def _check(rule: dict, facts: dict) -> tuple[bool | None, str]:
    f, op, v = rule["field"], rule["op"], rule["value"]
    have = facts.get(f)
    if have is None:
        return None, f"{f.replace('_', ' ')}: not in your data, please confirm ({op} {v})"
    ok = {"==": have == v, "!=": have != v, ">=": have >= v, "<=": have <= v, ">": have > v, "<": have < v,
          "in": have in (v if isinstance(v, list) else [v])}.get(op)
    if ok is None:
        return None, f"{f}: rule '{op}' not understood"
    shown = f"{have:,.1f}" if isinstance(have, float) else str(have)
    return bool(ok), f"{f.replace('_', ' ')} is {shown} (needs {op} {v})"


def financing_matches(L: Ledger, monthly: pd.DataFrame, fc: CashflowForecast | None, as_of: dt.date,
                      existing_debt_service: float) -> FinancingMatches:
    cur = L.base
    facts = company_facts(L, monthly, as_of, fc)
    pts = fc.points if fc else []
    # free cash flow per month = last-12-month operating cash flow (no loans, no owner money, interest
    # added back), scaled by how much money in the forecast expects compared with the last 12 months
    ttm = monthly.tail(12)
    op_net = float((ttm["op_in"] - ttm["op_out"]).sum()) / 100 / max(1, len(ttm))
    ttm_in = float(ttm["cash_in"].sum()) / 100
    fc_in = sum(float(p.inflow) for p in pts) * (12 / max(1, len(pts)))
    growth = min(1.5, max(0.5, fc_in / ttm_in)) if ttm_in > 0 and pts else 1.0
    recent = L.lines_upto(as_of)
    recent = recent[recent["entry_date"] > as_of - dt.timedelta(days=365)]
    interest_m = float(recent[recent["acc_subtype"] == "interest"]["base_c"].sum()) / 100 / 12
    net_month = op_net * growth                      # free cash flow after current interest
    cfads = net_month + interest_m                   # cash available for (existing + new) debt service
    min_cash = facts["_min_forecast_cash"]
    avg_out = facts["_avg_monthly_out"]
    shortfall = max(0.0, avg_out - (min_cash or 0.0))
    matches = []
    for p in load_catalogue():
        if p.get("country") != L.country:
            continue
        checks = []
        unknown = 0
        failed = 0
        for r in p.get("eligibility", []):
            ok, why = _check(r, facts)
            if not r.get("confirmed", True):
                why += " (rule not confirmed by the provider)"
            if ok is None:
                unknown += 1
            elif not ok:
                failed += 1
            checks.append(EligibilityCheck(rule=f"{r['field']} {r['op']} {r['value']}", passed=bool(ok), reason=why))
        ccy = p.get("currency") or cur
        apr = parse_apr(p.get("indicative_rate_text", ""), L.country)
        tenor = int(p.get("tenor_months_max") or p.get("tenor_months_min") or 12)
        tenor = max(1, min(tenor, 60))
        # size: the forecast shortfall below one month of outgoings, else one month of outgoings
        want = shortfall if shortfall > 0 else avg_out
        lo, hi = p.get("amount_min"), p.get("amount_max")
        if hi:
            want = min(want, float(hi))
        if lo:
            want = max(want, float(lo))
        need = want
        # what the cash flow can carry: payment that keeps DSCR at MIN_DSCR, turned into a loan amount
        max_pay = cfads / MIN_DSCR - existing_debt_service
        max_principal = max_pay / annuity(1.0, apr, tenor) if max_pay > 0 else 0.0
        capped = False
        if 0 < max_principal < want:
            want, capped = max_principal, True
            if lo and want < float(lo):
                want = float(lo)
        if capped:   # round down, so the rounded amount still keeps DSCR at the threshold
            want = float(math.floor(want / 1000) * 1000) if want > 10000 else float(math.floor(want))
        else:
            want = float(round(want, -3)) if want > 10000 else float(round(want, 0))
        afford = None
        if p.get("type") not in NOT_LOANS and want > 0:
            pay = annuity(want, apr, tenor)
            dscr = cfads / (existing_debt_service + pay) if (existing_debt_service + pay) > 0 else None
            free_after = net_month - (existing_debt_service - interest_m)   # after current principal repayments
            share = pay / free_after * 100 if free_after > 0 else None
            ok = dscr is not None and dscr >= MIN_DSCR
            afford = Affordability(
                requested_amount=dec(round(want * 100)), monthly_repayment=dec(round(pay * 100)),
                dscr=round(dscr, 2) if dscr is not None else None,
                min_forecast_cash_after=dec(round(((min_cash or 0) + want - pay * min(tenor, len(pts))) * 100)),
                affordable=ok,
                explanation=((f"Borrowing {format_money(dec(round(want * 100)), ccy)} over {tenor} months at about {apr:.1f}% "
                              f"a year costs about {format_money(dec(round(pay * 100)), ccy)} a month. Forecast cash available "
                              f"for debt service (operating cash flow without loans or owner money over the last 12 "
                              f"months, scaled by the forecast) is {format_money(dec(round(cfads * 100)), cur)} a month, "
                              f"against existing loan payments of {format_money(dec(round(existing_debt_service * 100)), cur)}"
                              f" a month, so DSCR is "
                              f"{dscr:.2f} ({'at least' if ok else 'below'} the {MIN_DSCR} lenders usually want)"
                              + (f"; the repayment is {share:.0f}% of forecast monthly free cash flow." if share is not None
                                 else "; forecast free cash flow is not positive.")
                              + (f" The amount is limited to what your cash flow can carry; the full need is about "
                                 f"{format_money(dec(round(need * 100)), ccy)}." if capped else "")
                              + (" Your cash flow cannot carry new loan payments at the moment." if max_pay <= 0 else ""))
                             if dscr is not None else "Repayment cannot be assessed: no forecast cash flow."))
        eligible = failed == 0
        score = 0.0
        n = max(1, len(checks))
        # a rule we cannot check from the data (to confirm with the owner) counts half: neither pass nor fail
        score += 0.45 * ((sum(1 for c in checks if c.passed) + 0.5 * unknown) / n) if checks else 0.3
        score += 0.30 * (1.0 if afford and afford.affordable else 0.3 if afford and (afford.dscr or 0) >= 1 else 0.0) \
            if afford else 0.15
        score += 0.15 * max(0.0, 1 - apr / 40)
        if facts["needs_working_capital"] and TYPE_MAP.get(p.get("type"), "other") in ("working_capital", "overdraft",
                                                                                         "invoice_financing"):
            score += 0.10
        if not eligible:
            score *= 0.4
        product = FinancingProduct(
            id=p["id"], provider=p.get("provider", ""), name=p.get("name", ""), country=p.get("country", ""),
            product_type=TYPE_MAP.get(p.get("type"), "other"), currency=ccy,  # type: ignore[arg-type]
            min_amount=dec(round(float(lo) * 100)) if lo else None, max_amount=dec(round(float(hi) * 100)) if hi else None,
            apr_min_pct=None, apr_max_pct=apr, tenor_months_min=p.get("tenor_months_min"),
            tenor_months_max=p.get("tenor_months_max"),
            requirements=list(p.get("documents_required", []))[:10], source_url=p.get("source_url", ""), illustrative=True)
        fails = [c.reason for c in checks if not c.passed and "please confirm" not in c.reason]
        confirm = [c.reason for c in checks if "please confirm" in c.reason]
        why = (("Fits on the published rules we can check" if eligible else "Does not fit: " + "; ".join(fails))
               + (f". To confirm with you: {'; '.join(confirm)}" if confirm else "")
               + (f". {afford.explanation}" if afford else ".")
               + (f" Rate used: {apr:.1f}% (highest rate quoted in public sources; actual offers differ)."))
        matches.append(FinancingMatch(product=product, eligible=eligible, fit_score=round(min(1.0, score), 3),
                                      checks=checks, affordability=afford, explanation=why[:1500],
                                      evidence=fc.opening_cash.evidence[:5] if fc else []))
    matches.sort(key=lambda m: (-m.fit_score, m.product.id))
    facts_txt = (f"{facts['years_trading']} years trading, last-12-month revenue "
                 f"{format_money(dec(round(facts['annual_revenue_local'] * 100)), cur)}")
    top = [m for m in matches if m.eligible][:3]
    summary = (f"Checked {len(matches)} illustrative {L.country} products against your numbers ({facts_txt}). "
               + (f"Best fits: {'; '.join(m.product.name for m in top)}." if top else "None fits on the published rules.")
               + " Illustrative public information only; the copilot never applies for credit.")
    return FinancingMatches(company_id=L.company_id, as_of=as_of, currency=cur, matches=matches, summary=summary)
