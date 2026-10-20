"""Weekly Owner Brief: what the copilot tells the owner unprompted, built from templates (no LLM).

Contents: the Cash Truth gap, the cash outlook, the top 3 alerts and up to 5 recommended actions.
Every action that changes the books carries its draft journal entry; the agent queues it for the
owner's approval (T2) and fills `approval_id`. Nothing here writes anything.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from backend.core.util import format_money
from backend.engine.types import (
    AlertList, BriefAction, CashflowForecast, CashTruthResult, FinancingMatches, KPISet, OwnerBrief,
)

KIND_WORDS = {
    "unrecorded_expense": "payments missing from the books",
    "cash_count_shortfall": "cash missing at the count",
    "fee_not_recorded": "fees missing from the books",
    "unrecorded_receipt": "money received but not recorded",
    "duplicate_entry": "entries booked twice",
    "fx_difference": "exchange-rate differences",
    "stale_receivable": "receivables unlikely to be paid",
    "timing_difference": "dates that differ from the bank",
    "other": "other differences",
}


def owner_brief(company_id: str, as_of: dt.date, currency: str, ct: CashTruthResult, al: AlertList,
                fc: CashflowForecast, fin: FinancingMatches | None, kp: KPISet) -> OwnerBrief:
    cur = currency
    week_end = as_of
    week_start = as_of - dt.timedelta(days=6)
    rev_kpi = next((k for k in kp.kpis if k.key == "cash_truth_gap_pct"), None)
    gap_pct = rev_kpi.value if rev_kpi else None

    actions: list[BriefAction] = []
    # 1. approve the drafts that close most of the gap (largest cash effect first, grouped by kind)
    with_drafts = [f for f in ct.findings if f.proposed_entries and f.amount != 0]
    with_drafts.sort(key=lambda f: (-abs(f.amount), f.id))
    seen_kinds: dict[str, int] = {}
    for f in with_drafts:
        if len([a for a in actions if a.kind == "approve_draft"]) >= 3:
            break
        if seen_kinds.get(f.kind, 0) >= 2:
            continue
        seen_kinds[f.kind] = seen_kinds.get(f.kind, 0) + 1
        actions.append(BriefAction(
            id="", kind="approve_draft", title=f"Approve correction: {f.title}",
            why=(f"{f.explanation} Approving saves a draft entry in your books (nothing is paid or sent)."),
            amount=f.amount, currency=cur, finding_id=f.id, draft=f.proposed_entries[0], evidence=f.evidence[:20]))
    # 2. chase the latest customers / plan payments from the top alerts
    for a in al.alerts:
        if a.kind == "late_customer" and not any(x.kind == "send_reminder" for x in actions):
            actions.append(BriefAction(id="", kind="send_reminder", title=f"Send a reminder: {a.title}",
                                       why=a.message + " The copilot drafts the message; you approve before it is sent.",
                                       amount=a.amount, currency=cur, alert_id=a.id, evidence=a.evidence[:20]))
        elif a.kind == "bill_due" and not any(x.kind == "plan_payment" for x in actions):
            actions.append(BriefAction(id="", kind="plan_payment", title=a.title, why=a.message, amount=a.amount,
                                       currency=cur, alert_id=a.id, evidence=a.evidence[:20]))
    # 3. low cash -> watch / consider financing
    low = next((a for a in al.alerts if a.kind == "low_cash" and a.severity in ("critical", "warning")), None)
    if low:
        best = next((m for m in (fin.matches if fin else []) if m.eligible and m.affordability
                     and m.affordability.affordable), None)
        if best:
            actions.append(BriefAction(
                id="", kind="consider_financing", title=f"Look at: {best.product.name}",
                why=(f"{low.message} {best.affordability.explanation} Illustrative only; the copilot never applies "
                     f"for credit."), amount=best.affordability.requested_amount, currency=best.product.currency,
                alert_id=low.id, product_id=best.product.id, evidence=low.evidence[:10]))
        else:
            actions.append(BriefAction(id="", kind="watch_cash", title=low.title, why=low.message, amount=low.amount,
                                       currency=cur, alert_id=low.id, evidence=low.evidence[:10]))
    actions = actions[:5]
    for i, a in enumerate(actions, start=1):
        a.id = f"act-{i}"

    pts = fc.points
    low_pt = min(pts, key=lambda p: p.closing_cash) if pts else None
    end_cash = pts[-1].closing_cash if pts else None
    gap = ct.gap.value
    by_kind: dict[str, Decimal] = {}
    for f in ct.findings:
        if f.amount:
            by_kind[f.kind] = by_kind.get(f.kind, Decimal(0)) + f.amount
    top_kinds = sorted(by_kind.items(), key=lambda kv: -abs(kv[1]))[:2]
    if gap == 0:
        headline = f"Your books match the cash you can prove ({format_money(ct.verified_cash.value, cur)})."
    else:
        headline = (f"Your books show {format_money(abs(gap), cur)} {'more' if gap < 0 else 'less'} cash than you can "
                    f"prove" + (f" ({gap_pct:.1f}% of yearly sales)" if gap_pct is not None else "") + ".")
    top_alerts = al.alerts[:3]
    parts = [f"Owner Brief for the week {week_start} to {week_end}.", headline]
    if top_kinds:
        parts.append("Biggest causes: " + "; ".join(f"{KIND_WORDS.get(k, k)} {format_money(abs(v), cur)}"
                                                    for k, v in top_kinds) + ".")
    if end_cash is not None:
        parts.append(f"Cash outlook: {format_money(end_cash, cur)} in 13 weeks, lowest "
                     f"{format_money(low_pt.closing_cash, cur)} in the week of {low_pt.period_start}.")
    if top_alerts:
        parts.append("Top alerts: " + "; ".join(a.title for a in top_alerts) + ".")
    if actions:
        parts.append(f"{len(actions)} recommended action(s); {sum(a.kind == 'approve_draft' for a in actions)} "
                     f"correction(s) wait for your approval.")
    facts = {
        "start": week_start, "end": week_end, "gap": gap, "gap_abs": format_money(abs(gap), cur),
        "verified": format_money(ct.verified_cash.value, cur), "pct": gap_pct,
        "causes": [(k, format_money(abs(v), cur)) for k, v in top_kinds],
        "end_cash": format_money(end_cash, cur) if end_cash is not None else None,
        "low_cash": format_money(low_pt.closing_cash, cur) if low_pt else None,
        "low_week": low_pt.period_start if low_pt else None,
        "n_alerts": len(al.alerts), "n_critical": sum(a.severity == "critical" for a in al.alerts),
        "n_actions": len(actions), "n_drafts": sum(a.kind == "approve_draft" for a in actions),
    }
    summaries = {"en": " ".join(parts), "zh": _render(facts, ZH), "bn": _render(facts, BN)}
    return OwnerBrief(company_id=company_id, as_of=as_of, week_start=week_start, week_end=week_end, currency=cur,
                      headline=headline, cash_truth_gap=ct.gap, verified_cash=ct.verified_cash,
                      gap_pct_of_revenue=gap_pct, forecast_end_cash=end_cash,
                      forecast_lowest_cash=low_pt.closing_cash if low_pt else None,
                      forecast_lowest_week=low_pt.period_start if low_pt else None, top_alerts=top_alerts,
                      actions=actions, kpis=[k for k in kp.kpis if k.key in ("runway_months", "gross_margin_pct",
                                                                             "dso_days", "net_margin_pct")],
                      summary=" ".join(parts), summaries=summaries)


# ---------------------------------------------------------------------------- other languages
# Fixed templates (no LLM): every number is formatted in from the engine's own figures.
ZH = {
    "title": "本周老板简报（{start} 至 {end}）。",
    "equal": "账面现金与可核实现金一致（{verified}）。",
    "more": "账面现金比可核实的现金多 {gap_abs}{pct}。",
    "less": "账面现金比可核实的现金少 {gap_abs}{pct}。",
    "pct": "（占年销售额 {pct:.1f}%）",
    "causes": "主要原因：{items}。",
    "sep": "；",
    "outlook": "现金展望：13 周后约为 {end_cash}，最低点 {low_cash}（{low_week} 当周）。",
    "alerts": "警报 {n_alerts} 项，其中严重 {n_critical} 项。",
    "actions": "建议行动 {n_actions} 项，其中 {n_drafts} 项更正分录待您批准。",
    "kinds": {
        "unrecorded_expense": "未入账的支出", "cash_count_shortfall": "现金盘点短缺", "fee_not_recorded": "未入账的费用",
        "unrecorded_receipt": "未入账的收款", "duplicate_entry": "重复记账", "fx_difference": "汇率差异",
        "stale_receivable": "难以收回的应收款", "timing_difference": "日期差异", "other": "其他差异",
    },
}
BN = {
    "title": "সাপ্তাহিক মালিক সারসংক্ষেপ ({start} থেকে {end})।",
    "equal": "আপনার খাতা ও যাচাইযোগ্য নগদ মিলে গেছে ({verified})।",
    "more": "আপনার খাতায় যাচাইযোগ্য নগদের চেয়ে {gap_abs} বেশি দেখাচ্ছে{pct}।",
    "less": "আপনার খাতায় যাচাইযোগ্য নগদের চেয়ে {gap_abs} কম দেখাচ্ছে{pct}।",
    "pct": " (বছরের বিক্রির {pct:.1f}%)",
    "causes": "প্রধান কারণ: {items}।",
    "sep": "; ",
    "outlook": "নগদের পূর্বাভাস: ১৩ সপ্তাহ পরে প্রায় {end_cash}, সর্বনিম্ন {low_cash} ({low_week} সপ্তাহে)।",
    "alerts": "সতর্কতা {n_alerts}টি, এর মধ্যে জরুরি {n_critical}টি।",
    "actions": "{n_actions}টি প্রস্তাবিত পদক্ষেপ; {n_drafts}টি সংশোধনী আপনার অনুমোদনের অপেক্ষায়।",
    "kinds": {
        "unrecorded_expense": "খাতায় না-থাকা খরচ", "cash_count_shortfall": "নগদ গণনায় ঘাটতি",
        "fee_not_recorded": "খাতায় না-থাকা চার্জ", "unrecorded_receipt": "খাতায় না-থাকা প্রাপ্তি",
        "duplicate_entry": "দুবার লেখা এন্ট্রি", "fx_difference": "বিনিময় হারের পার্থক্য",
        "stale_receivable": "আদায় অনিশ্চিত পাওনা", "timing_difference": "তারিখের পার্থক্য", "other": "অন্যান্য পার্থক্য",
    },
}


def _render(f: dict, t: dict) -> str:
    out = [t["title"].format(**f)]
    pct = t["pct"].format(pct=f["pct"]) if f["pct"] is not None else ""
    if f["gap"] == 0:
        out.append(t["equal"].format(**f))
    else:
        out.append(t["more" if f["gap"] < 0 else "less"].format(gap_abs=f["gap_abs"], pct=pct))
    if f["causes"]:
        out.append(t["causes"].format(items=t["sep"].join(f"{t['kinds'].get(k, k)} {v}" for k, v in f["causes"])))
    if f["end_cash"] is not None:
        out.append(t["outlook"].format(**f))
    if f["n_alerts"]:
        out.append(t["alerts"].format(**f))
    if f["n_actions"]:
        out.append(t["actions"].format(**f))
    return " ".join(out) if t is BN else "".join(out)
