"""DuckDB connection wrapper.

One `Database` per process. DuckDB connections are not safe for concurrent use, so all
access goes through a re-entrant lock (FastAPI runs sync endpoints in a thread pool).
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

import duckdb
import pandas as pd

LEDGER_DIR = Path(__file__).resolve().parent
SCHEMA_FILES = (LEDGER_DIR / "schema.sql", LEDGER_DIR / "system_schema.sql")

LINEAGE_COLUMNS = ("source_system", "source_file", "source_row_id", "ingested_at", "ingest_batch_id")
# Lineage fields the caller must supply; the loader stamps ingested_at and ingest_batch_id.
REQUIRED_LINEAGE = ("source_system", "source_file", "source_row_id")

# Canonical data tables (all carry lineage). Order = a sensible load order.
DATA_TABLES = (
    "companies",
    "accounts",
    "counterparties",
    "journal_entries",
    "journal_lines",
    "invoices",
    "invoice_payments",
    "bills",
    "bill_payments",
    "external_statement_lines",
    "inventory_items",
    "inventory_movements",
    "payroll_runs",
    "fx_rates",
    "pos_shifts",
    "pos_orders",
    "pos_order_lines",
    "pos_payments",
    "pos_daily_imports",
)
# Read-only derived views. They can be cited as evidence ("pos_sales_daily:<id>") but not loaded.
DERIVED_VIEWS = ("pos_sales_daily",)
SYSTEM_TABLES = (
    "ingest_batches",
    "audit_log",
    "audit_head",
    "approvals",
    "company_permissions",
    "llm_usage",
    "reminders",
)


class Database:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._conn = duckdb.connect(path)
        self._lock = threading.RLock()
        self._columns: dict[str, list[tuple[str, str, bool]]] = {}
        self._defaults: dict[str, dict[str, Any]] = {}

    # -- low level ---------------------------------------------------------
    @contextmanager
    def locked(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with self._lock:
            yield self._conn

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        with self._lock:
            self._conn.execute(sql, params or [])

    def query_df(self, sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
        with self._lock:
            return self._conn.execute(sql, params or []).df()

    def query_dicts(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] | None = None) -> dict | None:
        rows = self.query_dicts(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params or []).fetchone()
            return row[0] if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- schema ------------------------------------------------------------
    def init_schema(self) -> None:
        """Create all canonical + system tables (idempotent)."""
        with self._lock:
            for f in SCHEMA_FILES:
                self._conn.execute(f.read_text(encoding="utf-8"))
            self._columns.clear()
            self._defaults.clear()

    def table_columns(self, table: str) -> list[tuple[str, str, bool]]:
        """[(column, duckdb_type, not_null)] for a known table."""
        if table not in DATA_TABLES and table not in SYSTEM_TABLES and table not in DERIVED_VIEWS:
            raise ValueError(f"unknown table: {table}")
        if table not in self._columns:
            rows = self.query_dicts(
                "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position",
                [table],
            )
            self._columns[table] = [(r["column_name"], r["data_type"], r["is_nullable"] == "NO") for r in rows]
            defaults = {}
            for r in rows:
                if r["column_default"] is not None:
                    # Evaluate the schema's default expression once (constants only, e.g. 'counter', 0, 1).
                    defaults[r["column_name"]] = self.scalar(f"SELECT {r['column_default']}")
            self._defaults[table] = defaults
        return self._columns[table]

    def columns_with_default(self, table: str) -> dict[str, Any]:
        """{column: default value} for columns whose schema declares a DEFAULT."""
        self.table_columns(table)
        return self._defaults[table]


def connect(path: str = ":memory:", init: bool = True) -> Database:
    db = Database(path)
    if init:
        db.init_schema()
    return db
