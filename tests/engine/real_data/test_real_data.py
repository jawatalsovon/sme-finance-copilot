"""Light checks on public real data (skipped when data/raw/ is not downloaded).
The full numbers come from `python -m tests.engine.real_data.validate` (docs/validation/REAL_DATA_RESULTS.md)."""
import pandas as pd
import pytest

from backend.engine.duplicates import payment_duplicates
from tests.engine.real_data import validate as V


@pytest.mark.skipif(not V.V1_WEEKLY.is_file(), reason="UCI Online Retail II not in data/raw")
def test_v1_backtest_runs_and_is_reported_against_seasonal_naive():
    w = pd.read_csv(V.V1_WEEKLY, parse_dates=["InvoiceDate"]).set_index("InvoiceDate")["revenue_gbp"]
    r = V.v1_backtest(w)
    assert r["weeks"] == 104
    # R1 measured seasonal naive 21.1% (13 weeks) and 17.7% (26 weeks) on the same file and protocol
    assert abs(r["holdout_13"]["seasonal_naive_smape"] - 21.14) < 0.05
    assert abs(r["holdout_26"]["seasonal_naive_smape"] - 17.65) < 0.05
    assert 0 < r["holdout_13"]["engine_smape"] < 100


@pytest.mark.skipif(not V.V4_TSF.is_file(), reason="Monash car parts not in data/raw")
def test_v4_sample_is_reproducible():
    a = V.v4_backtest(n_sample=20)
    b = V.v4_backtest(n_sample=20)
    assert a == b and a["evaluated"] > 0


@pytest.mark.skipif(not (V.V2_DIR.is_dir() and list(V.V2_DIR.glob("*.csv"))), reason="Bexley files not in data/raw")
def test_v2_injected_repeats_are_found():
    d = V.v2_load()
    assert len(d) == 18719
    dj, key = V.inject(d)
    assert len(key) == 350 and len(dj) == len(d) + 350
    s = V.score_pairs(list(zip(*[payment_duplicates(dj)[c] for c in ("id_a", "id_b")])), key)
    assert s["per_kind"]["reversal"]["recall"] >= 0.9


def test_duplicate_rules_on_a_tiny_ledger():
    d = pd.DataFrame([
        {"id": "a", "date": "2025-08-01", "supplier_id": "s1", "amount": 900.0, "doc_no": "1"},
        {"id": "b", "date": "2025-08-01", "supplier_id": "s1", "amount": 900.0, "doc_no": "2"},   # exact repeat
        {"id": "c", "date": "2025-08-05", "supplier_id": "s2", "amount": 700.0, "doc_no": "3"},
        {"id": "d", "date": "2025-08-09", "supplier_id": "s2", "amount": 700.0, "doc_no": "4"},   # near repeat
        {"id": "e", "date": "2025-08-01", "supplier_id": "s3", "amount": 1200.0, "doc_no": "5"},  # schedule
        {"id": "f", "date": "2025-09-01", "supplier_id": "s3", "amount": 1200.0, "doc_no": "6"},
        {"id": "g", "date": "2025-09-01", "supplier_id": "s3", "amount": 1200.0, "doc_no": "7"},
        {"id": "h", "date": "2025-09-10", "supplier_id": "s4", "amount": 650.0, "doc_no": "8"},
        {"id": "i", "date": "2025-09-20", "supplier_id": "s4", "amount": -650.0, "doc_no": "9"},  # reversal
    ])
    r = payment_duplicates(d)
    got = {(x.rule, x.id_a, x.id_b) for x in r.itertuples()}
    assert got == {("exact_repeat", "a", "b"), ("near_repeat", "c", "d"), ("reversal", "h", "i")}
