"""Seed the demo state on a fresh in-memory DB and check the counts (needs `python scripts/data/generate.py` first)."""
import time
from pathlib import Path

import pytest

from backend.ledger.db import connect

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "synthetic" / "uk_garden" / "answer_key.json").exists(), reason="run scripts/data/generate.py first")


def test_seed_loads_three_companies_quickly():
    from scripts.data.seed import seed

    db = connect(":memory:")
    t0 = time.time()
    counts = seed(db)
    secs = time.time() - t0
    assert secs < 60, secs
    assert counts["_rejected"] == 0
    assert db.query_df("SELECT COUNT(*) AS n FROM companies").iloc[0]["n"] == 3
    for t in ("journal_entries", "journal_lines", "external_statement_lines", "invoices", "accounts"):
        assert counts[t] > 0, t
    assert counts["pos_orders"] > 1000 and counts["pos_daily_imports"] > 1000
    for cid in ("bd_grocer", "cn_xborder", "uk_garden"):
        n = db.query_df("SELECT COUNT(*) AS n FROM journal_entries WHERE company_id = ?", [cid]).iloc[0]["n"]
        assert n > 5000, (cid, n)
    unbalanced = db.query_df("SELECT COUNT(*) AS n FROM (SELECT entry_id FROM journal_lines GROUP BY entry_id HAVING SUM(amount_base) <> 0)").iloc[0]["n"]
    assert unbalanced == 0
    assert db.query_df("SELECT COUNT(*) AS n FROM pos_sales_daily WHERE company_id = 'uk_garden'").iloc[0]["n"] > 1000
