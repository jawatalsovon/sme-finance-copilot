"""cn_xborder: Lanxin Digital Accessories Co. (Shenzhen). Cross-border seller, base CNY, sells in USD/GBP/EUR.
SYNTHETIC - no real firm.  Marketplace/payout layouts are 'style of', not copies.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

import calendars as cal
from core import (D0, D1, Acct, Anchors, Book, Entry, Problems, World, add_months, days, month_end, rng_for, rnd,
                  scale_to_targets, weighted_split, ym)

CID = "cn_xborder"
COMPANY = dict(name="Lanxin Digital Accessories Co.", country="CN", base_currency="CNY", industry="cross_border_ecommerce", fiscal_year_start_month=1,
               founded_on="2016-06-01", description="Shenzhen phone/laptop accessories seller; Amazon-style marketplace, own storefront and EU wholesale; Wise-style multi-currency account.")
C = 100
REF_RATE = 7.0
USD_SALES = {2020: 1.05e6, 2021: 1.65e6, 2022: 1.80e6, 2023: 1.62e6, 2024: 1.84e6, 2025: 1.88e6, 2026: 1.50e6}
COST_SCALE = {2020: 0.80, 2021: 0.86, 2022: 0.92, 2023: 0.96, 2024: 1.0, 2025: 1.04, 2026: 1.07}
MONTH_W = {1: .068, 2: .062, 3: .068, 4: .070, 5: .072, 6: .074, 7: .090, 8: .078, 9: .070, 10: .085, 11: .140, 12: .123}

ACCTS = [
    Acct("petty", "1000", "Petty cash (CNY, office safe)", "asset", "cash", "CNY"),
    Acct("bank", "1100", "CNY current account", "asset", "bank", "CNY"),
    Acct("wise_usd", "1110", "Wise-style balance USD", "asset", "bank", "USD"),
    Acct("wise_eur", "1111", "Wise-style balance EUR", "asset", "bank", "EUR"),
    Acct("wise_gbp", "1112", "Wise-style balance GBP", "asset", "bank", "GBP"),
    Acct("clr_us", "1210", "Marketplace settlement due - US (USD)", "asset", "ar", "USD"),
    Acct("clr_uk", "1211", "Marketplace settlement due - UK (GBP)", "asset", "ar", "GBP"),
    Acct("clr_eu", "1212", "Marketplace settlement due - EU (EUR)", "asset", "ar", "EUR"),
    Acct("clr_store", "1213", "Storefront processor balance (USD)", "asset", "ar", "USD"),
    Acct("ar_b2b", "1220", "Wholesale customers (EUR)", "asset", "ar", "EUR"),
    Acct("inv", "1300", "Inventory", "asset", "inventory", "CNY"),
    Acct("prepaid", "1350", "Supplier deposits", "asset", "prepaid", "CNY"),
    Acct("fa", "1500", "Tooling and equipment", "asset", "fixed_asset", "CNY"),
    Acct("loan", "2300", "Bank working-capital loan", "liability", "loan", "CNY"),
    Acct("cap", "3000", "Owner's capital", "equity", "owner_equity", "CNY"),
    Acct("draw", "3100", "Owner's dividends and drawings", "equity", "drawings", "CNY"),
    Acct("rev_us", "4010", "Sales - marketplace US", "income", "revenue", "CNY"),
    Acct("rev_uk", "4011", "Sales - marketplace UK", "income", "revenue", "CNY"),
    Acct("rev_eu", "4012", "Sales - marketplace EU", "income", "revenue", "CNY"),
    Acct("rev_store", "4013", "Sales - own storefront", "income", "revenue", "CNY"),
    Acct("rev_b2b", "4014", "Sales - EU wholesale", "income", "revenue", "CNY"),
    Acct("refunds", "4090", "Customer refunds and returns", "income", "revenue", "CNY"),
    Acct("othinc", "4900", "Marketplace reimbursements and other income", "income", "other_income", "CNY"),
    Acct("cogs", "5000", "Goods cost", "expense", "cogs", "CNY"),
    Acct("freight", "5100", "Inbound freight", "expense", "cogs", "CNY"),
    Acct("duty", "5150", "Import duties and customs", "expense", "cogs", "CNY"),
    Acct("shrink", "5200", "Lost and damaged stock", "expense", "cogs", "CNY"),
    Acct("fee_ref", "6010", "Marketplace referral fees", "expense", "opex", "CNY"),
    Acct("fee_fba", "6011", "Fulfilment fees", "expense", "opex", "CNY"),
    Acct("fee_store", "6012", "Storage fees", "expense", "opex", "CNY"),
    Acct("fee_sub", "6013", "Seller subscription", "expense", "opex", "CNY"),
    Acct("fee_pay", "6014", "Payment processing fees", "expense", "opex", "CNY"),
    Acct("ads", "6020", "Advertising", "expense", "marketing", "CNY"),
    Acct("sal", "6100", "Salaries and social security", "expense", "payroll", "CNY"),
    Acct("rent", "6200", "Office and warehouse rent", "expense", "rent", "CNY"),
    Acct("soft", "6300", "Software and services", "expense", "opex", "CNY"),
    Acct("acct", "6310", "Accounting and legal", "expense", "opex", "CNY"),
    Acct("samples", "6400", "Samples, tooling and small supplier costs", "expense", "opex", "CNY"),
    Acct("bankfee", "6500", "Bank and transfer fees", "expense", "opex", "CNY"),
    Acct("intr", "6600", "Loan interest", "expense", "interest", "CNY"),
    Acct("fx_gl", "6900", "Exchange gain / loss", "expense", "fx_loss", "CNY"),
    Acct("bad", "6950", "Bad debts written off", "expense", "other_expense", "CNY"),
    Acct("refund_exp", "6960", "Refunds and returns (expense)", "expense", "other_expense", "CNY"),
]
WISE = {"USD": "wise_usd", "EUR": "wise_eur", "GBP": "wise_gbp"}
FACTORIES = ["Dongguan Hengda Electronics", "Shenzhen Boyuan Cable Co.", "Huizhou Sunrise Plastics", "Shenzhen GaN Power Tech", "Dongguan Fengshun Leather",
             "Shenzhen Weiye Mould", "Guangzhou Anke Packaging"]
B2B_CUST = ["Elektro Nord GmbH", "Digitalwaren Rhein GmbH", "Mobilzubehor Kontor GmbH", "Accessoires Lyon SARL", "Tecnomarket Milano SRL", "Gadget Hub Iberia SL"]
EMP = [("Managing director", 18000), ("Operations 1", 9000), ("Operations 2", 8500), ("Operations 3", 8000), ("Product design 1", 12000), ("Product design 2", 11000),
       ("Warehouse 1", 7000), ("Warehouse 2", 6800), ("Finance", 9500), ("Customer service", 6500)]
# channel parameters (rates on principal)
CH = {
    "US": dict(ccy="USD", clr="clr_us", rev="rev_us", mkt="Amazon.com", share=0.60, ref=0.150, fba=0.108, ads=0.105, refund=0.042, storage=0.012),
    "UK": dict(ccy="GBP", clr="clr_uk", rev="rev_uk", mkt="Amazon.co.uk", share=0.12, ref=0.150, fba=0.118, ads=0.090, refund=0.045, storage=0.012),
    "EU": dict(ccy="EUR", clr="clr_eu", rev="rev_eu", mkt="Amazon.de", share=0.10, ref=0.150, fba=0.122, ads=0.090, refund=0.050, storage=0.012),
    "STORE": dict(ccy="USD", clr="clr_store", rev="rev_store", mkt="OwnStore", share=0.18, ref=0.0, fba=0.0, ads=0.0, refund=0.025, storage=0.0, pay=0.029),
}
STORE_ADS = 0.11  # storefront ads paid from the USD balance by card, share of storefront principal


@dataclass
class Sim:
    world: World
    book: Book = None  # type: ignore[assignment]
    problems: Problems = None  # type: ignore[assignment]
    meta: dict = field(default_factory=dict)
    entry_info: dict[str, dict] = field(default_factory=dict)
    daily: dict = field(default_factory=dict)  # (channel, date) -> dict of native minor amounts
    periods: dict = field(default_factory=dict)  # (channel, k) -> dict
    conversions: list = field(default_factory=list)
    purchases: list = field(default_factory=list)
    b2b: list = field(default_factory=list)
    bills: list = field(default_factory=list)
    payroll: list = field(default_factory=list)
    problems_noise: list = field(default_factory=list)


def usd_per(ccy: str, d: dt.date, A: Anchors) -> float:
    return {"USD": 1.0, "GBP": A.usd_per_gbp(d), "EUR": A.usd_per_eur(d)}[ccy]


def build(seed: int, A: Anchors) -> Sim:
    rng = rng_for(seed, CID, "sim")
    w = World(CID, "CNY", ACCTS, fx=lambda ccy, d: A.cny_per(ccy, d), fx_acct="fx_gl")
    sim = Sim(w)
    all_days = list(days())

    # ------------------------------------------------------------------ daily sales (USD equivalent)
    raw = {}
    for d in all_days:
        f = MONTH_W[d.month] * (31 / 30.4)
        f *= [1.04, 1.02, 0.99, 0.98, 0.99, 0.98, 1.00][d.weekday()]
        if d.year == 2020:
            f *= 0.55 if dt.date(2020, 3, 1) <= d <= dt.date(2020, 4, 30) else (1.28 if d >= dt.date(2020, 6, 1) else 1.0)
        bf = cal.black_friday(d.year)
        if bf <= d <= bf + dt.timedelta(days=3):
            f *= 1.9 if d != bf else 2.2
        pd_ = cal.PRIME_DAY[d.year]
        if pd_ <= d <= pd_ + dt.timedelta(days=1):
            f *= 2.0
        if dt.date(d.year, 12, 18) <= d <= dt.date(d.year, 12, 31):
            f *= 0.85
        if d.year == 2025 and d >= dt.date(2025, 8, 29):
            f *= 0.93  # tariff / de-minimis changes (assumed, not verified)
        raw[d] = f * float(np.exp(rng.normal(0, 0.11)))
    usd_total = scale_to_targets(raw, {y: int(v * 100) for y, v in USD_SALES.items()})  # USD cents per day
    # channel split
    chans = list(CH)
    drift = {c: 0.0 for c in chans}
    D = sim.daily
    for d in all_days:
        shares = np.array([CH[c]["share"] * float(np.exp(rng.normal(0, 0.05))) for c in chans])
        parts = weighted_split(usd_total[d], list(shares))
        for c, usd_c in zip(chans, parts):
            ccy = CH[c]["ccy"]
            prin = rnd(usd_c / usd_per(ccy, d, A)) if ccy != "USD" else usd_c
            p = CH[c]
            refunds = rnd(prin * p["refund"] * float(np.exp(rng.normal(0, 0.25))))
            orders = max(1, rnd(usd_c / 100 / 27)) if usd_c else 0
            rec = dict(prin=prin, refunds=refunds, orders=orders,
                       ref=rnd(prin * p["ref"]), fba=rnd(prin * p["fba"]), ads=rnd(prin * p["ads"]),
                       pay=(rnd(prin * p.get("pay", 0)) + 30 * orders) if c == "STORE" else 0, reimb=0)
            if c != "STORE" and rng.random() < 0.12:
                rec["reimb"] = rnd(prin * 0.03 * float(rng.uniform(0.5, 1.5)))
            rec["ref_refund_fee"] = rnd(refunds * p["ref"] * 0.2) if c != "STORE" else 0  # refund admin fee kept by the marketplace
            rec["ref"] += rec["ref_refund_fee"]
            D[(c, d)] = rec
    # monthly storage (last day) and subscription (day 1, US)
    for d in all_days:
        if d.day == 1:
            D[("US", d)]["sub"] = 3999
        if d == month_end(d):
            for c in ("US", "UK", "EU"):
                mon = sum(D[(c, x)]["prin"] for x in days(d.replace(day=1), d))
                D[(c, d)]["storage"] = rnd(mon * CH[c]["storage"] * float(rng.uniform(0.85, 1.15)))

    # goods cost plan (CNY cents per day) - ratio follows oil (plastics) and the 2025 tariffs show up in duty/freight, not here
    months = sorted({ym(d) for d in all_days})
    cogs_ratio = {}
    for m in months:
        dd = dt.date(int(m[:4]), int(m[5:]), 15)
        cogs_ratio[m] = 0.385 * (0.88 + 0.12 * A.commodity_index("brent_usd_bbl", dd)) * (1.0 + (0.03 if dd.year >= 2022 else 0.0))
    cogs_day: dict[dt.date, int] = {}
    for d in all_days:
        net_usd = sum(D[(c, d)]["prin"] * (usd_per(CH[c]["ccy"], d, A)) - 0.85 * D[(c, d)]["refunds"] * usd_per(CH[c]["ccy"], d, A) for c in chans)
        cogs_day[d] = rnd(net_usd * REF_RATE * cogs_ratio[ym(d)])

    # ------------------------------------------------------------------ opening
    OPEN = dict(petty=8_000 * C, bank=650_000 * C, inv=1_300_000 * C, fa=150_000 * C)
    open_usd, open_eur, open_gbp = 42_000 * C, 6_000 * C, 4_000 * C
    spec = [("petty", OPEN["petty"]), ("bank", OPEN["bank"]), ("wise_usd", open_usd), ("wise_eur", open_eur), ("wise_gbp", open_gbp), ("inv", OPEN["inv"]), ("fa", OPEN["fa"])]
    w.post(D0, spec + [("cap", -sum(rnd(a * w.fx(w.accts[k].ccy, D0)) if w.accts[k].ccy != "CNY" else a for k, a in spec))], ref="OPENING", memo="Opening balances 1 Jan 2020", kind="opening")

    # ------------------------------------------------------------------ schedules
    periods = {}
    k = 0
    while D0 + dt.timedelta(days=14 * k) <= D1:
        a = D0 + dt.timedelta(days=14 * k)
        periods[k] = (a, a + dt.timedelta(days=13))
        k += 1
    seg_ends = {c: defaultdict(list) for c in chans}
    for kk, (a, b) in periods.items():
        for c in chans:
            cur = a
            while cur <= min(b, D1):
                end = min(b, month_end(cur), D1)
                seg_ends[c][end].append((kk, cur))
                cur = end + dt.timedelta(days=1)
    payout_day = {(b + dt.timedelta(days=2)): kk for kk, (a, b) in periods.items()}
    reserve_windows = [(dt.date(2022, 11, 1), dt.date(2023, 2, 28), 0.20), (dt.date(2025, 6, 1), dt.date(2025, 8, 31), 0.15)]
    reserve_old = {c: 0 for c in chans}
    acc = defaultdict(int)  # (channel, k) -> net due for the settlement
    sim.periods = {kk: dict(start=a, end=b) for kk, (a, b) in periods.items()}

    # ------------------------------------------------------------------ state for the day loop
    inv_est = OPEN["inv"]
    pending_receipts: list[dict] = []
    loan_draw = {2021: 800_000, 2022: 1_200_000, 2023: 1_200_000, 2024: 1_500_000, 2025: 1_500_000}
    loan_bal = 0
    cs = lambda d: COST_SCALE[d.year]  # noqa: E731
    seq = defaultdict(int)
    freight_acc = defaultdict(int)  # month -> usd cents accrued
    duty_acc = defaultdict(int)
    store_ads_acc = defaultdict(int)
    shrink_ids: dict[str, str] = {}
    cogs_ids: dict[str, str] = {}
    fee_ids: dict[tuple, list] = defaultdict(list)      # (channel,k) -> fee entry ids
    pay_ids: dict[tuple, str] = {}                      # (channel,k) -> payout entry id
    reimb_ids: dict[tuple, list] = defaultdict(list)
    refund_ids: dict[tuple, list] = defaultdict(list)
    sale_ids: dict[tuple, list] = defaultdict(list)
    conv_ids: dict[str, list] = defaultdict(list)       # month -> conversion entry ids
    samples_unrec: dict[str, list] = defaultdict(list)
    deposit_ids: list[tuple] = []
    freight_ids: dict[str, list] = defaultdict(list)
    pay_info: dict[str, dict] = {}
    month_cogs: dict[str, int] = defaultdict(int)
    emp_pay_seq = 0
    b2b_open: list[dict] = []
    b2b_count = 0

    def fund_bank(d: dt.date, need: int) -> None:
        nonlocal loan_bal
        short = need + 200_000 * C - w.bal("bank")
        if short <= 0:
            return
        amt = ((short // (100_000 * C)) + 1) * 100_000 * C
        e = w.post(d, [("bank", amt), ("cap", -amt)], ref="CAPITAL", memo="Owner transfers funds to cover working capital", kind="owner_injection")
        sim.problems_noise.append(("owner_injection", d, [e.id]))

    for d in all_days:
        # ---- sales segments ending today
        for c in chans:
            for (kk, seg_start) in seg_ends[c].get(d, []):
                p = CH[c]
                rows = [D[(c, x)] for x in days(seg_start, d)]
                tot = lambda key: sum(r.get(key, 0) for r in rows)  # noqa: E731
                prin, refunds = tot("prin"), tot("refunds")
                fee_lines = [("fee_ref", tot("ref")), ("fee_fba", tot("fba")), ("ads", tot("ads")), ("fee_store", tot("storage")), ("fee_sub", tot("sub")), ("fee_pay", tot("pay"))]
                fees_total = sum(a for _, a in fee_lines)
                ccy = p["ccy"]
                e = w.post(d, [(p["clr"], prin), (p["rev"], -prin, ccy)], ref=f"SET-{c}-{kk:03d}-S", memo=f"{p['mkt']} sales {seg_start:%d %b}-{d:%d %b %Y}", kind="mkt_sale", tags={"channel": c, "k": kk, "seg": seg_start.isoformat()})
                sale_ids[(c, kk)].append(e.id)
                if refunds:
                    e = w.post(d, [("refunds", refunds, ccy), (p["clr"], -refunds)], ref=f"SET-{c}-{kk:03d}-R", memo=f"{p['mkt']} customer refunds {seg_start:%d %b}-{d:%d %b %Y}", kind="mkt_refund", tags={"channel": c, "k": kk})
                    refund_ids[(c, kk)].append(e.id)
                spec = [(k_, a, ccy) for k_, a in fee_lines if a] + [(p["clr"], -fees_total)]
                e = w.post(d, spec, ref=f"SET-{c}-{kk:03d}-F", memo=f"{p['mkt']} fees and charges {seg_start:%d %b}-{d:%d %b %Y}", kind="mkt_fees", tags={"channel": c, "k": kk})
                fee_ids[(c, kk)].append(e.id)
                reimb = tot("reimb")
                reimb_total_net = prin - refunds - fees_total + reimb
                if reimb:
                    e = w.post(d, [(p["clr"], reimb), ("othinc", -reimb, ccy)], ref=f"SET-{c}-{kk:03d}-X", memo=f"{p['mkt']} reimbursement for lost/damaged units", kind="mkt_reimb", tags={"channel": c, "k": kk})
                    reimb_ids[(c, kk)].append(e.id)
                acc[(c, kk)] += reimb_total_net
                if c == "STORE":
                    ad = rnd(sum(r["prin"] for r in rows) * STORE_ADS)
                    store_ads_acc[ym(d)] += ad
        # ---- payouts
        if d in payout_day:
            kk = payout_day[d]
            a_, b_ = periods[kk]
            for c in chans:
                p = CH[c]
                net = acc[(c, kk)]
                res_new = 0
                if c == "US":
                    for ws, we, pct in reserve_windows:
                        if ws <= a_ <= we:
                            res_new = rnd(max(net, 0) * pct)
                pay = net - res_new + reserve_old[c]
                if pay > 0:
                    w_acct = WISE[p["ccy"]]
                    e = w.post(d, [(w_acct, pay), (p["clr"], -pay)], ref=f"PAYOUT-{c}-{kk:03d}", memo=f"{p['mkt']} settlement {kk:03d} deposit", kind="mkt_payout", tags={"channel": c, "k": kk})
                    pay_ids[(c, kk)] = e.id
                    pay_info[e.id] = dict(net=net, reserve_new=res_new, reserve_old=reserve_old[c], channel=c, k=kk)
                reserve_old[c] = res_new
        # ---- B2B invoices (EUR)
        if d.day in (6, 21) and d.month in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12) and rng.random() < (0.55 if d.day == 6 else 0.50):
            amt = int(rng.integers(55, 130)) * 100 * C * (1 + 0.15 * (d.year - 2020) / 6)
            amt = rnd(amt / 5000) * 5000
            cust = int(rng.integers(len(B2B_CUST)))
            b2b_count += 1
            inv_id = f"inv-{CID}-{b2b_count:04d}"
            e = w.post(d, [("ar_b2b", amt), ("rev_b2b", -amt, "EUR")], ref=f"WS{b2b_count:04d}", memo=f"Invoice WS{b2b_count:04d} to {B2B_CUST[cust]}", kind="b2b_invoice")
            delay = int(rng.integers(30, 75))
            row = dict(id=inv_id, no=f"WS{b2b_count:04d}", cust=cust, date=d, due=d + dt.timedelta(days=45), total=amt, entry=e.id, pay_date=d + dt.timedelta(days=delay), paid=False, written_off=None)
            sim.b2b.append(row)
            b2b_open.append(row)
        for row in [r for r in b2b_open if r["pay_date"] <= d and not r.get("never")]:
            if row.get("never") is None:
                row["never"] = bool(rng.random() < 0.07)  # ~7% of wholesale invoices are never collected
                if row["never"]:
                    continue
            e = w.post(d, [("wise_eur", row["total"]), ("ar_b2b", -row["total"])], ref=f"WSPAY-{row['no']}", memo=f"Payment from {B2B_CUST[row['cust']]} for {row['no']}", kind="b2b_payment")
            row["paid"], row["pay_entry"], row["pay_actual"] = True, e.id, d
            b2b_open.remove(row)
        for row in list(b2b_open):
            if row.get("never") and not row["written_off"] and d >= row["due"] + dt.timedelta(days=200):
                amt_base = w.bs["ar_b2b"] and rnd(row["total"] * w.bs["ar_b2b"] / w.nat["ar_b2b"])
                e = w.post(d, [("bad", amt_base), ("ar_b2b", -row["total"])], ref=f"WO-{row['no']}", memo=f"Written off {row['no']} ({B2B_CUST[row['cust']]}): no payment 200+ days after due", kind="writeoff")
                row["written_off"], row["wo_entry"] = d, e.id
                b2b_open.remove(row)

        # ---- freight and duties (USD) accrue from principal, paid twice monthly / monthly
        prin_usd = sum(D[(c, d)]["prin"] * usd_per(CH[c]["ccy"], d, A) for c in chans)
        g = A.gscpi(d - dt.timedelta(days=30))
        freight_acc[ym(d)] += prin_usd * 0.040 * float(np.clip(1 + 0.30 * g, 0.7, 2.6))
        us_usd = D[("US", d)]["prin"] + D[("STORE", d)]["prin"]
        duty_acc[ym(d)] += us_usd * (0.015 if d < dt.date(2025, 4, 10) else 0.055)
        if d.day == 15 or d == month_end(d):
            m = ym(d)
            frac = 0.5 if d.day == 15 else 1.0
            amt = rnd(freight_acc[m] * frac)
            if d == month_end(d):
                amt = rnd(freight_acc[m]) - freight_acc.get(m + "_paid", 0)
            freight_acc[m + "_paid"] = freight_acc.get(m + "_paid", 0) + amt
            if amt > 0:
                e = w.post(d, [("freight", amt, "USD"), ("wise_usd", -amt)], ref=f"FRT{d:%Y%m%d}", memo="Freight forwarder: sea/air freight invoice (USD)", kind="freight")
                freight_ids[m].append(e.id)
        if d == month_end(d):
            m = ym(d)
            dty = rnd(duty_acc[m])
            if dty:
                w.post(d, [("duty", dty, "USD"), ("wise_usd", -dty)], ref=f"DUTY{m}", memo="Customs broker: import duties and clearance", kind="duty")
            ad = store_ads_acc[m]
            if ad:
                w.post(d, [("ads", ad, "USD"), ("wise_usd", -ad)], ref=f"ADS{m}", memo="Social ads (card payment, USD)", kind="store_ads")

        # ---- Wise conversions to CNY (every 14 days, 4 days after period end)
        for kk, (a_, b_) in periods.items():
            if d == b_ + dt.timedelta(days=4):
                for ccy, buf in (("USD", 30_000 * C), ("EUR", 2_000 * C), ("GBP", 2_000 * C)):
                    wa = WISE[ccy]
                    amt = (w.bal(wa) - buf) // (500 * C) * (500 * C)
                    if amt >= 2_000 * C:
                        rate = w.fx(ccy, d) * (1 - 0.0055)
                        cny = rnd(amt * rate)
                        e = w.post(d, [("bank", cny), (wa, -amt)], ref=f"CONV{d:%Y%m%d}{ccy}", memo=f"Converted {amt / 100:,.2f} {ccy} to CNY and sent to bank", kind="conversion", tags={"ccy": ccy})
                        sim.conversions.append(dict(entry=e.id, date=d, ccy=ccy, nat=amt, cny=cny, rate=rate))
                        conv_ids[ym(d)].append(e.id)

        # ---- production purchases (PO on 5th/15th/25th; deposit now, balance + receipt 30 days later)
        if d.day in (5, 15, 25):
            cny_close = cal.CNY[d.year]
            in_closure = cny_close - dt.timedelta(days=10) <= d <= cny_close + dt.timedelta(days=20)
            if not in_closure:
                land = d + dt.timedelta(days=30)
                lo = land
                horizon = sum(cogs_day.get(lo + dt.timedelta(days=i), cogs_day[d]) for i in range(0, 60)) if lo + dt.timedelta(days=60) <= D1 else 60 * cogs_day[d]
                buf = 1.0
                if d.month in (8, 9, 10):
                    buf = 1.40
                if d.month == 12 or (d.month == 1 and d.day <= 5):
                    buf = 1.25
                proj = inv_est + sum(r["total"] for r in pending_receipts) - sum(cogs_day.get(d + dt.timedelta(days=i), cogs_day[d]) for i in range(0, 30))
                q = int(max(0, horizon * buf - proj))
                q = (q // (1000 * C)) * (1000 * C)
                if q >= 20_000 * C:
                    parts = weighted_split(q, list(rng.uniform(0.5, 1.5, size=int(rng.integers(2, 4)))))
                    for part in parts:
                        part = (part // (100 * C)) * (100 * C)
                        dep = rnd(part * 0.30 / (100 * C)) * 100 * C
                        fac = int(rng.integers(len(FACTORIES)))
                        fund_bank(d, dep)
                        seq["po"] += 1
                        po = f"PO{d:%y%m}-{seq['po']:03d}"
                        e = w.post(d, [("prepaid", dep), ("bank", -dep)], ref=po + "-DEP", memo=f"Deposit 30% to {FACTORIES[fac]} for {po}", kind="po_deposit")
                        rec = dict(po=po, fac=fac, order_date=d, land=land, total=part, dep=dep, dep_entry=e.id)
                        pending_receipts.append(rec)
                        sim.purchases.append(rec)
                        deposit_ids.append((e.id, d, dep))
        for r in [x for x in pending_receipts if x["land"] <= d]:
            bal_pay = r["total"] - r["dep"]
            fund_bank(d, bal_pay)
            e = w.post(d, [("inv", r["total"]), ("prepaid", -r["dep"]), ("bank", -bal_pay)], ref=r["po"] + "-BAL", memo=f"Balance 70% to {FACTORIES[r['fac']]} and goods received for {r['po']}", kind="po_receipt")
            r["receipt_entry"] = e.id
            inv_est += r["total"]
            pending_receipts.remove(r)
        inv_est -= cogs_day[d]

        # ---- payroll, rent, services, loan
        scl = cs(d)
        if d.day == 10 and d > D0:
            pm = add_months(d, -1)
            tot_net = 0
            rows = []
            for i, (role, base) in enumerate(EMP):
                gross = rnd(base * scl / 100) * 100 * C
                rows.append((i, role, gross))
            net_total = sum(r_[2] for r_ in rows)
            social = rnd(net_total * 0.22 / (100 * C)) * 100 * C
            fund_bank(d, net_total)
            e1 = w.post(d, [("sal", net_total), ("bank", -net_total)], ref=f"PAY{pm:%Y%m}", memo=f"Payroll {pm:%b %Y} (net to staff)", kind="payroll")
            sim.payroll.append(dict(month=pm, rows=rows, entry=e1.id, pay_date=d, social=social))
        if d.day == 15 and d > D0:
            pm = add_months(d, -1)
            last = sim.payroll[-1]
            fund_bank(d, last["social"])
            e2 = w.post(d, [("sal", last["social"]), ("bank", -last["social"])], ref=f"SOC{pm:%Y%m}", memo=f"Social insurance and housing fund {pm:%b %Y}", kind="payroll_social")
            last["social_entry"] = e2.id
        if d.day == 3:
            rent = rnd(31_000 * scl / 500) * 500 * C
            fund_bank(d, rent)
            w.post(d, [("rent", rent), ("bank", -rent)], ref=f"RENT{d:%Y%m}", memo=f"Office and warehouse rent {d:%b %Y}", kind="rent")
        if d.day == 8:
            sv = rnd(6_500 * scl / 10) * 10 * C
            fund_bank(d, sv)
            w.post(d, [("soft", sv), ("bank", -sv)], ref=f"SOFT{d:%Y%m}", memo="ERP, listing tools, design software subscriptions", kind="software")
        if d.day == 12:
            ac = rnd(3_000 * scl / 100) * 100 * C
            fund_bank(d, ac)
            w.post(d, [("acct", ac), ("bank", -ac)], ref=f"ACC{d:%Y%m}", memo="Bookkeeper monthly fee", kind="accounting")
        if d.day == 28:
            bf = rnd(550 * scl) * C // 1
            w.post(d, [("bankfee", bf), ("bank", -bf)], ref=f"BF{d:%Y%m}", memo="Bank account and remittance charges", kind="bank_fee")
        # loans: draw 1 Sep, repay 25 Aug following, interest on 20th
        if d.month == 9 and d.day == 1 and d.year in loan_draw:
            amt = loan_draw[d.year] * C
            w.post(d, [("bank", amt), ("loan", -amt)], ref=f"LOAN{d.year}", memo="Working-capital loan drawn for the peak-season stock build", kind="loan_drawdown")
            loan_bal += amt
        if d.month == 8 and d.day == 25 and loan_bal > 0:
            fund_bank(d, loan_bal)
            w.post(d, [("loan", loan_bal), ("bank", -loan_bal)], ref=f"LOANR{d.year}", memo="Working-capital loan repaid at maturity", kind="loan_repayment")
            loan_bal = 0
        if d.day == 20 and loan_bal > 0:
            it = rnd(loan_bal * 0.048 / 12)
            w.post(d, [("intr", it), ("bank", -it)], ref=f"LINT{d:%Y%m}", memo="Loan interest", kind="loan_interest")
        # dividends once a year
        if d.month == 3 and d.day == 28 and d.year >= 2021:
            amt = 700_000 * C
            fund_bank(d, amt)
            e = w.post(d, [("draw", amt), ("bank", -amt)], ref=f"DIV{d.year}", memo="Owner's annual dividend", kind="drawings")
        # petty cash: samples, tooling and small supplier costs paid in cash / by personal WeChat then reimbursed
        if d.day in (2, 9, 16, 23, 29) or rng.random() < 0.05:
            if rng.random() < 0.9:
                amt = rnd(float(rng.uniform(500, 4200)) * scl / 10) * 10 * C
                if w.bal("petty") < amt + 3_000 * C:
                    top = 30_000 * C
                    fund_bank(d, top)
                    e0 = w.post(d, [("petty", top), ("bank", -top)], ref=f"CASHW{d:%Y%m%d}", memo="Cash withdrawal for petty cash", kind="cash_withdrawal")
                memo = ["Sample units from factory", "Mould repair deposit", "Packaging sample", "Courier to factory (cash)", "Tooling fee paid by WeChat", "Product photo studio (cash)"][int(rng.integers(6))]
                e = w.post(d, [("samples", amt), ("petty", -amt)], ref="", memo=memo, kind="petty_expense")
                if rng.random() < 0.20:
                    pass
                else:
                    samples_unrec[ym(d)].append(e.id)
        # ---- month end: goods cost and lost stock
        month_cogs[ym(d)] += cogs_day[d]
        if d == month_end(d):
            m = ym(d)
            c_ = month_cogs[m]
            e = w.post(d, [("cogs", c_), ("inv", -c_)], ref=f"COGS{m}", memo=f"Cost of goods sold {m}", kind="cogs")
            cogs_ids[m] = e.id
            sh = rnd(c_ * float(rng.uniform(0.004, 0.012)) / C) * C
            inv_est -= sh
            e = w.post(d, [("shrink", sh), ("inv", -sh)], ref=f"SHR{m}", memo=f"Lost/damaged units at fulfilment centres not reimbursed {m}", kind="shrinkage")
            shrink_ids[m] = e.id

    sim.meta.update(dict(shrink_ids=shrink_ids, cogs_ids=cogs_ids, fee_ids=fee_ids, pay_ids=pay_ids, reimb_ids=reimb_ids, refund_ids=refund_ids, sale_ids=sale_ids,
                         conv_ids=conv_ids, samples_unrec=samples_unrec, deposit_ids=deposit_ids, freight_ids=freight_ids, pay_info=pay_info,
                         cogs_day=cogs_day, periods=periods, reserve_windows=reserve_windows, payout_day=payout_day, seg_ends=seg_ends, acc=dict(acc), cogs_ratio=cogs_ratio))
    return sim
