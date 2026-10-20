"""Read helpers used by the API and the agent's T0 tools.

These are plain deterministic SQL lookups/sums over posted rows. Business analysis
(Cash Truth, P&L, forecasts ...) lives in backend/engine, not here.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from backend.ledger.db import Database

CASH_SUBTYPES = ("cash", "bank", "wallet")


def list_companies(db: Database) -> list[dict]:
    return db.query_dicts(
        "SELECT id, name, country, base_currency, industry, fiscal_year_start_month FROM companies ORDER BY name"
    )


def get_company(db: Database, company_id: str) -> dict | None:
    return db.query_one("SELECT * FROM companies WHERE id = ?", [company_id])


def list_accounts(db: Database, company_id: str, subtypes: list[str] | None = None) -> list[dict]:
    sql = "SELECT id, code, name, type, subtype, currency FROM accounts WHERE company_id = ? AND is_active"
    params: list = [company_id]
    if subtypes:
        sql += " AND subtype IN (SELECT UNNEST(?))"
        params.append(list(subtypes))
    return db.query_dicts(sql + " ORDER BY code, name", params)


def account_balances(
    db: Database,
    company_id: str,
    as_of: dt.date,
    subtypes: list[str] | None = None,
    evidence_limit: int = 25,
) -> list[dict]:
    """Book balance per account in base currency from POSTED journal lines up to as_of.

    Each row: id, code, name, type, subtype, currency, balance_base (Decimal),
    line_count, evidence_line_ids (most recent first, capped at evidence_limit).
    """
    sql = """
        WITH lines AS (
            SELECT jl.id, jl.account_id, jl.amount_base, je.entry_date
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.entry_id AND je.company_id = jl.company_id
            WHERE jl.company_id = ? AND je.status = 'posted' AND je.entry_date <= ?
        )
        SELECT a.id, a.code, a.name, a.type, a.subtype, a.currency,
               COALESCE(SUM(l.amount_base), 0) AS balance_base,
               COUNT(l.id) AS line_count,
               list(l.id ORDER BY l.entry_date DESC, l.id) FILTER (WHERE l.id IS NOT NULL) AS line_ids
        FROM accounts a
        LEFT JOIN lines l ON l.account_id = a.id
        WHERE a.company_id = ?
    """
    params: list = [company_id, as_of, company_id]
    if subtypes:
        sql += " AND a.subtype IN (SELECT UNNEST(?))"
        params.append(list(subtypes))
    sql += " GROUP BY ALL ORDER BY a.code, a.name"
    out = []
    for r in db.query_dicts(sql, params):
        ids = r.pop("line_ids") or []
        r["balance_base"] = Decimal(str(r["balance_base"]))
        r["evidence_line_ids"] = list(ids)[:evidence_limit]
        out.append(r)
    return out


def search_journal_lines(
    db: Database,
    company_id: str,
    text: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    account_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    sql = """
        SELECT jl.id, je.entry_date, je.reference, jl.account_id, a.name AS account_name,
               jl.debit, jl.credit, jl.currency, jl.amount_base,
               COALESCE(jl.memo, je.memo) AS memo, jl.counterparty_id, je.status
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        LEFT JOIN accounts a ON a.id = jl.account_id
        WHERE jl.company_id = ?
    """
    params: list = [company_id]
    if text:
        sql += " AND (COALESCE(jl.memo,'') ILIKE ? OR COALESCE(je.memo,'') ILIKE ? OR COALESCE(je.reference,'') ILIKE ?)"
        params += [f"%{text}%"] * 3
    if date_from:
        sql += " AND je.entry_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND je.entry_date <= ?"
        params.append(date_to)
    if account_id:
        sql += " AND jl.account_id = ?"
        params.append(account_id)
    sql += " ORDER BY je.entry_date DESC, jl.id LIMIT ?"
    params.append(int(limit))
    return db.query_dicts(sql, params)


def search_statement_lines(
    db: Database,
    company_id: str,
    text: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    channel: str | None = None,
    limit: int = 20,
) -> list[dict]:
    sql = """
        SELECT id, channel, account_id, txn_date, amount, currency, amount_base,
               description, counterparty_text, balance
        FROM external_statement_lines WHERE company_id = ?
    """
    params: list = [company_id]
    if text:
        sql += " AND (COALESCE(description,'') ILIKE ? OR COALESCE(counterparty_text,'') ILIKE ?)"
        params += [f"%{text}%"] * 2
    if date_from:
        sql += " AND txn_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND txn_date <= ?"
        params.append(date_to)
    if channel:
        sql += " AND channel = ?"
        params.append(channel)
    sql += " ORDER BY txn_date DESC, id LIMIT ?"
    params.append(int(limit))
    return db.query_dicts(sql, params)


def unbalanced_entries(db: Database, company_id: str, tolerance: float = 0.01) -> list[dict]:
    """Journal entries whose lines do not sum to zero in base currency (data-quality check)."""
    return db.query_dicts(
        """
        SELECT entry_id, SUM(amount_base) AS imbalance, COUNT(*) AS lines
        FROM journal_lines WHERE company_id = ?
        GROUP BY entry_id HAVING ABS(SUM(amount_base)) > ?
        ORDER BY entry_id
        """,
        [company_id, tolerance],
    )


def employee_names(db: Database, company_id: str | None = None) -> list[str]:
    sql = "SELECT DISTINCT name FROM counterparties WHERE kind = 'employee'"
    params: list = []
    if company_id:
        sql += " AND company_id = ?"
        params.append(company_id)
    return [r["name"] for r in db.query_dicts(sql, params) if r["name"]]
