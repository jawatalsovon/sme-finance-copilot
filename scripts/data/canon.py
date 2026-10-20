"""Canonical-table output: truth/ and books/ folders in the exact column layout of backend/ledger/schema.sql."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from core import ROOT, Book, Entry, World, money, write_csv

_COLS: dict[str, list[str]] = {}


def schema_columns() -> dict[str, list[str]]:
    """Column order per table, parsed from backend/ledger/schema.sql (so we never drift from A1's contract)."""
    if _COLS:
        return _COLS
    sql = (ROOT / "backend" / "ledger" / "schema.sql").read_text(encoding="utf-8")
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
        cols = []
        for line in m.group(2).splitlines():
            line = re.sub(r"--.*", "", line).strip()
            if not line or line.startswith(("PRIMARY", "CHECK", "FOREIGN", "UNIQUE")):
                continue
            for part in re.split(r",\s*(?=[a-z_]+\s+[A-Z])", line):
                mm = re.match(r"^([a-z_]+)\s+[A-Z]", part.strip())
                if mm:
                    cols.append(mm.group(1))
        _COLS[m.group(1)] = cols
    return _COLS


LINEAGE_TAIL = ("ingested_at", "ingest_batch_id")  # stamped by the loader; not written to the CSVs
BIG = {"journal_lines", "journal_entries", "external_statement_lines", "pos_orders", "pos_order_lines", "pos_payments", "invoices", "invoice_payments"}


class Tables:
    def __init__(self) -> None:
        self.t: dict[str, list[dict]] = defaultdict(list)

    def add(self, table: str, **row) -> None:
        self.t[table].append(row)

    def write(self, outdir: Path, only: set[str] | None = None) -> dict[str, int]:
        cols = schema_columns()
        counts = {}
        for name, rows in sorted(self.t.items()):
            if only and name not in only:
                continue
            header = [c for c in cols[name] if c not in LINEAGE_TAIL]
            bad = {k for r in rows for k in r} - set(cols[name])
            if bad:
                raise ValueError(f"{name}: unknown columns {bad}")
            gz = name in BIG and len(rows) > 5000
            fn = outdir / (f"{name}.csv.gz" if gz else f"{name}.csv")
            counts[name] = write_csv(fn, header, ([_cell(r.get(c)) for c in header] for r in rows), gz=gz)
        return counts


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def acct_id(cid: str, w: World, key: str) -> str:
    return f"{cid}-{w.accts[key].code}"


def add_accounts(T: Tables, w: World, source_system: str, source_file: str) -> None:
    for a in w.accts.values():
        T.add("accounts", id=acct_id(w.cid, w, a.key), company_id=w.cid, code=a.code, name=a.name, type=a.type, subtype=a.subtype,
              currency=a.ccy, is_active=True, source_system=source_system, source_file=source_file, source_row_id=a.code)


def add_journal(T: Tables, w: World, entries: list[Entry], *, prefix: str, lineage, status: str = "posted") -> None:
    """lineage(entry) -> (source_system, source_file, source_row_id)."""
    for e in sorted(entries, key=lambda x: (x.date, x.id)):
        ss, sf, sr = lineage(e)
        T.add("journal_entries", id=e.id, company_id=w.cid, entry_date=e.date, reference=e.ref or None, memo=e.memo or None, status=status,
              origin="imported", created_by=None, source_system=ss, source_file=sf, source_row_id=sr)
        for k, l in enumerate(e.lines, 1):
            T.add("journal_lines", id=f"{prefix}-{e.id.split('-', 1)[1]}-{k}", entry_id=e.id, company_id=w.cid, line_no=k,
                  account_id=acct_id(w.cid, w, l.acct), debit=money(l.dr), credit=money(l.cr), currency=l.ccy, fx_rate=f"{l.fx:.8f}",
                  amount_base=money(l.base), counterparty_id=l.cp, memo=l.memo, source_system=ss, source_file=sf,
                  source_row_id=f"{sr}#{k}")
