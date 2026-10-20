"""Chart-of-accounts helpers: lookup by code / name / subtype, and a keyword guess for accounts that only
appear as a code + name in a journal export (the owner can correct the guess; it never touches amounts)."""
from __future__ import annotations

import re
from typing import Any, Optional

import pandas as pd

from backend.connectors._util import clean, norm_header

TYPES = ("asset", "liability", "equity", "income", "expense")

_RULES: dict[str, list[tuple[str, str]]] = {
    "asset": [
        (r"\bcash\b|\btills?\b|petty|safe", "cash"),
        (r"clearing|awaiting payout", "other_asset"),
        (r"bkash|nagad|rocket|wallet|alipay|wechat|paypal|stripe", "wallet"),
        (r"wise|bank|current a/?c|current account|savings|checking", "bank"),
        (r"receivable|debtors|baki|settlement due|processor balance|customers?\b", "ar"),
        (r"inventory|stock", "inventory"),
        (r"prepaid|advance|dadon|supplier deposit|deposits?\b", "prepaid"),
        (r"equipment|fixtures|vehicle|tooling|polytunnel|machinery|fridge|furniture|computer", "fixed_asset"),
    ],
    "liability": [
        (r"payable|creditors|udhar|wholesaler", "ap"),
        (r"\bvat\b|sales tax|\bgst\b|tax control|tax payable", "tax_payable"),
        (r"loan|borrowing|overdraft|mortgage", "loan"),
        (r"payroll|wages payable|paye", "payroll_payable"),
        (r"accrued|accrual", "accrued"),
    ],
    "equity": [
        (r"capital", "owner_equity"),
        (r"drawings|dividend", "drawings"),
        (r"retained|earnings", "retained_earnings"),
    ],
    "income": [
        (r"other income|reimbursement|interest received|gain", "other_income"),
    ],
    "expense": [
        (r"cost of|cogs|goods cost|freight|duties|customs|stock loss|write-?offs?|wastage|lost and damaged|spoilage|shrink", "cogs"),
        (r"salar|wage|payroll|staff", "payroll"),
        (r"\brent\b", "rent"),
        (r"electric|energy|water|utilit|internet", "utilities"),
        (r"marketing|advertis", "marketing"),
        (r"interest", "interest"),
        (r"exchange|\bfx\b", "fx_loss"),
        (r"bad debt|refunds? and returns", "other_expense"),
        (r"income tax|corporation tax", "tax"),
    ],
}
_DEFAULT_SUBTYPE = {"asset": "other_asset", "liability": "other_liability", "equity": "owner_equity",
                    "income": "revenue", "expense": "opex"}
_BY_DIGIT = {"1": "asset", "2": "liability", "3": "equity", "4": "income", "5": "expense", "6": "expense",
             "7": "expense", "8": "expense", "9": "expense"}


def infer_account(code: Optional[str], name: str) -> tuple[str, str]:
    """(type, subtype) from the code's first digit and keywords in the name."""
    low = name.lower()
    atype = _BY_DIGIT.get((code or "")[:1])
    if atype is None:
        for t in TYPES:
            if any(re.search(p, low) for p, _ in _RULES[t]):
                atype = t
                break
        atype = atype or "expense"
    for pat, sub in _RULES[atype]:
        if re.search(pat, low):
            return atype, sub
    return atype, _DEFAULT_SUBTYPE[atype]


class AccountCatalog:
    """Accounts of one company: id, code, name, type, subtype."""

    def __init__(self, company_id: str, accounts: Any = None):
        self.company_id = company_id
        self.rows: list[dict[str, Any]] = []
        if accounts is None:
            return
        if hasattr(accounts, "query_df"):
            accounts = accounts.query_df("SELECT id, code, name, type, subtype, currency FROM accounts WHERE company_id = ?",
                                         [company_id])
        df = accounts if isinstance(accounts, pd.DataFrame) else pd.DataFrame(list(accounts))
        for r in df.to_dict("records"):
            self.rows.append({k: clean(r.get(k)) for k in ("id", "code", "name", "type", "subtype", "currency")})

    def add(self, row: dict[str, Any]) -> None:
        self.rows.append(row)

    def by_code(self, code: Any) -> Optional[dict[str, Any]]:
        c = clean(code)
        return next((r for r in self.rows if r["code"] == c), None) if c else None

    def by_name(self, name: Any) -> Optional[dict[str, Any]]:
        n = norm_header(name)
        return next((r for r in self.rows if norm_header(r["name"]) == n), None) if n else None

    def by_subtype(self, subtype: str) -> Optional[dict[str, Any]]:
        return next((r for r in self.rows if r["subtype"] == subtype), None)

    def account_id(self, code: Optional[str], name: Optional[str]) -> str:
        """Id for (code, name): existing account if known, else the deterministic '<company>-<code>'."""
        hit = self.by_code(code) or self.by_name(name)
        if hit:
            return hit["id"]
        from backend.core.ids import make_id
        return f"{self.company_id}-{clean(code)}" if clean(code) else make_id("acct", self.company_id, name)
