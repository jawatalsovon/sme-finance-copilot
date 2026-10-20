"""uk_garden exports: bank CSV, Square-style POS CSVs, QuickBooks-importable CSVs, Odoo/Loyverse samples, canonical tables."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path

import canon
import uk
from core import D0, D1, Trace, days, money, month_end, rnd, rng_for, write_csv, write_json, ym
from uk import CID, SUPPLIERS, TRADE, ORDER_START

BOOK_SYSTEM = "quickbooks"
BOOK_FILE = "qbo_*.csv"


def system_for(file: str) -> str:
    return "quickbooks"


def fx_at(A):
    return lambda ccy, d: 1.0


def _d(d):
    return d.strftime("%d/%m/%Y")


def build_bank(sim):
    w = sim.world
    rows = []
    for e in w.entries:
        for l in e.lines:
            if l.acct != "bank":
                continue
            desc = {"square_payout": "SQUARE INC PAYOUT", "cash_deposit": "CASH DEPOSIT BRANCH", "trade_payment": "BACS " + e.memo.replace("BACS from ", "").upper(),
                    "bill_payment": "FASTER PAYMENT " + e.memo.replace("Paid ", "").upper(), "payroll": "BULK PAYMENT WAGES", "payroll_tax": "HMRC PAYE NI", "rent": "DD LANDLORD RENT RATES",
                    "utilities": "DD ENERGY WATER", "professional": "DD INSURANCE SOFTWARE", "marketing": "CARD PAYMENT MARKETING", "repair": "FASTER PAYMENT REPAIRS", "bank_fee": "ACCOUNT CHARGES",
                    "drawings": "TRANSFER TO OWNER", "loan_drawdown": "LOAN ADVANCE", "loan_payment": "LOAN REPAYMENT", "loan_interest": "LOAN REPAYMENT", "vat_payment": "HMRC VAT",
                    "owner_injection": "TRANSFER FROM OWNER", "opening": "BALANCE B/F"}.get(e.kind, e.memo.upper())
            g = e.ref if e.kind in ("loan_payment", "loan_interest") else e.id
            rows.append(dict(date=e.date, amount=l.nat, desc=desc, entries=[e.id], key=(e.date, e.ref[:7] if e.kind in ("loan_payment", "loan_interest") else e.id), kind=e.kind))
    merged = {}
    for r in rows:
        k = r["key"]
        if k in merged:
            merged[k]["amount"] += r["amount"]
            merged[k]["entries"] += r["entries"]
        else:
            merged[k] = r
    out = sorted(merged.values(), key=lambda r: (r["date"], r["entries"][0]))
    bal = 0
    for i, r in enumerate(out):
        bal += r["amount"]
        r["balance"] = bal
        r["ext"] = f"BK{r['date']:%y%m%d}{i % 100000:05d}"
    assert bal == w.bal("bank")
    return out


def run(sim, seed, A, outdir: Path, T_truth, T_books):
    trace = Trace()
    w, b = sim.world, sim.book
    src = outdir / "sources"
    rng = rng_for(seed, CID, "export")
    bank = build_bank(sim)
    by_year = defaultdict(list)
    for r in bank:
        by_year[r["date"].year].append(r)
    n = 0
    for y, rs in sorted(by_year.items()):
        fn = f"bank_current_account_{y}.csv"
        out = []
        for i, r in enumerate(rs, 2):
            a = r["amount"]
            out.append([_d(r["date"]), r["desc"], money(a) if a > 0 else "", money(-a) if a < 0 else "", money(r["balance"])])
            r["src"] = (fn, str(i))
            for eid in r["entries"]:
                trace.add(fn, str(i), "truth", eid)
        write_csv(src / fn, ["Date", "Description", "Money In", "Money Out", "Balance"], out)
        for r in rs:
            n += 1
            T_truth.add("external_statement_lines", id=f"ext-{CID}-{n:06d}", company_id=CID, channel="bank", account_id=canon.acct_id(CID, w, "bank"), statement_ref="****7305", txn_date=r["date"], value_date=r["date"],
                        amount=money(r["amount"]), currency="GBP", fx_rate="1.00000000", amount_base=money(r["amount"]), description=r["desc"], balance=money(r["balance"]), external_txn_id=r["ext"], fee="0.00",
                        source_system="bank_csv", source_file=fn, source_row_id=r["src"][1])
    # till counts
    bal = 0
    day_amt = defaultdict(int)
    for e in w.entries:
        for l in e.lines:
            if l.acct == "till":
                day_amt[e.date] += l.nat
    counts = []
    for d in days():
        bal += day_amt.get(d, 0)
        if d == month_end(d):
            counts.append((d, bal))
    write_csv(src / "till_cash_counts.csv", ["count_date", "counted_cash_gbp"], [[d.isoformat(), money(v)] for d, v in counts])
    for ci, (d, v) in enumerate(counts, 2):
        n += 1
        trace.add("till_cash_counts.csv", str(ci), "truth_balance", f"till:{d}")
        T_truth.add("external_statement_lines", id=f"ext-{CID}-{n:06d}", company_id=CID, channel="cash_count", account_id=canon.acct_id(CID, w, "till"), statement_ref="tills and safe", txn_date=d, value_date=d,
                    amount=money(v), currency="GBP", fx_rate="1.00000000", amount_base=money(v), description="Month-end till and safe count", balance=money(v), external_txn_id=f"COUNT{d:%Y%m%d}", fee="0.00",
                    source_system="cash_count", source_file="till_cash_counts.csv", source_row_id=str(ci))
    # Square deposits
    dep_rows = []
    for i, p in enumerate(sim.payouts, 2):
        dep_rows.append([p["date"].isoformat(), f"DEP{p['date']:%Y%m%d}", "Sent", money(p["gross"]), money(-p["fee"]), money(p["gross"] - p["fee"])])
        trace.add("square_deposits.csv", str(i), "truth", p["entry"])
        n += 1
        T_truth.add("external_statement_lines", id=f"ext-{CID}-{n:06d}", company_id=CID, channel="pos_settlement", account_id=canon.acct_id(CID, w, "bank"), statement_ref="Square payouts", txn_date=p["date"], value_date=p["date"],
                    amount=money(p["gross"] - p["fee"]), currency="GBP", fx_rate="1.00000000", amount_base=money(p["gross"] - p["fee"]), description="Square deposit (net of fees)", external_txn_id=f"DEP{p['date']:%Y%m%d}",
                    fee=money(p["fee"]), source_system="square_pos", source_file="square_deposits.csv", source_row_id=str(i))
    write_csv(src / "square_deposits.csv", ["Deposit Date", "Deposit ID", "Status", "Card Sales (Gross)", "Fees", "Net Deposit"], dep_rows)
    # POS: daily summaries before ORDER_START
    daily = []
    sq_i = 1
    for d in sorted(sim.day):
        x = sim.day[d]
        if d >= ORDER_START:
            continue
        sq_i += 1
        gross = x["net"] + x["disc"]
        daily.append([d.isoformat(), money(gross), money(x["disc"]), money(x["net"]), money(x["vat"]), money(x["gift_sold"]), money(x["net"] + x["vat"] + x["gift_sold"]), money(x["cash"]), money(x["card"]), money(x["gift_red"]), x["n"]])
        trace.add("square_daily_sales_summary.csv", str(sq_i), "truth", x["entry"])
        for T in (T_truth, T_books):
            T.add("pos_daily_imports", id=f"posdi-{CID}-{d:%Y%m%d}", company_id=CID, sales_date=d, location="Hollinbank", currency="GBP", gross_sales=money(gross), discounts=money(x["disc"]), refunds="0.00", net_sales=money(x["net"]),
                  tax=money(x["vat"]), tips="0.00", service_charge="0.00", fees=None, cash_amount=money(x["cash"]), card_amount=money(x["card"]), wallet_amount="0.00", aggregator_amount="0.00",
                  other_amount=money(x["gift_red"] + 0), transactions_count=x["n"], source_system="square_pos", source_file="square_daily_sales_summary.csv", source_row_id=d.isoformat())
    write_csv(src / "square_daily_sales_summary.csv", ["Date", "Gross Sales", "Discounts", "Net Sales", "Tax", "Gift Card Sales", "Total Collected", "Cash", "Card", "Gift Card Redeemed", "Transactions"], daily)
    # POS orders (Square-style transactions + items), plus Odoo/Loyverse samples for Sept 2026
    tx_rows, it_rows, odoo, loy = [], [], [], []
    oseq = 0
    cashiers = ["EMP01", "EMP02", "EMP03", "EMP04"]
    for d in sorted(sim.day):
        x = sim.day[d]
        if not x["orders"]:
            continue
        bst = 1 if dt.date(d.year, 3, 25) <= d <= dt.date(d.year, 10, 25) else 0
        for o in x["orders"]:
            oseq += 1
            oid = f"sqord-{CID}-{oseq:06d}"
            o["id"] = oid
            hr = int(rng.integers(9, 17)); mi = int(rng.integers(0, 60))
            ts = dt.datetime(d.year, d.month, d.day, hr - bst, mi)
            sign = 1 if o["status"] == "sale" else -1
            ext = f"SQ{d:%y%m%d}{oseq % 100000:05d}"
            cash = sum(p["amount"] for p in o["payments"] if p["method"] == "cash")
            card = sum(p["amount"] for p in o["payments"] if p["method"] == "card")
            tx_rows.append([d.isoformat(), f"{hr:02d}:{mi:02d}:00", "Europe/London", money(sign * o["gross"]), "0.00", money(sign * o["gross"]), money(sign * o["tax"]), "0.00", money(sign * o["total"]), money(cash), money(card),
                            ext, f"PAY{ext}", "Payment" if sign > 0 else "Refund", "Hollinbank", cashiers[oseq % 4]])
            rid = len(tx_rows) + 1
            trace.add("square_transactions_2026.csv", str(rid), "truth", x["entry"])
            T_truth.add("pos_orders", id=oid, company_id=CID, external_order_id=ext, location="Hollinbank", opened_at=ts, closed_at=ts + dt.timedelta(minutes=3), business_date=d, timezone="Europe/London", currency="GBP",
                        channel="counter", status=o["status"], refund_for_order_id=o["ref"]["id"] if o["status"] == "refund" and "id" in o["ref"] else None, gross=money(o["gross"]), discount="0.00", tax=money(o["tax"]), tip="0.00",
                        service_charge="0.00", fee=None, total=money(o["total"]), employee_ref=cashiers[oseq % 4], source_system="square_pos", source_file="square_transactions_2026.csv", source_row_id=str(rid))
            for l in o["lines"]:
                it_rows.append([d.isoformat(), f"{hr:02d}:{mi:02d}:00", l["cat"], l["name"], l["qty"], l["sku"], money(sign * l["total"]), "0.00", money(sign * l["total"]), money(sign * l["tax"]), ext, "Hollinbank"])
                iid = len(it_rows) + 1
                trace.add("square_items_2026.csv", str(iid), "truth", x["entry"])
                T_truth.add("pos_order_lines", id=f"sqline-{CID}-{oseq:06d}-{l['no']}", company_id=CID, order_id=oid, line_no=l["no"], sku=l["sku"], name=l["name"], qty=f"{l['qty']}", unit_price=money(l["unit"]), discount="0.00",
                            tax=money(l["tax"]), line_total=money(l["total"]), source_system="square_pos", source_file="square_items_2026.csv", source_row_id=str(iid))
                if d >= dt.date(2026, 9, 1):
                    odoo.append([f"Shop/{oseq:05d}", f"{d.isoformat()} {hr:02d}:{mi:02d}:00", f"POS/{d:%Y%m%d}", cashiers[oseq % 4], l["name"], l["qty"] * sign, money(l["unit"]), money(sign * l["total"]), money(sign * l["tax"]), money(sign * (l["total"] + l["tax"])), o["payments"][0]["method"].capitalize()])
                    trace.add("odoo_pos_order_lines_sample_2026-09.csv", str(len(odoo) + 1), "truth", x["entry"])
            for k, p in enumerate(o["payments"], 1):
                T_truth.add("pos_payments", id=f"sqpay-{CID}-{oseq:06d}-{k}", company_id=CID, order_id=oid, method=p["method"], provider=p["provider"], amount=money(p["amount"]), fee=None, currency="GBP", paid_at=ts,
                            reference=f"PAY{ext}", source_system="square_pos", source_file="square_transactions_2026.csv", source_row_id=str(rid))
            if d >= dt.date(2026, 9, 1):
                loy.append([f"{d.isoformat()} {hr:02d}:{mi:02d}", f"{oseq:05d}", "SALE" if sign > 0 else "REFUND", money(sign * o["gross"]), "0.00", money(sign * o["gross"]), money(sign * o["tax"]), money(sign * o["total"]),
                            o["payments"][0]["method"].upper(), "; ".join(f"{l['name']} x{l['qty']}" for l in o["lines"])])
                trace.add("loyverse_receipts_sample_2026-09.csv", str(len(loy) + 1), "truth", x["entry"])
    write_csv(src / "square_transactions_2026.csv", ["Date", "Time", "Time Zone", "Gross Sales", "Discounts", "Net Sales", "Tax", "Tip", "Total Collected", "Cash", "Card", "Transaction ID", "Payment ID", "Event Type", "Location", "Staff ID"], tx_rows)
    write_csv(src / "square_items_2026.csv", ["Date", "Time", "Category", "Item", "Qty", "SKU", "Gross Sales", "Discounts", "Net Sales", "Tax", "Transaction ID", "Location"], it_rows)
    write_csv(src / "odoo_pos_order_lines_sample_2026-09.csv", ["Order Ref", "Date", "Session", "Cashier", "Product", "Quantity", "Unit Price", "Subtotal", "Taxes", "Total", "Payment Method"], odoo)
    write_csv(src / "loyverse_receipts_sample_2026-09.csv", ["Date", "Receipt number", "Receipt type", "Gross sales", "Discounts", "Net sales", "Taxes", "Total collected", "Payment type", "Description"], loy)
    # QuickBooks-importable CSVs (BOOKS view)
    ent = {e.id: e for e in w.entries}
    acct_name = {k: a.name for k, a in w.accts.items()}
    write_csv(src / "qbo_chart_of_accounts.csv", ["Account Number", "Account Name", "Type", "Detail Type"], [[a.code, a.name, a.type.capitalize(), a.subtype] for a in w.accts.values()])
    for fnm, nrows in (("qbo_chart_of_accounts.csv", len(w.accts)), ("qbo_customers.csv", len(TRADE)), ("qbo_suppliers.csv", len(SUPPLIERS))):
        for ri in range(2, nrows + 2):
            trace.add(fnm, str(ri), "master_data", "master_data")
    write_csv(src / "qbo_customers.csv", ["Customer", "Terms"], [[t, "Net 30"] for t in TRADE])
    write_csv(src / "qbo_suppliers.csv", ["Supplier", "Terms"], [[s, "Net 30"] for s in SUPPLIERS])
    inv_rows, bill_rows, jr_rows = [], [], []
    seq = 0
    book_sorted = b.sorted_entries()
    inv_by = {r["entry"]: r for r in sim.trade}
    bill_by = {x["entry"]: x for x in sim.bills}
    for e in book_sorted:
        tid = e.tags.get("truth_id")
        if e.kind == "trade_invoice":
            r = inv_by[tid]
            inv_rows.append([r["no"], TRADE[r["cust"]], _d(e.date), _d(r["due"]), "Net 30", "Landscaping supplies", "Plants, compost and materials", 1, money(r["net"]), money(r["net"]), "20.0% S", money(r["vat"]), "GBP"])
            trace.add("qbo_invoices.csv", str(len(inv_rows) + 1), "book", e.id)
        elif e.kind in ("bill", "dup_bill"):
            x = bill_by.get(tid) or bill_by[e.tags.get("orig", "")] if (tid in bill_by or e.tags.get("orig")) else None
            if x is None:
                x = next(v for k, v in bill_by.items() if ent[k].lines[0].dr == e.lines[0].dr and ent[k].date == e.date)
            bill_rows.append([e.ref, SUPPLIERS[x["sup"]], _d(e.date), _d(x["due"]), "Net 30", "Cost of sales:Stock purchases", e.memo, money(x["net"]), "20.0% S", money(x["vat"]), "GBP"])
            trace.add("qbo_bills.csv", str(len(bill_rows) + 1), "book", e.id)
        else:
            seq += 1
            jn = f"JE{seq:06d}"
            for l in e.lines:
                jr_rows.append([jn, _d(e.date), acct_name[l.acct], money(l.dr) if l.dr else "", money(l.cr) if l.cr else "", e.memo, "", "GBP"])
                trace.add("qbo_journal_entries.csv", str(len(jr_rows) + 1), "book", e.id)
    write_csv(src / "qbo_invoices.csv", ["InvoiceNo", "Customer", "InvoiceDate", "DueDate", "Terms", "Item(Product/Service)", "ItemDescription", "ItemQuantity", "ItemRate", "ItemAmount", "ItemTaxCode", "ItemTaxAmount", "Currency"], inv_rows)
    write_csv(src / "qbo_bills.csv", ["BillNo", "Supplier", "BillDate", "DueDate", "Terms", "Category", "Description", "LineAmount", "LineTaxCode", "LineTaxAmount", "Currency"], bill_rows)
    write_csv(src / "qbo_journal_entries.csv", ["JournalNo", "JournalDate", "AccountName", "Debits", "Credits", "Description", "Name", "Currency"], jr_rows)
    # subledgers
    book_ids = set(b.entries)
    for T, ss, sf in ((T_truth, "synthetic", "truth"), (T_books, "quickbooks", "qbo_*.csv")):
        for i, t in enumerate(TRADE, 1):
            T.add("counterparties", id=f"{CID}-cust-{i:02d}", company_id=CID, kind="customer", name=t, country="GB", payment_terms_days=30, source_system=ss, source_file=sf, source_row_id=f"cust-{i:02d}")
        for i, t in enumerate(SUPPLIERS, 1):
            T.add("counterparties", id=f"{CID}-sup-{i:02d}", company_id=CID, kind="supplier", name=t, country="GB", payment_terms_days=30, source_system=ss, source_file=sf, source_row_id=f"sup-{i:02d}")
        for i in range(1, 13):
            T.add("counterparties", id=f"{CID}-emp-{i:02d}", company_id=CID, kind="employee", name=f"Staff {i:02d}", country="GB", source_system=ss, source_file=sf, source_row_id=f"emp-{i:02d}")
        for c in ("plants", "compost", "pots", "tools", "furniture", "christmas", "other"):
            T.add("inventory_items", id=f"{CID}-item-{c}", company_id=CID, sku=c.upper(), name=c.capitalize(), unit="pcs", category="merchandise", cost_method="average", source_system=ss, source_file=sf, source_row_id=c)
    for v, T in (("truth", T_truth), ("books", T_books)):
        ss = "synthetic" if v == "truth" else "quickbooks"
        for r in sim.trade:
            pay_ok = r.get("paid") and (v == "truth" or b.bid(r["pay_entry"]) in book_ids)
            wo = r.get("wo_entry") and (v == "truth" or b.bid(r["wo_entry"]) in book_ids)
            T.add("invoices", id=r["id"], company_id=CID, counterparty_id=f"{CID}-cust-{r['cust'] + 1:02d}", invoice_no=r["no"], issue_date=r["date"], due_date=r["due"], currency="GBP", fx_rate="1.00000000",
                  total=money(r["total"]), tax=money(r["vat"]), total_base=money(r["total"]), status="paid" if pay_ok else ("written_off" if wo else "open"),
                  journal_entry_id=r["entry"] if v == "truth" else b.bid(r["entry"]), memo="Trade invoice", source_system=ss, source_file="truth" if v == "truth" else "qbo_invoices.csv", source_row_id=r["no"])
            if pay_ok:
                T.add("invoice_payments", id=f"ipay-{CID}-{v[0]}{r['no']}", company_id=CID, invoice_id=r["id"], payment_date=r["pay_actual"], amount=money(r["total"]), currency="GBP", fx_rate="1.00000000", amount_base=money(r["total"]),
                      method="bank", deposit_account_id=canon.acct_id(CID, w, "bank"), journal_entry_id=r["pay_entry"] if v == "truth" else b.bid(r["pay_entry"]), source_system=ss, source_file="truth" if v == "truth" else "bank_csv", source_row_id=r["no"])
        for x in sim.bills:
            T.add("bills", id=x["id"], company_id=CID, counterparty_id=f"{CID}-sup-{x['sup'] + 1:02d}", bill_no=x["no"], issue_date=x["date"], due_date=x["due"], currency="GBP", fx_rate="1.00000000", total=money(x["total"]),
                  tax=money(x["vat"]), total_base=money(x["total"]), status="paid" if x.get("pay_entry") else "open", expense_account_id=canon.acct_id(CID, w, "inv"),
                  journal_entry_id=x["entry"] if v == "truth" else b.bid(x["entry"]), memo="Supplier bill", source_system=ss, source_file="truth" if v == "truth" else "qbo_bills.csv", source_row_id=x["no"])
            if x.get("pay_entry"):
                T.add("bill_payments", id=f"bpay-{CID}-{v[0]}{x['no']}", company_id=CID, bill_id=x["id"], payment_date=x["pay_date"], amount=money(x["total"]), currency="GBP", fx_rate="1.00000000", amount_base=money(x["total"]),
                      method="bank", paid_from_account_id=canon.acct_id(CID, w, "bank"), journal_entry_id=x["pay_entry"] if v == "truth" else b.bid(x["pay_entry"]), source_system=ss, source_file="truth" if v == "truth" else "bank_csv", source_row_id=x["no"])
        for pr in sim.payroll:
            m = pr["month"]
            tax = pr["tax"]
            for i, g in pr["rows"]:
                T.add("payroll_runs", id=f"pr-{CID}-{v[0]}{m:%Y%m}-{i + 1:02d}", company_id=CID, run_ref=f"WAGES{m:%Y%m}", period_start=m, period_end=month_end(m), pay_date=pr["pay_date"], employee_id=f"{CID}-emp-{i + 1:02d}",
                      gross=money(g), deductions="0.00", employer_costs=money(rnd(tax / len(pr["rows"]))), net=money(g), currency="GBP", fx_rate="1.00000000", amount_base=money(g + rnd(tax / len(pr["rows"]))),
                      paid_from_account_id=canon.acct_id(CID, w, "bank"), journal_entry_id=pr["entry"] if v == "truth" else b.bid(pr["entry"]), source_system=ss, source_file="truth" if v == "truth" else "qbo_journal_entries.csv", source_row_id=f"{m:%Y%m}-{i}")
    # findings: trade invoices > 90 days overdue
    from key import asof_dates
    findings, k = [], 0
    for asof in asof_dates():
        for r in sim.trade:
            if r["date"] > asof or (r.get("paid") and r["pay_actual"] <= asof) or (r.get("written_off") and r["written_off"] <= asof):
                continue
            late = (asof - r["due"]).days
            if late > 90:
                k += 1
                findings.append(dict(id=f"{CID}-S{k:05d}", type="stale_receivable", subtype="overdue_over_90_days", date=asof.isoformat(), as_of=asof.isoformat(), amount=money(r["total"]), currency="GBP", amount_base=money(r["total"]),
                                     description=f"{TRADE[r['cust']]} invoice {r['no']} is {late} days past due at {asof}", truth_entries=[r["entry"]], book_entries=[], affected_source_rows=[],
                                     fix={"void_book_entries": [], "add_entries": [], "note": "no ledger change; chase or provide for the debt"}, details=dict(invoice=r["id"], eventually_paid=bool(r.get("paid")), never_collected=bool(r["never"]))))
    return dict(trace=trace, findings=findings, key_extra={})
