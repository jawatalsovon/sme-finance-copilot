import datetime as dt
from decimal import Decimal

import pytest

from backend.engine import pnl as P
from backend.engine.interfaces import EngineError
from tests.engine.conftest import AS_OF, SMES, synth_engine


def test_dev_fixture_pnl_books_and_corrected(dev_engine):
    r = dev_engine.monthly_pnl("devco", "2026-09", "books")
    assert r.revenue.value == Decimal("63500.00")          # 45,000 cash + 18,500 bKash
    assert r.cogs.value == Decimal("60000.00")
    assert r.operating_expenses.value == Decimal("65000.00")  # rent 25,000 + salaries 40,000
    assert r.net_profit.value == Decimal("-61500.00")
    assert r.revenue.evidence and all(e.startswith("journal_lines:") for e in r.revenue.evidence)
    c = dev_engine.monthly_pnl("devco", "2026-09", "corrected")
    # corrected adds the bank charges (3,500) and wallet fee (350) found by Cash Truth
    assert c.operating_expenses.value - r.operating_expenses.value >= Decimal("3850.00")
    assert c.basis == "corrected" and c.notes


def test_bad_month_is_a_plain_error(dev_engine):
    with pytest.raises(EngineError):
        dev_engine.monthly_pnl("devco", "2026-13", "books")
    with pytest.raises(EngineError):
        dev_engine.monthly_pnl("devco", "1999-01", "books")


@pytest.mark.parametrize("cid", SMES)
def test_books_basis_reproduces_answer_key_to_the_cent(cid):
    eng, key = synth_engine(cid)
    L = eng._ledger(cid)
    tbl = P.monthly_table(L, None, "books", AS_OF)
    for row in key["book_monthly_pnl"]:
        t = tbl.loc[row["month"]]
        for f in ("revenue", "cogs", "net_profit", "other_income"):
            assert Decimal(int(t[f])) / 100 == Decimal(row[f]), (row["month"], f)


@pytest.mark.parametrize("cid", SMES)
def test_corrected_basis_moves_towards_true_pnl(cid):
    eng, key = synth_engine(cid)
    L = eng._ledger(cid)
    books = P.monthly_table(L, None, "books", AS_OF)
    corr = P.monthly_table(L, eng._corrections(cid), "corrected", AS_OF)
    shrink: dict[str, Decimal] = {}
    for p in key["problems"]:
        if p["type"] == "inventory_shrinkage":   # needs stock counts that no source provides
            shrink[p["date"][:7]] = shrink.get(p["date"][:7], Decimal(0)) + Decimal(p["amount_base"])
    err_b = err_c = rev_b = rev_c = Decimal(0)
    for row in key["true_monthly_pnl"]:
        m = row["month"]
        truth = Decimal(row["net_profit"]) + shrink.get(m, Decimal(0))
        err_b += abs(Decimal(int(books.loc[m, "net_profit"])) / 100 - truth)
        err_c += abs(Decimal(int(corr.loc[m, "net_profit"])) / 100 - truth)
        rev_b += abs(Decimal(int(books.loc[m, "revenue"])) / 100 - Decimal(row["revenue"]))
        rev_c += abs(Decimal(int(corr.loc[m, "revenue"])) / 100 - Decimal(row["revenue"]))
    assert err_c < err_b * Decimal("0.35"), (err_b, err_c)
    assert rev_c <= rev_b + Decimal("0.01"), (rev_b, rev_c)


def test_monthly_pnl_matches_fast_table():
    eng, _ = synth_engine("cn_xborder")
    L = eng._ledger("cn_xborder")
    tbl = P.monthly_table(L, eng._corrections("cn_xborder"), "corrected", AS_OF)
    r = eng.monthly_pnl("cn_xborder", "2024-11", "corrected")
    assert r.net_profit.value == Decimal(int(tbl.loc["2024-11", "net_profit"])) / 100
    assert r.cash_in.value > 0 and r.cash_out.value > 0 and r.summary


def test_kpis_have_required_keys():
    eng, _ = synth_engine("bd_grocer")
    k = eng.kpis("bd_grocer", AS_OF)
    keys = {x.key for x in k.kpis}
    assert {"runway_months", "gross_margin_pct", "dso_days", "dpo_days", "cash_conversion", "revenue_growth_pct",
            "top_customer_share_pct"} <= keys
    gm = next(x for x in k.kpis if x.key == "gross_margin_pct")
    assert 5 < gm.value < 20      # D1 design: bd_grocer gross margin about 11-12%
