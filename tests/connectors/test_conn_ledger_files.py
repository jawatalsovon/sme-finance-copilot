"""Ledger-type files: QuickBooks-style CSVs, journal exports, cash-book notebooks (Excel), POS daily summary."""
import datetime as dt

import openpyxl
import pandas as pd

from backend.connectors.base import registry
from backend.ledger.db import connect
from backend.ledger.loader import load_frames

registry.autodiscover()

COA = ("Account Number,Account Name,Type,Detail Type\n1000,Cash,Asset,cash\n1200,Debtors,Asset,ar\n2000,Creditors,Liability,ap\n"
       "2200,VAT control,Liability,tax_payable\n4000,Sales,Income,revenue\n1300,Stock,Asset,inventory\n")
INV = ("InvoiceNo,Customer,InvoiceDate,DueDate,Terms,Item(Product/Service),ItemDescription,ItemQuantity,ItemRate,ItemAmount,ItemTaxCode,ItemTaxAmount,Currency\n"
       "INV1,Ilkley Co,02/01/2024,01/02/2024,Net 30,Supplies,x,1,100.00,100.00,20% S,20.00,GBP\n"
       "INV1,Ilkley Co,02/01/2024,01/02/2024,Net 30,Supplies,y,2,50.00,100.00,20% S,20.00,GBP\n")
BILL = ("BillNo,Supplier,BillDate,DueDate,Terms,Category,Description,LineAmount,LineTaxCode,LineTaxAmount,Currency\n"
        "B1,Wharfedale,05/01/2024,04/02/2024,Net 30,Cost of sales:Stock purchases,x,300.00,20% S,60.00,GBP\n")
JRN = ("JournalNo,JournalDate,AccountName,Debits,Credits,Description,Name,Currency\n"
       "JE1,03/01/2024,Cash,50.00,,sale,,GBP\nJE1,03/01/2024,Sales,,50.00,sale,,GBP\n")


def _w(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_quickbooks_style_csv_set_posts_balanced_entries(tmp_path):
    paths = [_w(tmp_path, "qbo_journal_entries.csv", JRN), _w(tmp_path, "qbo_bills.csv", BILL),
             _w(tmp_path, "qbo_invoices.csv", INV), _w(tmp_path, "qbo_chart_of_accounts.csv", COA)]   # order must not matter
    res = registry.create("csv_upload", "co", {"paths": paths, "base_currency": "GBP",
                                               "profiles_dir": str(tmp_path / "p")}).pull()
    fr = res.frames
    assert list(fr["accounts"]["code"]) == ["1000", "1200", "2000", "2200", "4000", "1300"]
    assert fr["accounts"].set_index("code").loc["2200", "subtype"] == "tax_payable"
    inv = fr["invoices"].iloc[0]
    assert (inv["total"], inv["tax"], inv["status"]) == ("240.00", "40.00", "open")        # two item lines, one invoice
    bill = fr["bills"].iloc[0]
    assert bill["total"] == "360.00" and bill["expense_account_id"] == "co-1300"            # 'Stock purchases' -> inventory
    jl = fr["journal_lines"]
    assert pd.to_numeric(jl["amount_base"]).groupby(jl["entry_id"]).sum().abs().max() == 0   # every entry balances
    ar = jl[jl["account_id"] == "co-1200"]
    assert ar["debit"].tolist() == ["240.00"]
    assert set(fr["counterparties"]["kind"]) == {"customer", "supplier"}
    reports = load_frames(connect(":memory:"), fr, company_id="co", if_exists="skip")
    assert all(r.rejected_count == 0 for r in reports.values())


def _notebook(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mar24"
    ws.append(["Mirpur Family Store - Hisab March 2024"])
    ws.append([])
    ws.append(["Date", "Details", "Head", "Mode", "In", "Out", "Note"])
    ws.append([dt.datetime(2024, 3, 1), "Opening balance 2024", "Opening balance", "Cash", 1000, None, "acct:cash"])
    ws.append(["1-Mar", "Opening balance 2024", "Opening balance", "Capital", None, "1,000", "acct:cap"])
    ws.append(["03/01/2024", "Cash sales", "Sales", "CASH ", "Tk 2,500", None, "S1"])          # mm/dd in the March tab
    ws.append([dt.datetime(2024, 3, 2), "Purchase - oil", "Purchse", "Udhar", None, "১০০০", None])
    ws.append([dt.datetime(2024, 3, 3), "Mystery", "Zzzz", "Cash", 5, None, None])             # unknown label
    baki = wb.create_sheet("Baki")
    baki.append(["Date", "Household", "Particulars", "Given (credit)", "Received", "How paid"])
    baki.append(["01/03/2024", "Rina household", "Goods on credit", 500, None, None])
    p = tmp_path / "notebook_2024.xlsx"
    wb.save(p)
    return str(p)


def test_cashbook_notebook_messy_rows_become_journal_entries(tmp_path):
    accounts = pd.DataFrame([{"id": f"co-{c}", "code": c, "name": n, "type": "asset", "subtype": s, "currency": "BDT"}
                             for c, n, s in [("1000", "Cash", "cash"), ("3000", "Capital", "owner_equity"),
                                             ("4000", "Sales", "revenue"), ("1300", "Stock", "inventory"),
                                             ("2000", "AP", "ap")]])
    c = registry.create("gsheets", "co", {"path": _notebook(tmp_path), "base_currency": "BDT", "accounts": accounts,
                                          "profiles_dir": str(tmp_path / "p")})
    assert c.test_connection().ok
    res = c.pull()
    je, jl = res.frames["journal_entries"], res.frames["journal_lines"]
    assert len(je) == 3                                                                    # opening + sale + purchase
    opening = je[je["reference"] == "OPENING"].iloc[0]
    assert str(opening["entry_date"]) == "2024-03-01" and opening["source_row_id"] == "Mar24!R4"
    sale = je[je["reference"] == "S1"].iloc[0]
    assert str(sale["entry_date"]) == "2024-03-01"                                         # tab month settles 03/01
    sale_lines = jl[jl["entry_id"] == sale["id"]].set_index("account_id")
    assert sale_lines.loc["co-1000", "debit"] == "2500.00" and sale_lines.loc["co-4000", "credit"] == "2500.00"
    buy = jl[(jl["source_row_id"] == "Mar24!R7#1")].iloc[0]
    assert buy["account_id"] == "co-1300" and buy["debit"] == "1000.00"                    # 'Purchse' typo, Bangla digits
    assert any("Zzzz" in w for w in res.warnings)                                          # unknown label reported
    assert any("Baki" in w and "skipped" in w for w in res.warnings)                       # unmapped tab reported


def test_square_style_daily_summary_loads_as_daily_imports(tmp_path):
    text = ("Date,Gross Sales,Discounts,Net Sales,Tax,Gift Card Sales,Total Collected,Cash,Card,Gift Card Redeemed,Transactions\n"
            "2020-01-02,1256.83,36.61,1220.22,229.40,0.00,1449.62,374.35,1075.27,0.00,34\n")
    res = registry.create("csv_upload", "co", {"path": _w(tmp_path, "sq.csv", text), "base_currency": "GBP",
                                               "profiles_dir": str(tmp_path / "p")}).pull()
    d = res.frames["pos_daily_imports"].iloc[0]
    assert (d["gross_sales"], d["net_sales"], d["card_amount"], d["transactions_count"]) == ("1256.83", "1220.22", "1075.27", 34)
    load_frames(connect(":memory:"), res.frames, company_id="co")
