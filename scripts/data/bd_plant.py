"""bd_grocer: planted data-entry problems (edits to the owner's books) and honest noise."""
from __future__ import annotations

import datetime as dt

import calendars as cal
from bd import CID, D1, Sim
from core import Book, Entry, Problems, rng_for


def _spec(e: Entry) -> list[tuple]:
    return [(l.acct, l.nat, l.ccy) for l in e.lines]


def plant(sim: Sim, seed: int) -> None:
    w = sim.world
    b = sim.book = Book(w)
    P = sim.problems = Problems(CID, w, b)
    rng = rng_for(seed, CID, "plant")
    M = sim.meta
    ent = P.tmap

    # 1. cash expenses nobody wrote down (monthly groups)
    for m, ids in sorted(M["unrec_cash"].items()):
        for i in ids:
            b.drop(i)
        tot = sum(ent[i].lines[0].dr for i in ids)
        P.plant("unrecorded_cash_expense", ent[ids[-1]].date,
                f"{len(ids)} small cash expenses in {m} (porter, tea, rickshaw, chanda, tips) paid from the till or bank and never written in the notebook",
                truth=ids, amount=tot, ccy="BDT", month=m, evidence="cash count at month end")
    # 2. wallet fees never recorded (70% of months)
    for m, ids in sorted(M["fee_entries"].items()):
        if rng.random() > 0.85:
            continue
        tids, tot = [], 0
        for i in ids:
            tot += ent[i].lines[0].dr
            b.drop(i)
            tids.append(i)
        P.plant("unrecorded_fee", ent[tids[-1]].date,
                f"bKash merchant charges and send-money charges for {m} deducted by the wallet but never entered (sales typed gross)",
                truth=tids, amount=tot, ccy="BDT", month=m, subtype="wallet_fees")
    # 3. owner's personal spending through the shop wallet
    for m, ids in sorted(M["personal"].items()):
        for i in ids:
            b.drop(i)
        tot = sum(ent[i].lines[0].dr for i in ids)
        P.plant("personal_spend_mixed", ent[ids[-1]].date,
                f"Owner's household payments made from the shop wallet in {m}; not in the notebook (should be owner drawings)",
                truth=ids, amount=tot, ccy="BDT", month=m)
    # 4. bKash receipts typed as cash sales
    for m, ids in sorted(M["wallet_sale_ids"].items()):
        if rng.random() > 0.18:
            continue
        pick = [i for i in ids if rng.random() < 0.20]
        if not pick:
            continue
        for i in pick:
            e = b.get(i)
            e.lines = [b.make_line("cash" if l.acct == "wallet" else l.acct, l.nat, e.date) for l in e.lines]
        tot = sum(ent[i].lines[0].dr for i in pick)
        P.plant("misrouted_receipt", ent[pick[-1]].date,
                f"bKash customer payments on {len(pick)} days in {m} typed into the notebook as cash sales (cash overstated, wallet understated)",
                truth=pick, book=[b.bid(i) for i in pick], amount=tot, ccy="BDT", month=m, subtype="wallet_booked_as_cash")
    # 5. duplicate purchases (block pasted twice) and advance recorded twice
    for tid, acct, amt in M["purchases"]:
        if acct in ("cash", "bank") and rng.random() < 0.002:
            e = ent[tid]
            n = b.add(e.date, _spec(e), ref=e.ref + "-dup", memo=e.memo, kind="dup_purchase")
            P.plant("duplicate_entry", e.date, f"Purchase '{e.memo}' entered twice in the notebook (block pasted again)",
                    book=[n.id], amount=amt, ccy="BDT", subtype="pasted_twice", original_truth_id=tid)
    for tid, amt in M["adv_consume"]:
        if rng.random() < 0.05:
            e = ent[tid]
            n = b.add(e.date, [("inv", amt), ("cash", -amt)], ref=e.ref + "-dup", memo="Goods received (paid cash again)", kind="dup_advance")
            P.plant("duplicate_entry", e.date, "Wholesaler advance (dadon) recorded when paid, then recorded again as a cash purchase when the goods arrived",
                    book=[n.id], amount=amt, ccy="BDT", subtype="advance_recorded_twice", original_truth_id=tid)
    # 6. timing: deposits entered a day or two early/late
    for tid, d in M["deposits"]:
        r = rng.random()
        shift = -int(rng.integers(1, 3)) if (d.day == 1 and r < 0.55) else (int(rng.integers(1, 3)) if (d.day == 15 and r < 0.30) else 0)
        if shift and d + dt.timedelta(days=shift) <= D1:
            b.alter(tid, date=d + dt.timedelta(days=shift))
            P.plant("timing_difference", d, f"Cash deposit entered in the notebook {abs(shift)} day(s) {'before' if shift < 0 else 'after'} the bank date",
                    truth=[tid], book=[b.bid(tid)], amount=ent[tid].lines[0].dr, ccy="BDT", shift_days=shift)
    # 7. baki that will never be paid -> never written off
    for wo in sim.writeoffs:
        h = sim.households[wo["hh"]]
        b.drop(wo["entry"])
        P.plant("phantom_receivable", wo["date"],
                f"{h.name}: baki dues of {wo['amount'] // 100} taka, no payment since {h.default_from}; still shown as receivable",
                truth=[wo["entry"]], amount=wo["amount"], ccy="BDT", household=h.cid, last_payment_before=str(h.default_from))
    # 8. baki payments by bKash with no reference -> not recorded
    for tid in M["wallet_cust_pay"]:
        if rng.random() < 0.03:
            e = ent[tid]
            b.drop(tid)
            P.plant("unrecorded_receipt", e.date,
                    f"Customer paid baki by bKash ({e.memo}); the sheet shows no payment, so the customer still looks like a debtor",
                    truth=[tid], amount=e.lines[0].dr, ccy="BDT", household=sim.households[e.tags["hh"]].cid)
    # 9. misclassified expenses
    for m, tid in sorted(M["delivery_wage_ids"].items()):
        if rng.random() < 0.30:
            e = b.get(tid)
            for l in e.lines:
                if l.acct == "sal":
                    l.acct = "deliv"
            P.plant("misclassified_expense", ent[tid].date, f"Delivery boys' wages for {m} booked as delivery and transport instead of salaries",
                    truth=[tid], book=[b.bid(tid)], amount=ent[tid].lines[0].dr, ccy="BDT", wrong_account="deliv", right_account="sal")
    for m, ids in sorted(M["misclass_cands"].items()):
        for tid in ids:
            if rng.random() < 0.3:
                e = b.get(tid)
                for l in e.lines:
                    if l.acct == "repair":
                        l.acct = "misc"
                P.plant("misclassified_expense", ent[tid].date, "Repair bill booked under small cash expenses",
                        truth=[tid], book=[b.bid(tid)], amount=ent[tid].lines[0].dr, ccy="BDT", wrong_account="misc", right_account="repair")
    # 10. spoilage and shrinkage never recorded
    for m, tid in sorted(M["shrink_ids"].items()):
        b.drop(tid)
        P.plant("inventory_shrinkage", ent[tid].date, f"Spoilage, egg breakage and loose-goods weight loss in {m} never written off; book stock is too high",
                truth=[tid], amount=ent[tid].lines[0].dr, ccy="BDT", month=m)

    # ------------------------------------------------------------------ honest noise (NOT problems)
    kinds = {"owner_injection": "Owner paid money into the bank to cover a cash shortfall: real, recorded in book and bank, not an unexplained receipt",
             "large_equipment_purchase": "One-off chiller and freezer purchase financed by the new loan: unusual size but properly recorded",
             "legit_repeat_purchase": "Two identical purchases from the same wholesaler on the same day: both are real (two deliveries), so this is NOT a duplicate"}
    why = {"owner_injection": "Matches a bank deposit and an equity entry", "large_equipment_purchase": "Matches loan disbursement and bank payment",
           "legit_repeat_purchase": "Both payments appear on the statement / cash count"}
    for kind, d, ids in sim.problems_noise:
        P.note(kind, d, kinds[kind], why[kind], ids)
    dep_sample = [(tid, d) for tid, d in M["deposits"] if d.day == 15][:12]
    for tid, d in dep_sample:
        if b.get(tid) and b.get(tid).date == d:
            P.note("round_cash_deposit", d, "Round-sum cash deposit on the 15th that does not equal that day's sales",
                   "Matches the cash left in the till; recorded on the bank date", [tid])
    big = sorted([x for x in M["purchases"] if 0 <= (cal.EID_FITR[ent[x[0]].date.year] - ent[x[0]].date).days <= 24], key=lambda x: -x[2])[:8]
    for tid, acct, amt in big:
        P.note("seasonal_stock_up", ent[tid].date, "Very large pre-Eid stock purchase", "Real seasonal build; paid from bank/cash and matched on the statement", [tid])
