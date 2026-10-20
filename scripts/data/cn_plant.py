"""cn_xborder: planted data-entry problems (the bookkeeper's Excel books) and honest noise."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from cn import CID, CH, D1, Sim, WISE, B2B_CUST
from core import Book, Entry, Problems, days, money, month_end, rng_for, rnd, ym


def _spec(e: Entry) -> list[tuple]:
    return [(l.acct, l.nat, l.ccy) for l in e.lines]


def plant(sim: Sim, seed: int, A=None) -> None:
    from core import Anchors
    A = A or Anchors()
    w = sim.world
    b = sim.book = Book(w)
    P = sim.problems = Problems(CID, w, b)
    rng = rng_for(seed, CID, "plant")
    M = sim.meta
    ent = P.tmap
    touched: set[str] = set()

    def clr_total(ids: list[str]) -> int:
        return sum(l.cr for i in ids for l in ent[i].lines if l.acct.startswith("clr_"))

    # --- reserve windows: the books ignore the marketplace's held-back reserve
    for (c, k), pid_ in sorted(M["pay_ids"].items(), key=lambda x: (x[0][1], x[0][0])):
        info = M["pay_info"][pid_]
        a_, b_ = M["periods"][k]
        in_win = [i for i, (ws, we, pct) in enumerate(M["reserve_windows"]) if c == "US" and ws <= a_ <= we]
        if in_win and in_win[0] == 0 and info["reserve_new"] != info["reserve_old"]:
            e = ent[pid_]
            p_ = e.lines[0].dr
            newp = info["net"]
            b.alter(pid_, lines=[b.make_line(WISE["USD"], newp, e.date), b.make_line("clr_us", -newp, e.date)])
            touched.add(pid_)
            P.plant("timing_difference", e.date, f"Marketplace held back part of settlement {k:03d} as a reserve; the books recorded the full amount as received",
                    truth=[pid_], book=[b.bid(pid_)], amount=abs(newp - p_), ccy="USD", subtype="reserve_held_back", settlement=k)
    # --- fees deducted from payouts but booked as full sales
    for (c, k), pid_ in sorted(M["pay_ids"].items(), key=lambda x: (x[0][1], x[0][0])):
        if c == "STORE" or pid_ in touched or rng.random() > 0.045:
            continue
        fids = M["fee_ids"][(c, k)]
        tot = clr_total(fids)
        if tot == 0:
            continue
        e = ent[pid_]
        p_ = e.lines[0].dr
        ccy = CH[c]["ccy"]
        for f in fids:
            b.drop(f)
        b.alter(pid_, lines=[b.make_line(WISE[ccy], p_ + tot, e.date), b.make_line(CH[c]["clr"], -(p_ + tot), e.date)])
        touched.add(pid_)
        P.plant("missing_marketplace_fee", e.date, f"Settlement {k:03d} ({CH[c]['mkt']}): referral, fulfilment, advertising and storage fees were deducted from the payout but the books show the sales as received in full",
                truth=fids + [pid_], book=[b.bid(pid_)], amount=tot, ccy=ccy, subtype="fees_netted_from_payout", settlement=k, channel=c)
    # --- payouts booked at settlement close instead of deposit date
    for (c, k), pid_ in sorted(M["pay_ids"].items(), key=lambda x: (x[0][1], x[0][0])):
        if pid_ in touched or rng.random() > 0.20:
            continue
        e = ent[pid_]
        nd = e.date - dt.timedelta(days=2)
        b.alter(pid_, date=nd)
        touched.add(pid_)
        P.plant("timing_difference", e.date, f"Settlement {k:03d} ({CH[c]['mkt']}) booked on the settlement close date, 2 days before the money reached the account",
                truth=[pid_], book=[b.bid(pid_)], amount=e.lines[0].dr, ccy=CH[c]["ccy"], subtype="booked_at_close_date", shift_days=-2, channel=c)
    # --- FX: bookkeeper converts at the monthly average, bank paid spot minus spread
    avg_rate: dict[tuple, float] = {}
    for m_ in sorted(M["conv_ids"]):
        ids = M["conv_ids"][m_]
        if rng.random() > 0.55:
            continue
        d0 = dt.date(int(m_[:4]), int(m_[5:]), 1)
        diff = 0
        for i in ids:
            e = ent[i]
            ccy = e.tags["ccy"]
            if (m_, ccy) not in avg_rate:
                ds = list(days(d0, month_end(d0)))
                avg_rate[(m_, ccy)] = sum(A.cny_per(ccy, x) for x in ds) / len(ds)
            wl = next(l for l in e.lines if l.acct.startswith("wise"))
            bank_l = next(l for l in e.lines if l.acct == "bank")
            new_cny = rnd(wl.cr * avg_rate[(m_, ccy)])
            lines = [b.make_line("bank", new_cny, e.date), b.make_line(wl.acct, -wl.cr, e.date)]
            lines[1].base = -new_cny
            b.alter(i, lines=lines)
            diff += new_cny - bank_l.dr
        P.plant("fx_misbooking", month_end(d0), f"Conversions in {m_} booked at the monthly average rate; the account received spot less the spread, so CNY bank is overstated in the books",
                truth=ids, book=[b.bid(i) for i in ids], amount=diff, ccy="CNY", month=m_, subtype="monthly_average_rate")
    for m_ in sorted(M["freight_ids"]):
        if rng.random() > 0.25:
            continue
        d0 = dt.date(int(m_[:4]), int(m_[5:]), 1)
        qs = dt.date(d0.year, 3 * ((d0.month - 1) // 3) + 1, 1) - dt.timedelta(days=90)
        stale = A.cny_per("USD", max(qs, dt.date(2020, 1, 1)))
        tot = 0
        ids = M["freight_ids"][m_]
        for i in ids:
            e = ent[i]
            fl = next(l for l in e.lines if l.acct == "freight")
            wl = next(l for l in e.lines if l.acct == "wise_usd")
            new_exp = rnd(fl.dr * stale)
            lines = [b.make_line("freight", fl.dr, e.date, "USD"), b.make_line("wise_usd", -fl.dr, e.date, "USD")]
            lines[0].base, lines[0].fx = new_exp, round(stale, 8)
            lines[1].base = wl.base
            plug = -(new_exp + wl.base)
            if plug:
                lines.append(b.make_line("fx_gl", plug, e.date, "CNY"))
                lines[-1].base = plug
                lines[-1].fx = 1.0
            b.alter(i, lines=lines)
            tot += abs(new_exp - fl.base)
        P.plant("fx_misbooking", month_end(d0), f"Freight invoices for {m_} are in USD but were booked at a stale quarterly rate; the expense is mis-stated",
                truth=ids, book=[b.bid(i) for i in ids], amount=tot, ccy="CNY", month=m_, subtype="freight_stale_rate")
    # --- petty cash / WeChat payments to small suppliers never reach the books
    for m_, ids in sorted(M["samples_unrec"].items()):
        for i in ids:
            b.drop(i)
        tot = sum(ent[i].lines[0].dr for i in ids)
        P.plant("unrecorded_cash_expense", ent[ids[-1]].date, f"{len(ids)} sample, tooling and small supplier payments in {m_} made in cash or by personal WeChat and never entered",
                truth=ids, amount=tot, ccy="CNY", month=m_, evidence="monthly petty-cash count")
    # --- factory deposit booked twice (sheet and bank file both list it)
    for eid, d, dep in M["deposit_ids"]:
        if rng.random() < 0.05:
            e = ent[eid]
            n = b.add(e.date, _spec(e), ref=e.ref + "-dup", memo=e.memo + " (second row)", kind="dup_deposit")
            P.plant("duplicate_entry", e.date, f"Factory deposit {e.ref} listed once in the PO tracker and again from the bank file; booked twice", book=[n.id], amount=dep, ccy="CNY",
                    subtype="deposit_booked_twice", original_truth_id=eid)
    # --- wholesale customer payments in EUR not recorded; reimbursements not recorded
    for r in sim.b2b:
        if r.get("paid") and rng.random() < 0.12:
            b.drop(r["pay_entry"])
            P.plant("unrecorded_receipt", r["pay_actual"], f"{B2B_CUST[r['cust']]} paid invoice {r['no']} into the EUR balance; the Excel receivables sheet still shows it unpaid",
                    truth=[r["pay_entry"]], amount=r["total"], ccy="EUR", subtype="wholesale_payment_not_recorded", invoice=r["id"])
    for (c, k), ids in sorted(M["reimb_ids"].items(), key=lambda x: (x[0][1], x[0][0])):
        if rng.random() < 0.45:
            for i in ids:
                b.drop(i)
            tot = sum(ent[i].lines[0].dr for i in ids)
            e = ent[ids[-1]]
            P.plant("unrecorded_receipt", e.date, f"Marketplace reimbursed lost/damaged units in settlement {k:03d} ({CH[c]['mkt']}); never entered as income",
                    truth=ids, amount=tot, ccy=CH[c]["ccy"], subtype="reimbursement_not_recorded", settlement=k, channel=c)
    # --- wholesale invoices never collected
    for r in sim.b2b:
        if r.get("written_off"):
            b.drop(r["wo_entry"])
            P.plant("phantom_receivable", r["written_off"], f"{B2B_CUST[r['cust']]} invoice {r['no']} ({money(r['total'])} EUR) unpaid 200+ days after due and never chased; still counted as receivable",
                    truth=[r["wo_entry"]], amount=r["total"], ccy="EUR", invoice=r["id"], due=str(r["due"]))
    # --- refunds booked as an expense
    for (c, k), ids in sorted(M["refund_ids"].items(), key=lambda x: (x[0][1], x[0][0])):
        if rng.random() < 0.06:
            for i in ids:
                e = b.get(i)
                for l in e.lines:
                    if l.acct == "refunds":
                        l.acct = "refund_exp"
            tot = sum(ent[i].lines[0].dr for i in ids)
            P.plant("misclassified_expense", ent[ids[-1]].date, f"Customer refunds for settlement {k:03d} booked as an expense instead of a deduction from sales (revenue and expenses both overstated)",
                    truth=ids, book=[b.bid(i) for i in ids], amount=tot, ccy=CH[c]["ccy"], wrong_account="refund_exp", right_account="refunds", channel=c)
    # --- lost / damaged stock never claimed or written down
    for m_, tid in sorted(M["shrink_ids"].items()):
        b.drop(tid)
        P.plant("inventory_shrinkage", ent[tid].date, f"Units lost or damaged at fulfilment centres in {m_} and not reimbursed; never written off, stock is overstated",
                truth=[tid], amount=ent[tid].lines[0].dr, ccy="CNY", month=m_)

    # ------------------------------------------------------------------ honest noise
    for kind, d, ids in sim.problems_noise:
        P.note(kind, d, "Owner paid funds into the CNY account to cover payments: real and recorded", "Equity entry + bank deposit both present", ids)
    for (c, k), pid_ in sorted(M["pay_ids"].items(), key=lambda x: (x[0][1], x[0][0])):
        a_, b_ = M["periods"][k]
        ws, we, pct = M["reserve_windows"][1]
        if c == "US" and ws <= a_ <= we and M["pay_info"][pid_]["reserve_new"] != M["pay_info"][pid_]["reserve_old"]:
            P.note("reserve_held_recorded", ent[pid_].date, "Marketplace holds back part of the settlement as a reserve (payout smaller than the sales)", "Recorded correctly at the amount actually deposited", [pid_])
    rec = sorted(sim.purchases, key=lambda r: -r["total"])[:6]
    for r in rec:
        if "receipt_entry" in r:
            P.note("large_pre_peak_purchase", ent[r["receipt_entry"]].date, "Very large factory balance payment ahead of Q4 / Chinese New Year", "Real seasonal stock build; deposit and balance both on the bank statement", [r["dep_entry"], r["receipt_entry"]])
    conv_sample = [i for m_ in sorted(M["conv_ids"]) for i in M["conv_ids"][m_] if b.get(i) and b.get(i).lines[0].dr == ent[i].lines[0].dr][:6]
    for i in conv_sample:
        P.note("conversion_spread", ent[i].date, "Wise-style conversion delivered about 0.55% fewer CNY than the mid-market rate", "Normal spread; recorded at the amount received", [i])
    freights = sorted([i for ids in M["freight_ids"].values() for i in ids], key=lambda i: -ent[i].lines[0].dr)[:4]
    for i in freights:
        P.note("freight_spike", ent[i].date, "Unusually high freight invoice (container-rate spike in 2021-22)", "Real cost driven by supply-chain pressure; recorded at the invoiced USD amount", [i])
    for e in w.entries:
        if e.kind == "loan_drawdown":
            P.note("loan_drawdown", e.date, "Large unexplained-looking deposit into the CNY account", "Working-capital loan for the peak-season stock build; liability recorded", [e.id])
    for kk, (a_, b_) in sorted(M["periods"].items())[-1:]:
        pass
