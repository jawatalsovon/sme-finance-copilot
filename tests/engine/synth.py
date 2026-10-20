"""Load D1's synthetic companies (data/synthetic/<sme>/) into a DuckDB ledger for engine tests.

The engine sees exactly what a real owner's copilot would see:
  * books/*                                   -> the ledger as the owner kept it (with its mistakes)
  * truth/external_statement_lines.csv[.gz]   -> bank / wallet statements and cash counts (reality)
Nothing else from truth/ and nothing from answer_key.json is loaded; the key is only read by
the scoring code (tests/engine/scoring.py).

If D1's own seeder (scripts/data/seed.py) exists, prefer it; this is the fallback used by tests.
"""
from __future__ import annotations

import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path

import pandas as pd

from backend.ledger.db import DATA_TABLES, Database, connect
from backend.ledger.loader import load_frames

ROOT = Path(__file__).resolve().parents[2]
SYN = Path(os.environ.get("SYNTHETIC_DIR", ROOT / "data" / "synthetic"))  # override = a frozen snapshot
SMES = ("bd_grocer", "cn_xborder", "uk_garden")

BOOK_TABLES = ("companies", "accounts", "counterparties", "journal_entries", "journal_lines", "invoices",
               "invoice_payments", "bills", "bill_payments", "inventory_items", "inventory_movements",
               "payroll_runs", "fx_rates", "pos_shifts", "pos_daily_imports")
REALITY_TABLES = ("external_statement_lines", "pos_orders", "pos_order_lines", "pos_payments")  # as scripts/data/seed.py


def available() -> list[str]:
    return [c for c in SMES if (SYN / c / "books").is_dir() and (SYN / c / "answer_key.json").is_file()]


def _read(path_base: Path) -> pd.DataFrame | None:
    for p in (path_base.with_suffix(".csv"), Path(str(path_base) + ".csv.gz")):
        if p.is_file():
            return pd.read_csv(p, dtype={"source_row_id": str, "external_txn_id": str, "statement_ref": str,
                                         "reference": str, "counterparty_text": str}, keep_default_na=True,
                               low_memory=False)
    return None


def company_frames(company_id: str) -> dict[str, pd.DataFrame]:
    base = SYN / company_id
    frames: dict[str, pd.DataFrame] = {}
    for t in BOOK_TABLES:
        df = _read(base / "books" / t)
        if df is not None:
            frames[t] = df
    for t in REALITY_TABLES:
        df = _read(base / "books" / t)
        if df is None:
            df = _read(base / "truth" / t)
        if df is not None:
            frames[t] = df
    esl = frames.get("external_statement_lines")
    settle = None
    if esl is not None and not (esl["channel"] == "pos_settlement").any():  # older data: map the reports here
        settle = settlement_lines(company_id, frames)
    if settle is not None:
        frames["external_statement_lines"] = pd.concat([frames["external_statement_lines"], settle], ignore_index=True)
    for t, df in frames.items():
        if "company_id" not in df.columns and t not in ("fx_rates", "companies"):
            df["company_id"] = company_id
    return frames


# Marketplace / storefront settlement reports -> external_statement_lines (channel pos_settlement).
# Mapping = what a connector would do: one line per settlement row, mapped to the ledger's
# "settlement due" account for that marketplace; the report header becomes the payout line.
_SETTLEMENT_FILES = {  # file prefix -> account code
    "amazon_style_settlement_US_": "1210",
    "amazon_style_settlement_UK_": "1211",
    "amazon_style_settlement_EU_": "1212",
    "storefront_payout_report_": "1213",
}


