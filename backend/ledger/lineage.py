"""Evidence references and lineage lookup.

An evidence reference is the string "<table>:<id>", e.g. "journal_lines:jl-3f2a9c01".
Every engine figure and every agent tool result lists the references of the rows it was
computed from; `get_source_rows` turns them back into the original rows + lineage.
"""
from __future__ import annotations

import re
from collections import OrderedDict

from backend.core.ids import ID_PATTERN
from backend.ledger.db import DATA_TABLES, DERIVED_VIEWS, Database

CITABLE = DATA_TABLES + DERIVED_VIEWS
# Longest names first so e.g. pos_order_lines is never cut short.
EVIDENCE_REF = re.compile(r"\b(" + "|".join(sorted(CITABLE, key=len, reverse=True)) + r"):([A-Za-z0-9_.\-]+)")


def make_ref(table: str, row_id: str) -> str:
    if table not in CITABLE:
        raise ValueError(f"unknown table for evidence ref: {table}")
    if not ID_PATTERN.match(str(row_id)):
        raise ValueError(f"invalid row id for evidence ref: {row_id!r}")
    return f"{table}:{row_id}"


def refs(table: str, ids) -> list[str]:
    return [make_ref(table, str(i)) for i in ids]


def parse_ref(ref: str) -> tuple[str, str]:
    table, _, row_id = ref.partition(":")
    if table not in CITABLE or not ID_PATTERN.match(row_id or ""):
        raise ValueError(f"invalid evidence ref: {ref!r}")
    return table, row_id


def extract_refs(text: str) -> list[str]:
    """All well-formed evidence refs found in free text, in order, de-duplicated."""
    seen: OrderedDict[str, None] = OrderedDict()
    for m in EVIDENCE_REF.finditer(text or ""):
        seen[f"{m.group(1)}:{m.group(2)}"] = None
    return list(seen)


def get_source_rows(db: Database, evidence: list[str], company_id: str | None = None) -> dict:
    """Resolve evidence refs to rows.

    Returns {"rows": [{"ref", "table", "row": {...incl. lineage}}], "missing": [refs]}.
    If company_id is given, rows of other companies are reported as missing (tenant isolation).
    """
    by_table: dict[str, list[str]] = {}
    invalid = []
    for ref in evidence:
        try:
            t, i = parse_ref(ref)
        except ValueError:
            invalid.append(ref)
            continue
        by_table.setdefault(t, []).append(i)

    found: dict[str, dict] = {}
    for table, ids in by_table.items():
        cols = {c[0] for c in db.table_columns(table)}
        sql = f"SELECT * FROM {table} WHERE id IN (SELECT UNNEST(?))"
        params: list = [ids]
        if company_id is not None and "company_id" in cols and table != "companies":
            sql += " AND (company_id = ? OR company_id IS NULL)" if table == "fx_rates" else " AND company_id = ?"
            params.append(company_id)
        elif company_id is not None and table == "companies":
            sql += " AND id = ?"
            params.append(company_id)
        for row in db.query_dicts(sql, params):
            found[f"{table}:{row['id']}"] = {k: _jsonable(v) for k, v in row.items()}

    rows = []
    missing = list(invalid)
    for ref in evidence:
        if ref in found:
            t, _ = parse_ref(ref)
            rows.append({"ref": ref, "table": t, "row": found[ref]})
        elif ref not in invalid:
            missing.append(ref)
    return {"rows": rows, "missing": missing}


def _jsonable(v):
    from decimal import Decimal
    import datetime as dt

    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v
