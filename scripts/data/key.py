"""Answer key: derived from (truth, book, problems) - never typed by hand."""
from __future__ import annotations

import bisect
import datetime as dt
from collections import Counter, defaultdict
from typing import Callable, Iterable

from core import GEN_VERSION, Book, Entry, Problems, World, entry_json, money, month_end, rnd, ym, D0, D1

def asof_dates() -> list[dt.date]:
    out = []
    for y in range(2020, 2027):
        for m in (3, 6, 9, 12):
            d = month_end(dt.date(y, m, 1))
            if d <= D1:
                out.append(d)
    return sorted(set(out) | {dt.date(2025, 12, 31), D1})


PNL_ORDER = ["revenue", "other_income", "cogs", "payroll", "rent", "utilities", "marketing", "interest", "tax", "fx_loss", "other_expense", "opex"]


def pnl_by_month(entries: Iterable[Entry], w: World) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in entries:
        m = ym(e.date)
        row = out.setdefault(m, defaultdict(int))
        for l in e.lines:
            a = w.accts[l.acct]
            if a.type == "income":
                row[a.subtype] += -l.base
                row["_income"] += -l.base
            elif a.type == "expense":
                row[a.subtype] += l.base
                row["_expense"] += l.base
    res = {}
    for m, r in out.items():
        res[m] = r
    return res


def pnl_rows(entries, w, months: list[str]) -> list[dict]:
    p = pnl_by_month(entries, w)
    rows = []
    for m in months:
        r = p.get(m, {})
        rev, oi, cogs = r.get("revenue", 0), r.get("other_income", 0), r.get("cogs", 0)
        exp = {k: money(r.get(k, 0)) for k in PNL_ORDER if k not in ("revenue", "other_income", "cogs") and r.get(k, 0)}
        rows.append(dict(month=m, revenue=money(rev), other_income=money(oi), cogs=money(cogs), gross_profit=money(rev - cogs),
                         operating_expenses=exp, total_expenses=money(r.get("_expense", 0)), net_profit=money(r.get("_income", 0) - r.get("_expense", 0))))
    return rows


