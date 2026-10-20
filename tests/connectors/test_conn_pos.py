"""POS adapter: Square sandbox API (recorded-style fixtures) and the Square / Odoo / Loyverse export profiles."""
import json

import httpx
import pandas as pd
import pytest

from backend.connectors.base import ConnectorError, registry
from backend.ledger.db import connect
from backend.ledger.loader import load_frames
from tests.connectors.conftest import FIX

registry.autodiscover()
SQ = FIX / "square"


def square_client(seen: list):
    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, dict(req.url.params), req.headers.get("authorization"),
                     json.loads(req.content) if req.content else None))
        if req.url.path == "/v2/locations":
            return httpx.Response(200, content=(SQ / "locations.json").read_bytes())
        if req.url.path == "/v2/payments":
            return httpx.Response(200, content=(SQ / "payments.json").read_bytes())
        if req.url.path == "/v2/orders/search":
            body = json.loads(req.content)
            page = "orders_page2.json" if body.get("cursor") == "page2" else "orders_page1.json"
            return httpx.Response(200, content=(SQ / page).read_bytes())
        return httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_not_configured_and_production_refused(monkeypatch):
    c = registry.create("pos", "co", {"mode": "square_api"})
    r = c.test_connection()
    assert r.ok is False and "not configured" in r.message and "SQUARE_ACCESS_TOKEN" in r.message
    with pytest.raises(ConnectorError, match="not configured"):
        c.pull()
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "production")
    msg = registry.create("pos", "co", {"mode": "square_api"}).test_connection().message
    assert "sandbox" in msg


def test_square_sandbox_pull_maps_orders_payments_fees_and_local_business_day(monkeypatch):
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-token-123")
    seen = []
    c = registry.create("pos", "uk_garden", {"mode": "square_api", "http_client": square_client(seen)})
    assert c.test_connection().ok
    res = c.pull()
    o = res.frames["pos_orders"].set_index("external_order_id")
    assert list(o.index) == ["ORD1", "ORD2", "RF1"]                               # two pages followed, refund is its own order
    ord1 = o.loc["ORD1"]
    assert str(ord1["business_date"]) == "2026-10-19"                              # 23:30 UTC on the 18th = 00:30 BST on the 19th
    assert str(ord1["opened_at"]) == "2026-10-18 23:30:00" and ord1["timezone"] == "Europe/London"
    assert (ord1["gross"], ord1["tax"], ord1["tip"], ord1["total"], ord1["currency"]) == ("10.00", "2.00", "1.00", "13.00", "GBP")
    assert o.loc["RF1", "status"] == "refund" and o.loc["RF1", "total"] == "3.00"       # magnitudes are positive
    assert o.loc["RF1", "refund_for_order_id"] == res.frames["pos_orders"].set_index("external_order_id").loc["ORD2", "id"]
    pays = res.frames["pos_payments"].set_index("reference")
    assert (pays.loc["PAY1", "method"], pays.loc["PAY1", "provider"], pays.loc["PAY1", "amount"], pays.loc["PAY1", "fee"]) == ("card", "visa", "13.00", "0.23")
    assert pays.loc["PAY2", "method"] == "cash" and pd.isna(pays.loc["PAY2", "fee"])   # unknown fee stays NULL, never guessed
    assert pays.loc["RF1", "amount"] == "-3.00"                                       # refund payment is signed
    line = res.frames["pos_order_lines"].iloc[0]
    assert (line["name"], line["qty"], line["unit_price"], line["line_total"], line["tax"]) == ("Barbecue", "2", "5.0000", "10.00", "2.00")
    # token is only ever sent as a bearer header, to the sandbox host, and appears nowhere in the data
    assert all(s[3] == "Bearer sandbox-token-123" for s in seen)
    assert "sandbox-token-123" not in json.dumps({k: v.astype(str).values.tolist() for k, v in res.frames.items()}) and "sandbox-token-123" not in " ".join(res.warnings)
    assert any(s[1] == "/v2/orders/search" and s[4].get("cursor") == "page2" for s in seen)
    reports = load_frames(connect(":memory:"), res.frames, company_id="uk_garden", if_exists="skip")
    assert all(r.rejected_count == 0 for r in reports.values())


