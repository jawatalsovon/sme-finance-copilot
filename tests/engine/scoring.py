"""Score Cash Truth findings against D1's answer key (used ONLY here and in tests).

A planted problem counts as found when at least one finding cites one of its rows:
  * its book entries (journal_entries:<id>),
  * its statement / settlement evidence rows (file + row -> external_statement_lines:<id>),
  * for month-level cash problems: the cash count of that month,
  * for receivable problems: the customer's invoices.
A finding is a true positive when it cites at least one planted problem; otherwise it is a
false positive (including findings that only cite rows the key marks as honest noise).
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from decimal import Decimal

from backend.engine.data import Ledger

# key type -> our finding kinds that describe it (for the type-agreement column only)
TYPE_MAP = {
    "unrecorded_cash_expense": {"cash_count_shortfall", "unrecorded_expense"},
    "unrecorded_fee": {"fee_not_recorded"},
    "missing_marketplace_fee": {"fee_not_recorded"},
    "personal_spend_mixed": {"unrecorded_expense"},
    "unrecorded_receipt": {"unrecorded_receipt"},
    "misrouted_receipt": {"other"},
    "duplicate_entry": {"duplicate_entry"},
    "timing_difference": {"timing_difference"},
    "phantom_receivable": {"stale_receivable"},
    "stale_receivable": {"stale_receivable"},
    "misclassified_expense": {"other"},
    "fx_misbooking": {"fx_difference"},
    "inventory_shrinkage": {"other"},
}
UNDETECTABLE = {"inventory_shrinkage"}   # needs stock counts, which no source in the data provides


def problem_refs(L: Ledger, key: dict, as_of: dt.date) -> dict[str, set[str]]:
    st = L.statements
    by_file_row = {(f, str(r)): i for f, r, i in zip(st["source_file"], st["source_row_id"], st["id"])}
    counts = st[st["channel"] == "cash_count"]
    count_by_month = {f"{d.year:04d}-{d.month:02d}": i for d, i in zip(counts["txn_date"], counts["id"])}
    inv = L.invoices
    inv_by_cp = defaultdict(set)
    for i, cp in zip(inv["id"], inv["counterparty_id"]):
        inv_by_cp[cp].add(i)
    cp_ext = dict(zip(L.counterparties["id"], L.counterparties["id"]))
    out = {}
    for p in key["problems"]:
        if p.get("as_of") and p["as_of"] != as_of.isoformat():
            continue
        if dt.date.fromisoformat(p["date"]) > as_of:
            continue
        refs = {f"journal_entries:{e}" for e in p.get("book_entries", [])}
        for r in p.get("affected_source_rows", []):
            sid = by_file_row.get((r["file"], str(r["row"])))
            if sid:
                refs.add(f"external_statement_lines:{sid}")
        det = p.get("details") or {}
        if p["type"] == "unrecorded_cash_expense" and det.get("month") in count_by_month:
            refs.add(f"external_statement_lines:{count_by_month[det['month']]}")
        if p["type"] in ("phantom_receivable", "stale_receivable"):
            if det.get("invoice"):
                refs.add(f"invoices:{det['invoice']}")
                cp = inv[inv["id"] == det["invoice"]]["counterparty_id"]
                for c in cp:
                    refs |= {f"invoices:{i}" for i in inv_by_cp[c]}
            if det.get("household"):
                cid = f"{L.company_id}-{det['household']}"
                refs |= {f"invoices:{i}" for i in inv_by_cp.get(cid, set())}
        out[p["id"]] = refs
    return out


def noise_refs(key: dict) -> set[str]:
    s = set()
    for n in key.get("honest_noise", []):
        for t in n.get("truth_ids", []):
            s.add(f"journal_entries:{t.replace('je-', 'bk-', 1)}")
    return s


def score(L: Ledger, key: dict, result, as_of: dt.date) -> dict:
    prefs = problem_refs(L, key, as_of)
    ptype = {p["id"]: p["type"] for p in key["problems"]}
    pamt = {p["id"]: abs(Decimal(p["amount_base"])) for p in key["problems"]}
    ref_to_p = defaultdict(set)
    for pid, refs in prefs.items():
        for r in refs:
            ref_to_p[r].add(pid)
    found = set()
    tp = 0
    type_ok = 0
    fp_kinds = Counter()
    for f in result.findings:
        hit = set()
        for r in f.evidence:
            hit |= ref_to_p.get(r, set())
        if hit:
            tp += 1
            found |= hit
            if any(f.kind in TYPE_MAP.get(ptype[h], set()) for h in hit):
                type_ok += 1
        else:
            fp_kinds[f.kind] += 1
    n_f = len(result.findings)
    in_scope = set(prefs)
    detectable = {p for p in in_scope if ptype[p] not in UNDETECTABLE}
    by_type = {}
    for t in sorted({ptype[p] for p in in_scope}):
        ps = [p for p in in_scope if ptype[p] == t]
        by_type[t] = (sum(1 for p in ps if p in found), len(ps))
    key_gap = -Decimal(key["cash_truth_gap"][as_of.isoformat()]["gap"]) if as_of.isoformat() in key["cash_truth_gap"] else None
    amt_total = sum(pamt[p] for p in detectable) or Decimal(1)
    return {
        "findings": n_f,
        "precision": tp / n_f if n_f else 0.0,
        "recall": len(found & in_scope) / len(in_scope) if in_scope else 0.0,
        "recall_detectable": len(found & detectable) / len(detectable) if detectable else 0.0,
        "recall_amount_weighted": float(sum(pamt[p] for p in found & detectable) / amt_total),
        "type_agreement": type_ok / tp if tp else 0.0,
        "problems": len(in_scope),
        "by_type": by_type,
        "fp_kinds": dict(fp_kinds),
        "gap_engine": result.gap.value,
        "gap_key": key_gap,
        "gap_abs_error": abs(result.gap.value - key_gap) if key_gap is not None else None,
        "gap_rel_error": float(abs(result.gap.value - key_gap) / abs(key_gap)) if key_gap else None,
        "unexplained": result.unexplained.value,
    }
