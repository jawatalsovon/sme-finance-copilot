"""Bulk load pandas DataFrames into canonical tables, with lineage validation.

    report = load_dataframe(db, "journal_lines", df, company_id="acme", source_system="bank_csv")

Rules
  * Unknown columns -> ValueError (whole frame refused: a mapping bug, not a data issue).
  * Rows missing source_system / source_file / source_row_id -> rejected (not inserted),
    returned in report.rejected with a reason.
  * Rows missing a NOT NULL column, or with an id not matching ID_PATTERN -> rejected.
  * ingested_at and ingest_batch_id are stamped by the loader.
  * Text columns are scanned by the prompt-injection guard; flagged cells are reported
    (they are still loaded - they are data, and the guard wraps them when shown to the LLM).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backend.core.ids import ID_PATTERN, new_id
from backend.core.util import utc_now
from backend.ledger.db import DATA_TABLES, REQUIRED_LINEAGE, Database

# Free-text columns that come from outside and may carry injected instructions.
UNTRUSTED_TEXT_COLUMNS = ("memo", "description", "counterparty_text", "name", "reference")


class LineageError(ValueError):
    """Raised when a frame cannot be loaded at all (e.g. no lineage columns)."""


@dataclass
class LoadReport:
    table: str
    batch_id: str
    received: int
    inserted: int
    rejected: pd.DataFrame = field(default_factory=pd.DataFrame)  # rows + 'reject_reason'
    injection_flags: list[dict] = field(default_factory=list)  # {row_id, column, patterns}
    skipped_existing: int = 0

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def as_dict(self) -> dict:
        return {
            "table": self.table,
            "batch_id": self.batch_id,
            "received": self.received,
            "inserted": self.inserted,
            "rejected": self.rejected_count,
            "skipped_existing": self.skipped_existing,
            "reject_reasons": self.rejected["reject_reason"].value_counts().to_dict() if self.rejected_count else {},
            "injection_flags": len(self.injection_flags),
        }


def _blank(series: pd.Series) -> pd.Series:
    return series.isna() | (series.astype("string").str.strip() == "")


def load_dataframe(
    db: Database,
    table: str,
    df: pd.DataFrame,
    *,
    company_id: str | None = None,
    source_system: str | None = None,
    source_file: str | None = None,
    connector_id: str | None = None,
    batch_id: str | None = None,
    if_exists: str = "error",
    scan_injection: bool = True,
) -> LoadReport:
    """Validate and insert `df` into `table`.

    company_id / source_system / source_file, when given, fill columns that are absent
    from the frame (they never overwrite values already present).
    if_exists: 'error' (duplicate id raises) or 'skip' (rows whose id exists are ignored).
    """
    if table not in DATA_TABLES:
        raise ValueError(f"not a canonical data table: {table}")
    if if_exists not in ("error", "skip"):
        raise ValueError("if_exists must be 'error' or 'skip'")

    cols = db.table_columns(table)
    col_names = [c[0] for c in cols]
    col_types = {c[0]: c[1] for c in cols}
    not_null = {c[0] for c in cols if c[2]}

    frame = df.copy()
    defaults = {"company_id": company_id, "source_system": source_system, "source_file": source_file}
    for col, val in defaults.items():
        if val is not None and col in col_names and col not in frame.columns:
            frame[col] = val

    unknown = [c for c in frame.columns if c not in col_names]
    if unknown:
        raise ValueError(f"{table}: unknown columns {unknown}; allowed: {col_names}")
    missing_lineage_cols = [c for c in REQUIRED_LINEAGE if c not in frame.columns]
    if missing_lineage_cols:
        raise LineageError(f"{table}: frame has no lineage columns {missing_lineage_cols}")

    batch_id = batch_id or new_id("batch")
    now = utc_now().replace(tzinfo=None)
    frame["ingested_at"] = now
    frame["ingest_batch_id"] = batch_id

    reasons = pd.Series("", index=frame.index, dtype="object")

    def add_reason(mask: pd.Series, text: str) -> None:
        reasons.loc[mask & (reasons == "")] = text

    for col in REQUIRED_LINEAGE:
        add_reason(_blank(frame[col]), f"missing lineage: {col}")
    # NOT NULL columns with a schema default (fx_rate = 1, channel = 'counter' ...) may be absent
    # or partly empty: gaps get the default. Lineage columns have no default, so they still reject.
    defaults = db.columns_with_default(table)
    for col, value in defaults.items():
        if col in frame.columns and col in not_null and frame[col].isna().any():
            frame[col] = frame[col].astype(object).where(frame[col].notna(), value)
    for col in sorted(not_null - set(frame.columns) - set(defaults)):
        add_reason(pd.Series(True, index=frame.index), f"missing required column: {col}")
    for col in sorted(not_null & set(frame.columns)):
        add_reason(frame[col].isna(), f"null in required column: {col}")
    if "id" in frame.columns:
        bad_id = ~frame["id"].astype("string").fillna("").str.match(ID_PATTERN.pattern)
        add_reason(bad_id, "invalid id (allowed: letters, digits, _ . -)")
        dup = frame["id"].duplicated(keep="first")
        add_reason(dup, "duplicate id within frame")

    rejected_mask = reasons != ""
    rejected = frame[rejected_mask].copy()
    rejected["reject_reason"] = reasons[rejected_mask]
    good = frame[~rejected_mask]

    flags: list[dict] = []
    if scan_injection and len(good):
        from backend.security.injection import scan_text

        for col in UNTRUSTED_TEXT_COLUMNS:
            if col not in good.columns:
                continue
            for idx, val in good[col].items():
                if isinstance(val, str) and val:
                    hits = scan_text(val)
                    if hits:
                        flags.append({
                            "table": table,
                            "row_id": str(good.at[idx, "id"]) if "id" in good.columns else str(idx),
                            "column": col,
                            "patterns": hits,
                        })

    inserted = 0
    skipped = 0
    with db.locked() as conn:
        conn.execute(
            "INSERT INTO ingest_batches (id, company_id, source_system, source_file, connector_id, "
            "target_table, started_at, rows_received, status) VALUES (?,?,?,?,?,?,?,?, 'running')",
            [batch_id, company_id, source_system or _first(frame, "source_system"),
             source_file or _first(frame, "source_file"), connector_id, table, now, len(frame)],
        )
        if len(good):
            if if_exists == "skip" and "id" in good.columns:
                existing = {
                    r[0] for r in conn.execute(
                        f"SELECT id FROM {table} WHERE id IN (SELECT UNNEST(?))", [good["id"].astype(str).tolist()]
                    ).fetchall()
                }
                if existing:
                    skipped = int(good["id"].isin(existing).sum())
                    good = good[~good["id"].isin(existing)]
            if len(good):
                insert_cols = list(good.columns)
                select = ", ".join(f'CAST("{c}" AS {col_types[c]}) AS "{c}"' for c in insert_cols)
                conn.register("_incoming", good)
                try:
                    conn.execute(
                        f'INSERT INTO {table} ({", ".join(chr(34) + c + chr(34) for c in insert_cols)}) '
                        f"SELECT {select} FROM _incoming"
                    )
                finally:
                    conn.unregister("_incoming")
                inserted = len(good)
        status = "ok" if not len(rejected) else ("partial" if inserted else "failed")
        conn.execute(
            "UPDATE ingest_batches SET finished_at = ?, rows_inserted = ?, rows_rejected = ?, status = ? WHERE id = ?",
            [utc_now().replace(tzinfo=None), inserted, len(rejected), status, batch_id],
        )

    return LoadReport(table=table, batch_id=batch_id, received=len(frame), inserted=inserted,
                      rejected=rejected, injection_flags=flags, skipped_existing=skipped)


def load_frames(db: Database, frames: dict[str, pd.DataFrame], **kwargs) -> dict[str, LoadReport]:
    """Load several canonical tables in DATA_TABLES order (e.g. a connector PullResult)."""
    reports = {}
    for table in DATA_TABLES:
        if table in frames and frames[table] is not None and len(frames[table]):
            reports[table] = load_dataframe(db, table, frames[table], **kwargs)
    return reports


def _first(frame: pd.DataFrame, col: str) -> str | None:
    if col in frame.columns and len(frame):
        v = frame[col].iloc[0]
        return None if pd.isna(v) else str(v)
    return None
