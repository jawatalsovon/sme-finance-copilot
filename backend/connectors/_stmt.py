"""Build canonical `external_statement_lines` from a mapped table (bank, wallet, cash count, settlement)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Optional

import pandas as pd

from backend.connectors._fx import FxResolver
from backend.connectors._util import (clean, first_present, header_currency, mask_account, money_str,
                                      parse_date, to_decimal)
from backend.core.ids import make_id

CHANNELS = ("bank", "wallet", "cash_count", "pos_settlement")


def _columns(df: pd.DataFrame, spec: Any) -> list[str]:
    """Columns of df that `spec` (a header or a list of headers: coalesce) refers to, resolved once per frame."""
    key = spec if isinstance(spec, str) else tuple(spec or ())
    cache = df.attrs.setdefault("_colcache", {})
    if key not in cache:
        names = [spec] if isinstance(spec, str) else list(spec or [])
        cache[key] = [c for c in (first_present(df, n) for n in names) if c is not None]
    return cache[key]


def _pick(row: Any, df: pd.DataFrame, spec: Any) -> Any:
    """Value of the first non-blank column named in `spec`. `row` is a dict (a df.to_dict("records") item)."""
    for col in _columns(df, spec):
        v = row.get(col)
        if clean(v) is not None:
            return v
    return None


def build_statement_lines(
    df: pd.DataFrame, columns: dict[str, Any], options: dict[str, Any], *, company_id: str,
    source_system: str, source_file: str, fx: FxResolver, default_currency: Optional[str] = None,
    account_id: Optional[str] = None, statement_ref: Optional[str] = None,
    since: Optional[dt.date] = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Map rows to statement lines. Returns (frame, warnings). Rows that cannot be read (no date or no
    amount) are NOT dropped silently: they are counted in a warning with the first row numbers."""
    warnings: list[str] = []
    opts = options or {}
    layout = opts.get("layout", "movements")
    channel = opts.get("channel", "bank")
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    dayfirst = bool(opts.get("dayfirst", True))
    mode = opts.get("amount_mode", "signed")
    ccy_default = (opts.get("currency") or default_currency or "").upper() or None
    if not ccy_default and not columns.get("currency"):
        for key in ("amount", "credit", "debit", "money_in", "money_out"):
            col = first_present(df, columns.get(key))
            if col and header_currency(col):
                ccy_default = header_currency(col)
                break
    if layout == "settlement":
        recs, bad = _settlement_records(df, columns, dayfirst, opts, warnings)
    else:
        recs, bad = _movement_records(df, columns, dayfirst, mode, channel, opts)
    out: list[dict[str, Any]] = []
    missing_fx: dict[str, int] = {}
    for r in recs:
        if since and r["date"] < since:
            continue
        ccy = (r.get("currency") or ccy_default or "").upper()
        if not ccy:
            raise ValueError("currency unknown: map a currency column or set config['currency']")
        rate = fx.rate(ccy, r["date"])
        if rate is None:
            missing_fx[ccy] = missing_fx.get(ccy, 0) + 1
            continue
        amt: Decimal = r["amount"]
        fee = r.get("fee")
        out.append({
            "id": make_id("ext", company_id, source_system, source_file, r["row"], r.get("sub", "")),
            "company_id": company_id, "channel": channel, "account_id": account_id,
            "statement_ref": r.get("statement_ref") or statement_ref,
            "txn_date": r["date"], "value_date": r.get("value_date"),
            "amount": money_str(amt), "currency": ccy, "fx_rate": str(rate),
            "amount_base": money_str(amt * rate), "description": r.get("description"),
            "counterparty_text": r.get("counterparty"), "balance": money_str(r.get("balance")),
            "external_txn_id": r.get("ref"), "fee": money_str(abs(fee) if fee is not None else Decimal(0)),
            "source_system": source_system, "source_file": source_file, "source_row_id": str(r["row"]),
        })
    if missing_fx:
        raise ValueError(f"no FX rate to the base currency for {sorted(missing_fx)} "
                         f"({sum(missing_fx.values())} rows); provide config['fx_rates'] or load fx_rates first")
    warnings += _continuity_warning(out, source_file)
    if bad:
        warnings.append(f"{source_file}: {len(bad)} rows skipped (no readable date/amount), first source rows: {bad[:5]}")
    return pd.DataFrame(out), warnings


