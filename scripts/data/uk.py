"""uk_garden: Hollinbank Garden Centre (Yorkshire). GBP. SYNTHETIC - no real firm.
Square-style POS (order level from ORDER_START, daily summaries before), QuickBooks-style books, bank CSV."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

import calendars as cal
from core import (D0, D1, Acct, Anchors, Book, Problems, World, add_months, days, month_end, rng_for, rnd, scale_to_targets, weighted_split, ym)

CID = "uk_garden"
COMPANY = dict(name="Hollinbank Garden Centre", country="GB", base_currency="GBP", industry="garden_centre", fiscal_year_start_month=4, founded_on="2004-04-01",
               description="Independent Yorkshire garden centre; Square-style POS, QuickBooks Online-style books, bank CSV, trade accounts.")
P_ = 100
ORDER_START = dt.date(2026, 4, 1)
NET_TARGET = {2020: 1.12e6, 2021: 1.52e6, 2022: 1.58e6, 2023: 1.55e6, 2024: 1.60e6, 2025: 1.66e6, 2026: 1.38e6}
COST_SCALE = {2020: 0.80, 2021: 0.84, 2022: 0.90, 2023: 0.96, 2024: 1.0, 2025: 1.04, 2026: 1.07}
MONTH_W = {1: .045, 2: .05, 3: .095, 4: .14, 5: .16, 6: .12, 7: .085, 8: .075, 9: .07, 10: .075, 11: .07, 12: .095}
CLOSED = [(dt.date(2020, 3, 24), dt.date(2020, 5, 12)), (dt.date(2020, 11, 5), dt.date(2020, 12, 2)), (dt.date(2021, 1, 6), dt.date(2021, 4, 11))]
ACCTS = [
    Acct("till", "1000", "Tills and safe", "asset", "cash", "GBP"), Acct("bank", "1100", "Business current account", "asset", "bank", "GBP"),
    Acct("clr", "1150", "Square clearing (card sales awaiting payout)", "asset", "other_asset", "GBP"), Acct("ar", "1200", "Trade debtors", "asset", "ar", "GBP"),
    Acct("inv", "1300", "Stock", "asset", "inventory", "GBP"), Acct("fa", "1500", "Polytunnels and equipment", "asset", "fixed_asset", "GBP"),
    Acct("ap", "2000", "Trade creditors", "liability", "ap", "GBP"), Acct("vat", "2200", "VAT control", "liability", "tax_payable", "GBP"),
    Acct("gift", "2250", "Gift cards outstanding", "liability", "other_liability", "GBP"), Acct("loan", "2300", "Bank loan", "liability", "loan", "GBP"),
    Acct("cap", "3000", "Owner's capital", "equity", "owner_equity", "GBP"), Acct("draw", "3100", "Owner's drawings", "equity", "drawings", "GBP"),
    Acct("sales", "4000", "Sales", "income", "revenue", "GBP"), Acct("othinc", "4900", "Other income", "income", "other_income", "GBP"),
    Acct("cogs", "5000", "Cost of sales", "expense", "cogs", "GBP"), Acct("shrink", "5100", "Plant wastage and stock write-offs", "expense", "cogs", "GBP"),
    Acct("sal", "6000", "Wages, PAYE and NI", "expense", "payroll", "GBP"), Acct("rent", "6100", "Rent and business rates", "expense", "rent", "GBP"),
    Acct("util", "6200", "Energy and water", "expense", "utilities", "GBP"), Acct("mkt", "6300", "Marketing", "expense", "marketing", "GBP"),
    Acct("fees", "6400", "Card processing fees", "expense", "opex", "GBP"), Acct("bankfee", "6410", "Bank charges", "expense", "opex", "GBP"),
    Acct("repair", "6500", "Repairs and maintenance", "expense", "opex", "GBP"), Acct("prof", "6600", "Professional fees, software and insurance", "expense", "opex", "GBP"),
    Acct("misc", "6700", "Petty cash expenses", "expense", "opex", "GBP"), Acct("intr", "6800", "Loan interest", "expense", "interest", "GBP"),
    Acct("bad", "6900", "Bad debts", "expense", "other_expense", "GBP"),
]
SUPPLIERS = ["Pennine Plant Growers", "Wharfedale Nurseries", "GreenSoil Peat-Free Compost", "Aire Valley Pots & Ware", "Moorland Garden Tools", "Dales Outdoor Furniture", "Northern Christmas Trade", "Calder Seeds & Bulbs"]
TRADE = ["Ilkley Landscapes", "Harrogate Garden Design", "Skipton Grounds Care", "Otley Paving & Planting", "Leeds Lawn & Border", "Wetherby Estates Gardening", "Pateley Bridge Landscaping", "Halifax Hedging Co",
         "Saltaire Garden Services", "York Vale Landscapes", "Burley Tree Surgeons", "Menston Patios"]
# sku, name, category, price(p ex VAT), vat rate, weight by month group
CATALOG = [("PL-BED6", "Bedding plants 6-pack", "plants", 395, 20), ("PL-SHRUB", "Shrub 3L pot", "plants", 1295, 20), ("PL-PERN", "Perennial 2L", "plants", 795, 20), ("PL-ROSE", "Rose bush", "plants", 1495, 20),
           ("PL-HERB", "Herb and veg plant", "plants", 295, 0), ("SD-SEED", "Seed packet", "plants", 249, 0), ("PL-TREE", "Fruit tree", "plants", 2995, 20), ("CP-PEAT", "Peat-free compost 50L", "compost", 795, 20),
           ("CP-TOP", "Topsoil bulk bag", "compost", 1495, 20), ("PT-TERRA", "Terracotta pot", "pots", 895, 20), ("PT-PLAS", "Plastic planter", "pots", 495, 20), ("PT-GLAZ", "Glazed pot large", "pots", 2495, 20),
           ("TL-HAND", "Hand tool", "tools", 1195, 20), ("TL-MOW", "Cordless trimmer", "tools", 8995, 20), ("TL-GLOVE", "Gardening gloves", "tools", 595, 20), ("FU-BENCH", "Garden bench", "furniture", 14995, 20),
           ("FU-SET", "Patio set", "furniture", 39995, 20), ("FU-BBQ", "Barbecue", "furniture", 17995, 20), ("XM-TREE", "Christmas tree", "christmas", 4995, 20), ("XM-DEC", "Christmas decorations", "christmas", 1295, 20),
           ("XM-LIGHT", "Lights set", "christmas", 1995, 20), ("OT-FOOD", "Bird food", "other", 695, 20), ("OT-GIFT", "Gift item", "other", 1495, 20)]
CAT_W = {"plants": [1, 1, 3, 4, 4, 3, 2, 2, 2, 2, 1, 1], "compost": [.5, .5, 2, 3, 3, 2, 1, 1, 1, 1, .5, .3], "pots": [.5, .5, 2, 3, 3, 2, 1.5, 1.2, 1, 1, .8, .8],
         "tools": [.6, .6, 1.2, 1.5, 1.5, 1.2, 1, 1, 1, 1, .8, 1], "furniture": [.1, .2, 1, 2, 2.5, 2.5, 2, 1.5, .6, .3, .2, .2], "christmas": [0, 0, 0, 0, 0, 0, 0, .1, .4, 1.5, 4, 4], "other": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1.5, 2]}
EMP_BASE = [2500, 2300, 2250, 2100, 2100, 2000, 1900, 1800, 1750, 1700, 1650, 1550]  # monthly GBP at 2025 scale (some part-time)


@dataclass
class Sim:
    world: World
    book: Book = None  # type: ignore
    problems: Problems = None  # type: ignore
    meta: dict = field(default_factory=dict)
    day: dict = field(default_factory=dict)
    orders: list = field(default_factory=list)
    trade: list = field(default_factory=list)
    bills: list = field(default_factory=list)
    payroll: list = field(default_factory=list)
    payouts: list = field(default_factory=list)
    problems_noise: list = field(default_factory=list)
    entry_info: dict = field(default_factory=dict)


def is_bd(d: dt.date, bh: dict) -> bool:
    return d.weekday() < 5 and d not in bh.get(d.year, set())


def build(seed: int, A: Anchors) -> Sim:
    rng = rng_for(seed, CID, "sim")
    w = World(CID, "GBP", ACCTS)
    sim = Sim(w)
    bh = {y: cal.uk_bank_holidays(y) for y in range(2019, 2028)}
    all_days = list(days())
    closed = lambda d: any(a <= d <= b for a, b in CLOSED) or d == dt.date(d.year, 12, 25) or d == dt.date(d.year, 12, 26) or d == dt.date(d.year, 1, 1)  # noqa: E731
    raw = {}
    for d in all_days:
        tx, tn, pr, _ = A.wx_uk[d]
        f = MONTH_W[d.month] * [0.75, 0.8, 0.85, 0.9, 1.0, 1.45, 1.35][d.weekday()]
        if 3 <= d.month <= 9 and tx > 18 and pr < 1:
            f *= 1.30
        if pr > 5:
            f *= 0.78
        if d.month in (11, 12, 1, 2) and tx < 3:
            f *= 0.85
        e = cal.easter(d.year)
        if e - dt.timedelta(days=2) <= d <= e + dt.timedelta(days=1):
            f *= 1.5
        if d in (cal.uk_mothers_day(d.year), cal.uk_mothers_day(d.year) - dt.timedelta(days=1)):
            f *= 1.35
        if d in bh[d.year]:
            f *= 1.25
        if d.year == 2020 and d >= dt.date(2020, 5, 13) and d.month <= 8:
            f *= 1.35
        if any(a <= d <= b for a, b in CLOSED):
            f *= 0.12
        if closed(d) and not any(a <= d <= b for a, b in CLOSED):
            f = 0.0
        raw[d] = f * float(np.exp(rng.normal(0, 0.10)))
    retail = scale_to_targets(raw, {y: int(v * 0.85 * 100) for y, v in NET_TARGET.items()})
    cpi = {m: A.cpi.get(m, 143.6) for m in {ym(d) for d in all_days}}
    ratio = {}
    for m in sorted(cpi):
        y, mo = int(m[:4]), int(m[5:])
        pm = ym(add_months(dt.date(y, mo, 1), -6))
        ratio[m] = float(np.clip(0.595 * (1 + 0.6 * (cpi[m] / cpi.get(pm, cpi[m]) - 1)), 0.57, 0.66))
    cogs_day = {d: rnd(retail[d] * ratio[ym(d)] + 0) for d in all_days}
    # opening
    op = dict(till=1_500_00, bank=120_000_00, inv=180_000_00, fa=95_000_00, ap=-34_000_00, loan=0)
    w.post(D0, [("till", op["till"]), ("bank", op["bank"]), ("inv", op["inv"]), ("fa", op["fa"]), ("ap", op["ap"]), ("cap", -(op["till"] + op["bank"] + op["inv"] + op["fa"] + op["ap"]))], ref="OPENING", memo="Opening balances 1 Jan 2020", kind="opening")
    cs = lambda d: COST_SCALE[d.year]  # noqa: E731
    inv_est = op["inv"]
    pending_bills = []
    seq = defaultdict(int)
    gift_bal = 0
    clr_queue: dict[dt.date, int] = defaultdict(int)  # payout date -> gross card sales
    clr_src: dict[dt.date, list] = defaultdict(list)
    last_bill_day = D0
    purch_acc = 0
    unrec = defaultdict(list); fee_pay = defaultdict(list); shrink_ids = {}; cogs_ids = {}; bills_e = []; gift_days = defaultdict(list)
    credit_notes = []; bankfee_ids = defaultdict(list); loan_int_ids = []; trade_pay = []; payout_ids = []
    month_cogs = defaultdict(int)
    loan_bal = 0; loan_pmt = 0
    vat_due_dates = {}
    tshares = lambda d: (0.76 + 0.012 * (d.year - 2020) + 0.0, 0.0)  # noqa: E731

    def fund(d, need):
        if w.bal("bank") - need < 15_000_00:
            amt = ((need + 20_000_00 - w.bal("bank")) // 10_000_00 + 1) * 10_000_00
            e = w.post(d, [("bank", amt), ("cap", -amt)], ref="CAPITAL", memo="Owner puts money in", kind="owner_injection")
            sim.problems_noise.append(("owner_injection", d, [e.id]))

    cat_keys = list(CAT_W)
    for d in all_days:
        net = retail[d]
        # ---------------- POS day
        if net > 0:
            if d >= ORDER_START:
                ords, net, vat, disc = gen_orders(sim, d, net, rng, A)
                gift_sold = gift_red = 0
                n_ord = len([o for o in ords if o["status"] == "sale"])
                card = sum(p["amount"] for o in ords for p in o["payments"] if p["method"] == "card")
                cash = sum(p["amount"] for o in ords for p in o["payments"] if p["method"] == "cash")
            else:
                ords, disc, n_ord = None, rnd(net * 0.03), max(1, rnd(net / 3600))
                vat = rnd(net * 0.94 * 0.2)
                gift_sold = rnd(net * 0.011 / P_) * P_ if rng.random() < 0.5 else 0
                gift_red = min(rnd(net * 0.012 / P_) * P_, gift_bal) if rng.random() < 0.6 else 0
                total = net + vat + gift_sold
                card_share = min(max(0.76 + 0.012 * (d.year - 2020) + float(rng.normal(0, 0.03)), 0.6), 0.92)
                card = rnd((total - gift_red) * card_share)
                cash = total - gift_red - card
            spec = [("till", cash), ("clr", card), ("gift", gift_red - gift_sold), ("sales", -net), ("vat", -vat)]
            e = w.post(d, spec, ref=f"POS{d:%Y%m%d}", memo="Square daily sales summary", kind="pos_day")
            sim.day[d] = dict(net=net, vat=vat, card=card, cash=cash, gift_red=gift_red, gift_sold=gift_sold, disc=disc, n=n_ord, entry=e.id, orders=ords)
            gift_bal += gift_sold - gift_red
            if gift_sold:
                gift_days[ym(d)].append(e.id)
            if card:
                pd_ = d + dt.timedelta(days=1)
                while not is_bd(pd_, bh):
                    pd_ += dt.timedelta(days=1)
                clr_queue[pd_] += card
                clr_src[pd_].append(e.id)
        # ---------------- Square payout
        if d in clr_queue:
            g = clr_queue.pop(d)
            fee = rnd(g * 0.0175)
            ids = clr_src.pop(d)
            e = w.post(d, [("bank", g - fee), ("fees", fee), ("clr", -g)], ref=f"SQDEP{d:%Y%m%d}", memo="Square deposit (net of fees)", kind="square_payout")
            sim.payouts.append(dict(date=d, gross=g, fee=fee, entry=e.id, sales=ids))
            payout_ids.append(e.id)
            fee_pay[ym(d)].append(e.id)
        # ---------------- cash to bank Tue/Fri
        if d.weekday() in (1, 4) and w.bal("till") > 2_500_00:
            amt = (w.bal("till") - 1_500_00) // 10_000 * 10_000
            if amt > 0:
                w.post(d, [("bank", amt), ("till", -amt)], ref=f"PAYIN{d:%Y%m%d}", memo="Cash paid into bank", kind="cash_deposit")
        # ---------------- trade invoices (15% of net)
        if d.weekday() < 5 and not closed(d) and rng.random() < 0.55 * (1.15 if 3 <= d.month <= 9 else 0.7):
            amt = rnd(float(rng.lognormal(6.4, 0.5)) * cs(d) * 100 * 0.85 / 100) * 100
            vat = rnd(amt * 0.2)
            cust = int(rng.integers(len(TRADE)))
            seq["inv"] += 1
            e = w.post(d, [("ar", amt + vat), ("sales", -amt), ("vat", -vat)], ref=f"INV{seq['inv']:05d}", memo=f"Invoice to {TRADE[cust]}", kind="trade_invoice")
            late = int(rng.choice([22, 30, 38, 45, 58, 75, 100]))
            row = dict(id=f"inv-{CID}-{seq['inv']:05d}", no=f"INV{seq['inv']:05d}", cust=cust, date=d, due=d + dt.timedelta(days=30), net=amt, vat=vat, total=amt + vat, entry=e.id,
                       pay_date=d + dt.timedelta(days=late), never=bool(rng.random() < 0.012), paid=False)
            sim.trade.append(row)
        for r in sim.trade:
            if not r["paid"] and not r["never"] and r["pay_date"] <= d and "pay_entry" not in r:
                e = w.post(d, [("bank", r["total"]), ("ar", -r["total"])], ref=f"BACS-{r['no']}", memo=f"BACS from {TRADE[r['cust']]}", kind="trade_payment")
                r["paid"], r["pay_entry"], r["pay_actual"] = True, e.id, d
                trade_pay.append(e.id)
            if r["never"] and "wo_entry" not in r and d >= r["due"] + dt.timedelta(days=170):
                e = w.post(d, [("bad", r["net"]), ("vat", r["vat"]), ("ar", -r["total"])], ref=f"WO-{r['no']}", memo=f"Written off {r['no']} ({TRADE[r['cust']]})", kind="writeoff")
                r["wo_entry"], r["written_off"] = e.id, d
        # ---------------- purchases: bill Mon/Thu from accumulated need
        horizon = sum(cogs_day.get(d + dt.timedelta(days=i), cogs_day[d]) for i in range(1, 31)) if d + dt.timedelta(days=31) <= D1 else 30 * cogs_day[d]
        surge = 1.35 if d.month in (1, 2, 3) else (1.3 if d.month in (9, 10) else 1.0)
        need = max(0, cogs_day[d] + 0.15 * (horizon * surge - inv_est))
        purch_acc += need
        if d.weekday() in (0, 3) and not closed(d) and purch_acc > 500_00:
            amt = rnd(purch_acc / 100) * 100
            parts = weighted_split(amt, list(rng.uniform(0.4, 1.6, size=int(rng.integers(2, 4)))))
            for part in parts:
                sup = int(rng.integers(len(SUPPLIERS)))
                vatp = rnd(part * 0.17)
                seq["bill"] += 1
                e = w.post(d, [("inv", part), ("vat", vatp), ("ap", -(part + vatp))], ref=f"BILL{seq['bill']:05d}", memo=f"Bill from {SUPPLIERS[sup]}", kind="bill")
                b = dict(id=f"bill-{CID}-{seq['bill']:05d}", no=f"BILL{seq['bill']:05d}", sup=sup, date=d, due=d + dt.timedelta(days=30), net=part, vat=vatp, total=part + vatp, entry=e.id)
                sim.bills.append(b); pending_bills.append(b); bills_e.append(e.id)
                if rng.random() < 0.015:
                    seq["cn"] += 1
                    cn_amt = rnd(part * 0.06 / 100) * 100
                    ce = w.post(d + dt.timedelta(days=9), [("ap", cn_amt), ("cogs", -cn_amt)], ref=f"CN{seq['cn']:04d}", memo=f"Credit note from {SUPPLIERS[sup]}", kind="credit_note") if d + dt.timedelta(days=9) <= D1 else None
                    if ce:
                        credit_notes.append(ce.id)
            inv_est += amt
            purch_acc = 0
        inv_est -= cogs_day[d]
        for b in [x for x in pending_bills if x["due"] <= d]:
            fund(d, b["total"])
            e = w.post(d, [("ap", b["total"]), ("bank", -b["total"])], ref=f"PAY-{b['no']}", memo=f"Paid {SUPPLIERS[b['sup']]} {b['no']}", kind="bill_payment")
            b["pay_entry"], b["pay_date"] = e.id, d
            pending_bills.remove(b)
        # ---------------- recurring
        s_ = cs(d)
        if d.day == 28:
            tot = 0
            rows = []
            for i, base in enumerate(EMP_BASE):
                g = rnd(base * s_ * (1.35 if d.month in (4, 5, 6, 11, 12) and i >= 8 else 1.0)) * 100
                rows.append((i, g)); tot += g
            fund(d, tot)
            e = w.post(d, [("sal", tot), ("bank", -tot)], ref=f"WAGES{d:%Y%m}", memo=f"Wages {d:%b %Y} net pay", kind="payroll")
            sim.payroll.append(dict(month=d.replace(day=1), rows=rows, entry=e.id, pay_date=d, tax=rnd(tot * 0.28)))
        if d.day == 22 and sim.payroll:
            last = sim.payroll[-1]
            if last["pay_date"] < d:
                t = last["tax"]
                fund(d, t)
                last["tax_entry"] = w.post(d, [("sal", t), ("bank", -t)], ref=f"HMRC{d:%Y%m}", memo="PAYE and NI to HMRC", kind="payroll_tax").id
        if d.day == 1:
            r_ = rnd(5_800 * s_ / 10) * 10 * 1
            fund(d, r_ * 100)
            w.post(d, [("rent", r_ * 100), ("bank", -r_ * 100)], ref=f"RENT{d:%Y%m}", memo="Rent and business rates", kind="rent")
        if d.day == 10:
            sp = 1.0 + (0.55 if 2022 <= d.year <= 2023 and d.month in (9, 10, 11, 12, 1, 2, 3) else 0.0) + (0.25 if d.month in (12, 1, 2) else 0.0)
            u = rnd(2_300 * s_ * sp) * 100
            fund(d, u); w.post(d, [("util", u), ("bank", -u)], ref=f"UTIL{d:%Y%m}", memo="Electricity, gas and water", kind="utilities")
            pf = rnd(1_100 * s_) * 100
            fund(d, pf); w.post(d, [("prof", pf), ("bank", -pf)], ref=f"PROF{d:%Y%m}", memo="Insurance, accountant and software", kind="professional")
            mk = rnd((1_500 if d.month in (3, 4, 5, 11) else 900) * s_) * 100
            fund(d, mk); w.post(d, [("mkt", mk), ("bank", -mk)], ref=f"MKT{d:%Y%m}", memo="Marketing and signage", kind="marketing")
        if d.day == 18 and rng.random() < 0.6:
            rp = int(rng.integers(150, 1900)) * 100
            fund(d, rp); w.post(d, [("repair", rp), ("bank", -rp)], ref=f"REP{d:%Y%m%d}", memo="Repairs and maintenance", kind="repair")
        if d.day == 27:
            bf = rnd(160 * s_) * 100 // 100 * 100
            e = w.post(d, [("bankfee", bf), ("bank", -bf)], ref=f"BC{d:%Y%m}", memo="Bank charges", kind="bank_fee")
            bankfee_ids[ym(d)].append(e.id)
        if d.day == 20:
            drw = 11_000_00
            fund(d, drw); w.post(d, [("draw", drw), ("bank", -drw)], ref=f"DRAW{d:%Y%m}", memo="Owner's drawings", kind="drawings")
        if d == dt.date(2021, 6, 15):
            w.post(d, [("bank", 60_000_00), ("loan", -60_000_00)], ref="LOAN1", memo="Growth-guarantee term loan drawn", kind="loan_drawdown")
            loan_bal = 60_000_00
            loan_pmt = rnd(60_000_00 * (0.075 / 12) / (1 - (1 + 0.075 / 12) ** -60))
            sim.entry_info["loan_start"] = d
        if loan_bal > 0 and d.day == 15 and d > dt.date(2021, 6, 15):
            it = rnd(loan_bal * 0.075 / 12); pr = min(loan_pmt - it, loan_bal)
            fund(d, pr + it)
            g = f"LOAN{d:%Y%m}"
            w.post(d, [("loan", pr), ("bank", -pr)], ref=g, memo="Loan instalment - capital", kind="loan_payment")
            e = w.post(d, [("intr", it), ("bank", -it)], ref=g + "I", memo="Loan instalment - interest", kind="loan_interest")
            loan_int_ids.append(e.id)
            loan_bal -= pr
        # VAT: pay quarter balance on day 7 of 2nd month after quarter end
        if d.day == 7 and d.month in (2, 5, 8, 11):
            qend = add_months(d, -1).replace(day=1) - dt.timedelta(days=1)
            amt = w.bal("vat") * -1
            amt = max(amt - sim.entry_info.get("vat_paid_through", 0), 0) if False else -w.bal("vat")
            if amt > 0:
                v = amt // 100 * 100 - 0
                v = min(v, amt)
                fund(d, v)
                w.post(d, [("vat", v), ("bank", -v)], ref=f"VAT{qend:%Y%m}", memo=f"VAT return payment quarter to {qend:%d %b %Y}", kind="vat_payment")
        # petty cash expenses
        if not closed(d) and rng.random() < 0.5:
            amt = rnd(float(rng.uniform(15, 110)) * s_) * 100
            if w.bal("till") > amt + 1_000_00:
                e = w.post(d, [("misc", amt), ("till", -amt)], ref="", memo=["Compost delivery driver", "Cleaning supplies", "Till float shortage", "Window cleaner", "Milk and tea", "Car park sweeper"][int(rng.integers(6))], kind="petty_expense")
                unrec[ym(d)].append(e.id)
        month_cogs[ym(d)] += cogs_day[d]
        if d == month_end(d):
            m = ym(d); c_ = month_cogs[m]
            cogs_ids[m] = w.post(d, [("cogs", c_), ("inv", -c_)], ref=f"COGS{m}", memo=f"Cost of sales {m} (stock take)", kind="cogs").id
            cold = d.month in (11, 12, 1, 2, 3) and float(rng.random()) < 0.2
            sh = rnd(c_ * (0.02 if d.month in (4, 5, 6, 7, 8, 9) else 0.015) * (2.2 if cold else 1.0) / 100) * 100
            inv_est -= sh
            shrink_ids[m] = w.post(d, [("shrink", sh), ("inv", -sh)], ref=f"WASTE{m}", memo=f"Plant wastage and dead stock {m}", kind="shrinkage").id
    sim.meta.update(dict(unrec=unrec, fee_pay=fee_pay, shrink_ids=shrink_ids, cogs_ids=cogs_ids, gift_days=gift_days, credit_notes=credit_notes, bankfee_ids=bankfee_ids,
                         loan_int_ids=loan_int_ids, trade_pay=trade_pay, payout_ids=payout_ids, bh=bh, retail=retail))
    return sim


def gen_orders(sim, d, target_net, rng, A):
    """Baskets for one day, scaled to hit target_net exactly. Returns (orders, net, vat, discount)."""
    m = d.month - 1
    cats = list(CAT_W)
    cw = np.array([CAT_W[c][m] for c in cats], dtype=float)
    skus = [s for s in CATALOG]
    sw = np.array([CAT_W[s[2]][m] / sum(1 for x in CATALOG if x[2] == s[2]) for s in skus]) + 1e-9
    n = max(1, rnd(target_net / 3600))
    baskets = []
    for _ in range(n):
        k = 1 + int(rng.poisson(0.9))
        idx = rng.choice(len(skus), size=k, p=sw / sw.sum())
        lines = [[skus[int(i)], int(rng.integers(1, 4)) if skus[int(i)][3] < 2000 else 1] for i in idx]
        baskets.append(lines)
    raw = [sum(s[3] * q for s, q in b) for b in baskets]
    f = target_net / max(sum(raw), 1)
    orders = []
    net_sum = vat_sum = disc_sum = 0
    for i, b in enumerate(baskets):
        o_lines = []
        for j, (s, q) in enumerate(b, 1):
            up = max(1, rnd(s[3] * f))
            o_lines.append(dict(no=j, sku=s[0], name=s[1], cat=s[2], qty=q, unit=up, vat_rate=s[4]))
        orders.append(dict(d=d, lines=o_lines, status="sale"))
    # fix pence: put residual on first line of first order
    cur = sum(l["qty"] * l["unit"] for o in orders for l in o["lines"])
    res = target_net - cur
    if res:
        l0 = orders[0]["lines"][0]
        if l0["qty"] == 1 and l0["unit"] + res > 0:
            l0["unit"] += res
        else:
            orders[0]["lines"].append(dict(no=len(orders[0]["lines"]) + 1, sku="OT-ADJ", name="Sundries", cat="other", qty=1, unit=res if res > 0 else 0, vat_rate=20))
            if res < 0:
                orders[0]["lines"][-1]["unit"] = 0
                l0["unit"] += res if l0["unit"] + res > 0 and l0["qty"] == 1 else 0
    for o in orders:
        for l in o["lines"]:
            l["total"] = l["qty"] * l["unit"]
            l["tax"] = rnd(l["total"] * l["vat_rate"] / 100)
        o["gross"] = sum(l["total"] for l in o["lines"])
        o["tax"] = sum(l["tax"] for l in o["lines"])
        o["discount"] = 0
        o["total"] = o["gross"] + o["tax"]
        net_sum += o["gross"]; vat_sum += o["tax"]
    # tenders per order
    card_p = 0.78 + 0.012 * (d.year - 2020)
    for i, o in enumerate(orders):
        r = rng.random()
        m_ = "card" if r < card_p else "cash"
        o["payments"] = [dict(method=m_, amount=o["total"], provider={"card": "visa", "cash": None, "voucher": "square_gift_card"}[m_])]
        o["status"] = "sale"
    # tiny share of refunds (one line, same day)
    extra = []
    for o in orders:
        if rng.random() < 0.01:
            l = dict(o["lines"][0])
            tot = l["total"] + l["tax"]
            extra.append(dict(d=d, lines=[dict(l, no=1)], status="refund", gross=l["total"], tax=l["tax"], discount=0, total=tot, ref=o,
                              payments=[dict(method=o["payments"][0]["method"], amount=-tot, provider=o["payments"][0]["provider"])]))
            net_sum -= l["total"]; vat_sum -= l["tax"]
    orders += extra
    # gift card voucher payments cap by outstanding handled by caller (vouchers are modest)
    return orders, net_sum, vat_sum, 0
