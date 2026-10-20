"""Read-only data access for the engine: one `Ledger` snapshot per company.

All money is handled as integer cents (int64) inside the engine so matching is exact;
results are converted to Decimal only when they leave the engine.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from functools import cached_property

import numpy as np
import pandas as pd

from backend.engine.interfaces import EngineError
from backend.ledger.db import Database

CASH_SUBTYPES = ("cash", "bank", "wallet")


def cents(series) -> np.ndarray:
    """Decimal/float money column -> int64 cents (round half away from zero)."""
    a = pd.to_numeric(pd.Series(series), errors="coerce").fillna(0).to_numpy(dtype="float64")
    return np.round(a * 100.0).astype("int64")


def dec(c: int | float | np.integer) -> Decimal:
    """int cents -> Decimal with 2 places."""
    return (Decimal(int(round(float(c)))) / Decimal(100)).quantize(Decimal("0.01"))


def to_date(v) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return pd.Timestamp(v).date()


def month_key(d) -> str:
    d = to_date(d)
    return f"{d.year:04d}-{d.month:02d}"


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise EngineError(f"month must look like YYYY-MM, got {month!r}")
    y, m = int(month[:4]), int(month[5:])
    if not 1 <= m <= 12:
        raise EngineError(f"month must look like YYYY-MM, got {month!r}")
    start = dt.date(y, m, 1)
    end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
    return start, end


class Ledger:
    """Snapshot of one company's canonical rows (posted journal + statements + AR/AP)."""

    def __init__(self, db: Database, company_id: str):
        self.db = db
        self.company_id = company_id
        comp = db.query_one("SELECT * FROM companies WHERE id = ?", [company_id])
        if not comp:
            raise EngineError(f"unknown company: {company_id}")
        self.company = comp
        self.base = comp["base_currency"]
        self.country = comp["country"]

    # ------------------------------------------------------------------ accounts
    @cached_property
    def accounts(self) -> pd.DataFrame:
        df = self.db.query_df(
            "SELECT id, code, name, type, subtype, currency FROM accounts WHERE company_id = ? ORDER BY code, id",
            [self.company_id])
        return df.set_index("id", drop=False)

    def accounts_of(self, *subtypes: str) -> pd.DataFrame:
        a = self.accounts
        return a[a["subtype"].isin(subtypes)]

    def account_name(self, account_id: str) -> str:
        a = self.accounts
        if account_id in a.index:
            r = a.loc[account_id]
            return f"{r['code']} {r['name']}" if r["code"] else str(r["name"])
        return account_id

    # ------------------------------------------------------------------ journal
    @cached_property
    def lines(self) -> pd.DataFrame:
        """Posted journal lines with entry header and account info."""
        df = self.db.query_df(
            """
            SELECT l.id AS line_id, l.entry_id, e.entry_date, e.reference, e.memo AS entry_memo,
                   l.line_no, l.account_id, l.debit, l.credit, l.currency, l.fx_rate, l.amount_base,
                   l.counterparty_id, l.memo AS line_memo
            FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id
            WHERE l.company_id = ? AND e.company_id = ? AND e.status = 'posted'
            ORDER BY e.entry_date, l.entry_id, l.line_no
            """, [self.company_id, self.company_id])
        df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
        df["native_c"] = cents(df["debit"]) - cents(df["credit"])
        df["base_c"] = cents(df["amount_base"])
        df["fx_rate"] = pd.to_numeric(df["fx_rate"], errors="coerce").fillna(1.0)
        acc = self.accounts
        df["acc_type"] = df["account_id"].map(acc["type"]).fillna("")
        df["acc_subtype"] = df["account_id"].map(acc["subtype"]).fillna("")
        df["acc_name"] = df["account_id"].map(acc["name"]).fillna("")
        df["month"] = [f"{d.year:04d}-{d.month:02d}" for d in df["entry_date"]]
        return df

    def lines_upto(self, as_of: dt.date) -> pd.DataFrame:
        df = self.lines
        return df[df["entry_date"] <= as_of]

    # ------------------------------------------------------------------ statements
    @cached_property
    def statements(self) -> pd.DataFrame:
        df = self.db.query_df(
            """
            SELECT id, channel, account_id, statement_ref, txn_date, amount, currency, fx_rate, amount_base,
                   description, counterparty_text, balance, external_txn_id, fee, source_file, source_row_id
            FROM external_statement_lines WHERE company_id = ?
            """, [self.company_id])
        df["txn_date"] = pd.to_datetime(df["txn_date"]).dt.date
        df["amount_c"] = cents(df["amount"])
        df["fee_c"] = cents(df["fee"])
        df["base_c"] = cents(df["amount_base"])
        df["description"] = df["description"].fillna("").astype(str)
        df["counterparty_text"] = df["counterparty_text"].fillna("").astype(str)
        rowno = pd.to_numeric(df["source_row_id"], errors="coerce").fillna(0)
        df["_order"] = rowno
        df = df.sort_values(["txn_date", "source_file", "_order", "id"], kind="mergesort").reset_index(drop=True)
        df["month"] = [f"{d.year:04d}-{d.month:02d}" for d in df["txn_date"]]
        return df

    # ------------------------------------------------------------------ AR / AP
    @cached_property
    def invoices(self) -> pd.DataFrame:
        df = self.db.query_df(
            """SELECT i.*, COALESCE(p.paid, 0) AS paid, COALESCE(p.paid_base, 0) AS paid_base,
                      p.last_payment, c.name AS customer_name, c.payment_terms_days
               FROM invoices i
               LEFT JOIN (SELECT invoice_id, SUM(amount) AS paid, SUM(amount_base) AS paid_base,
                                 MAX(payment_date) AS last_payment
                          FROM invoice_payments WHERE company_id = ? GROUP BY invoice_id) p ON p.invoice_id = i.id
               LEFT JOIN counterparties c ON c.id = i.counterparty_id
               WHERE i.company_id = ? AND i.status NOT IN ('void', 'draft')""",
            [self.company_id, self.company_id])
        for c in ("issue_date", "due_date", "last_payment"):
            df[c] = pd.to_datetime(df[c]).dt.date
        return df

    @cached_property
    def invoice_payments(self) -> pd.DataFrame:
        df = self.db.query_df("SELECT * FROM invoice_payments WHERE company_id = ?", [self.company_id])
        df["payment_date"] = pd.to_datetime(df["payment_date"]).dt.date
        return df

    @cached_property
    def bills(self) -> pd.DataFrame:
        df = self.db.query_df(
            """SELECT b.*, COALESCE(p.paid, 0) AS paid, COALESCE(p.paid_base, 0) AS paid_base, p.last_payment,
                      c.name AS supplier_name
               FROM bills b
               LEFT JOIN (SELECT bill_id, SUM(amount) AS paid, SUM(amount_base) AS paid_base,
                                 MAX(payment_date) AS last_payment
                          FROM bill_payments WHERE company_id = ? GROUP BY bill_id) p ON p.bill_id = b.id
               LEFT JOIN counterparties c ON c.id = b.counterparty_id
               WHERE b.company_id = ? AND b.status NOT IN ('void', 'draft')""",
            [self.company_id, self.company_id])
        for c in ("issue_date", "due_date", "last_payment"):
            df[c] = pd.to_datetime(df[c]).dt.date
        return df

    @cached_property
    def counterparties(self) -> pd.DataFrame:
        return self.db.query_df("SELECT id, kind, name, payment_terms_days FROM counterparties WHERE company_id = ?",
                                [self.company_id])

    # ------------------------------------------------------------------ FX
    @cached_property
    def _fx(self) -> dict[str, pd.Series]:
        df = self.db.query_df(
            "SELECT rate_date, from_currency, to_currency, rate FROM fx_rates "
            "WHERE (company_id IS NULL OR company_id = ?) AND (to_currency = ? OR from_currency = ?)",
            [self.company_id, self.base, self.base])
        out: dict[str, pd.Series] = {}
        if df.empty:
            return out
        df["rate_date"] = pd.to_datetime(df["rate_date"])
        df["rate"] = pd.to_numeric(df["rate"], errors="coerce")
        direct = df[df["to_currency"] == self.base]
        for ccy, g in direct.groupby("from_currency"):
            out[ccy] = g.groupby("rate_date")["rate"].last().sort_index()
        inverse = df[(df["from_currency"] == self.base) & (df["to_currency"] != self.base)]
        for ccy, g in inverse.groupby("to_currency"):
            if ccy not in out:
                out[ccy] = (1.0 / g.groupby("rate_date")["rate"].last()).sort_index()
        return out

    def fx_rate(self, currency: str, on: dt.date) -> float | None:
        """Base units per 1 unit of `currency` on the latest rate date <= `on` (None if unknown)."""
        if currency == self.base:
            return 1.0
        s = self._fx.get(currency)
        if s is None or s.empty:
            return None
        i = s.index.searchsorted(pd.Timestamp(on), side="right") - 1
        return float(s.iloc[max(i, 0)])

    def fx_series(self, currency: str) -> pd.Series | None:
        return self._fx.get(currency)

    # ------------------------------------------------------------------ helpers
    @property
    def first_date(self) -> dt.date | None:
        df = self.lines
        return df["entry_date"].min() if len(df) else None

    @property
    def last_date(self) -> dt.date | None:
        dates = []
        if len(self.lines):
            dates.append(self.lines["entry_date"].max())
        if len(self.statements):
            dates.append(self.statements["txn_date"].max())
        return max(dates) if dates else None


_NAME_FEE = re.compile(r"\b(charge|charges|fee|fees|commission|processing)\b", re.I)
_NAME_SMALL = re.compile(r"\b(small|petty|sundry|misc|miscellaneous|tea|porter)\b", re.I)


def is_fee_account_name(name: str) -> bool:
    return bool(_NAME_FEE.search(name or ""))


def is_small_expense_account_name(name: str) -> bool:
    return bool(_NAME_SMALL.search(name or ""))