def _movement_records(df, columns, dayfirst, mode, channel, opts):
    recs, bad = [], []
    for row in df.to_dict("records"):
        n = int(row["_row"])
        d = parse_date(_pick(row, df, columns.get("date")), dayfirst=dayfirst)
        if channel == "cash_count":
            amt = to_decimal(_pick(row, df, columns.get("amount") or columns.get("balance")))
            if d is None or amt is None:
                bad.append(n)
                continue
            recs.append({"row": n, "date": d, "amount": amt, "balance": amt, "ref": f"COUNT{d:%Y%m%d}",
                         "description": opts.get("description", "Month-end cash count"),
                         "currency": clean(_pick(row, df, columns.get("currency")))})
            continue
        if mode == "split":
            cr = to_decimal(_pick(row, df, columns.get("credit") or columns.get("money_in")))
            dr = to_decimal(_pick(row, df, columns.get("debit") or columns.get("money_out")))
            amt = None if cr is None and dr is None else (cr or Decimal(0)) - (dr or Decimal(0))
            if opts.get("credit_is_out"):            # ledger-style export (debit = money in)
                amt = -amt if amt is not None else None
        else:
            amt = to_decimal(_pick(row, df, columns.get("amount")))
        if d is None or amt is None:
            bad.append(n)
            continue
        fee = to_decimal(_pick(row, df, columns.get("fee")))
        if opts.get("fee_deducted") and fee:         # wallet: the fee leaves the balance on top of the amount
            amt -= abs(fee)
        recs.append({
            "row": n, "date": d,
            "value_date": parse_date(_pick(row, df, columns.get("value_date")), dayfirst=dayfirst) or d,
            "amount": amt, "balance": to_decimal(_pick(row, df, columns.get("balance"))),
            "fee": fee,
            "description": clean(_pick(row, df, columns.get("description"))) or opts.get("description"),
            "counterparty": clean(_pick(row, df, columns.get("counterparty"))),
            "ref": clean(_pick(row, df, columns.get("external_txn_id") or columns.get("reference"))),
            "currency": clean(_pick(row, df, columns.get("currency"))),
        })
    return recs, bad


def _settlement_records(df, columns, dayfirst, opts, warnings):
    """Amazon-style settlement report: one summary row per settlement (total-amount = payout to the bank)
    followed by detail rows (sales, fees, refunds ...). Every row becomes a line on the settlement
    clearing account; the payout is negative, so a closed settlement nets to zero. An open settlement
    (blank total) has no payout line yet."""
    recs, bad = [], []
    open_ids: set[str] = set()
    for row in df.to_dict("records"):
        n = int(row["_row"])
        sid = clean(_pick(row, df, columns.get("settlement_id")))
        ttype = clean(_pick(row, df, columns.get("type")))
        ccy = clean(_pick(row, df, columns.get("currency")))
        if ttype is None:                              # summary row
            total = to_decimal(_pick(row, df, columns.get("total_amount")))
            dep = parse_date(_pick(row, df, columns.get("deposit_date")), dayfirst=False)
            if total is None or dep is None:
                open_ids.add(sid or "?")
                continue
            recs.append({"row": n, "date": dep, "amount": -total, "ref": sid, "statement_ref": sid,
                         "description": opts.get("payout_description", "Payout to bank account"), "currency": ccy})
            continue
        d = parse_date(_pick(row, df, columns.get("posted_date")), dayfirst=False)
        amt = to_decimal(_pick(row, df, columns.get("amount")))
        if d is None or amt is None:
            bad.append(n)
            continue
        adesc = clean(_pick(row, df, columns.get("description")))
        recs.append({"row": n, "date": d, "amount": amt, "ref": sid, "statement_ref": sid,
                     "description": f"{ttype}: {adesc}" if adesc else ttype, "currency": ccy})
    if open_ids:
        warnings.append(f"open settlement(s) without a payout total: {sorted(open_ids)} (detail rows kept, payout line pending)")
    return recs, bad


def _continuity_warning(rows: list[dict[str, Any]], source_file: str) -> list[str]:
    """Running-balance check: previous balance + amount should equal the next balance. A break means a missing,
    duplicated or mis-read row - exactly what the owner needs to know before trusting the statement."""
    breaks, prev, checked = [], None, 0
    for r in rows:
        if r["balance"] is None or r["channel"] == "cash_count":
            prev = None
            continue
        bal, amt = Decimal(r["balance"]), Decimal(r["amount"])
        if prev is not None:
            checked += 1
            if prev + amt != bal:
                breaks.append(r["source_row_id"])
        prev = bal
    if breaks:
        return [f"{source_file}: running balance does not follow from the amounts at {len(breaks)} of {checked} rows "
                f"(first source rows: {breaks[:5]}) - a row may be missing, duplicated or mis-read"]
    return []
