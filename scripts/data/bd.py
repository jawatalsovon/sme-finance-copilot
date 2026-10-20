"""bd_grocer: Mirpur Family Store (Dhaka). Neighbourhood grocery, BDT.  SYNTHETIC - no real firm.

build(seed, A) -> Sim with truth (World), the owner's books (Book), planted problems and raw detail
for the exporters (wallet transactions, households, employees, ...).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

import calendars as cal
from core import (D0, D1, Acct, Anchors, Book, Entry, Problems, World, add_months, days, month_end, rng_for, rnd,
                  scale_to_targets, weighted_split, ym)

CID = "bd_grocer"
T = 100  # 1 taka = 100 poisha
COMPANY = dict(name="Mirpur Family Store", country="BD", base_currency="BDT", industry="grocery_retail", fiscal_year_start_month=7, founded_on="2012-03-01",
               description="Neighbourhood grocery in Dhaka; cash, bKash-style wallet, card and household credit (baki). Google-Sheets notebook.")
YEAR_SALES = {2020: 25.0e6, 2021: 27.6e6, 2022: 31.2e6, 2023: 33.8e6, 2024: 34.6e6, 2025: 36.0e6, 2026: 29.6e6}
COST_SCALE = {2020: 0.66, 2021: 0.72, 2022: 0.80, 2023: 0.88, 2024: 0.94, 2025: 1.0, 2026: 1.06}

ACCTS = [
    Acct("cash", "1000", "Cash on hand (till and safe)", "asset", "cash", "BDT"),
    Acct("bank", "1100", "Mirpur Commercial Bank current a/c", "asset", "bank", "BDT"),
    Acct("wallet", "1150", "bKash-style merchant wallet", "asset", "wallet", "BDT"),
    Acct("ar", "1200", "Customer credit (baki)", "asset", "ar", "BDT"),
    Acct("inv", "1300", "Stock in hand", "asset", "inventory", "BDT"),
    Acct("adv", "1350", "Advances to wholesalers (dadon)", "asset", "prepaid", "BDT"),
    Acct("fa", "1500", "Equipment and fridges", "asset", "fixed_asset", "BDT"),
    Acct("ap", "2000", "Wholesaler payables", "liability", "ap", "BDT"),
    Acct("loan", "2300", "Bank term loans", "liability", "loan", "BDT"),
    Acct("cap", "3000", "Owner's capital", "equity", "owner_equity", "BDT"),
    Acct("draw", "3100", "Owner's drawings", "equity", "drawings", "BDT"),
    Acct("sales", "4000", "Sales", "income", "revenue", "BDT"),
    Acct("othinc", "4900", "Other income", "income", "other_income", "BDT"),
    Acct("cogs", "5000", "Cost of goods sold", "expense", "cogs", "BDT"),
    Acct("shrink", "5100", "Stock losses (spoilage and shrinkage)", "expense", "cogs", "BDT"),
    Acct("sal", "6000", "Staff salaries and bonus", "expense", "payroll", "BDT"),
    Acct("rent", "6100", "Shop rent", "expense", "rent", "BDT"),
    Acct("util", "6200", "Electricity, internet and water", "expense", "utilities", "BDT"),
    Acct("deliv", "6300", "Delivery and transport", "expense", "opex", "BDT"),
    Acct("fees", "6400", "Wallet and bank charges", "expense", "opex", "BDT"),
    Acct("repair", "6500", "Repairs and maintenance", "expense", "opex", "BDT"),
    Acct("misc", "6600", "Porter, tea and small cash expenses", "expense", "opex", "BDT"),
    Acct("lic", "6700", "Licences and trade fees", "expense", "opex", "BDT"),
    Acct("intr", "6800", "Loan interest", "expense", "interest", "BDT"),
    Acct("bad", "6900", "Bad debts written off", "expense", "other_expense", "BDT"),
]

FIRST = ["Abdul", "Rahim", "Karim", "Salma", "Nasrin", "Jahid", "Mizan", "Rokeya", "Faruk", "Shirin", "Kamal", "Hasina", "Babul",
         "Sumon", "Rina", "Alam", "Parveen", "Selim", "Moni", "Taslima", "Habib", "Nargis", "Mamun", "Ruma", "Shahin"]
LAST = ["Hossain", "Ahmed", "Begum", "Khan", "Uddin", "Akter", "Mia", "Islam", "Sarker", "Chowdhury", "Rahman", "Talukder"]
WHOLESALERS = ["Chawkbazar Rice House", "Karwan Bazar Oil & Sugar", "Mirpur Dairy Depot", "Shyambazar Lentil Traders",
               "Dhaka Spice Mart", "Egg & Poultry Supply (Mirpur-10)", "City Household Goods", "Bangla Snacks Distributor"]
EMPLOYEES = [("Manager", 25000), ("Counter staff 1", 15500), ("Counter staff 2", 15000), ("Stock staff 1", 14500),
             ("Stock staff 2", 14000), ("Cashier", 16000), ("Delivery boy 1", 13000), ("Delivery boy 2", 12500)]  # last two = delivery
DELIVERY_STAFF = {6, 7}


@dataclass
class Household:
    idx: int
    cid: str
    name: str
    weight: float
    pay_day: int
    default_from: dt.date | None = None  # stops paying from here
    slow: tuple[dt.date, dt.date] | None = None  # skips payments in this window
    wallet_payer: bool = False
    stop_credit: dt.date | None = None


@dataclass
class Sim:
    world: World
    book: Book
    problems: Problems
    accts: list[Acct]
    households: list[Household]
    wallet_txns: dict[dt.date, list[dict]] = field(default_factory=dict)
    credit_sales: dict[tuple[dt.date, int], int] = field(default_factory=dict)  # (date, hh idx) -> cents
    hh_payments: list[dict] = field(default_factory=list)
    bills: list[dict] = field(default_factory=list)
    bill_payments: list[dict] = field(default_factory=list)
    payroll: list[dict] = field(default_factory=list)
    suppliers: list[str] = field(default_factory=lambda: list(WHOLESALERS))
    writeoffs: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    entry_info: dict[str, dict] = field(default_factory=dict)  # truth entry id -> extra info for exporters
    credit_entry: dict[dt.date, str] = field(default_factory=dict)


def _raw_sales(A: Anchors, rng: np.random.Generator) -> dict[dt.date, float]:
    raw: dict[dt.date, float] = {}
    wdf = [1.0, 0.98, 0.97, 1.02, 1.10, 1.12, 1.03]  # Mon..Sun (Fri/Sat busiest around the weekend)
    for d in days():
        f = wdf[d.weekday()]
        ef, ea = cal.EID_FITR[d.year], cal.EID_ADHA[d.year]
        dtf, dta = (ef - d).days, (ea - d).days
        if 0 < dtf <= 30:
            f *= 1.08
        if 1 <= dtf <= 9:
            f *= 1.50 + 0.04 * (9 - dtf)
        if dtf == 0:
            f *= 0.12
        if -8 <= dtf < 0:
            f *= 0.55
        if 1 <= dta <= 5:
            f *= 1.30
        if dta == 0:
            f *= 0.12
        if -4 <= dta < 0:
            f *= 0.60
        if d.month == 4 and 11 <= d.day <= 14:
            f *= 1.18
        if 6 <= d.month <= 9:
            f *= 0.96
            if A.wx_bd[d][2] > 40:
                f *= 0.90
        if dt.date(2020, 3, 20) <= d <= dt.date(2020, 3, 24):
            f *= 1.35
        elif dt.date(2020, 3, 25) <= d <= dt.date(2020, 5, 30):
            f *= 0.62
        if dt.date(2024, 7, 18) <= d <= dt.date(2024, 8, 6):
            f *= 0.55
        f *= float(np.exp(rng.normal(0, 0.09)))
        raw[d] = f
    return raw


def _cogs_ratio(A: Anchors) -> dict[str, float]:
    """Monthly COGS / sales. Costs follow commodity prices; shop prices follow 2 months later (margin squeeze in 2022)."""
    months = sorted({ym(d) for d in days()})
    cost = {}
    for m in months:
        d = dt.date(int(m[:4]), int(m[5:]), 15)
        cost[m] = 0.45 * A.commodity_index("soybean_oil_usd_mt", d) + 0.25 * A.commodity_index("sugar_world_usd_kg", d) \
            + 0.30 * A.wfp_index(d, ("Rice (coarse)", "Lentils (masur)", "Wheat flour"))
    out = {}
    for i, m in enumerate(months):
        prev = cost[months[max(0, i - 2)]]
        out[m] = float(np.clip(0.874 + 0.20 * (cost[m] / prev - 1.0), 0.84, 0.925))
    return out


def build(seed: int, A: Anchors) -> Sim:
    rng = rng_for(seed, CID, "sim")
    w = World(CID, "BDT", ACCTS)
    sim_hh: list[Household] = []
    # ------------------------------------------------------------------ households (baki customers)
    n_hh = 120
    wts = rng.pareto(2.2, n_hh) + 1.0
    for i in range(n_hh):
        nm = f"{FIRST[int(rng.integers(len(FIRST)))]} {LAST[int(rng.integers(len(LAST)))]} household"
        sim_hh.append(Household(i, f"hh-{i + 1:03d}", nm, float(wts[i]), int(rng.integers(3, 27)), wallet_payer=bool(rng.random() < 0.35)))
    defaulters = rng.choice(np.arange(8, n_hh), size=10, replace=False)
    starts = sorted(dt.date(2020, 6, 1) + dt.timedelta(days=int(x)) for x in rng.choice(np.arange(0, 1900), size=10, replace=False))
    for i, s in zip(defaulters, starts):
        sim_hh[int(i)].default_from = s
        sim_hh[int(i)].stop_credit = s + dt.timedelta(days=int(rng.integers(30, 60)))
    for i in rng.choice(np.setdiff1d(np.arange(n_hh), defaulters), size=14, replace=False):
        s0 = dt.date(2020, 9, 1) + dt.timedelta(days=int(rng.integers(0, 2200)))
        sim_hh[int(i)].slow = (s0, s0 + dt.timedelta(days=int(rng.integers(100, 140))))
    sim = Sim(w, None, None, ACCTS, sim_hh)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ sales and ratios
    raw = _raw_sales(A, rng)
    targets = {y: int(v / 1) for y, v in YEAR_SALES.items()}  # whole taka
    sales_t = scale_to_targets(raw, targets)  # whole taka per day
    ratio = _cogs_ratio(A)
    cogs_day = {d: rnd(sales_t[d] * T * ratio[ym(d)]) for d in sales_t}

    # ------------------------------------------------------------------ opening balances (2019-12-31 close, posted 2020-01-01)
    hh_open: dict[int, int] = {}
    ar_open_total = 380_000 * T
    ow = weighted_split(ar_open_total, [h.weight for h in sim_hh])
    for h, v in zip(sim_hh, ow):
        hh_open[h.idx] = v
    w.post(D0, [("cash", 250_000 * T), ("bank", 900_000 * T), ("wallet", 40_000 * T), ("ar", ar_open_total), ("inv", 2_100_000 * T),
                ("fa", 1_200_000 * T), ("ap", -450_000 * T), ("loan", -1_500_000 * T),
                ("cap", -(250_000 + 900_000 + 40_000 + 380_000 + 2_100_000 + 1_200_000 - 450_000 - 1_500_000) * T)],
           ref="OPENING", memo="Opening balances 1 Jan 2020", kind="opening")

    hh_bal = {h.idx: hh_open[h.idx] for h in sim_hh}
    hh_last_pay = {h.idx: dt.date(2019, 12, 20) for h in sim_hh}
    inv_est = 2_100_000 * T
    adv_bal = 0
    pending_bills: list[dict] = []  # AP bills awaiting payment
    ap_seq = 0
    loans = [dict(start=dt.date(2020, 1, 10), principal=1_500_000 * T, months=36, rate=0.115),
             dict(start=dt.date(2023, 3, 10), principal=2_000_000 * T, months=48, rate=0.125)]
    for L in loans:
        r = L["rate"] / 12
        L["pmt"] = rnd(L["principal"] * r / (1 - (1 + r) ** -L["months"]))
        L["bal"] = L["principal"] if L["start"].year == 2020 else 0
    emp_rows = []
    wallet_week_last = dt.date(2019, 12, 29)
    fee_rate_w, fee_rate_send = 0.015, 0.005

    def pay_from(d: dt.date, amount: int, prefs: list[str], reserve: dict[str, int]) -> str:
        """First account in prefs that can pay `amount` keeping its reserve; else owner injects cash into the bank."""
        for a in prefs:
            if w.bal(a) - reserve.get(a, 0) >= amount:
                return a
        need = amount - (w.bal("bank") - reserve.get("bank", 0))
        inj = ((max(need, 0) // (200_000 * T)) + 1) * 200_000 * T
        e = w.post(d, [("bank", inj), ("cap", -inj)], ref="CAPITAL", memo="Owner puts in money to cover shortfall", kind="owner_injection")
        sim.problems_noise.append(("owner_injection", d, [e.id]))  # type: ignore[attr-defined]
        return "bank"

    sim.problems_noise = []  # type: ignore[attr-defined]
    cost_scale = lambda d: COST_SCALE[d.year]  # noqa: E731
    reserves = {"cash": 120_000 * T, "bank": 150_000 * T, "wallet": 8_000 * T}

    unrec_cash: dict[str, list[str]] = defaultdict(list)  # month -> truth ids (unrecorded misc cash)
    recorded_misc: list[str] = []
    personal: dict[str, list[str]] = defaultdict(list)
    fee_entries: dict[str, list[str]] = defaultdict(list)
    shrink_ids: dict[str, str] = {}
    cogs_ids: dict[str, str] = {}
    wallet_sale_ids: dict[str, list[str]] = defaultdict(list)
    deposits: list[tuple[str, dt.date]] = []
    purchases: list[tuple[str, str, int]] = []  # (truth id, pay acct, amount)
    advances: list[tuple[str, int]] = []
    adv_consume: list[tuple[str, int]] = []
    wallet_cust_pay: list[str] = []  # truth ids of baki payments received into wallet
    misclass_cands: dict[str, list[str]] = defaultdict(list)
    month_cogs: dict[str, int] = defaultdict(int)
    delivery_wage_ids: dict[str, str] = {}
    last_adv = dt.date(2019, 12, 1)

    all_days = list(days())
    for di, d in enumerate(all_days):
        S = sales_t[d] * T
        t_years = (d - D0).days / 365.25
        closed = d == cal.EID_FITR[d.year] or d == cal.EID_ADHA[d.year]
        # ---------------- tender split
        sh_w = (0.10 + 0.019 * t_years) * float(np.exp(rng.normal(0, 0.06)))
        sh_card = 0.0 if d < dt.date(2022, 6, 1) else (0.025 + 0.004 * (d.year - 2022)) * float(np.exp(rng.normal(0, 0.1)))
        sh_cr = (0.14 - 0.006 * t_years) * float(np.exp(rng.normal(0, 0.08)))
        W = rnd(S * sh_w / T) * T
        Cd = rnd(S * sh_card / T) * T
        Cr = rnd(S * sh_cr / T) * T if not closed else 0
        Ca = S - W - Cd - Cr
        # ---------------- credit sales -> households
        if Cr > 0:
            active = [h for h in sim_hh if h.stop_credit is None or d <= h.stop_credit]
            k = int(min(len(active), max(2, rng.integers(4, 14))))
            idxs = rng.choice(len(active), size=k, replace=False, p=np.array([h.weight for h in active]) / sum(h.weight for h in active))
            parts = weighted_split(Cr, list(rng.uniform(0.4, 1.6, size=k)))
            for ix, part in zip(idxs, parts):
                h = active[int(ix)]
                sim.credit_sales[(d, h.idx)] = part
                hh_bal[h.idx] += part
        # ---------------- sales entries
        if Ca:
            w.post(d, [("cash", Ca), ("sales", -Ca)], ref=f"S{d:%Y%m%d}-C", memo="Cash sales", kind="sale_cash")
        if W:
            n = max(1, rnd(W / (900 * T)))
            amts = weighted_split(W, list(rng.lognormal(0, 0.7, size=n)))
            tx = []
            tot_fee = 0
            for j, a in enumerate(amts):
                fee = rnd(a * fee_rate_w)
                tot_fee += fee
                tx.append(dict(amount=a, fee=fee, type="Payment", cp=f"01{int(rng.integers(3, 9))}{int(rng.integers(0, 10 ** 8)):08d}"))
            sim.wallet_txns[d] = tx
            e = w.post(d, [("wallet", W), ("sales", -W)], ref=f"S{d:%Y%m%d}-W", memo="bKash sales", kind="sale_wallet")
            sim.entry_info[e.id] = {"txn_index": 0}
            wallet_sale_ids[ym(d)].append(e.id)
            ef = w.post(d, [("fees", tot_fee), ("wallet", -tot_fee)], ref=f"F{d:%Y%m%d}", memo="bKash merchant charges", kind="wallet_fee")
            fee_entries[ym(d)].append(ef.id)
        if Cd:
            sd = d + dt.timedelta(days=2)
            if sd <= D1:
                fee = rnd(Cd * 0.014)
                g = f"POS{sd:%Y%m%d}"
                w.post(sd, [("bank", Cd), ("sales", -Cd)], ref=f"S{d:%Y%m%d}-K", memo="Card sales settled to bank", kind="sale_card", tags={"grp": g})
                w.post(sd, [("fees", fee), ("bank", -fee)], ref=f"POSFEE{sd:%Y%m%d}", memo="Card processing charge", kind="card_fee", tags={"grp": g})
        if Cr:
            sim.credit_entry[d] = w.post(d, [("ar", Cr), ("sales", -Cr)], ref=f"S{d:%Y%m%d}-B", memo="Credit sales (baki)", kind="sale_credit").id

        # ---------------- baki collections
        for h in sim_hh:
            if d.day != h.pay_day or hh_bal[h.idx] <= 0:
                continue
            if h.default_from and d >= h.default_from:
                continue
            if h.slow and h.slow[0] <= d <= h.slow[1]:
                continue
            frac = float(rng.choice([1.0, 1.0, 0.8, 0.6, 0.5]))
            amt = min(hh_bal[h.idx], rnd(hh_bal[h.idx] * frac / T) * T)
            if amt <= 0:
                continue
            via_w = h.wallet_payer or rng.random() < 0.1
            if via_w:
                fee = rnd(amt * fee_rate_w)
                e = w.post(d, [("wallet", amt), ("ar", -amt)], ref=f"BK{d:%Y%m%d}-{h.cid}", memo=f"Baki collected - {h.name}", kind="baki_wallet", tags={"hh": h.idx})
                ef = w.post(d, [("fees", fee), ("wallet", -fee)], ref=f"F{d:%Y%m%d}-{h.idx}", memo="bKash merchant charges on baki payment", kind="wallet_fee")
                fee_entries[ym(d)].append(ef.id)
                sim.wallet_txns.setdefault(d, []).append(dict(amount=amt, fee=fee, type="Payment", cp=f"017{int(rng.integers(0, 10 ** 8)):08d}", baki=e.id))
                wallet_cust_pay.append(e.id)
            else:
                e = w.post(d, [("cash", amt), ("ar", -amt)], ref=f"BK{d:%Y%m%d}-{h.cid}", memo=f"Baki collected - {h.name}", kind="baki_cash", tags={"hh": h.idx})
            hh_bal[h.idx] -= amt
            hh_last_pay[h.idx] = d
            sim.hh_payments.append(dict(hh=h.idx, date=d, amount=amt, entry=e.id, method="wallet" if via_w else "cash"))
        # write-offs 190 days after the household stopped paying
        for h in sim_hh:
            if h.default_from and not any(x["hh"] == h.idx for x in sim.writeoffs) and d >= h.default_from + dt.timedelta(days=190) and hh_bal[h.idx] > 0:
                amt = hh_bal[h.idx]
                e = w.post(d, [("bad", amt), ("ar", -amt)], ref=f"WO-{h.cid}", memo=f"Written off - {h.name} (no payment for 190+ days)", kind="writeoff", tags={"hh": h.idx})
                sim.writeoffs.append(dict(hh=h.idx, date=d, amount=amt, entry=e.id))
                hh_bal[h.idx] = 0
                hh_last_pay[h.idx] = d

        # ---------------- purchases
        if not closed:
            horizon = sum(cogs_day.get(d + dt.timedelta(days=k), cogs_day[d]) for k in range(1, 18)) if d + dt.timedelta(days=18) <= D1 else 17 * cogs_day[d]
            surge = 1.0
            for e_d, lead in ((cal.EID_FITR[d.year], 24), (cal.EID_ADHA[d.year], 10)):
                if 0 < (e_d - d).days <= lead:
                    surge = 1.28
            target = horizon * surge
            P = int(np.clip(cogs_day[d] + 0.35 * (target - inv_est), 0, 3.0 * cogs_day[d]))
            P = (P // (100 * T)) * (100 * T)
            if d.weekday() == 4 and d.month not in (3, 4):  # Friday: wholesalers' day off for many
                P = int(P * 0.35 // (100 * T)) * (100 * T)
            if P > 0:
                k = int(rng.integers(1, 4))
                parts = [x // T * T for x in weighted_split(P, list(rng.uniform(0.5, 1.5, size=k)))]
                parts[0] += P - sum(parts)
                for part in parts:
                    sup = int(rng.integers(len(WHOLESALERS)))
                    r = rng.random()
                    if r < 0.42:
                        mode = "cash"
                    elif r < 0.64:
                        mode = "bank"
                    elif r < 0.72:
                        mode = "wallet"
                    elif r < 0.94:
                        mode = "credit"
                    else:
                        mode = "advance" if adv_bal >= part else "cash"
                    if mode == "credit":
                        ap_seq += 1
                        e = w.post(d, [("inv", part), ("ap", -part)], ref=f"PB{ap_seq:05d}", memo=f"Purchase on credit - {WHOLESALERS[sup]}", kind="purchase_credit")
                        due = d + dt.timedelta(days=int(rng.integers(10, 22)))
                        b = dict(id=f"bill-{CID}-{ap_seq:05d}", sup=sup, date=d, due=due, total=part, entry=e.id, no=f"PB{ap_seq:05d}")
                        pending_bills.append(b)
                        sim.bills.append(b)
                        purchases.append((e.id, "ap", part))
                    elif mode == "advance":
                        e = w.post(d, [("inv", part), ("adv", -part)], ref=f"DADON-USE-{d:%Y%m%d}", memo=f"Goods received against advance - {WHOLESALERS[sup]}", kind="purchase_advance_use")
                        adv_bal -= part
                        adv_consume.append((e.id, part))
                        purchases.append((e.id, "adv", part))
                    else:
                        acct = pay_from(d, part, [mode, "cash" if mode != "cash" else "bank", "bank"], reserves)
                        if acct == "wallet":
                            fee = rnd(part * fee_rate_send)
                            g = f"PW{d:%Y%m%d}-{sup}-{w.n + 1}"
                            e = w.post(d, [("inv", part), ("wallet", -part)], ref=g, memo=f"Purchase paid by bKash - {WHOLESALERS[sup]}", kind="purchase_wallet", tags={"grp": g})
                            ef = w.post(d, [("fees", fee), ("wallet", -fee)], ref=g + "F", memo="bKash send-money charge", kind="wallet_fee", tags={"grp": g})
                            fee_entries[ym(d)].append(ef.id)
                        else:
                            e = w.post(d, [("inv", part), (acct, -part)], ref=f"P{d:%Y%m%d}-{sup}", memo=f"Purchase - {WHOLESALERS[sup]}", kind=f"purchase_{acct}")
                        purchases.append((e.id, acct, part))
                        if acct in ("cash", "bank") and part >= 20_000 * T and rng.random() < 0.012:
                            e2 = w.post(d, [("inv", part), (acct, -part)], ref=f"P{d:%Y%m%d}-{sup}B", memo=f"Purchase - {WHOLESALERS[sup]}", kind=f"purchase_{acct}")
                            purchases.append((e2.id, acct, part))
                            inv_est += part
                            sim.problems_noise.append(("legit_repeat_purchase", d, [e.id, e2.id]))  # type: ignore[attr-defined]
                    sim.entry_info[e.id] = {"sup": sup}
                inv_est += P
            if d - last_adv >= dt.timedelta(days=int(rng.integers(35, 60))) and d.weekday() != 4:
                amt = int(rng.integers(80, 260)) * 1000 * T
                src = pay_from(d, amt, ["bank", "cash"], reserves)
                sup = int(rng.integers(len(WHOLESALERS)))
                e = w.post(d, [("adv", amt), (src, -amt)], ref=f"DADON-{d:%Y%m%d}", memo=f"Advance (dadon) to {WHOLESALERS[sup]}", kind="advance_paid")
                adv_bal += amt
                advances.append((e.id, amt))
                last_adv = d
        inv_est -= cogs_day[d]

        # ---------------- pay supplier bills
        for b in [x for x in pending_bills if x["due"] <= d]:
            src = pay_from(d, b["total"], ["bank", "cash"] if rng.random() < 0.7 else ["cash", "bank"], reserves)
            e = w.post(d, [("ap", b["total"]), (src, -b["total"])], ref=f"PAY-{b['no']}", memo=f"Paid supplier bill {b['no']}", kind="bill_payment")
            sim.bill_payments.append(dict(bill=b["id"], date=d, amount=b["total"], entry=e.id, method=src))
            pending_bills.remove(b)

        # ---------------- recurring costs
        cs = cost_scale(d)
        if d.day == 1 and d > D0:
            pm = add_months(d, -1)
            rows = []
            tot_cash = tot_bank = tot_deliv = 0
            for i, (role, base) in enumerate(EMPLOYEES):
                gross = rnd(base * cs / 500) * 500 * T
                to_bank = (i == 0)
                rows.append(dict(idx=i, role=role, gross=gross, bank=to_bank, month=pm))
                if i in DELIVERY_STAFF:
                    tot_deliv += gross
                elif to_bank:
                    tot_bank += gross
                else:
                    tot_cash += gross
            sc = pay_from(d, tot_cash + tot_deliv, ["cash", "bank"], reserves)
            e1 = w.post(d, [("sal", tot_cash), (sc, -tot_cash)], ref=f"SAL{pm:%Y%m}", memo=f"Salaries {pm:%b %Y} (shop staff)", kind="salary_cash")
            e2 = w.post(d, [("sal", tot_bank), ("bank", -tot_bank)], ref=f"SALB{pm:%Y%m}", memo=f"Manager salary {pm:%b %Y} (bank transfer)", kind="salary_bank")
            e3 = w.post(d, [("sal", tot_deliv), (sc, -tot_deliv)], ref=f"SALD{pm:%Y%m}", memo=f"Delivery boys wages {pm:%b %Y}", kind="salary_delivery")
            delivery_wage_ids[ym(d)] = e3.id
            for r in rows:
                sim.payroll.append(dict(**r, entry=e1.id if not r["bank"] and r["idx"] not in DELIVERY_STAFF else (e2.id if r["bank"] else e3.id), pay_date=d))
        if d.day == 5:
            rent = 62_000 * T * (1.07 ** (d.year - 2026))
            rent = rnd(rent / (500 * T)) * 500 * T
            src = pay_from(d, rent, ["bank", "cash"], reserves)
            w.post(d, [("rent", rent), (src, -rent)], ref=f"RENT{d:%Y%m}", memo=f"Shop rent {d:%b %Y}", kind="rent")
        if d.day == 12:
            summer = 1.25 if d.month in (4, 5, 6, 7, 8, 9) else 0.9
            u = rnd(30_000 * T * cs * summer / (100 * T)) * 100 * T
            u_w = rnd(u * 0.3 / (100 * T)) * 100 * T
            src = pay_from(d, u - u_w, ["bank", "cash"], reserves)
            w.post(d, [("util", u - u_w), (src, -(u - u_w))], ref=f"UTIL{d:%Y%m}", memo=f"Electricity and generator fuel {d:%b %Y}", kind="utilities")
            if w.bal("wallet") >= u_w + reserves["wallet"]:
                w.post(d, [("util", u_w), ("wallet", -u_w)], ref=f"UTILW{d:%Y%m}", memo="Internet and water via bKash bill pay", kind="utilities")
            else:
                w.post(d, [("util", u_w), ("cash", -u_w)], ref=f"UTILC{d:%Y%m}", memo="Internet and water (cash)", kind="utilities")
        if d.weekday() == 0:
            dv = rnd(2_800 * T * cs / (50 * T)) * 50 * T
            src = pay_from(d, dv, ["cash", "bank"], reserves)
            w.post(d, [("deliv", dv), (src, -dv)], ref=f"FUEL{d:%Y%m%d}", memo="Delivery bike fuel and rickshaw hire", kind="delivery")
        if d.day == 20:
            amt = rnd(85_000 * T * cs / (1000 * T)) * 1000 * T
            src = pay_from(d, amt, ["bank"], reserves)
            w.post(d, [("draw", amt), (src, -amt)], ref=f"DRAW{d:%Y%m}", memo="Owner's monthly drawings", kind="drawings")
        if d.day == 25 and rng.random() < 0.18:
            amt = int(rng.integers(3, 25)) * 1000 * T
            src = pay_from(d, amt, ["cash", "bank"], reserves)
            e = w.post(d, [("repair", amt), (src, -amt)], ref=f"REP{d:%Y%m%d}", memo=["Fridge service", "Shutter repair", "Weighing scale", "Shelf carpenter", "Electrician"][int(rng.integers(5))], kind="repair")
            misclass_cands[ym(d)].append(e.id)
        if d.month == 7 and d.day == 15:
            lic = rnd(48_000 * T * cs / (500 * T)) * 500 * T
            src = pay_from(d, lic, ["bank"], reserves)
            w.post(d, [("lic", lic), (src, -lic)], ref=f"LIC{d.year}", memo="Trade licence, signboard tax and fire licence", kind="licence")
        if d.day == 28:
            w.post(d, [("fees", 600 * T), ("bank", -600 * T)], ref=f"BANKFEE{d:%Y%m}", memo="Bank account maintenance and cheque book charges", kind="bank_fee")
        for L in loans:
            if L["start"].day == d.day and d > L["start"] and L["bal"] > 0:
                interest = rnd(L["bal"] * L["rate"] / 12)
                prin = min(L["pmt"] - interest, L["bal"])
                src = pay_from(d, prin + interest, ["bank"], reserves)
                g = f"LOAN{d:%Y%m}"
                w.post(d, [("loan", prin), (src, -prin)], ref=g, memo="Bank term loan instalment - principal", kind="loan_payment", tags={"grp": g})
                w.post(d, [("intr", interest), (src, -interest)], ref=g + "I", memo="Bank term loan instalment - interest", kind="loan_interest", tags={"grp": g})
                L["bal"] -= prin
            if d == L["start"] and L["start"].year == 2023:
                w.post(d, [("bank", L["principal"]), ("loan", -L["principal"])], ref="LOAN2", memo="New equipment loan disbursed", kind="loan_drawdown")
                e = w.post(d + dt.timedelta(days=6), [("fa", 1_850_000 * T), ("bank", -1_850_000 * T)], ref="CHILLER", memo="Walk-in chiller and two display freezers", kind="equipment")
                L["bal"] = L["principal"]
                sim.problems_noise.append(("large_equipment_purchase", d + dt.timedelta(days=6), [e.id]))  # type: ignore[attr-defined]

        # ---------------- daily small cash expenses (porter, tea, rickshaw, chanda, tips, small repairs)
        if not closed and rng.random() < 0.36:
            amt = int(rng.integers(8, 26)) * 100 * T * cs ** 0.3
            amt = rnd(amt / (50 * T)) * 50 * T
            memo = ["Porter", "Rickshaw hire", "Tea and snacks for staff", "Chanda (mosque/local club)", "Tips", "Small repair", "Plastic bags", "Mobile load"][int(rng.integers(8))]
            src = "cash" if w.bal("cash") - amt > 20_000 * T else "bank"
            e = w.post(d, [("misc", amt), (src, -amt)], ref="", memo=memo, kind="misc_cash")
            if rng.random() < 0.18:
                recorded_misc.append(e.id)
            else:
                unrec_cash[ym(d)].append(e.id)
        # owner mixes personal spending with the shop wallet
        if rng.random() < 0.15 and w.bal("wallet") > 30_000 * T:
            amt = int(rng.integers(8, 46)) * 100 * T
            e = w.post(d, [("draw", amt), ("wallet", -amt)], ref="", memo="Owner personal bKash payment (household)", kind="personal_wallet")
            personal[ym(d)].append(e.id)

        # ---------------- till to bank on the 1st and 15th, wallet to bank weekly
        if d.day in (1, 15) and d > D0:
            dep = ((w.bal("cash") - 250_000 * T) // (50_000 * T)) * 50_000 * T
            if dep > 0:
                e = w.post(d, [("bank", dep), ("cash", -dep)], ref=f"DEP{d:%Y%m%d}", memo="Cash deposited to bank", kind="cash_deposit")
                deposits.append((e.id, d))
        if d.weekday() == 6:
            wd_amt = ((w.bal("wallet") - 25_000 * T) // (10_000 * T)) * 10_000 * T
            if wd_amt > 0:
                w.post(d, [("bank", wd_amt), ("wallet", -wd_amt)], ref=f"WTB{d:%Y%m%d}", memo="bKash merchant settlement to bank", kind="wallet_to_bank")
        # ---------------- month end: COGS and shrinkage (stock count)
        month_cogs[ym(d)] += cogs_day[d]
        if d == month_end(d):
            m = ym(d)
            c = month_cogs[m]
            e = w.post(d, [("cogs", c), ("inv", -c)], ref=f"COGS{m}", memo=f"Cost of goods sold {m} (opening stock + purchases - closing stock)", kind="cogs")
            cogs_ids[m] = e.id
            sr = 0.0045 + 0.0015 * float(rng.random()) + (0.002 if d.month in (6, 7, 8, 9) else 0)
            sh = rnd(c * sr / T) * T
            inv_est -= sh
            e = w.post(d, [("shrink", sh), ("inv", -sh)], ref=f"SHR{m}", memo=f"Spoilage, egg breakage, loose-goods weight loss {m}", kind="shrinkage")
            shrink_ids[m] = e.id

    sim.meta.update(dict(unrec_cash=unrec_cash, recorded_misc=recorded_misc, personal=personal, fee_entries=fee_entries,
                         shrink_ids=shrink_ids, cogs_ids=cogs_ids, wallet_sale_ids=wallet_sale_ids, deposits=deposits,
                         purchases=purchases, advances=advances, adv_consume=adv_consume, wallet_cust_pay=wallet_cust_pay,
                         misclass_cands=misclass_cands, delivery_wage_ids=delivery_wage_ids, sales_t=sales_t,
                         hh_bal=hh_bal, hh_open=hh_open, ratio=ratio))
    sim.rng = rng_for(seed, CID, "plant")  # type: ignore[attr-defined]
    return sim
