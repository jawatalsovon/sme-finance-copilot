"""Acceptance table per SME: Cash Truth vs answer key, corrected P&L vs true P&L, backtest, financing.

    python -m tests.engine.report                 # all SMEs with data
    python -m tests.engine.report bd_grocer       # one
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from decimal import Decimal

from backend.engine.impl import Engine
from backend.engine.types import ScenarioChange
from tests.engine.scoring import score
from tests.engine.synth import answer_key, available, build_db

AS_OF = dt.date(2026, 9, 30)


def pnl_compare(eng: Engine, cid: str, key: dict) -> dict:
    """Max absolute difference, corrected vs true, over all months (revenue, cogs, net profit)."""
    worst = {"revenue": Decimal(0), "cogs": Decimal(0), "net_profit": Decimal(0), "other_income": Decimal(0)}
    worst_month = {}
    exact = 0
    shrink = {}
    for p in key["problems"]:
        if p["type"] == "inventory_shrinkage":
            m = p["date"][:7]
            shrink[m] = shrink.get(m, Decimal(0)) + Decimal(p["amount_base"])
    worst_ex = Decimal(0)
    for row in key["true_monthly_pnl"]:
        m = row["month"]
        r = eng.monthly_pnl(cid, m, "corrected")
        got = {"revenue": r.revenue.value, "cogs": r.cogs.value, "net_profit": r.net_profit.value}
        oi = r.net_profit.value - r.operating_profit.value
        for k in ("revenue", "cogs", "net_profit"):
            d = abs(got[k] - Decimal(row[k]))
            if d > worst[k]:
                worst[k], worst_month[k] = d, m
        d_np = abs(got["net_profit"] - Decimal(row["net_profit"]))
        exact += d_np < Decimal("1")
        # same comparison with the (undetectable) shrinkage taken out of the truth
        d_ex = abs(got["net_profit"] - (Decimal(row["net_profit"]) + shrink.get(m, Decimal(0))))
        worst_ex = max(worst_ex, d_ex)
    return {"max_abs_diff": worst, "worst_month": worst_month, "months_net_profit_within_1": exact,
            "months": len(key["true_monthly_pnl"]), "max_abs_net_diff_excl_shrinkage": worst_ex}


def run(cid: str) -> dict:
    db = build_db(cid)
    key = answer_key(cid)
    eng = Engine(db)
    t = time.time()
    ct = eng.cash_truth(cid, AS_OF)
    t_ct = time.time() - t
    sc = score(eng._ledger(cid), key, ct, AS_OF)
    t = time.time()
    pc = pnl_compare(eng, cid, key)
    t_pnl = time.time() - t
    t = time.time()
    fw = eng.cashflow_forecast(cid, 13, "week", AS_OF)
    fm = eng.cashflow_forecast(cid, 12, "month", AS_OF)
    t_fc = time.time() - t
    fin = eng.financing_matches(cid, AS_OF)
    al = eng.alerts(cid, AS_OF)
    kp = eng.kpis(cid, AS_OF)
    scn = eng.scenario(cid, [ScenarioChange(kind="cogs_pct", value=15)], AS_OF)
    return {"cid": cid, "score": sc, "pnl": pc, "fw": fw, "fm": fm, "fin": fin, "alerts": al, "kpis": kp,
            "scenario": scn, "ct": ct, "secs": {"cash_truth": t_ct, "pnl_all_months": t_pnl, "forecasts": t_fc}}


def main(argv: list[str]) -> None:
    cids = argv or available()
    rows = [run(c) for c in cids]
    print(f"{'SME':<11} {'recall':>7} {'recall*':>7} {'prec':>6} {'gap engine':>16} {'gap key':>16} {'gap err':>9} "
          f"{'P&L max|d| net':>15} {'excl.shrink':>12} {'sMAPE 13w':>9} {'sMAPE 12m':>9}")
    for r in rows:
        s, p = r["score"], r["pnl"]
        print(f"{r['cid']:<11} {s['recall']:7.3f} {s['recall_detectable']:7.3f} {s['precision']:6.3f} "
              f"{float(s['gap_engine']):16,.2f} {float(s['gap_key']):16,.2f} {float(s['gap_abs_error']):9.2f} "
              f"{float(p['max_abs_diff']['net_profit']):15,.2f} {float(p['max_abs_net_diff_excl_shrinkage']):12,.2f} "
              f"{r['fw'].backtest.value if r['fw'].backtest else float('nan'):9.1f} "
              f"{r['fm'].backtest.value if r['fm'].backtest else float('nan'):9.1f}")
    print("recall* = excluding inventory shrinkage (no stock counts in any source); gap = verified - book cash at", AS_OF)
    for r in rows:
        s, p = r["score"], r["pnl"]
        print(f"\n[{r['cid']}] by type (found/planted): " + ", ".join(f"{k} {a}/{b}" for k, (a, b) in s["by_type"].items()))
        print(f"  findings {s['findings']}, false positives by kind {s['fp_kinds']}, amount-weighted recall "
              f"{s['recall_amount_weighted']:.3f}")
        print(f"  corrected P&L vs true: max |diff| revenue {p['max_abs_diff']['revenue']}, cogs {p['max_abs_diff']['cogs']}, "
              f"net {p['max_abs_diff']['net_profit']} (worst {p['worst_month']}); net within 1 unit in "
              f"{p['months_net_profit_within_1']}/{p['months']} months")
        top = r["fin"].matches[:3]
        print("  top financing: " + "; ".join(f"{m.product.id} (fit {m.fit_score:.2f}, eligible {m.eligible}, DSCR "
                                              f"{m.affordability.dscr if m.affordability else '-'})" for m in top))
        print(f"  alerts: {r['alerts'].summary}")
        print(f"  scenario: {r['scenario'].summary}")
        print(f"  timings (s): " + ", ".join(f"{k} {v:.1f}" for k, v in r["secs"].items()))


if __name__ == "__main__":
    main(sys.argv[1:])