def settlement_lines(company_id: str, frames: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    src = SYN / company_id / "sources"
    files = sorted(p for p in src.glob("*.csv") if any(p.name.startswith(k) for k in _SETTLEMENT_FILES))
    if not files:
        return None
    acc = frames["accounts"]
    code_to_id = dict(zip(acc["code"].astype(str), acc["id"]))
    fx = frames.get("fx_rates")
    base = str(frames["companies"]["base_currency"].iloc[0])
    rates: dict[str, pd.Series] = {}
    if fx is not None:
        for ccy, g in fx[fx["to_currency"] == base].groupby("from_currency"):
            rates[ccy] = g.assign(d=pd.to_datetime(g["rate_date"])).set_index("d")["rate"].sort_index()
    out = []
    for p in files:
        code = next(v for k, v in _SETTLEMENT_FILES.items() if p.name.startswith(k))
        d = pd.read_csv(p, dtype=str)
        for i, r in d.iterrows():
            sid = r["settlement-id"]
            if isinstance(r.get("transaction-type"), str):
                amount = float(r["amount"])
                date = str(r["posted-date"])[:10]
                desc = f'{r["transaction-type"]}: {r["amount-description"]}'
            else:  # header row = the payout to the bank (open settlements have no total yet)
                if not isinstance(r.get("total-amount"), str):
                    continue
                amount = -float(r["total-amount"])
                date = str(r["deposit-date"])[:10]
                desc = "Payout to bank account"
            ccy = r["currency"]
            rate = 1.0
            if ccy != base and ccy in rates:
                s = rates[ccy]
                s = s[s.index <= pd.Timestamp(date)]
                rate = float(s.iloc[-1]) if len(s) else float(rates[ccy].iloc[0])
            out.append({
                "id": f"stl-{company_id}-{p.stem}-{i + 2}", "company_id": company_id, "channel": "pos_settlement",
                "account_id": code_to_id[code], "statement_ref": str(r.get("marketplace-name") or "") or None,
                "txn_date": date, "value_date": date, "amount": round(amount, 2), "currency": ccy, "fx_rate": rate,
                "amount_base": round(amount * rate, 2), "description": desc,
                "counterparty_text": r.get("marketplace-name") if isinstance(r.get("marketplace-name"), str) else None,
                "balance": None, "external_txn_id": sid, "fee": 0.0,
                "source_system": "marketplace_csv", "source_file": p.name, "source_row_id": str(i + 2),
            })
    return pd.DataFrame(out)


def load_company(db: Database, company_id: str) -> dict:
    frames = company_frames(company_id)
    frames = {t: frames[t] for t in DATA_TABLES if t in frames}
    reports = load_frames(db, frames, if_exists="skip", scan_injection=False)
    bad = {t: r.rejected_count for t, r in reports.items() if r.rejected_count}
    if bad:
        raise RuntimeError(f"{company_id}: rows rejected while loading: {bad}")
    return {t: r.inserted for t, r in reports.items()}


def _cache_path(company_id: str) -> Path:
    d = Path(os.environ.get("ENGINE_TEST_CACHE", Path(tempfile.gettempdir()) / "sme_engine_cache"))
    d.mkdir(parents=True, exist_ok=True)
    stamp = 0.0
    for p in (SYN / company_id).rglob("*"):
        if p.is_file():
            stamp = max(stamp, p.stat().st_mtime)
    return d / f"{company_id}-{int(stamp)}.duckdb"


def build_db(company_id: str) -> Database:
    """Fresh on-disk DuckDB with one synthetic company (cached between test runs)."""
    path = _cache_path(company_id)
    if not path.exists():
        # each process builds into its own temp file, so parallel test runs never share a writable file
        tmp = path.with_suffix(f".{os.getpid()}.building")
        if tmp.exists():
            tmp.unlink()
        db = connect(str(tmp))
        load_company(db, company_id)
        db.close()
        try:
            if path.exists():          # another process finished first: use its copy
                tmp.unlink()
            else:
                tmp.replace(path)
        except OSError:                # lost the race while the other copy is open: theirs is identical
            tmp.unlink(missing_ok=True)
    return _open_read_only(path)


def _open_read_only(path: Path) -> Database:
    """Read-only connection, so several test processes (and the engine) can share the cached file."""
    import duckdb

    db = Database(":memory:")
    db._conn.close()
    db._conn = duckdb.connect(str(path), read_only=True)
    db.path = str(path)
    return db


@lru_cache(maxsize=4)
def answer_key(company_id: str) -> dict:
    return json.loads((SYN / company_id / "answer_key.json").read_text(encoding="utf-8"))
