"""uk_garden: planted problems in the QuickBooks-style books, and honest noise."""
from __future__ import annotations

import datetime as dt

from core import Book, Entry, Problems, rng_for
from uk import CID, TRADE


def _spec(e: Entry) -> list[tuple]:
    return [(l.acct, l.nat, l.ccy) for l in e.lines]


def plant(sim, seed: int) -> None:
    w = sim.world
    b = sim.book = Book(w)
    P = sim.problems = Problems(CID, w, b)
    rng = rng_for(seed, CID, "plant")
    M = sim.meta
    ent = P.tmap
    fee_touched: set[str] = set()
    # 1. Square deposits posted net: fee never shown as an expense (50% of months)
    for m, ids in sorted(M["fee_pay"].items()):
        if rng.random() > 0.5:
            continue
        tot = 0
        for i in ids:
            e = b.get(i)
            fl = next(l for l in e.lines if l.acct == "fees")
            tot += fl.dr
            e.lines = [b.make_line("bank", next(l for l in e.lines if l.acct == "bank").nat, e.date), b.make_line("clr", -next(l for l in e.lines if l.acct == "bank").nat, e.date)]
        fee_touched.update(ids)
        P.plant("unrecorded_fee", ent[ids[-1]].date, f"Square deposits in {m} posted at the net amount; card fees never shown as an expense, and the clearing account never clears",
                truth=ids, book=[b.bid(i) for i in ids], amount=tot, ccy="GBP", month=m, subtype="pos_fees_netted")
    # 2. petty cash never entered
    for m, ids in sorted(M["unrec"].items()):
        for i in ids:
            b.drop(i)
        P.plant("unrecorded_cash_expense", ent[ids[-1]].date, f"{len(ids)} petty-cash payments in {m} (delivery driver, cleaning, float shortages) not entered",
                truth=ids, amount=sum(ent[i].lines[0].dr for i in ids), ccy="GBP", month=m)
    # 3. bank charges and loan interest unrecorded
    for m, ids in sorted(M["bankfee_ids"].items()):
        if rng.random() < 0.6:
            for i in ids:
                b.drop(i)
            P.plant("unrecorded_fee", ent[ids[-1]].date, f"Bank charges for {m} on the statement but not in QuickBooks", truth=ids, amount=sum(ent[i].lines[0].dr for i in ids), ccy="GBP", month=m, subtype="bank_charges")
    for i in M["loan_int_ids"]:
        if rng.random() < 0.5:
            b.drop(i)
            P.plant("unrecorded_fee", ent[i].date, "Loan interest taken by the bank but not recorded", truth=[i], amount=ent[i].lines[0].dr, ccy="GBP", subtype="loan_interest")
    # 4. trade debtors: never collected; BACS without remittance
    for r in sim.trade:
        if r.get("wo_entry"):
            b.drop(r["wo_entry"])
            P.plant("phantom_receivable", r["written_off"], f"{TRADE[r['cust']]} invoice {r['no']} never paid; open in QuickBooks 170+ days after due", truth=[r["wo_entry"]], amount=r["total"], ccy="GBP", invoice=r["id"])
    for i in M["trade_pay"]:
        if rng.random() < 0.08:
            e = ent[i]
            b.drop(i)
            P.plant("unrecorded_receipt", e.date, f"BACS receipt without remittance advice ({e.memo}); invoice still shown as open", truth=[i], amount=e.lines[0].dr, ccy="GBP")
    # 5. supplier bill entered twice (emailed PDF + manual)
    for e in sim.world.entries:
        if e.kind == "bill" and rng.random() < 0.03:
            n = b.add(e.date, _spec(e), ref=e.ref + "-PDF", memo=e.memo + " (emailed PDF)", kind="dup_bill")
            P.plant("duplicate_entry", e.date, f"Bill {e.ref} entered twice with different references", book=[n.id], amount=e.lines[0].dr, ccy="GBP", subtype="bill_entered_twice", original_truth_id=e.id)
    # 6. gift card sales taken to revenue
    for m, ids in sorted(M["gift_days"].items()):
        if rng.random() < 0.4:
            for i in ids:
                e = b.get(i)
                for l in e.lines:
                    if l.acct == "gift":
                        l.acct = "sales"
            P.plant("gift_card_misbooked", ent[ids[-1]].date, f"Gift card sales in {m} credited to sales instead of gift-card liability (redemptions are counted again later)",
                    truth=ids, book=[b.bid(i) for i in ids], amount=sum(d["gift_sold"] for d in sim.day.values() if d["entry"] in ids) or 1, ccy="GBP", month=m)
    # 7. supplier credit notes booked as income
    for i in M["credit_notes"]:
        if rng.random() < 0.7:
            e = b.get(i)
            for l in e.lines:
                if l.acct == "cogs":
                    l.acct = "othinc"
            P.plant("misclassified_expense", ent[i].date, "Supplier credit note booked as other income instead of reducing cost of sales", truth=[i], book=[b.bid(i)], amount=ent[i].lines[0].dr, ccy="GBP", wrong_account="othinc", right_account="cogs")
    # 8. dead stock never written down
    for m, i in sorted(M["shrink_ids"].items()):
        b.drop(i)
        P.plant("inventory_shrinkage", ent[i].date, f"Plant wastage and dead stock in {m} never written off", truth=[i], amount=ent[i].lines[0].dr, ccy="GBP", month=m)
    # 9. timing: Square deposit booked on the sale day
    for pay in sim.payouts:
        i = pay["entry"]
        if i not in fee_touched and b.get(i) and b.get(i).date == pay["date"] and rng.random() < 0.06:
            nd = pay["date"] - dt.timedelta(days=1)
            b.alter(i, date=nd)
            P.plant("timing_difference", pay["date"], "Square deposit booked on the sale day, a day before the money reached the bank", truth=[i], book=[b.bid(i)], amount=pay["gross"] - pay["fee"], ccy="GBP", shift_days=-1)
    # honest noise
    for kind, d, ids in sim.problems_noise:
        P.note(kind, d, "Owner paid money into the bank", "Recorded in books and bank", ids)
    last = sim.payouts[-1]
    P.note("payout_in_transit", last["date"], "Card sales of the last trading days have not been paid out by Square at period end", "Square clearing balance is correct; money arrives next business day", [last["entry"]])
    for e in sim.world.entries:
        if e.kind == "vat_payment" and e.date.month == 11:
            P.note("vat_payment", e.date, "Large quarterly VAT payment", "Predictable quarterly cash outflow, recorded", [e.id])
        if e.kind == "loan_drawdown":
            P.note("loan_drawdown", e.date, "Large deposit into the bank", "Term loan, liability recorded", [e.id])
