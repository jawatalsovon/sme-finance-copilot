import datetime as dt
from decimal import Decimal

import pytest

from backend.ledger.lineage import get_source_rows
from tests.engine.conftest import AS_OF, SMES, synth_engine
from tests.engine.scoring import score


def _balanced(entry) -> bool:
    tot = sum((ln.debit - ln.credit) * Decimal(str(ln.fx_rate)) for ln in entry.lines)
    return abs(tot) < Decimal("0.05")


# ------------------------------------------------------------------ dev fixture (tiny, fast)
def test_dev_fixture_finds_unrecorded_bank_and_wallet_items(dev_engine):
    r = dev_engine.cash_truth("devco", dt.date(2026, 9, 30))
    assert r.currency == "BDT"
    # books: bank 500000-60000-25000-40000 = 375000; statement 370300 -> gap -4700
    bank = next(c for c in r.channels if c.channel == "bank")
    assert bank.book_balance == Decimal("375000.00") and bank.verified_balance == Decimal("370300.00")
    assert r.gap.value == r.verified_cash.value - r.book_cash.value
    amounts = sorted(abs(f.amount) for f in r.findings if f.amount)
    assert Decimal("3500.00") in amounts            # bank charges not in the books
    assert Decimal("350.00") in amounts             # wallet fee not in the books
    assert r.unexplained.value == r.gap.value - r.explained.value
    for f in r.findings:
        assert f.evidence, f.title
        for e in f.proposed_entries:
            assert _balanced(e), f.title


def test_dev_fixture_injection_text_is_only_data(dev_engine):
    r = dev_engine.cash_truth("devco", dt.date(2026, 9, 30))
    # the poisoned statement line is reported as an unrecorded payment, its text never changes behaviour
    hit = [f for f in r.findings if "IGNORE PREVIOUS" in f.explanation.upper()]
    assert all(not f.proposed_entries or _balanced(f.proposed_entries[0]) for f in hit)


# ------------------------------------------------------------------ synthetic SMEs vs. D1 answer keys
@pytest.mark.parametrize("cid", SMES)
def test_cash_truth_matches_answer_key(cid):
    eng, key = synth_engine(cid)
    r = eng.cash_truth(cid, AS_OF)
    s = score(eng._ledger(cid), key, r, AS_OF)
    # gap = verified - book; the key stores book - verified
    assert abs(s["gap_engine"] - s["gap_key"]) <= Decimal("0.05"), s
    assert s["recall_detectable"] >= 0.85, s
    assert s["precision"] >= 0.80, s
    assert abs(r.unexplained.value) <= Decimal("0.05")


@pytest.mark.parametrize("cid", SMES)
def test_findings_have_resolvable_evidence_and_balanced_drafts(cid):
    eng, _ = synth_engine(cid)
    r = eng.cash_truth(cid, AS_OF)
    assert r.findings and r.summary
    for f in r.findings:
        assert f.evidence and len(f.evidence) <= 200
        assert 0 <= f.confidence <= 1
        for e in f.proposed_entries:
            assert _balanced(e), (f.title, e)
    sample = [f.evidence[0] for f in r.findings[:: max(1, len(r.findings) // 25)]]
    rows = get_source_rows(eng.db, sample, company_id=cid)
    assert rows


def test_cash_truth_is_deterministic():
    eng, _ = synth_engine("bd_grocer")
    a = eng.cash_truth("bd_grocer", AS_OF).model_dump_json()
    from backend.engine.impl import Engine
    b = Engine(eng.db)
    import backend.engine.impl as impl
    impl._CACHE.clear()
    assert b.cash_truth("bd_grocer", AS_OF).model_dump_json() == a


def test_cash_truth_earlier_as_of_matches_key():
    eng, key = synth_engine("bd_grocer")
    d = dt.date(2025, 12, 31)
    r = eng.cash_truth("bd_grocer", d)
    assert abs(r.gap.value + Decimal(key["cash_truth_gap"][d.isoformat()]["gap"])) <= Decimal("0.05")


@pytest.mark.parametrize("cid", SMES)
def test_every_proposed_draft_balances_in_base_currency(cid):
    """All drafts (in findings and in the result list) balance on amount_base, and each line's amount_base is
    its own-currency amount times its rate (never add debits/credits of different currencies)."""
    eng, _ = synth_engine(cid)
    r = eng.cash_truth(cid, AS_OF)
    entries = list(r.proposed_entries) + [e for f in r.findings for e in f.proposed_entries]
    assert entries
    for e in entries:
        assert sum(ln.amount_base for ln in e.lines) == Decimal("0"), e
        assert e.total_debit_base == e.total_credit_base == sum(ln.amount_base for ln in e.lines if ln.amount_base > 0)
        assert e.currency_base == r.currency
        for ln in e.lines:
            assert abs((ln.debit - ln.credit) * ln.fx_rate - ln.amount_base) <= Decimal("0.01"), ln
            assert ln.debit >= 0 and ln.credit >= 0 and not (ln.debit and ln.credit)