SQUARE_TX = """Date,Time,Time Zone,Gross Sales,Discounts,Net Sales,Tax,Tip,Total Collected,Cash,Card,Transaction ID,Payment ID,Event Type,Location,Staff ID
2026-09-01,14:23:00,Europe/London,90.38,0.00,90.38,18.08,0.00,108.46,0.00,108.46,SQ1,PAY1,Payment,Hollinbank,EMP02
2026-09-01,17:59:00,Europe/London,3.38,0.00,3.38,0.68,0.00,4.06,4.06,0.00,SQ2,PAY2,Payment,Hollinbank,EMP03
2026-09-02,13:00:00,Europe/London,-5.70,0.00,-5.70,-1.14,0.00,-6.84,0.00,-6.84,SQ3,PAY3,Refund,Hollinbank,EMP04
"""
SQUARE_ITEMS = """Date,Time,Category,Item,Qty,SKU,Gross Sales,Discounts,Net Sales,Tax,Transaction ID,Location
2026-09-01,14:23:00,x,Barbecue,1,BBQ,87.49,0.00,87.49,17.50,SQ1,Hollinbank
2026-09-01,14:23:00,x,Gloves,1,GLV,2.89,0.00,2.89,0.58,SQ1,Hollinbank
2026-09-01,17:59:00,x,Bird food,1,BF,3.38,0.00,3.38,0.68,SQ2,Hollinbank
2026-09-02,13:00:00,x,Perennial 2L,2,PL,-5.70,0.00,-5.70,-1.14,SQ3,Hollinbank
"""
ODOO = """Order Ref,Date,Session,Cashier,Product,Quantity,Unit Price,Subtotal,Taxes,Total,Payment Method
Shop/1,2026-09-01 13:23:00,POS/1,EMP02,Barbecue,1,87.49,87.49,17.50,104.99,Card
Shop/1,2026-09-01 13:23:00,POS/1,EMP02,Gloves,1,2.89,2.89,0.58,3.47,Card
Shop/2,2026-09-01 16:59:00,POS/1,EMP03,Bird food,1,3.38,3.38,0.68,4.06,Cash
Shop/3,2026-09-02 12:00:00,POS/2,EMP04,Perennial 2L,-2,2.85,-5.70,-1.14,-6.84,Card
"""
LOYVERSE = """Date,Receipt number,Receipt type,Gross sales,Discounts,Net sales,Taxes,Total collected,Payment type,Description
2026-09-01 13:23,1,SALE,90.38,0.00,90.38,18.08,108.46,CARD,Barbecue x1; Gloves x1
2026-09-01 16:59,2,SALE,3.38,0.00,3.38,0.68,4.06,CASH,Bird food x1
2026-09-02 12:00,3,REFUND,-5.70,0.00,-5.70,-1.14,-6.84,CARD,Perennial 2L x2
"""


def _pull(tmp_path, **files):
    paths = []
    for name, text in files.items():
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        paths.append(str(p))
    return registry.create("pos", "uk_garden", {"paths": paths, "currency": "GBP"}).pull()


def _totals(res):
    o = res.frames["pos_orders"]
    return (len(o), o.groupby("status").size().to_dict(), round(pd.to_numeric(o["gross"]).sum(), 2),
            round(pd.to_numeric(o["tax"]).sum(), 2), round(pd.to_numeric(o["total"]).sum(), 2))


def test_square_odoo_and_loyverse_exports_of_the_same_orders_agree_to_the_penny(tmp_path):
    sq = _pull(tmp_path, **{"tx.csv": SQUARE_TX, "items.csv": SQUARE_ITEMS})
    od = _pull(tmp_path, **{"odoo.csv": ODOO})
    lo = _pull(tmp_path, **{"loy.csv": LOYVERSE})
    assert _totals(sq) == (3, {"refund": 1, "sale": 2}, 99.46, 19.9, 119.36)
    for r in (od, lo):
        assert _totals(r) == _totals(sq)
    assert _totals(sq)[0] == 3 and _totals(sq)[1] == {"refund": 1, "sale": 2}
    # refunds: positive magnitudes on the order, negative payment
    for r in (sq, od, lo):
        refund = r.frames["pos_orders"].query("status == 'refund'").iloc[0]
        assert refund["total"] in ("6.84",) and str(refund["business_date"]) == "2026-09-02"
        pay = r.frames["pos_payments"].set_index("order_id").loc[refund["id"]]
        assert pay["amount"] == "-6.84" and pay["method"] == "card"
    # lines: Square and Odoo carry them, Loyverse receipts do not (the export has only a text description)
    assert len(sq.frames["pos_order_lines"]) == 4 and len(od.frames["pos_order_lines"]) == 4
    assert "pos_order_lines" not in lo.frames


def test_utc_export_times_are_converted_to_the_local_business_day(tmp_path):
    late = ODOO.replace("2026-09-01 16:59:00", "2026-09-01 23:30:00")      # 23:30 UTC = 00:30 BST on 2 Sep
    od = _pull(tmp_path, **{"odoo.csv": late})
    o = od.frames["pos_orders"].set_index("external_order_id")
    assert str(o.loc["Shop/2", "business_date"]) == "2026-09-02" and str(o.loc["Shop/2", "opened_at"]) == "2026-09-01 23:30:00"


def test_repeated_order_ids_are_flagged_not_dropped(tmp_path):
    dup = LOYVERSE + "2026-09-03 10:00,2,SALE,1.00,0.00,1.00,0.20,1.20,CASH,x x1\n"
    res = _pull(tmp_path, **{"loy.csv": dup})
    assert len(res.frames["pos_orders"]) == 4 and any("more than once" in w for w in res.warnings)


def test_unknown_layout_is_refused_with_a_pointer_to_csv_upload(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    c = registry.create("pos", "co", {"paths": [str(p)], "currency": "GBP"})
    assert c.test_connection().ok is False
    with pytest.raises(ConnectorError, match="csv_upload"):
        c.pull()
