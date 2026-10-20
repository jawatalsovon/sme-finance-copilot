"""POS order tables and the derived pos_sales_daily view, checked against hand-computed totals."""
from decimal import Decimal

import pandas as pd
import pytest

from backend.ledger import connect, get_source_rows, load_dataframe
from backend.ledger.lineage import extract_refs
from backend.testing import lineage_frame

D = Decimal


@pytest.fixture
def db():
    d = connect(":memory:")
    orders = lineage_frame([
        # sale paid cash + bKash: gross 1000, discount 50, tax 47.50, tip 20 -> total 1017.50
        dict(id="o1", company_id="c1", location="Mirpur", business_date="2026-09-01", currency="BDT",
             status="sale", gross=1000, discount=50, tax=47.5, tip=20, total=1017.5),
        # foodpanda delivery: gross 600, commission 90
        dict(id="o2", company_id="c1", location="Mirpur", business_date="2026-09-01", currency="BDT",
             status="sale", channel="delivery", aggregator="foodpanda", gross=600, tax=30, fee=90, total=630),
        # refund of part of o1 on the same day: 200 ex tax, 10 tax
        dict(id="o3", company_id="c1", location="Mirpur", business_date="2026-09-01", currency="BDT",
             status="refund", refund_for_order_id="o1", gross=200, tax=10, total=210),
        # void must be ignored
        dict(id="o4", company_id="c1", location="Mirpur", business_date="2026-09-01", currency="BDT",
             status="void", gross=999, total=999),
        # other company, same day: must not leak into c1
        dict(id="o5", company_id="c2", location="Mirpur", business_date="2026-09-01", currency="BDT",
             status="sale", gross=5000, total=5000),
    ], source_system="pos_test", source_file="orders.csv")
    payments = lineage_frame([
        dict(id="p1", company_id="c1", order_id="o1", method="cash", amount=517.5, currency="BDT"),
        dict(id="p2", company_id="c1", order_id="o1", method="wallet", provider="bkash", amount=500, fee=9,
             currency="BDT"),
        dict(id="p3", company_id="c1", order_id="o2", method="aggregator", provider="foodpanda", amount=630,
             currency="BDT"),
        dict(id="p4", company_id="c1", order_id="o3", method="cash", amount=-210, currency="BDT"),
        dict(id="p5", company_id="c2", order_id="o5", method="card", amount=5000, currency="BDT"),
    ], source_system="pos_test", source_file="payments.csv")
    lines = lineage_frame([
        dict(id="l1", company_id="c1", order_id="o1", line_no=1, name="Rice 5kg", qty=2, unit_price=500,
             discount=50, line_total=950),
    ], source_system="pos_test", source_file="lines.csv")
    imports = lineage_frame([
        # a day that only has a daily summary -> appears with basis 'imported'
        dict(id="d1", company_id="c1", sales_date="2026-09-02", location="Mirpur", currency="BDT",
             gross_sales=800, net_sales=800, cash_amount=800, transactions_count=7),
        # a day that also has orders -> ignored in favour of orders
        dict(id="d2", company_id="c1", sales_date="2026-09-01", location="Mirpur", currency="BDT",
             gross_sales=1, net_sales=1),
    ], source_system="pos_export", source_file="daily.csv")
    for table, df in [("pos_orders", orders), ("pos_payments", payments), ("pos_order_lines", lines),
                      ("pos_daily_imports", imports)]:
        rep = load_dataframe(d, table, df)
        assert rep.rejected_count == 0, rep.rejected
    yield d
    d.close()


def test_pos_sales_daily_view_totals(db):
    rows = db.query_dicts("SELECT * FROM pos_sales_daily WHERE company_id = 'c1' ORDER BY sales_date")
    assert len(rows) == 2
    day = rows[0]
    assert day["basis"] == "orders" and day["order_count"] == 3        # void excluded
    assert day["gross_sales"] == D("1600.00")                            # 1000 + 600
    assert day["discounts"] == D("50.00")
    assert day["refunds"] == D("200.00")
    assert day["net_sales"] == D("1350.00")                              # 1600 - 50 - 200
    assert day["tax"] == D("67.50")                                      # 47.5 + 30 - 10
    assert day["tips"] == D("20.00")
    assert day["fees"] == D("99.00")                                     # 90 commission + 9 bKash fee
    assert (day["cash_amount"], day["wallet_amount"], day["aggregator_amount"], day["card_amount"]) == \
        (D("307.50"), D("500.00"), D("630.00"), D("0.00"))               # 517.5 - 210 cash refund
    assert day["transactions_count"] == 2                                # sales only
    imported = rows[1]
    assert imported["basis"] == "imported" and imported["net_sales"] == D("800.00") and imported["id"] == "d1"


def test_view_rows_are_citable_and_company_scoped(db):
    day = db.query_one("SELECT id FROM pos_sales_daily WHERE company_id = 'c1' AND basis = 'orders'")
    ref = f"pos_sales_daily:{day['id']}"
    assert extract_refs(f"see [{ref}] and [pos_order_lines:l1]") == [ref, "pos_order_lines:l1"]
    res = get_source_rows(db, [ref, "pos_payments:p5"], company_id="c1")
    assert [r["ref"] for r in res["rows"]] == [ref] and res["missing"] == ["pos_payments:p5"]


def test_view_is_not_loadable_and_payment_methods_are_checked(db):
    with pytest.raises(ValueError):
        load_dataframe(db, "pos_sales_daily", lineage_frame([{"id": "x"}]))
    with pytest.raises(Exception):
        load_dataframe(db, "pos_payments", lineage_frame([dict(id="bad", company_id="c1", order_id="o1",
                                                               method="crypto", amount=1, currency="BDT")]))
    assert db.scalar("SELECT COUNT(*) FROM pos_payments WHERE id = 'bad'") == 0
