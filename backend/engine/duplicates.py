"""Duplicate-payment scan for a supplier payments ledger (bills / bill payments).

Plain words: two payments to the same supplier that look like one bill paid twice. A payment that
belongs to a schedule (the same supplier and the same amount in two or more different months, e.g.
rent, a care package, a placement) is treated as regular and never flagged as an exact or near repeat.

Rules, fixed before any scoring:
  exact_repeat  same supplier, same amount, same day, different document number, not a schedule
  near_repeat   same supplier, same amount, 1-7 days apart, not a schedule (nearest earlier payment)
  amount_typo   same supplier, same day, amounts 0.5%-10% apart, neither part of a schedule
  reversal      a negative payment (credit note) equal to an earlier payment to the same supplier within
                90 days: check that the original is not also refunded elsewhere or counted twice
  schedule_extra a schedule paid more times in one month than its usual number of times per month (the
                lower median of its monthly counts): the extra payment is paired with the nearest earlier one
Input columns: id, date, supplier_id, amount, doc_no. Output: one row per flagged pair.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

NEAR_DAYS = 7
TYPO_MIN, TYPO_MAX = 0.005, 0.10
REVERSAL_DAYS = 90


ALL_RULES = ("exact_repeat", "near_repeat", "amount_typo", "reversal", "schedule_extra")
CORE_RULES = ("exact_repeat", "near_repeat", "amount_typo", "reversal")


def payment_duplicates(d: pd.DataFrame, rules: tuple[str, ...] = CORE_RULES) -> pd.DataFrame:
    """rules: CORE_RULES (default, fewer false alarms) or ALL_RULES (adds schedule_extra: higher recall on
    repeats of regular payments, many more candidates to review)."""
    d = d[["id", "date", "supplier_id", "amount", "doc_no"]].copy()
    d["date"] = [x if isinstance(x, dt.date) else pd.Timestamp(x).date() for x in d["date"]]
    d["cents"] = (d["amount"] * 100).round().astype("int64")
    pos = d[d["cents"] > 0]
    months = pos.assign(m=[x.strftime("%Y-%m") for x in pos["date"]]).groupby(["supplier_id", "cents"])["m"].nunique()
    schedule = set(months[months >= 2].index)
    d["sched"] = [(s, c) in schedule for s, c in zip(d["supplier_id"], d["cents"])]
    out = []

    def add(rule, a, b):
        out.append({"rule": rule, "id_a": a.id, "id_b": b.id, "date_a": a.date, "date_b": b.date,
                    "amount_a": float(a.amount), "amount_b": float(b.amount), "supplier_id": a.supplier_id})

    for sup, g in d.groupby("supplier_id", sort=True):
        g = g.sort_values(["date", "doc_no", "id"])
        rows = list(g.itertuples())
        p = [r for r in rows if r.cents > 0]
        used = set()
        # exact and near repeats (non-schedule only)
        by_amt: dict[int, list] = {}
        for r in p:
            if not r.sched:
                by_amt.setdefault(r.cents, []).append(r)
        for _, rs in by_amt.items():
            for i, r in enumerate(rs):
                if r.id in used:
                    continue
                for e in reversed(rs[:i]):           # nearest earlier payment of the same amount
                    gap = (r.date - e.date).days
                    if gap > NEAR_DAYS:
                        break
                    if e.id in used or e.doc_no == r.doc_no:
                        continue
                    add("exact_repeat" if gap == 0 else "near_repeat", e, r)
                    used.add(r.id)
                    break
        # schedules paid more often than usual in a month
        sched_rows: dict[int, list] = {}
        if "schedule_extra" not in rules:
            p_sched = []
        else:
            p_sched = p
        for r in p_sched:
            if r.sched:
                sched_rows.setdefault(r.cents, []).append(r)
        for _, rs in sched_rows.items():
            per_month: dict[str, list] = {}
            for r in rs:
                per_month.setdefault(r.date.strftime("%Y-%m"), []).append(r)
            counts = sorted(len(v) for v in per_month.values())
            usual = counts[(len(counts) - 1) // 2]
            for _, mr in per_month.items():
                for i in range(usual, len(mr)):
                    r = mr[i]
                    e = next((x for x in reversed(mr[:i]) if x.id not in used and x.doc_no != r.doc_no), None)
                    if e is not None and r.id not in used:
                        add("schedule_extra", e, r)
                        used.add(r.id)
        # amount typos: same day, close but different amounts
        by_day: dict[dt.date, list] = {}
        for r in p:
            if not r.sched and r.id not in used:
                by_day.setdefault(r.date, []).append(r)
        for _, rs in by_day.items():
            rs = sorted(rs, key=lambda r: (r.cents, r.id))
            for x, y in zip(rs, rs[1:]):
                if x.id in used or y.id in used or x.cents == y.cents:
                    continue
                rel = (y.cents - x.cents) / y.cents
                if TYPO_MIN <= rel <= TYPO_MAX:
                    add("amount_typo", x, y)
                    used.add(y.id)
        # reversals: a credit note equal to an earlier payment
        for r in rows:
            if r.cents >= 0:
                continue
            for e in reversed([x for x in p if x.date <= r.date]):
                if (r.date - e.date).days > REVERSAL_DAYS:
                    break
                if e.cents == -r.cents and e.id not in used:
                    add("reversal", e, r)
                    used.add(e.id)
                    break
    out = [o for o in out if o["rule"] in rules]
    return pd.DataFrame(out, columns=["rule", "id_a", "id_b", "date_a", "date_b", "amount_a", "amount_b",
                                      "supplier_id"])
