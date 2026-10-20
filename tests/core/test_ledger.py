import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest

from backend.ledger import DATA_TABLES, LINEAGE_COLUMNS, LineageError, connect, get_source_rows, load_dataframe
from backend.ledger import queries
from backend.ledger.dev_seed import DEV_COMPANY_ID, dev_fixture_frames, seed_dev_fixture
from backend.testing import lineage_frame


@pytest.fixture
def db():
    d = connect(":memory:")
    yield d
    d.close()


def test_schema_creates_every_table_with_lineage(db):
    tables = {r["table_name"] for r in db.query_dicts("SELECT table_name FROM information_schema.tables")}
    for t in DATA_TABLES:
        assert t in tables
        cols = {c[0] for c in db.table_columns(t)}
        assert set(LINEAGE_COLUMNS) <= cols, t
        not_null = {c[0] for c in db.table_columns(t) if c[2]}
        assert set(LINEAGE_COLUMNS) <= not_null, f"lineage must be NOT NULL in {t}"
    for t in ("audit_log", "approvals", "company_permissions", "llm_usage", "ingest_batches"):
        assert t in tables


def test_schema_init_is_idempotent(db):
    db.init_schema()
    db.init_schema()
    assert db.scalar("SELECT COUNT(*) FROM companies") == 0


def test_dev_fixture_matches_its_source_frames(db):
    seed_dev_fixture(db)
    frames = dev_fixture_frames()
    for table, df in frames.items():
        assert db.scalar(f"SELECT COUNT(*) FROM {table}") == len(df)
    # Book balance check against the source frame, not against our own query output.
    jl, acc = frames["journal_lines"], frames["accounts"]
    bank_id = acc.loc[acc["subtype"] == "bank", "id"].iloc[0]
    expected = (jl.loc[jl["account_id"] == bank_id, "debit"] - jl.loc[jl["account_id"] == bank_id, "credit"]).sum()
    rows = queries.account_balances(db, DEV_COMPANY_ID, dt.date(2026, 10, 1), ["bank"])
    assert rows[0]["balance_base"] == Decimal(str(expected)) == Decimal("375000")
    assert len(rows[0]["evidence_line_ids"]) == (jl["account_id"] == bank_id).sum()
    assert queries.unbalanced_entries(db, DEV_COMPANY_ID) == []


def test_rows_missing_lineage_are_rejected(db):
    df = lineage_frame([
        {"id": "c1", "name": "A", "country": "BD", "base_currency": "BDT"},
        {"id": "c2", "name": "B", "country": "KE", "base_currency": "KES"},
        {"id": "c3", "name": "C", "country": "VN", "base_currency": "VND"},
    ])
    df.loc[1, "source_row_id"] = None
    df.loc[2, "source_file"] = "  "
    rep = load_dataframe(db, "companies", df)
    assert rep.inserted == 1 and rep.rejected_count == 2
    assert set(rep.rejected["reject_reason"]) == {"missing lineage: source_row_id", "missing lineage: source_file"}
    assert db.scalar("SELECT COUNT(*) FROM companies") == 1
    row = db.query_one("SELECT * FROM companies WHERE id = 'c1'")
    assert row["ingest_batch_id"] == rep.batch_id and row["ingested_at"] is not None
    batch = db.query_one("SELECT * FROM ingest_batches WHERE id = ?", [rep.batch_id])
    assert batch["status"] == "partial" and batch["rows_rejected"] == 2


def test_frame_without_lineage_columns_is_refused(db):
    with pytest.raises(LineageError):
        load_dataframe(db, "companies", pd.DataFrame([{"id": "c1", "name": "A", "country": "BD",
                                                       "base_currency": "BDT"}]))


def test_unknown_columns_and_bad_ids(db):
    with pytest.raises(ValueError, match="unknown columns"):
        load_dataframe(db, "companies", lineage_frame([{"id": "c1", "name": "A", "country": "BD",
                                                        "base_currency": "BDT", "colour": "red"}]))
    rep = load_dataframe(db, "companies", lineage_frame([
        {"id": "has space", "name": "A", "country": "BD", "base_currency": "BDT"},
        {"id": "ok-1", "name": None, "country": "BD", "base_currency": "BDT"},
    ]))
    assert rep.inserted == 0
    assert list(rep.rejected["reject_reason"]) == ["invalid id (allowed: letters, digits, _ . -)",
                                                   "null in required column: name"]


def test_if_exists_skip_makes_reload_idempotent(db):
    df = lineage_frame([{"id": "c1", "name": "A", "country": "BD", "base_currency": "BDT"}])
    load_dataframe(db, "companies", df)
    rep = load_dataframe(db, "companies", df, if_exists="skip")
    assert rep.inserted == 0 and rep.skipped_existing == 1


def test_source_rows_lookup_and_tenant_isolation(db):
    seed_dev_fixture(db)
    res = get_source_rows(db, ["journal_lines:devco-jl-e2-1", "accounts:missing", "not-a-ref"], company_id="devco")
    assert [r["ref"] for r in res["rows"]] == ["journal_lines:devco-jl-e2-1"]
    row = res["rows"][0]["row"]
    assert row["source_system"] == "dev_fixture" and row["source_row_id"] and row["debit"] == 45000.0
    assert set(res["missing"]) == {"accounts:missing", "not-a-ref"}
    other = get_source_rows(db, ["journal_lines:devco-jl-e2-1"], company_id="someone-else")
    assert other["rows"] == [] and other["missing"] == ["journal_lines:devco-jl-e2-1"]