class CashIndex:
    """Cumulative native/base balance by date for statement accounts of a set of entries."""

    def __init__(self, entries: Iterable[Entry], w: World) -> None:
        by: dict[str, dict[dt.date, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for e in entries:
            for l in e.lines:
                if w.accts[l.acct].is_stmt:
                    x = by[l.acct][e.date]
                    x[0] += l.nat
                    x[1] += l.base
        self.ds: dict[str, list[dt.date]] = {}
        self.cum: dict[str, list[tuple[int, int]]] = {}
        for a, d in by.items():
            ds = sorted(d)
            n = b = 0
            cum = []
            for x in ds:
                n += d[x][0]
                b += d[x][1]
                cum.append((n, b))
            self.ds[a], self.cum[a] = ds, cum

    def at(self, acct: str, d: dt.date) -> tuple[int, int]:
        ds = self.ds.get(acct)
        if not ds:
            return 0, 0
        i = bisect.bisect_right(ds, d) - 1
        return self.cum[acct][i] if i >= 0 else (0, 0)


def build_key(*, cid: str, name: str, base: str, w: World, book: Book, P: Problems, trace, asofs: list[dt.date], fx_at: Callable[[str, dt.date], float],
              seed: int, extra: dict, findings: list[dict] | None = None) -> dict:
    months = []
    d = dt.date(D0.year, D0.month, 1)
    while d <= D1:
        months.append(ym(d))
        d = (month_end(d) + dt.timedelta(days=1))
    truth_idx = CashIndex(w.entries, w)
    book_idx = CashIndex(book.entries.values(), w)
    stmt_accts = [a for a in w.accts.values() if a.is_stmt]

    def base_at(ccy: str, nat: int, d: dt.date) -> int:
        return nat if ccy == base else rnd(nat * fx_at(ccy, d))

    # ---- problems
    probs = []
    for p in P.items:
        dl = P.delta_lines(p)
        gap_acct: dict[str, int] = defaultdict(int)
        pnl_m: dict[str, int] = defaultdict(int)
        for d_, acct, ccy, nat, bs in dl:
            a = w.accts[acct]
            if a.is_stmt:
                gap_acct[acct] += nat
            if a.type == "income":
                pnl_m[ym(d_)] += -bs
            elif a.type == "expense":
                pnl_m[ym(d_)] -= bs
        rows = []
        for bid in p["book_ids"]:
            for f, r in trace.by_ref.get(bid, []):
                rows.append(dict(file=f, row=r, role="book_row_to_correct"))
        for tid in p["truth_ids"]:
            for f, r in trace.by_ref.get(tid, []):
                rows.append(dict(file=f, row=r, role="evidence_row"))
        amt = p["amount"]
        ccy = p["ccy"] or base
        probs.append(dict(
            id=p["id"], type=p["type"], subtype=p["subtype"], date=p["date"].isoformat(), amount=money(amt) if amt is not None else None, currency=ccy,
            amount_base=money(base_at(ccy, amt, p["date"])) if amt is not None else None, description=p["description"],
            truth_entries=p["truth_ids"], book_entries=p["book_ids"], affected_source_rows=rows,
            gap_effect_native={w.accts[a].code: money(v) for a, v in sorted(gap_acct.items()) if v},
            net_profit_effect_if_uncorrected_by_month={m: money(v) for m, v in sorted(pnl_m.items()) if v},
            fix=P.fix_ops(p), details=p["extra"]))
    # ---- findings that do not change the ledger (e.g. stale receivables)
    for f in findings or []:
        probs.append(f)
    # ---- true P&L and book P&L
    true_pnl = pnl_rows(w.entries, w, months)
    book_pnl = pnl_rows(book.entries.values(), w, months)
    # ---- month-end cash
    me_cash = []
    for m in months:
        d_ = month_end(dt.date(int(m[:4]), int(m[5:]), 1))
        tot_t = tot_b = 0
        accs = []
        for a in stmt_accts:
            tn, _ = truth_idx.at(a.key, d_)
            bn, _ = book_idx.at(a.key, d_)
            tb, bb = base_at(a.ccy, tn, d_), base_at(a.ccy, bn, d_)
            tot_t += tb
            tot_b += bb
            accs.append(dict(account=f"{a.code} {a.name}", currency=a.ccy, true_balance=money(tn), book_balance=money(bn), true_balance_base=money(tb), book_balance_base=money(bb)))
        me_cash.append(dict(month_end=d_.isoformat(), accounts=accs, true_cash_total_base=money(tot_t), book_cash_total_base=money(tot_b), gap_base=money(tot_b - tot_t)))
    # ---- cash truth gap at as-of dates
    gaps = {}
    for d_ in asofs:
        tot_t = tot_b = 0
        accs = []
        for a in stmt_accts:
            tn, _ = truth_idx.at(a.key, d_)
            bn, _ = book_idx.at(a.key, d_)
            tb, bb = base_at(a.ccy, tn, d_), base_at(a.ccy, bn, d_)
            tot_t += tb
            tot_b += bb
            accs.append(dict(account=f"{a.code} {a.name}", kind=a.subtype, currency=a.ccy, book_balance=money(bn), verified_balance=money(tn), gap=money(bn - tn), gap_base=money(bb - tb)))
        by_type: dict[str, int] = defaultdict(int)
        expl_nat: dict[str, int] = defaultdict(int)
        n_by_type: Counter = Counter()
        for p in P.items:
            s = 0
            for d2, acct, ccy, nat, bs in P.delta_lines(p):
                if d2 <= d_ and w.accts[acct].is_stmt:
                    s += base_at(w.accts[acct].ccy, nat, d_)
                    expl_nat[acct] += nat
            if s:
                by_type[p["type"]] += s
                n_by_type[p["type"]] += 1
        ttm = 0
        lo = d_ - dt.timedelta(days=364)
        for e in w.entries:
            if lo <= e.date <= d_:
                for l in e.lines:
                    if w.accts[l.acct].subtype == "revenue":
                        ttm += -l.base
        gap = tot_b - tot_t
        unexpl = 0
        for a in stmt_accts:
            unexpl += base_at(a.ccy, (book_idx.at(a.key, d_)[0] - truth_idx.at(a.key, d_)[0]) - expl_nat[a.key], d_)
        explained = gap - unexpl
        gaps[d_.isoformat()] = dict(
            as_of=d_.isoformat(), base_currency=base, accounts=accs, book_cash_total=money(tot_b), verified_cash_total=money(tot_t), gap=money(gap),
            ttm_revenue=money(ttm), gap_pct_of_ttm_revenue=round(100.0 * gap / ttm, 3) if ttm else None,
            gap_by_problem_type={k: money(v) for k, v in sorted(by_type.items())}, problems_contributing_by_type=dict(sorted(n_by_type.items())),
            explained_by_planted_problems=money(explained), unexplained=money(unexpl),
            meaning="positive = the books show more cash than really exists; verified = bank/wallet statement balances and physical cash counts")
    # ---- summary
    yearly = defaultdict(lambda: [0, 0])
    for r in true_pnl:
        y = r["month"][:4]
        yearly[y][0] += round(float(r["revenue"]) * 100)
        yearly[y][1] += round(float(r["net_profit"]) * 100)
    summary = dict(
        company_id=cid, name=name, base_currency=base, window=[D0.isoformat(), D1.isoformat()], seed=seed, generator_version=GEN_VERSION,
        planted_problem_counts=dict(sorted(Counter(p["type"] for p in P.items).items())), planted_problem_total=len(P.items),
        findings_without_ledger_effect=len(findings or []), honest_noise_count=len(P.noise),
        revenue_by_year={y: money(v[0]) for y, v in sorted(yearly.items())}, net_profit_by_year={y: money(v[1]) for y, v in sorted(yearly.items())})
    return dict(
        _readme="Answer key for the synthetic company. SYNTHETIC DATA - no real firm. Amounts are decimal strings in major units. 'gap' = book minus verified. "
                "Fix = void the listed book entries, then add the listed entries; after that the books equal the truth exactly.",
        summary=summary, problems=probs, honest_noise=P.noise, true_monthly_pnl=true_pnl, book_monthly_pnl=book_pnl,
        true_month_end_cash=me_cash, cash_truth_gap=gaps, **extra)
